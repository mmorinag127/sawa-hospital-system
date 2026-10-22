"""Prepare editable route defaults without saving a pattern or daily actual."""
from copy import deepcopy
from fastapi import HTTPException
from src.schemas.menu_process import ProcessDocument
from src.services import menu_process_service as process
from src.services import menu_process_route_service as routes
from src.services.process_heat_history import heating_work
from src.services.process_text_details import infer_missing_details


def prepare(session, menu_id, request):
    menu, binding, recipe, revision = process.state(session, menu_id)
    process.check_versions(menu, binding, recipe, request)
    document = request.document.model_dump()
    process.check_materials(menu, document)
    # A client may edit today's values, but cannot substitute another pattern.
    saved_source = (revision.document if revision else {}).get('shared_menu_source')
    if revision is None and document.get('shared_menu_source'):
        from src.services.shared_menu_service import daily_draft
        suggestion = daily_draft(session, menu)
        saved_source = (suggestion or {}).get('shared_menu_source')
    if document.get('shared_menu_source') != saved_source:
        raise HTTPException(422, '標準パターンの参照元が一致しません')
    result = deepcopy(document)
    pattern_choices = {}
    if result.get('pattern_applications'):
        from src.models.process_pattern import ProcessPatternRevision, ProcessPattern
        from src.services.process_pattern_service import _prefix
        saved_applications = {a['application_id']: a for a in (revision.document if revision else {}).get('pattern_applications', [])}
        for application in result['pattern_applications']:
            saved = saved_applications.get(application['application_id'])
            if saved and any(application.get(key) != saved.get(key) for key in ('pattern_revision_id', 'material_mapping')):
                raise HTTPException(422, '工程パターンの参照元が一致しません')
            source = session.get(ProcessPatternRevision, application['pattern_revision_id'])
            pattern = session.get(ProcessPattern, source.pattern_id) if source else None
            if not pattern or pattern.facility_id != menu.facility_id:
                raise HTTPException(422, '工程パターンの保存版が見つかりません')
            if (set(application['material_mapping']) != {m['id'] for m in source.material_slots}
                    or not set(application['material_mapping'].values()) <= {m['id'] for m in result['ingredients']}):
                raise HTTPException(422, '工程パターンの材料対応が一致しません')
            prefix = f"application-{application['application_id']}"
            for item in source.fixed_equipment:
                step = next((s for s in result['steps'] if s['id'] == _prefix(item['step_id'], prefix)), None)
                if step and not step['equipment'].get('id') and step['equipment'].get('type') == item['equipment']['type']:
                    step['equipment'] = deepcopy(item['equipment'])
                    step['basis']['equipment_default'] = '保存済み標準パターン'
                    step['basis']['review'] = 'pending'
            for item in source.execution_choices:
                pattern_choices[_prefix(item['step_id'], prefix)] = item['options']
                if len(item['options']) == 1:
                    step = next((s for s in result['steps'] if s['id'] == _prefix(item['step_id'], prefix)), None)
                    if step and not step['equipment'].get('id') and step['equipment'].get('type') == item['options'][0]['type']:
                        step['equipment'] = deepcopy(item['options'][0])
                        step['basis']['equipment_default'] = 'パターンの設備候補・未確認'
                        step['basis']['review'] = 'pending'
    if saved_source:
        from src.models.shared_menu import SharedMenuRevision
        source = session.get(SharedMenuRevision, saved_source['revision_id'])
        if source is None:
            raise HTTPException(422, '標準パターンの保存版が見つかりません')
        standard = source.document
        if not result.get('route_config'):
            result['route_config'] = deepcopy(standard.get('route_config'))
        same_template = ((result.get('route_config') or {}).get('template_id') ==
                         (standard.get('route_config') or {}).get('template_id'))
        standards = {s['id']: s for s in standard['steps']}
        for step in result['steps']:
            default = standards.get(step['id'])
            if (same_template and default and not step['equipment'].get('id')
                    and step['equipment'].get('type') == default['equipment'].get('type')
                    and default['equipment'].get('id')):
                step['equipment'] = deepcopy(default['equipment'])
                step['basis']['equipment_default'] = '保存済み標準パターン'
                step['basis']['review'] = 'pending'
        materials = {m['id']: m for m in result['ingredients']}
        for material in standard['ingredients']:
            target = materials.get(saved_source['material_mapping'].get(material['id']))
            if target is None:
                continue
            for key in ('drawing', 'supply'):
                if same_template and not target.get(key) and material.get(key):
                    target[key] = deepcopy(material[key])
    config = result.get('route_config') or {}
    if not config.get('template_id') or not result['steps']:
        return {'base_version': request.expected_version, 'document': result}
    # An operator's explicit unresolved choice is not permission to infer it.
    # Only absent method information is completed as a reviewable draft.
    for step in result['steps']:
        if step.get('conditions', {}).get('heat_treatment') is not None:
            heating_work(step)
    result, _ = infer_missing_details(result, process.snapshot(menu))
    svg, manifest = routes.load_template(config['template_id'])
    manifest = routes.preview_manifest(svg, manifest)
    result, assignments = routes.bind_preview_equipment(result, manifest)
    for assignment in assignments:
        step = next(s for s in result['steps'] if s['id'] == assignment['step_id'])
        step['basis']['equipment_default'] = '施設・工程からの配置案・未確認'
        step['basis']['equipment_default_reason'] = assignment['reason']
        step['basis']['review'] = 'pending'
    for step in result['steps']:
        if step['id'] in pattern_choices and step['equipment'] not in pattern_choices[step['id']]:
            raise HTTPException(422, f"{step['operation']}の初期案がパターンの設備候補にありません。パターンの候補を確認してください")
    routes.require_pattern_equipment(result)
    # Never write a revision, execution time, or confirmation as a side effect.
    return {'base_version': request.expected_version,
            'document': ProcessDocument.model_validate(result).model_dump()}
