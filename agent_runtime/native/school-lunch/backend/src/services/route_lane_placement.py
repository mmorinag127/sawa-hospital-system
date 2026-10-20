"""Place parallel lanes locally after obstacle-only route discovery.

This never edits process topology. A local shift is accepted only if its
endpoints, port directions and ordered doorway crossings remain unchanged.
"""
from .process_route_layout_service import compact
from .process_route_network_service import (
    EPS, MIN_LANE_GAP, _crossed_barriers, _line_intervals, _line_edge_allowed,
)


def segments(path):
    return list(zip(path, path[1:]))


def direction(a, b):
    axis = 0 if a[0] != b[0] else 1
    return axis, 1 if b[axis] > a[axis] else -1


def reserved_segments(base, prior_paths):
    """Keep a shared fixed port's minimum straight approach out of lane cost.

    Only an identical endpoint is shared, not the interior of an unrelated
    route. Both paths still terminate at the original, unmodified port.
    """
    result = []
    ends = (base[0], base[-1])
    for path in prior_paths:
        for i, (a,b) in enumerate(segments(path)):
            axis, sign = direction(a,b)
            a,b = list(a),list(b)
            length = abs(b[axis]-a[axis])
            trim_start = MIN_LANE_GAP if i == 0 and path[0] in ends else 0
            trim_end = MIN_LANE_GAP if i == len(path)-2 and path[-1] in ends else 0
            if length <= trim_start+trim_end+EPS:
                continue
            a[axis] += trim_start*sign
            b[axis] -= trim_end*sign
            result.append((tuple(a),tuple(b)))
    return result


def crossing_signature(path, barriers, gates):
    result = []
    for a, b in segments(path):
        for wall, at in _crossed_barriers(a, b, barriers):
            matches = tuple(sorted(g['id'] for g in gates.values()
                if g['barrier_id'] == wall['id'] and g['span'][0]-EPS <= at <= g['span'][1]+EPS))
            if not matches:
                return None
            result.append((wall['id'], matches, direction(a, b)))
    return tuple(result)


def valid_path(path, base, geometry, gates, *, preserve_directions=(True, True)):
    if len(path) < 2 or path[0] != base[0] or path[-1] != base[-1]:
        return False
    if ((preserve_directions[0] and direction(*path[:2]) != direction(*base[:2]))
            or (preserve_directions[1] and direction(*path[-2:]) != direction(*base[-2:]))):
        return False
    for i, (a, b) in enumerate(segments(path)):
        if a == b or (a[0] != b[0] and a[1] != b[1]):
            return False
        axis, sign = direction(a, b)
        if i and direction(path[i-1], a) == (axis, -sign):
            return False
        fixed = a[1-axis]
        key = (axis, fixed)
        if key not in geometry['coverage']:
            geometry['coverage'][key] = _line_intervals(geometry['regions'], axis, fixed)
            geometry['obstacles'][key] = _line_intervals(geometry['forbidden'], axis, fixed, obstacles=True)
        lo, hi = sorted((a[axis], b[axis]))
        if not _line_edge_allowed(lo, hi, geometry['coverage'][key], geometry['obstacles'][key]):
            return False
    barriers = [dict(id=w[0], axis=w[1], position=w[2], span=w[3]) for w in geometry['barriers']]
    # No corners on/along a wall: otherwise _crossed_barriers cannot see a
    # crossing split into two segments at a vertex on that wall.
    for p in path:
        for w in barriers:
            axis = 0 if w['axis'] == 'x' else 1
            if abs(p[axis]-w['position']) < 6-EPS and w['span'][0]-EPS <= p[1-axis] <= w['span'][1]+EPS:
                return False
    signature = crossing_signature(path, barriers, gates)
    return signature is not None and signature == crossing_signature(base, barriers, gates)


