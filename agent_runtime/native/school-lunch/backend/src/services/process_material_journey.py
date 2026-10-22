"""Material source-to-finish contract, separate from cooking and geometry.

Only explicit supply settings/graph steps establish storage. Names, recipe
dates, drawing examples and proximity are not a storage decision source.
"""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import re
from src.schemas.menu_process import material_flow


def is_serving_work(step):
    # A terminal phase such as "pour sauce" retains its serving-component
    # context. Do not require renaming the recorded culinary action.
    return bool(re.search(r'盛[り]?付|提供|配缶|搬出',step.get('operation','')) or
                re.search(r'盛[り]?付|提供|配缶',(step.get('conditions') or {}).get('component_name','')))


@lru_cache(maxsize=16)
def _receipt_connections(svg, manifest_json):
    """Check declared receiving corridors with the renderer's exact wall solver.

    The cache key includes the complete drawing and manifest, including gates;
    a changed facility snapshot can never reuse an earlier permission result.
    This establishes connectivity, not a reservation for all material lanes.
    """
    from xml.etree import ElementTree as ET
    from src.services.kitchen_wall_geometry import (
        routing_network, endpoint_geometry, route_regions, storage_obstacles,
    )
    from src.services.process_route_network_service import _route
    manifest = json.loads(manifest_json)
    network = routing_network(ET.fromstring(svg), manifest['flow_network'], manifest)
    points = {p['id']: p for p in manifest['points']
              if p['id'] in manifest.get('supply_area_ids', [])}
    inspections = [p for p in points.values() if p['name'] == '検品室']
    result = {}
    for target in points.values():
        if not re.match(r'冷[蔵凍]庫|米倉庫|食材倉庫', target['name']):
            continue
        failures = {}
        for source in inspections:
            try:
                boxes = [supply_area_bounds(p, network) for p in (source, target)]
                start, end = [((l+r)/2, (t+b)/2) for l,t,r,b in boxes]
                cells, gates = endpoint_geometry(network, start, end)
                forbidden = storage_obstacles(network, {'area:'+source['id'], 'area:'+target['id']}, receipt=True)
                _route(start, end, route_regions(network, receipt=True)+cells+boxes,
                       network['barriers'], gates, [], 0, forbidden=forbidden,
                       candidate_geometry=True)
            except ValueError:
                failures[source['id']] = (source['name']+'から'+target['name']+
                    'への搬入経路を図面の登録通路・出入口で確認できません。施設の搬入経路設定を確認してください。')
        result[target['id']] = failures
    return result


def supply_options(manifest, svg=None):
    result = []
    for point in manifest['points']:
        if point.get('kind') != 'area_anchor' or point['id'] not in manifest.get('supply_area_ids', []):
            continue
        name = point['name']
        role = ('inspection' if name == '検品室' else 'storage'
                if re.match(r'冷[蔵凍]庫|米倉庫|食材倉庫', name) else None)
        if role:
            option = {'id':point['id'], 'name':name, 'role':role}
            if manifest.get('flow_network'):
                try:
                    supply_area_bounds(point, manifest['flow_network'])
                except ValueError as exc:
                    option['unavailable_reason'] = str(exc)
            result.append(option)
    if manifest.get('flow_network'):
        if svg is None:
            # Canonical template for normal editing; snapshot callers pass their
            # actual SVG and network explicitly below instead of substituting it.
            from src.services.menu_process_route_service import load_template
            svg, _ = load_template(manifest['template_id'])
        connections = _receipt_connections(svg, json.dumps(manifest, sort_keys=True))
        for option in result:
            if option['role'] == 'storage':
                option['unavailable_from'] = dict(connections.get(option['id'], {}))
    # On-site ingredients, e.g. water, must have an explicitly selected place.
    for point in manifest['points']:
        if point.get('kind')=='equipment_port':
            result.append({'id':point['id'], 'name':point['name'], 'role':'on_site'})
    return result


