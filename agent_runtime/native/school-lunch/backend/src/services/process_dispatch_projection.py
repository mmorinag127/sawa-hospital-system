"""Facility-common serving-to-dispatch projection, not a recipe answer.

The numbered boxes are drawing lanes, not inferred delivery destinations.
Explicit downstream work always wins; never overwrite the saved graph.
"""
from copy import deepcopy
import re
from src.services.process_material_journey import is_serving_work


def expand_dispatch(document, manifest):
    rule = manifest.get('supply_observations', {}).get('dispatch_representation')
    if not rule:
        return deepcopy(document), []
    points = {p['id']: p for p in manifest['points']}
    ids = rule['drawing_lane_point_ids']
    if len(ids) != len(set(ids)) or any(
            pid not in points or points[pid].get('kind') != 'dispatch' for pid in ids):
        raise ValueError('搬出口の図面表示レーン設定が不正です')
    doc = deepcopy(document)
    used = {i['step_id'] for s in doc['steps'] for i in s['inputs'] if i.get('step_id')}
    terminals = [s for s in doc['steps'] if s['id'] not in used and not s.get('outputs')
                 and is_serving_work(s)
                 and not re.search(r'搬出', s['operation'])]
    occupied = {s['equipment'].get('id') for s in doc['steps']}
    available = [points[pid] for pid in ids if points[pid]['equipment_id'] not in occupied]
    if len(terminals) > len(available):
        raise ValueError('搬出口の図面表示レーンが不足しています。工程を統合して省略しません。')
    existing = {s['id'] for s in doc['steps']}
    audit = []
    for origin, point in zip(terminals, available):
        sid = origin['id'] + ':dispatch'
        if sid in existing:
            raise ValueError('搬出表示工程のIDが重複しています')
        doc['steps'].append({
            'id': sid, 'operation': '搬出',
            'equipment': {'id': point['equipment_id'], 'type': '搬出口'},
            'inputs': [{'step_id': origin['id'], 'label': '盛付後の搬出'}],
            'outputs': [], 'conditions': {},
            'basis': {'operation': 'facility-observation:serving-dispatch'},
            '_dispatch_point_id': point['id'],
            '_dispatch_menu_number': origin.get('_menu_number', 1),
            **{k: origin[k] for k in ('_route_color', '_menu_name') if k in origin},
        })
        doc['route_config']['step_ports'][sid] = {
            'ingress': point['id'], 'egress': point['id']}
        audit.append({'from_step_id': origin['id'], 'to_step_id': sid,
                      'point_id': point['id'], 'basis': rule,
                      'meaning': 'drawing_lane_not_delivery_destination'})
    return doc, audit
