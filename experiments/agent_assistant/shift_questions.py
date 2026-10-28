"""Business decisions for infeasible shifts; exact native revisions remain server-owned."""
import hashlib
import json
import re
from copy import deepcopy

from .conversation import check_answer
from .store import Conflict, now
from .sandbox import run_blocking
from .task_flow import rows

SHIFT_TASKS = {'shift_next_month', 'shift_wishes', 'shift_absence'}
STATUSES = {'request_off':'希望休', 'request_work':'勤務希望', 'preferred_work':'勤務希望', 'unavailable':'勤務不可', 'half_day':'半日勤務'}


def rule_request_text(choice):
    text = '\n'.join([choice.get('prior_text', ''), choice['text']]).strip()
    # The native parser uses “休みをN日”. The screen's explicit holiday-count
    # sentence has the same meaning; retain the original answer in the ledger.
    match = re.fullmatch(r'(?:月の)?休日(?:数|日数)を(\d+(?:\.5)?)日(?:にする|に変更する|に変更|にして|にしてください|にします)?[。.]?', text)
    return '休みを'+match.group(1)+'日' if match else text


class ShiftQuestions:
    def __init__(self, tools):
        self.tools, self.store = tools, tools.store

    async def ask(self, run, command, question, choices, *, kind='choice', context=None):
        ident = 'shift-question:' + hashlib.sha256(command.encode()).hexdigest()
        payload = {'command_id':ident, 'kind':kind, 'choices':choices, 'context':context or {}}
        await run_blocking(self.store.event, run['id'], 'business_shift_question', payload, ident)
        await self.tools.execute(run['id'], run['generation'], 'ask_operator', {'question':question[:2000], 'options':[item['label'] for item in choices]}, ident)
        return {'ok':True, 'waiting_input':True}

    async def conflict(self, run, command, existing, source):
        target = run['task_inputs']['targets'][0]
        day = run['task_inputs']['inputs']['absence_date']
        descriptions = '、'.join(STATUSES.get(item['status'], item['status']) + ('（必須）' if item['strength']=='hard' else '（希望）') for item in existing)
        return await self.ask(run, command+':absence', f'{target["label"]}さんの{day}には「{descriptions}」が登録されています。この日を欠勤に変更しますか？ 他の職員・日付の条件は変更しません。', [
            {'label':'この日の条件を欠勤に変更する', 'action':'absence', 'source':source, 'existing':existing},
            {'label':'欠勤する人・日を選び直す', 'action':'retarget'}])

    async def infeasible(self, run, command, result):
        issues = result.get('issues') or result.get('preflight', {}).get('issues') or result.get('violations') or []
        messages = [item.get('message', '') for item in issues if isinstance(item, dict)]
        choices = []
        settings = await self.tools.flow.read(run, 'shift_get_settings')
        for issue in issues:
            for suggestion in issue.get('suggestions', []):
                patch = suggestion.get('settings_patch')
                if isinstance(patch, dict) and patch:
                    choices.append({'label':suggestion['label'], 'action':'settings', 'before':settings, 'patch':patch})
        if settings.get('monthly_holiday_exact_days') is not None:
            choices.insert(0, {'label':'休日数を全員同じにする指定を外す', 'action':'settings', 'before':settings, 'patch':{'monthly_holiday_exact_days':None}})
        choices = choices[:5] + [
            {'label':'登録済みの希望休・勤務条件を見直す', 'action':'constraints'},
            {'label':'変更したい勤務条件を伝える', 'action':'describe'},
            {'label':'条件を変えずにもう一度作成する', 'action':'retry'}]
        return await self.ask(run, command+':infeasible', '今の条件をすべて満たす勤務案を作れませんでした。' + ('\n'+'\n'.join(messages) if messages else '\n登録済みの希望休・勤務条件と必要人数を確認して調整してください。') + '\nどう調整しますか？ 選んだ変更は次の質問で確認してから反映します。', choices)

    def answer(self, run, message, question_id, option, *, schedule=True):
        matches = [event['payload'] for event in self.store.events(run['id']) if event['kind']=='business_shift_question' and event['payload']['command_id']==question_id]
        if not matches:
            return False
        if run['status'] != 'waiting_input':
            raise Conflict('この質問は回答済みです')
        question = matches[-1]
        if question['kind']=='text':
            choice = {'action':'parse', 'text':message, **question['context']}
        else:
            choices = [item for item in question['choices'] if item['label']==message]
            if len(choices)!=1:
                raise ValueError('表示された選択肢から回答してください')
            choice = choices[0]
        with self.store.tx() as db:
            current = db.execute('SELECT generation,status,task_inputs FROM runs WHERE id=?', (run['id'],)).fetchone()
            if current['generation']!=run['generation'] or current['status']!='waiting_input':
                raise Conflict('この質問は回答済み、または依頼が変更されています')
            events = [{**dict(row), 'payload': json.loads(row['payload'])} for row in
                      db.execute('SELECT * FROM events WHERE run_id=? ORDER BY id', (run['id'],))]
            check_answer(events, message, question_id, option)
            context = json.loads(current['task_inputs'])
            state = context['business_workflow']
            state.update(phase='shift_adjustment', epoch=state['epoch']+1)
            for kind, payload in [('operator_reply', {'message':message, 'question_id':question_id, 'option':option}), ('business_shift_answer', {'question_id':question_id, 'choice':choice})]:
                db.execute('INSERT INTO events(run_id,provider_id,kind,payload,created) VALUES(?,?,?,?,?)', (run['id'], kind+':'+question_id, kind, json.dumps(payload,ensure_ascii=False), now()))
            db.execute("UPDATE runs SET status='preparing',task_inputs=?,updated=? WHERE id=?", (json.dumps(context,ensure_ascii=False), now(), run['id']))
        if schedule:
            self.tools.business.kick(run['id'])
        return True

    async def call(self, run, command, name, parameters):
        from .business_contracts import SERVER_EXECUTION
        token = SERVER_EXECUTION.set(True)
        try:
            result = await self.tools.execute(run['id'], run['generation'], name, {'parameters':parameters}, command)
        finally:
            SERVER_EXECUTION.reset(token)
        if not result.get('ok'):
            if result.get('status') in (400,409,422):
                from .workflow_completion import OperatorWait
                raise OperatorWait(await self.ask(run, command+':rejected', '変更は保存されませんでした。登録内容が更新されたか、指定した条件を適用できません。現在の条件を見直して、変更内容をもう一度確認してください。', [{'label':'現在の希望休・勤務条件を見る', 'action':'constraints'}, {'label':'変更したい勤務条件を伝える', 'action':'describe'}]))
            raise Conflict('勤務条件の保存結果を確認できません')
        return result

    async def advance(self, run):
        answers = [event['payload'] for event in await run_blocking(self.store.events, run['id']) if event['kind']=='business_shift_answer']
        choice = answers[-1]['choice']
        action = choice['action']
        command = 'shift-adjustment:' + answers[-1]['question_id']
        inputs = run['task_inputs']['inputs']
        if action=='retry':
            return True
        if action=='retarget':
            staff = rows(await self.tools.flow.read(run, 'shift_get_staff'))
            choices = [{'label':item['name'], 'action':'retarget_staff', 'staff':item} for item in staff]
            return await self.page(run, command, '欠勤する職員を選んでください。', choices)
        if action=='retarget_staff':
            from .selection_proposals import absence_days
            return await self.ask(run, command, '欠勤する日を選んでください。', [*({'label':item['label'], 'action':'retarget_date', 'staff':choice['staff'], 'day':item['date']} for item in absence_days()), {'label':'別の日を指定する', 'action':'retarget_date_input', 'staff':choice['staff']}])
        if action=='retarget_date_input':
            return await self.ask(run, command, '欠勤日を「2026-11-03」の形式で入力してください。', [], kind='text', context={'date_staff':choice['staff']})
        if action=='retarget_date' or action=='parse' and 'date_staff' in choice:
            from datetime import date
            from .selection_proposals import adjacent_month
            try:
                day = date.fromisoformat(choice.get('day') or choice['text'])
            except ValueError:
                return await self.ask(run, command, '日付を確認できませんでした。欠勤日を「2026-11-03」の形式で入力してください。', [], kind='text', context={'date_staff':choice['date_staff']})
            staff = choice.get('staff') or choice['date_staff']
            periods = [await self.tools.flow.read(run, 'shift_get_period', {'query':{'month':adjacent_month(day.strftime('%Y-%m'), offset)}}) for offset in (-1,0,1)]
            periods = [item for item in periods if item['start_date']<=day.isoformat()<item['end_date_exclusive']]
            if len(periods)!=1:
                raise Conflict('欠勤日の正規勤務期間を確認できません')
            context = deepcopy(run['task_inputs'])
            context['inputs'].update(month=periods[0]['target_month'], absence_date=day.isoformat())
            context['targets'] = [{'key':str(staff['id']), 'label':staff['name'], 'data':staff}]
            await run_blocking(self.store.update, run['id'], task_inputs=context)
            return True
        if action=='page':
            return await self.page(run, command, choice['question'], choice['items'], choice['page'])
        if action=='constraints':
            period = await self.tools.flow.read(run, 'shift_get_period', {'query':{'month':inputs['month']}})
            source = await self.tools.flow.read(run, 'shift_get_constraints', {'query': {
                'date_from': period['start_date'], 'date_to': period['end_date_exclusive']}})
            staff = {item['id']:item['name'] for item in rows(await self.tools.flow.read(run, 'shift_get_staff'))}
            items = [item for item in rows(source) if period['start_date']<=item['work_date']<period['end_date_exclusive']]
            choices = [{'label':f'{staff[item["staff_id"]]}・{item["work_date"]}・{STATUSES.get(item["status"],item["status"])}（'+('必須' if item['strength']=='hard' else '希望')+'）', 'action':'constraint', 'item':item, 'source':source} for item in items]
            for index, option in enumerate(choices):
                if sum(item['label']==option['label'] for item in choices)>1:
                    option['label'] += f'（登録順{index+1}）'
            if not choices:
                return await self.ask(run, command, 'この期間には個別の希望休・勤務条件がありません。変更したい休日数・曜日・人数条件を教えてください。', [], kind='text')
            return await self.page(run, command, 'どの希望休・勤務条件を変更しますか？', choices)
        if action=='constraint':
            item = choice['item']
            if run['task_id']=='shift_absence' and str(item['staff_id'])==run['task_inputs']['targets'][0]['key'] and item['work_date']==inputs['absence_date']:
                return await self.ask(run, command, '指定された欠勤は勤務不可のまま保持します。ほかの条件を選んでください。', [{'label':'ほかの希望休・勤務条件を見る', 'action':'constraints'}, {'label':'変更したい勤務条件を伝える', 'action':'describe'}])
            status_choices = [('request_off','希望休'), ('request_work','勤務希望')]
            options = [{'label':f'{label}（{strength_label}）にする', 'action':'confirm', 'operations':[{'name':'shift_update_constraint', 'parameters':{'ids':{'constraint_id':item['id']}, 'body':{**{key:item.get(key) for key in ('staff_id','work_date','weight','reason','shift_template_id')}, 'shift_template_id':None, 'status':status, 'strength':strength, 'revision':item['revision'], 'collection_revision':choice['source']['collection_revision']}}}], 'summary':choice['label']+' → '+label+'（'+strength_label+'）'} for status,label in status_choices for strength,strength_label in [('soft','希望'),('hard','必須')]]
            return await self.ask(run, command, 'この条件をどう変更しますか？', options)
        if action=='absence':
            operations = []
            for item in choice['existing']:
                body = {key:item.get(key) for key in ('staff_id','work_date','weight','reason')}
                body.update(status='unavailable', strength='hard', shift_template_id=None, revision=item['revision'], collection_revision=choice['source']['collection_revision'])
                operations.append({'name':'shift_update_constraint', 'parameters':{'ids':{'constraint_id':item['id']}, 'body':body}})
            return await self.confirm(run, command, '指定された職員の欠勤日の条件を、すべて「勤務不可・必須」に変更します。', operations)
        if action=='describe':
            return await self.ask(run, command, '変更したい勤務条件を教えてください。例：「佐藤さんの火曜日を固定休みにする」「休日数を9日にする」。変更案を確認してから反映します。', [], kind='text')
        if action=='parse':
            text = rule_request_text(choice)
            result = await self.call(run, command+':parse', 'shift_propose_rules', {'body':{'text':text}, 'reason':'利用者が回答した勤務条件を既存ルール解析で変更案へ変換'})
            proposal = result['data']
            if proposal.get('questions'):
                return await self.ask(run, command, '\n'.join(proposal['questions'])+'\n変更したい条件を補足して、もう一度教えてください。', [], kind='text', context={'prior_text':text})
            settings = await self.tools.flow.read(run, 'shift_get_settings')
            if settings['settings_revision'] != proposal['input_versions']['settings_revision']:
                return await self.ask(run, command, '条件が更新されました。現在の条件を確認して変更案を作り直しますか？', [{'label':'現在の条件から作り直す', 'action':'describe'}])
            body = {'settings':{**settings, **proposal['settings_patch']}, 'pair_rule_candidates':proposal['pair_rule_candidates'], 'staff_updates':proposal['staff_updates'], 'input_versions':proposal['input_versions']}
            return await self.confirm(run, command, '\n'.join(proposal['explanations']), [{'name':'shift_apply_rules','parameters':{'body':body}}])
        if action=='settings':
            return await self.confirm(run, command, choice['label'], [{'name':'shift_save_settings', 'parameters':{'body':{**choice['before'], **choice['patch']}}}])
        if action=='confirm':
            return await self.confirm(run, command, choice['summary'], choice['operations'])
        if action=='apply':
            for index, operation in enumerate(choice['operations']):
                parameters = deepcopy(operation['parameters'])
                # Multiple same-day conditions are applied one at a time; each
                # collection version must still match our immediately prior receipt.
                if index and operation['name']=='shift_update_constraint':
                    source = await self.tools.flow.read(run, 'shift_get_constraints')
                    previous = await run_blocking(self.store.events, run['id'])
                    receipts = [e['payload'] for e in previous if e['kind']=='native_response_receipt' and e['payload']['command_id']==command+':apply:'+str(index-1)]
                    if not receipts or source['collection_revision'] != receipts[-1]['data']['collection_revision']:
                        raise Conflict('欠勤条件の変更中に別の更新がありました')
                    parameters['body']['collection_revision'] = source['collection_revision']
                parameters['reason'] = '利用者が質問で選択し、変更内容を確認した勤務条件だけを反映'
                await run_blocking(self.store.event, run['id'], 'business_shift_authorization', {'name':operation['name'], 'parameters':parameters, 'question_id':answers[-1]['question_id']}, command+':authorize:'+str(index))
                result = await self.call(run, command+':apply:'+str(index), operation['name'], parameters)
                if not result.get('ok'):
                    raise Conflict('勤務条件の保存を確認できません')
            return True
        raise Conflict('勤務条件への回答を確認できません')

    async def confirm(self, run, command, summary, operations):
        return await self.ask(run, command, '次の内容で変更し、勤務案を作り直しますか？\n'+summary+'\n他の条件は保持します。', [{'label':'この変更を反映して作り直す', 'action':'apply', 'operations':operations}, {'label':'変更せずに条件を選び直す', 'action':'constraints'}])

    async def page(self, run, command, question, items, page=0):
        items = deepcopy(items)
        labels = [item['label'] for item in items]
        for index, item in enumerate(items):
            if labels.count(item['label'])>1:
                item['label'] += f'（候補{index+1}）'
        choices = items[page*7:(page+1)*7] if len(items)>8 else items
        if len(items)>8:
            next_page = page+1 if (page+1)*7<len(items) else 0
            choices = choices+[{'label':'ほかの候補を見る' if next_page else '最初の候補を見る', 'action':'page', 'items':items, 'page':next_page, 'question':question}]
        return await self.ask(run, command, question, choices)


def authorized(tools, run, name, args):
    from .business_contracts import SERVER_EXECUTION
    if not SERVER_EXECUTION.get() or run.get('task_id') not in SHIFT_TASKS:
        return False
    if name=='shift_propose_rules':
        return any(event['kind']=='business_shift_answer' and event['payload']['choice'].get('action')=='parse' and rule_request_text(event['payload']['choice'])==args.body.get('text') for event in tools.store.events(run['id']))
    parameters = args.model_dump(mode='json')
    return any(event['kind']=='business_shift_authorization' and event['payload']['name']==name and all(parameters[key]==event['payload']['parameters'].get(key, {} if key in ('ids','query','body') else '') for key in ('ids','query','body')) for event in tools.store.events(run['id']))
