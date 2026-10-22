from __future__ import annotations

from copy import deepcopy
from functools import wraps
from types import SimpleNamespace
import hashlib
import json
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from src.models.school_lunch import MenuExecution
from src.models.menu_process import ProcessRecipe, ProcessRevision, ProcessBinding, ProcessProposal
from src.schemas.menu_process import ProcessDocument, CorrectionPlan, Step, MaterialDrawing, material_flow


def conflict():
    raise HTTPException(409, '献立または工程が更新されました。入力を残したまま最新版と比較してください。')


def menu_for(session, menu_id):
    menu = session.get(MenuExecution, menu_id)
    if not menu:
        raise HTTPException(404, '献立が見つかりません')
    return menu


def seed(menu):
    return {'schema_version': 1, 'ingredients': [
        {'id': f'm{row.id}', 'name': row.ingredient.name, 'state': ''}
        for row in menu.menu_ingredients], 'steps': []}


def snapshot(menu):
    return {'menu_id': menu.id, 'name': menu.name, 'facility_id': menu.facility_id,
            'service_date': str(menu.service_date), 'version': menu.version,
            'instructions': menu.cooking_instructions,
            'ingredients': [{'id': f'm{r.id}', 'name': r.ingredient.name,
                             'process_text': r.process_text} for r in menu.menu_ingredients]}


def check_materials(menu, document):
    actual = [(i['id'], i['name']) for i in document['ingredients']]
    expected = [(i['id'], i['name']) for i in seed(menu)['ingredients']]
    if actual != expected:
        raise HTTPException(422, '献立の材料ID・名称・順序と工程が一致しません。献立を再確認してください。')


def state(session, menu_id):
    menu = menu_for(session, menu_id)
    binding = session.get(ProcessBinding, menu_id)
    recipe = session.get(ProcessRecipe, binding.recipe_id) if binding else None
    revision = session.get(ProcessRevision, binding.revision_id) if binding else None
    return menu, binding, recipe, revision


def context(session, menu_id):
    menu, binding, recipe, revision = state(session, menu_id)
    revisions = []
    if recipe:
        revisions = session.scalars(select(ProcessRevision).where(
            ProcessRevision.recipe_id == recipe.id,
            (ProcessRevision.scope == 'menu') | (ProcessRevision.menu_execution_id == menu_id)
        ).order_by(ProcessRevision.number.desc())).all()
    # Reuse is only offered for an explicitly confirmed canonical revision.  A
    # newer draft may be the editing head, but is never a reusable recipe.
    recipes = session.scalars(select(ProcessRecipe).where(
        ProcessRecipe.facility_id == menu.facility_id,
        ProcessRecipe.confirmed_revision_id.is_not(None)).order_by(ProcessRecipe.name, ProcessRecipe.id)).all()
    document = deepcopy(revision.document) if revision else seed(menu)
    from src.services.shared_menu_service import daily_draft
    suggested_document = daily_draft(session, menu) if revision is None else None
    shared_menu_defaults = None
    source = (suggested_document or document).get('shared_menu_source')
    if source:
        from src.models.shared_menu import SharedMenuRevision
        shared_revision = session.get(SharedMenuRevision, source['revision_id'])
        if shared_revision:
            source_document = shared_revision.document
            shared_menu_defaults = {
                'revision_id': shared_revision.id,
                'template_id': (source_document.get('route_config') or {}).get('template_id'),
                'equipment': {
                    step['id']: deepcopy(step['equipment'])
                    for step in source_document.get('steps', [])
                },
            }
    proposal = session.scalars(select(ProcessProposal).where(
        ProcessProposal.menu_execution_id == menu_id
    ).order_by(ProcessProposal.id.desc()).limit(1)).first()
    # A proposal is not a saved/confirmed process. Offer only the latest proposal
    # with the exact source and editing version it was generated from.
    pending = None
    if (proposal and proposal.base_version == (binding.version if binding else 0)
            and proposal.input_document == ProcessDocument.model_validate(document).model_dump()
            and proposal.inference_trace.get('source_snapshot') == snapshot(menu)
            and proposal.inference_trace.get('recipe_revision') == (recipe.version if recipe else 0)):
        pending = proposal_response(proposal)
    return {'menu': {'id': menu.id, 'name': menu.name, 'facility_id': menu.facility_id,
                     'service_date': str(menu.service_date), 'version': menu.version},
            'version': binding.version if binding else 0, 'recipe_id': recipe.id if recipe else None,
            'recipe_revision': recipe.version if recipe else 0,
            'revision': revision.id if revision else None,
            'status': revision.status if revision else 'draft', 'scope': revision.scope if revision else 'menu',
            'document': document, 'suggested_document': suggested_document,
            'shared_menu_defaults': shared_menu_defaults, 'proposal': pending,
            'history': [{'id': r.id, 'number': r.number, 'status': r.status, 'scope': r.scope,
                         'created_at': r.created_at.isoformat(), 'origin': r.origin,
                         'document': remap_materials(r.document, menu, clear_actuals=r.menu_execution_id != menu.id) or deepcopy(r.document),
                         'can_restore': remap_materials(r.document, menu, clear_actuals=r.menu_execution_id != menu.id) is not None,
                         'change_note': r.change_note,
                         'changes': r.changes} for r in revisions],
            'recipes': [{'id': r.id, 'name': r.name,
                         'revision': session.get(ProcessRevision, r.confirmed_revision_id).number}
                        for r in recipes]}


