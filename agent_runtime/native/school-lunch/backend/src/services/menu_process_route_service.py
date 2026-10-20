"""Render process graphs on the selected canonical SVG.

Legacy preview equipment candidates remain unconfirmed metadata. Pattern-based
graphs require selected equipment; only missing drawing angles are calculated.
Neither rendering path changes the saved graph.
"""

from __future__ import annotations

import json
from copy import deepcopy
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from time import monotonic
from datetime import date
from src.schemas.menu_process import material_flow

from src.services.named_svg_route_service import validate_pair, _equipment_bounds, resolve_line_styles, hide_standard_routes
from src.services.route_diagram_service import COLORS
from src.services.menu_process_preview_geometry import preview_manifest, usable_port
from src.services.process_graph_route_layout import layout_events, render as render_graph, transport_groups
from src.services.process_material_journey import expand_supplies, supply_options, supply_area_bounds
from src.services.process_dispatch_projection import expand_dispatch
from src.services.kitchen_wall_geometry import routing_network
from src.services.process_receipt_legend import project_receipts, paint_receipts
from src.services.process_heat_history import project_heat_styles, heat_history

NS = "http://www.w3.org/2000/svg"
DAY_ROUTE_COLORS = ('#111111', '#2563eb', '#ea580c', '#16a34a', '#db2777', '#9333ea', '#0891b2')


def require_pattern_equipment(document):
    if not (document.get('pattern_applications') or document.get('shared_menu_source')):
        return
    missing = [step.get('operation') or step['id'] for step in document['steps']
               if not step['equipment'].get('id')]
    if missing:
        raise ValueError('実際に使用した設備を選択してください: ' + '、'.join(missing))
    config = document.get('route_config') or {}
    template_id = config.get('template_id')
    inventory = {item['id']: item for template in options()['templates']
                 if template['template_id'] == template_id for item in template['equipment']}
    if not inventory:
        raise ValueError('実際に使用した設備を検証するには厨房図面を選択してください')
    invalid = [step.get('operation') or step['id'] for step in document['steps']
               if (not inventory.get(step['equipment']['id'])
                   or inventory[step['equipment']['id']]['type'] != step['equipment'].get('type'))]
    if invalid:
        raise ValueError('選択した厨房図面と一致しない設備があります: ' + '、'.join(invalid))


def paint_menu_heading(layer, menu, manifest):
    """Only live menu metadata; no example school/date from the SVG."""
    service_date = getattr(menu, 'service_date', None)
    if service_date is not None:
        day = service_date if isinstance(service_date, date) else date.fromisoformat(str(service_date))
        values = {'month': str(day.month), 'day': str(day.day), 'weekday': '月火水木金土日'[day.weekday()]}
        fields = {f['id']: f for f in manifest['fields']}
        for key, value in values.items():
            field = fields[key]
            text = element(layer, 'text', x=field['x'], y=field['y']+field['font_size'],
                           font_size=field['font_size'], data_rendered_field_id=key)
            text.text = value
    facility = getattr(menu, 'facility', None)
    name = facility.get('name') if isinstance(facility, dict) else getattr(facility, 'name', None)
    if name:
        # Preserve the complete registered name, not an invented abbreviation.
        # Cover the template's prefecture/suffix so other names are not doubled.
        element(layer, 'rect', x=45, y=15, width=380, height=48, fill='white')
        count = len(name)
        size = min(22, 360/max(1, count))
        text = element(layer, 'text', x=55, y=50, font_size=size,
                       data_rendered_field_id='school_name')
        text.text = name


def element(parent, name, **attrs):
    # The kitchen template styles all text. Presentation attributes alone
    # lose to that stylesheet, making menu legends black and sizes incorrect.
    if name == "text":
        styles = [attrs.get("style", "").rstrip(";")]
        for key in ("fill", "font_size", "font_weight"):
            if key in attrs:
                value = attrs[key]
                if key == "font_size" and isinstance(value, (int, float)):
                    value = f"{value}px"
                styles.append(f"{key.replace('_', '-')}:{value}")
        attrs["style"] = ";".join(style for style in styles if style)
    return ET.SubElement(
        parent,
        "{" + NS + "}" + name,
        {key.replace("_", "-"): str(value) for key, value in attrs.items()},
    )


