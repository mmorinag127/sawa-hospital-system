"""Server-owned native receipts for selected hospital and shift work.

All mutations still pass through Tools.execute and the native approval gates.
The model supplies business decisions, never copies concurrency tokens or sheets.
"""
import hashlib

from .store import Conflict
from .task_flow import rows
from .shift_questions import ShiftQuestions


class OperatorWait(Exception):
    def __init__(self, result):
        self.result = result


class WorkflowCompletion:
    def __init__(self, tools):
        self.tools = tools

    def current(self, ident, generation, domain):
        run = self.tools.store.get(ident)
        if run['generation'] != generation or run['status'] != 'running':
            raise Conflict('依頼は停止または変更されました')
        allowed = ('shift_next_month', 'shift_wishes', 'shift_absence') if domain == 'shift' else ('hospital_pending_faxes', 'hospital_review_faxes')
        if run.get('task_id') not in allowed:
            raise Conflict('選択した業務専用の操作です')
        return run

    async def call(self, run, command, step, name, parameters=None, raw=None):
        from .catalog import OPERATIONS
        operation = OPERATIONS.get(name)
        if operation and operation.method != 'GET':
            with self.tools.store.read() as db:
                pending = db.execute("SELECT request FROM commands WHERE run_id=? AND status!='done'",(run['id'],)).fetchall()
            import json
            if any((op := OPERATIONS.get(json.loads(row['request'])['name'])) and op.method != 'GET' for row in pending):
                raise Conflict('結果未確定の業務操作があります。再送せず実行結果の照合が必要です')
        key = 'workflow_' + hashlib.sha256((command + ':' + step).encode()).hexdigest()
        result = await self.tools.execute(run['id'], run['generation'], name,
            raw if raw is not None else {'parameters': parameters or {}}, key)
        if result.get('waiting_input') or result.get('needs_operator_confirmation'):
            raise OperatorWait(result)
        if not result.get('ok'):
            if name == 'shift_solve' and result.get('status') in (400,409,422):
                import json
                try:
                    detail = json.loads(result.get('detail','{}')).get('detail', {})
                except (TypeError, ValueError):
                    detail = {}
                if isinstance(detail, dict) and isinstance(detail.get('preflight'), dict):
                    raise OperatorWait(await ShiftQuestions(self.tools).infeasible(run, command, detail))
            raise Conflict(name + ': ' + str(result))
        return result

    async def shift_evidence(self, run, command, sid, cid):
        evidence = []
        period = await self.call(run, command, 'period', 'shift_get_period', {'query': {'month': run['task_inputs']['inputs']['month']}})
        evidence.append(period['command_id'])
        query = {'start': period['data']['start_date'], 'end': period['data']['end_date_exclusive']}
        for name in ('shift_get_staff', 'shift_get_constraints', 'shift_get_settings', 'shift_get_demands', 'shift_get_workdays'):
            result = await self.call(run, command, name, name, {'query': query} if name in ('shift_get_demands','shift_get_workdays') else {})
            evidence.append(result['command_id'])
        detail = await self.call(run, command, 'candidate', 'shift_get_run', {'ids': {'run_id': sid}, 'query': {'candidate_id': cid}})
        evidence.append(detail['command_id'])
        validation = await self.call(run, command, 'validation', 'shift_get_validation', {'ids': {'candidate_id': cid}})
        evidence.append(validation['command_id'])
        return evidence, detail['data']

    async def prepare_shift(self, ident, generation, raw, command):
        if raw:
            raise ValueError('入力項目はありません')
        run = self.current(ident, generation, 'shift')
        review = self.tools.flow.review(ident)
        if review and review['state'] in ('awaiting_approval', 'approved'):
            await self.tools.flow.recheck(run, review)
            return {'ok': True, 'state': review['state'], 'review_id': review['id']}
        month = run['task_inputs']['inputs']['month']
        if run['task_id'] == 'shift_absence':
            await self.tools.flow.capture_shift_reference(run)
            source = (await self.call(run, command, 'constraints-before', 'shift_get_constraints'))['data']
            target = run['task_inputs']['targets'][0]
            staff_id = int(target['key'])
            day = run['task_inputs']['inputs']['absence_date']
            existing = [item for item in rows(source) if item['staff_id'] == staff_id and item['work_date'] == day]
            if existing:
                if len(existing) != 1 or existing[0]['status'] != 'unavailable' or existing[0]['strength'] != 'hard':
                    return await ShiftQuestions(self.tools).conflict(run, command, existing, source)
            else:
                await self.call(run, command, 'absence', 'shift_save_constraint', {'body': {
                    'staff_id': staff_id, 'work_date': day, 'status': 'unavailable', 'strength': 'hard',
                    'weight': 10, 'reason': '利用者が選択した職員・日付の急な欠勤',
                    'collection_revision': source['collection_revision']}, 'reason': '利用者指定の欠勤だけ登録し、他の条件を保持'})
        preflight = await self.call(run, command, 'preflight', 'shift_preflight', {'body': {'target_month': month}, 'reason': '選択月の登録済み条件を正規APIで検査'})
        if preflight['data'].get('blocking'):
            return await ShiftQuestions(self.tools).infeasible(run, command, preflight['data'])
        result = await self.call(run, command, 'solve', 'shift_solve', {'body': {'target_month': month, 'strategy': 'balanced', 'candidate_count': 1}, 'reason': '選択月・登録済み希望休と勤務条件を保持して勤務案を作成'})
        sid = result['data']['solve_run_id']
        result = await self.call(run, command, 'wait', 'wait_for_native_job', raw={'operation':'shift_get_run','parameters': {'ids': {'run_id': sid}}})
        if result['data'].get('status') == 'failed':
            return await ShiftQuestions(self.tools).infeasible(run, command, result['data'])
        if result['data'].get('status') != 'succeeded' or not result['data'].get('selected_candidate_id'):
            raise Conflict('勤務案の実行結果を確認できません')
        cid = result['data']['selected_candidate_id']
        evidence, candidate = await self.shift_evidence(run, command + ':review', sid, cid)
        return await self.call(run, command, 'approval', 'request_task_approval', raw={
            'summary': '登録済みの希望休・勤務条件を反映した勤務案です。人員不足・違反と変更内容を確認し、承認してください。',
            'evidence_commands': evidence, 'actions': [{'operation': 'shift_confirm', 'parameters': {
                'ids': {'run_id': sid}, 'query': {'candidate_id': cid}, 'body': candidate['command_precondition'],
                'reason': '利用者が承認した勤務候補だけを確定'}}]})

    async def complete_shift(self, ident, generation, raw, command):
        if raw:
            raise ValueError('入力項目はありません')
        run = self.current(ident, generation, 'shift')
        review = self.tools.flow.review(ident)
        if not review or review['state'] != 'approved':
            raise Conflict('勤務案の利用者承認が必要です')
        await self.tools.flow.recheck(run, review)
        if len(review['actions']) != 1 or review['actions'][0]['operation'] != 'shift_confirm':
            raise Conflict('承認した確定操作が一意ではありません')
        action = review['actions'][0]
        from .fax_workflow import digest
        parameters = {**action['parameters'], 'reason': '利用者が承認した勤務候補だけを確定'}
        if digest(action) not in review['applied']:
            await self.call(run, command, 'confirm', 'shift_confirm', parameters)
        sid = parameters['ids']['run_id']; cid = parameters['query']['candidate_id']
        artifact = await self.call(run, command, 'export', 'shift_export', {'ids': {'run_id': sid}, 'query': {'candidate_id': cid, 'format': 'xlsx'}})
        evidence, detail = await self.shift_evidence(run, command + ':final', sid, cid)
        if detail.get('confirmed') is not True:
            raise Conflict('承認した勤務案の確定を確認できません')
        return await self.call(run, command, 'finish', 'finish_work', raw={'summary': '承認した勤務案を確定し、正規勤務表を出力・保存結果を再照合しました。', 'evidence_commands': evidence, 'artifact_ids': [artifact['artifact']['id']]})

    async def prepare_fax(self, ident, generation, raw, command):
        if set(raw) != {'fax_id'}:
            raise ValueError('対象FAXを指定してください')
        run = self.current(ident, generation, 'hospital')
        await self.tools.faxes.refresh(run)
        item = next((item for item in self.tools.faxes.items(ident) if item['fax_id'] == raw['fax_id']), None)
        if not item:
            raise Conflict('依頼対象のFAXではありません')
        captured = await self.tools.faxes.capture(run, item)
        original = await self.call(run, command, 'original', 'hospital_get_document', {'ids': {'order_id': item['snapshot']['order_id']}})
        # The canonical sheet remains on the server. Every field and every row is
        # returned for model review, without OCR image/evidence transport metadata.
        sheet = captured['sheet']
        return {'ok': True, 'fax_id': item['fax_id'], 'source': item['snapshot'], 'original': original['artifact'],
                'sheet': {'fields': sheet['fields'], 'rows': sheet['rows']},
                'next_step': 'read_artifact_pageで原本を読み、数量を照合して業務上の不明点を質問する。その後request_fax_approvalへfax_idと照合結果だけを渡す。未変更のsheetは転記しない。'}

    async def complete_faxes(self, ident, generation, raw, command):
        if raw:
            raise ValueError('入力項目はありません')
        run = self.current(ident, generation, 'hospital')
        await self.tools.faxes.refresh(run)
        artifacts = []; evidence = []
        items = self.tools.faxes.items(ident)
        for item in items:
            if not item['decision'] or not item['decision'].get('approve') or item['state'] not in ('approved', 'completed'):
                raise Conflict('全対象FAXの原本照合・利用者承認が必要です')
        for item in items:
            oid = item['snapshot']['order_id']; sheet = item['review']['sheet']
            for name, body in [('hospital_check_anomalies', {'sheet': sheet, 'use_llm': False}), ('hospital_review_sheet', {'sheet': sheet}), ('hospital_save_sheet', {'sheet': sheet}), ('hospital_bag_order', {}), ('hospital_confirm_order', {})]:
                # Recovery after an interrupted export/verification resumes from
                # known successful owned receipts, never repeats known writes.
                prior = [entry for entry in self.tools.flow.commands(ident)
                         if entry['request']['name'] == name and entry['result'].get('ok')
                         and entry['request']['arguments'].get('parameters', {}).get('ids') == {'order_id': oid}
                         and entry['request']['arguments']['parameters'].get('body', {}) == body]
                if prior:
                    current = await self.tools.faxes.capture(run, item)
                    if current['signature'] != item['decision']['native_signature'] or current['revision'] != item['decision']['approved_revision']:
                        raise Conflict('承認後にFAXの正本または版が変わりました。再確認が必要です')
                    continue
                wf = await self.call(run, command, str(oid)+':before:'+name, 'hospital_get_workflow', {'ids': {'order_id': oid}})
                await self.call(run, command, str(oid)+':'+name, name, {'ids': {'order_id': oid}, 'body': body, 'expected_revision': wf['data']['revision'], 'reason': '利用者が原本照合し承認した数量シートをそのまま処理'})
            for name in ('hospital_export_labels', 'hospital_export_delivery_notes', 'hospital_export_aggregate'):
                output = await self.call(run, command, str(oid)+':'+name, name, {'query': {'order_id': oid}})
                artifacts.append(output['artifact']['id'])
            final = await self.call(run, command, str(oid)+':final', 'hospital_get_workflow', {'ids': {'order_id': oid}})
            evidence.append(final['command_id'])
            fax = await self.call(run, command, str(oid)+':final-fax', 'hospital_get_fax', {'ids': {'uploaded_pdf_id': item['fax_id']}})
            evidence.append(fax['command_id'])
        if not evidence:
            evidence.append((await self.call(run, command, 'empty', 'hospital_pending_faxes'))['command_id'])
        return await self.call(run, command, 'finish', 'finish_work', raw={'summary': '承認したFAX注文を確定し、正規のラベル・納品書・製造集計を出力・照合しました。', 'evidence_commands': evidence, 'artifact_ids': artifacts})
