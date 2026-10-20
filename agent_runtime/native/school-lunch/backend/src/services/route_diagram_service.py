from fastapi import HTTPException
from sqlalchemy import select, tuple_
from sqlalchemy.orm import load_only
from sqlalchemy.exc import IntegrityError
from io import BytesIO
from PIL import Image
from src.models.school_lunch import MenuExecution
from src.models.route_diagram import KitchenLayout, RouteDiagram

COLORS = {"main": "#2563eb", "side_green": "#16a34a", "side_orange": "#ea580c", "dessert_salad": "#db2777", "soup": "#9333ea"}


def public_route(route):
    return {**{key: value for key, value in route.items() if key != "_label_modes"}, "label_modes": route.get("_label_modes", {})}


def saved_route_labels(route, previous):
    # Unknown historical values require an explicit choice, not a name guess.
    modes = previous.get("_label_modes", {})
    choices = route.pop("label_modes", {}) or {}
    route["_label_modes"] = {
        key: choices[key] if choices.get(key) is not None and choices[key] != modes.get(key)
        else modes.get(key) if route.get(key) == previous.get(key) else "manual"
        for key in ("label", "map_label", "label_position")
    }
    return route


def apply_automatic_label_choices(session, diagram, routes, previous):
    names = {material.id: f"{menu.name} / {material.ingredient.name}"
             for menu in menus(session, diagram.facility_id, diagram.service_date) for material in menu.menu_ingredients}
    numbers = {material_id: str(index) for index, material_id in enumerate(names, 1)}
    reposition = set()
    for route in routes:
        before = previous[route["id"]].get("_label_modes", {})
        for key, mode in route["_label_modes"].items():
            if mode != "automatic":
                continue
            if key == "label":
                route[key] = names[route["id"]]
            elif key == "map_label":
                # Automatic identity is independent of known route geometry.
                # Re-saving also repairs records created by older generators.
                route[key] = numbers[route["id"]]
            elif before.get(key) != "automatic" or route.get(key) is None:
                reposition.add(route["id"])
    if reposition:
        assign_map_labels(routes, session.get(KitchenLayout, diagram.layout_id), positions_only=True,
                          preserve_positions={r["id"] for r in routes} - reposition)


def label_issues(routes):
    issues = []
    if any(route.get("_label_modes", {}).get("map_label") == "automatic" and not route.get("map_label") for route in routes):
        issues.append("自動の図中番号が未設定です。動線の修正を保存して番号を更新してください")
    labels = [str(route["map_label"]).strip() for route in routes if route.get("map_label") is not None]
    if any(not label for label in labels) or len(labels) != len(set(labels)):
        issues.append("図中の短縮ラベルが空白または重複しています。動線図で一意のラベルを保存してください")
    return issues


def points(route, layout):
    return route["points"] if route.get("points") is not None else [layout.locations.get(name) for name in route["locations"]]


def appearance(route, index):
    states = route.get("segment_states")
    state = states[index] if states is not None and index < len(states) else {}
    color = "#dc2626" if state.get("raw_meat") and not state.get("heated") else COLORS.get(state.get("food_category"), route["color"])
    style = ("dashed" if state["heated"] else "solid") if state.get("heated") is not None else (route["styles"][index] if index < len(route["styles"]) else None)
    return {"color": color, "line_style": style}


def menus(session, facility_id, day):
    return session.query(MenuExecution).filter_by(facility_id=facility_id, service_date=day).order_by(MenuExecution.id).all()


def versions(rows):
    return {str(row.id): row.version for row in rows}


