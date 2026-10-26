"""Constraint-first orthogonal process-route network renderer.

This module deliberately has no fallback to the historical free-path router.
Business topology is prepared first; each leg is then solved inside its
operator-declared corridor union and independently checked before rendering.
"""
from copy import deepcopy
from bisect import bisect_left, bisect_right
import heapq
import math

from .named_svg_route_service import resolve_line_styles, validate_pair
from .process_route_rules_service import prepare_process_route_rules
from .process_route_center_flow_service import _centres, canvas, paint
from .process_route_layout_service import compact


EPS = 1e-6
# Five stroke widths (renderer stroke=1.6), leaving visible white space even
# next to a 5-unit crossing halo. This is a hard bound, never a soft penalty.
MIN_LANE_GAP = 8.0
OBSTACLE_CLEARANCE = 4.0


def _inside(p, box):
    return box[0] - EPS <= p[0] <= box[2] + EPS and box[1] - EPS <= p[1] <= box[3] + EPS


def _allowed_grid_y(xs, ys, regions, barriers):
    """Reuse exact y-membership sets; keep every original grid candidate."""
    region_rows = [(l-EPS, r+EPS, {y for y in ys if t-EPS <= y <= b+EPS})
                   for l,t,r,b in regions]
    wall_rows = [(w, {y for y in ys if (
        w['span'][0]-EPS <= y <= w['span'][1]+EPS if w['axis']=='x'
        else abs(y-w['position']) < 6-EPS)}) for w in barriers]
    allowed = {}
    for x in xs:
        rows = set()
        for left,right,values in region_rows:
            if left <= x <= right:
                rows.update(values)
        for wall,values in wall_rows:
            if (abs(x-wall['position']) < 6-EPS if wall['axis']=='x'
                else wall['span'][0]-EPS <= x <= wall['span'][1]+EPS):
                rows.difference_update(values)
        allowed[x] = rows
    return allowed


def _line_intervals(boxes, axis, fixed, *, obstacles=False):
    """Index exact rectangle intersections once per grid line."""
    spans = []
    for box in boxes:
        if obstacles:
            if not box[1-axis]+EPS < fixed < box[3-axis]-EPS:
                continue
            start, end = box[axis]+EPS, box[axis+2]-EPS
            if start >= end:
                continue
        else:
            if not box[1-axis]-EPS <= fixed <= box[3-axis]+EPS:
                continue
            start, end = box[axis], box[axis+2]
        spans.append((start, end))
    merged = []
    tolerance = 0 if obstacles else EPS
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + tolerance:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return [a for a, _ in merged], [b for _, b in merged]


def _line_edge_allowed(lo, hi, coverage, obstacles):
    starts, ends = coverage
    if hi-lo <= 2*EPS:
        # Preserve the original cursor comparisons even for sub-EPS edges;
        # subtracting floats is not interchangeable at these boundaries.
        cursor = lo
        for start, end in zip(starts, ends):
            if end < cursor-EPS:
                continue
            if start > cursor+EPS:
                return False
            cursor = max(cursor,end)
            if cursor >= hi-EPS:
                break
        if cursor < hi-EPS:
            return False
    else:
        index = bisect_right(starts, lo + EPS) - 1
        if index < 0 or ends[index] < hi-EPS:
            return False
    starts, ends = obstacles
    index = bisect_left(starts, hi) - 1
    return index < 0 or ends[index] <= lo


def _edge_in_regions(a, b, regions):
    """An axis-aligned edge is legal only when its whole length is covered."""
    if a[0] != b[0] and a[1] != b[1]:
        return False
    axis, fixed = (1, a[0]) if a[0] == b[0] else (0, a[1])
    lo, hi = sorted((a[axis], b[axis]))
    spans = []
    for l, t, r, bottom in regions:
        if (l - EPS <= fixed <= r + EPS) if axis == 1 else (t - EPS <= fixed <= bottom + EPS):
            spans.append((t, bottom) if axis == 1 else (l, r))
    cursor = lo
    for start, end in sorted(spans):
        if end < cursor - EPS:
            continue
        if start > cursor + EPS:
            return False
        cursor = max(cursor, end)
        if cursor >= hi - EPS:
            return True
    return cursor >= hi - EPS


def _crossed_barriers(a, b, barriers):
    crossed = []
    for barrier in barriers:
        axis, pos = barrier['axis'], barrier['position']
        if axis == 'x' and a[1] == b[1] and barrier['span'][0] - EPS <= a[1] <= barrier['span'][1] + EPS and min(a[0], b[0]) + EPS < pos < max(a[0], b[0]) - EPS:
            crossed.append((barrier, a[1]))
        if axis == 'y' and a[0] == b[0] and barrier['span'][0] - EPS <= a[0] <= barrier['span'][1] + EPS and min(a[1], b[1]) + EPS < pos < max(a[1], b[1]) - EPS:
            crossed.append((barrier, a[0]))
    return sorted(crossed, key=lambda item: (item[0]['position'] - a[0 if item[0]['axis']=='x' else 1]) / (b[0 if item[0]['axis']=='x' else 1] - a[0 if item[0]['axis']=='x' else 1]))


def fixed_route_geometry(regions, barriers, forbidden=()):
    """Request-local indexes for geometry which cannot change between legs."""
    return {
        "regions": tuple(tuple(box) for box in regions),
        "barriers": tuple((wall["id"], wall["axis"], wall["position"], tuple(wall["span"])) for wall in barriers),
        "forbidden": tuple(tuple(box) for box in forbidden),
        "coverage": {}, "obstacles": {}, "crossings": {},
    }


def fixed_wall_geometry(barriers):
    return {
        "barriers": tuple((wall["id"], wall["axis"], wall["position"], tuple(wall["span"])) for wall in barriers),
        "lines": {}, "crossings": {},
    }


def _indexed_wall_crossings(a, b, barriers, geometry):
    edge = (a, b)
    if edge not in geometry["crossings"]:
        axis = 0 if a[1] == b[1] else 1
        fixed = a[1 - axis]
        line = (axis, fixed)
        if line not in geometry["lines"]:
            walls = sorted(
                ((wall["position"], index, wall) for index, wall in enumerate(barriers)
                 if wall["axis"] == ("x" if axis == 0 else "y")
                 and wall["span"][0] - EPS <= fixed <= wall["span"][1] + EPS),
                key=lambda item: (item[0], item[1]),
            )
            geometry["lines"][line] = ([item[0] for item in walls], [item[2] for item in walls])
        positions, walls = geometry["lines"][line]
        low, high = sorted((a[axis], b[axis]))
        candidates = walls[bisect_right(positions, low + EPS):bisect_left(positions, high - EPS)]
        geometry["crossings"][edge] = _crossed_barriers(a, b, candidates)
    return geometry["crossings"][edge]


