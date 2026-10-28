"""Color/ROI line detection is evidence only, never material or direction inference."""
import hashlib
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
from sqlalchemy import select
from sqlalchemy.orm import object_session
from fastapi import HTTPException
from src.models.route_diagram import KitchenLayout, RouteDiagram, RouteSourceSession
from src.services import route_diagram_service as routes


def sha(content):
    return hashlib.sha256(content).hexdigest()


def artifact_source(row):
    if row.artifact_source_id is None:
        return row
    owner = object_session(row)
    source = owner.get(RouteSourceSession, row.artifact_source_id) if owner else None
    if (source is None or source.id == row.id or source.artifact_source_id is not None
            or row.source_pdf is not None or row.image is not None
            or (source.source_sha256, source.image_sha256, source.page)
            != (row.source_sha256, row.image_sha256, row.page)):
        raise HTTPException(409, "原本参照の整合性を確認できません")
    return source


def source_content(row):
    source = artifact_source(row)
    if (not isinstance(source.source_pdf, bytes) or not isinstance(source.image, bytes)
            or sha(source.source_pdf) != row.source_sha256 or sha(source.image) != row.image_sha256):
        raise HTTPException(409, "原本の整合性を確認できません")
    return source.source_pdf, source.image


def detect(image_bytes, settings):
    import cv2
    import numpy as np
    from PIL import Image

    image = np.array(Image.open(BytesIO(image_bytes)).convert("RGB"))
    height, width = image.shape[:2]
    x0, y0, x1, y1 = settings["roi"]
    left, top = round(x0*width), round(y0*height)
    crop = image[top:round(y1*height), left:round(x1*width)]
    if min(crop.shape[:2]) < 2:
        raise HTTPException(422, "読取範囲が小さすぎます")
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    rgb = np.uint8([[[int(settings["color"][i:i+2], 16) for i in (1, 3, 5)]]])
    hue = int(cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[0, 0, 0])
    distance = abs(hsv[:, :, 0].astype(int)-hue)
    distance = np.minimum(distance, 180-distance)
    mask = ((distance <= settings["hue_tolerance"]) & (hsv[:, :, 1] >= settings["min_saturation"]) & (hsv[:, :, 2] >= settings["min_value"])).astype(np.uint8)*255
    lines = cv2.HoughLinesP(mask, 1, np.pi/180, settings["threshold"], minLineLength=settings["min_length"], maxLineGap=settings["max_gap"])
    # Retain every detected segment, including colored frames. No automatic
    # frame removal, joining, material assignment, direction, or heat inference.
    found = [] if lines is None else sorted(tuple(map(int, line)) for line in lines.reshape(-1, 4))
    if len(found) > 1000:
        raise HTTPException(422, "候補が1000本を超えています。範囲・色・最短線長を絞ってください")
    return [{"id": i+1, "points": [[(a+left)/width, (b+top)/height], [(c+left)/width, (d+top)/height]]} for i, (a,b,c,d) in enumerate(found)]


def current(session, row):
    diagram = session.query(RouteDiagram).filter_by(id=row.diagram_id).with_for_update().one_or_none()
    layout = session.get(KitchenLayout, row.layout_id)
    if not diagram or not layout or diagram.version != row.diagram_version or diagram.layout_id != row.layout_id or diagram.layout_version != row.layout_version or layout.version != row.layout_version or routes.versions(routes.menus(session, diagram.facility_id, diagram.service_date)) != row.source_versions:
        raise HTTPException(409, "対象の動線・献立・厨房図面が変更されています。原本から新しい確認を開始してください")
    source_content(row)
    return diagram