def assign_map_labels(routes, layout, *, positions_only=False, preserve_positions=()):
    """Number generated routes and place keys without changing their geometry.

    Generation assigns identity even before geometry is known. Confirmation,
    reuse and explicit saves may complete automatic placement; reads/exports
    never modify labels, and callers preserve operator-controlled positions.
    """
    with Image.open(BytesIO(layout.image)) as image:
        scale = min(900 / image.width, 560 / image.height)
        width, height = image.width * scale, image.height * scale
    occupied = []
    for route in routes:
        if route.get("id") in preserve_positions and route.get("label_position") is not None:
            x, y = route["label_position"]
            occupied.append((x, y, x + (12 + len(route.get("map_label") or route["label"]) * 12) / width, y + 20 / height))
    for index, route in enumerate(routes, 1):
        if not positions_only:
            route["map_label"] = str(index)
        if route.get("id") in preserve_positions:
            continue
        route_points = points(route, layout)
        if not route_points or any(point is None for point in route_points):
            continue  # Existing missing-location/operation blockers remain authoritative.
        route.pop("label_position", None)
        label_width, label_height = (12 + len(route.get("map_label") or route["label"]) * 12) / width, 20 / height
        candidates = []
        # Shared kitchen nodes are crowded. Segment positions are also part of
        # the registered route, so labels need not compete only at endpoints.
        anchors = [tuple(point) for point in route_points]
        anchors += [(a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio)
                    for a, b in zip(route_points, route_points[1:]) for ratio in (1/3, 2/3)]
        for x, y in dict.fromkeys(anchors):
            for dx, dy in [(.008,-.035),(-.035,-.035),(.008,.012),(-.035,.012),(.03,-.065),(-.06,.035)]:
                left, top = round(x + dx, 3), round(y + dy, 3)
                if .01 <= left and left + label_width <= .99 and .01 <= top and top + label_height <= .99:
                    candidates.append((left, top, left + label_width, top + label_height))
        box = next((b for b in candidates if all(b[2]+.004 <= a[0] or a[2]+.004 <= b[0] or b[3]+.004 <= a[1] or a[3]+.004 <= b[1] for a in occupied)), None)
        if box is not None:
            route["label_position"] = list(box[:2])
            occupied.append(box)
        # No arbitrary location is substituted. blockers() stops export until
        # the operator explicitly supplies a position for this numbered route.


def blockers_for_diagrams(session, diagrams):
    diagrams = list(diagrams)
    if not diagrams:
        return {}
    layout_ids = list(dict.fromkeys(row.layout_id for row in diagrams))
    layouts = {}
    for start in range(0, len(layout_ids), 500):
        layouts.update((row.id, row) for row in session.scalars(
            select(KitchenLayout).options(load_only(
                KitchenLayout.id, KitchenLayout.version, KitchenLayout.locations,
            )).where(KitchenLayout.id.in_(layout_ids[start:start + 500]))))
    scopes = list(dict.fromkeys((row.facility_id, row.service_date) for row in diagrams))
    from src.services.agent_daily_process import answer_revisions_for_scopes
    answer_revisions = answer_revisions_for_scopes(session, scopes)
    menu_versions = {scope: {} for scope in scopes}
    for start in range(0, len(scopes), 500):
        for row in session.execute(select(
            MenuExecution.id, MenuExecution.version, MenuExecution.facility_id, MenuExecution.service_date,
        ).where(tuple_(MenuExecution.facility_id, MenuExecution.service_date).in_(
            scopes[start:start + 500])).order_by(MenuExecution.id)):
            menu_versions[(row.facility_id, row.service_date)][str(row.id)] = row.version
    return {row.id: blockers(
        session, row, layouts.get(row.layout_id),
        menu_source_versions=menu_versions[(row.facility_id, row.service_date)],
        answer_revisions=answer_revisions[(row.facility_id, row.service_date)],
    ) for row in diagrams}