def _geometry_indexes(geometry, regions, barriers, forbidden):
    if geometry is None:
        geometry = fixed_route_geometry(regions, barriers, forbidden)
    if (geometry["regions"] != tuple(tuple(box) for box in regions) or
            geometry["barriers"] != tuple((wall["id"], wall["axis"], wall["position"], tuple(wall["span"])) for wall in barriers) or
            geometry["forbidden"] != tuple(tuple(box) for box in forbidden)):
        raise ValueError("異なる corridor/壁/設備条件で固定経路ジオメトリを共有できません")
    return geometry


def _validate_network(manifest, request, routes):
    network = manifest.get('flow_network')
    if not isinstance(network, dict):
        raise ValueError('施設マスターに flow_network がありません')
    regions = network.get('regions')
    barriers = network.get('barriers', [])
    gates = network.get('gates', [])
    if not isinstance(regions, list) or not regions:
        raise ValueError('flow_network.regions が未設定です')
    ids, region_by_id = set(), {}
    for item in regions:
        if not isinstance(item, dict) or set(item) != {'id', 'bounds'} or not isinstance(item['id'], str) or item['id'] in ids:
            raise ValueError('flow_network.regions が不正です')
        box = item['bounds']
        if not isinstance(box, list) or len(box) != 4 or any(type(v) not in (int, float) for v in box) or not (box[0] < box[2] and box[1] < box[3]):
            raise ValueError('flow_network region bounds が不正です')
        ids.add(item['id']); region_by_id[item['id']] = tuple(map(float, box))
    barrier_by_id = {}
    for item in barriers:
        if not isinstance(item, dict) or set(item) != {'id', 'axis', 'position', 'span'} or item.get('axis') not in ('x', 'y') or not isinstance(item.get('id'), str) or item['id'] in barrier_by_id:
            raise ValueError('flow_network.barriers が不正です')
        span = item['span']
        if not isinstance(item['position'], (int, float)) or not isinstance(span, list) or len(span) != 2 or not span[0] < span[1]:
            raise ValueError('flow_network barrier span が不正です')
        barrier_by_id[item['id']] = dict(item, position=float(item['position']), span=tuple(map(float, span)))
    gate_by_id = {}
    for item in gates:
        if not isinstance(item, dict) or set(item) != {'id', 'barrier_id', 'span'} or not isinstance(item.get('id'), str) or item['id'] in gate_by_id or item.get('barrier_id') not in barrier_by_id:
            raise ValueError('flow_network.gates が不正です')
        span = item['span']
        if not isinstance(span, list) or len(span) != 2 or not span[0] < span[1]:
            raise ValueError('flow_network gate span が不正です')
        gate_by_id[item['id']] = dict(item, span=tuple(map(float, span)))
    layout = request.get('layout', {})
    corridors, required_gates = layout.get('leg_corridors'), layout.get('leg_gates', {})
    if not isinstance(corridors, dict) or set(corridors) != set(routes):
        raise ValueError('layout.leg_corridors は全動線に必要です')
    if not isinstance(required_gates, dict) or set(required_gates) - set(routes):
        raise ValueError('layout.leg_gates の動線IDが不正です')
    anchors = network.get('anchor_ranges', {})
    known_points = {point['id'] for point in manifest.get('points', [])}
    if not isinstance(anchors, dict): raise ValueError('flow_network.anchor_ranges が不正です')
    anchor_ranges = {}
    for point_id, spec in anchors.items():
        if point_id not in known_points or not isinstance(spec, dict) or set(spec) != {'bounds', 'axis'} or spec['axis'] not in ('x', 'y'):
            raise ValueError('flow_network.anchor_ranges の point_id/axis が不正です')
        bounds = spec['bounds']
        if not isinstance(bounds, list) or len(bounds) != 4 or any(type(value) not in (int, float) for value in bounds) or not (bounds[0] <= bounds[2] and bounds[1] <= bounds[3]):
            raise ValueError('flow_network.anchor_ranges の bounds が不正です')
        anchor_ranges[point_id] = (tuple(map(float, bounds)), spec['axis'])
    result = {}
    for rid, route in routes.items():
        legs = corridors.get(rid)
        if not isinstance(legs, dict) or set(legs) != {str(i) for i in range(len(route['stages']) - 1)}:
            raise ValueError('layout.leg_corridors は全区間を指定してください: ' + rid)
        result[rid] = {}
        for i in range(len(route['stages']) - 1):
            names = legs[str(i)]
            if not isinstance(names, list) or not names or len(set(names)) != len(names) or any(name not in region_by_id for name in names):
                raise ValueError('未知または空の corridor 領域です: ' + rid + '/' + str(i))
            gids = required_gates.get(rid, {}).get(str(i), [])
            if not isinstance(gids, list) or len(set(gids)) != len(gids) or any(gid not in gate_by_id for gid in gids):
                raise ValueError('未知または重複した必須 gate です: ' + rid + '/' + str(i))
            result[rid][i] = ([region_by_id[n] for n in names], gids)
    return list(barrier_by_id.values()), gate_by_id, result, anchor_ranges


