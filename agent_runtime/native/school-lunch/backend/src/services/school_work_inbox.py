"""Read registered work, never manufacture calendar dates or source associations."""
from datetime import date

from sqlalchemy import String, cast, select
from sqlalchemy.orm import load_only

from src.models.school_lunch import Facility, MenuExecution, DailyWorkSchedule
from src.models.route_diagram import RouteDiagram
from src.models.implementation_price_draft import ImplementationPriceDraft
from src.models.menu_process import ProcessBinding, ProcessRevision, ProcessRecipe
from src.models.menu_process_route import ProcessRouteArtifact
from src.services.route_diagram_service import blockers_for_diagrams
from src.services.implementation_price_scope_service import menu_source_ids


def registered_work(session, *, facility_id=None, month=None):
    try:
        month_start = date.fromisoformat(month + "-01") if month is not None else None
    except ValueError:
        month_start = None
    month_end = date(month_start.year + (month_start.month == 12), month_start.month % 12 + 1, 1) if month_start else None

    def scoped(model, day_column):
        statement = select(model).order_by(model.id)
        if facility_id is not None:
            statement = statement.where(model.facility_id == facility_id)
        if month_start is not None:
            statement = statement.where(day_column >= month_start, day_column < month_end)
        elif month is not None:
            # The service also supports literal date-prefix searches, not guessed months.
            statement = statement.where(cast(day_column, String).startswith(month + '-', autoescape=True))
        return statement

    facility_query = select(Facility)
    if facility_id is not None:
        facility_query = facility_query.where(Facility.id == facility_id)
    facilities = {row.id: row.name for row in session.scalars(facility_query)}
    menu_query = scoped(MenuExecution, MenuExecution.service_date).options(load_only(
        MenuExecution.id, MenuExecution.facility_id, MenuExecution.service_date,
        MenuExecution.name, MenuExecution.version, MenuExecution.status, MenuExecution.source_document_id,
    ))
    menus = list(session.scalars(menu_query))
    menu_ids = {row.id for row in menus}
    scoped_menu_ids = menu_query.with_only_columns(MenuExecution.id).order_by(None)
    binding_query = select(ProcessBinding).where(ProcessBinding.menu_execution_id.in_(scoped_menu_ids))
    bindings = {row.menu_execution_id: row for row in session.scalars(binding_query)} if menu_ids else {}
    revisions = {row.id: row for row in session.scalars(select(ProcessRevision).options(
        load_only(ProcessRevision.id, ProcessRevision.recipe_id, ProcessRevision.status)).where(
        ProcessRevision.id.in_(binding_query.with_only_columns(ProcessBinding.revision_id))))} if bindings else {}
    recipes = {row.id: row for row in session.scalars(select(ProcessRecipe).options(
        load_only(ProcessRecipe.id, ProcessRecipe.facility_id)).where(
        ProcessRecipe.id.in_(binding_query.with_only_columns(ProcessBinding.recipe_id))))} if bindings else {}
    artifacts = {}
    if menu_ids:
        for row in session.scalars(select(ProcessRouteArtifact).options(load_only(
            ProcessRouteArtifact.id, ProcessRouteArtifact.menu_execution_id,
            ProcessRouteArtifact.process_revision_id, ProcessRouteArtifact.generation_status,
        )).where(ProcessRouteArtifact.menu_execution_id.in_(scoped_menu_ids))):
            artifacts.setdefault(row.menu_execution_id, []).append(row)
    menu_versions = {}
    for row in menus:
        menu_versions.setdefault((row.facility_id, row.service_date), {})[str(row.id)] = row.version
    prices = list(session.scalars(scoped(ImplementationPriceDraft, ImplementationPriceDraft.implementation_date)))
    referenced_ids = set()
    for row in prices:
        if isinstance(row.payload_json, dict):
            try:
                referenced_ids.update(menu_source_ids(row.payload_json))
            except ValueError:
                # The unchanged serialization below reports malformed references.
                pass
    sources = {row.id: row for row in menus}
    outside_ids = sorted(referenced_ids - menu_ids)
    # Explicit JSON references can span scopes; bound each query's parameters.
    for start in range(0, len(outside_ids), 500):
        sources.update((row.id, row) for row in session.scalars(
            select(MenuExecution).where(MenuExecution.id.in_(outside_ids[start:start + 500]))))
    items = {}

    def item(school, day, orphan=None):
        key = f"price:{orphan}" if orphan else f"school:{school}:day:{day.isoformat()}"
        if key not in items:
            items[key] = {"id": key, "facility_id": school, "facility_name": facilities.get(school),
                          "service_date": day.isoformat() if day else None, "menus": [],
                          "prices": [], "schedules": [], "routes": [], "next_actions": [],
                          "review_reasons": []}
        return items[key]

    for row in menus:
        value = item(row.facility_id, row.service_date)
        value['menus'].append({'id': row.id, 'name': row.name, 'version': row.version,
                               'status': row.status, 'source_document_id': row.source_document_id,
                               'process': None, 'route_artifacts': []})
        menu = value['menus'][-1]
        binding = bindings.get(row.id)
        if binding:
            revision = revisions.get(binding.revision_id)
            recipe = recipes.get(binding.recipe_id)
            if not revision or not recipe or recipe.facility_id != row.facility_id or revision.recipe_id != recipe.id:
                value['review_reasons'].append('保存工程の参照不一致')
            else:
                menu['process'] = {'revision_id': revision.id, 'version': binding.version, 'status': revision.status}
        for artifact in artifacts.get(row.id, []):
            menu['route_artifacts'].append({'id': artifact.id, 'process_revision_id': artifact.process_revision_id,
                                           'generation_status': artifact.generation_status})
    for row in session.scalars(scoped(DailyWorkSchedule, DailyWorkSchedule.service_date).options(load_only(
        DailyWorkSchedule.id, DailyWorkSchedule.facility_id, DailyWorkSchedule.service_date,
        DailyWorkSchedule.source_menu_versions, DailyWorkSchedule.version, DailyWorkSchedule.status,
    ))):
        value = item(row.facility_id, row.service_date)
        stale = row.source_menu_versions != menu_versions.get((row.facility_id, row.service_date), {})
        value['schedules'].append({'id': row.id, 'version': row.version, 'status': row.status, 'stale': stale})
        if stale:
            value['review_reasons'].append('献立変更後の工程表')
    diagrams = list(session.scalars(scoped(RouteDiagram, RouteDiagram.service_date)))
    diagram_blockers = blockers_for_diagrams(session, diagrams)
    for row in diagrams:
        value = item(row.facility_id, row.service_date)
        blockers = diagram_blockers[row.id]
        value['routes'].append({'id': row.id, 'version': row.version, 'layout_id': row.layout_id,
                          'layout_version': row.layout_version, 'blockers': blockers})
        value['review_reasons'].extend(blockers)
    for row in prices:
        payload = row.payload_json
        issues = []
        if row.facility_id is None:
            issues.append('価格資料の学校未所属')
        elif row.facility_id not in facilities:
            issues.append('価格資料の学校を確認できません')
        if row.implementation_date is None:
            issues.append('価格資料の実施日未登録')
        if not isinstance(payload, dict):
            payload = {}
            issues.append('価格資料の保存形式を確認できません')
        if payload.get('facility_id') != row.facility_id:
            issues.append('価格資料の学校参照不一致')
        if payload.get('implementation_date') != (row.implementation_date.isoformat() if row.implementation_date else None):
            issues.append('価格資料の実施日参照不一致')
        try:
            source_ids = menu_source_ids(payload)
        except ValueError:
            source_ids = []
            issues.append('価格資料の献立参照形式不正')
        for identifier in source_ids:
            source = sources.get(identifier)
            if source is None or source.facility_id != row.facility_id or source.service_date != row.implementation_date:
                issues.append('価格資料の献立参照不一致')
                break
        value = item(row.facility_id, row.implementation_date,
                     row.id if row.facility_id not in facilities or row.implementation_date is None else None)
        value['prices'].append({'id': row.id, 'label': row.label,
                                'source_menu_execution_ids': source_ids,
                                'issues': issues})
        value['review_reasons'].extend(issues)
    for value in items.values():
        if value['menus']:
            if not value['schedules']:
                value['next_actions'].append('登録献立の工程表が未作成')
            if any(menu['process'] is None for menu in value['menus']):
                value['next_actions'].append('保存工程がない献立')
            if not value['prices']:
                value['next_actions'].append('登録献立の実施価格が未保存')
        if any(menu['status'] == 'draft' for menu in value['menus']):
            value['review_reasons'].append('下書き献立')
        if value['prices'] and not value['menus']:
            value['next_actions'].append('同じ学校・実施日の献立が未登録')
        if any(schedule['status'] == 'draft' for schedule in value['schedules']):
            value['review_reasons'].append('下書き工程表')
        if len(value['schedules']) > 1 or len(value['routes']) > 1:
            value['review_reasons'].append('同一対象の保存資料が複数あります')
        value['review_reasons'] = list(dict.fromkeys(value['review_reasons']))
    # Filters restrict stored facts only; no date is synthesized for unknown originals.
    return {'items': [value for value in items.values()
                      if (facility_id is None or value['facility_id'] == facility_id)
                      and (month is None or (value['service_date'] or '').startswith(month + '-'))]}