def remap_materials(document, menu, *, clear_actuals=True):
    """Explicit reuse only, by ordered material identities (including duplicate uses)."""
    document = deepcopy(document)
    expected = seed(menu)['ingredients']
    if [i['name'] for i in document['ingredients']] != [i['name'] for i in expected]:
        return None
    mapping = {old['id']: new['id'] for old, new in zip(document['ingredients'], expected)}
    for old, new in zip(document['ingredients'], expected):
        old['id'] = new['id']
    if clear_actuals: document.pop('daily_work_source', None)
    fixed_pattern_equipment = {}
    shared_default_equipment = {}
    for application in document.get('pattern_applications', []) if clear_actuals else []:
        # Reusing a prior day is not permission to treat its operator-selected
        # equipment as today's fact. Fixed pattern equipment stays in the graph.
        from src.services.process_pattern_service import _prefix
        from src.models.process_pattern import ProcessPatternRevision
        from sqlalchemy.orm import object_session
        owner = object_session(menu)
        source = owner.get(ProcessPatternRevision, application['pattern_revision_id']) if owner else None
        if source is None:
            raise HTTPException(422, '再利用するパターンの保存版が見つかりません')
        prefix = f"application-{application['application_id']}"
        fixed_pattern_equipment.update({_prefix(item['step_id'], prefix): item['equipment']
                                        for item in source.fixed_equipment})
        application['execution_selections'] = []
    if clear_actuals and document.get('shared_menu_source'):
        from src.models.shared_menu import SharedMenuRevision
        from sqlalchemy.orm import object_session
        owner = object_session(menu)
        source = owner.get(SharedMenuRevision, document['shared_menu_source']['revision_id']) if owner else None
        if source is None:
            raise HTTPException(422, '再利用する共有献立の保存版が見つかりません')
        shared_default_equipment = {
            step['id']: step['equipment'] for step in source.document.get('steps', [])
        }
        document['route_config'] = deepcopy(source.document.get('route_config'))
    for s in document['steps']:
        for i in s['inputs']:
            if i.get('ingredient_id'):
                i['ingredient_id'] = mapping[i['ingredient_id']]
        for output in s.get('outputs', []):
            if output.get('ingredient_ids') is not None:
                output['ingredient_ids'] = [mapping[mid] for mid in output['ingredient_ids']]
        # Daily work facts must not become facts for a different day by reuse.
        if clear_actuals:
            s.pop('execution', None)
            if s.pop('daily_work', None) is not None or s.get('basis', {}).get('agent_daily_question'):
                if s['id'] in fixed_pattern_equipment:
                    s['equipment'] = deepcopy(fixed_pattern_equipment[s['id']])
                else:
                    s['equipment']['id'] = None
                s.get('basis', {}).pop('agent_daily_question', None)
                s.get('basis', {}).pop('review', None)
                if document.get('route_config'): document['route_config']['step_ports'].pop(s['id'], None)
        elif s.get('daily_work'):
            for placement in s['daily_work']['placements']:
                placement['ingredient_ids'] = [mapping[mid] for mid in placement['ingredient_ids']]
        if clear_actuals and document.get('pattern_applications') and fixed_pattern_equipment.get(s['id']) != s['equipment']:
            s['equipment']['id'] = None
            if document.get('route_config'):
                document['route_config']['step_ports'].pop(s['id'], None)
        if clear_actuals and document.get('shared_menu_source'):
            if s['id'] in shared_default_equipment:
                s['equipment'] = deepcopy(shared_default_equipment[s['id']])
            else:
                # A day-only added step has no standard location to reuse.
                s['equipment']['id'] = None
    for application in document.get('pattern_applications', []):
        application['material_mapping'] = {
            slot: mapping[ingredient_id] for slot, ingredient_id in application['material_mapping'].items()
        }
    if document.get('shared_menu_source'):
        document['shared_menu_source']['material_mapping'] = {
            slot: mapping[mid] for slot, mid in document['shared_menu_source']['material_mapping'].items()
        }
    return document


def reuse_compatibility(source_document, target_document, menu, source_snapshot=None):
    """Compare material state and established process intent before reuse.

    Unknown target intent is returned for the explicit preview surface, but is
    not silently treated as equal.  A known mismatch needs an explicit request
    confirmation before the existing apply endpoint can write anything.
    """
    mapped = remap_materials(source_document, menu)
    if mapped is None:
        return {'compatible': False, 'conditions': [
            {'id': 'material-identities', 'status': 'mismatch',
             'message': '材料構成が異なるため工程マスターを適用できません'}]}
    target = {item['id']: item for item in target_document['ingredients']}
    conditions = []
    for item in mapped['ingredients']:
        target_item = target[item['id']]
        source_state, target_state = item.get('state', ''), target_item.get('state', '')
        status = 'unknown' if not source_state or not target_state else ('compatible' if source_state == target_state else 'mismatch')
        conditions.append({'id': f'state-{item["id"]}', 'status': status,
                           'message': f'{item["name"]}: 保存済み標準={source_state or "未指定"}、今回={target_state or "未指定"}'})
    # Existing target graph may be empty.  It is then a previewable unknown,
    # never evidence that the source's cooking intent matched.
    if target_document.get('steps'):
        def intent(doc):
            return [(s['operation'], s['equipment'].get('type'), s.get('conditions', {})) for s in doc['steps']]
        source_intent = intent(mapped)
        target_intent = intent(target_document)
        status = 'compatible' if source_intent == target_intent else 'mismatch'
    else:
        status = 'unknown'
    def describe_steps(document):
        return ' → '.join(s['operation'] + '（' + (s['equipment'].get('type') or '場所未指定') + '）' +
                         ('：' + ' / '.join(str(v) for v in s.get('conditions', {}).values() if v) if any(s.get('conditions', {}).values()) else '')
                         for s in document.get('steps', [])) or '未作成'
    process_message = '工程用途・別処理/混合条件：一致' if status == 'compatible' else (
        '工程の違い\n保存済み標準：' + describe_steps(mapped) + '\n今回：' + describe_steps(target_document))
    conditions.append({'id': 'process-intent', 'status': status, 'message': process_message})
    if source_snapshot is not None:
        current_source = snapshot(menu)
        previous = (source_snapshot.get('instructions'), [(i.get('name'), i.get('process_text')) for i in source_snapshot.get('ingredients', [])])
        current = (current_source.get('instructions'), [(i.get('name'), i.get('process_text')) for i in current_source.get('ingredients', [])])
        conditions.append({'id': 'handout-instructions', 'status': 'compatible' if previous == current else 'mismatch',
                           'message': '手配表の調理手順・材料別加工指示：' + ('一致' if previous == current else '保存元と今回で異なります')})
    return {'compatible': not any(item['status'] == 'mismatch' for item in conditions), 'conditions': conditions}


