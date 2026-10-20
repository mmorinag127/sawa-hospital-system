"""Pattern pool operations; composition deliberately has no persistence side effect."""
from copy import deepcopy
import hashlib

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update

from src.models.menu_process import ProcessBinding, ProcessRecipe, ProcessRevision
from src.models.process_pattern import ProcessPattern, ProcessPatternApplication, ProcessPatternRevision
from src.schemas.menu_process import ProcessDocument
from src.services import menu_process_service as processes


def _not_found():
    raise HTTPException(404, '選択学校の工程パターンが見つかりません')


def _pattern_data(pattern, revision):
    return {'id': pattern.id, 'name': pattern.name, 'version': pattern.version,
            'latest_revision': _revision_data(revision)}


def _revision_data(revision):
    return {'id': revision.id, 'number': revision.number, 'parent_revision_id': revision.parent_revision_id,
            'material_slots': revision.material_slots, 'document': revision.document,
            'fixed_equipment': revision.fixed_equipment, 'execution_choices': revision.execution_choices,
            'change_note': revision.change_note, 'created_at': revision.created_at.isoformat()}


def list_patterns(session, facility_id):
    rows = session.scalars(select(ProcessPattern).where(ProcessPattern.facility_id == facility_id)
                           .order_by(ProcessPattern.name, ProcessPattern.id)).all()
    return {'items': [_pattern_data(row, session.get(ProcessPatternRevision, row.head_revision_id)) for row in rows]}


def get_pattern(session, facility_id, pattern_id):
    pattern = session.get(ProcessPattern, pattern_id)
    if not pattern or pattern.facility_id != facility_id:
        _not_found()
    revisions = session.scalars(select(ProcessPatternRevision).where(ProcessPatternRevision.pattern_id == pattern.id)
                                .order_by(ProcessPatternRevision.number.desc())).all()
    return {**_pattern_data(pattern, session.get(ProcessPatternRevision, pattern.head_revision_id)),
            'revisions': [_revision_data(item) for item in revisions]}


def _validate_template(body):
    try:
        document = ProcessDocument.model_validate(body.document).model_dump(exclude_none=False)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    if document.get('daily_work_source') or any(s.get('daily_work') for s in document['steps']):
        raise HTTPException(422, '当日の一緒・別々の回答や使用設備はパターン定義に保存できません')
    if document.get('pattern_applications'):
        raise HTTPException(422, 'パターン定義に日次パターン採用記録は指定できません')
    if any(step.get('execution') is not None for step in document['steps']):
        raise HTTPException(422, 'パターン定義に日次実績executionは保存できません')
    slots = {item.id for item in body.material_slots}
    if len(slots) != len(body.material_slots):
        raise HTTPException(422, '材料スロットIDが重複しています')
    if any(not item.required for item in body.material_slots):
        raise HTTPException(422, '未対応材料を推測しないため、パターンの材料スロットは全て必須です')
    if {item['id'] for item in document['ingredients']} != slots:
        raise HTTPException(422, 'パターン工程の材料は明示した材料スロットと一致させてください')
    steps = {item['id']: item for item in document['steps']}
    fixed = {item.step_id: item.equipment.model_dump() for item in body.fixed_equipment}
    choices = {item.step_id: [option.model_dump() for option in item.options] for item in body.execution_choices}
    if len(fixed) != len(body.fixed_equipment) or len(choices) != len(body.execution_choices) or set(fixed) & set(choices):
        raise HTTPException(422, '固定設備と実施時設備候補は工程ごとに一つだけ指定してください')
    if not steps or (set(fixed) | set(choices)) != set(steps):
        raise HTTPException(422, '全ての作業について固定設備または当日選択の設備候補を指定してください')
    for step_id, equipment in fixed.items():
        if steps[step_id]['equipment'] != equipment:
            raise HTTPException(422, '固定設備は工程の設備と一致させてください')
    # A selectable step intentionally remains without an actual equipment until compose selection.
    for step_id in choices:
        if steps[step_id]['equipment'].get('id'):
            raise HTTPException(422, '実施時選択の工程に具体的な設備を固定しないでください')
    template_id = (document.get('route_config') or {}).get('template_id')
    for equipment in [*fixed.values(), *(item for values in choices.values() for item in values)]:
        _verify_equipment(template_id, equipment)
    return document


def create_pattern(session, facility_id, body):
    document = _validate_template(body)
    pattern = ProcessPattern(facility_id=facility_id, name=body.name, version=1)
    session.add(pattern); session.flush()
    revision = ProcessPatternRevision(pattern_id=pattern.id, number=1,
        material_slots=[item.model_dump() for item in body.material_slots], document=document,
        fixed_equipment=[item.model_dump() for item in body.fixed_equipment],
        execution_choices=[item.model_dump() for item in body.execution_choices], change_note=body.change_note)
    session.add(revision); session.flush()
    pattern.head_revision_id = revision.id
    session.flush()
    session.refresh(pattern)
    return _pattern_data(pattern, revision)