def shift_segment(path, index, offset, *, preserve_directions=(True, True)):
    """Translate a supporting line; intersect its two neighbouring lines.

    Port coordinates are fixed. End runs get one lane-width straight stub
    before a perpendicular fan-out, never a diagonal connection.
    """
    a, b = path[index:index+2]
    axis, sign = direction(a, b)
    fixed = 1-axis
    moved_a, moved_b = list(a), list(b)
    moved_a[fixed] += offset
    moved_b[fixed] += offset
    prefix, suffix = list(path[:index]), list(path[index+2:])
    if index == 0:
        stub = list(a)
        if preserve_directions[0]:
            stub[axis] += sign*MIN_LANE_GAP
        moved_a[axis] = stub[axis]
        prefix = [a, tuple(stub)]
    if index == len(path)-2:
        stub = list(b)
        if preserve_directions[1]:
            stub[axis] -= sign*MIN_LANE_GAP
        moved_b[axis] = stub[axis]
        suffix = [tuple(stub), b]
    if (moved_b[axis]-moved_a[axis])*sign <= EPS:
        return None
    return compact([*prefix, tuple(moved_a), tuple(moved_b), *suffix])


def overlap_length(a, b, c, d):
    axis, _ = direction(a, b)
    fixed = 1-axis
    if c[fixed] != d[fixed] or abs(a[fixed]-c[fixed]) >= MIN_LANE_GAP-EPS:
        return 0.0
    return max(0.0, min(max(a[axis],b[axis]),max(c[axis],d[axis])) - max(min(a[axis],b[axis]),min(c[axis],d[axis])))


def conflict_length(path, occupied):
    return sum(overlap_length(a,b,c,d) for a,b in segments(path) for c,d in occupied)


def simplify_path(path, base, occupied, geometry, gates, *, preserve_directions=(True, True)):
    """Remove lane-placement doglegs without changing endpoints or gate order.

    Every shortcut remains orthogonal and passes the same equipment, wall,
    doorway, port-direction and occupied-lane checks as the original path.
    """
    def cost(points):
        return 80*max(0,len(points)-2) + sum(abs(b[0]-a[0])+abs(b[1]-a[1]) for a,b in segments(points))
    while len(path)>2:
        current=conflict_length(path,occupied)
        best=None
        for i in range(len(path)-2):
            for j in range(i+2,len(path)):
                a,b=path[i],path[j]
                for corner in ((a[0],b[1]),(b[0],a[1])):
                    candidate=compact([*path[:i+1],corner,*path[j:]])
                    if cost(candidate)>=cost(path)-EPS:
                        continue
                    if not valid_path(candidate,base,geometry,gates,preserve_directions=preserve_directions):
                        continue
                    if conflict_length(candidate,occupied)>current+EPS:
                        continue
                    rank=(cost(candidate),len(candidate),candidate)
                    if best is None or rank<best[0]:
                        best=rank,candidate
        if best is None:
            break
        path=best[1]
    return path


def nudge_path(base, occupied, geometry, gates, *, preserve_directions=(True, True)):
    """Move conflicting runs to the closest free parallel lane.

    Occupancy supplies lane boundaries, not new global graph coordinates.
    Strictly decreasing conflict length guarantees termination. If legal local
    shifts cannot resolve a conflict, the caller may re-route that edge only.
    """
    path = base
    score = conflict_length(path, occupied)
    while score > EPS:
        best = None
        for i, (a, b) in enumerate(segments(path)):
            if not any(overlap_length(a,b,c,d) > EPS for c,d in occupied):
                continue
            axis, _ = direction(a,b)
            fixed = 1-axis
            offsets = {c[fixed]+gap-a[fixed] for c,d in occupied
                if c[fixed] == d[fixed]
                and min(max(a[axis],b[axis]),max(c[axis],d[axis])) > max(min(a[axis],b[axis]),min(c[axis],d[axis]))+EPS
                for gap in (-MIN_LANE_GAP, MIN_LANE_GAP)}
            for offset in sorted(offsets, key=lambda v:(abs(v),v)):
                if abs(offset) < EPS:
                    continue
                candidate = shift_segment(path,i,offset,preserve_directions=preserve_directions)
                if candidate is None or not valid_path(candidate,base,geometry,gates,preserve_directions=preserve_directions):
                    continue
                candidate_score = conflict_length(candidate,occupied)
                if candidate_score >= score-EPS:
                    continue
                rank = (candidate_score, len(candidate), abs(offset))
                if best is None or rank < best[0]:
                    best = (rank, candidate)
        if best is None:
            break
        path = best[1]
        score = best[0][0]
    return path, score