def reuse_compatibility_preview(session, menu_id, request):
    menu, binding, current, revision = state(session, menu_id)
    check_versions(menu, binding, current, request)
    recipe = session.get(ProcessRecipe, request.recipe_id)
    if not recipe or recipe.facility_id != menu.facility_id:
        raise HTTPException(404, '選択学校の工程マスターが見つかりません')
    source = session.scalar(select(ProcessRevision).where(
        ProcessRevision.recipe_id == recipe.id,
        ProcessRevision.number == request.expected_selected_recipe_revision,
        ProcessRevision.id == recipe.confirmed_revision_id,
        ProcessRevision.status == 'confirmed',
        ProcessRevision.scope == 'menu',
    ))
    if not source:
        raise HTTPException(409, '表示後に確認済み工程マスターが更新されました')
    target = revision.document if revision else seed(menu)
    return reuse_compatibility(source.document, target, menu, source.source_snapshot)


def check_versions(menu, binding, recipe, request):
    if menu.version != request.menu_version or (binding.version if binding else 0) != request.expected_version:
        conflict()
    expected = getattr(request, 'expected_recipe_revision', None)
    if expected is not None and expected != (recipe.version if recipe else 0):
        conflict()


def change_list(before, after, path=''):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [c for key in sorted(before.keys() | after.keys())
                for c in change_list(before.get(key), after.get(key), f'{path}/{key}')]
    return [{'path': path or '/', 'before': before, 'after': after}]


def user_edits(before, after, constraints=None):
    """Keep clears and deleted nodes, not just positive edits, across generations."""
    after = deepcopy(after)
    constraints = deepcopy(constraints or {})
    old_steps = {s['id']: s for s in before['steps']}
    current_ids = {s['id'] for s in after['steps']}
    removed = {s['id']: s for s in constraints.get('deleted_steps', [])}
    removed.update({k: v for k, v in old_steps.items() if k not in current_ids})
    constraints['deleted_steps'] = [v for k, v in removed.items() if k not in current_ids]
    for s in after['steps']:
        old = old_steps.get(s['id'])
        for key in ('operation', 'inputs', 'equipment', 'conditions', 'outputs'):
            value = s.get(key)
            has_value = any(value.values()) if isinstance(value, dict) else bool(value)
            if (old is not None and (old.get(key) != value or old.get('basis', {}).get(key) == 'user')) or (old is None and has_value):
                s['basis'][key] = 'user'
    return after, constraints


def mutation_operation(function):
    @wraps(function)
    def owned(session, *args, **kwargs):
        # begin_nested flushes pending state: reject it before entering the savepoint.
        if session.new or session.dirty or session.deleted:
            raise HTTPException(409, '未保存の変更を含むSessionでは工程を更新できません')
        connection = session.connection()
        if connection.dialect.name == 'sqlite':
            # A legacy sqlite3 SELECT/autobegin may not have issued BEGIN.
            # Keep RELEASE SAVEPOINT inside the caller's real transaction.
            driver = connection.connection.driver_connection
            if not driver.in_transaction:
                connection.exec_driver_sql('BEGIN')
        try:
            with session.begin_nested():
                return function(session, *args, **kwargs)
        except IntegrityError as exc:
            raise HTTPException(409, '工程が同時に保存されました。最新版と比較してください。') from exc
        except OperationalError as exc:
            code = getattr(exc.orig, 'sqlstate', None) or getattr(exc.orig, 'pgcode', None)
            sqlite_code = getattr(exc.orig, 'sqlite_errorcode', None)
            if (code in {'40001', '40P01', '55P03'}
                    or (isinstance(sqlite_code, int) and (sqlite_code & 255) in {5, 6})):
                raise HTTPException(409, '工程が同時に更新されています。入力を保持して再確認してください。') from exc
            raise
    return owned


def lock_menu(session, menu_id, version):
    if session.execute(update(MenuExecution).where(MenuExecution.id == menu_id,
            MenuExecution.version == version).values(version=version),
            execution_options={'synchronize_session': False}).rowcount != 1:
        if session.scalar(select(MenuExecution.id).where(MenuExecution.id == menu_id)) is None:
            raise HTTPException(404, '献立が見つかりません')
        conflict()
    # Caller is clean; refresh the CAS inputs after obtaining the menu lock.
    session.expire_all()


def lock_recipes(session, recipe_ids):
    """Shared order for existing recipe rows, including multi-recipe transfer."""
    for recipe_id in sorted(set(recipe_ids)):
        if session.execute(update(ProcessRecipe).where(ProcessRecipe.id == recipe_id)
                .values(version=ProcessRecipe.version),
                execution_options={'synchronize_session': False}).rowcount != 1:
            conflict()


@mutation_operation
def save(session, menu_id, request, actor_role='operator'):
    lock_menu(session, menu_id, request.menu_version)
    return _save_locked(session, menu_id, request, actor_role)


