from copy import deepcopy
import unicodedata

from fastapi import HTTPException
from sqlalchemy import select, update

from src.models.school_lunch import Ingredient
from src.models.shared_menu import SharedMenu, SharedMenuAdoption, SharedMenuRevision
from src.schemas.menu_process import ProcessDocument
from src.services import menu_process_service as process
from src.services.menu_process_route_service import options as route_options


def revision_data(revision):
    return {
        'id': revision.id, 'number': revision.number,
        'parent_revision_id': revision.parent_revision_id, 'status': revision.status,
        'source_month': revision.source_month, 'provenance': revision.provenance,
        'document': revision.document, 'pattern_associations': revision.pattern_associations,
        'change_note': revision.change_note,
        'ingredient_count': len(revision.document['ingredients']),
        'step_count': len(revision.document['steps']),
        'created_at': revision.created_at.isoformat(),
    }


def data(menu, revision):
    return {'id': menu.id, 'name': menu.name, 'version': menu.version,
            'latest_revision': revision_data(revision)}


def list_(session, query, month, summary=False):
    statement = select(SharedMenu, SharedMenuRevision).join(
        SharedMenuRevision, SharedMenu.head_revision_id == SharedMenuRevision.id
    ).order_by(SharedMenu.name, SharedMenu.id)
    if query:
        statement = statement.where(SharedMenu.name.ilike('%' + query + '%'))
    if month:
        statement = statement.where(SharedMenuRevision.source_month == month)
    items = []
    for menu, revision in session.execute(statement).all():
        item = data(menu, revision)
        if summary:
            item['latest_revision'] = {key: item['latest_revision'][key] for key in (
                'id', 'number', 'status', 'source_month', 'ingredient_count', 'step_count'
            )}
        items.append(item)
    return {'items': items}


def get(session, shared_menu_id):
    menu = session.get(SharedMenu, shared_menu_id)
    if not menu:
        raise HTTPException(404, '共有献立が見つかりません')
    revisions = session.scalars(select(SharedMenuRevision).where(
        SharedMenuRevision.shared_menu_id == shared_menu_id
    ).order_by(SharedMenuRevision.number.desc())).all()
    return {**data(menu, session.get(SharedMenuRevision, menu.head_revision_id)),
            'revisions': [revision_data(revision) for revision in revisions]}


def daily_draft(session, menu):
    """Offer a unique matching catalog pattern, without adopting or saving it.

    Names and the complete ordered material list must agree. Multiple matching
    recipes remain an operator choice; no first-candidate selection is allowed.
    """
    def identity(value):
        return ''.join(unicodedata.normalize('NFKC', value).split()).lstrip('★')

    expected = process.seed(menu)['ingredients']
    names = [identity(item['name']) for item in expected]
    matches = []
    for catalog, revision in session.execute(select(SharedMenu, SharedMenuRevision).join(
            SharedMenuRevision, SharedMenu.head_revision_id == SharedMenuRevision.id)).all():
        if (identity(catalog.name) == identity(menu.name)
                and [identity(item['name']) for item in revision.document['ingredients']] == names
                and revision.document['steps']):
            matches.append(revision)
    if len(matches) != 1:
        return None
    source = matches[0]
    mapping = {old['id']: new['id'] for old, new in zip(source.document['ingredients'], expected)}
    return adoption_document(menu, source, mapping)


def adoption_document(menu, source, mapping):
    """Shared material remapping for an unsaved suggestion and explicit adoption."""
    document = deepcopy(source.document)
    expected = process.seed(menu)['ingredients']
    source_ids = {ingredient['id'] for ingredient in document['ingredients']}
    expected_ids = {ingredient['id'] for ingredient in expected}
    if (set(mapping) != source_ids or set(mapping.values()) != expected_ids
            or len(set(mapping.values())) != len(mapping)):
        raise HTTPException(422, '共有献立と日次材料を全て明示対応してください')
    for step in document['steps']:
        step.pop('execution', None)
        for item in step['inputs']:
            if item.get('ingredient_id'):
                item['ingredient_id'] = mapping[item['ingredient_id']]
        for output in step.get('outputs', []):
            if output.get('ingredient_ids') is not None:
                output['ingredient_ids'] = [mapping[item] for item in output['ingredient_ids']]
    settings = {mapping[item['id']]: {key: deepcopy(item[key])
                for key in ('state', 'supply', 'drawing') if item.get(key) is not None}
                for item in document['ingredients']}
    document['ingredients'] = [{**deepcopy(item), **settings[item['id']]} for item in expected]
    document['shared_menu_source'] = {'revision_id': source.id, 'material_mapping': mapping}
    return document


def editor_options(session):
    templates = route_options()['templates']
    operations, documented_names = set(), set()
    for document in session.scalars(select(SharedMenuRevision.document)).all():
        operations.update(step['operation'] for step in document.get('steps', [])
                          if step.get('operation'))
        documented_names.update(ingredient['name'] for ingredient in document.get('ingredients', [])
                                if ingredient.get('name'))
    ingredients = [{'id': ingredient.id, 'name': ingredient.name,
                    'category': ingredient.category, 'stock_unit': ingredient.stock_unit,
                    'order_unit': ingredient.order_unit}
                   for ingredient in session.scalars(select(Ingredient).order_by(
                       Ingredient.name, Ingredient.id)).all()]
    known_names = {ingredient['name'] for ingredient in ingredients}
    ingredients.extend({'id': None, 'name': name, 'category': None,
                        'stock_unit': None, 'order_unit': None}
                       for name in sorted(documented_names - known_names))
    return {'templates': templates, 'operations': sorted(operations), 'ingredients': ingredients}