_ASSETS = Path(__file__).resolve().parents[1] / "data" / "kitchen_svg"
_TEMPLATES = {
    "kitchen-rice": "kitchen-base-rice",
    "kitchen-bread": "kitchen-base-bread",
}
RENDERER_VERSION = 'menu-process-route-v2:' + hashlib.sha256(b''.join(
    Path(__file__).with_name(name).read_bytes() for name in (
        'menu_process_route_service.py', 'process_route_network_service.py',
        'process_route_layout_service.py', 'named_svg_route_service.py', 'route_diagram_service.py',
        'menu_process_preview_geometry.py', 'route_lane_placement.py',
        'process_graph_route_layout.py', 'process_route_center_flow_service.py', 'process_material_journey.py', 'material_drawing_projection.py', 'kitchen_wall_geometry.py', 'process_receipt_legend.py', 'process_heat_history.py'
    )
) + (Path(__file__).resolve().parents[1] / 'schemas' / 'menu_process.py').read_bytes()
  + b''.join((_ASSETS/name).read_bytes() for name in (
      'equipment-observations.json','supply-observations.json','kitchen-flow-network.json',
      'kitchen-base-rice.json','kitchen-base-rice.svg',
      'kitchen-base-bread.json','kitchen-base-bread.svg'))).hexdigest()


def load_template(template_id: str):
    stem = _TEMPLATES.get(template_id)
    if not stem:
        raise ValueError("未登録の正本厨房SVGテンプレートです")
    svg = (_ASSETS / f"{stem}.svg").read_bytes()
    manifest = json.loads((_ASSETS / f"{stem}.json").read_text())
    # This is the canonical facility network (walls, corridors and gates), not
    # a transcribed reference-day route.
    manifest["flow_network"] = json.loads(
        (_ASSETS / "kitchen-flow-network.json").read_text()
    )
    manifest['equipment_observations'] = json.loads(
        (_ASSETS / 'equipment-observations.json').read_text()
    )
    manifest['supply_observations'] = json.loads(
        (_ASSETS / 'supply-observations.json').read_text()
    )
    validate_pair(svg, manifest)
    return svg, manifest


def options():
    """Expose only actual equipment and exact ports from canonical manifests."""
    result = []
    for template_id in _TEMPLATES:
        _, manifest = load_template(template_id)
        groups = {}
        for point in manifest["points"]:
            if point.get("kind") != "equipment_port":
                continue
            groups.setdefault(point["equipment_id"], []).append(
                {
                    "id": point["id"],
                    "name": point["name"],
                    "side": point["side"],
                }
            )
        result.append(
            {
                "template_id": template_id,
                "template_version": manifest["version"],
                "material_sources": supply_options(manifest),
                "equipment": [
                    {
                        "id": equipment_id,
                        "name": ports[0]["name"].rsplit("・", 1)[0],
                        "type": equipment_type(equipment_id, ports[0]["name"]),
                        "ports": ports,
                    }
                    for equipment_id, ports in sorted(groups.items())
                ],
            }
        )
    return {"templates": result, "colors": list(COLORS.values()), "day_colors": list(DAY_ROUTE_COLORS)}


def equipment_type(equipment_id, name):
    """Names describe the master equipment, not a guess about a menu's use."""
    for text, kind in (("炊飯器", "炊飯器"), ("スチコン", "スチコン"),
                       ("ゼットオーブン", "ゼットオーブン"), ("フライヤー", "フライヤー"),
                       ("コンロ", "コンロ"),
                       ("ミキサー", "ミキサー"), ("スライサー", "スライサー"),
                       ("シンク", "シンク"), ("作業台", "作業台"),
                       ("冷却", "冷却器"), ("脱水機", "脱水機"), ("冷蔵庫", "冷蔵庫")):
        if text in name and "無記名" not in name:
            return kind
    if equipment_id.startswith("kettle-"):
        return "回転釜"
    if equipment_id == "rice-transfer-table":
        return "作業台"
    return None


def preview(document, menu):
    return _event_result(preview_events(document, menu))


def _event_result(events):
    for event in events:
        if event["type"] == "result":
            return event["result"]
    raise RuntimeError("動線生成が結果を返さず終了しました")


