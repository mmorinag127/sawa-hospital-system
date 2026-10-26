"""Daily school workflow receipts belong to the server, not model-authored IDs."""
import json
from .store import Conflict
from .sandbox import run_blocking


class DailyCompletion:
    def __init__(self,tools): self.tools=tools

    def current(self,ident,generation):
        run=self.tools.store.get(ident)
        if run['generation']!=generation or run['status']!='running': raise Conflict('依頼は停止または変更されました')
        if run.get('task_id')!='lunch_daily_bundle': raise Conflict('日次給食業務専用の操作です')
        return run

    async def save_schedule(self,ident,generation,raw,command):
        run=await run_blocking(self.current, ident,generation)
        fields={'plating_start','plating_end','timeline_ticks','delivery_assignees'}
        if set(raw)!=fields:raise ValueError('盛付開始・終了、8個の時刻目盛、配送担当を回答してください')
        if not isinstance(raw['timeline_ticks'],list) or len(raw['timeline_ticks'])!=8:raise ValueError('原本に使う8個の時刻目盛を指定してください')
        inputs=run['task_inputs']['inputs']
        names={target['data']['facility']['name'] for target in run['task_inputs']['targets']}
        if len(names)!=1:raise Conflict('選択した学校名が一意ではありません')
        school=next(iter(names));query={'facility_id':inputs['facility_id'],'month':inputs['month']}
        bindings=[e['payload'] for e in await run_blocking(self.tools.store.events, ident) if e['kind']=='daily_schedule_binding']
        if bindings:
            binding=bindings[-1]
        else:
            result=await self.tools.execute(ident,generation,'lunch_get_schedule_draft',{'parameters':{'query':{**query,'facility_name':school,'service_date':inputs['service_date'],'refresh_sources':True}}},command+':draft')
            if not result.get('ok'):raise Conflict('保存済み工程から工程表案を取得できません')
            draft=result['data']
            if draft.get('pattern_missing') or not draft.get('rows'):raise Conflict('工程表の元になる保存済み工程が不足しています')
            binding={'id':draft['id'],'body':self.schedule_body(draft,school,inputs['service_date'])}
            await run_blocking(self.tools.store.event, ident,'daily_schedule_binding',binding)
        body={**binding['body'],**raw}
        operation='lunch_create_schedule' if binding['id'] is None else 'lunch_save_schedule'
        parameters={'ids':{} if binding['id'] is None else {'schedule_id':binding['id']},'query':query,'body':body,'reason':'利用者が回答した盛付時刻・時間目盛・配送担当を、保存済みの当日工程表案へ設定'}
        result=await self.tools.execute(ident,generation,operation,{'parameters':parameters},command)
        if result.get('ok'):
            saved=result['data']
            await run_blocking(self.tools.store.event, ident,'daily_schedule_binding',{'id':saved['id'],'body':self.schedule_body(saved,school,inputs['service_date'])})
        return result

    @staticmethod
    def schedule_body(data,school,day):
        required=('version','rows','source_menu_versions','source_process_revisions')
        if any(key not in data or data[key] is None for key in required):raise Conflict('工程表案の保存版・工程版を照合できません')
        allowed=required+('source_notes','plating_start','plating_end','timing_profile_id','timing_profile_version','timeline_ticks','delivery_assignees','status')
        return {**{key:data[key] for key in allowed if key in data},'facility_name':school,'service_date':day}

    async def read(self,run,command):
        inputs=run['task_inputs']['inputs'];query={'facility_id':inputs['facility_id'],'month':inputs['month']}
        saved={c['result']['data']['draft_id'] for c in (await run_blocking(self.tools.flow.commands, run['id']))
               if c['request']['name']=='lunch_save_price_source' and c['result'].get('ok') and c['result'].get('data',{}).get('draft_id')}
        bindings=[event['payload'] for event in await run_blocking(self.tools.store.events, run['id']) if event['kind']=='daily_price_binding']
        if bindings:
            binding=bindings[-1]
            commands={entry['id']:entry for entry in (await run_blocking(self.tools.flow.commands, run['id']))}
            receipt=commands.get(binding['command_id'])
            if (not receipt or receipt['request']['name']!='lunch_save_price_source'
                    or not receipt['result'].get('ok') or receipt['result'].get('data',{}).get('draft_id')!=binding['draft_id']):
                raise Conflict('今回保存した実施価格の選択記録と正規APIの証跡が一致しません')
            saved={binding['draft_id']}
        if len(saved)!=1: raise Conflict('今回保存した実施価格を一つに確定してください')
        price_id=next(iter(saved))
        operations=[('lunch_get_menus',{'query':query})]
        operations += [('lunch_get_process',{'ids':{'menu_id':target['data']['id']},'query':query}) for target in run['task_inputs']['targets']]
        operations += [('lunch_get_schedules',{'query':query}),('lunch_get_routes',{'query':query}),
                       ('lunch_get_price_source',{'ids':{'draft_id':price_id},'query':query}),
                       ('lunch_get_price_result',{'ids':{'draft_id':price_id},'query':query})]
        evidence=[]
        for index,(name,parameters) in enumerate(operations):
            result=await self.tools.execute(run['id'],run['generation'],name,{'parameters':parameters},command+':read:'+str(index))
            if not result.get('ok'): raise Conflict('保存結果の再取得に失敗しました：'+name)
            evidence.append(result['command_id'])
        return evidence

    async def prepare(self,ident,generation,raw,command):
        if raw: raise ValueError('入力項目はありません')
        run=await run_blocking(self.current, ident,generation)
        from .canonical_route_layout import prepare
        run=await prepare(self.tools,run)
        inputs=run['task_inputs']['inputs']
        generated=[c for c in (await run_blocking(self.tools.flow.commands, ident)) if c['request']['name'] in ('lunch_generate_route','lunch_save_route')
                   and c['result'].get('ok') and c['request']['arguments'].get('parameters',{}).get('body',{}).get('service_date')==inputs['service_date'] and c['request']['arguments'].get('parameters',{}).get('body',{}).get('layout_id')==inputs['layout_id']]
        if not generated:
            result=await self.tools.execute(ident,generation,'lunch_generate_route',{'parameters':{
                'query':{'facility_id':inputs['facility_id'],'month':inputs['month']},
                'body':{key:inputs[key] for key in ('facility_id','service_date','layout_id')},
                'reason':'利用者が選択した日次一括作成。回答済みの当日工程と選択済み図面から動線図を作成'}},command+':route')
            if not result.get('ok'):raise Conflict('回答済み工程から動線図を作成できません：'+str(result.get('error',result.get('detail',''))))
        evidence=await self.read(run,command)
        schedules=[c['result']['data'] for c in (await run_blocking(self.tools.flow.commands, ident))
                   if c['id'] in evidence and c['request']['name']=='lunch_get_schedules']
        selected=[row for rows in schedules for row in rows if row['service_date']==run['task_inputs']['inputs']['service_date']]
        if len(selected)!=1:raise Conflict('対象日の工程表を一つに確定してください')
        ticks=selected[0].get('timeline_ticks')
        if not isinstance(ticks,list) or len(ticks)!=8:
            raise Conflict('原本の工程表に使う8個の時刻目盛が未入力です。利用者に時刻目盛を質問し、工程表へ保存してから再確認してください')
        return await self.tools.execute(ident,generation,'request_task_approval',
            {'summary':'回答した作業場所・釜、作業工程表・動線図・実施価格を確認してください。','evidence_commands':evidence,'actions':[]},command)

    async def complete(self,ident,generation,raw,command):
        if raw: raise ValueError('入力項目はありません')
        run=await run_blocking(self.current, ident,generation);review=(await run_blocking(self.tools.flow.review, ident))
        if not review or review['state']!='approved': raise Conflict('三帳票の内容を利用者が承認していません')
        await self.tools.flow.recheck(run,review)
        inputs=run['task_inputs']['inputs'];query={'facility_id':inputs['facility_id'],'month':inputs['month']}
        artifacts=[]
        for index,(read,export,key) in enumerate([('lunch_get_schedules','lunch_export_schedule','schedule_id'),('lunch_get_routes','lunch_export_route','diagram_id')]):
            evidence=[e for e in review['evidence'] if e['operation']==read]
            if len(evidence)!=1: raise Conflict('承認済み帳票の保存結果が一意ではありません')
            targets=[row for row in evidence[0]['data'] if row['service_date']==inputs['service_date']]
            if len(targets)!=1: raise Conflict('承認済みの対象日・帳票を一つに確定してください')
            target=targets[0];source={k:target[k] for k in ('id','version','service_date')}
            source['facility_id']=target.get('facility_id',target.get('facility',{}).get('id'))
            if source['facility_id']!=inputs['facility_id']: raise Conflict('承認した帳票の学校が一致しません')
            if key=='diagram_id':source.update({k:target[k] for k in ('layout_id','layout_version')})
            parameters={'ids':{key:target['id']},'query':{**query,'expected_sources':json.dumps([source])}}
            if key=='schedule_id':parameters['query']['format']='original'
            result=await self.tools.execute(ident,generation,export,{'parameters':parameters},command+':export:'+str(index))
            if not result.get('ok') or not result.get('artifact'):raise Conflict('帳票を取得できません：'+export)
            artifacts.append(result['artifact']['id'])
        prices=[e for e in review['evidence'] if e['operation']=='lunch_get_price_result']
        if len(prices)!=1:raise Conflict('承認済み実施価格が一意ではありません')
        price=prices[0]
        result=await self.tools.execute(ident,generation,'lunch_export_price',{'parameters':{'ids':price['parameters']['ids'],'query':{**query,'expected_fingerprint':price['data']['fingerprint']}}},command+':export:price')
        if not result.get('ok') or not result.get('artifact'):raise Conflict('実施価格表を取得できません')
        artifacts.append(result['artifact']['id'])
        evidence=await self.read(run,command+':verify')
        return await self.tools.execute(ident,generation,'finish_work',{'summary':'承認した作業工程表・動線図・実施価格表を正規APIで出力し、保存結果と照合しました。','evidence_commands':evidence,'artifact_ids':artifacts},command)