def _save_locked(session, menu_id, request, actor_role='operator', *, selected_recipe=None, selected_constraints=None, shared_adoption=False):
    menu, binding, recipe, previous = state(session, menu_id)
    check_versions(menu, binding, recipe, request)
    document = request.document.model_dump(exclude_none=False)
    source = document.get('shared_menu_source')
    old_source = previous.document.get('shared_menu_source') if previous else None
    if not shared_adoption and not selected_recipe and source != old_source:
        from src.services.shared_menu_service import daily_draft
        suggestion = daily_draft(session, menu) if previous is None else None
        if not suggestion or source != suggestion.get('shared_menu_source'):
            raise HTTPException(422, '共有献立の採用元は専用の採用操作で変更してください。出典を削除・書換えできません。')
    if source and request.scope != 'day':
        raise HTTPException(422, '共有献立を採用した工程は日次範囲でのみ保存できます。')
    if source:
        from src.services.shared_menu_service import _validate_equipment
        _validate_equipment(document, confirmed=request.status == 'confirmed')
    if previous and previous.document.get('pattern_applications') and not document.get('pattern_applications'):
        # A daily graph sourced from patterns must not silently lose its source
        # while being edited. Explicit graph deletion remains valid, but retains
        # the provenance needed by the schedule/output projection.
        if not document['steps']:
            document['pattern_applications'] = deepcopy(previous.document['pattern_applications'])
        else:
            raise HTTPException(422, '採用済みパターンの出典を削除できません。工程を空にする場合も出典は保持されます。')
    if previous and previous.document.get('daily_work_source') and not document.get('daily_work_source'):
        raise HTTPException(422, '当日の作業場所回答の出典を削除できません。工程を変更した場合は再確認してください。')
    if document.get('daily_work_source') and request.scope != 'day':
        raise HTTPException(422, '当日の作業回答は日次範囲でのみ保存できます。')
    check_materials(menu, document)
    supply_templates = {m['supply']['template_id'] for m in document['ingredients']
                        if m.get('supply') and m['supply'].get('template_id')}
    if supply_templates:
        from src.services.menu_process_route_service import load_template
        from src.services.process_material_journey import require_available_supplies
        try:
            for template_id in sorted(supply_templates):
                svg, manifest = load_template(template_id)
                require_available_supplies(document, manifest, svg)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if request.status == 'confirmed':
        try:
            request.document.check_confirmable()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if selected_recipe:
        recipe = selected_recipe
    elif (request.scope == 'menu' and recipe and previous and previous.scope == 'menu'
          and recipe.head_revision_id != previous.id):
        raise HTTPException(409, '共通工程が別の献立日から更新されています。最新のマスターと比較してから保存してください。')
    if recipe is None:
        recipe = ProcessRecipe(facility_id=menu.facility_id, name=menu.name, version=0)
        session.add(recipe)
        session.flush()
    number = recipe.version + 1
    if session.execute(update(ProcessRecipe).where(ProcessRecipe.id == recipe.id,
            ProcessRecipe.version == recipe.version).values(version=number),
            execution_options={'synchronize_session': False}).rowcount != 1:
        conflict()
    before = previous.document if previous else seed(menu)
    # User changes are stored with field-level provenance; unchanged labels survive.
    edit_base = document if selected_recipe else before
    edit_constraints = selected_constraints if selected_recipe else (previous.user_constraints if previous else None)
    document, constraints = user_edits(edit_base, document, edit_constraints)
    revision = ProcessRevision(recipe_id=recipe.id, number=number,
        parent_revision_id=previous.id if previous else None, menu_execution_id=menu_id,
        scope=request.scope, status=request.status, origin='user', actor_role=actor_role,
        document=document, source_snapshot=snapshot(menu), changes=change_list(before, document),
        user_constraints=constraints,
        change_note=request.change_note)
    session.add(revision)
    session.flush()
    if source:
        from src.models.shared_menu import SharedMenuRevision, SharedMenuAdoption
        shared_revision = session.get(SharedMenuRevision, source['revision_id'])
        mapping = source['material_mapping']
        if (not shared_revision or set(mapping) != {m['id'] for m in shared_revision.document['ingredients']}
                or not set(mapping.values()) <= {m['id'] for m in document['ingredients']}
                or len(mapping) != len(set(mapping.values()))):
            raise HTTPException(422, '共有献立の採用元と材料対応が一致しません')
        session.add(SharedMenuAdoption(process_revision_id=revision.id,
                    shared_menu_revision_id=shared_revision.id, material_mapping=mapping))
    if document.get('pattern_applications'):
        from src.services.process_pattern_service import record_applications
        record_applications(session, revision.id, document)
    if binding:
        result = session.execute(update(ProcessBinding).where(
                ProcessBinding.menu_execution_id == menu_id, ProcessBinding.version == request.expected_version
            ).values(recipe_id=recipe.id, revision_id=revision.id, version=request.expected_version + 1),
                execution_options={'synchronize_session': False})
        if result.rowcount != 1:
            conflict()
    else:
        session.add(ProcessBinding(menu_execution_id=menu_id, recipe_id=recipe.id,
                                   revision_id=revision.id, version=1))
        session.flush()
    if request.scope == 'menu':
        values = {'head_revision_id': revision.id}
        if request.status == 'confirmed':
            values['confirmed_revision_id'] = revision.id
        session.execute(update(ProcessRecipe).where(ProcessRecipe.id == recipe.id).values(**values),
                        execution_options={'synchronize_session': False})
    session.flush()
    session.expire_all()
    return context(session, menu_id)


