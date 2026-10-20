"""Adapt a saved work graph to the existing orthogonal route renderer.

Only graph connectivity and the selected facility geometry are inputs. No
reference-day routes, recipe names or ingredient-specific coordinates belong
here. Equipment visits are allocated before any edge is routed.
"""
from collections import defaultdict
from types import SimpleNamespace
import xml.etree.ElementTree as ET

from .process_route_center_flow_service import paint
from .process_route_layout_service import compact
from .process_route_network_service import (
    _route, _crossed_barriers, _outside_boxes, _join_vertices,
    fixed_route_geometry, MIN_LANE_GAP,
)
from .route_lane_placement import nudge_path, reserved_segments, simplify_path, conflict_length
from .kitchen_wall_geometry import endpoint_geometry, storage_obstacles, route_regions
from .process_heat_history import heat_history

NS = "{http://www.w3.org/2000/svg}"


def allocate_visits(document, boxes, points, active_steps=None):
    steps = {s['id']: s for s in document['steps'] if active_steps is None or s['id'] in active_steps}
    automatic = document.get('_automatic_step_ports', {})
    free = {sid for sid in steps if set(automatic.get(sid, [])) == {'ingress', 'egress'}}
    parents = {sid: sid for sid in steps}

    def find(sid):
        if parents[sid] != sid:
            parents[sid] = find(parents[sid])
        return parents[sid]

    from src.services.material_drawing_projection import shared_visit_groups
    from src.schemas.menu_process import material_flow
    flow,_=material_flow(document)
    for shared in shared_visit_groups(document,flow):
        shared=[sid for sid in shared if sid in free]
        for sid in shared[1:]: parents[find(sid)]=find(shared[0])

    # Consecutive work at one equipment has no external material movement.
    # Keep every work/edge in metadata; share only its geometric visit.
    for sid, step in steps.items():
        for source in step['inputs']:
            other = source.get('step_id')
            if (other in free and sid in free and
                    steps[other]['equipment']['id'] == step['equipment']['id']):
                parents[find(sid)] = find(other)
    units = defaultdict(list)
    for sid in steps:
        units[find(sid)].append(sid)
    by_equipment = defaultdict(list)
    for unit, members in units.items():
        by_equipment[steps[members[0]]['equipment']['id']].append(unit)
    # Order independent visits by their neighbouring work, not material
    # registration order. The latter can reverse a parallel bundle before the
    # router even starts. This changes y order only; never graph connectivity,
    # equipment identity or explicit operator ports.
    neighbours = defaultdict(set)
    for sid, step in steps.items():
        for source in step['inputs']:
            other = source.get('step_id')
            if other not in steps:
                continue
            a, b = find(other), find(sid)
            if a != b:
                neighbours[a].add(b)
                neighbours[b].add(a)
    def neighbour_position(unit):
        positions = []
        eid = steps[units[unit][0]]['equipment']['id']
        for other in neighbours[unit]:
            other_eid = steps[units[other][0]]['equipment']['id']
            if eid == other_eid:
                continue
            l, t, r, b = boxes[other_eid]
            positions.append(((t+b)/2, (l+r)/2))
        if not positions:
            l, t, r, b = boxes[eid]
            return (t+b)/2, (l+r)/2
        return tuple(sum(p[i] for p in positions)/len(positions) for i in (0, 1))
    visits = {}
    for eid, groups in by_equipment.items():
        groups = sorted(groups, key=neighbour_position)
        l, t, r, b = boxes[eid]
        # Stable graph order, vertical offsets only: independent visits never
        # receive the identical centre simply because they use one equipment.
        intervals = max(1, len(groups)-1)
        # Prefer the usual inset, but do not squeeze lanes below the router's
        # clearance while unused space still exists inside the equipment.
        # Preserve a stroke-sized border; never move a visit outside its box.
        inset = min(8., max(1.5, ((b-t)-MIN_LANE_GAP*intervals)/2))
        gap = min(22., (b-t-2*inset)/intervals)
        if gap <= 0:
            raise ValueError('設備内の作業位置を分離できません: ' + eid)
        for i, unit in enumerate(groups):
            center = ((l+r)/2, (t+b)/2 + (i-(len(groups)-1)/2)*gap)
            for sid in units[unit]:
                binding = document['route_config']['step_ports'][sid]
                ends = {k: (points[binding[k]]['x'], points[binding[k]]['y'])
                        for k in ('ingress', 'egress')}
                visits[sid] = dict(unit=unit, equipment_id=eid,
                    ingress=center if sid in free else ends['ingress'],
                    egress=center if sid in free else ends['egress'], free=sid in free,
                    reservation_half_height=min(8.,gap*.4))
    return visits, units