def revise_pattern(session, facility_id, pattern_id, body):
    pattern = session.get(ProcessPattern, pattern_id)
    if not pattern or pattern.facility_id != facility_id:
        _not_found()
    if pattern.version != body.expected_version:
        raise HTTPException(409, 'パターンが更新されています。入力を残したまま最新版と比較してください。')
    document = _validate_template(body)
    current = session.get(ProcessPatternRevision, pattern.head_revision_id)
    result = session.execute(update(ProcessPattern).where(ProcessPattern.id == pattern.id,
        ProcessPattern.version == body.expected_version).values(version=body.expected_version + 1),
        execution_options={'synchronize_session': False})
    if result.rowcount != 1:
        raise HTTPException(409, 'パターンが同時に更新されています。')
    revision = ProcessPatternRevision(pattern_id=pattern.id, number=body.expected_version + 1,
        parent_revision_id=current.id, material_slots=[item.model_dump() for item in body.material_slots],
        document=document, fixed_equipment=[item.model_dump() for item in body.fixed_equipment],
        execution_choices=[item.model_dump() for item in body.execution_choices], change_note=body.change_note)
    session.add(revision); session.flush()
    pattern.head_revision_id = revision.id
    session.flush()
    session.refresh(pattern)
    return _pattern_data(pattern, revision)


def _prefix(value, prefix):
    scope = hashlib.sha256(prefix.encode()).hexdigest()[:16]
    item = hashlib.sha256(value.encode()).hexdigest()[:32]
    return f'p-{scope}-{item}'


def _remap_document(template, mapping, application_id, selections):
    prefix = f'application-{application_id}'
    document = deepcopy(template)
    step_map = {step['id']: _prefix(step['id'], prefix) for step in document['steps']}
    material_values = {}
    for material in document['ingredients']:
        old_id = material['id']
        material['id'] = mapping[material['id']]
        drawing = material.get('drawing')
        if drawing:
            drawing['independent_step_ids'] = [step_map[item] for item in drawing.get('independent_step_ids', [])]
            drawing['shared_step_ids'] = [step_map[item] for item in drawing.get('shared_step_ids', [])]
            if drawing.get('merge_step_id'):
                drawing['merge_step_id'] = step_map[drawing['merge_step_id']]
            if drawing.get('group_id'):
                drawing['group_id'] = _prefix(drawing['group_id'], prefix)
        material_values[mapping[old_id]] = {key: deepcopy(material[key]) for key in ('supply', 'drawing') if material.get(key) is not None}
    for step in document['steps']:
        original_id = step['id']
        step['id'] = step_map[original_id]
        step['execution'] = None  # daily actuals never come from a reusable pattern
        if step.get('conditions', {}).get('location_group'):
            step['conditions']['location_group'] = _prefix(step['conditions']['location_group'], prefix)
        if original_id in selections:
            step['equipment'] = deepcopy(selections[original_id])
        for source in step['inputs']:
            if source.get('ingredient_id'):
                source['ingredient_id'] = mapping[source['ingredient_id']]
            if source.get('step_id'):
                source['step_id'] = step_map[source['step_id']]
            if source.get('output_id'):
                source['output_id'] = _prefix(source['output_id'], prefix)
            if source.get('edge_id'):
                source['edge_id'] = _prefix(source['edge_id'], prefix)
        for output in step.get('outputs', []):
            output['id'] = _prefix(output['id'], prefix)
            if output.get('ingredient_ids') is not None:
                output['ingredient_ids'] = [mapping[item] for item in output['ingredient_ids']]
    route = document.get('route_config')
    if route:
        route['step_ports'] = {step_map[key]: value for key, value in route.get('step_ports', {}).items()}
        route['edge_styles'] = {_prefix(key, prefix): value for key, value in route.get('edge_styles', {}).items()}
    return document, material_values


def _verify_equipment(template_id, equipment):
    # Equipment IDs are validated against the configured school SVG manifest.
    from src.services.menu_process_route_service import options
    template = next((item for item in options()['templates'] if item['template_id'] == template_id), None)
    if not template:
        raise HTTPException(422, 'パターンの厨房SVGテンプレートが指定されていません')
    items = template['equipment']
    if not any(item.get('id') == equipment['id'] and item.get('type') == equipment['type'] for item in items):
        raise HTTPException(422, '選択設備が学校の厨房SVGに存在しないか設備種類が一致しません')


