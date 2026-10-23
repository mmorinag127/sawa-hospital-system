"""Durable business progression. Provider sessions only prepare content/questions.

An approval wakes this controller, not the model. Fixed native commands own
confirmation, exports and completion. Recovery never guesses a missing receipt.
"""
import asyncio
import json
import time
import httpx

from .business_contracts import CONTRACTS, SERVER_EXECUTION, owned
from .business_outputs import complete_review
from .conversation import pending_question
from .store import Conflict, now


class BusinessWorkflow:
    def __init__(self, store, tools, providers):
        self.store, self.tools, self.providers = store, tools, providers
        self.jobs = {}
        self.closed = False
        tools.business = self
        from .shift_questions import ShiftQuestions
        self.shift_questions = ShiftQuestions(tools)

    def state(self, ident):
        return self.store.get(ident)['task_inputs'].get('business_workflow', {})

    def checkpoint(self, ident, **values):
        with self.store.tx() as db:
            row = db.execute('SELECT task_inputs FROM runs WHERE id=?', (ident,)).fetchone()
            context = json.loads(row['task_inputs'])
            state = context.get('business_workflow', {})
            if all(state.get(key) == value for key, value in values.items()):
                return state
            state = {**state, **values}
            context['business_workflow'] = state
            db.execute('UPDATE runs SET task_inputs=?,updated=? WHERE id=?', (json.dumps(context, ensure_ascii=False), now(), ident))
        self.store.event(ident, 'business_progress', {'phase': state['phase']})
        return state

    def initialize(self, run):
        if run.get('task_id') not in CONTRACTS:
            raise ValueError('登録された業務を選択してください')
        self.checkpoint(run['id'], version=1, phase='source' if run['task_id'] == 'lunch_import' else 'preparing', epoch=0, attempts=0)

    def adopt(self, run):
        if owned(run) or run.get('task_id') not in CONTRACTS or run['status'] not in ('running', 'waiting_input', 'reconciliation_required'):
            return
        try:
            self.require_copy(run)
        except Conflict:
            self.checkpoint(run['id'], version=1, phase='support', resume_phase='checking', epoch=0, attempts=4)
            self.store.event(run['id'], 'business_support', {'message': '保存済みの業務データの復元を管理者が確認します。過去の回答・承認は保持しています。'})
            return
        review = self.tools.flow.review(run['id'])
        phase = 'saving' if review and review['state'] == 'approved' else 'approval' if review and review['state'] == 'awaiting_approval' else 'question' if pending_question(self.store.events(run['id'])) else 'checking'
        if run['task_id'] == 'lunch_import' and not run['session_id'] and not any(event['kind'] == 'source' for event in self.store.events(run['id'])):
            phase = 'source'
        self.checkpoint(run['id'], version=1, phase=phase, epoch=0, attempts=0)

    async def continue_idle_model(self, run):
        events = self.store.events(run['id'])
        idle = [event['payload'] for event in events if event['kind'] == 'business_model_idle']
        if not idle or idle[-1] != {'session_id': run['session_id'], 'generation': run['generation']}:
            raise Conflict('接続先の応答の確定を確認できません')
        if sum(event['kind'] == 'business_idle_continuation' for event in events) >= 2:
            raise Conflict('業務確認の結果を取得できません。管理者が対応します')
        self.activate(run)
        self.checkpoint(run['id'], phase='analysing')
        self.store.event(run['id'], 'business_idle_continuation', {'session_id': run['session_id']})
        manager = self.providers.for_run(run['id']) or self.providers
        try:
            await manager.follow(run['id'], {'type': 'user.message', 'content': [{'type': 'text', 'text': 'まだ業務の準備が完了していません。文章だけで終了せず、不足する業務情報をask_operatorで質問するか、実データを確認した結果を承認画面へ提出してください。確定・出力は業務サーバーが担当します。'}]})
        except BaseException:
            try:
                self.set_status(run, 'reconciliation_required')
            except Conflict:
                pass
            raise

    def activate(self, run):
        self.set_status(run, 'running')

    def set_status(self, run, status):
        self.store.update(run['id'], expected_generation=run['generation'], status=status)

    def require_copy(self, run):
        durable = getattr(self.tools.sandbox, 'durable_store', None)
        if durable is not None:
            durable.require_working_copy(run['id'])
        for domain in run['domains']:
            if not (self.tools.sandbox.path(run['id']) / 'data' / domain / 'database.sqlite3').is_file():
                raise Conflict('保存済みの業務データを確認できません。初期データへ置き換えず管理者が確認します')

    def kick(self, ident, authorization=None):
        run = self.store.get(ident)
        if not owned(run) or self.closed or run['status'] == 'completed':
            return
        if authorization:
            self.tools.authorizations[ident] = authorization
        old = self.jobs.get(ident)
        if not old or old.done():
            self.jobs[ident] = asyncio.create_task(self.drive(ident))

    async def close(self):
        self.closed = True
        for job in self.jobs.values():
            job.cancel()
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)

    async def call(self, run, name, raw, command):
        token = SERVER_EXECUTION.set(True)
        try:
            result = await self.tools.execute(run['id'], run['generation'], name, raw, command)
        finally:
            SERVER_EXECUTION.reset(token)
        if not result.get('ok') and not result.get('queued_for_human') and not result.get('waiting_input'):
            raise Conflict(str(result.get('error', result.get('question', '業務処理の結果を確認できません'))))
        return result

    async def prepare_selected(self, run):
        task = run['task_id']
        state = self.state(run['id'])
        command = 'business:prepare:' + str(state['epoch'])
        contract = CONTRACTS[task]
        if contract.prepare == 'shift':
            return await self.call(run, 'prepare_shift_review', {}, command)
        evidence = []
        if contract.prepare == 'hospital_outputs':
            reads = [('hospital_get_workflow', {'ids': {'order_id': target['key']}}) for target in run['task_inputs']['targets']]
        elif contract.prepare == 'shift_selected':
            reads = []
            for target in run['task_inputs']['targets']:
                ids = target['data']
                query = {'candidate_id': ids['candidate_id']} if ids['candidate_id'] is not None else {}
                reads.append(('shift_get_run', {'ids': {'run_id': ids['run_id']}, 'query': query}))
                if ids['candidate_id'] is not None:
                    reads.append(('shift_get_validation', {'ids': {'candidate_id': ids['candidate_id']}}))
        elif contract.prepare == 'lunch_month':
            query = {key: run['task_inputs']['inputs'][key] for key in ('facility_id', 'month')}
            reads = [(name, {'query': query}) for name in ('lunch_get_menus', 'lunch_get_schedules', 'lunch_get_routes')]
        else:
            raise Conflict('この業務の準備手順はエージェントの内容確認を必要とします')
        for index, (name, parameters) in enumerate(reads):
            result = await self.call(run, name, {'parameters': parameters}, command + ':read:' + str(index))
            evidence.append(result['command_id'])
        return await self.call(run, 'request_task_approval', {'summary': '選択した対象の内容を確認してください。承認後は必要な保存・帳票作成まで自動で進みます。', 'evidence_commands': evidence, 'actions': []}, command + ':review')

    async def choose_layout(self, run):
        inputs = run['task_inputs'].get('inputs', {})
        if run['task_id'] != 'lunch_daily_bundle' or inputs.get('layout_id'):
            return False
        layouts = inputs.get('layout_choices', [])
        if len(layouts) < 2:
            raise Conflict('使える図面を確認できません。図面の登録は管理者が確認します')
        choices = [{'label': item['name'] if sum(other['name'] == item['name'] for other in layouts) == 1 else item['name'] + '（候補' + str(index + 1) + '）', 'layout_id': item['id'], 'layout_version': item['version']} for index, item in enumerate(layouts)]
        state = self.state(run['id'])
        page = state.get('layout_page', 0)
        if len(choices) > 8:
            choices = choices[page * 7:(page + 1) * 7]
            if not choices:
                raise Conflict('図面の選択内容が変わりました。保存済みの選択を管理者が確認します')
            next_page = page + 1 if (page + 1) * 7 < len(layouts) else 0
            choices.append({'label': 'ほかの図面を見る' if next_page else '最初の図面へ戻る', 'next_page': next_page})
        command = 'business:layout:' + str(state['epoch']) + ':' + str(page)
        result = await self.call(run, 'ask_operator', {'question': 'この日の作業に使う図面を選んでください。', 'options': [choice['label'] for choice in choices]}, command)
        self.store.event(run['id'], 'business_layout_question', {'command_id': result['command_id'], 'choices': choices})
        self.checkpoint(run['id'], phase='question')
        return True

    def answer_layout(self, run, message, question_id, option):
        from .conversation import check_answer
        with self.store.tx() as db:
            events = [{**dict(row), 'payload': json.loads(row['payload'])} for row in
                      db.execute('SELECT * FROM events WHERE run_id=? ORDER BY id', (run['id'],))]
            questions = [event['payload'] for event in events if event['kind'] == 'business_layout_question'
                         and event['payload']['command_id'] == question_id]
            if not questions:
                return False
            current = db.execute('SELECT generation,status,task_inputs FROM runs WHERE id=?', (run['id'],)).fetchone()
            if current['generation'] != run['generation'] or current['status'] != 'waiting_input':
                raise Conflict('この質問は回答済み、または依頼が停止・変更されています')
            check_answer(events, message, question_id, option)
            choices = [choice for choice in questions[-1]['choices'] if choice['label'] == message]
            if len(choices) != 1:
                raise ValueError('表示された図面を選択してください')
            context = json.loads(current['task_inputs'])
            state = context['business_workflow']
            choice = choices[0]
            if 'next_page' in choice:
                state.update(layout_page=choice['next_page'], epoch=state['epoch'] + 1)
            else:
                context['inputs'].update(layout_id=choice['layout_id'], layout_version=choice['layout_version'])
            state['phase'] = 'preparing'
            db.execute('INSERT INTO events(run_id,provider_id,kind,payload,created) VALUES(?,?,?,?,?)',
                       (run['id'], 'operator_reply:' + question_id, 'operator_reply',
                        json.dumps({'message': message, 'question_id': question_id, 'option': option}, ensure_ascii=False), now()))
            db.execute("UPDATE runs SET task_inputs=?,status='preparing',updated=? WHERE id=?",
                       (json.dumps(context, ensure_ascii=False), now(), run['id']))
        self.kick(run['id'])
        return True

    async def pause_model(self, ident, command, result):
        run = self.store.get(ident)
        if not owned(run):
            return False
        review = self.tools.flow.review(ident)
        faxes = self.tools.faxes.items(ident) if self.tools.faxes.active(run) else []
        if not (review and review['state'] == 'awaiting_approval') and not any(item['state'] == 'awaiting_approval' for item in faxes):
            return False
        self.store.event(ident, 'business_model_wait', {'command_id': command, 'result': {key: value for key, value in result.items() if key != '_content'}})
        self.checkpoint(ident, phase='approval', attempts=0)
        return True

    async def continue_model(self, run, message):
        waits = [event for event in self.store.events(run['id']) if event['kind'] == 'business_model_wait']
        if not waits:
            raise Conflict('内容確認の再開位置を照合できません')
        wait = waits[-1]['payload']
        manager = self.providers.for_run(run['id']) or self.providers
        self.store.event(run['id'], 'business_model_send', {'command_id': wait['command_id']})
        self.activate(run)
        self.checkpoint(run['id'], phase='analysing')
        await manager.follow(run['id'], {'type': 'user.custom_tool_result', 'custom_tool_use_id': wait['command_id'], 'content': [{'type': 'text', 'text': json.dumps({'previous_result': wait['result'], 'business_update': message, 'instruction': '必要な業務質問と訂正案の準備だけを続け、新しい確認結果を提出してください。確定・出力・完了はサーバーが行います。'}, ensure_ascii=False)}]})

    async def complete(self, run, review):
        contract = CONTRACTS[run['task_id']]
        command = 'business:complete:' + (review['id'] if review else 'faxes')
        if contract.complete == 'shift':
            return await self.call(run, 'complete_shift_work', {}, command)
        if contract.complete == 'daily':
            return await self.call(run, 'complete_lunch_daily_work', {}, command)
        if contract.complete == 'faxes':
            return await self.call(run, 'complete_fax_work', {}, command)
        return await complete_review(self, run, review)

    async def advance(self, ident):
        run = self.store.get(ident)
        state = self.state(ident)
        if run['status'] == 'completed':
            self.checkpoint(ident, phase='completed')
            return False
        if run['status'] in ('stopped', 'rolled_back', 'stop_pending', 'rollback_stop_pending', 'stopping'):
            return False  # Explicit administrator action, never an automatic cancellation.
        if state.get('phase') == 'support' and time.time() < state.get('next_attempt', 0):
            return False
        if state.get('phase') == 'support':
            if state.get('attempts', 0) >= 4 and not state.get('retryable'):
                return False
            state = self.checkpoint(ident, phase=state.get('resume_phase', 'checking'))
        if self.tools.authorization_validator:
            await self.tools.authorization_validator(run)
        current = self.store.get(ident)
        if current['generation'] != run['generation'] or current['status'] in ('completed', 'stopped', 'rolled_back', 'stop_pending', 'rollback_stop_pending', 'stopping'):
            return False
        self.require_copy(run)
        from .native_recovery import reconcile
        with self.store.read() as db:
            pending_native = db.execute("SELECT 1 FROM commands WHERE run_id=? AND status NOT IN ('done','recoverable_wait','recoverable_read') LIMIT 1", (ident,)).fetchone()
        if pending_native and not (self.providers.tasks.get(ident) and not self.providers.tasks[ident].done()):
            await self.tools.sandbox.start(run)
            await reconcile(self, run)
        if state['phase'] == 'source':
            self.set_status(run, 'waiting_input')
            return False
        manager_task = self.providers.tasks.get(ident)
        model_busy = manager_task and not manager_task.done()
        review = self.tools.flow.review(ident)
        faxes = self.tools.faxes.items(ident) if self.tools.faxes.active(run) else []
        if review and review['state']=='needs_correction' and state['phase'] not in ('correction','shift_adjustment','question'):
            self.decision_ready(ident, False)
            return True
        all_faxes_approved = bool(faxes) and all(item['state'] in ('approved', 'completed') and (item.get('decision') or {}).get('approve') for item in faxes)
        if review and review['state'] == 'approved' and run['task_id'] != 'hospital_review_faxes' or all_faxes_approved:
            if model_busy:
                return True
            self.checkpoint(ident, phase='saving')
            # A saved approval/phase survives gateway restarts. The command ledger
            # still refuses any mutation whose result is unknown.
            self.activate(run)
            await self.tools.sandbox.start(self.store.get(ident))
            await self.complete(self.store.get(ident), review)
            self.checkpoint(ident, phase='completed')
            try:
                if not self.store.get(ident)['session_id'] and not any(event['kind'] in ('gemini_dispatch', 'agent.custom_tool_use') for event in self.store.events(ident)):
                    self.store.update(ident, reserved=0)
                    self.store.event(ident, 'business_no_model_usage', {'reason': 'モデル接続を開始せず正規APIだけで業務完了'})
                else:
                    await self.providers.refresh_usage(ident, release=True)
            except Exception:
                self.store.event(ident, 'business_accounting_pending', {'message': '業務は完了。モデル料金の照合は管理画面で継続します。'})
            return False
        recovered = {event['payload']['review_id'] for event in self.store.events(ident) if event['kind'] == 'business_recognition_applied'}
        if review and review['state'] == 'approved' and run['task_id'] == 'hospital_review_faxes' and review['id'] not in recovered:
            if model_busy:
                return True
            # Recognition recovery is one approval, quantities/output are another.
            await self.apply_recovery(run, review)
            return True
        if review and review['state'] == 'awaiting_approval' or any(item['state'] == 'awaiting_approval' for item in faxes):
            self.activate(run)
            self.checkpoint(ident, phase='approval')
            return False
        if run['status'] == 'waiting_input' and pending_question(self.store.events(ident)):
            self.checkpoint(ident, phase='question')
            return False
        if model_busy:
            return True
        if run['status'] == 'waiting_input' and state['phase'] in ('analysing', 'checking'):
            await self.continue_idle_model(run)
            return True
        if state['phase'] == 'correction':
            if not run['session_id']:
                self.activate(run)
                self.checkpoint(ident, phase='analysing')
                await self.providers.start(self.store.get(ident))
                return True
            await self.continue_model(run, review['decision']['message'] if review and review.get('decision') else '回答した内容を反映し、次の対象の確認を続けてください。')
            return True
        if state['phase'] == 'shift_adjustment':
            await self.tools.sandbox.start(run)
            self.activate(run)
            from .workflow_completion import OperatorWait
            try:
                result = await self.shift_questions.advance(self.store.get(ident))
            except OperatorWait as wait:
                result = wait.result
            if result is True:
                self.checkpoint(ident, phase='preparing')
                return True
            self.checkpoint(ident, phase='question')
            return False
        if state['phase'] == 'preparing':
            await self.tools.sandbox.start(run)
            self.activate(run)
            run = self.store.get(ident)
            if await self.choose_layout(run):
                return False
            if self.tools.faxes.active(run):
                await self.tools.faxes.refresh(run)
                if not self.tools.faxes.items(ident):
                    await self.call(run, 'complete_fax_work', {}, 'business:empty-faxes')
                    self.checkpoint(ident, phase='completed')
                    self.store.update(ident, reserved=0)
                    return False
            if CONTRACTS[run['task_id']].prepare != 'agent':
                result = await self.prepare_selected(run)
                self.checkpoint(ident, phase='question' if result.get('waiting_input') else 'approval')
                return False
            self.checkpoint(ident, phase='analysing')
            await self.providers.start(run)
            return True
        if run['status'] == 'reconciliation_required' or state['phase'] == 'checking':
            self.set_status(run, 'reconciliation_required')
            self.checkpoint(ident, phase='checking')
            await self.providers.resume(ident)
            return True
        raise Conflict('必要な質問または確認結果を取得できません。保存済み内容を保持して担当者が確認します')

    async def apply_recovery(self, run, review):
        from .fax_workflow import digest
        for index, action in enumerate(review['actions']):
            if digest(action) not in self.tools.flow.review(run['id'])['applied']:
                await self.call(run, action['operation'], {'parameters': {**action['parameters'], 'reason': '利用者が原本と照合し承認した復旧内容'}}, 'business:recovery:' + review['id'] + ':' + str(index))
        self.checkpoint(run['id'], phase='correction')
        self.store.event(run['id'], 'business_recognition_applied', {'review_id': review['id']})
        await self.continue_model(self.store.get(run['id']), '承認された原本・認識の復旧を保存しました。各FAXの数量確認を提出してください。')

    async def drive(self, ident):
        while not self.closed:
            try:
                if not await self.advance(ident):
                    return
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                from .recovery import RecoveryPending
                state = self.state(ident)
                attempt = state.get('attempts', 0) + 1
                delay = min(300, 15 * 2 ** min(attempt, 4))
                retryable = isinstance(exc, (RecoveryPending, httpx.TransportError, TimeoutError, ConnectionError)) or isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500
                self.store.event(ident, 'business_support', {'class': type(exc).__name__, 'message': str(exc) if isinstance(exc, (ValueError, Conflict)) else '業務結果の照合が必要です。回答・承認・保存済み内容は保持しています。'})
                self.checkpoint(ident, phase='support', resume_phase=state.get('resume_phase') if state['phase']=='support' else state['phase'], attempts=attempt, next_attempt=time.time() + delay, retryable=retryable)
                if attempt >= 4 and not retryable:
                    return
                await asyncio.sleep(delay)

    def source_ready(self, run):
        with self.store.tx() as db:
            current = db.execute('SELECT generation,status,task_inputs FROM runs WHERE id=?', (run['id'],)).fetchone()
            if current['generation'] != run['generation'] or current['status'] != 'waiting_input':
                raise Conflict('依頼は開始済み、または停止・変更されています')
            context = json.loads(current['task_inputs'])
            if context['business_workflow']['phase'] != 'source':
                raise Conflict('原本を待っている依頼ではありません')
            context['business_workflow']['phase'] = 'preparing'
            db.execute("UPDATE runs SET task_inputs=?,status='preparing',updated=? WHERE id=?",
                       (json.dumps(context, ensure_ascii=False), now(), run['id']))
        self.kick(run['id'])

    def decision_ready(self, ident, approve):
        run = self.store.get(ident)
        if run['status'] in ('completed', 'stopped', 'rolled_back', 'stop_pending', 'rollback_stop_pending', 'stopping'):
            return
        state = self.state(ident)
        if not approve:
            from .shift_questions import SHIFT_TASKS
            if run['task_id'] in SHIFT_TASKS:
                review = self.tools.flow.review(ident)
                self.store.event(ident, 'business_shift_answer', {'question_id':'review:'+review['id'], 'choice':{'action':'parse', 'text':review['decision']['message']}})
                self.checkpoint(ident, phase='shift_adjustment', epoch=state.get('epoch', 0) + 1)
            else:
                self.checkpoint(ident, phase='correction', epoch=state.get('epoch', 0) + 1)
        elif self.tools.faxes.active(run) and not all(item['state'] in ('approved', 'completed') for item in self.tools.faxes.items(ident)):
            self.checkpoint(ident, phase='correction')
        self.kick(ident)

    def projection(self, run, review=None, faxes=()):
        if not owned(run):
            return {'state': 'completed' if run['status'] == 'completed' else 'archived', 'title': '作業が完了しました' if run['status'] == 'completed' else '以前の試用記録', 'message': '過去の作業記録です。実行状況の確認は管理者が対応します。', 'next_action': 'none'}
        state = run['task_inputs']['business_workflow']
        phase = state['phase']
        if run['status'] == 'completed':
            status, title, message, action = 'completed', '作業が完了しました', '承認した内容の保存と、必要な出力の確認が完了しました。', 'outputs'
        elif run['status'] in ('stopped', 'rolled_back', 'stop_pending', 'rollback_stop_pending', 'stopping'):
            status, title, message, action = 'archived', '作業記録', 'この依頼の作業記録です。処理状況は管理者が確認しています。', 'none'
        elif phase == 'source':
            status, title, message, action = 'source', '原本を添付してください', '選択した学校・対象月の献立・調理手配書を添付してください。', 'source'
        elif phase == 'support' or run['status'] in ('reconciliation_required', 'budget_reached', 'failed'):
            status, title, message, action = 'checking', '保存した内容を確認しています', '回答・承認した内容は保存されています。システム側で確認しており、操作は必要ありません。解決できない場合は管理者が対応します。', 'none'
        elif review and review['state'] == 'awaiting_approval' or any(item['state'] == 'awaiting_approval' for item in faxes):
            status, title, message, action = 'review', '内容を確認してください', '内容がよければ承認してください。その後は保存・帳票作成まで自動で進みます。', 'approve'
        elif run['status'] == 'waiting_input' and pending_question(self.store.events(run['id'])):
            status, title, message, action = 'question', 'この質問にお答えください', '表示された選択肢または入力欄で回答すると、続きの作業へ進みます。', 'answer'
        else:
            status, title, message, action = 'working', '保存・帳票作成を進めています' if phase == 'saving' else '訂正内容を反映しています' if phase == 'correction' or review and review['state']=='needs_correction' else '作業を進めています', '確認が必要になったら、ここに質問や確認する内容が表示されます。', 'none'
        return {'state': status, 'title': title, 'message': message, 'next_action': action, 'deliverables': list(CONTRACTS[run['task_id']].deliverables)}
