"""Bind native optimistic concurrency to observed state, never model-authored versions."""
from datetime import date
from .store import Conflict
from .sandbox import run_blocking


async def bind_version(tools, run, args):
    body = dict(args.body)
    if body.get('replace_confirmed_source') is not None and body['replace_confirmed_source'] is not False:
        raise Conflict('採用済み原本の動線を自動で上書きできません')
    facility = body.get('facility_id')
    layout = body.get('layout_id')
    if type(facility) is not int or facility <= 0 or type(layout) is not int or layout <= 0:
        raise ValueError('対象の学校ID・図面IDを指定してください')
    day = body.get('service_date')
    if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day:
        raise ValueError('対象日をYYYY-MM-DDで指定してください')
    bindings = [event['payload'] for event in await run_blocking(tools.store.events, run['id'])
                if event['kind']=='route_generation_binding' and event['payload']['facility_id']==facility and event['payload']['service_date']==day]
    if len(bindings)>1: raise Conflict('動線図の保存版の記録が重複しています')
    if bindings:
        binding = bindings[0]
        if binding['layout_id'] != layout: raise Conflict('版を確認した図面と指定された図面が異なります')
    else:
        current = await tools.flow.read(run,'lunch_get_routes',{'query':{'facility_id':facility,'month':day[:7]}})
        if not isinstance(current,list): raise Conflict('動線図の保存状態を取得できません')
        for row in current:
            try: valid_day=isinstance(row,dict) and date.fromisoformat(row.get('service_date')).isoformat()==row['service_date']
            except (TypeError,ValueError): valid_day=False
            if (not isinstance(row,dict) or row.get('facility_id')!=facility or
                type(row.get('facility_id')) is not int or not valid_day or
                type(row.get('id')) is not int or row['id']<=0 or
                type(row.get('version')) is not int or row['version']<1 or
                not isinstance(row.get('service_date'),str) or row['service_date'][:7]!=day[:7]):
                raise Conflict('動線図の学校・対象月・保存版を照合できません')
        matches=[row for row in current if row['service_date']==day]
        if len(matches)>1: raise Conflict('同じ学校・日付の動線図が複数あります')
        binding={'facility_id':facility,'service_date':day,'layout_id':layout,
                 'diagram_id':matches[0]['id'] if matches else None,
                 'version':matches[0]['version'] if matches else 0}
        await run_blocking(tools.store.event, run['id'],'route_generation_binding',binding)
    body['version']=binding['version']
    body.pop('replace_confirmed_source',None)
    return args.model_copy(update={'body':body})