def _equipment_by_template():
    return {template['template_id']: {item['id']: item for item in template['equipment']}
            for template in route_options()['templates']}


def _validate_equipment(document, *, confirmed):
    config = document.get('route_config') or {}
    template_id = config.get('template_id')
    actual_equipment = [equipment for step in document['steps'] for equipment in ([p['equipment'] for p in step['daily_work']['placements']] if step.get('daily_work') else [step['equipment']])]
    has_equipment = any(equipment.get('id') for equipment in actual_equipment)
    if confirmed and not template_id:
        raise HTTPException(422, '確認済みにするには厨房図面を選択してください')
    if has_equipment and not template_id:
        raise HTTPException(422, '設備を指定するには厨房図面を選択してください')
    if not template_id:
        return
    inventory = _equipment_by_template().get(template_id)
    if inventory is None:
        raise HTTPException(422, '厨房図面が登録されていません')
    for equipment in actual_equipment:
        kind, equipment_id = equipment.get('type'), equipment.get('id')
        if confirmed and bool(kind) != bool(equipment_id):
            raise HTTPException(422, '設備種類と具体的な設備IDを対で指定してください')
        if equipment_id and not kind:
            raise HTTPException(422, '設備IDには設備種類が必要です')
        if confirmed and not equipment_id:
            raise HTTPException(422, '確認済みにするには各工程の具体的な設備IDが必要です')
        if equipment_id:
            selected = inventory.get(equipment_id)
            if selected is None or selected['type'] != kind:
                raise HTTPException(422, '使用設備は選択した厨房図面に存在しません')


def validate(body):
    document = body.document.model_dump(exclude_none=False)
    if document.get('daily_work_source') or any(s.get('daily_work') for s in document['steps']):
        raise HTTPException(422, '当日の一緒・別々の回答や使用設備は共有献立に保存できません')
    if document.get('pattern_applications'):
        raise HTTPException(422, '日次パターン採用記録は共有献立に保存できません')
    if document.get('shared_menu_source') is not None:
        raise HTTPException(422, '日次共有献立出典は共有献立に保存できません')
    if any(step.get('execution') is not None for step in document['steps']):
        raise HTTPException(422, '日次実績executionは共有献立に保存できません')
    _validate_equipment(document, confirmed=body.status == 'confirmed')
    if body.status == 'confirmed':
        try:
            body.document.check_confirmable(require_equipment_ids=True)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    return document


def create(session, body):
    if body.expected_version != 0:
        raise HTTPException(422, '新規共有献立のexpected_versionは0です')
    document = validate(body)
    menu = SharedMenu(name=body.name, version=1)
    session.add(menu)
    session.flush()
    revision = SharedMenuRevision(shared_menu_id=menu.id, number=1, status=body.status,
                                  source_month=body.source_month, provenance=body.provenance,
                                  document=document, pattern_associations=body.pattern_associations,
                                  change_note=body.change_note)
    session.add(revision)
    session.flush()
    menu.head_revision_id = revision.id
    return data(menu, revision)


def revise(session, shared_menu_id, body):
    menu = session.get(SharedMenu, shared_menu_id)
    if not menu:
        raise HTTPException(404, '共有献立が見つかりません')
    if menu.version != body.expected_version:
        raise HTTPException(409, '共有献立が更新されています')
    previous = session.get(SharedMenuRevision, menu.head_revision_id)
    document = validate(body)
    changed = session.execute(update(SharedMenu).where(
        SharedMenu.id == shared_menu_id, SharedMenu.version == body.expected_version
    ).values(version=body.expected_version + 1))
    if changed.rowcount != 1:
        raise HTTPException(409, '共有献立が同時に更新されています')
    revision = SharedMenuRevision(shared_menu_id=shared_menu_id,
                                  number=body.expected_version + 1,
                                  parent_revision_id=previous.id, status=body.status,
                                  source_month=body.source_month, provenance=body.provenance,
                                  document=document, pattern_associations=body.pattern_associations,
                                  change_note=body.change_note)
    session.add(revision)
    session.flush()
    session.execute(update(SharedMenu).where(SharedMenu.id == shared_menu_id).values(
        name=body.name, head_revision_id=revision.id))
    session.flush()
    session.expire(menu)
    return data(session.get(SharedMenu, shared_menu_id), revision)


@process.mutation_operation
def adopt(session, menu_id, body):
    process.lock_menu(session, menu_id, body.expected_menu_version)
    menu, binding, recipe, _ = process.state(session, menu_id)
    if (menu.version != body.expected_menu_version
            or (binding.version if binding else 0) != body.expected_binding_version
            or (recipe.version if recipe else 0) != body.expected_recipe_revision):
        process.conflict()
    source = session.get(SharedMenuRevision, body.shared_menu_revision_id)
    if not source:
        raise HTTPException(404, '共有献立改訂が見つかりません')
    document = adoption_document(menu, source, body.material_mapping)
    request = type('AdoptionRequest', (), {
        'menu_version': body.expected_menu_version,
        'expected_version': body.expected_binding_version,
        'expected_recipe_revision': body.expected_recipe_revision,
        'scope': 'day', 'status': 'draft',
        'document': ProcessDocument.model_validate(document), 'change_note': body.change_note,
    })()
    result = process._save_locked(session, menu_id, request, 'operator', shared_adoption=True)
    return {**result, 'shared_menu_source_revision_id': source.id}