def _route(start, end, regions, barriers, gates, required_gate_ids, lane, forbidden=(), occupied=(), start_direction=None, end_direction=None, candidate_geometry=False, fixed_geometry=None, wall_geometry=None):
    """Search once, with gate progress in the state, rather than joining detours.

    Wall coordinates cannot be vertices. Thus even a failed search cannot
    produce a corner on a wall, or hide an undeclared room crossing at a
    segment endpoint. Gate ranges are constraints, not compulsory midpoints.
    """
    geometry = _geometry_indexes(fixed_geometry, regions, barriers, forbidden)
    walls = wall_geometry if wall_geometry is not None else fixed_wall_geometry(barriers)
    if walls["barriers"] != geometry["barriers"]:
        raise ValueError("異なる壁条件で交差インデックスを共有できません")
    if not all(any(_inside(p, box) for box in regions) for p in (start,end)):
        raise ValueError('設備 port が指定 corridor 領域内にありません')
    def on_wall(p):
        return any(abs(p[0 if w['axis']=='x' else 1]-w['position'])<6-EPS and
                   w['span'][0]-EPS <= p[1 if w['axis']=='x' else 0] <= w['span'][1]+EPS for w in barriers)
    if on_wall(start) or on_wall(end):
        raise ValueError('設備 port を壁上に置くことはできません')
    xs={start[0],end[0]};ys={start[1],end[1]}
    for x,y in (start,end):
        xs.update((x-MIN_LANE_GAP,x+MIN_LANE_GAP));ys.update((y-MIN_LANE_GAP,y+MIN_LANE_GAP))
    for l,t,r,b in regions:
        xs.update((l,r));ys.update((t,b))
    for l,t,r,b in forbidden:
        xs.update((l-OBSTACLE_CLEARANCE,r+OBSTACLE_CLEARANCE));ys.update((t-OBSTACLE_CLEARANCE,b+OBSTACLE_CLEARANCE))
    for a,b in occupied:
        if a[0]==b[0]: xs.update((a[0]-MIN_LANE_GAP,a[0]+MIN_LANE_GAP))
        else: ys.update((a[1]-MIN_LANE_GAP,a[1]+MIN_LANE_GAP))
    for w in barriers:
        (xs if w['axis']=='x' else ys).update((w['position']-6,w['position']+6))
    targets={}
    for gid in (sorted(gates) if candidate_geometry else required_gate_ids):
        g=gates[gid];w=next(w for w in barriers if w['id']==g['barrier_id'])
        # Candidate permissions outside this edge's coverage are not required
        # waypoints. They contribute no grid coordinate or reachable edge.
        if candidate_geometry and not _gate_intervals(w,g,regions):
            continue
        target=_gate_slot(w,g,regions,lane.get(gid,0) if isinstance(lane,dict) else lane)
        targets[gid]=target
        (ys if w['axis']=='x' else xs).add(target)
    # Point membership is separable by x. Avoid scanning unrelated walls and
    # rectangles at every grid point while producing the identical node set.
    allowed_y = _allowed_grid_y(xs, ys, regions, barriers)
    nodes={(x,y) for x in xs for y in ys if y in allowed_y[x]}
    rows={};cols={}
    for p in nodes:
        rows.setdefault(p[1],[]).append(p);cols.setdefault(p[0],[]).append(p)
    graph={p:[] for p in nodes}
    for group,axis in ((rows,0),(cols,1)):
        for points in group.values():
            points.sort(key=lambda p:p[axis])
            # Only boxes intersecting this row/column can affect any edge on
            # it. Preserve original order and exact EPS predicates; this is
            # an index, not a reduced routing search or a geometry heuristic.
            fixed=points[0][1-axis]
            line_key=(axis, fixed)
            coverage=geometry["coverage"].get(line_key)
            if coverage is None:
                coverage=geometry["coverage"][line_key]=_line_intervals(regions,axis,fixed)
            obstacles=geometry["obstacles"].get(line_key)
            if obstacles is None:
                obstacles=geometry["obstacles"][line_key]=_line_intervals(forbidden,axis,fixed,obstacles=True)
            for a,b in zip(points,points[1:]):
                if not _line_edge_allowed(a[axis],b[axis],coverage,obstacles): continue
                if not candidate_geometry and any(_parallel_crowding(a,b,c,d) for c,d in occupied): continue
                length=abs(a[axis]-b[axis])
                graph[a].append((b,length));graph[b].append((a,length))
    # The search retains direction and required-gate index. Crossing a legal
    # but unrequested doorway is not an alternative to the requested route.
    queue=[(abs(end[0]-start[0])+abs(end[1]-start[1]),0,0,start,-1,0,0)];best={};parents={};serial=0;endkey=None
    # Direction/progress states revisit the same physical edge. Its occupancy
    # cost is invariant during this search; reuse that exact value, not a
    # shortened candidate list or an approximation of the routing constraints.
    crowding_costs={};crowding_lines={};barrier_crossings=geometry["crossings"]
    while queue:
        _,_,cost,p,axis,sign,progress=heapq.heappop(queue)
        key=(p,axis,sign,progress)
        if key in best and cost>best[key]+EPS: continue
        if p==end and progress==len(required_gate_ids): endkey=key;break
        for q,length in graph[p]:
            naxis=0 if p[0]!=q[0] else 1;nsign=1 if q[naxis]>p[naxis] else -1
            if axis==-1 and start_direction is not None and (naxis,nsign)!=start_direction:continue
            if q==end and end_direction is not None and (naxis,nsign)!=end_direction:continue
            # Keep a full lane-width straight approach at a port. A tiny
            # terminal elbow otherwise crowds the equipment's outgoing line.
            if axis!=-1 and axis!=naxis:
                if end_direction is not None:
                    ea,es=end_direction
                    if p[1-ea]==end[1-ea] and naxis==ea and 0<(end[ea]-p[ea])*es<MIN_LANE_GAP-EPS:continue
                if start_direction is not None:
                    sa,ss=start_direction
                    if p[1-sa]==start[1-sa] and axis==sa and 0<(p[sa]-start[sa])*ss<MIN_LANE_GAP-EPS:continue
            if axis==naxis and sign==-nsign:continue
            nextprogress=progress;penalty=0;valid=True
            directed_edge=(p,q)
            if directed_edge not in barrier_crossings:
                barrier_crossings[directed_edge]=_indexed_wall_crossings(p,q,barriers,walls)
            for w,at in barrier_crossings[directed_edge]:
                if candidate_geometry:
                    if not any(g['barrier_id']==w['id'] and g['span'][0]-EPS<=at<=g['span'][1]+EPS for g in gates.values()):
                        valid=False;break
                    continue
                if nextprogress>=len(required_gate_ids):valid=False;break
                gid=required_gate_ids[nextprogress];g=gates[gid]
                if w['id']!=g['barrier_id'] or not g['span'][0]-EPS<=at<=g['span'][1]+EPS:valid=False;break
                penalty+=abs(at-targets[gid])*0.05;nextprogress+=1
            if not valid:continue
            turn=axis!=-1 and axis!=naxis
            if candidate_geometry:
                physical_edge=(p,q) if p<q else (q,p)
                if physical_edge not in crowding_costs:
                    line=(naxis,p[1-naxis])
                    if line not in crowding_lines:
                        # Other orientations and distant parallel lines have
                        # exactly zero cost. Index them once per coordinate;
                        # retain every potentially nonzero term and its order.
                        crowding_lines[line]=[(c,d) for c,d in occupied
                            if c[1-naxis]==d[1-naxis] and abs(p[1-naxis]-c[1-naxis])<MIN_LANE_GAP-EPS]
                    crowding_costs[physical_edge]=length*20*sum(_parallel_crowding(p,q,c,d) for c,d in crowding_lines[line])
                penalty += crowding_costs[physical_edge]
            ncost=cost+length+80*turn+penalty
            nkey=(q,naxis,nsign,nextprogress)
            if ncost+EPS>=best.get(nkey,float('inf')):continue
            best[nkey]=ncost;parents[nkey]=key;serial+=1
            heuristic=abs(end[0]-q[0])+abs(end[1]-q[1])
            heapq.heappush(queue,(ncost+heuristic,serial,ncost,q,naxis,nsign,nextprogress))
    if endkey is None:raise ValueError('指定 corridor/gate 内に直交経路がありません')
    path=[];key=endkey
    while key in parents:path.append(key[0]);key=parents[key]
    path.append(start)
    return compact(path[::-1])