def transport_groups(document, topology, materials_by_step):
    """Number materials that actually follow the same flow as one legend item.

    Equality is proven by every graph edge, not by ingredient name or a recipe
    heuristic. Distinct branches/batches therefore remain distinguishable.
    """
    grouped = {}
    for material in document['ingredients']:
        mid = material['id']
        decision=material.get('drawing') or {}
        if decision.get('mode')=='annotation': continue
        edges = tuple(edge['edge_id'] for edge in topology if mid in edge['ingredient_ids'])
        places = tuple(sid for sid, ids in materials_by_step.items() if mid in ids)
        key=(('explicit-group',decision['group_id']) if decision.get('mode')=='shared' else
             ('independent',mid) if decision.get('mode')=='independent' else (edges,places))
        grouped.setdefault(key, []).append(material)
    return list(grouped.values())


def occupied_for(base,a,b,placed,visits,boxes):
    reserved=[]
    for old_path,old_a,old_b in placed:
        shared=({a['unit']} & {old_a['unit']}) | ({b['unit']} & {old_b['unit']})
        shared_boxes=[boxes[visits[u]['equipment_id']] for u in shared]
        segments=(list(zip(old_path,old_path[1:])) if a['free'] and b['free']
                  else reserved_segments(base,[old_path]))
        reserved+=_outside_boxes(segments,shared_boxes)
    return reserved


def plan_departures(visits, units, topology):
    # Reserve departures before any arrival is routed. Otherwise a many-way
    # merge can occupy all four directions and force the later exit to retrace
    # an incoming line. This is an angle plan, not a fixed equipment-side port.
    departures={}
    for unit in units:
        visit=visits[unit]
        if not visit['free']: continue
        targets=[visits[e['to_step_id']]['ingress'] for e in topology
                 if visits[e['from_step_id']]['unit']==unit
                 and visits[e['to_step_id']]['unit']!=unit]
        if not targets: continue
        p=visit['egress']
        delta=[sum(q[i]-p[i] for q in targets) for i in (0,1)]
        axis=0 if abs(delta[0])>=abs(delta[1]) else 1
        # Visits share x but have distinct y positions. A vertical departure
        # through another visit would need an immediate elbow; choose the
        # unobstructed horizontal angle before routing any arrivals instead.
        vertical_blocked = axis==1 and any(other!=unit and visits[other]['equipment_id']==visit['equipment_id']
                          and (visits[other]['ingress'][1]-p[1])*delta[1]>0
                          for other in units)
        if vertical_blocked:
            axis=0
        sign = 1 if delta[axis]>=0 else -1
        if vertical_blocked or (axis == 0 and abs(delta[0]) < 1e-9):
            # This is a lateral detour around another visit, not the main
            # travel direction. Separate it from the incoming approach even
            # when the destination has a small horizontal offset.
            incoming = [visits[e['from_step_id']]['egress'][0]-p[0] for e in topology
                        if visits[e['to_step_id']]['unit']==unit
                        and visits[e['from_step_id']]['unit']!=unit]
            if sum(incoming) > 0:
                sign = -1
            elif sum(incoming) < 0:
                sign = 1
        departures[unit]=(axis,sign)
    return departures