def preview_events(document, menu):
    """No database writes; render the submitted draft, not a stored revision."""
    config = document.get("route_config")
    if not config or not config.get("template_id"):
        raise ValueError("厨房図面を選択してください（カード下の「動線図の設定」）。工程の確認済み保存は不要です。")
    svg, manifest = load_template(config["template_id"])
    manifest = preview_manifest(svg, manifest)
    if document.get('pattern_applications') or document.get('shared_menu_source'):
        require_pattern_equipment(document)
        drawing, assignments = deepcopy(document), []
    else:
        drawing, assignments = bind_preview_equipment(document, manifest)
    drawing = bind_preview_ports(drawing, manifest)
    events = generate_events(SimpleNamespace(id=None, document=drawing), menu, {
        "routing_policy": "candidate_geometry", "svg_snapshot": svg.decode(),
        "manifest_snapshot": manifest, "network_snapshot": manifest["flow_network"],
    }, is_preview=True)
    for event in events:
        if event["type"] == "progress":
            yield event
            continue
        result = event["result"]
        yield {"type": "result", "result": {**result, "source": "current_graph", "renderer_version": RENDERER_VERSION,
            "svg_hash": hashlib.sha256(result["svg"].encode()).hexdigest(),
            "input_hash": hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "unreviewed_steps": sum(s.get("basis", {}).get("review") != "user-confirmed" for s in document["steps"]),
            "equipment_assignments": assignments,
            "warnings": [
                "表示中の工程から作成した候補図です。工程・動線図はDBに保存していません。",
                *([f"未設定の設備{len(assignments)}作業を図面上へ仮配置しました。使用場所は未確認です。"] if assignments else []),
                *result.get('warnings', []),
            ]}}