def blockers(session, diagram, layout=None, *, menu_source_versions=None, answer_revisions=None):
    if layout is None:
        layout = session.get(KitchenLayout, diagram.layout_id)
    from src.services.agent_daily_process import route_revision_issues
    issues = label_issues(diagram.routes) + route_revision_issues(session, diagram, answer_revisions)
    if not diagram.routes:
        issues.append("材料の動線がありません。献立の材料を確認してください")
    if menu_source_versions is None:
        menu_source_versions = versions(menus(session, diagram.facility_id, diagram.service_date))
    if menu_source_versions != diagram.source_versions:
        issues.append("献立が変更されています。下書きを再生成してください")
    if layout.version != diagram.layout_version:
        issues.append("厨房図面が変更されています。下書きを再生成してください")
    for route in diagram.routes:
        from src.services.route_source_service import provenance_issue
        issue = provenance_issue(session, diagram, route)
        if issue:
            issues.append(f'{route["label"]}: {issue}')
        if not route["locations"] and not route.get("points"):
            issues.append(f'{route["label"]}: 工程が未登録です')
        for name in ([] if route.get("points") is not None else route["locations"]):
            if name not in layout.locations:
                issues.append(f'{route["label"]}: 場所「{name}」の座標が未登録です')
        route_points = points(route, layout)
        if route.get("map_label") and route.get("label_position") is None and route_points and all(p is not None for p in route_points):
            issues.append(f'{route["label"]}: 図中番号を配置できません。ラベル位置を指定してください')
        unresolved = any(appearance(route, i)["line_style"] not in ("solid", "dashed") for i in range(max(0, len(route_points)-1)))
        if route.get("points") is not None:
            unresolved = unresolved or len(route["styles"]) != len(route_points)-1
        if route.get("points") is None and route.get("segment_states") is None:
            unresolved = len(route["styles"]) != len(route["locations"]) or any(style not in ("solid", "dashed") for style in route["styles"])
        if unresolved:
            issues.append(f'{route["label"]}: 線種が未確定です。実線・破線を確認してください')
    return list(dict.fromkeys(issues))


def generate(session, facility_id, day, layout_id, expected_version, replace_confirmed_source=False):
    layout = session.get(KitchenLayout, layout_id)
    if not layout or layout.facility_id != facility_id:
        raise HTTPException(422, "対象校の厨房図面を指定してください")
    rows = menus(session, facility_id, day)
    if not rows:
        raise HTTPException(422, "対象日の献立がありません")
    diagram = session.query(RouteDiagram).filter_by(facility_id=facility_id, service_date=day).with_for_update().one_or_none()
    if (diagram.version if diagram else 0) != expected_version:
        raise HTTPException(409, "別の変更があります。再読み込みしてください")
    if diagram and any(r.get("source_provenance") for r in diagram.routes) and not replace_confirmed_source:
        raise HTTPException(409, "原本から確認した経路を含みます。破棄して再生成することを明示的に確認してください")
    from src.services.process_work_schedule import saved_patterns
    from src.services.agent_daily_process import has_daily_answers, material_routes
    processes = saved_patterns(session, facility_id, day)
    routes = []
    for menu in rows:
        process = processes.get(str(menu.id))
        if process and has_daily_answers(process[1].document):
            try: routes.extend(material_routes(menu, process[1], layout))
            except ValueError as exc: raise HTTPException(422, str(exc)) from exc
            continue
        for ingredient in menu.menu_ingredients:
            routes.append({"id": ingredient.id, "label": f"{menu.name} / {ingredient.ingredient.name}", "color": ingredient.route_color, "locations": [s.location for s in ingredient.process_steps], "styles": [s.route_line_style for s in ingredient.process_steps]})
    assign_map_labels(routes, layout)
    for route in routes:
        route["_label_modes"] = {"label": "automatic", "map_label": "automatic", "label_position": "automatic"}
    if diagram is None:
        diagram = RouteDiagram(facility_id=facility_id, service_date=day, version=0)
        session.add(diagram)
    diagram.version += 1
    diagram.layout_id = layout.id
    diagram.layout_version = layout.version
    diagram.source_versions = versions(rows)
    diagram.routes = routes
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "別の変更があります。再読み込みしてください") from exc
    return diagram


def export(session, diagrams, layouts=None):
    from src.services.route_export_snapshot import begin_export_read, freeze_row
    begin_export_read(session)
    if not diagrams:
        raise HTTPException(422, "出力する動線図がありません")
    diagrams = [freeze_row(diagram) for diagram in diagrams]
    if layouts is None:
        layouts = {row.id: freeze_row(row) for row in session.query(KitchenLayout).filter(KitchenLayout.id.in_({diagram.layout_id for diagram in diagrams})).populate_existing().all()}
    for diagram in diagrams:
        layout = layouts.get(diagram.layout_id)
        if layout is None:
            raise HTTPException(409, "動線図の図面がありません")
        issues = blockers(session, diagram, layout)
        if issues:
            raise HTTPException(409, {"date": str(diagram.service_date), "blockers": issues})
    from src.services.route_diagram_export import export_editable
    return export_editable(session, diagrams, layouts=layouts)