def supply_area_bounds(point, network):
    """Use registered facility rectangles, never a reference-day route point."""
    registered=network.get('anchor_ranges',{}).get(point['id'])
    if registered:
        bounds=tuple(registered['bounds'])
    else:
        name=point['name'].replace('-','')
        matches=[r['bounds'] for r in network['regions'] if r['id'].replace('-','')==name]
        if len(matches)!=1:
            raise ValueError(point['name']+'の室内配置範囲が図面マスターに未登録です。別の保管庫には置換しません。')
        bounds=tuple(matches[0])
    cells=network.get('protected_storage_cells',{}).get('area:'+point['id'])
    if cells:
        # Keep the registered horizontal centre, but use the actual room's
        # vertical interior instead of squeezing every material into its label.
        bounds=(bounds[0],min(c[1] for c in cells)+8,bounds[2],max(c[3] for c in cells)-8)
    return bounds


def require_available_supplies(document, manifest, svg=None):
    """Reject explicitly selected unusable paths; keep incomplete drafts editable."""
    options = {p['id']: p for p in supply_options(manifest, svg)}
    for material in document['ingredients']:
        if material.get('drawing', {}).get('mode') == 'annotation':
            continue
        supply = material.get('supply') or {}
        if supply.get('template_id') != manifest['template_id']:
            continue  # Template mismatch is validated by the journey contract.
        for field in ('inspection_point_id', 'storage_point_id', 'source_point_id'):
            reason = options.get(supply.get(field), {}).get('unavailable_reason')
            if reason:
                raise ValueError(material['name']+'：'+reason)
        if supply.get('mode') == 'delivery' and supply.get('storage') == 'stored':
            reason = options.get(supply.get('storage_point_id'), {}).get('unavailable_from', {}).get(supply.get('inspection_point_id'))
            if reason:
                raise ValueError(material['name']+'：'+reason)


def inspect_journeys(document, manifest, svg=None):
    by_step, by_output = material_flow(document)
    options = {p['id']:p for p in supply_options(manifest, svg)}
    children = {s['id']:[] for s in document['steps']}
    for target in document['steps']:
        for ref in target['inputs']:
            if ref.get('step_id'):
                children[ref['step_id']].append((target['id'],ref.get('output_id')))
    records=[]
    for material in document['ingredients']:
        mid=material['id']; issues=[]; supply=material.get('supply') or {}
        starts=[s for s in document['steps'] if any(i.get('ingredient_id')==mid for i in s['inputs'])]
        point_ids=[]
        annotation=material.get('drawing',{}).get('mode')=='annotation'
        if annotation:
            pass  # Kept in recipe lineage; not an independent transport source.
        elif not supply:
            issues.append('搬入・検品・保管経路が未設定')
        elif supply.get('template_id')!=manifest['template_id']:
            issues.append('搬入経路と厨房図面が不一致')
        elif supply.get('mode')=='on_site':
            pid=supply.get('source_point_id')
            if pid not in options or options[pid]['role']!='on_site':
                issues.append('厨房内で供給する場所が未設定または図面に存在しない')
            else:
                point_ids=[pid]
        else:
            pid=supply.get('inspection_point_id')
            if pid not in options or options[pid]['role']!='inspection':
                issues.append('検品場所が未設定または図面に存在しない')
            else:
                point_ids=[pid]
            storage=supply.get('storage','unknown')
            if storage=='unknown':
                issues.append('保管場所／保管せず使用の選択が未設定')
            elif storage=='stored':
                pid=supply.get('storage_point_id')
                if pid not in options or options[pid]['role']!='storage':
                    issues.append('保管場所が未設定または図面に存在しない')
                else:
                    point_ids.append(pid)
                    reason = options[pid].get('unavailable_from', {}).get(supply.get('inspection_point_id'))
                    if reason:
                        issues.append(reason)
            elif storage=='direct' and supply.get('storage_point_id'):
                issues.append('保管せず使用の設定に保管場所が残っている')
        if not starts:
            issues.append('最初の作業へ未接続')
        for pid in point_ids:
            if options[pid].get('unavailable_reason'):
                issues.append(options[pid]['unavailable_reason'])
        visited=set(); queue=[s['id'] for s in starts]
        while queue:
            sid=queue.pop()
            if sid in visited:
                continue
            visited.add(sid)
            queue.extend(target for target,oid in children[sid]
                         if mid in (by_output[(sid,oid)] if oid else by_step[sid]))
        ends=[]
        for step in document['steps']:
            sid=step['id']
            if sid not in visited:
                continue
            relevant=[target for target,oid in children[sid]
                      if mid in (by_output[(sid,oid)] if oid else by_step[sid])]
            # A disconnected split branch must not disappear merely because
            # another branch of the same material eventually reaches serving.
            open_outputs=[o['id'] for o in step['outputs'] if mid in by_output[sid,o['id']]
                          and not any(oid==o['id'] for _,oid in children[sid])]
            if open_outputs:
                issues.append(f"{step['operation']}の取り分け出力が未接続："+'、'.join(open_outputs))
            if not relevant:
                ends.append(sid)
                if not is_serving_work(step):
                    issues.append(f"{step['operation'] or sid}で途切れており盛付・提供へ未接続")
        records.append({'ingredient_id':mid,'name':material['name'], 'source_points':point_ids,
            'drawing_mode':material.get('drawing',{}).get('mode'),
            'start_steps':[s['id'] for s in starts], 'end_steps':ends,
            'reachable_steps':sorted(visited),'issues':issues})
    return {'complete':all(not r['issues'] for r in records),'materials':records}