def confirm(session, row, body):
    diagram = current(session, row)
    if row.status != "extracted" or row.version != body.version or row.source_sha256 != body.source_sha256 or row.image_sha256 != body.image_sha256:
        raise HTTPException(409, "候補または原本が一致しません。再読込してください")
    if not body.reviewed or not body.alignment_reviewed:
        raise HTTPException(422, "原本・対象材料・方向・位置合わせを明示的に確認してください")
    target = next((r for r in diagram.routes if r["id"] == body.material_id), None)
    if target is None or body.material_id not in {m.id for menu in routes.menus(session, diagram.facility_id, diagram.service_date) for m in menu.menu_ingredients}:
        raise HTTPException(422, "対象日に存在する材料行を明示してください")
    if target.get("source_provenance"):
        raise HTTPException(409, "この材料には確定済み原本経路があります。候補の再読取では上書きできません")
    candidates = {c["id"]: c for c in row.candidates}
    ids = [s.id for s in body.selection]
    if not ids or len(set(ids)) != len(ids) or any(i not in candidates for i in ids):
        raise HTTPException(422, "重複しない候補を原本から選択してください")
    source_points = []
    connector_indices = []
    for item in body.selection:
        a, b = candidates[item.id]["points"][::(-1 if item.reverse else 1)]
        if source_points and source_points[-1] != a:
            connector_indices.append(len(source_points)-1)
            source_points.append(a)
        elif not source_points:
            source_points.append(a)
        source_points.append(b)
    if connector_indices and not body.connectors_reviewed:
        raise HTTPException(422, "離れた候補間の接続線もプレビューで確認してください")
    if len(source_points) > 100 or len(body.styles) != len(source_points)-1:
        raise HTTPException(422, "点数は100以下、線種はプレビューの線分数と一致させてください")
    sx0, sy0, sx1, sy1 = row.settings["roi"]
    dx0, dy0, dx1, dy1 = body.destination_roi
    mapped = [[dx0+(x-sx0)/(sx1-sx0)*(dx1-dx0), dy0+(y-sy0)/(sy1-sy0)*(dy1-dy0)] for x,y in source_points]
    if any(not all(0 <= v <= 1 for v in p) for p in mapped):
        raise HTTPException(422, "位置合わせ後の経路が図面の外です")
    confirmation = body.model_dump()
    confirmation.update(points=mapped, connector_indices=connector_indices)
    row.confirmation = confirmation
    row.status = "confirmed"
    row.version += 1
    updated = {**target, "points": mapped, "styles": body.styles, "color": body.color,
               "source_provenance": {"session_id": row.id, "candidate_ids": ids, "source_sha256": row.source_sha256, "image_sha256": row.image_sha256, "page": row.page}}
    updated.pop("segment_states", None)  # New geometry has explicitly chosen styles, not old heat assumptions.
    confirmed_routes = [updated if r["id"] == body.material_id else r for r in diagram.routes]
    if updated.get("_label_modes", {}).get("label_position") == "automatic" and updated.get("label_position") is None:
        # Confirmation can supply the first known geometry. Complete only the
        # automatic placement that was blocked before those points existed.
        routes.assign_map_labels(confirmed_routes, session.get(KitchenLayout, diagram.layout_id), positions_only=True,
                                 preserve_positions={r["id"] for r in confirmed_routes if r["id"] != body.material_id})
    diagram.routes = confirmed_routes
    diagram.version += 1
    session.flush()
    return diagram