@processes.mutation_operation
def compose(session, menu_id, body):
    menu = processes.menu_for(session, menu_id)
    binding, recipe = session.get(ProcessBinding, menu_id), None
    recipe = session.get(ProcessRecipe, binding.recipe_id) if binding else None
    if menu.version != body.expected_menu_version or (binding.version if binding else 0) != body.expected_binding_version or (recipe.version if recipe else 0) != body.expected_recipe_revision:
        processes.conflict()
    current = ProcessDocument.model_validate(body.document).model_dump(exclude_none=False)
    processes.check_materials(menu, current)
    used_materials = {item['ingredient_id'] for step in current['steps'] for item in step['inputs'] if item.get('ingredient_id')}
    all_steps = list(current['steps'])
    provenance = deepcopy(current.get('pattern_applications', []))
    seen_applications = {item['application_id'] for item in provenance}
    for application in body.applications:
        if application.application_id in seen_applications:
            raise HTTPException(422, '採用IDが重複しています')
        seen_applications.add(application.application_id)
        revision = session.get(ProcessPatternRevision, application.pattern_revision_id)
        pattern = session.get(ProcessPattern, revision.pattern_id) if revision else None
        if not pattern or pattern.facility_id != menu.facility_id:
            _not_found()
        slots = {item['id']: item for item in revision.material_slots}
        if set(application.material_mapping) != set(slots):
            raise HTTPException(422, '全ての材料スロットを明示的に対応付けてください')
        target_ids = set(application.material_mapping.values())
        valid_ids = {item['id'] for item in current['ingredients']}
        if not target_ids <= valid_ids or len(target_ids) != len(application.material_mapping):
            raise HTTPException(422, '材料対応先は当日の重複しない材料IDを指定してください')
        if target_ids & used_materials:
            raise HTTPException(422, 'すでに工程で使われた材料をパターンへ重複対応できません')
        used_materials.update(target_ids)
        choices = {item['step_id']: item['options'] for item in revision.execution_choices}
        if len({item.step_id for item in application.execution_selections}) != len(application.execution_selections):
            raise HTTPException(422, '同一工程の実施時設備選択が重複しています')
        selections = {item.step_id: item.equipment.model_dump() for item in application.execution_selections}
        if not set(selections) <= set(choices):
            raise HTTPException(422, '実施時選択は定義された設備候補の工程だけに指定してください')
        route = revision.document.get('route_config') or {}
        template_id = route.get('template_id')
        current_route = current.get('route_config') or {}
        if current_route and current_route.get('template_id') != template_id:
            raise HTTPException(422, '日次工程とパターンの厨房SVGテンプレートが一致しません')
        for step_id, equipment in selections.items():
            if equipment not in choices[step_id]:
                raise HTTPException(422, '選択設備はパターンの許可候補に含まれません')
            _verify_equipment(template_id, equipment)
        for item in revision.fixed_equipment:
            _verify_equipment(template_id, item['equipment'])
        remapped, material_values = _remap_document(revision.document, application.material_mapping, application.application_id, selections)
        current['schema_version'] = max(current['schema_version'], remapped['schema_version'])
        for material in current['ingredients']:
            values = material_values.get(material['id'], {})
            for key, value in values.items():
                if material.get(key) is not None and material[key] != value:
                    raise HTTPException(422, '当日の材料設定とパターン設定が競合しています。明示的に一致させてから組み立ててください')
                if material.get(key) is None:
                    material[key] = value
        if remapped.get('route_config'):
            if not current.get('route_config'):
                current['route_config'] = remapped['route_config']
            else:
                current['route_config'].setdefault('step_ports', {}).update(remapped['route_config'].get('step_ports', {}))
                current['route_config'].setdefault('edge_styles', {}).update(remapped['route_config'].get('edge_styles', {}))
        all_steps.extend(remapped['steps'])
        provenance.append({'application_id': application.application_id, 'pattern_revision_id': revision.id,
                           'material_mapping': application.material_mapping,
                           'execution_selections': [{'step_id': step_id, 'equipment': equipment}
                                                    for step_id, equipment in selections.items()]})
    current['steps'] = all_steps
    current['pattern_applications'] = provenance
    try:
        document = ProcessDocument.model_validate(current).model_dump(exclude_none=False)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {'document': document}


def record_applications(session, process_revision_id, document):
    """Called only after the ordinary ProcessRevision was successfully inserted."""
    revision = session.get(ProcessRevision, process_revision_id)
    menu = processes.menu_for(session, revision.menu_execution_id)
    seen = set()
    for item in document.get('pattern_applications', []):
        if item['application_id'] in seen:
            raise HTTPException(422, '採用IDが重複しています')
        seen.add(item['application_id'])
        source = session.get(ProcessPatternRevision, item['pattern_revision_id'])
        pattern = session.get(ProcessPattern, source.pattern_id) if source else None
        if not pattern or pattern.facility_id != menu.facility_id:
            raise HTTPException(422, '採用パターンの学校または改訂版が不正です')
        slots = {row['id'] for row in source.material_slots}
        materials = {row['id'] for row in document['ingredients']}
        if set(item['material_mapping']) != slots or not set(item['material_mapping'].values()) <= materials:
            raise HTTPException(422, '採用パターンの材料対応が不正です')
        choices = {row['step_id']: row['options'] for row in source.execution_choices}
        for selection in item['execution_selections']:
            if selection['step_id'] not in choices or selection['equipment'] not in choices[selection['step_id']]:
                raise HTTPException(422, '採用パターンの実施時設備選択が不正です')
            _verify_equipment((source.document.get('route_config') or {}).get('template_id'), selection['equipment'])
        for fixed in source.fixed_equipment:
            _verify_equipment((source.document.get('route_config') or {}).get('template_id'), fixed['equipment'])
        session.add(ProcessPatternApplication(process_revision_id=process_revision_id,
            pattern_revision_id=item['pattern_revision_id'], application_id=item['application_id'],
            material_mapping=item['material_mapping'], execution_selections=item['execution_selections']))
