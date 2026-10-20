"""Project explicitly answered daily work through existing schedule/route APIs."""
from src.schemas.menu_process import ProcessDocument, material_flow


def has_daily_answers(document):
    return bool(document.get('daily_work_source')) or any(s.get('basis', {}).get('agent_daily_question') for s in document.get('steps', []))


def work_placements(step, ingredient_ids):
    """The same explicit actuals drive both schedules and ingredient routes."""
    work = step.get('daily_work')
    if not work:
        if len(ingredient_ids) > 1:
            raise ValueError('複数食材の一緒・別々の確認と使用設備の回答が必要です')
        return [{'ingredient_ids': list(ingredient_ids), 'equipment': step['equipment']}]
    placements = work['placements']
    ids = [mid for p in placements for mid in p['ingredient_ids']]
    if len(ids) != len(set(ids)) or set(ids) != set(ingredient_ids):
        raise ValueError('食材ごとの使用設備に欠落・重複・対象外があります')
    if len(ingredient_ids) > 1 and not work.get('grouping_question_id'):
        raise ValueError('一緒・別々の回答がありません')
    if work['mode'] == 'individual' and len(ingredient_ids) != 1:
        raise ValueError('単独作業の対象食材は一つです')
    if work['mode'] in ('individual', 'together'):
        if len(placements) != 1 or placements[0]['equipment'] != step['equipment']:
            raise ValueError('共通工程の設備が回答と一致しません')
    elif work['mode'] != 'separate' or len(placements) != len(ids) or any(len(p['ingredient_ids']) != 1 for p in placements) or step['equipment'].get('id'):
        raise ValueError('別々の作業には食材ごとの使用設備が必要です')
    if any(not p.get('question_id') or not p['equipment'].get('id') or not p['equipment'].get('type') or (step['equipment'].get('type') and p['equipment']['type'] != step['equipment']['type']) for p in placements):
        raise ValueError('作業場所・釜の回答が未完了です')
    return placements


def answered_document(document):
    steps = document.get('steps', [])
    if not steps or not document.get('daily_work_source') or not all(s.get('basis', {}).get('agent_daily_question') == document['daily_work_source'] and s.get('basis', {}).get('review') == 'user-confirmed' for s in steps):
        return False
    try:
        from src.schemas.menu_process import material_flow
        flows, _ = material_flow(document)
        for step in steps: work_placements(step, flows[step['id']])
    except (ValueError, KeyError, TypeError):
        return False
    return True


def material_routes(menu, revision, layout):
    from src.services.menu_process_route_service import options
    from src.services.process_heat_history import heat_history
    document = ProcessDocument.model_validate(revision.document).model_dump()
    if not answered_document(document): raise ValueError('食材ごとの作業場所への回答が未完了です')
    template_id = (document.get('route_config') or {}).get('template_id')
    inventory = {e['id']: e for t in options()['templates'] if t['template_id'] == template_id for e in t['equipment']}
    flow, outputs = material_flow(document); heated = heat_history(document)
    result = []
    materials = {f'm{x.id}': x for x in menu.menu_ingredients}
    if set(materials) != {x['id'] for x in document['ingredients']}: raise ValueError('工程と献立の食材が一致しません')
    for material in document['ingredients']:
        mid = material['id']; ingredient = materials[mid]
        path = [s for s in document['steps'] if mid in flow[s['id']]]
        if not path: raise ValueError(material['name'] + ': 作業工程がありません')
        for previous, current in zip(path, path[1:]):
            matching = [i for i in current['inputs'] if i.get('step_id') == previous['id'] and (not i.get('output_id') or mid in outputs[(previous['id'], i['output_id'])])]
            if not matching:
                raise ValueError(material['name'] + ': 分岐した材料経路は一続きの経路へ置換できません。工程・動線を確認してください')
        locations, styles = [], []
        for step in path:
            placements = work_placements(step, flow[step['id']])
            equipment = next(p['equipment'] for p in placements if mid in p['ingredient_ids'])
            place = inventory.get(equipment.get('id'))
            if not place or place['type'] != equipment.get('type') or place['name'] not in layout.locations:
                raise ValueError(material['name'] + ': 回答した設備の座標が選択図面にありません')
            locations.append(place['name']); styles.append('dashed' if mid in heated[step['id']] else 'solid')
        result.append({'id': ingredient.id, 'label': f'{menu.name} / {material["name"]}', 'color': ingredient.route_color,
                       'locations': locations, 'styles': styles, 'process_source': {'menu_id': menu.id, 'revision_id': revision.id}})
    return result


def answer_revisions_for_scopes(session, scopes):
    from sqlalchemy import select, tuple_
    from src.models.school_lunch import MenuExecution
    from src.models.menu_process import ProcessBinding, ProcessRevision
    scopes = list(dict.fromkeys(scopes))
    result = {scope: {} for scope in scopes}
    for start in range(0, len(scopes), 500):
        rows = session.execute(select(MenuExecution.id, MenuExecution.facility_id, MenuExecution.service_date,
            ProcessRevision.id.label('revision_id'), ProcessRevision.document).join(
            ProcessBinding, ProcessBinding.menu_execution_id == MenuExecution.id).join(
            ProcessRevision, ProcessRevision.id == ProcessBinding.revision_id).where(
            tuple_(MenuExecution.facility_id, MenuExecution.service_date).in_(scopes[start:start + 500])))
        for row in rows:
            if has_daily_answers(row.document): result[(row.facility_id, row.service_date)][row.id] = row.revision_id
    return result


def route_revision_issues(session, diagram, selected=None):
    if selected is None:
        selected = answer_revisions_for_scopes(session, [(diagram.facility_id, diagram.service_date)])[(diagram.facility_id, diagram.service_date)]
    recorded = {r['process_source']['menu_id']: r['process_source']['revision_id'] for r in diagram.routes if r.get('process_source')}
    if selected != recorded: return ['作業場所・釜の回答後に工程が変更されています。動線図を再生成してください']
    return []