def source_views(session, diagrams):
    ids = sorted({route['source_provenance'].get('session_id') for diagram in diagrams
                  for route in diagram.routes if route.get('source_provenance')
                  and isinstance(route['source_provenance'].get('session_id'), int)})
    views = {}
    fields = ('id', 'status', 'diagram_id', 'layout_id', 'layout_version', 'source_versions',
              'confirmation', 'page', 'source_sha256', 'image_sha256')
    source = RouteSourceSession
    for offset in range(0, len(ids), 200):
        result = session.execute(select(
            *(getattr(source, name) for name in fields), source.artifact_source_id,
            source.source_pdf.is_not(None).label('has_pdf'), source.image.is_not(None).label('has_image'),
        ).where(source.id.in_(ids[offset:offset + 200])))
        try:
            for row in result:
                values = {name: getattr(row, name) for name in fields}
                values.update(artifact_source_id=row.artifact_source_id, has_pdf=row.has_pdf, has_image=row.has_image)
                views[row.id] = SimpleNamespace(**values)
        finally:
            result.close()
    root_ids = list({row.artifact_source_id if row.artifact_source_id is not None else row.id for row in views.values()})
    roots = {}
    for offset in range(0, len(root_ids), 200):
        result = session.execute(select(source.id, source.artifact_source_id, source.source_sha256,
            source.image_sha256, source.page, source.source_pdf, source.image).where(
            source.id.in_(root_ids[offset:offset + 200])).execution_options(yield_per=1))
        try:
            for row in result:
                valid = (row.artifact_source_id is None and isinstance(row.source_pdf, bytes) and isinstance(row.image, bytes)
                    and sha(row.source_pdf) == row.source_sha256 and sha(row.image) == row.image_sha256)
                roots[row.id] = (row.source_sha256, row.image_sha256, row.page) if valid else None
        finally:
            result.close()
    for row in views.values():
        direct = row.artifact_source_id is None
        root_id = row.id if direct else row.artifact_source_id
        row._bytes_valid = ((direct or (root_id != row.id and not row.has_pdf and not row.has_image))
            and roots.get(root_id) == (row.source_sha256, row.image_sha256, row.page))
    return views


def provenance_issue(session, diagram, route, *, sources=None):
    proof = route.get("source_provenance")
    if not proof:
        return None
    row = session.get(RouteSourceSession, proof.get("session_id")) if sources is None else sources.get(proof.get("session_id"))
    bytes_valid = getattr(row, '_bytes_valid', False) if sources is not None else False
    if sources is None and row is not None:
        try:
            source_content(row)
            bytes_valid = True
        except HTTPException:
            bytes_valid = False
    if not row or row.status != "confirmed" or row.diagram_id != diagram.id or row.layout_id != diagram.layout_id or row.layout_version != diagram.layout_version or any(diagram.source_versions.get(key) != value for key,value in row.source_versions.items()) or not row.confirmation or row.confirmation.get("material_id") != route["id"] or proof.get("candidate_ids") != [s["id"] for s in row.confirmation["selection"]] or proof.get("page") != row.page or proof.get("source_sha256") != row.source_sha256 or proof.get("image_sha256") != row.image_sha256 or not bytes_valid:
        return "原本候補の確定・材料との対応が確認できません"
    return None


def modified(session, route, *, sources=None):
    proof = route.get("source_provenance")
    row = ((session.get(RouteSourceSession, proof.get("session_id")) if sources is None
            else sources.get(proof.get("session_id"))) if proof else None)
    if not row or not row.confirmation:
        return None
    return any(route.get(key) != row.confirmation.get(key) for key in ("points", "styles", "color")) or bool(route.get("segment_states"))


def derived_session(session, original, source_route, target_diagram, target_route):
    issue = provenance_issue(session, original, source_route)
    if issue:
        raise HTTPException(409, f"原本経路を再利用できません: {issue}")
    source = session.get(RouteSourceSession, source_route["source_provenance"]["session_id"])
    original_artifact = artifact_source(source)
    confirmation = deepcopy(source.confirmation)
    confirmation.update(material_id=target_route["id"], derived_from={"session_id":source.id,
        "diagram_id":original.id,"material_id":source_route["id"],"service_date":str(original.service_date)})
    row = RouteSourceSession(diagram_id=target_diagram.id, diagram_version=target_diagram.version,
        layout_id=target_diagram.layout_id,layout_version=target_diagram.layout_version,source_versions=deepcopy(target_diagram.source_versions),
        filename=source.filename,artifact_source_id=original_artifact.id,source_sha256=source.source_sha256,page=source.page,
        image_sha256=source.image_sha256,version=1,status="confirmed",settings=deepcopy(source.settings),
        candidates=deepcopy(source.candidates),confirmation=confirmation)
    session.add(row);session.flush()
    return {**deepcopy(source_route["source_provenance"]),"session_id":row.id}