def lock_selected_recipe(session, menu, request):
    """Fence the displayed confirmed revision, retaining the caller's transaction."""
    expected = getattr(request, 'expected_selected_recipe_revision', None)
    if (type(request.recipe_id) is not int or request.recipe_id <= 0
            or type(expected) is not int or expected <= 0):
        raise HTTPException(422, '表示済みレシピID・確認済み版を明示してください')
    if session.new or session.dirty or session.deleted:
        raise HTTPException(409, '未保存の変更を含むSessionではレシピを適用できません')
    lock_menu(session, menu.id, request.menu_version)
    menu = menu_for(session, menu.id)
    # Conditional UPDATE locks in PostgreSQL AND SQLite. The selected revision
    # is explicit; no active/head/latest read supplies a missing expectation.
    selected_id = select(ProcessRevision.id).where(
        ProcessRevision.recipe_id == request.recipe_id,
        ProcessRevision.number == expected,
        ProcessRevision.status == 'confirmed',
        ProcessRevision.scope == 'menu',
    ).scalar_subquery()
    with session.no_autoflush:
        fenced = session.execute(update(ProcessRecipe).where(
            ProcessRecipe.id == request.recipe_id,
            ProcessRecipe.facility_id == menu.facility_id,
            ProcessRecipe.confirmed_revision_id == selected_id,
        ).values(version=ProcessRecipe.version),
            execution_options={'synchronize_session': False})
        if fenced.rowcount != 1:
            raise HTTPException(409, '表示後に確認済みレシピが更新されました。入力を保持して再読込してください')
        # Refresh only a clean Session; never validate cached identity-map data.
        recipe = session.scalars(select(ProcessRecipe).where(
            ProcessRecipe.id == request.recipe_id,
        ).execution_options(populate_existing=True)).one()
        revision = session.scalars(select(ProcessRevision).where(
            ProcessRevision.recipe_id == request.recipe_id,
            ProcessRevision.number == expected,
            ProcessRevision.id == recipe.confirmed_revision_id,
            ProcessRevision.status == 'confirmed',
            ProcessRevision.scope == 'menu',
        ).execution_options(populate_existing=True)).one()
    return recipe, revision


@mutation_operation
def use_recipe(session, menu_id, request, actor_role='operator'):
    from src.schemas.menu_process import SaveRequest
    if session.new or session.dirty or session.deleted:
        raise HTTPException(409, '未保存の変更を含むSessionではレシピを適用できません')
    recipe, revision = lock_selected_recipe(session, SimpleNamespace(id=menu_id), request)
    menu, binding, current, _ = state(session, menu_id)
    check_versions(menu, binding, current, request)
    target_revision = state(session, menu_id)[3]
    target_document = target_revision.document if target_revision else seed(menu)
    compatibility = reuse_compatibility(revision.document, target_document, menu, revision.source_snapshot)
    mismatches = {item['id'] for item in compatibility['conditions'] if item['status'] != 'compatible'}
    if not mismatches <= set(request.confirmed_reuse_condition_ids):
        raise HTTPException(422, '材料状態または工程用途に相違・未確認があります。互換性プレビューで条件を確認してから明示適用してください。')
    document = remap_materials(revision.document, menu)
    if document is None:
        raise HTTPException(422, '材料構成が異なるため、そのまま適用できません。Gemini提案の参考例として利用できます。')
    constraints = deepcopy(revision.user_constraints)
    mapping = {old['id']: new['id'] for old, new in zip(revision.document['ingredients'], document['ingredients'])}
    for removed in constraints.get('deleted_steps', []):
        for source in removed['inputs']:
            if source.get('ingredient_id') in mapping:
                source['ingredient_id'] = mapping[source['ingredient_id']]
        for output in removed.get('outputs', []):
            if output.get('ingredient_ids') is not None:
                output['ingredient_ids'] = [mapping[mid] for mid in output['ingredient_ids']]
    payload = SaveRequest(expected_version=request.expected_version,
        expected_recipe_revision=current.version if current else 0,
        menu_version=request.menu_version, document=document, status='draft', scope='day',
        change_note=f'工程マスター #{recipe.id} の版 #{revision.number} を明示適用')
    return _save_locked(session, menu_id, payload, actor_role, selected_recipe=recipe, selected_constraints=constraints)


def confirmed_examples(session, menu, recipe):
    recipes = session.scalars(select(ProcessRecipe).where(
        ProcessRecipe.facility_id == menu.facility_id,
        ProcessRecipe.confirmed_revision_id.is_not(None))).all()
    names = {r.ingredient.name for r in menu.menu_ingredients}
    examples = []
    for r in recipes:
        rev = session.get(ProcessRevision, r.confirmed_revision_id)
        overlap = len(names & {i['name'] for i in rev.document['ingredients']})
        if overlap or (recipe and recipe.id == r.id):
            examples.append((1000 if recipe and recipe.id == r.id else overlap,
                             {'revision_id': rev.id, 'menu': r.name, 'document': rev.document,
                              'user_constraints': rev.user_constraints}))
    return [x[1] for x in sorted(examples, key=lambda x: (-x[0], x[1]['revision_id']))[:5]]


def deleted_step_match(step, removed):
    def sources(value):
        return sorted((i.get('ingredient_id') or '', i.get('step_id') or '', i.get('output_id') or '') for i in value.get('inputs', []))
    return step['id'] == removed['id'] or (step['operation'] == removed['operation'] and sources(step) == sources(removed))