def _gate_intervals(barrier, gate, regions):
    """Intersect the coverage union on both sides, including adjacent cells."""
    low, high = gate['span']
    axis=1 if barrier['axis']=='x' else 0
    before=list(zip(*_line_intervals(regions,axis,barrier['position']-1)))
    after=list(zip(*_line_intervals(regions,axis,barrier['position']+1)))
    return [(max(low,a,c),min(high,b,d)) for a,b in before for c,d in after
            if max(low,a,c)+2*EPS<min(high,b,d)]


def _gate_slot(barrier, gate, regions, lane):
    """Pick a rank-preserving gate slot legal on both sides of the wall."""
    low,high=gate['span']
    intervals=_gate_intervals(barrier,gate,regions)
    if not intervals: raise ValueError('gate の両側に corridor union がありません')
    target = (low + high) / 2 + lane
    # Choosing the nearest feasible value retains globally assigned relative
    # lane order without assuming that a gate centre belongs to this corridor.
    choices = [(max(a + EPS, min(b - EPS, target)), a, b) for a, b in intervals]
    return min(choices, key=lambda item: abs(item[0] - target))[0]


def _segment_hits_box(a, b, box):
    l, t, r, bottom = box
    if a[0] == b[0]:
        return l + EPS < a[0] < r - EPS and max(min(a[1], b[1]), t + EPS) < min(max(a[1], b[1]), bottom - EPS)
    return t + EPS < a[1] < bottom - EPS and max(min(a[0], b[0]), l + EPS) < min(max(a[0], b[0]), r - EPS)


def _collinear_overlap(a, b, c, d):
    if a[0] == b[0] == c[0] == d[0]: return max(min(a[1], b[1]), min(c[1], d[1])) < min(max(a[1], b[1]), max(c[1], d[1])) - EPS
    if a[1] == b[1] == c[1] == d[1]: return max(min(a[0], b[0]), min(c[0], d[0])) < min(max(a[0], b[0]), max(c[0], d[0])) - EPS
    return False


def _parallel_crowding(a, b, c, d):
    """Reject near parallel strokes, not just identical centre lines.

    Orthogonal crossings are distinct from a shared parallel run. Endpoint
    contact has zero projected length and is handled by the topology layer.
    """
    for axis in (0, 1):
        fixed=1-axis
        if a[fixed]==b[fixed] and c[fixed]==d[fixed]:
            overlap=min(max(a[axis],b[axis]),max(c[axis],d[axis]))-max(min(a[axis],b[axis]),min(c[axis],d[axis]))
            return overlap>EPS and abs(a[fixed]-c[fixed])<MIN_LANE_GAP-EPS
    return False


def _outside_boxes(segments, boxes):
    """Reserve transports, but not the interior of an explicitly shared join."""
    result=list(segments)
    for box in boxes:
        clipped=[]
        for a,b in result:
            axis=0 if a[0]!=b[0] else 1
            fixed=a[1-axis]
            low,high=sorted((a[axis],b[axis]))
            start,end=(box[0],box[2]) if axis==0 else (box[1],box[3])
            side_low,side_high=(box[1],box[3]) if axis==0 else (box[0],box[2])
            if not side_low<=fixed<=side_high or high<=start or low>=end:
                clipped.append((a,b));continue
            def point(v):return (v,fixed) if axis==0 else (fixed,v)
            if low<start:clipped.append((point(low),point(start)))
            if high>end:clipped.append((point(end),point(high)))
        result=clipped
    return result


def _unreserve_shared(segments, shared):
    """Only explicitly declared shared intervals may reuse an occupied rail."""
    result=list(segments)
    for c,d in shared:
        clipped=[]
        for a,b in result:
            if not _collinear_overlap(a,b,c,d):clipped.append((a,b));continue
            axis=0 if a[0]!=b[0] else 1;fixed=a[1-axis]
            low,high=sorted((a[axis],b[axis]));start,end=sorted((c[axis],d[axis]))
            def point(v):return (v,fixed) if axis==0 else (fixed,v)
            if low<start:clipped.append((point(low),point(start)))
            if high>end:clipped.append((point(end),point(high)))
        result=clipped
    return result


