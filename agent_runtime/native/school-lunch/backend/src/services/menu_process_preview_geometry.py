"""Geometry-only port approaches for explicitly unconfirmed route previews."""
from copy import deepcopy
import math
import xml.etree.ElementTree as ET

from src.services.named_svg_route_service import _equipment_bounds
from src.services.process_route_network_service import _inside, _crossed_barriers, _segment_hits_box, _edge_in_regions

DIRECTIONS={"西接続":(0,-1),"東接続":(0,1),"北接続":(1,-1),"南接続":(1,1)}

def usable_port(point, network, obstacles=()):
    p=(point["x"],point["y"])
    if not any(_inside(p,r["bounds"]) for r in network["regions"]) or on_wall(p,network):
        return False
    if obstacles:
        axis,sign=DIRECTIONS[point['side']]
        q=list(p);q[axis]+=8*sign
        if any(_segment_hits_box(p,q,box) for box in obstacles) or not _edge_in_regions(p,q,[r['bounds'] for r in network['regions']]):
            return False
    return True

def on_wall(p,network):
    return any(abs(p[0 if w['axis']=='x' else 1]-w['position'])<6-1e-6
        and w['span'][0]-1e-6<=p[1 if w['axis']=='x' else 0]<=w['span'][1]+1e-6
        for w in network['barriers'])

def preview_manifest(svg, manifest, *, approach_width=48.0):
    """Join actual equipment ports to corridor rectangles along their outward ray.

    No room/gate/operation is invented. A connector is admitted only when it
    intersects an existing corridor without any equipment or closed-wall crossing.
    It is preview geometry, not permission to use an equipment/room in production.
    """
    if not math.isfinite(approach_width) or approach_width <= 0:
        raise ValueError('設備接続の描画幅は正の有限値が必要です')
    result=deepcopy(manifest)
    network=result['flow_network']
    regions=list(network['regions'])
    root=ET.fromstring(svg)
    elements={el.get('id'):el for el in root.iter() if el.get('id')}
    parents={child:parent for parent in root.iter() for child in parent}
    equipment={p['equipment_id'] for p in manifest['points'] if p.get('kind')=='equipment_port'}
    boxes=[_equipment_bounds(elements[eid],parents) for eid in equipment]
    boxes=[(l+.1,t+.1,r-.1,b-.1) for l,t,r,b in boxes]
    connectors=[]
    for point in result['points']:
        if point.get('kind')!='equipment_port' or point.get('side') not in DIRECTIONS:
            continue
        p=(point['x'],point['y'])
        if usable_port(point,network) or on_wall(p,network):
            continue
        axis,sign=DIRECTIONS[point['side']]
        fixed=1-axis
        candidates=[]
        for region in regions:
            bounds=region['bounds']
            if not bounds[fixed]<=p[fixed]<=bounds[fixed+2]:
                continue
            lo,hi=bounds[axis],bounds[axis+2]
            start=lo if sign>0 else hi
            if (start-p[axis])*sign<0:
                continue
            q=list(p)
            q[axis]=min(lo+8,hi) if sign>0 else max(hi-8,lo)
            candidates.append((abs(q[axis]-p[axis]),tuple(q)))
        for _,q in sorted(candidates):
            if any(_segment_hits_box(p,q,box) for box in boxes):
                continue
            if any(not any(g['barrier_id']==wall['id'] and g['span'][0]<=at<=g['span'][1]
                           for g in network['gates']) for wall,at in _crossed_barriers(p,q,network['barriers'])):
                continue
            lo=[min(p[0],q[0]),min(p[1],q[1])]
            hi=[max(p[0],q[0]),max(p[1],q[1])]
            # This is drawing space, not the measured width of a physical
            # aisle. Equipment, walls and permitted doorway spans remain
            # independent hard constraints for every generated segment.
            lo[fixed]-=approach_width/2;hi[fixed]+=approach_width/2
            connectors.append({'id':'preview-approach:'+point['id'],'bounds':lo+hi})
            break
    network['regions'].extend(connectors)
    result['_preview_obstacles']=boxes
    return result