def protect_existing(current, proposed, constraints=None):
    """Existing nonempty user content is immutable to Gemini. Fill gaps only."""
    if proposed.get('daily_work_source') != current.get('daily_work_source'):
        raise HTTPException(422, 'Geminiは利用者の当日回答を変更できません')
    if proposed['ingredients'] != current['ingredients']:
        raise HTTPException(422, 'Geminiが材料を変更したため提案を採用できません')
    steps = {s['id']: s for s in proposed['steps']}
    old_steps = {s['id']: s for s in current['steps']}
    for removed in (constraints or {}).get('deleted_steps', []):
        if any(deleted_step_match(s, removed) for s in proposed['steps']):
            raise HTTPException(422, 'Geminiが削除済み工程を復活させました。保存内容は変更していません。')
    for s in proposed['steps']:
        if s.get('daily_work') != old_steps.get(s['id'], {}).get('daily_work'):
            raise HTTPException(422, 'Geminiは一緒・別々の回答や食材ごとの使用設備を追加・変更できません')
        previous_id = old_steps.get(s['id'], {}).get('equipment', {}).get('id')
        if s['equipment']['id'] != previous_id:
            raise HTTPException(422, 'Geminiが実機IDを指定しました。実機の割当は推定対象外です。')
    existing_ids = [s['id'] for s in current['steps']]
    if [s['id'] for s in proposed['steps'] if s['id'] in existing_ids] != existing_ids:
        raise HTTPException(422, 'Geminiが既存工程の順序を変更しました。保存内容は変更していません。')
    for s in current['steps']:
        if s['id'] not in steps:
            raise HTTPException(422, 'Geminiが既存工程を削除したため提案を採用できません')
        other = steps[s['id']]
        for key in ('operation', 'inputs', 'equipment', 'conditions', 'outputs'):
            if s['basis'].get(key) == 'user' and s[key] != other[key]:
                raise HTTPException(422, 'Geminiがユーザーの変更・空欄化を上書きしました。保存内容は変更していません。')
        for key in ('operation', 'inputs', 'outputs'):
            if s[key] and s[key] != other[key]:
                raise HTTPException(422, 'Geminiがユーザー工程を変更しました。保存内容は変更していません。')
        for key in ('equipment', 'conditions', 'basis'):
            if any(v and other[key].get(k) != v for k, v in s[key].items()):
                raise HTTPException(422, 'Geminiがユーザー設定を変更しました。保存内容は変更していません。')


def infer(session, menu_id, request, *, supply_only=False, drawing_only=False):
    from src.services import menu_process_gemini as gemini
    from src.services import menu_process_planner as planner
    menu, binding, recipe, revision = state(session, menu_id)
    check_versions(menu, binding, recipe, request)
    current = request.document.model_dump()
    check_materials(menu, current)
    current, constraints = user_edits(revision.document if revision else seed(menu), current,
                                     revision.user_constraints if revision else None)
    examples = confirmed_examples(session, menu, recipe)
    material_instructions = [{'id': f'm{r.id}', 'instruction': r.process_text,
                              'quantity': str(r.required_quantity), 'unit': r.unit} for r in menu.menu_ingredients]
    plan = planner.compile_plan(menu.name, menu.cooking_instructions, material_instructions, current, constraints)
    prompt = gemini.build_prompt(menu.name, menu.cooking_instructions, material_instructions, plan, examples)
    source_before = snapshot(menu)
    inference_name, inference_instructions = menu.name, menu.cooking_instructions
    # Release read transaction before remote network call. Recheck all versions after.
    session.rollback()
    if supply_only or drawing_only:
        document, issues, raw, model = deepcopy(current), [], {}, 'existing-cooking-graph'
    elif plan['slots'] or plan['allow_added_steps']:
        document, issues, raw, model = gemini.complete_plan(prompt, plan)
    else:
        raw, model = {'plan_hash': plan['hash'], 'added_steps': [], 'fills': [], 'issues': []}, 'fixed-rules-v1'
        try:
            document, issues = planner.apply_patch(plan, raw)
        except ValueError as exc:
            raise HTTPException(422, '固定工程の整合性を確認できませんでした。保存内容は変更していません。') from exc
    protect_existing(current, document, constraints)
    drawing_trace = None
    if not supply_only and document.get('schema_version') == 2:
        from src.services.menu_material_drawing_inference import infer_missing as infer_drawing
        try:
            document, drawing_issues, drawing_trace = infer_drawing(document, inference_name, inference_instructions,
                                                                    material_instructions, examples)
        except ValueError as exc:
            raise HTTPException(422, '描画対象の推論案を検証できませんでした。保存内容は変更していません。'+str(exc)) from exc
        issues.extend(drawing_issues)
        if drawing_trace:
            prompt += '\n'+json.dumps(drawing_trace, ensure_ascii=False)
            if drawing_only: model = drawing_trace['calls'][-1]['model']
    from src.services.menu_material_supply_inference import infer_missing
    try:
        if drawing_only:
            supply_issues, supply_trace = [], None
        else:
            document, supply_issues, supply_trace = infer_missing(document, inference_name, inference_instructions,
                                                                 material_instructions, examples)
    except ValueError as exc:
        raise HTTPException(422, '搬入・保管の推論案を検証できませんでした。保存内容は変更していません。'+str(exc)) from exc
    issues.extend(supply_issues)
    if supply_trace:
        model = supply_trace['calls'][-1]['model'] if supply_only else model
        prompt += '\n'+json.dumps(supply_trace, ensure_ascii=False)
    session.expire_all()
    menu, binding, recipe, _ = state(session, menu_id)
    check_versions(menu, binding, recipe, request)
    if snapshot(menu) != source_before:
        conflict()
    proposal = ProcessProposal(menu_execution_id=menu_id, base_version=request.expected_version,
        model=model, prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
        example_revision_ids=[e['revision_id'] for e in examples], input_document=current, document=document,
        inference_trace={'plan': plan, 'response': raw, 'issues': issues,
                         'material_supply': supply_trace,
                         'material_drawing': drawing_trace,
                         'source_snapshot': source_before,
                         'recipe_revision': recipe.version if recipe else 0})
    session.add(proposal)
    session.flush()
    return proposal_response(proposal)


def proposal_response(proposal):
    trace = proposal.inference_trace
    return {'proposal_id': proposal.id, 'document': deepcopy(proposal.document),
            'base_version': proposal.base_version,
            'example_revision_ids': proposal.example_revision_ids, 'model': proposal.model,
            'issues': trace.get('issues', []),
            'fixed_step_ids': list(trace.get('plan', {}).get('new_fixed_ids', []))}