def expand_supplies(document, manifest, svg=None):
    """Compile explicit supply legs, retaining original cooking work verbatim."""
    audit=inspect_journeys(document,manifest,svg)
    if not audit['complete']:
        details=[f"{r['name']}：{'／'.join(r['issues'])}" for r in audit['materials'] if r['issues']]
        raise ValueError('材料の搬入から盛付までの経路が未完成です。作業台から始まる図は生成しません。\n'+'\n'.join(details))
    doc=deepcopy(document)
    points={p['id']:p for p in manifest['points']}
    prefix=[]
    existing={s['id'] for s in doc['steps']}
    groups={}
    for record in audit['materials']:
        if record['drawing_mode']=='annotation': continue
        key=tuple(record['source_points']),tuple(record['start_steps'])
        groups.setdefault(key,[]).append(record)
    for records in groups.values():
        record=records[0]
        mids=[r['ingredient_id'] for r in records]
        name='・'.join(r['name'] for r in records)
        group_key='|'.join(mids); previous=None
        owner=next(s for s in doc['steps'] if s['id']==record['start_steps'][0])
        for index,pid in enumerate(record['source_points']):
            sid='supply-'+hashlib.sha256((group_key+':'+str(index)).encode()).hexdigest()[:24]
            if sid in existing:
                raise ValueError('搬入工程のIDが既存工程と重複しています')
            point=points[pid]
            operation='搬入・検品' if index==0 and point['name']=='検品室' else '保管・払出' if point['kind']=='area_anchor' else '厨房内供給'
            prefix.append({'id':sid,'operation':operation,
                'inputs':[{'step_id':previous,'label':name}] if previous else [{'ingredient_id':material_id} for material_id in mids],
                'equipment':{'type':'保管場所' if point['kind']=='area_anchor' else '供給場所',
                    'id':point.get('equipment_id') or 'area:'+pid},
                'conditions':{'processing_mode':'separate_batches'},'basis':{'operation':'explicit:material-supply'},'outputs':[],
                '_supply_point_id':pid, '_route_color':owner.get('_route_color') or doc['route_config'].get('color') or '#2563eb',
                '_menu_name':owner.get('_menu_name','')})
            previous=sid
        for step in doc['steps']:
            new=[]; linked=False
            for ref in step['inputs']:
                if ref.get('ingredient_id') in mids:
                    if not linked:
                        new.append({'step_id':previous,'label':name})
                        linked=True
                else:
                    new.append(ref)
            step['inputs']=new
    doc['steps']=prefix+doc['steps']
    original_flow, _ = material_flow(document)
    compiled_flow, _ = material_flow(doc)
    if any(compiled_flow[sid] != origins for sid, origins in original_flow.items()):
        raise ValueError('搬入経路の接続により工程の対象材料が変わりました。生成を中止します。')
    return doc,audit
