"""Project saved pattern graphs into schedule rows without inferring work."""
from datetime import time

from sqlalchemy import select

from src.models.menu_process import ProcessBinding, ProcessRevision
from src.models.school_lunch import MenuExecution


def saved_patterns(session, facility_id, service_date):
    rows = session.execute(select(MenuExecution, ProcessRevision).join(
        ProcessBinding, ProcessBinding.menu_execution_id == MenuExecution.id
    ).join(ProcessRevision, ProcessRevision.id == ProcessBinding.revision_id).where(
        MenuExecution.facility_id == facility_id,
        MenuExecution.service_date == service_date,
    )).all()
    from src.services.agent_daily_process import has_daily_answers
    return {str(menu.id): (menu, revision) for menu, revision in rows
            if revision.document.get('pattern_applications') or revision.document.get('shared_menu_source') or has_daily_answers(revision.document)}


def revision_map(patterns):
    return {key: revision.id for key, (_, revision) in patterns.items()}


def missing_execution(patterns, session=None):
    from src.services.shared_menu_review import school_direct_document
    missing = []
    for menu, revision in patterns.values():
        from src.services.agent_daily_process import has_daily_answers, answered_document
        if has_daily_answers(revision.document) and not answered_document(revision.document):
            missing.append(f'{menu.name}: 作業場所・釜の再確認')
        if session is not None and school_direct_document(session,revision.document):
            continue
        if not revision.document['steps']:
            missing.append(f'{menu.name}: 作業未登録')
        for step in revision.document['steps']:
            label = f"{menu.name}・{step['operation'] or step['id']}"
            if not step['equipment'].get('id') and not step.get('daily_work'):
                missing.append(label + ': 使用設備')
            actual = step.get('execution') or {}
            for field, title in [('start_time', '開始時刻'), ('end_time', '終了時刻'),
                                 ('assignees', '担当者'), ('work_zone', '汚染区分'), ('phase', '作業段階')]:
                if not actual.get(field):
                    missing.append(label + ': ' + title)
    return missing


def project_tasks(document):
    from src.schemas.menu_process import ProcessDocument, material_flow
    from src.services.menu_process_route_service import options
    document = ProcessDocument.model_validate(document).model_dump()
    materials = {item['id']: item['name'] for item in document['ingredients']}
    template_id = (document.get('route_config') or {}).get('template_id')
    equipment = {item['id']: item for template in options()['templates']
                 if template['template_id'] == template_id for item in template['equipment']}
    flows, _ = material_flow(document)
    tasks = []
    for step in document['steps']:
        from src.services.agent_daily_process import work_placements, has_daily_answers
        placements = work_placements(step, flows[step['id']]) if has_daily_answers(document) else [{'ingredient_ids': flows[step['id']], 'equipment': step['equipment']}]
        for placement in placements:
            ids = placement['ingredient_ids']
            actual = step.get('execution') or {}
            selected = placement['equipment'].get('id')
            place = equipment.get(selected)
            if selected and place is None:
                raise ValueError(f"{step['operation']}: 図面に存在しない設備です")
            if selected and place['type'] != placement['equipment'].get('type'):
                raise ValueError(f"{step['operation']}: 設備種類と図面の設備が一致しません")
            kettle = selected.removeprefix('kettle-').upper() if selected and selected.startswith('kettle-') else None
            if kettle not in {'A', 'B', 'C', 'D', 'E', 'F'}:
                kettle = None
            label = step['operation']
            if place:
                label += f"（{place['name']}）"
            tasks.append({
                'id': None, 'sequence': len(tasks) + 1,
                'operation': label,
                'ingredient_name': '・'.join(materials[key] for key in materials if key in ids),
                'start_time': time.fromisoformat(actual['start_time']) if actual.get('start_time') else None,
                'end_time': time.fromisoformat(actual['end_time']) if actual.get('end_time') else None,
                'assignees': actual.get('assignees', []),
                'work_zone': actual.get('work_zone'), 'phase': actual.get('phase'),
                'kettle': kettle, 'kettle_use': actual.get('kettle_use'),
                'uses_raw_meat': actual.get('uses_raw_meat'),
                'duration_minutes': None, 'offset_before_plating_minutes': None,
            })
    return tasks


def assert_pattern_rows(payload, patterns, session=None):
    from src.services.shared_menu_review import school_direct_document
    expected = revision_map(patterns)
    if (payload.get('source_process_revisions') or {}) != expected:
        raise ValueError('工程グラフが変更されています。保存済み工程から工程表を再作成してください。')
    rows = payload['rows']
    for key, (_, revision) in patterns.items():
        matched = [row for row in rows if str(row.get('menu_execution_id')) == key]
        if session is not None and school_direct_document(session,revision.document):
            if matched:
                raise ValueError('学校直納の献立は厨房の工程表に含めません。工程表を再作成してください。')
            continue
        if len(matched) != 1:
            raise ValueError('パターン工程の献立が工程表から欠落または重複しています。')
        projected = project_tasks(revision.document)
        actual = matched[0]['tasks']
        fields = set(projected[0]) - {'id'} if projected else set()
        if not projected or len(actual) != len(projected) or any(
            any(a.get(field) != b.get(field) for field in fields)
            for a, b in zip(actual, projected)
        ):
            raise ValueError('パターン工程の作業・設備・時刻・担当は工程グラフで変更してから再作成してください。')