def _apply_correction_operations(current, plan, request, constraints):
    """Apply only the explicitly selected semantic fields, then validate graph invariants."""
    document = deepcopy(current)
    ingredients = {item['id']: item for item in document['ingredients']}
    steps = {item['id']: item for item in document['steps']}
    allowed_ingredients = set(request.target_ingredient_ids)
    allowed_steps = set(request.target_step_ids)
    unknown = (allowed_ingredients - set(ingredients)) | (allowed_steps - set(steps))
    if unknown:
        raise HTTPException(422, '修正対象が現在の工程に存在しません。再読込して対象を選び直してください。')
    # Target materials authorize their existing lineage and downstream joins;
    # they do not authorize a rewrite of unrelated material facts.
    by_step, _ = material_flow(current)
    lineage_steps = {sid for sid, origins in by_step.items() if set(origins) & allowed_ingredients}
    permitted_steps = allowed_steps | lineage_steps
    permitted_materials = set(allowed_ingredients)
    for step_id in allowed_steps:
        permitted_materials.update(by_step.get(step_id, []))
    for operation in plan.operations:
        if operation.kind == 'replace_material_drawing':
            if operation.ingredient_id not in permitted_materials:
                raise HTTPException(422, '対象外材料の描画設定変更を拒否しました')
            try:
                drawing = MaterialDrawing.model_validate(operation.value).model_dump(exclude_none=False)
            except ValueError as exc:
                raise HTTPException(422, '材料の描画設定が不正です') from exc
            if drawing['status'] == 'user-confirmed':
                raise HTTPException(422, 'Geminiは描画設定を利用者確認済みにできません')
            ingredients[operation.ingredient_id]['drawing'] = drawing
        elif operation.kind == 'replace_ingredient_state':
            if operation.ingredient_id not in allowed_ingredients:
                raise HTTPException(422, '指定外の材料を修正する提案を拒否しました')
            if not isinstance(operation.value, str) or len(operation.value) > 200:
                raise HTTPException(422, '材料状態の修正値が不正です')
            ingredients[operation.ingredient_id]['state'] = operation.value
            for sid in lineage_steps:
                steps[sid].setdefault('basis', {})['review'] = 'pending'
                steps[sid]['basis']['correction_instruction'] = request.instruction
        elif operation.kind == 'delete_step':
            if operation.step_id not in permitted_steps:
                raise HTTPException(422, '対象材料の経路外にある工程削除を拒否しました')
            if set(by_step.get(operation.step_id, [])) - permitted_materials:
                raise HTTPException(422, '対象外材料と共有される工程削除を拒否しました')
            document['steps'] = [item for item in document['steps'] if item['id'] != operation.step_id]
            steps.pop(operation.step_id, None)
            if document.get('route_config'):
                document['route_config'].get('step_ports', {}).pop(operation.step_id, None)
        elif operation.kind == 'add_step':
            if not isinstance(operation.value, dict):
                raise HTTPException(422, '追加工程の形式が不正です')
            try:
                added = Step.model_validate(operation.value).model_dump(exclude_none=False)
            except ValueError as exc:
                raise HTTPException(422, '追加工程の内容が不正です') from exc
            if not added['id'].startswith('correction-') or added['id'] in steps:
                raise HTTPException(422, '追加工程IDは新規のcorrection-接頭辞で指定してください')
            if any(deleted_step_match(added, removed) for removed in constraints.get('deleted_steps', [])):
                raise HTTPException(422, '利用者が削除した工程を別IDで復活する提案は適用できません。必要な場合は手動で追加してください。')
            if added.get('basis', {}).get('review') == 'user-confirmed':
                raise HTTPException(422, 'Geminiは利用者確認済みを設定できません')
            if any(str(key).lower() in {'geometry', 'position', 'coordinate', 'layout', 'port', 'equipment_id', 'model'}
                   for key in added.get('conditions', {})):
                raise HTTPException(422, '追加工程の条件に図面・モデル・設備実体情報は保存できません')
            if added['equipment'].get('id') is not None:
                raise HTTPException(422, '追加工程に実機IDは指定できません')
            for source in added['inputs']:
                if source.get('ingredient_id') and source['ingredient_id'] not in permitted_materials:
                    raise HTTPException(422, '対象外材料を使う追加工程を拒否しました')
                if source.get('step_id') and source['step_id'] not in permitted_steps:
                    raise HTTPException(422, '対象経路外を参照する追加工程を拒否しました')
            document['steps'].append(added)
            added.setdefault('basis', {})['review'] = 'pending'
            added['basis']['correction_instruction'] = request.instruction
            steps[added['id']] = added
            permitted_steps.add(added['id'])
        else:
            if operation.step_id not in permitted_steps:
                raise HTTPException(422, '対象材料の経路外にある工程修正を拒否しました')
            shared_with = set(by_step.get(operation.step_id, [])) - permitted_materials
            if shared_with and operation.field != 'inputs':
                raise HTTPException(422, '選択工程は対象外材料と共有されています。影響する全材料を選択するか、別工程を追加してください。')
            step = steps[operation.step_id]
            if operation.field == 'equipment':
                if not isinstance(operation.value, dict) or operation.value.get('id') is not None:
                    raise HTTPException(422, '実機IDを含む設備修正は提案できません')
            if operation.field == 'inputs':
                if not isinstance(operation.value, list):
                    raise HTTPException(422, '工程接続の修正値が不正です')
                def identity(source):
                    return (source.get('ingredient_id'), source.get('step_id'), source.get('output_id'))
                old_sources = {identity(source) for source in step['inputs']}
                if any(not isinstance(source, dict) for source in operation.value):
                    raise HTTPException(422, '工程接続の修正値が不正です')
                def protected_input(source):
                    origins = {source['ingredient_id']} if source.get('ingredient_id') else set(by_step.get(source.get('step_id'), []))
                    return bool(origins - permitted_materials or
                                (source.get('step_id') and source['step_id'] not in permitted_steps))
                if ([source for source in step['inputs'] if protected_input(source)] !=
                        [source for source in operation.value if protected_input(source)]):
                    raise HTTPException(422, '対象外材料・工程の接続または付随情報を変更する修正を拒否しました')
                for source in operation.value:
                    if not isinstance(source, dict):
                        raise HTTPException(422, '工程接続の修正値が不正です')
                    unchanged = identity(source) in old_sources
                    if not unchanged and source.get('ingredient_id') and source['ingredient_id'] not in permitted_materials:
                        raise HTTPException(422, '対象外材料への接続変更を拒否しました')
                    if not unchanged and source.get('step_id') and source['step_id'] not in permitted_steps:
                        raise HTTPException(422, '対象経路外への接続変更を拒否しました')
            if operation.field == 'conditions':
                if not isinstance(operation.value, dict):
                    raise HTTPException(422, '工程条件の修正値が不正です')
                prohibited = {'geometry', 'position', 'coordinate', 'layout', 'port', 'equipment_id', 'model'}
                if any(str(key).lower() in prohibited for key in operation.value):
                    raise HTTPException(422, '工程条件に図面・モデル・設備実体情報は保存できません')
            if operation.field == 'equipment':
                if operation.value.get('type') == step['equipment'].get('type'):
                    operation.value = {**operation.value, 'id': step['equipment'].get('id')}
                elif document.get('route_config'):
                    document['route_config'].get('step_ports', {}).pop(operation.step_id, None)
            step[operation.field] = operation.value
            step.setdefault('basis', {})['review'] = 'pending'
            step['basis']['correction_instruction'] = request.instruction
    # Operations may insert a predecessor for an existing step.  Persist the
    # stable topological order required by ProcessDocument; cycles are rejected.
    original_order = {item['id']: index for index, item in enumerate(document['steps'])}
    ordered, remaining = [], {item['id']: item for item in document['steps']}
    while remaining:
        ready = [item for item in remaining.values() if all(
            not source.get('step_id') or source['step_id'] in {done['id'] for done in ordered}
            for source in item['inputs'])]
        if not ready:
            raise HTTPException(422, '修正案に循環または未解決の前工程参照があります')
        for item in sorted(ready, key=lambda value: original_order[value['id']]):
            ordered.append(item)
            remaining.pop(item['id'])
    document['steps'] = ordered
    original = {s['id']: s for s in current['steps']}
    for step in ordered:
        if step != original.get(step['id']) and any(deleted_step_match(step, removed) for removed in constraints.get('deleted_steps', [])):
            raise HTTPException(422, '利用者が削除した工程を復活する提案は適用できません')
    try:
        # This enforces graph lineage, source IDs, split/mixing invariants and
        # known equipment kinds after every proposed edit.
        result = ProcessDocument.model_validate(document).model_dump(exclude_none=False)
    except ValueError as exc:
        raise HTTPException(422, f'修正案の工程整合性を確認できません: {exc}') from exc
    return result


