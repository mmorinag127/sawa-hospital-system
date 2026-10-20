"""Read-only operator view derived from native, version-bound review evidence."""
from datetime import date, timedelta

STATUS = {'off':'休み','working':'勤務','paid_leave':'有休','half_day':'半日','unavailable':'勤務不可',
          'request_off':'希望休','request_work':'勤務希望','hard':'必須','soft':'希望'}


def items(value):
    if isinstance(value, list): return value
    return value.get('items', []) if isinstance(value, dict) else []


def cells(data):
    result = {}
    for row in data.get('statuses', []):
        result[(str(row['staff_id']), row['work_date'])] = STATUS.get(row.get('work_kind'), STATUS.get(row.get('status'), row.get('status', '未確認')))
    assigned = {}
    for row in data.get('assignments', []):
        key = (str(row['staff_id']), row['work_date'])
        assigned.setdefault(key, []).append(' / '.join(str(row[k]) for k in ('position','shift_template') if row.get(k)))
    result.update({key:'・'.join(sorted(values)) for key,values in assigned.items()})
    return result


def build_shift_review(review, reference=None):
    evidence = {row['operation']:row['data'] for row in review['evidence']}
    data = evidence.get('shift_get_run', {})
    period = evidence.get('shift_get_period', data)
    start, end = period.get('start_date'), period.get('end_date_exclusive')
    days = []
    if start and end:
        day = date.fromisoformat(start)
        while day < date.fromisoformat(end):
            days.append({'date':day.isoformat(),'label':f'{day.month}/{day.day}','weekday':'月火水木金土日'[day.weekday()],'weekend':day.weekday()>=5})
            day += timedelta(days=1)
    current = cells(data)
    staff = {str(row['id']):row['name'] for row in items(evidence.get('shift_get_staff'))}
    for row in data.get('statuses', []) + data.get('assignments', []):
        staff[str(row['staff_id'])] = row['staff_name']
    matrix = [{'name':name,'cells':[{'date':d['date'],'text':current.get((ident,d['date']),'未確認')} for d in days]}
              for ident,name in staff.items() if any(key[0]==ident for key in current)]
    constraints=[]
    statuses={(str(row['staff_id']),row['work_date']):row for row in data.get('statuses',[])}
    for row in items(evidence.get('shift_get_constraints')):
        day=row['work_date']
        if start and end and not start<=day<end: continue
        key=(str(row['staff_id']),day);actual=statuses.get(key);kind=row.get('status')
        matches=None
        if actual and kind in ('unavailable','request_off'): matches=actual['status']=='off'
        elif actual and kind=='request_work': matches=actual['status']=='working'
        constraints.append({'職員':staff.get(key[0],row.get('staff_name',key[0])),'日付':day,
                            '指定':STATUS.get(kind,kind),'適用':STATUS.get(row.get('strength'),row.get('strength')),
                            '結果':current.get(key,'未確認'),'反映':('反映済み' if matches else '未反映') if matches is not None else '個別確認が必要'})
    validation=evidence.get('shift_get_validation',data.get('candidate_validation'))
    issues=[]
    for row in (validation or {}).get('warnings',[]) + data.get('violations',[]):
        item={'内容':row.get('message',str(row)),'区分':'要修正' if row.get('severity')=='error' else '注意'}
        if item not in issues: issues.append(item)
    shortages=[]
    if 'shift_get_demands' in evidence:
        for demand in items(evidence['shift_get_demands']):
            count=len({row['staff_id'] for row in data.get('assignments',[]) if all(row.get(k)==demand.get(k) for k in ('work_date','position_id','shift_template_id'))})
            minimum=demand.get('min_count') if demand.get('min_count') is not None else demand['required_count']
            if count<minimum:
                shortages.append({'日付':demand['work_date'],'担当':demand.get('position_name',str(demand['position_id'])),'必要人数':minimum,'配置人数':count,'不足人数':minimum-count})
    reference=reference or {'state':'not_captured'}
    changes=[]
    if reference.get('state')=='available':
        previous=cells(reference['data'])
        for ident,day in sorted(current.keys() | previous.keys(),key=lambda key:(key[1],key[0])):
            text=current.get((ident,day),'記録なし')
            old=previous.get((ident,day),'記録なし')
            if text!=old: changes.append({'日付':day,'職員':staff.get(ident,ident),'変更前':old,'変更後':text})
        comparison='同じ期間の確定済み勤務表との変更点'
    else:
        comparison={'none':'比較する確定済み勤務表がありません。','ambiguous':'比較する確定済み勤務表が複数あります。比較対象の指定が必要です。','not_captured':'変更前の勤務表は取得していません。'}.get(reference.get('state'),'比較対象を確認できません。')
    return {'period':f'{start} ～ {date.fromisoformat(end)-timedelta(days=1)}' if start and end else '勤務期間が未確認です',
            'days':days,'matrix':matrix,'constraints':constraints,'issues':issues,'shortages':shortages,
            'constraints_known':'shift_get_constraints' in evidence,'validation_known':isinstance(validation,dict) and isinstance(validation.get('blocking'),bool),'blocking':(validation or {}).get('blocking'),
            'demand_known':'shift_get_demands' in evidence,'comparison':comparison,'changes':changes,
            'comparison_available':reference.get('state')=='available',
            'reflected':sum(row['反映']=='反映済み' for row in constraints),'unreflected':sum(row['反映']=='未反映' for row in constraints)}
