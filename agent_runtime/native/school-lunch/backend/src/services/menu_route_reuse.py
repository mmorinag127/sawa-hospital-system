"""Reuse unchanged ingredient paths in the existing school/day diagram aggregate."""
from copy import deepcopy
from datetime import time
from decimal import Decimal

from src.models.route_diagram import KitchenLayout, RouteDiagram
from src.services import route_diagram_service as diagrams
from src.services.route_source_service import provenance_issue, derived_session
from fastapi import HTTPException


def signature(value, key=""):
    if isinstance(value, dict):
        return tuple(sorted((name, signature(item, name)) for name, item in value.items() if name != "id"))
    if isinstance(value, list):
        return tuple(signature(item) for item in value)
    if key in ("required_quantity", "conversion_rate") and value is not None:
        return Decimal(str(value))
    if key == "scheduled_time" and isinstance(value, str):
        return time.fromisoformat(value)
    return value


def clone_unchanged_routes(session, source, target):
    original = session.query(RouteDiagram).filter_by(facility_id=source["facility"]["id"], service_date=source["service_date"]).with_for_update().one_or_none()
    if original is None:
        return
    # The target menu was inserted in this transaction. Exclude only that new id
    # when checking the saved source snapshot, including same-day variants.
    prior_menus = [m for m in diagrams.menus(session, original.facility_id, original.service_date) if m.id != target["id"]]
    layout = session.get(KitchenLayout, original.layout_id)
    if original.source_versions != diagrams.versions(prior_menus) or layout.version != original.layout_version:
        return
    pairs = [(r["id"], signature(r)) for r in source["ingredients"]]
    route_ids = {route["id"] for route in original.routes}
    mapping = {}
    for row in target["ingredients"]:
        matches = [old_id for old_id, value in pairs if value == signature(row)]
        if (len(matches) > 1 and route_ids.intersection(matches)) or (
            len(matches) == 1 and matches[0] in mapping and matches[0] in route_ids
        ):
            raise HTTPException(409, "同じ内容の食材行が複数あり、再利用する動線の対応行を一意に確認できません。原本経路の対応を確認してください。")
        if len(matches) == 1:
            mapping[matches[0]] = row["id"]
    copied = {mapping[r["id"]]: {**deepcopy(r), "id": mapping[r["id"]]} for r in original.routes if r["id"] in mapping}
    originals = {mapping[r["id"]]: deepcopy(r) for r in original.routes if r["id"] in mapping}
    for route in originals.values():
        issue = provenance_issue(session, original, route)
        if issue:
            raise HTTPException(409, f"原本経路を再利用できません: {issue}")
    if not copied:
        return
    for route in copied.values():
        if any(route.get("_label_modes", {}).get(key) not in ("automatic", "manual") for key in ("label", "map_label", "label_position")):
            raise HTTPException(409, "動線ラベルの自動生成・手入力の来歴が未確認です。元の動線図で各ラベルの再利用方法（自動・手入力）を選択して保存してください。経路を再生成する必要はありません。")
    target_diagram = session.query(RouteDiagram).filter_by(facility_id=target["facility"]["id"], service_date=target["service_date"]).with_for_update().one_or_none()
    existing_routes = None
    if target_diagram is not None:
        target_layout = session.get(KitchenLayout, target_diagram.layout_id)
        prior_target = [m for m in diagrams.menus(session, target_diagram.facility_id, target_diagram.service_date) if m.id != target["id"]]
        if target_diagram.source_versions != diagrams.versions(prior_target) or target_layout.version != target_diagram.layout_version:
            return
        # Coordinates are portable only when the operator's actual image and
        # named locations match. Never replace a different target kitchen.
        if target_layout.image != layout.image or target_layout.locations != layout.locations:
            return
        existing_routes = deepcopy(target_diagram.routes)
        layout = target_layout
    elif layout.facility_id != target["facility"]["id"]:
        layout = KitchenLayout(facility_id=target["facility"]["id"], name=layout.name, image=layout.image, locations=deepcopy(layout.locations), version=1)
        session.add(layout)
        session.flush()
    # This internal regeneration restores every existing route below. It is not
    # the operator action that discards confirmed source paths.
    draft = diagrams.generate(session, target["facility"]["id"], target["service_date"], layout.id, target_diagram.version if target_diagram else 0, replace_confirmed_source=True)
    for target_id, route in copied.items():
        if route.get("source_provenance"):
            route["source_provenance"] = derived_session(session, original, originals[target_id], draft, route)
    old_by_id = {r["id"]: r for r in existing_routes or []}
    merged = []
    for generated in draft.routes:
        route = copied.get(generated["id"], old_by_id.get(generated["id"], generated))
        if generated["id"] in copied:
            for key in ("label", "map_label"):
                if route["_label_modes"][key] == "automatic":
                    if key in generated:
                        route[key] = generated[key]
                    else:
                        route.pop(key, None)
        merged.append(route)
    issues = diagrams.label_issues(merged)
    if issues:
        raise HTTPException(409, "再利用先で短縮ラベルが重複します。手入力ラベルは自動変更しません。元または再利用先の動線図でラベルを変更してください。")
    # Reposition only newly copied automatic labels, using the preserved path
    # geometry and all existing/manual label boxes as occupied space.
    preserve = {r["id"] for r in merged if r["id"] not in copied or r["_label_modes"]["label_position"] == "manual"}
    diagrams.assign_map_labels(merged, layout, positions_only=True, preserve_positions=preserve)
    draft.routes = merged
    session.flush()
