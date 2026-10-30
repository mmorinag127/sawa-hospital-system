"""Selected scopes and version-bound human review shared by the named tasks."""
import asyncio
from bisect import bisect_left
from copy import deepcopy
from datetime import date
import json
import shutil
from uuid import uuid4

from .catalog import OPERATIONS, build_path, public_intake_data
from .fax_workflow import digest
from .store import Conflict
from .sandbox import run_blocking
from .tasks import TASKS
from .lunch_questions import LunchQuestions


def month_bounds(value):
    if not isinstance(value, str) or len(value) != 7:
        raise ValueError('対象月を選択してください')
    start = date.fromisoformat(value+'-01')
    end = date(start.year+(start.month == 12), start.month % 12+1, 1)
    return start.isoformat(), end.isoformat()


def rows(data):
    if isinstance(data, list):
        return data
    for key in ('items','staff','constraints','orders','facilities'):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    raise Conflict('対象一覧のAPI応答を確認できません')


def shift_confirmation_content(data, *, before=None):
    """Compare reviewed business data while permitting the native confirmation receipt.

    Confirmation freezes validation and renews command tokens. Neither addition
    is a new assignment or permission to change an existing source snapshot.
    """
    result = deepcopy(data)
    for key in ('confirmed', 'confirmed_at', 'command_precondition'):
        result.pop(key, None)
    if before is None:
        return result
    selected = data.get('selected_candidate_id')
    if before.get('command_precondition') is not None:
        token = data.get('command_precondition')
        if (not isinstance(token, dict) or token.get('candidate_id') != selected
                or set(token) != {'candidate_id', 'candidate_revision', 'input_revision'}
                or any(not isinstance(token.get(key), str) or len(token[key]) != 64
                       or any(char not in '0123456789abcdef' for char in token[key])
                       for key in ('candidate_revision', 'input_revision'))):
            raise Conflict('確定後の勤務候補の版を確認できません')
    prior = {row['id']: row for row in before.get('candidates', [])}
    for candidate in result.get('candidates', []):
        score = candidate.get('score_breakdown', {})
        old_score = prior.get(candidate['id'], {}).get('score_breakdown', {})
        if candidate['id'] != selected or 'confirmed_validation_snapshot' in old_score:
            continue
        snapshot = score.get('confirmed_validation_snapshot')
        if snapshot is None:
            continue
        if (not isinstance(snapshot, dict) or snapshot.get('version') != 1
                or snapshot.get('validation') != before.get('candidate_validation')
                or not isinstance(snapshot.get('rule_source'), dict)
                or not isinstance(snapshot.get('time_rows'), list)):
            raise Conflict('勤務案の確定検査記録が一致しません')
        state = snapshot.get('candidate_state', {})
        for key, fields in (
            ('assignments', ('staff_id', 'work_date', 'shift_template_id', 'position_id', 'is_locked', 'source')),
            ('statuses', ('staff_id', 'work_date', 'status', 'work_kind', 'shift_template_id')),
        ):
            project = lambda values: sorted(digest({field: row.get(field) for field in fields}) for row in values)
            if not isinstance(state.get(key), list) or project(state[key]) != project(before.get(key, [])):
                raise Conflict('勤務案の確定検査記録と承認した勤務が一致しません')
        del score['confirmed_validation_snapshot']
    return result