def bind_preview_equipment(document, manifest):
    """Bind missing draft equipment to canonical-SVG candidates for preview only.

    This deliberately does not write the submitted graph, select a facility, or
    turn a tentative placement into an operator-confirmed decision.
    """
    doc = deepcopy(document)
    groups = {}
    for point in manifest["points"]:
        if point.get("kind") == "equipment_port":
            groups.setdefault(point["equipment_id"], []).append(point)
    inventory = {
        equipment_id: {
            "name": points[0]["name"].rsplit("・", 1)[0],
            "type": equipment_type(equipment_id, points[0]["name"]),
            "center": (
                (min(p["x"] for p in points) + max(p["x"] for p in points)) / 2,
                (min(p["y"] for p in points) + max(p["y"] for p in points)) / 2,
            ),
        }
        for equipment_id, points in groups.items()
    }
    steps = {step["id"]: step for step in doc["steps"]}
    material_index = {m['id']:m for m in doc['ingredients']}
    thermal_paths = heat_history(doc, reset_on_cooling=True)
    def uncooled_input(ref):
        sid = ref.get('step_id')
        if sid not in steps:
            return False
        selected = thermal_paths[sid]
        if ref.get('output_id'):
            output = next(o for o in steps[sid]['outputs'] if o['id']==ref['output_id'])
            if output.get('ingredient_ids') is not None:
                selected = selected & set(output['ingredient_ids'])
        return bool(selected)
    assignments = []
    locations={}
    for step in doc['steps']:
        group=step.get('conditions',{}).get('location_group')
        eid=step.get('equipment',{}).get('id')
        if group and eid:
            if group in locations and locations[group]!=eid:
                raise ValueError('同一作業場所の明示設備が矛盾しています: '+group)
            locations[group]=eid
    for step in doc["steps"]:
        equipment = step.get("equipment") or {}
        equipment_id = equipment.get("id")
        if equipment_id:
            if equipment_id not in inventory:
                raise ValueError(f"工程 {step['id']} の明示された設備IDが図面にありません: {equipment_id}")
            continue
        kind = equipment.get("type")
        candidates = sorted(eid for eid, item in inventory.items() if kind and item["type"] == kind)
        if not candidates:
            raise ValueError(f"工程 {step['id']} の設備種別「{kind or '未設定'}」に対応する図面上の設備がありません")
        previous=set()
        for source in step.get('inputs',[]):
            if source.get('step_id') not in steps: continue
            upstream=steps[source['step_id']].get('equipment') or {}
            ids=([upstream['id']] if upstream.get('id') else
                 [eid for eid,item in inventory.items() if upstream.get('type') and item['type']==upstream['type']])
            if len(ids)==1: previous.add(ids[0])
        preferred={rule['to_equipment_id'] for rule in manifest['flow_network'].get('preferred_successors',[])
                   if rule['from_equipment_id'] in previous and rule['operation']==step.get('operation')
                   and rule['to_equipment_id'] in candidates}
        observations=[]
        for rule in manifest.get('equipment_observations',{}).get('rules',[]):
            if (rule['equipment_type']!=kind or rule['to_equipment_id'] not in candidates
                    or not any(token in step.get('operation','') for token in rule['operation_contains'])):
                continue
            if rule.get('direct_storage_id'):
                direct=[i.get('ingredient_id') for i in step.get('inputs',[])]
                if (not direct or not all(direct) or not all(
                    material_index[mid].get('supply',{}).get('storage_point_id')==rule['direct_storage_id']
                    for mid in direct)):
                    continue
            if rule.get('from_equipment_id') and previous!={rule['from_equipment_id']}:
                continue
            if rule.get('from_any_equipment_ids') and not previous.intersection(rule['from_any_equipment_ids']):
                continue
            observations.append(rule)
        observed_candidates={r['to_equipment_id'] for r in observations}
        if len(observed_candidates)>1:
            raise ValueError(f"工程 {step['id']} の施設観察ルールが競合しています")
        if len(preferred)>1:
            raise ValueError(f"工程 {step['id']} の施設標準の受渡し先が複数あります")
        location_group=step.get('conditions',{}).get('location_group')
        if location_group in locations:
            chosen=locations[location_group]
            if chosen not in candidates:raise ValueError('同一作業場所の設備種類が矛盾しています: '+location_group)
            reason='explicit_work_location_continuity'
        elif preferred:
            chosen, reason = next(iter(preferred)), "canonical_facility_successor"
        elif observed_candidates:
            chosen, reason = next(iter(observed_candidates)), 'unconfirmed_facility_observation'
        elif len(candidates) == 1:
            chosen, reason = candidates[0], "canonical_type_unique"
        else:
            # Use neighboring equipment, or their type-matched candidate sets
            # when still unassigned. Operation/name affinity avoids plainly
            # contradictory placements (for example prep at a serving table).
            neighbor_steps = [
                steps[source["step_id"]]
                for source in step.get("inputs", []) if source.get("step_id") in steps
            ] + [
                other for other in doc["steps"]
                if any(source.get("step_id") == step["id"] for source in other.get("inputs", []))
            ]
            neighbors=[]
            for neighbor in neighbor_steps:
                target=neighbor.get('equipment',{})
                if target.get('id') in inventory:
                    neighbors.append([inventory[target['id']]['center']])
                elif target.get('type'):
                    choices=[item['center'] for item in inventory.values() if item['type']==target['type']]
                    if choices: neighbors.append(choices)
            same_previous={steps[i['step_id']]['equipment'].get('id') for i in step.get('inputs',[]) if i.get('step_id') in steps and steps[i['step_id']]['equipment'].get('type')==kind}
            same_previous.discard(None)
            hot_input = any(uncooled_input(ref) for ref in step.get('inputs', []))

            def rank(eid):
                name = inventory[eid]["name"]
                operation = (step.get("operation") or "").replace('り','')
                mismatch = 0
                if (step.get('conditions',{}).get('work_phase')=='preparation' or any(token in operation for token in ("下処理", "洗浄", "切", "皮むき", "下味", "すおろす", "戻す", "戻し", "もどす", "もどし", "油抜き"))) and any(token in name for token in ('盛付','和え物')):
                    mismatch = 1
                affinity = 0 if any(token in operation and token in name for token in ("野菜", "魚", "肉", "和え", "盛付", "ご飯")) else 1
                distance = sum(min((inventory[eid]["center"][0]-x)**2+(inventory[eid]["center"][1]-y)**2 for x,y in choices) for choices in neighbors)
                continuity=0 if not same_previous or eid in same_previous else 1
                # Independent, unassigned kettle work must not all collapse
                # onto the nearest pot. This is tentative diagram allocation,
                # not a capacity/schedule claim; explicit IDs are never changed.
                load = (sum(other.get('equipment',{}).get('id')==eid for other in doc['steps'])
                        if kind=='回転釜' and not same_previous else 0)
                # A lexical "和える" match is not evidence for moving freshly
                # heated food into the cold salad workroom. This is an input
                # graph constraint, not a claim about actual food temperature.
                thermal_mismatch = int(hot_input and '和え物' in name)
                return thermal_mismatch, mismatch, affinity, continuity, load, distance, eid

            chosen, reason = min(candidates, key=rank), "canonical_type_graph_geometry"
        equipment["id"] = chosen
        if location_group:locations[location_group]=chosen
        step["equipment"] = equipment
        assignments.append({
            "step_id": step["id"], "operation": step.get("operation") or "作業未設定",
            "equipment_id": chosen, "equipment_name": inventory[chosen]["name"],
            "reason": reason, "candidate_count": len(candidates),
            **({'observation_ids':[r['id'] for r in observations],
                'observation_source':manifest['equipment_observations']['source']}
               if reason=='unconfirmed_facility_observation' else {}),
            **({"menu_name":step["_menu_name"]} if step.get("_menu_name") else {}),
        })
    return doc, assignments