def layout_events(document, routes, topology, boxes, points, network, styles,
           materials_by_step, material_numbers):
    active={e[k] for e in topology for k in ('from_step_id','to_step_id')}
    visits, units = allocate_visits(document, boxes, points,active)
    departures = plan_departures(visits, units, topology)
    # Shared work/transport decisions coalesce only the same physical segment.
    unique={}; kept_routes=[]; kept_edges=[]
    for route,edge in zip(routes,topology):
        key=(visits[edge['from_step_id']]['unit'],visits[edge['to_step_id']]['unit'],route['color'],route['style'])
        if key in unique:
            target=unique[key]
            target['ingredient_ids']=sorted(set(target['ingredient_ids'])|set(edge['ingredient_ids']))
            target['all_ingredient_ids']=sorted(set(target.get('all_ingredient_ids',target['ingredient_ids']))|set(edge.get('all_ingredient_ids',edge['ingredient_ids'])))
            target.setdefault('shared_edge_ids',[]).append(edge['edge_id'])
        else:
            unique[key]=edge; kept_routes.append(route); kept_edges.append(edge)
    routes[:]=kept_routes; topology[:]=kept_edges
    # Plan the physical connections before announcing a denominator. Shared
    # transfers can reduce the count; all progress records must use that same
    # final count, including the initial zero record and an empty layout.
    yield {'type':'progress','completed':0,'total':len(routes)}
    steps = {s['id']: s for s in document['steps']}
    barriers = network['barriers']
    directions = {'西接続': (0,-1), '東接続': (0,1), '北接続': (1,-1), '南接続': (1,1)}
    geometry = dict(mode='process-graph', paths=[], through_paths=[], connection_paths=[],
        transit_paths=[], shared_sections={}, crossings=[], labels=[], ports={},
        topology={'equipment_connections': []})
    stats = dict(base_searches=0, reused_base_paths=0, nudged=0, rerouted=0, unresolved=[])
    caches, base_paths, placed, constraints = {}, {}, [], []
    for index, (route, edge) in enumerate(zip(routes, topology)):
        a, b = visits[edge['from_step_id']], visits[edge['to_step_id']]
        start, end = a['egress'], b['ingress']
        allowed = frozenset((a['equipment_id'], b['equipment_id']))
        receipt = (steps[edge['from_step_id']]['operation']=='搬入・検品'
                   and steps[edge['to_step_id']]['operation']=='保管・払出')
        cache_key = allowed, frozenset((a['unit'], b['unit'])), receipt
        if cache_key not in caches:
            # Equipment interiors are traversable only for the two works
            # actually connected by this edge, never an unrelated shortcut.
            terminal_cells, gates = endpoint_geometry(network,start,end)
            coverage = route_regions(network,receipt=receipt) + terminal_cells + [boxes[eid] for eid in sorted(allowed)]
            obstacles = [(l+.1,t+.1,r-.1,b-.1) for eid,(l,t,r,b) in boxes.items() if eid not in allowed]
            # A storage label is not the storage room. Prevent crossing an
            # unrelated refrigerator between two other works. Expand adjacent
            # cell seams slightly so a route cannot sneak along their boundary.
            obstacles.extend(storage_obstacles(network, allowed, receipt=receipt))
            # Reserve every unrelated visit before the first connector. A
            # vertical run must not pass through a later work's label/port.
            for unit in units:
                other = visits[unit]
                if unit not in cache_key[1] and other['equipment_id'] in allowed:
                    x,y = other['ingress']
                    if (x,y) in (start,end):
                        continue  # Identical, explicitly shared manual port.
                    half=other['reservation_half_height']
                    obstacles.append((x-8,y-half,x+8,y+half))
            caches[cache_key] = coverage, obstacles, fixed_route_geometry(coverage, barriers, obstacles), gates
        coverage, obstacles, fixed, gates = caches[cache_key]
        # A supply point feeding work inside this very equipment is not an
        # outward equipment exit. Keep its coordinate, but do not force a trip
        # outside and back through the adjacent worktable.
        internal_supply = (bool(steps[edge['from_step_id']].get('_supply_point_id'))
                           and a['equipment_id'] == b['equipment_id'])
        sd = departures.get(a['unit']) if a['free'] else None if internal_supply else directions[route['start']['side']]
        d = directions[route['end']['side']]
        ed = None if b['free'] else (d[0], -d[1])
        key = start, end, sd, ed, cache_key
        if start == end:
            path = [start]
        else:
            if key not in base_paths:
                try:
                    base_paths[key] = _route(start,end,coverage,barriers,gates,[],0,
                        forbidden=obstacles, occupied=(), start_direction=sd,end_direction=ed,
                        candidate_geometry=True,fixed_geometry=fixed)
                except ValueError as exc:
                    raise ValueError(f"{a['equipment_id']} → {b['equipment_id']}：{exc}") from exc
                stats['base_searches'] += 1
            else:
                stats['reused_base_paths'] += 1
            base = base_paths[key]
            reserved = occupied_for(base,a,b,placed,visits,boxes)
            if b['unit'] in departures and a['unit']!=b['unit']:
                axis,sign=departures[b['unit']]
                tip=list(end); tip[axis]+=sign*24
                reserved.append((end,tuple(tip)))
            # Auto-positioned centres have no fixed entry angle. Keeping the
            # first search's angle as a constraint preserved needless stubs.
            preserve_directions = (sd is not None, not b['free'])
            path, conflict = nudge_path(base, reserved, fixed, gates,
                                       preserve_directions=preserve_directions)
            if path != base:
                stats['nudged'] += 1
            if conflict > 1e-6:
                stats['rerouted'] += 1
                alternative = _route(start,end,coverage,barriers,gates,[],0,
                    forbidden=obstacles,occupied=reserved,start_direction=sd,end_direction=ed,
                    candidate_geometry=True,fixed_geometry=fixed)
                alternative, remaining = nudge_path(alternative,reserved,fixed,gates,
                                                    preserve_directions=preserve_directions)
                if remaining < conflict:
                    path, conflict = alternative, remaining
            path = simplify_path(path,path,reserved,fixed,gates,
                                 preserve_directions=preserve_directions)
            conflict = conflict_length(path,reserved)
            if conflict > 1e-6:
                stats['unresolved'].append(dict(edge_id=route['id'], overlap_length=conflict))
        placed.append((path,a,b))
        constraints.append((coverage,obstacles,fixed,gates,sd,ed))
        record = dict(route_id=route['id'],leg=0,points=[list(p) for p in path],
            color=route['color'],style=route['style'],dasharray=styles[route['style']]['dasharray'])
        geometry['paths'].append(record)
        geometry['ports'][route['id']] = [list(start),list(end)]
        crossed = [next(g['id'] for g in gates.values() if g['barrier_id']==w['id'] and g['span'][0]<=at<=g['span'][1])
                   for p,q in zip(path,path[1:]) for w,at in _crossed_barriers(p,q,barriers)]
        edge.update(path=record['points'],gate_ids=crossed,color=route['color'],style=route['style'],
                    equipment_internal=a['equipment_id']==b['equipment_id'])
        yield {'type':'progress','completed':index+1,'total':len(routes)}

    # A late edge cannot always escape an earlier edge by moving itself.
    # Reconsider both against the complete layout, accepting only a strict
    # conflict reduction. No topology, endpoint, equipment or wall is changed.
    changed=True
    stats['refined']=0
    while changed:
        changed=False
        for i in reversed(range(len(placed))):
            path,a,b=placed[i]
            if len(path)<2: continue
            reserved=occupied_for(path,a,b,placed[:i]+placed[i+1:],visits,boxes)
            before=conflict_length(path,reserved)
            if before<=1e-6: continue
            coverage,obstacles,fixed,gates,sd,ed=constraints[i]
            directions_kept=(sd is not None,not b['free'])
            candidate,remaining=nudge_path(path,reserved,fixed,gates,preserve_directions=directions_kept)
            if remaining>1e-6:
                alternative=_route(path[0],path[-1],coverage,barriers,gates,[],0,
                    forbidden=obstacles,occupied=reserved,start_direction=sd,end_direction=ed,
                    candidate_geometry=True,fixed_geometry=fixed)
                alternative,score=nudge_path(alternative,reserved,fixed,gates,preserve_directions=directions_kept)
                if score<remaining: candidate,remaining=alternative,score
            if remaining>=before-1e-6: continue
            candidate=simplify_path(candidate,candidate,reserved,fixed,gates,preserve_directions=directions_kept)
            placed[i]=(candidate,a,b)
            geometry['paths'][i]['points']=[list(p) for p in candidate]
            topology[i]['path']=geometry['paths'][i]['points']
            topology[i]['gate_ids']=[next(g['id'] for g in gates.values()
                if g['barrier_id']==w['id'] and g['span'][0]<=at<=g['span'][1])
                for p,q in zip(candidate,candidate[1:]) for w,at in _crossed_barriers(p,q,barriers)]
            stats['refined']+=1
            changed=True
    stats['unresolved']=[]
    for i,(path,a,b) in enumerate(placed):
        overlap=conflict_length(path,occupied_for(path,a,b,placed[:i]+placed[i+1:],visits,boxes))
        if overlap>1e-6:
            stats['unresolved'].append(dict(edge_id=routes[i]['id'],overlap_length=overlap))

    paint_routes = [dict(r) for r in routes]
    heated_by_step=heat_history(document)
    for unit,members in units.items():
        visit = visits[unit]
        connected = [r['id'] for r,e in zip(routes,topology)
                     if e['from_step_id'] in members or e['to_step_id'] in members]
        if not connected: continue
        color = steps[unit].get('_route_color', document['route_config'].get('color') or '#2563eb')
        # Explicit manual entry/exit points stay exact. Equipment transit uses
        # the same dish colour and renderer, never a separate grey overlay.
        p,q = visit['ingress'],visit['egress']
        if p != q:
            rid = 'equipment-transit:' + unit
            paint_routes.append({'id':rid,'color':color})
            path = compact([p,(q[0],p[1]),q])
            visible={mid for sid in members for mid in materials_by_step[sid] if mid in material_numbers}
            heated={mid for sid in members for mid in heated_by_step[sid]}
            transit_style='dashed' if visible and visible<=heated else 'solid'
            geometry['through_paths'].append(dict(route_id=rid,stage=0,equipment_id=visit['equipment_id'],
                points=[list(v) for v in path],color=color,style=transit_style,dasharray=styles[transit_style]['dasharray']))
            geometry['ports'][rid] = [list(p),list(q)]
            connected.append(rid)
        if connected:
            geometry['topology']['equipment_connections'].append(dict(route_id=connected[0],
                member_route_ids=connected[1:],equipment_id=visit['equipment_id'],bounds=list(boxes[visit['equipment_id']]),
                junctions=[]))
    _join_vertices(geometry['paths']+geometry['through_paths'], geometry['topology']['equipment_connections'])

    # One opaque, final annotation layer. A work sequence at the same place
    # has one union of material numbers, not repeated overprinted labels.
    occupied_labels = []
    for unit, members in units.items():
        if not any(e['from_step_id'] in members or e['to_step_id'] in members for e in topology): continue
        if steps[unit].get('_dispatch_point_id'):
            # Reference convention: dispatch boxes identify the dish, not a
            # repeated list of every ingredient already printed at serving.
            l,t,r,b = boxes[visits[unit]['equipment_id']]
            x,y = (l+r)/2,(t+b)/2
            geometry['labels'].append(dict(route_id=unit,stage=0,
                color=steps[unit].get('_route_color',document['route_config'].get('color') or '#2563eb'),
                text=str(steps[unit]['_dispatch_menu_number']),
                box=[x-7,y-8,x+7,y+8],members=[dict(step_id=unit,numbers=[])]))
            continue
        numbers = sorted({material_numbers[m] for sid in members for m in materials_by_step[sid] if m in material_numbers})
        if not numbers:
            continue
        v = visits[unit]
        x,y = ((v['ingress'][i]+v['egress'][i])/2 for i in (0,1))
        color = steps[unit].get('_route_color',document['route_config'].get('color') or '#2563eb')
        rows = [numbers[i:i+3] for i in range(0,len(numbers),3)]
        width,height = min(3,len(numbers))*15, len(rows)*16-2
        # Stack labels beside their allocated visit when a large material set
        # would mask another visit. Coordinates remain purely geometric.
        offsets = [0] + [value for i in range(1,60) for value in (-16*i,16*i)]
        for offset in offsets:
            box = [x-width/2,y-height/2+offset,x+width/2,y+height/2+offset]
            if not any(box[0]<r+1 and box[2]>l-1 and box[1]<b+1 and box[3]>t-1 for l,t,r,b in occupied_labels):
                break
        else:
            raise ValueError('材料番号の重なりを解消できません: '+unit)
        occupied_labels.append(box)
        for row,values in enumerate(rows):
            cy = box[1]+row*16+7
            geometry['labels'].append(dict(route_id=unit,stage=0,color=color,
                text=''.join(chr(0x2460+n-1) if n<=20 else str(n) for n in values),
                box=[x-len(values)*7.5,cy-7,x+len(values)*7.5,cy+7],
                members=[dict(step_id=sid,numbers=numbers) for sid in members]))
    prepared = SimpleNamespace(request={'routes':paint_routes},run_topology=[],dependencies={},order=[r['id'] for r in paint_routes])
    yield {'type':'layout','geometry':geometry,'prepared':prepared,'stats':stats}


def render(root, geometry, prepared):
    defs = ET.SubElement(root, NS+'defs')
    for i,route in enumerate(prepared.request['routes']):
        marker = ET.SubElement(defs,NS+'marker',dict(id=f'named-route-arrow-{i}',viewBox='0 0 8 8',refX='7',refY='4',
            markerWidth='8',markerHeight='8',markerUnits='userSpaceOnUse',orient='auto',overflow='visible'))
        ET.SubElement(marker,NS+'path',{'d':'M 1 1 L 7 4 L 1 7','fill':'none','stroke':route['color'],
            'stroke-width':'1.15','stroke-linecap':'round','stroke-linejoin':'round'})
    paint(root,geometry,prepared)