def _labels(prepared, positions, manifest, boxes):
    labels = []
    clusters=[]
    for rid, stages in prepared.stages.items():
        route = prepared.routes_by_id[rid]
        numbers = route.get('material_numbers', [route.get('material_number')])
        for i in route.get('number_stages', range(len(stages))):
            if i == 0 and prepared.dependencies[rid]:
                continue  # upstream member labels already identify this join
            x, y = positions[rid, i]
            point=stages[i];mode=prepared.number_modes[rid,i]
            if mode=='equipment':
                anchor=point.get('label_anchor')
                if anchor:
                    target=next(p for p in manifest['points'] if p['id']==anchor);x,y=target['x'],target['y']
                elif point.get('equipment_id') in boxes:
                    l,t,r,b=boxes[point['equipment_id']];x,y=(l+r)/2,(t+b)/2
            elif mode=='adjacent':
                side=prepared.layout.get('label_sides',{}).get(rid,{}).get(str(i),'top')
                dx,dy={'left':(-18,0),'right':(18,0),'top':(0,-18),'bottom':(0,18)}[side];x+=dx;y+=dy
            identity=point.get('equipment_id',point.get('area_id',point['id']))
            record=(rid,i,numbers,route['color'])
            touching=[c for c in clusters if c['identity']==identity and any(abs(x-p[0])<15 and abs(y-p[1])<14 for p in c['positions'])]
            cluster=dict(identity=identity,positions=[(x,y)],members=[record])
            for other in touching:
                cluster['positions']+=other['positions'];cluster['members']+=other['members'];clusters.remove(other)
            clusters.append(cluster)
    for cluster in clusters:
        ordered=sorted(zip(cluster['positions'],cluster['members']),key=lambda pair:(pair[0][0],pair[0][1],min(pair[1][2])))
        x=sum(p[0] for p,_ in ordered)/len(ordered);y=sum(p[1] for p,_ in ordered)/len(ordered)
        members=[m for _,m in ordered]
        atoms = [(rid,stage,number,color) for rid,stage,numbers,color in members for number in numbers]
        for row in range(0,len(atoms),3):
            items=atoms[row:row+3]
            runs=[dict(text=chr(0x2460+number-1) if 1<=number<=20 else str(number),color=color)
                  for _,_,number,color in items]
            width=15*len(items);cy=y+(row//3-(math.ceil(len(atoms)/3)-1)/2)*16
            labels.append(dict(route_id=items[0][0],stage=items[0][1],text=''.join(v['text'] for v in runs),
                               runs=runs,color=items[0][3],
                               members=[dict(route_id=rid,stage=i,numbers=nums) for rid,i,nums,_ in members],
                               box=[x-width/2,cy-7,x+width/2,cy+7]))
    return labels


def _network_slots(prepared, boxes, anchor_ranges):
    """Allocate all equipment ports at once, preserving only explicit fixed ports."""
    parent = {}
    keys = [('stage', rid, i) for rid, points in prepared.stages.items() for i in range(len(points))]
    def find(key):
        parent.setdefault(key, key)
        if parent[key] != key: parent[key] = find(parent[key])
        return parent[key]
    def join(a, b): parent[find(a)] = find(b)
    for run in prepared.run_topology:
        anchor = run['members'][0]
        for member in run['members'][1:]:
            for offset in range(len(run['stage_names'])):
                join(('stage', anchor['route_id'], anchor['from_stage'] + offset), ('stage', member['route_id'], member['from_stage'] + offset))
    for rid, refs in prepared.dependencies.items():
        for ref in refs: join(('stage', rid, 0), ('stage', ref, len(prepared.stages[ref]) - 1))
    groups, anchor_groups = {}, {}
    for key in keys:
        point = prepared.stages[key[1]][key[2]]; equipment = point.get('equipment_id')
        if equipment: groups.setdefault(equipment, {}).setdefault(find(key), []).append(key)
        elif point['id'] in anchor_ranges: anchor_groups.setdefault(point['id'], {}).setdefault(find(key), []).append(key)
        elif point.get('kind') == 'area_anchor': raise ValueError('area_anchor の flow_network.anchor_ranges が未設定です: ' + point['id'])
    result = {}
    for rid, points in prepared.stages.items():
        for i, point in enumerate(points):
            if not point.get('equipment_id'): result[rid, i] = (float(point['x']), float(point['y']))
    rank = {rid: i for i, rid in enumerate(prepared.order)}
    for equipment, units in groups.items():
        if equipment not in boxes: raise ValueError('図面上の設備領域が不明です: ' + equipment)
        l, t, r, bottom = boxes[equipment]
        ordered = sorted(units.values(), key=lambda unit: min((rank[k[1]], k[2]) for k in unit))
        equipment_sides={prepared.stages[k[1]][k[2]].get('side') for unit in ordered for k in unit}
        for index, unit in enumerate(ordered):
            fixed = [prepared.stages[k[1]][k[2]] for k in unit if prepared.stages[k[1]][k[2]].get('allocation') == 'fixed']
            coords = {(float(p['x']), float(p['y'])) for p in fixed}
            if len(coords) > 1: raise ValueError('共有設備訪問の固定 port が矛盾しています')
            if coords: position = next(iter(coords))
            else:
                sides = equipment_sides
                fraction = (index + 1) / (len(ordered) + 1)
                # Vertical approaches receive distinct X; horizontal approaches
                # receive distinct Y. Mixed approaches use the interior slot.
                if all(prepared.stages[k[1]][k[2]].get('kind')=='dispatch' for k in unit): position=(l+(r-l)*fraction,t)
                elif sides <= {'北接続', '南接続'}: position = (l + (r-l)*fraction, (t+bottom)/2)
                elif sides <= {'西接続', '東接続'}: position = ((l+r)/2, t + (bottom-t)*fraction)
                else: position = (l + (r-l)*fraction, t + (bottom-t)*fraction)
            for key in unit: result[key[1], key[2]] = position
    # Plan connected equipment visits before routing individual legs. A free
    # through-column follows the next compatible inlet, rather than returning
    # to an arbitrary equipment centre and adding an exterior elbow.
    visited=set()
    for rid in reversed(prepared.order):
        for stage in sorted(prepared.through_stages[rid],reverse=True):
            point=prepared.stages[rid][stage];equipment=point['equipment_id']
            root_key=find(('stage',rid,stage))
            if root_key in visited:continue
            visited.add(root_key)
            unit=groups[equipment][root_key];candidates={};valid=True
            for _,owner,index in unit:
                entry=prepared.stages[owner][index];exit_point=prepared.exit_points.get((owner,index))
                if (index not in prepared.through_stages[owner] or not exit_point or
                        entry.get('allocation')=='fixed' or exit_point.get('allocation')=='fixed' or
                        index+1>=len(prepared.stages[owner])):
                    valid=False;break
                inlet=prepared.stages[owner][index+1]
                direction=_port_direction(exit_point,True)
                if not direction or direction!=_port_direction(inlet,False):valid=False;break
                axis,sign=direction;cross=1-axis
                current=result[owner,index];target=result[owner,index+1]
                if (target[axis]-current[axis])*sign<=0:valid=False;break
                box=entry.get('compartment_bounds',boxes[equipment])
                value=target[cross]
                if not box[cross]+MIN_LANE_GAP<=value<=box[cross+2]-MIN_LANE_GAP:valid=False;break
                candidate=list(current);candidate[cross]=value;candidates[owner,index]=tuple(candidate)
            if not valid or len(set(candidates.values()))!=1:continue
            candidate=next(iter(candidates.values()))
            neighbors=[result[k[1],k[2]] for other,keys in groups[equipment].items() if other!=root_key for k in keys]
            if any(max(abs(candidate[0]-p[0]),abs(candidate[1]-p[1]))<MIN_LANE_GAP-EPS for p in neighbors):continue
            result.update(candidates)
    # Work back from downstream visits. A room feeding named parallel inlet
    # columns must retain their order, not the food list's textual order.
    ordered_anchor_groups=sorted(anchor_groups.items(),key=lambda item:max(k[2] for unit in item[1].values() for k in unit),reverse=True)
    for point_id, units in ordered_anchor_groups:
        bounds, axis = anchor_ranges[point_id]
        l, t, r, bottom = bounds
        coordinate=0 if axis=='x' else 1
        def downstream(unit):
            values=[result[k[1],k[2]+1][coordinate] for k in unit if (k[1],k[2]+1) in result]
            return sum(values)/len(values) if values else (l+r)/2 if coordinate==0 else (t+bottom)/2
        ordered=sorted(units.values(),key=lambda unit:(downstream(unit),min((rank[k[1]],k[2]) for k in unit)))
        targets=[downstream(unit) for unit in ordered];low,high=(l,r) if coordinate==0 else (t,bottom)
        align=all(low<=v<=high for v in targets) and all(b-a>=MIN_LANE_GAP for a,b in zip(targets,targets[1:]))
        if len(ordered)>1 and (high-low)/(len(ordered)+1)<MIN_LANE_GAP:
            raise ValueError('設備内に独立動線の間隔を確保できません: '+point_id)
        for index, unit in enumerate(ordered):
            fraction = (index + 1) / (len(ordered) + 1)
            value=targets[index] if align else low+(high-low)*fraction
            position = (value, (t+bottom)/2) if axis == 'x' else ((l+r)/2, value)
            for key in unit: result[key[1], key[2]] = position
    departures = dict(result)
    exit_groups={}
    for key,point in prepared.exit_points.items():
        if point.get('allocation')!='fixed':
            exit_groups.setdefault((point.get('equipment_id'),point.get('side')),[]).append(key)
    exit_slots={}
    for (equipment,side),members in exit_groups.items():
        if equipment not in boxes:continue
        l,t,r,bottom=boxes[equipment];axis=0 if side in ('北接続','南接続') else 1
        members.sort(key=lambda key:(result[key][axis],rank[key[0]],key[1]))
        if len(members)>1:
            coordinates=[result[key][axis] for key in members]
            aligned=all(b-a>=MIN_LANE_GAP-EPS for a,b in zip(coordinates,coordinates[1:]))
            for i,key in enumerate(members):
                exit_slots[key]=(coordinates[i] if aligned else l+(r-l)*(i+1)/(len(members)+1) if axis==0 else t+(bottom-t)*(i+1)/(len(members)+1))
    for (rid, stage), point in prepared.exit_points.items():
        if point.get('allocation') == 'fixed': departures[rid, stage] = (float(point['x']), float(point['y']))
        else:
            equipment = point.get('equipment_id')
            if equipment not in boxes: raise ValueError('図面上の設備領域が不明です: ' + str(equipment))
            l, t, r, bottom = boxes[equipment]; x, y = result[rid, stage]
            side = point.get('side')
            if (rid,stage) in exit_slots:
                if side in ('北接続','南接続'):x=exit_slots[rid,stage]
                else:y=exit_slots[rid,stage]
            if side==prepared.stages[rid][stage].get('side'):
                # A return through the same side needs a distinct outgoing
                # lane, not a reversed retrace of the incoming stroke.
                if side in ('北接続','南接続'):
                    x=x+MIN_LANE_GAP if x+MIN_LANE_GAP<r else x-MIN_LANE_GAP
                elif side in ('西接続','東接続'):
                    y=y+MIN_LANE_GAP if y+MIN_LANE_GAP<bottom else y-MIN_LANE_GAP
            departures[rid, stage] = ((l if side == '西接続' else r if side == '東接続' else x),
                                      (t if side == '北接続' else bottom if side == '南接続' else y))
    return result, departures


def _preassign_gate_lanes(prepared, leg_rules):
    """Assign every gate lane before any connector search begins."""
    ranks = {rid: index for index, rid in enumerate(prepared.order)}
    users = {}
    for rid, legs in leg_rules.items():
        for leg, (_, gate_ids) in legs.items():
            for gate_id in gate_ids: users.setdefault(gate_id, []).append((rid, leg))
    table = {}
    for gate_id, members in users.items():
        ordered = sorted(members, key=lambda item: (ranks[item[0]], item[1], item[0]))
        for index, key in enumerate(ordered): table[key, gate_id] = (index - (len(ordered)-1)/2) * MIN_LANE_GAP
    return {(rid, leg): {gate: value for (owner, gate), value in table.items() if owner == (rid, leg)}
            for rid, legs in leg_rules.items() for leg in legs}


def _chain_course(stops, regions, barriers, gates, gate_ids, lanes, forbidden, occupied, directions=None):
    """Route declared intermediate facility points without dropping them."""
    if len(stops)==2:
        return _route(stops[0],stops[-1],regions,barriers,gates,gate_ids,lanes,forbidden,occupied,
                      directions[0][1] if directions else None,directions[-1][0] if directions else None)
    # Assign an ordered prefix of gates to each named subcourse. Endpoint
    # side alone is insufficient: a permitted corridor can require leaving
    # and re-entering the same side of a wall. Backtracking preserves that
    # possibility without silently dropping the specified crossing.
    cache={}
    def solve(index, consumed):
        key=(index,consumed)
        if key in cache:return cache[key]
        if index==len(stops)-1:
            return [stops[-1]] if consumed==len(gate_ids) else None
        candidates=[]
        for count in range(len(gate_ids)-consumed+1):
            selected=gate_ids[consumed:consumed+count]
            try:
                part=_route(stops[index],stops[index+1],regions,barriers,gates,selected,lanes,forbidden,occupied,
                            directions[index][1] if directions else None,directions[index+1][0] if directions else None)
            except ValueError:continue
            tail=solve(index+1,consumed+count)
            if tail is not None:
                joined=part+tail[1:]
                cost=sum(math.dist(a,b) for a,b in zip(joined,joined[1:]))+80*max(0,len(compact(joined))-2)
                candidates.append((cost,joined))
        cache[key]=min(candidates,key=lambda item:item[0])[1] if candidates else None
        return cache[key]
    result=solve(0,0)
    if result is None:raise ValueError('明示経由点と必須 gate 順序を満たす直交経路がありません')
    return result


def _port_direction(point, departure):
    outward={'東接続':(0,1),'西接続':(0,-1),'南接続':(1,1),'北接続':(1,-1)}.get(point.get('side'))
    if outward is None:return None
    return outward if departure else (outward[0],-outward[1])


def _join_vertices(paths, joins):
    """Materialize graph junctions only inside explicitly shared equipment.

    One connector may end on another connector's long edge. Splitting that
    edge at the same node lets the common renderer distinguish a join curve
    from an unrelated crossing. It changes no transport course.
    """
    for join in joins:
        members={join['route_id'],*join['member_route_ids']}
        selected=[path for path in paths if path['route_id'] in members]
        points={tuple(p) for path in selected for p in path['points'] if _inside(p,join['bounds'])}
        for path in selected:
            divided=[]
            for a,b in zip(path['points'],path['points'][1:]):
                axis=0 if a[0]!=b[0] else 1
                inner=[p for p in points if p[1-axis]==a[1-axis] and min(a[axis],b[axis])<p[axis]<max(a[axis],b[axis])]
                inner.sort(key=lambda p:p[axis],reverse=b[axis]<a[axis])
                if not divided:divided.append(a)
                divided.extend([list(p) for p in inner]);divided.append(b)
            path['points']=divided or path['points']
        incoming={};outgoing={}
        for path in selected:
            for a,b in zip(path['points'],path['points'][1:]):
                incoming.setdefault(tuple(b),set()).add(tuple(a));outgoing.setdefault(tuple(a),set()).add(tuple(b))
        join['junctions']=[list(p) for p in sorted(points) if
                           (len(incoming.get(p,()))>1 and len(outgoing.get(p,()))==1) or
                           (len(outgoing.get(p,()))>1 and len(incoming.get(p,()))==1)]


def _verify_geometry(geometry, prepared, leg_rules, barriers, gates):
    """Independent final gate: never paint a merely partial search result."""
    segments=[]
    for path in geometry['paths']:
        rid,leg=path['route_id'],path['leg'];points=path['points'];regions,gids=leg_rules[rid][leg]
        actual=[]
        for a,b in zip(points,points[1:]):
            if not _edge_in_regions(a,b,regions):raise ValueError('経路が指定領域の外です: '+rid)
            actual.extend(_crossed_barriers(a,b,barriers))
        if len(actual)!=len(gids) or any(w['id']!=gates[gid]['barrier_id'] or not gates[gid]['span'][0]-EPS<=at<=gates[gid]['span'][1]+EPS for (w,at),gid in zip(actual,gids)):
            raise ValueError('解決後の必須横断が指定と一致しません: '+rid)
        def direction(a,b):
            axis=0 if a[0]!=b[0] else 1
            return axis,1 if b[axis]>a[axis] else -1
        directions=[direction(a,b) for a,b in zip(points,points[1:]) if a!=b]
        inlet=_port_direction(prepared.stages[rid][leg+1],False)
        outlet=_port_direction(prepared.exit_points.get((rid,leg),prepared.stages[rid][leg]),True)
        if not directions or (outlet and directions[0]!=outlet) or (inlet and directions[-1]!=inlet):
            raise ValueError('解決後の入出方向が指定と一致しません: '+rid)
    for path in geometry['paths']+geometry['through_paths']:
        for a,b in zip(path['points'],path['points'][1:]):
            for c,d,other in segments:
                if path['route_id']==other['route_id'] or not _parallel_crowding(a,b,c,d):continue
                axis=0 if a[0]!=b[0] else 1;fixed=a[1-axis]
                low=max(min(a[axis],b[axis]),min(c[axis],d[axis]));high=min(max(a[axis],b[axis]),max(c[axis],d[axis]))
                other_fixed=c[1-axis]
                ends=([(low,fixed),(high,fixed),(low,other_fixed),(high,other_fixed)] if axis==0
                      else [(fixed,low),(fixed,high),(other_fixed,low),(other_fixed,high)])
                joined=any({path['route_id'],other['route_id']}<={j['route_id'],*j['member_route_ids']} and all(_inside(p,j['bounds']) for p in ends) for j in geometry['topology']['equipment_connections'])
                shared=bool(path.get('shared_run') and path.get('shared_run')==other.get('shared_run'))
                for section in geometry['shared_sections'].values():
                    members={tuple(m) for m in section['members']}
                    if {(path['route_id'],path.get('leg')),(other['route_id'],other.get('leg'))}<=members:
                        shared=shared or any(all((p[0]==e[0]==f[0] and min(e[1],f[1])<=p[1]<=max(e[1],f[1])) or
                                                (p[1]==e[1]==f[1] and min(e[0],f[0])<=p[0]<=max(e[0],f[0])) for p in ends)
                                            for e,f in zip(section['points'],section['points'][1:]))
                if not joined and not shared:raise ValueError('独立動線の間隔が不足しています: '+path['route_id']+' / '+other['route_id'])
            segments.append((a,b,path))


def _internal_course(a, b, entry, exit_point, boxes, occupied):
    """Use the declared inlet/outlet directions inside the actual compartment."""
    box=entry.get('compartment_bounds',boxes[entry['equipment_id']])
    if not _inside(a,box) or not _inside(b,box):
        raise ValueError('設備内通過の接続点が指定区画外です')
    l,t,r,bottom=box
    # Dynamic visits end at an interior centre: the turn is allowed there.
    # Only an inlet on the physical boundary must continue inward first.
    incoming=_port_direction(entry,False) if a[0] in (l,r) or a[1] in (t,bottom) else None
    outgoing=_port_direction(exit_point,True)
    if a==b:raise ValueError('設備内通過の入口と出口が同一座標です')
    candidates=[]
    if incoming and outgoing and incoming[0]==outgoing[0]:
        if incoming[0]==1:
            y=(t+bottom)/2;candidates.append(compact([a,(a[0],y),(b[0],y),b]))
        else:
            x=(l+r)/2;candidates.append(compact([a,(x,a[1]),(x,b[1]),b]))
    candidates.extend((compact([a,(a[0],b[1]),b]),compact([a,(b[0],a[1]),b])))
    def direction(c,d):
        axis=0 if c[0]!=d[0] else 1
        return axis,1 if d[axis]>c[axis] else -1
    for path in candidates:
        if incoming and direction(path[0],path[1])!=incoming:continue
        if outgoing and direction(path[-2],path[-1])!=outgoing:continue
        if any(_parallel_crowding(c,d,e,f) for c,d in zip(path,path[1:]) for e,f in occupied):continue
        return path
    return _route(a,b,[box],[],{},[],0,[],occupied,incoming,outgoing)

def plan(svg, manifest, request):
    """Create a route-network plan; raises ValueError for every unsafe input."""
    validate_pair(svg, manifest)
    styles = resolve_line_styles(manifest)
    prepared = prepare_process_route_rules(manifest, request, styles)
    barriers, gates, leg_rules, anchor_ranges = _validate_network(manifest, request, prepared.routes_by_id)
    root, inactive = canvas(svg, manifest, prepared)
    centres, all_boxes = _centres(svg, manifest)
    positions, departures = _network_slots(prepared, {k: v for k, v in all_boxes.items() if k not in inactive}, anchor_ranges)
    gate_lanes = _preassign_gate_lanes(prepared, leg_rules)
    joins=[]
    for child,parents in prepared.dependencies.items():
        if not parents:continue
        equipment=prepared.stages[child][0].get('equipment_id')
        if equipment:
            joins.append(dict(route_id=child,kind='branch' if prepared.routes_by_id[child].get('branch_from') else 'merge',
                              equipment_id=equipment,bounds=list(all_boxes[equipment]),member_route_ids=parents))
    paths, through_paths, transit_paths, shared_courses, occupied, shared_sections = [], [], [], {}, [], {}
    occupied_owners=[]
    for order, rid in enumerate(prepared.order):
        route = prepared.routes_by_id[rid]
        segment_styles = route.get('segment_styles', [route['style']] * (len(prepared.stages[rid]) - 1))
        for leg in range(len(prepared.stages[rid]) - 1):
            regions, gids = leg_rules[rid][leg]
            allowed_equipment = {prepared.stages[rid][leg].get('equipment_id'), prepared.stages[rid][leg + 1].get('equipment_id')}
            allowed_equipment.update(p['equipment_id'] for pair in prepared.transits.get((rid,leg),[]) for p in pair)
            forbidden = [box for equipment_id, box in all_boxes.items() if equipment_id not in allowed_equipment and equipment_id not in inactive]
            # All visits are allocated before search. Reserve other materials'
            # ports now, including those whose routes have not been drawn yet;
            # otherwise an early exit can consume a later arrival's space.
            for (owner,stage),p in sorted(set(positions.items()) | set(departures.items())):
                if owner==rid:continue
                if any({owner,rid}<={j['route_id'],*j['member_route_ids']} and _inside(p,j['bounds']) for j in joins):continue
                if any(p==positions[rid,i] for i in range(len(prepared.stages[rid]))) and prepared.shared_run_legs.get((rid,leg)):continue
                if any((rid,leg) in section['members'] and any(member[0]==owner for member in section['members'])
                       and any(p==(float(point['x']),float(point['y'])) for point in section['points'])
                       for section in prepared.shared.values()):continue
                forbidden.append((p[0]-MIN_LANE_GAP,p[1]-MIN_LANE_GAP,p[0]+MIN_LANE_GAP,p[1]+MIN_LANE_GAP))
            reserved=[]
            for (a,b),owner in zip(occupied,occupied_owners):
                shared_boxes=[join['bounds'] for join in joins
                              if join['equipment_id'] in allowed_equipment and
                              {rid,owner}<={join['route_id'],*join['member_route_ids']}]
                reserved.extend(_outside_boxes([(a,b)],shared_boxes))
            # An explicitly declared equipment through-course is distinct from
            # an omitted interior.  It is routed and checked in the same
            # corridor as its outgoing leg before the external segment exists.
            if leg in prepared.through_stages[rid]:
                internal = _internal_course(positions[rid, leg], departures[rid, leg], prepared.stages[rid][leg], prepared.exit_points[rid,leg], all_boxes,reserved)
                through_paths.append(dict(route_id=rid, stage=leg, equipment_id=prepared.stages[rid][leg]['equipment_id'], points=[list(p) for p in internal], color=route['color'], style=segment_styles[leg], dasharray=styles[segment_styles[leg]]['dasharray']))
                occupied.extend(zip(internal, internal[1:]))
                occupied_owners.extend([rid]*(len(internal)-1))
            shared_run = prepared.shared_run_legs.get((rid, leg))
            run_key = prepared.run_position.get((rid, leg))
            reused = shared_run and run_key in shared_courses
            section_ids = prepared.leg_sections.get((rid, leg), [])
            if section_ids:
                stops=[departures[rid,leg]];section_bounds=[];known=[]
                for section_id in section_ids:
                    section=prepared.shared[section_id]
                    raw=[(float(p['x']),float(p['y'])) for p in section['points']]
                    trunk=[tuple(p) for p in shared_sections[section_id]['points']] if section_id in shared_sections else raw
                    section_bounds.append((section_id,len(stops),len(stops)+len(trunk)-1))
                    stops.extend(trunk)
                    if section_id in shared_sections:known.extend(zip(trunk,trunk[1:]))
                stops.append(positions[rid,leg+1])
                shared_reserved=_unreserve_shared(reserved,known)
                points=_chain_course(stops,regions,barriers,gates,gids,gate_lanes[rid,leg],forbidden,shared_reserved)
                search_from=0
                for section_id,first,last in section_bounds:
                    begin=points.index(stops[first],search_from)
                    finish=points.index(stops[last],begin)
                    trunk=points[begin:finish+1];search_from=finish
                    if section_id in shared_sections:
                        if compact(trunk)!=compact([tuple(p) for p in shared_sections[section_id]['points']]):
                            raise ValueError('共有区間を他の材料と同じ経路で描画できません: '+section_id)
                    else:
                        shared_sections[section_id]=dict(points=[list(p) for p in trunk],members=prepared.shared[section_id]['members'],route_id=rid,leg=leg,color=route['color'],style=segment_styles[leg],dasharray=styles[segment_styles[leg]]['dasharray'])
            elif shared_run and run_key in shared_courses:
                points = shared_courses[run_key]
            else:
                transit_stops = []
                first=prepared.exit_points.get((rid,leg),prepared.stages[rid][leg])
                directions=[(None,_port_direction(first,True))]
                for entry, exit_point in prepared.transits.get((rid, leg), []):
                    transit_stops.extend(((float(entry['x']), float(entry['y'])), (float(exit_point['x']), float(exit_point['y']))))
                    directions.extend([(_port_direction(entry,False),_port_direction(entry,False)),(_port_direction(exit_point,True),_port_direction(exit_point,True))])
                for name in prepared.layout.get('via',{}).get(rid,{}).get(str(leg),[]):
                    point=next(p for p in manifest['points'] if name in [p['name'],*p['aliases']])
                    transit_stops.append((float(point['x']),float(point['y'])));directions.append((None,None))
                directions.append((_port_direction(prepared.stages[rid][leg+1],False),None))
                try:
                    points = _chain_course([departures[rid, leg], *transit_stops, positions[rid, leg + 1]], regions, barriers, gates, gids, gate_lanes[rid, leg], forbidden, reserved,directions)
                except ValueError as exc:
                    raise ValueError(f'{rid} 区間{leg+1}: {exc}') from exc
                for entry, exit_point in prepared.transits.get((rid, leg), []):
                    a=(float(entry['x']),float(entry['y']));b=(float(exit_point['x']),float(exit_point['y']))
                    first=points.index(a);last=points.index(b,first)
                    part=points[first:last+1]
                    if not all(_inside(p,all_boxes[entry['equipment_id']]) for p in part):
                        raise ValueError(f'{rid}: 指定した設備内通過が設備外へ出ています')
                    transit_paths.append(dict(route_id=rid, leg=leg, equipment_id=entry['equipment_id'], points=[list(p) for p in part], color=route['color'], style=segment_styles[leg], dasharray=styles[segment_styles[leg]]['dasharray']))
                if shared_run:
                    shared_courses[run_key] = points
            record = dict(route_id=rid, leg=leg, points=[list(p) for p in points], color=route['color'], style=segment_styles[leg], dasharray=styles[segment_styles[leg]]['dasharray'])
            if shared_run:
                record['shared_run'] = shared_run
            paths.append(record)
            if not reused:
                occupied.extend(zip(points, points[1:]))
                occupied_owners.extend([rid]*(len(points)-1))
    geometry = dict(paths=paths, through_paths=through_paths, transit_paths=transit_paths, connection_paths=[], shared_sections=shared_sections, crossings=[], labels=[], topology=dict(shared_runs=prepared.run_topology, shared_sections=[dict(id=section_id, points=[point['name'] for point in section['points']], members=[dict(route_id=route_id, leg=leg) for route_id, leg in section['members']]) for section_id, section in prepared.shared.items()], equipment_connections=[]), stage_order={rid: route['stages'] for rid, route in prepared.routes_by_id.items()}, route_order=prepared.order, ports={rid:[list(positions[rid, i]) for i in range(len(points))] for rid, points in prepared.stages.items()}, exit_ports={rid:{str(i):list(departures[rid, i]) for owner, i in prepared.exit_points if owner == rid} for rid in prepared.routes_by_id}, dependencies=prepared.dependencies, flow_network=True)
    geometry['labels']=_labels(prepared,positions,manifest,all_boxes)
    geometry['mode']='network'
    geometry['topology']['equipment_connections']=joins
    _join_vertices(paths+through_paths,joins)
    _verify_geometry(geometry,prepared,leg_rules,barriers,gates)
    paint(root, geometry, prepared)
    import xml.etree.ElementTree as E
    return dict(ok=True, svg=E.tostring(root, encoding='unicode'), geometry=geometry, issues=[])