def bind_preview_ports(document, manifest):
    """Choose drawing angles only; never invent which equipment a work uses."""
    doc = deepcopy(document)
    groups = {}
    for point in manifest["points"]:
        if point.get("kind") == "equipment_port":
            groups.setdefault(point["equipment_id"], []).append(point)
    centers = {eid: ((min(p["x"] for p in ps)+max(p["x"] for p in ps))/2,
                     (min(p["y"] for p in ps)+max(p["y"] for p in ps))/2) for eid,ps in groups.items()}
    steps = {s["id"]:s for s in doc["steps"]}
    ports = doc["route_config"].setdefault("step_ports", {})
    automatic = doc.setdefault("_automatic_step_ports", {})
    normals = {"西接続":(-1,0), "東接続":(1,0), "北接続":(0,-1), "南接続":(0,1)}
    network = manifest["flow_network"]
    for sid,step in steps.items():
        eid = step["equipment"].get("id")
        if eid not in groups:
            continue  # The renderer reports every unmapped work; no substitution.
        center = centers[eid]
        before = [steps[i["step_id"]]["equipment"].get("id") for i in step["inputs"] if i.get("step_id")]
        after = [s["equipment"].get("id") for s in steps.values() if any(i.get("step_id")==sid for i in s["inputs"])]
        binding = ports.setdefault(sid,{"ingress":"", "egress":""})
        for direction,neighbors in (("ingress",before),("egress",after)):
            if binding.get(direction):
                continue
            targets = [centers[n] for n in neighbors if n in centers and n!=eid]
            # Unconnected ends have no physical transfer. Their angle is purely
            # diagrammatic; connected ends face their explicitly chosen neighbor.
            dx,dy = ((sum(p[0] for p in targets)/len(targets)-center[0],sum(p[1] for p in targets)/len(targets)-center[1])
                     if targets else ((-1,0) if direction=="ingress" else (1,0)))
            candidates = [p for p in groups[eid] if p.get("side") in normals and usable_port(p, network, manifest.get('_preview_obstacles',()))]
            if candidates:
                binding[direction] = max(candidates,key=lambda p:(normals[p["side"]][0]*dx+normals[p["side"]][1]*dy,p["id"]))["id"]
                automatic.setdefault(sid, []).append(direction)
    return doc


def preview_day(entries, template_id):
    return _event_result(preview_day_events(entries, template_id))


def preview_day_events(entries, template_id):
    """Combine explicit graphs before routing, so all dishes share lane occupancy."""
    combined = {"schema_version": 2, "ingredients": [], "steps": [],
                "route_config": {"template_id": template_id, "step_ports": {}, "edge_styles": {}}}
    legends = []
    for menu_number, (menu, source, color) in enumerate(entries, 1):
        require_pattern_equipment(source)
        if not source["steps"]:
            raise ValueError(f"{menu.name}：工程グラフが未作成です。同じ画面の「工程を編集」で作成してください。")
        doc = deepcopy(source)
        config = doc.get("route_config") or {}
        if config.get("template_id") not in (None, "", template_id):
            raise ValueError(f"{menu.name}：工程の厨房図面と選択した図面が異なります。")
        prefix = f"menu{menu.id}-"
        for material in doc["ingredients"]:
            material["id"] = prefix + material["id"]
            drawing=material.get('drawing')
            if drawing:
                for field in ('independent_step_ids','shared_step_ids'):
                    drawing[field]=[prefix+sid for sid in drawing[field]]
                if drawing.get('merge_step_id'): drawing['merge_step_id']=prefix+drawing['merge_step_id']
                if drawing.get('group_id'): drawing['group_id']=prefix+drawing['group_id']
        combined["ingredients"].extend(doc["ingredients"])
        legends.append({"name": menu.name, "color": color, "ingredients": doc["ingredients"]})
        for step in doc["steps"]:
            old_id = step["id"]
            step["id"] = prefix + old_id
            step["_route_color"] = color
            step["_menu_name"] = menu.name
            step["_menu_number"] = menu_number
            if step.get('conditions',{}).get('location_group'):
                step['conditions']['location_group']=prefix+step['conditions']['location_group']
            for source_input in step["inputs"]:
                for key in ("step_id", "ingredient_id", "edge_id", "output_id"):
                    if source_input.get(key):
                        source_input[key] = prefix + source_input[key]
            for output in step["outputs"]:
                output["id"] = prefix + output["id"]
                if output.get('ingredient_ids') is not None:
                    output['ingredient_ids'] = [prefix + mid for mid in output['ingredient_ids']]
            if old_id in config.get("step_ports", {}):
                combined["route_config"]["step_ports"][step["id"]] = config["step_ports"][old_id]
            combined["steps"].append(step)
        combined["route_config"]["edge_styles"].update({prefix+k:v for k,v in config.get("edge_styles", {}).items()})
    combined["_legends"] = legends
    menu = SimpleNamespace(name="選択日の全献立", service_date=entries[0][0].service_date,
                           facility=getattr(entries[0][0], 'facility', None))
    for event in preview_events(combined, menu):
        if event["type"] == "result":
            yield {"type": "result", "result": {**event["result"], "menu_ids": [m.id for m, _, _ in entries]}}
        else:
            yield event