class TaskFlow:
    def __init__(self, tools):
        self.tools, self.store = tools, tools.store
        self.questions = LunchQuestions(self)

    @staticmethod
    def active(run):
        return run.get('task_id') in TASKS and run['task_id'] != 'hospital_pending_faxes'

    async def read(self, run, name, parameters=None):
        if name == 'shift_get_constraints' and not parameters:
            month = run.get('task_inputs', {}).get('inputs', {}).get('month')
            if not month:
                raise Conflict('予定を読む対象月がありません')
            period = await self.read(run, 'shift_get_period', {'query': {'month': month}})
            parameters = {'query': {'date_from': period['start_date'], 'date_to': period['end_date_exclusive']}}
        if name == 'shift_get_runs' and not parameters:
            result, query = [], {'limit': 200}
            while True:
                page = await self.read(run, name, {'query': query})
                if not isinstance(page, list) or len(page) > 200:
                    raise Conflict('シフト履歴のページを確認できません')
                ids = [item.get('id') for item in page]
                if any(type(ident) is not int or ident <= 0 for ident in ids) or any(a <= b for a, b in zip(ids, ids[1:])):
                    raise Conflict('シフト履歴のID順を確認できません')
                if ids and query.get('before_id') is not None and ids[0] >= query['before_id']:
                    raise Conflict('シフト履歴のページが進みません')
                result.extend(page)
                if len(page) < 200:
                    return result
                query = {'limit': 200, 'before_id': ids[-1]}
        op = OPERATIONS[name]
        parameters = parameters or {}
        response = await self.tools.sandbox.request(run, op.domain, 'GET', build_path(op, parameters.get('ids',{})),
            params=parameters.get('query',{}), authorization=self.tools.authorizations.get(run['id']))
        if not response.is_success or 'application/json' not in response.headers.get('content-type',''):
            raise Conflict(f'{op.description}（HTTP {response.status_code}）')
        from .daily_scope import selected
        return selected(run,name,public_intake_data(name,response.json()))

    async def options(self, task_id, inputs, owner, authorization):
        spec = TASKS[task_id]
        inputs = dict(inputs)
        source_id = inputs.pop('source_run_id',None)
        source_copy = None
        if source_id is not None:
            durable = getattr(self.tools.sandbox,'durable_store',None)
            if (task_id not in ('hospital_review_faxes','hospital_outputs') or durable is None
                    or not isinstance(source_id,str) or len(source_id)!=32
                    or any(c not in '0123456789abcdef' for c in source_id)):
                raise ValueError('保存済みの病院依頼を一覧から選択してください')
            source_copy = await run_blocking(durable.capture_copy,source_id,owner)
            if (task_id=='hospital_outputs') != (source_copy['status']=='completed'):
                raise Conflict('確認用には確認待ち、帳票出力には完了済みの依頼を選択してください')
        if inputs.get('month'):
            month_bounds(inputs['month'])
        if inputs.get('facility_id') is not None and (type(inputs['facility_id']) is not int or inputs['facility_id']<=0): raise ValueError('学校を一覧から選択してください')
        if spec.get('date') and inputs.get('service_date') is not None:
            if not isinstance(inputs.get('service_date'),str): raise ValueError('対象日を選択してください')
            day=date.fromisoformat(inputs['service_date'])
            if day.isoformat()!=inputs['service_date']: raise ValueError('対象日はYYYY-MM-DDで指定してください')
            if inputs.get('month') is not None and inputs['month']!=day.strftime('%Y-%m'): raise ValueError('日付と月が一致しません')
            inputs={**inputs,'month':day.strftime('%Y-%m')}
        if set(inputs) - {'month','facility_id','absence_date','service_date'}:
            raise ValueError('指定されていない入力項目です')
        if task_id == 'shift_absence':
            from .selection_proposals import business_today
            inputs['absence_date'] = inputs.get('absence_date') or business_today().isoformat()
            absent = date.fromisoformat(inputs['absence_date'])

        run = dict(id=uuid4().hex, domains=[spec['domain']], purpose='target_lookup', task_id=task_id,
                   owner=owner,task_inputs={'source_copy':source_copy} if source_copy else {})
        self.tools.authorizations[run['id']] = authorization
        try:
            await run_blocking(self.tools.sandbox.prepare, run)
            await self.tools.sandbox.start(run)
            choices, schools, layouts, work_days = [], [], [], []
            kind = spec.get('target_kind')
            from .selection_proposals import adjacent_month, business_today, month_label, rank_months, school_months, school_output_counts, batches, absence_days
            current = business_today().strftime('%Y-%m')
            month_choices, available_runs = [], []
            if spec['domain'] == 'shift':
                if kind in ('candidate', 'confirmed_candidate'):
                    available_runs = rows(await self.read(run, 'shift_get_runs'))
                    eligible = [item for item in available_runs if kind != 'confirmed_candidate' or item.get('confirmed')]
                    months = [item['target_month'] for item in eligible if item.get('target_month')]
                    counts = {month: sum(item.get('target_month') == month for item in eligible) for month in months}
                    month_choices = [{'month': month, 'label': month_label(month), 'reason': f'保存済み勤務表 {counts[month]}件'} for month in rank_months(months, current)]
                elif task_id == 'shift_absence':
                    period_matches = []
                    for month in (adjacent_month(absent.strftime('%Y-%m'), -1), absent.strftime('%Y-%m'), adjacent_month(absent.strftime('%Y-%m'), 1)):
                        period = await self.read(run, 'shift_get_period', {'query': {'month': month}})
                        if period['start_date'] <= inputs['absence_date'] < period['end_date_exclusive']:
                            period_matches.append(period)
                    if len(period_matches) != 1:
                        raise Conflict('欠勤日に対応する勤務期間を一意に確認できません')
                    inputs['month'] = period_matches[0]['target_month']
                else:
                    source, query, revision = [], {}, None
                    while True:
                        page = await self.read(run, 'shift_get_constraint_dates', {'query': query})
                        current_revision = page.get('collection_revision')
                        if type(current_revision) is not int or (revision is not None and revision != current_revision):
                            raise Conflict('予定が更新されています。月候補を読み直してください')
                        revision = current_revision
                        values = rows(page)
                        previous = query.get('after')
                        for item in values:
                            day = date.fromisoformat(item['work_date']).isoformat()
                            if (previous is not None and day <= previous) or type(item.get('count')) is not int or item['count'] < 1:
                                raise Conflict('予定の日別件数またはページ順が不正です')
                            previous = day
                        source.extend(values)
                        cursor = page.get('next_after')
                        if cursor is None:
                            break
                        if not values or cursor != previous:
                            raise Conflict('予定の日別件数のページが進みません')
                        query = {'after': cursor}
                    counts = {}
                    days = [item['work_date'] for item in source]
                    cumulative = [0]
                    for item in source:
                        cumulative.append(cumulative[-1] + item['count'])
                    candidate_months = {current, adjacent_month(current,1)}
                    for item in source:
                        day = date.fromisoformat(item['work_date'])
                        candidate_months.update(adjacent_month(day.strftime('%Y-%m'), offset) for offset in (-1,0,1))
                    for month in candidate_months:
                        period = await self.read(run, 'shift_get_period', {'query':{'month':month}})
                        count = cumulative[bisect_left(days, period['end_date_exclusive'])] - cumulative[bisect_left(days, period['start_date'])]
                        if count:
                            counts[month] = count
                    preferred = adjacent_month(current, 1) if task_id == 'shift_next_month' else current
                    month_choices = [{'month': month, 'label': month_label(month), 'reason': f'登録済み希望休・勤務条件 {counts[month]}件' if month in counts else '翌月の勤務案を作成します' if month == adjacent_month(current, 1) else 'この月の登録済み条件で作成します'} for month in rank_months([preferred, current, adjacent_month(current, 1), *counts], preferred)]
                if task_id != 'shift_absence' and not inputs.get('month') and month_choices:
                    inputs['month'] = month_choices[0]['month']
            if task_id == 'hospital_pending_faxes':
                pending = rows(await self.read(run, 'hospital_pending_faxes'))
                choices = [dict(key=str(item['id']), label=' ／ '.join(str(item[key]) for key in ('name','facility','week') if item.get(key)), data=item) for item in pending]
            elif kind == 'order':
                facility_source = await self.read(run, 'hospital_list_facilities')
                if facility_source.get('source') != 'canonical_json':
                    raise Conflict('正本の施設一覧を確認できません')
                facility_names = {item['id']:item['name'] for item in rows(facility_source)}
                query = {'limit':1000,'status':'確定','include_archived':False}
                seen = set()
                while True:
                    page = await self.read(run,'hospital_list_orders',{'query':query})
                    for item in rows(page):
                        if item.get('facility') not in facility_names or not item.get('week_label') or not item.get('week_value'):
                            raise Conflict('確定済み注文の正本施設・対象週を確認できません')
                        choices.append(dict(key=str(item['id']), label=f"{facility_names[item['facility']]} ／ {item['week_label']} ／ 受付 {item.get('received_at') or '日時の記録なし'}", data=item, week=item['week_value']))
                    if not page.get('has_more'):
                        break
                    cursor = page.get('next_cursor')
                    if not isinstance(cursor,dict) or digest(cursor) in seen:
                        raise Conflict('注文一覧の続きが取得できません')
                    seen.add(digest(cursor)); query.update(cursor)
            elif kind == 'fax':
                data = await self.read(run,'hospital_review_faxes')
                choices = [dict(key=str(x['id']), label=f"{x['name']} ／ {x.get('facility','')} ／ {x.get('week','')} ／ {x['status']}", data=x) for x in rows(data)]
            elif kind in ('month','staff'):
                staff = rows(await self.read(run,'shift_get_staff'))
                if kind=='staff':
                    period=await self.read(run,'shift_get_period',{'query':{'month':inputs['month']}})
                    if not period['start_date']<=inputs['absence_date']<period['end_date_exclusive']: raise ValueError('欠勤日は設定された勤務期間内で選択してください')
                choices = [dict(key=str(x['id']), label=x['name'], data=x) for x in staff] if kind == 'staff' else [dict(key=inputs['month'],label=inputs['month']+' の勤務計画',data={'month':inputs['month']})]
            elif kind in ('candidate','confirmed_candidate'):
                for item in available_runs:
                    if item.get('target_month') != inputs.get('month'):
                        continue
                    detail = await self.read(run,'shift_get_run',{'ids':{'run_id':item['id']}})
                    if kind=='candidate' and not detail.get('candidates') and detail.get('status') in ('failed','error'):
                        choices.append(dict(key=f"{item['id']}:failed",label=f"{month_label(inputs['month'])} ／ 作成できなかった勤務案・不足理由",data={'run_id':item['id'],'candidate_id':None}))
                    for candidate in detail.get('candidates',[]):
                        if kind == 'confirmed_candidate' and (not detail.get('confirmed') or candidate['id'] != detail.get('selected_candidate_id')):
                            continue
                        choices.append(dict(key=f"{item['id']}:{candidate['id']}",label=f"{month_label(inputs['month'])} ／ {'確定済み' if detail.get('confirmed') else '勤務案'} ／ {detail.get('confirmed_at') or detail.get('created_at') or '作成日時の記録なし'} ／ 候補{len([x for x in choices if x['data']['run_id']==item['id']])+1}",data={'run_id':item['id'],'candidate_id':candidate['id']}))
            else:
                schools = rows(await self.read(run,'lunch_get_schools'))
                if inputs.get('facility_id') is not None and inputs['facility_id'] not in {school['id'] for school in schools}:
                    raise Conflict('一覧にある学校を選択してください')
                if spec.get('month'):
                    counts = school_months(self.tools.sandbox.path(run['id']) / 'data' / 'school-lunch' / 'database.sqlite3', inputs.get('facility_id'))
                    outputs = school_output_counts(self.tools.sandbox.path(run['id']) / 'data' / 'school-lunch' / 'database.sqlite3', inputs.get('facility_id'))
                    months = {month for school, month in counts}
                    if task_id == 'lunch_import':
                        months.update((current, adjacent_month(current, 1)))
                    month_choices = [{'month': month, 'label': month_label(month), 'reason': f"登録済み献立 {sum(count for (school, value), count in counts.items() if value == month)}件・作業工程表 {outputs.get(month, {}).get('作業工程表', 0)}日分・動線図 {outputs.get(month, {}).get('動線図', 0)}日分。最新の入力と保存済み帳票を処理時に照合します" if any(value == month for school, value in counts) else '取り込む原本の対象月を確認してください'} for month in rank_months(months, current)]
                    if not inputs.get('month') and month_choices:
                        inputs['month'] = month_choices[0]['month']
                if task_id == 'lunch_daily_bundle' and inputs.get('facility_id'):
                    if inputs['facility_id'] not in {school['id'] for school in schools}:
                        raise Conflict('一覧にある学校を選択してください')
                    from .work_day_proposals import propose
                    work_days = propose(self.tools.sandbox.path(run['id']) / 'data' / 'school-lunch' / 'database.sqlite3', inputs['facility_id'])
                if kind == 'school':
                    choices = [dict(key=str(x['id']),label=x['name'],data=x) for x in schools]
                elif task_id == 'lunch_daily_bundle' and inputs.get('facility_id') and not inputs.get('service_date'):
                    menus = []
                    for month in sorted({item['service_date'][:7] for item in work_days}):
                        menus.extend(rows(await self.read(run, 'lunch_get_menus', {'query': {'facility_id': inputs['facility_id'], 'month': month}})))
                    choices = [dict(key=str(x['id']), label=f"{x['service_date']} ／ {x['name']}", data=x) for x in menus]
                    layouts = rows(await self.read(run, 'lunch_get_layouts', {'query': {'facility_id': inputs['facility_id']}}))
                    layouts = [{'id':x['id'],'name':x['name'],'version':x['version']} for x in layouts]
                elif inputs.get('facility_id') and inputs.get('month'):
                    if str(inputs['facility_id']) not in {str(x['id']) for x in schools}:
                        raise Conflict('一覧にある学校を選択してください')
                    query = {'facility_id':int(inputs['facility_id']),'month':inputs['month']}
                    menus = rows(await self.read(run,'lunch_get_menus',{'query':query}))
                    if spec.get('date'): menus=[x for x in menus if x['service_date']==inputs['service_date']]
                    choices = [dict(key=str(x['id']),label=f"{x['service_date']} ／ {x['name']}",data=x) for x in menus]
                    layouts = rows(await self.read(run,'lunch_get_layouts',{'query':{'facility_id':query['facility_id']}}))
                    layouts = [{'id':x['id'],'name':x['name'],'version':x['version']} for x in layouts]
            ident = uuid4().hex
            payload = {'inputs':inputs,'choices':choices,'schools':schools,'layouts':layouts,'work_days':work_days,
                       'month_choices':month_choices,'batches':batches(choices, task_id),
                       'absence_days':absence_days() if task_id == 'shift_absence' else []}
            if source_copy:
                payload['source_copy'] = source_copy
            def save_selection():
                with self.store.tx() as db:
                    db.execute('INSERT INTO selections VALUES(?,?,?,?)',(ident,owner,task_id,json.dumps(payload,ensure_ascii=False)))
            await run_blocking(save_selection)
            return {'selection_id':ident, **payload}
        finally:
            await self.tools.sandbox.stop(run['id'])
            directory = self.tools.sandbox.path(run['id'])
            if directory.exists():
                await run_blocking(shutil.rmtree, directory)
            self.tools.authorizations.pop(run['id'],None)

    def selection(self, task_id, owner, selection_id, keys, layout_id=None):
        with self.store.read() as db:
            row = db.execute('SELECT * FROM selections WHERE id=? AND owner=? AND task_id=?',(selection_id,owner,task_id)).fetchone()
        if not row or not keys or len(keys) != len(set(keys)):
            raise ValueError('対象一覧から作業対象を選択してください')
        payload = json.loads(row['payload'])
        selected = [x for x in payload['choices'] if x['key'] in keys]
        if len(selected) != len(keys):
            raise Conflict('対象一覧にない値は選択できません')
        kind = TASKS[task_id]['target_kind']
        if kind in ('month','school','staff') and len(selected) != 1:
            raise ValueError('作業対象は一つ選択してください')
        inputs = dict(payload['inputs'])
        if TASKS[task_id].get('month'):
            month_bounds(inputs.get('month'))
        if kind == 'school':
            inputs['facility_id'] = selected[0]['data']['id']
        if kind == 'menu':
            dates={x['data']['service_date'] for x in selected}
            if TASKS[task_id].get('date'):
                if len(dates) != 1:
                    raise Conflict('提案された作業日を一つ選択してください')
                inputs['service_date'] = next(iter(dates))
                inputs['month'] = inputs['service_date'][:7]
            if {x['key'] for x in payload['choices'] if x['data']['service_date'] in dates} != set(keys):
                raise Conflict('対象日の献立をすべて選択してください')
            layout = next((x for x in payload['layouts'] if x['id'] == layout_id),None)
            if not layout and TASKS[task_id].get('date') and layout_id is None:
                if len(payload['layouts'])==1: layout=payload['layouts'][0]
            elif not layout:
                raise ValueError('使用する図面を一覧から選択してください')
            if layout:
                inputs['layout_id']=layout['id']; inputs['layout_version']=layout['version']
            if TASKS[task_id].get('date'):
                inputs['layout_choices']=payload['layouts']
        context = {'inputs':inputs,'targets':selected}
        if payload.get('source_copy'):
            context['source_copy'] = payload['source_copy']
        return context

    def choose_layout(self, run, layout_id):
        if run.get('task_id')!='lunch_daily_bundle': raise Conflict('日次の図面選択ではありません')
        inputs=run['task_inputs']['inputs']
        layout=next((x for x in inputs['layout_choices'] if x['id']==layout_id),None)
        reply = self.store.latest_event(run['id'], 'operator_reply')
        replies = [reply] if reply else []
        if not layout or not replies or replies[-1]['payload']['message']!=f"{layout['name']}（ID:{layout['id']}）":
            raise Conflict('図面候補をチャットで質問し、利用者の回答を取得してください')
        review=self.review(run['id'])
        if review and (inputs.get('layout_id'),inputs.get('layout_version'))!=(layout_id,layout['version']):
            review['state']='stale'; self.save_review(run['id'],review)
        context=run['task_inputs']; context['inputs']={**inputs,'layout_id':layout_id,'layout_version':layout['version']}
        self.store.update(run['id'],task_inputs=context)
        return {'ok':True,'layout':layout}

    def review(self, run_id):
        with self.store.read() as db:
            row = db.execute('SELECT payload FROM task_reviews WHERE run_id=?',(run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_review(self, run_id, review):
        with self.store.tx() as db:
            db.execute('INSERT INTO task_reviews VALUES(?,?) ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload', (run_id,json.dumps(review,ensure_ascii=False)))

    def commands(self, run_id):
        with self.store.read() as db:
            data = db.execute("SELECT rowid,* FROM commands WHERE run_id=? AND status='done'",(run_id,)).fetchall()
        return [{**dict(x),'request':json.loads(x['request']),'result':json.loads(x['result'])} for x in data]

    def context(self, run):
        if not self.active(run):
            raise ValueError('選択式の業務でのみ使えます')
        return {'ok':True, 'task':TASKS[run['task_id']]['label'],**run['task_inputs'],'review':self.review(run['id'])}

    async def capture_shift_reference(self, run):
        if not self.active(run) or not run['task_id'].startswith('shift_'):
            return
        if any(event['kind']=='shift_reference' for event in await run_blocking(self.store.events, run['id'])):
            return
        month=run['task_inputs']['inputs']['month']
        period=await self.read(run,'shift_get_period',{'query':{'month':month}})
        candidates=[row for row in rows(await self.read(run,'shift_get_runs'))
                    if row.get('target_month')==month and row.get('confirmed') is True
                    and all(row.get(key)==period[key] for key in ('start_date','end_date_exclusive'))]
        reference={'state':'none' if not candidates else 'ambiguous'}
        if len(candidates)==1:
            detail=await self.read(run,'shift_get_run',{'ids':{'run_id':candidates[0]['id']}})
            if detail.get('confirmed') is not True or detail.get('selected_candidate_id') is None:
                raise Conflict('比較元の確定勤務表を確認できません')
            reference={'state':'available','data':detail}
        await run_blocking(self.store.event, run['id'],'shift_reference',reference)

    async def scoped(self, run, name, args, *, inventory=False):
        if not self.active(run):
            return
        spec = TASKS[run['task_id']]
        op = OPERATIONS[name]
        from .shift_questions import authorized
        if authorized(self.tools, run, name, args):
            return
        if 'price' in name and op.domain=='school-lunch' and run['task_id']!='lunch_daily_bundle':
            raise Conflict('実施価格は日次の一括作成で扱います')
        if op.domain != spec['domain']:
            raise Conflict('選択した業務の対象外です')
        context = run['task_inputs']; inputs = context.get('inputs',{}); targets = context.get('targets',[])
        if not targets:
            raise Conflict('選択対象がありません')
        if op.method != 'GET' and name not in spec['writes']+spec['terminal']:
            if run['task_id'] != 'hospital_review_faxes' or name not in {'hospital_set_context','hospital_select_ocr','hospital_save_quad_review','hospital_save_header_axis_review','hospital_save_row_axis_review','hospital_pre_save','hospital_check_anomalies','hospital_review_sheet','hospital_save_sheet','hospital_bag_order','hospital_confirm_order'}:
                raise Conflict('選択した作業にはこの変更は含まれません')
        if op.domain == 'hospital':
            fax_ids = {str(x['data']['id']) for x in targets}
            if spec['target_kind']=='fax':
                await self.tools.faxes.initialize(run)
                order_ids={str(x['snapshot']['order_id']) for x in (await run_blocking(self.tools.faxes.items, run['id'])) if x['snapshot'].get('order_id')}
            else:
                order_ids=fax_ids
            if args.ids.get('uploaded_pdf_id') is not None and str(args.ids['uploaded_pdf_id']) not in fax_ids:
                raise Conflict('選択したFAXの対象外です')
            if args.ids.get('order_id') is not None and str(args.ids['order_id']) not in order_ids:
                raise Conflict('選択した注文の対象外です')
            if name.startswith('hospital_export_') and str(args.query.get('order_id')) not in order_ids:
                raise Conflict('選択した注文を一件ずつ指定してください')
        elif op.domain == 'shift':
            if name in ('shift_get_demands','shift_get_workdays','shift_get_constraints'):
                period=await self.read(run,'shift_get_period',{'query':{'month':inputs['month']}})
                start,end=period['start_date'],period['end_date_exclusive']
            if name in ('shift_get_demands','shift_get_workdays') and args.query != {'start':start,'end':end}:
                raise Conflict('選択月の開始日と翌月開始日をstart・endに指定してください')
            if name == 'shift_get_constraints' and args.query != {'date_from':start,'date_to':end}:
                raise Conflict('選択期間をdate_from・date_toに指定してください')
            if name in ('shift_get_overview','shift_get_period') and args.query.get('month')!=inputs['month']:
                raise Conflict('選択月を明示してください')
            if name in ('shift_solve','shift_preflight') and (args.body.get('target_month') != inputs['month'] or args.body.get('start_date') or args.body.get('end_date_exclusive')):
                raise Conflict('parameters.body.target_monthに選択月を指定し、body.start_date・body.end_date_exclusiveは送らないでください。勤務期間は正規APIが解決します')
            if name in ('shift_save_constraint','shift_update_constraint'):
                period=await self.read(run,'shift_get_period',{'query':{'month':inputs['month']}})
                if not period['start_date']<=inputs['absence_date']<period['end_date_exclusive']: raise Conflict('欠勤日が正規の勤務期間外です')
                if str(args.body.get('staff_id')) not in {x['key'] for x in targets} or args.body.get('work_date') != inputs.get('absence_date') or args.body.get('status') != 'unavailable' or args.body.get('strength') != 'hard':
                    raise Conflict('指定された職員・欠勤日だけを勤務不可にできます')
                if name == 'shift_update_constraint':
                    values = rows(await self.read(run,'shift_get_constraints',{'query':{
                        'date_from':period['start_date'],'date_to':period['end_date_exclusive']}}))
                    current = next((x for x in values if str(x['id']) == str(args.ids['constraint_id'])),None)
                    if not current or str(current['staff_id']) != str(args.body['staff_id']) or current['work_date'] != args.body['work_date']:
                        raise Conflict('別職員・別日の勤務条件を変更できません')
            allowed_runs = {str(x['data']['run_id']) for x in targets if 'run_id' in x['data']}
            allowed_candidates = {str(x['data']['candidate_id']) for x in targets if 'candidate_id' in x['data']}
            for command in await run_blocking(self.commands, run['id']):
                if command['request']['name'] == 'shift_solve' and command['result'].get('ok'):
                    data = command['result'].get('data',{})
                    if data.get('solve_run_id') is not None:
                        allowed_runs.add(str(data['solve_run_id']))
            if 'run_id' in args.ids:
                if str(args.ids['run_id']) not in allowed_runs:
                    raise Conflict('選択・作成した勤務案の対象外です')
                candidate = args.query.get('candidate_id',args.body.get('candidate_id'))
                failed_report=run['task_id']=='shift_check' and any(str(x['data'].get('run_id'))==str(args.ids['run_id']) and x['data'].get('candidate_id') is None for x in targets)
                if name in ('shift_get_run','shift_confirm','shift_export') and candidate is None and not (name=='shift_get_run' and (inventory or failed_report)):
                    raise Conflict('勤務候補を明示してください')
                if allowed_candidates and str(candidate) not in allowed_candidates:
                    raise Conflict('選択した勤務候補の対象外です')
                if candidate is not None:
                    detail = await self.read(run,'shift_get_run',{'ids':{'run_id':args.ids['run_id']},'query':{'candidate_id':candidate}})
                    if str(detail.get('selected_candidate_id')) != str(candidate):
                        raise Conflict('勤務候補と勤務案が一致しません')
            if name == 'shift_get_validation':
                if allowed_candidates and str(args.ids['candidate_id']) not in allowed_candidates:
                    raise Conflict('選択した勤務候補の対象外です')
                if not allowed_candidates:
                    found = False
                    for ident in allowed_runs:
                        detail = await self.read(run,'shift_get_run',{'ids':{'run_id':ident}})
                        found |= any(str(x['id'])==str(args.ids['candidate_id']) for x in detail.get('candidates',[]))
                    if not found: raise Conflict('作成した勤務候補ではありません')
        else:
            facility_id = inputs['facility_id']; query = {'facility_id':facility_id,'month':inputs['month']}
            if name != 'lunch_get_schools' and str(args.query.get('facility_id')) != str(facility_id):
                raise Conflict('選択した学校の対象外です')
            if args.query.get('month') is not None and args.query['month'] != inputs['month']:
                raise Conflict('選択した月の対象外です')
            if args.body.get('facility_id') is not None and str(args.body['facility_id']) != str(facility_id):
                raise Conflict('選択した学校の対象外です')
            menus = rows(await self.read(run,'lunch_get_menus',{'query':query}))
            if spec['target_kind']=='menu':
                by_id={str(x['id']):x for x in menus}
                if any(x['key'] not in by_id or by_id[x['key']]['version']!=x['data']['version'] for x in targets):
                    raise Conflict('対象選択後に献立の版が変わっています。選び直してください')
            menu_ids = {x['key'] for x in targets} if spec['target_kind']=='menu' else {str(x['id']) for x in menus}
            dates = {x['data']['service_date'] for x in targets} if spec['target_kind']=='menu' else {x['service_date'] for x in menus}
            if 'menu_id' in args.ids and str(args.ids['menu_id']) not in menu_ids:
                raise Conflict('選択した献立の対象外です')
            if spec['target_kind']=='menu':
                # Day-level APIs include every dish on that day; never silently
                # expand a partial dish selection to other dishes.
                if {str(x['id']) for x in menus if x['service_date'] in dates} != menu_ids:
                    raise Conflict('工程表・動線図は対象日の献立をすべて選択してください')
            if 'service_date' in args.query and args.query['service_date'] not in dates: raise Conflict('選択した献立日の対象外です')
            if 'service_date' in args.body and args.body['service_date'] not in dates:
                raise Conflict('選択した献立日の対象外です')
            if args.query.get('facility_name') is not None:
                school=targets[0]['data'] if spec['target_kind']=='school' else targets[0]['data']['facility']
                if args.query['facility_name']!=school['name']: raise Conflict('選択した学校名と出力先が一致しません')
            if args.body.get('facility_name') is not None:
                school = next((x['data'] if spec['target_kind']=='school' else x['data']['facility'] for x in targets),None)
                if not school or args.body['facility_name'] != school['name']:
                    raise Conflict('選択した学校名と一致しません')
            if run['task_id']=='lunch_daily_bundle':
                day=inputs['service_date']
                if 'implementation_date' in args.query and args.query['implementation_date']!=day: raise Conflict('実施価格の日付が対象外です')
                if name=='lunch_save_price_source':
                    if args.body.get('implementation_date')!=day or set(map(str,args.body.get('source_menu_execution_ids',[])))!=menu_ids or (args.body.get('source_menu_execution_id') is not None and str(args.body['source_menu_execution_id']) not in menu_ids):
                        raise Conflict('実施価格は対象日の全献立を明示してください')
                if name in ('lunch_get_price_source','lunch_get_price_result','lunch_export_price'):
                    payload=await self.read(run,'lunch_get_price_source',{'ids':args.ids,'query':{'facility_id':facility_id}})
                    source_ids=set(map(str,payload.get('source_menu_execution_ids',[])))
                    if payload.get('source_menu_execution_id') is not None: source_ids.add(str(payload['source_menu_execution_id']))
                    if payload.get('facility_id')!=facility_id or payload.get('implementation_date')!=day or source_ids!=menu_ids:
                        raise Conflict('実施価格の学校・日付・献立が一致しません')
            if name == 'lunch_generate_route':
                if args.body.get('layout_id') != inputs.get('layout_id'): raise Conflict('選択した図面を明示してください')
                layouts=rows(await self.read(run,'lunch_get_layouts',{'query':{'facility_id':facility_id}}))
                if not any(x['id']==inputs['layout_id'] and x['version']==inputs['layout_version'] for x in layouts): raise Conflict('選択後に図面が変更されています')
            for key, operation in [('schedule_id','lunch_get_schedules'),('diagram_id','lunch_get_routes')]:
                if key in args.ids:
                    resources = rows(await self.read(run,operation,{'query':query}))
                    if not any(str(x['id']) == str(args.ids[key]) and x['service_date'] in dates for x in resources):
                        raise Conflict('選択した学校・献立日に対応する結果ではありません')
            if name in ('lunch_import_source','lunch_confirm_import','lunch_save_procurement'):
                if str(args.query.get('facility_id',args.body.get('facility_id'))) != str(facility_id) or args.query.get('month',args.body.get('month')) != inputs['month']:
                    raise Conflict('学校・対象月を明示してください')
            imported={str(x['result'].get('data',{}).get('source_document_id')) for x in await run_blocking(self.commands, run['id']) if x['request']['name']=='lunch_import_source' and x['result'].get('ok')}
            if name == 'lunch_confirm_import':
                if str(args.body.get('source_document_id')) not in imported: raise Conflict('今回添付した原本の抽出結果ではありません')
                school_name=targets[0]['data']['name']
                if any(x.get('facility_name') != school_name or str(x.get('service_date',''))[:7] != inputs['month'] for x in args.body.get('menus',[])):
                    raise Conflict('確定する献立の学校・月が一致しません')
                documents = rows(await self.read(run,'lunch_get_source_documents',{'query':query}))
                if not any(str(x['id']) == str(args.body.get('source_document_id')) for x in documents):
                    raise Conflict('選択した学校・月の取込原本ではありません')
            if 'job_id' in args.ids or 'document_id' in args.ids:
                documents = rows(await self.read(run,'lunch_get_source_documents',{'query':query}))
                field = 'ocr_job_id' if 'job_id' in args.ids else 'id'
                ident = args.ids.get('job_id',args.ids.get('document_id'))
                if not any(str(x.get(field)) == str(ident) and (run['task_id']!='lunch_import' or str(x['id']) in imported) for x in documents):
                    raise Conflict('選択した学校・月の原本ではありません')
            if name == 'lunch_import_source':
                source = next((e['payload'] for e in reversed(await run_blocking(self.store.events, run['id'])) if e['kind']=='source' and e['payload']['source_id']==args.source_id),None)
                if not source or source['binding'] != {'facility_id':str(facility_id),'month':inputs['month']}:
                    raise Conflict('選択した学校・月と照合済みの添付原本が必要です')

    async def recheck(self, run, review):
        await self.questions.gate(run, 'review')
        documents = []
        for evidence in review['evidence']:
            documents.append({'operation':evidence['operation'],'parameters':evidence['parameters'],
                'data':await self.read(run,evidence['operation'],evidence['parameters'])})
        if digest(documents) != review['signature']:
            review['state']='stale'; await run_blocking(self.save_review, run['id'],review)
            raise Conflict('確認後に入力または結果が変わりました。再確認が必要です')
        return documents

    async def submit(self, run, summary, evidence_commands, actions):
        if not self.active(run) or not summary.strip():
            raise ValueError('選択式業務の確認結果が必要です')
        await self.questions.gate(run, 'review')
        commands = {x['id']:x for x in await run_blocking(self.commands, run['id'])}
        evidence = []
        for ident in evidence_commands:
            command = commands.get(ident)
            if not command or not command['result'].get('ok'):
                raise Conflict('成功した再取得命令を指定してください')
            name = command['request']['name']; op = OPERATIONS.get(name)
            if not op or op.method != 'GET' or 'data' not in command['result']:
                raise Conflict('結果のネイティブGETが必要です')
            parameters = command['request']['arguments'].get('parameters',{})
            from .tools import Arguments
            await self.scoped(run,name,Arguments.model_validate(parameters))
            data = await self.read(run,name,parameters)
            if data != command['result']['data']:
                raise Conflict('エージェントの確認後に結果が変わりました。再取得して照合してください')
            evidence.append({'operation':name,'parameters':parameters,'data':data})
        self.coverage(run,evidence)
        planned = []
        for action in actions:
            if set(action) != {'operation','parameters'} or action['operation'] not in TASKS[run['task_id']]['terminal']:
                raise Conflict('承認できる確定操作ではありません')
            from .tools import Arguments
            args = Arguments.model_validate(action['parameters'])
            await self.scoped(run,action['operation'],args)
            normalized = {'ids':args.ids,'query':args.query,'body':args.body,'source_id':args.source_id}
            if action['operation']=='shift_confirm':
                pair=(str(args.ids['run_id']),str(args.query.get('candidate_id',args.body.get('candidate_id'))))
                if pair not in {(str(x['parameters']['ids']['run_id']),str(x['parameters']['query']['candidate_id'])) for x in evidence if x['operation']=='shift_get_run'}:
                    raise Conflict('確認に提示した勤務候補と確定対象が一致しません')
            if action['operation']=='hospital_accept_recognition':
                matching=[x for x in evidence if x['operation']=='hospital_get_recognition_review' and str(x['parameters'].get('ids',{}).get('order_id'))==str(args.ids['order_id'])]
                if not matching or matching[0]['data'].get('source_receipt') != args.body.get('source_receipt'):
                    raise Conflict('原本セルと認識訂正の出典を提示してください')
            planned.append({'operation':action['operation'],'parameters':normalized})
        spec = TASKS[run['task_id']]
        if spec['terminal'] and not planned:
            raise Conflict('確定する操作とその内容を提示してください')
        if len({digest(x) for x in planned}) != len(planned):
            raise Conflict('同じ確定操作を重複して提示できません')
        review = {'id':uuid4().hex,'state':'awaiting_approval','summary':summary,'evidence':evidence,
                  'signature':digest(evidence),'actions':planned,'applied':[],'decision':None}
        await run_blocking(self.save_review, run['id'],review)
        await run_blocking(self.store.event, run['id'],'task_review',{'review_id':review['id'],'summary':summary})
        return {'ok':True,'queued_for_human':True,'review_id':review['id']}

    async def decide(self, run, review_id, approve, message):
        if run['status'] != 'running' or not self.active(run):
            raise Conflict('この依頼は回答を受け付けていません')
        review = await run_blocking(self.review, run['id'])
        if not review or review['id'] != review_id:
            raise Conflict('古い確認画面です')
        decision = {'approve':approve,'message':message}
        if review['decision'] == decision:
            return {'ok':True,'replayed':True}
        if review['state'] != 'awaiting_approval':
            raise Conflict('既に回答済み、または内容が変更されています')
        await self.recheck(run,review)
        if (await run_blocking(self.store.get, run['id']))['generation'] != run['generation']:
            raise Conflict('依頼は停止されました')
        if not approve and not message.strip():
            raise ValueError('訂正してほしい箇所を入力してください')
        def last_command_id():
            with self.store.read() as db:
                return db.execute('SELECT COALESCE(MAX(rowid),0) FROM commands WHERE run_id=?',(run['id'],)).fetchone()[0]
        last_command = await run_blocking(last_command_id)
        review.update(state='approved' if approve else 'needs_correction',decision=decision,approved_after=last_command)
        await run_blocking(self.save_review, run['id'],review)
        await run_blocking(self.store.event, run['id'],'task_decision',{'review_id':review_id,**decision})
        return {'ok':True}

    async def gate(self, run, name, args):
        if not self.active(run): return
        await self.scoped(run,name,args)
        if name in {'lunch_save_process','lunch_get_schedule_draft','lunch_create_schedule','lunch_save_schedule','lunch_generate_route','lunch_save_route'} or name.startswith('lunch_export_'):
            await self.questions.gate(run, name, args)
        spec = TASKS[run['task_id']]
        review = await run_blocking(self.review, run['id'])
        terminal = name in spec['terminal']
        if terminal or name in spec['exports']:
            if not review or review['state'] != 'approved':
                raise Conflict('対象結果の利用者承認が必要です')
            await self.recheck(run,review)
        if name in spec['exports'] and spec['domain']=='school-lunch' and name not in ('lunch_export_procurement','lunch_export_price'):
            operation='lunch_get_schedules' if 'schedule' in name else 'lunch_get_routes'
            resources=next((x['data'] for x in review['evidence'] if x['operation']==operation),None)
            if resources is None: raise Conflict('確認済みの出力対象一覧がありません')
            selected=rows(resources)
            key='schedule_id' if operation=='lunch_get_schedules' else 'diagram_id'
            if key in args.ids: selected=[x for x in selected if str(x['id'])==str(args.ids[key])]
            sources=[]
            for item in selected:
                source={k:item[k] for k in ('id','version','service_date')}
                source['facility_id']=item.get('facility_id',item.get('facility',{}).get('id'))
                if operation=='lunch_get_routes': source.update({k:item[k] for k in ('layout_id','layout_version')})
                sources.append(source)
            try: received=json.loads(args.query.get('expected_sources',''))
            except (ValueError,TypeError): raise Conflict('出力対象のID・版・学校・日付をexpected_sourcesに明示してください') from None
            if received != sources: raise Conflict('承認した帳票の版と出力指定が一致しません')
        if name=='lunch_export_procurement':
            result=next((x for x in review['evidence'] if x['operation']=='lunch_get_procurement'),None)
            if not result or not isinstance(result['data'].get('fingerprint'),str) or args.query.get('expected_fingerprint')!=result['data']['fingerprint']: raise Conflict('承認した月間発注案の版を指定してください')
        if name=='lunch_export_price':
            result=next((x for x in review['evidence'] if x['operation']=='lunch_get_price_result' and x['parameters']['ids']==args.ids),None)
            if not result or args.query.get('expected_fingerprint')!=result['data']['fingerprint']: raise Conflict('承認した実施価格の版を指定してください')
        if terminal:
            wanted = {'operation':name,'parameters':{'ids':args.ids,'query':args.query,'body':args.body,'source_id':args.source_id}}
            if wanted not in review['actions'] or digest(wanted) in review['applied']:
                raise Conflict('承認された未実行の確定内容と一致しません')
            return digest(wanted)
        if review and review['state']=='approved' and OPERATIONS[name].method != 'GET':
            # Computation/edit after review must be reviewed again, including a
            # change followed by a change back to the old visible value.
            review['state']='stale'; await run_blocking(self.save_review, run['id'],review)

    async def verify_output(self, run, name):
        if self.active(run) and name in TASKS[run['task_id']]['exports']:
            review=await run_blocking(self.review, run['id'])
            if not review or review['state']!='approved': raise Conflict('出力の承認がありません')
            await self.recheck(run,review)

    async def after(self, run, name, args, receipt):
        if name == 'lunch_save_process': await self.questions.after_save(run, args)
        if not self.active(run) or name not in TASKS[run['task_id']]['terminal']:
            return
        review = await run_blocking(self.review, run['id'])
        wanted = {'operation':name,'parameters':{'ids':args.ids,'query':args.query,'body':args.body,'source_id':args.source_id}}
        if not review or review['state']!='approved' or wanted not in review['actions']:
            raise Conflict('確定の承認記録を照合できません')
        if digest(wanted) in review['applied']:
            await self.recheck(run, review)
            return
        if name=='shift_confirm' and (receipt.get('confirmed') is not True or str(receipt.get('solve_run_id'))!=str(args.ids['run_id'])):
            raise Conflict('勤務案の確定結果を照合できません')
        review['applied'].append(digest(wanted))
        # Rebind only after an explicitly approved native transaction succeeded.
        # A failed/unknown operation never advances this state.
        for entry in review['evidence']:
            before=entry['data']; after=await self.read(run,entry['operation'],entry['parameters'])
            stable_before=before; stable_after=after
            if name=='shift_confirm' and entry['operation']=='shift_get_run':
                stable_before=shift_confirmation_content(before)
                stable_after=shift_confirmation_content(after, before=before)
                if after.get('confirmed') is not True: raise Conflict('確定した勤務案を再取得できません')
            elif name=='shift_confirm' and entry['operation']=='shift_get_runs':
                # Only this transaction's selected run may change confirmation.
                # Inventory membership and every other field remain immutable.
                stable_before=deepcopy(before); stable_after=deepcopy(after)
                prior=rows(stable_before); current=rows(stable_after)
                selected_before=[row for row in prior if str(row.get('id'))==str(args.ids['run_id'])]
                selected_after=[row for row in current if str(row.get('id'))==str(args.ids['run_id'])]
                if len(selected_before)!=1 or len(selected_after)!=1 or selected_after[0].get('confirmed') is not True:
                    raise Conflict('確定した勤務案を一覧で照合できません')
                for row in (selected_before[0],selected_after[0]):
                    for key in ('confirmed','confirmed_at'): row.pop(key,None)
            elif name=='lunch_save_source_review' and entry['operation']=='lunch_get_source_review' and entry['parameters']['ids']['job_id']==args.ids['job_id']:
                if after != receipt: raise Conflict('保存した原本確認記録と再取得結果が一致しません')
                stable_before=stable_after
            elif name=='hospital_accept_recognition' and entry['operation']=='hospital_get_recognition_review':
                # Acceptance changes workflow concurrency identity, never original
                # checkpoint cells, PDF identity or the recognized values.
                stable_before={**before,'source_receipt':{k:v for k,v in before.get('source_receipt',{}).items() if k not in ('workflow_revision','revision')}}
                stable_after={**after,'source_receipt':{k:v for k,v in after.get('source_receipt',{}).items() if k not in ('workflow_revision','revision')}}
            elif name=='hospital_retry_fax' and entry['operation']=='hospital_get_fax' and str(entry['parameters']['ids']['uploaded_pdf_id'])==str(args.ids['uploaded_pdf_id']):
                fields=('id','content_sha256','facility_hint','week_hint','original_filename','storage_uri','skip_ocr')
                stable_before={k:before.get(k) for k in fields}; stable_after={k:after.get(k) for k in fields}
            if stable_before != stable_after:
                review['state']='stale'; await run_blocking(self.save_review, run['id'],review)
                raise Conflict('確定中に確認対象の内容が変わりました。結果を再確認してください')
            entry['data']=after
        review['signature'] = digest(review['evidence'])
        await run_blocking(self.save_review, run['id'],review)

    def coverage(self, run, evidence):
        spec = TASKS[run['task_id']]; targets=run['task_inputs']['targets']; inputs=run['task_inputs']['inputs']
        if not evidence: raise Conflict('確認する保存結果がありません')
        if not spec['report'] and run['task_id'] != 'hospital_review_faxes':
            if any(self.tools.unresolved_evidence(x['operation'],x['data']) for x in evidence):
                raise Conflict('未完了・古い入力・未解決の問題が残っています')
        def entries(name): return [x for x in evidence if x['operation']==name]
        if spec['domain']=='hospital':
            if run['task_id']=='hospital_outputs':
                for target in targets:
                    if not any(str(x['parameters'].get('ids',{}).get('order_id'))==target['key'] and x['data'].get('state')=='confirmed' for x in entries('hospital_get_workflow')):
                        raise Conflict('選択した確定済み注文の状態が不足しています')
            else:
                for target in targets:
                    order_id=target['data'].get('order_id')
                    if not any(str(x['parameters'].get('ids',{}).get('order_id'))==str(order_id) for x in entries('hospital_get_recognition_review')) and not any(str(x['parameters'].get('ids',{}).get('uploaded_pdf_id'))==target['key'] for x in entries('hospital_get_fax')):
                        raise Conflict('対象FAXの原本・復旧内容の取得が不足しています')
        elif spec['domain']=='shift':
            details=entries('shift_get_run')
            if spec['terminal'] and len(details)!=1: raise Conflict('今回確定する勤務候補を一つ提示してください')
            if not details: raise Conflict('勤務候補の取得が必要です')
            expected={(str(x['data']['run_id']),str(x['data']['candidate_id'])) for x in targets if 'run_id' in x['data']}
            actual={(str(x['parameters'].get('ids',{}).get('run_id')),str(x['parameters'].get('query',{}).get('candidate_id'))) for x in details}
            if expected and actual != expected: raise Conflict('選択した全勤務候補を確認してください')
            for entry in details:
                candidate=entry['parameters'].get('query',{}).get('candidate_id')
                if candidate is None:
                    if not spec['report'] or entry['data'].get('status') not in ('failed','error') or entry['data'].get('candidates'):
                        raise Conflict('確認対象の勤務候補がありません')
                    continue
                if not any(str(x['parameters'].get('ids',{}).get('candidate_id'))==str(candidate) for x in entries('shift_get_validation')):
                    raise Conflict('勤務候補の不足・違反の検証結果が必要です')
                data=entry['data']
                if data.get('target_month') != inputs['month']:
                    raise Conflict('勤務候補の対象月が一致しません')
                if run['task_id']=='shift_export' and not data.get('confirmed'): raise Conflict('未確定の勤務表です')
            if spec['terminal'] and not all(entries(name) for name in ('shift_get_staff','shift_get_constraints','shift_get_settings','shift_get_period','shift_get_demands','shift_get_workdays')):
                raise Conflict('勤務案の元になった職員・希望休・勤務条件・勤務期間・必要人数の取得が不足しています')
            if run['task_id']=='shift_absence':
                constraints=rows(entries('shift_get_constraints')[0]['data'])
                for target in targets:
                    if not any(str(x.get('staff_id'))==target['key'] and x.get('work_date')==inputs['absence_date'] and x.get('status')=='unavailable' and x.get('strength')=='hard' for x in constraints):
                        raise Conflict('指定された欠勤が勤務条件に反映されていません')
        else:
            if run['task_id']=='lunch_import':
                if not entries('lunch_get_source_document'): raise Conflict('原本抽出の再取得結果が必要です')
            elif run['task_id']=='lunch_procurement':
                result=entries('lunch_get_procurement')
                if not result or any(x['data'].get('issues') or x['data'].get('inventory_ready') is False for x in result):
                    raise Conflict('在庫・価格・発注先などの不足を解決してください')
            else:
                if run['task_id']=='lunch_daily_bundle':
                    prices=entries('lunch_get_price_result')
                    if len(prices)!=1 or not prices[0]['data'].get('preview',{}).get('schools'): raise Conflict('保存済み実施価格の計算結果が必要です')
                    for school in prices[0]['data']['preview']['schools']:
                        if any(row.get('invoice_reconciliation',{}).get('status') not in (None,'matched','not_available') for row in school.get('rows',[])):
                            raise Conflict('実施価格の納品金額・単位の不一致を解決してください')
                    price_id=prices[0]['parameters']['ids']['draft_id']
                    if not any(x['request']['name']=='lunch_save_price_source' and x['result'].get('data',{}).get('draft_id')==price_id for x in self.commands(run['id'])):
                        raise Conflict('今回保存した実施価格ではありません')
                menus=entries('lunch_get_menus')
                schedules=entries('lunch_get_schedules'); routes=entries('lunch_get_routes')
                if not menus or not schedules or not routes: raise Conflict('献立・工程表・動線図の保存結果を取得してください')
                chosen={x['key'] for x in targets} if spec['target_kind']=='menu' else {str(x['id']) for x in rows(menus[0]['data'])}
                dates={x['service_date'] for x in rows(menus[0]['data']) if str(x['id']) in chosen}
                if not dates: raise Conflict('対象日の献立がありません')
                for entries_ in (schedules,routes):
                    resources=rows(entries_[0]['data'])
                    if not dates.issubset({x['service_date'] for x in resources if x.get('id')}): raise Conflict('対象日の帳票が不足しています')
                if spec['target_kind']=='menu':
                    if not all(any(str(x['parameters'].get('ids',{}).get('menu_id'))==ident for x in entries('lunch_get_process')) for ident in chosen): raise Conflict('選択献立の工程確認が不足しています')
                    changes=[x for x in self.commands(run['id']) if x['result'].get('ok')]
                    for ident in chosen:
                        if not any(x['request']['name'] in ('lunch_infer_process','lunch_save_process') and str(x['request']['arguments'].get('parameters',{}).get('ids',{}).get('menu_id'))==ident for x in changes): raise Conflict('選択献立の工程作成が未完了です')
                    for day in dates:
                        for producers in (('lunch_create_schedule','lunch_save_schedule'),('lunch_generate_route','lunch_save_route')):
                            if not any(x['request']['name'] in producers and x['request']['arguments'].get('parameters',{}).get('body',{}).get('service_date')==day for x in changes):
                                raise Conflict('対象日の工程表・動線図の作成が未完了です')

    async def finish(self, run, artifact_ids):
        await self.questions.gate(run, 'finish')
        if not self.active(run): return
        if run['task_id']=='hospital_review_faxes':
            # Each source/quantity approval and each final output are enforced by
            # FaxWorkflow, separately from the recognition-recovery approval.
            return
        review=await run_blocking(self.review, run['id'])
        if not review or review['state']!='approved': raise Conflict('利用者が結果を承認していません')
        await self.recheck(run,review)
        if len(review['applied']) != len(review['actions']): raise Conflict('承認した確定処理が未完了です')
        commands=await run_blocking(self.commands, run['id']); spec=TASKS[run['task_id']]
        if run['task_id']=='lunch_import':
            confirms=[x for x in commands if x['request']['name']=='lunch_confirm_import' and x['result'].get('ok')]
            if not confirms: raise Conflict('献立の確定保存が未完了です')
            expected={str(item['id']) for x in confirms for item in rows(x['result']['data'])}
            persisted=rows(await self.read(run,'lunch_get_menus',{'query':{'facility_id':run['task_inputs']['inputs']['facility_id'],'month':run['task_inputs']['inputs']['month']}}))
            if not expected or not expected.issubset({str(x['id']) for x in persisted}): raise Conflict('確定した献立を再取得できません')
            current={str(x['id']):x for x in persisted}
            for command in confirms:
                for saved in rows(command['result']['data']):
                    if any(current[str(saved['id'])].get(key)!=value for key,value in saved.items()): raise Conflict('確定した献立の数量・内容が変更されています')
        for operation in spec['exports']:
            exported=[x for x in commands if x['rowid']>review.get('approved_after',0) and x['request']['name']==operation and x['result'].get('artifact',{}).get('id') in artifact_ids]
            if not exported: raise Conflict('必要な帳票が揃っていません')
            if spec['domain']=='hospital':
                if {str(x['request']['arguments']['parameters']['query'].get('order_id')) for x in exported} != {x['key'] for x in run['task_inputs']['targets']}:
                    raise Conflict('選択した注文の帳票が不足しています')
            if spec['domain']=='shift':
                expected={(str(x['parameters']['ids']['run_id']),str(x['parameters']['query']['candidate_id'])) for x in review['evidence'] if x['operation']=='shift_get_run'}
                got={(str(x['request']['arguments']['parameters']['ids']['run_id']),str(x['request']['arguments']['parameters']['query'].get('candidate_id'))) for x in exported}
                if got != expected: raise Conflict('確認した勤務候補の帳票が不足しています')
            if operation=='lunch_export_price':
                expected={str(x['parameters']['ids']['draft_id']) for x in review['evidence'] if x['operation']=='lunch_get_price_result'}
                got={str(x['request']['arguments']['parameters']['ids'].get('draft_id')) for x in exported}
                if got!=expected: raise Conflict('確認した実施価格の帳票が不足しています')
                continue
            if spec['target_kind']=='menu':
                resources=next(x['data'] for x in review['evidence'] if x['operation']==('lunch_get_schedules' if operation=='lunch_export_schedule' else 'lunch_get_routes'))
                dates={x['data']['service_date'] for x in run['task_inputs']['targets']}
                key='schedule_id' if operation=='lunch_export_schedule' else 'diagram_id'
                expected={str(x['id']) for x in rows(resources) if x['service_date'] in dates}
                got={str(x['request']['arguments']['parameters']['ids'].get(key)) for x in exported}
                if got != expected: raise Conflict('対象日の帳票が不足しています')