def correction_change_list(before, after):
    """ID-addressed preview rows rather than one opaque steps-array replacement."""
    changes = []
    for collection in ('ingredients', 'steps'):
        old = {item['id']: item for item in before[collection]}
        new = {item['id']: item for item in after[collection]}
        for item_id in sorted(old.keys() | new.keys()):
            if item_id not in old or item_id not in new:
                changes.append({'path': f'/{collection}/{item_id}', 'before': old.get(item_id), 'after': new.get(item_id)})
            else:
                changes.extend(change_list(old[item_id], new[item_id], f'/{collection}/{item_id}'))
    changes.extend(change_list(before.get('route_config'), after.get('route_config'), '/route_config'))
    return changes


def correct(session, menu_id, request):
    """Generate a non-persisted, CAS-fenced correction preview from a natural instruction."""
    from src.services import menu_process_gemini as gemini
    menu, binding, recipe, revision = state(session, menu_id)
    check_versions(menu, binding, recipe, request)
    current = request.document.model_dump(exclude_none=False)
    check_materials(menu, current)
    if (set(request.target_ingredient_ids) - {i['id'] for i in current['ingredients']} or
            set(request.target_step_ids) - {s['id'] for s in current['steps']}):
        raise HTTPException(422, '修正対象が現在の工程に存在しません')
    constraints = deepcopy(revision.user_constraints if revision else {})
    source_before = snapshot(menu)
    prompt = gemini.build_correction_prompt(menu, current, request, constraints)
    # Do not hold a database read transaction during the remote model request.
    session.rollback()
    raw, _model = gemini.generate(prompt, schema=gemini.correction_schema())
    try:
        plan = CorrectionPlan.model_validate(raw)
    except ValueError as exc:
        raise HTTPException(422, 'Geminiの修正案を検証できませんでした。保存内容は変更していません。') from exc
    if plan.question:
        proposed = current
    else:
        proposed = _apply_correction_operations(current, plan, request, constraints)
    session.expire_all()
    menu, binding, recipe, _ = state(session, menu_id)
    check_versions(menu, binding, recipe, request)
    if snapshot(menu) != source_before:
        conflict()
    return {
        'base_version': request.expected_version,
        'document': proposed,
        'changes': [] if plan.question else correction_change_list(current, proposed),
        'question': plan.question,
        'choices': plan.choices,
        'issues': plan.issues,
        'instruction': request.instruction,
    }


def route_input(session, menu_id):
    """Downstream contract: frozen process semantics, never a newly inferred route."""
    menu, _, _, revision = state(session, menu_id)
    if not revision or revision.status != 'confirmed':
        raise HTTPException(409, '動線用データには確認済みの工程が必要です')
    if revision.source_snapshot != snapshot(menu):
        raise HTTPException(409, '献立が工程確認後に変更されています。再確認してください。')
    doc = ProcessDocument.model_validate(revision.document)
    doc.check_confirmable()
    missing = [s.id for s in doc.steps if not s.equipment.id]
    return {'menu_id': menu_id, 'revision_id': revision.id, 'document': revision.document,
            'unassigned_equipment_steps': missing, 'process_confirmed': True,
            'ready_for_geometry': False,
            'geometry_note': '設備IDと施設図面の照合は動線生成側で必要です。ここでは工程のみを返します。'}