def generate(revision, menu, profile_document, *, is_preview=False):
    return _event_result(generate_events(revision, menu, profile_document, is_preview=is_preview))


def generate_events(revision, menu, profile_document, *, is_preview=False):
    started = monotonic()
    document = revision.document
    if document.get("schema_version") != 2:
        raise ValueError("動線生成にはschema_version=2の工程が必要です")
    config = document.get("route_config")
    if not isinstance(config, dict):
        raise ValueError(
            "動線生成には明示的なtemplate_idと各工程の入出点設定が必要です"
        )
    template_id, ports = config.get("template_id"), config.get("step_ports")
    if not isinstance(template_id, str) or not isinstance(ports, dict):
        raise ValueError("動線設定の形式が不正です")
    svg = profile_document["svg_snapshot"].encode()
    manifest = profile_document["manifest_snapshot"]
    network = profile_document["network_snapshot"]
    if template_id != manifest["template_id"]:
        raise ValueError("工程と保存した施設図面が一致しません")
    if (document.get('pattern_applications') or document.get('shared_menu_source')) and not is_preview:
        require_pattern_equipment(document)
        # The operator chooses the equipment, not drawing angles. Compute only
        # unspecified geometry from the pinned facility snapshot; preserve all
        # explicit ports and never assign a missing equipment here.
        port_manifest = {**manifest, 'flow_network': network}
        document = bind_preview_ports(document, port_manifest)
        config = document['route_config']
        ports = config['step_ports']
    validate_pair(svg, manifest)
    root = ET.fromstring(svg)
    network = routing_network(root, network, manifest)
    if profile_document.get("routing_policy") != "candidate_geometry":
        raise ValueError(
            "この描画器は候補経路の生成のみ対応しています。確定経路とは扱えません"
        )
    from src.services.material_drawing_projection import require_decisions, project_connections
    require_decisions(document)
    document, journey_audit = expand_supplies(document, manifest)
    document, dispatch_audit = expand_dispatch(document, manifest)
    config = document['route_config']
    ports = config['step_ports']
    points = {
        point["id"]: point
        for point in manifest["points"]
        if point.get("kind") == "equipment_port"
    }
    source_points = {p['id']:p for p in manifest['points']}
    for item in dispatch_audit:
        point = source_points[item['point_id']]
        # The row of boxes lies on the outside wall. Finish the arrow on the
        # kitchen side, pointing at its box; do not invent a wall crossing.
        points[point['id']] = {**point, 'kind': 'equipment_port', 'side': '北接続',
                              'y': point['y'] - item['basis']['arrow_clearance']}
    area_bounds = {}
    for step in document['steps']:
        if not step.get('_supply_point_id'):
            continue
        point = source_points[step['_supply_point_id']]
        if point['kind']=='area_anchor':
            eid=step['equipment']['id']
            bounds=supply_area_bounds(point,network)
            area_bounds[eid]=bounds
            pid='supply-point:'+point['id']
            points[pid]={**point,'id':pid,'equipment_id':eid,'kind':'equipment_port','side':'西接続'}
            document.setdefault('_automatic_step_ports',{})[step['id']]=['ingress','egress']
        else:
            pid=point['id']
        ports[step['id']]={'ingress':pid,'egress':pid}
    steps = {step["id"]: step for step in document["steps"]}
    if not steps:
        raise ValueError("動線を表示する作業がありません。工程グラフに作業を追加してください。")
    if set(ports) - set(steps):
        raise ValueError("存在しない工程の入出点設定があります")
    missing = []
    for number, (step_id, step) in enumerate(steps.items(), 1):
        binding = ports.get(step_id, {"ingress": "", "egress": ""})
        if not isinstance(binding, dict) or set(binding) != {"ingress", "egress"}:
            raise ValueError("工程の入出点設定が不正です: " + step_id)
        problems = []
        if not step["equipment"].get("id"):
            problems.append("場所")
        for direction in ("ingress", "egress"):
            point = points.get(binding[direction])
            if not point or point.get("equipment_id") != step["equipment"]["id"]:
                problems.append("入口" if direction == "ingress" else "出口")
        if problems:
            missing.append(f"{number}. {step['operation'] or '作業未設定'}（{step_id}）：{'・'.join(problems)}")
    if missing:
        raise ValueError("図面上の場所・入出点が未設定、または設備と一致しません。\n" + "\n".join(missing))

    routes, topology, route_ids = [], [], set()
    materials_by_step, materials_by_output = material_flow(document)
    step_numbers = {sid: index for index, sid in enumerate(steps, 1)}
    for target in document["steps"]:
        for input_index, source in enumerate(target["inputs"]):
            if not source.get("step_id"):
                continue
            origin = steps[source["step_id"]]
            route_id = (
                source.get("edge_id")
                or f"{origin['id']}-{target['id']}-{input_index + 1}"
            )
            if route_id in route_ids:
                raise ValueError("工程エッジIDが重複しています")
            route_ids.add(route_id)
            output = next(
                (
                    item
                    for item in origin["outputs"]
                    if item["id"] == source.get("output_id")
                ),
                None,
            )
            # An explicitly blank GUI label is meaningful: never silently
            # replace it with a destination operation or output label.
            label = source.get("label")
            if not isinstance(label, str):
                raise ValueError("動線エッジのラベル形式が不正です: " + route_id)
            routes.append(
                {
                    "id": route_id,
                    "label": label,
                    "material_number": input_index + 1,
                    "color": origin.get("_route_color", config.get("color") or COLORS["main"]),
                    "style": config.get("edge_styles", {}).get(route_id, "solid"),
                    "start": points[ports[origin["id"]]["egress"]],
                    "end": points[ports[target["id"]]["ingress"]],
                }
            )
            topology.append(
                {
                    "edge_id": route_id,
                    "label": label,
                    "from_step_id": origin["id"],
                    "to_step_id": target["id"],
                    "output_id": source.get("output_id"),
                    "ingredient_ids": (materials_by_output[(origin['id'], source['output_id'])]
                                       if source.get('output_id') else materials_by_step[origin['id']]),
                    "quantity": output.get("quantity") if output else None,
                }
            )
    routes, topology, omitted_connections = project_connections(document,routes,topology)
    project_heat_styles(document,routes,topology)
    routes, topology, receipts = project_receipts(document,routes,topology,manifest)
    if not routes and any(m['drawing']['mode']!='annotation' for m in document['ingredients']):
        raise ValueError("描画対象に工程間の明示的な接続がありません")
    hide_standard_routes(root)
    # Template example values are not menu inputs. Never publish its example
    # rice/ingredient/dispatch text as though it came from the saved process.
    for el in root.iter():
        if el.get("data-field-id"):
            el.text = None
            for child in list(el):
                el.remove(child)
    elements = {el.get("id"): el for el in root.iter() if el.get("id")}
    parents = {child: parent for parent in root.iter() for child in parent}
    equipment_bounds = {
        eid: _equipment_bounds(elements[eid], parents)
        for eid in {p["equipment_id"] for p in points.values()} - set(area_bounds)
    }
    equipment_bounds.update(area_bounds)
    # All overlays are appended after the facility drawing; no source drawing
    # or daily reference is consulted after the profile snapshot is selected.
    height = 1410 + len(routes) * 28
    root.set("viewBox", f"0 0 1900 {height}")
    root.set("height", str(height))
    layer = element(root, "g", id="saved-process-routes")
    paint_menu_heading(layer, menu, manifest)
    # This legend contains both dish and material names. Cover the template's
    # two separate headings in the reserved left-hand text strip.
    element(layer, 'rect', x=25, y=58, width=268, height=1120, fill='white')
    legends = document.get("_legends") or [{"name": getattr(menu, "name", ""),
        "color": config.get("color") or COLORS["main"], "ingredients": document.get("ingredients", [])}]
    material_numbers = {}
    legend_y = 95
    for menu_number, legend in enumerate(legends, 1):
        groups = transport_groups({'ingredients':legend['ingredients']},topology,materials_by_step)
        for label in [f'{menu_number}. {legend["name"]}', *[f"{i}. " + '・'.join(m['name'] for m in group) for i,group in enumerate(groups,1)]]:
            for offset in range(0, len(label), 13):
                text = element(layer, "text", x=35, y=legend_y, font_size=17, fill=legend["color"])
                text.text = label[offset:offset+13]
                legend_y += 22
        legend_y += 14
        material_numbers.update({m["id"]:i for i,group in enumerate(groups,1) for m in group})
    receipt_bottom=paint_receipts(layer,receipts,material_numbers,element)
    # Reserve a separate warning band below the drawing/receipt table. Painting
    # it at y=5 used to erase the top of the original school/date heading.
    footer_y = max(1420, legend_y + 120, receipt_bottom+120)
    height = max(height, footer_y + 40 + len(routes) * 28)
    root.set("viewBox", f"0 0 1900 {height}")
    root.set("height", str(height))
    background=ET.Element('{'+NS+'}rect', {'id':'process-output-background',
        'x':'0','y':'0','width':'1900','height':str(height),'fill':'white'})
    root.insert(0,background)
    text = element(layer, "text", x=30, y=footer_y, font_size=18)
    source_label = "表示中の工程・未保存プレビュー" if is_preview else f"工程版 {revision.id}"
    text.text = f"{getattr(menu, 'service_date', '')}　{getattr(menu, 'name', '')}　{source_label}（設備内も献立色、交差：合流を意味しません）"
    line_styles = resolve_line_styles(manifest)

    for event in layout_events(document, routes, topology,
            equipment_bounds, points, network, line_styles, materials_by_step, material_numbers):
        if event['type']=='progress':
            yield {**event,'elapsed_seconds':monotonic()-started}
        else:
            geometry, prepared, placement_stats = event['geometry'], event['prepared'], event['stats']
    for index, (route, edge) in enumerate(zip(routes, topology)):
        text = element(layer, "text", x=30, y=footer_y + 30 + index * 28, fill=route["color"], font_size=18)
        text.text = f"{step_numbers[edge['from_step_id']]} {steps[edge['from_step_id']]['operation']} → {step_numbers[edge['to_step_id']]} {steps[edge['to_step_id']]['operation']}　{edge['label']} {edge['quantity'] or ''}"
    render_graph(root, geometry, prepared)
    element(layer, "rect", id="process-warning-background", x=15, y=footer_y-95, width=1870, height=35, fill="white")
    text = element(layer, "text", id="process-warning-text", x=30, y=footer_y-70, fill="#a33", font_size=22)
    text.text = "候補動線・仮配置・未確認：" + ("表示中の工程（未保存）" if is_preview else "保存した工程") + "と施設図面から自動配置（衛生上の経路承認ではありません）"
    warnings = []
    pending_drawing=[m['name'] for m in document['ingredients'] if m['drawing']['status']=='pending']
    if pending_drawing:
        warnings.append(f"{len(pending_drawing)}材料の描画対象判定は推定・未確認です。")
    if not routes:
        warnings.append('全材料が工程記載のみの指定です。独立した動線はありません。')
    inferred_supply=[m['name'] for m in document['ingredients'] if (m.get('supply') or {}).get('inference',{}).get('status')=='pending']
    if inferred_supply:
        warnings.append(f"{len(inferred_supply)}材料の搬入・保管場所はGeminiの推定・未確認です。確定した経路ではありません。")
    if placement_stats['unresolved']:
        warnings.append(f"{len(placement_stats['unresolved'])}接続で線の間隔不足が残っています。配置は未解決です。")
        text = element(layer, 'text', x=30, y=footer_y-32, fill='#a33', font_size=20)
        text.text = warnings[-1]
    yield {"type": "result", "result": {
        "revision_id": revision.id,
        "template_id": manifest["template_id"],
        "template_version": manifest["version"],
        "svg": ET.tostring(root, encoding="unicode"),
        "topology": topology,
        "generation_status": "draft",
        "placement_stats": placement_stats,
        "geometry": geometry,
        "material_journeys": journey_audit,
        "dispatch_transfers": dispatch_audit,
        "drawing_decisions": [{"ingredient_id":m['id'],"name":m['name'],**m['drawing']} for m in document['ingredients']],
        "annotation_only_connections": omitted_connections,
        "receipt_transfers": receipts,
        "warnings": warnings,
    }}
