"""Native monthly listings are evidence for the selected day only."""
from datetime import date
from .store import Conflict

LISTINGS={'lunch_get_menus','lunch_get_schedules','lunch_get_routes'}


def selected(run, name, data):
    if run.get('task_id')!='lunch_daily_bundle' or name not in LISTINGS:
        return data
    inputs=run['task_inputs']['inputs'];day=inputs['service_date'];facility=inputs['facility_id']
    if not isinstance(data,list):raise Conflict('対象日の一覧を正規APIから取得できません')
    for row in data:
        if not isinstance(row,dict):raise Conflict('対象日の一覧に不正な行があります')
        try:valid=date.fromisoformat(row.get('service_date')).isoformat()==row['service_date']
        except (TypeError,ValueError):valid=False
        owner=row.get('facility_id',row.get('facility',{}).get('id'))
        if not valid or owner!=facility:raise Conflict('一覧の学校・日付が依頼対象と一致しません')
    return [row for row in data if row['service_date']==day]
