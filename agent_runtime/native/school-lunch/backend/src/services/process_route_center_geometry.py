"""Orthogonal drawing primitives; no menu, date or material-specific geometry."""
from collections import defaultdict
import math
import xml.etree.ElementTree as E

from .named_svg_route_service import render, _tag


def canvas(svg, manifest, prepared):
    from .process_route_layout_service import visible_text
    req = prepared.request
    rich = {f['id'] for f in manifest['fields'] for key in req.get('field_runs', {}) if key in (f['id'], f['name'])}
    blanks = {f['id']: '' for f in manifest['fields'] if f['id'] not in rich and not (f['id'] == 'food_names' and req.get('legend_placement') == 'food_names')}
    legacy = {k: v for k, v in req.items() if k in ('template_id', 'template_version', 'field_runs', 'legend_placement')}
    legacy.update(fields=blanks | req.get('fields', {}), routes=prepared.legacy_routes)
    root = E.fromstring(render(svg, manifest, legacy))
    for el in list(root):
        if el.get('id') in ('named-route-layer', 'named-route-annotations'):
            root.remove(el)
    selected = set(prepared.layout.get('equipment_overlays', []))
    optional = [el for el in root.iter() if el.get('data-optional-equipment') == 'true']
    optional_ids = {e.get('id') for el in optional for e in el.iter()}
    if selected - optional_ids:
        raise ValueError('図面追加設備が不明です')
    inactive = set()
    for el in optional:
        ids = {e.get('id') for e in el.iter() if e.get('id')}
        if ids & selected:
            el.attrib.pop('display', None)
            el.attrib.pop('style', None)
        else:
            inactive |= ids
    used = {p.get('equipment_id') for points in prepared.stages.values() for p in points}
    used |= {p.get('equipment_id') for pairs in prepared.transits.values() for pair in pairs for p in pair}
    if used & inactive:
        raise ValueError('参照する追加設備を図面設定で明示選択してください')
    moved = set()
    for move in prepared.layout.get('text_positions', []):
        matches = [el for el in root.iter(_tag('text')) if visible_text(el) == move['text'] and ('element_id' not in move or el.get('id') == move['element_id'])]
        anchors = [p for p in manifest['points'] if move['at'] in (p['name'], *p['aliases']) and p['kind'] == 'label_anchor']
        if len(matches) != 1 or len(anchors) != 1 or id(matches[0]) in moved:
            raise ValueError('一意の見出しと見出し用座標を指定してください')
        el, anchor = matches[0], anchors[0]
        dx, dy = anchor['x'] - float(el.get('x')), anchor['y'] - float(el.get('y'))
        for child in el.iter():
            for axis, delta in (('x', dx), ('y', dy)):
                if child.get(axis) is not None:
                    child.set(axis, str(float(child.get(axis)) + delta))
        moved.add(id(el))
    return root, inactive


def segment_box(a, b, box):
    """Liang-Barsky interior intersection, including diagonal segments."""
    l, t, r, bottom = box
    if max(a[0],b[0])<=l or min(a[0],b[0])>=r or max(a[1],b[1])<=t or min(a[1],b[1])>=bottom:
        return False
    l += 1e-5; t += 1e-5; r -= 1e-5; bottom -= 1e-5
    lo, hi = 0., 1.
    for origin, delta, minimum, maximum in ((a[0], b[0]-a[0], l, r), (a[1], b[1]-a[1], t, bottom)):
        if abs(delta) < 1e-9:
            if not minimum <= origin <= maximum:
                return False
        else:
            x, y = sorted(((minimum-origin)/delta, (maximum-origin)/delta))
            lo, hi = max(lo, x), min(hi, y)
            if lo > hi:
                return False
    return hi > 0 and lo < 1


def intersection(a, b, c, d):
    vx, vy = b[0]-a[0], b[1]-a[1]
    wx, wy = d[0]-c[0], d[1]-c[1]
    denom = vx*wy-vy*wx
    if abs(denom) < 1e-8:
        return None
    qx, qy = c[0]-a[0], c[1]-a[1]
    u, v = (qx*wy-qy*wx)/denom, (qx*vy-qy*vx)/denom
    if 1e-5 < u < 1-1e-5 and 1e-5 < v < 1-1e-5:
        return (a[0]+u*vx, a[1]+u*vy)
    return None


def overlap(a, b, c, d):
    """Length of collinear overlap (not just coincident endpoints)."""
    length = math.dist(a, b)
    if length < 1e-6:
        return 0.
    ux, uy = (b[0]-a[0])/length, (b[1]-a[1])/length
    if any(abs((p[0]-a[0])*uy-(p[1]-a[1])*ux) > 1.8 for p in (c, d)):
        return 0.
    lo, hi = sorted(((c[0]-a[0])*ux+(c[1]-a[1])*uy, (d[0]-a[0])*ux+(d[1]-a[1])*uy))
    return max(0., min(length, hi)-max(0., lo))


def free_path(start, end, obstacles, domain, occupied=()):
    """Connect centre visits using horizontal/vertical strokes only.

    Test straight/L/Z courses first, then the existing rectilinear visibility
    search for genuinely obstructed courses. Fixed side-port geometry is never
    restored. A rejected course cannot escape the invariant with a diagonal.
    """
    from .process_route_layout_service import compact, route_leg
    start, end = tuple(start), tuple(end)
    inside = lambda p: domain[0] <= p[0] <= domain[2] and domain[1] <= p[1] <= domain[3]
    if not inside(start) or not inside(end):
        raise ValueError('設備通過点が指定された描画領域の外です')
    if start == end:
        return [start]
    xs={start[0],end[0],(start[0]+end[0])/2}
    ys={start[1],end[1],(start[1]+end[1])/2}
    for l,t,r,b in obstacles:xs.update((l-6,r+6));ys.update((t-6,b+6))
    for a,b in occupied:
        for p in (a,b):xs.update((p[0]-10,p[0]+10));ys.update((p[1]-10,p[1]+10))
    proposals=[[start,(start[0],end[1]),end],[start,(end[0],start[1]),end]]
    proposals += [[start,(x,start[1]),(x,end[1]),end] for x in sorted(xs) if domain[0]<=x<=domain[2]]
    proposals += [[start,(start[0],y),(end[0],y),end] for y in sorted(ys) if domain[1]<=y<=domain[3]]
    valid=[]
    for points in proposals:
        points=compact(points)
        if any(not inside(p) for p in points):continue
        edges=list(zip(points,points[1:]))
        if any(segment_box(a,b,box) for a,b in edges for box in obstacles):continue
        if any(overlap(a,b,c,d)>1e-6 for a,b in edges for c,d in occupied):continue
        crossings=sum(bool(intersection(a,b,c,d)) for a,b in edges for c,d in occupied)
        length=sum(math.dist(a,b) for a,b in edges)
        valid.append((length+160*max(0,len(points)-2)+60*crossings,points))
    # Prefer progress toward the next visit. Fewer corners obtained by first
    # going backwards around the far side of equipment are not a readable win.
    monotone=[v for v in valid if all(min(start[0],end[0])<=p[0]<=max(start[0],end[0]) and min(start[1],end[1])<=p[1]<=max(start[1],end[1]) for p in v[1])]
    if monotone:return min(monotone)[1]
    forward_domain=[min(start[0],end[0]),min(start[1],end[1]),max(start[0],end[0]),max(start[1],end[1])]
    forward=route_leg(start,end,obstacles,occupied,forward_domain,parallel_gap=3,bend_cost=160)
    if forward is not None:return forward
    if valid:return min(valid)[1]
    result=route_leg(start,end,obstacles,occupied,domain,parallel_gap=3,bend_cost=160)
    if result is None:raise ValueError('指定領域内で設備・文字を避ける動線がありません')
    return result


def auto_sections(prepared, positions, boxes, domain):
    """Preserve declared merge membership/order, derive junctions from flow.

    Historical waypoint coordinates describe the old drawing, not preparation
    steps. Every declared shared section is a distinct trunk in the new graph.
    """
    successors={}; predecessors=defaultdict(set)
    for sections in prepared.leg_sections.values():
        for a,b in zip(sections,sections[1:]):
            if a in successors and successors[a]!=b:
                raise ValueError('共有区間の接続先が一意ではありません')
            successors[a]=b;predecessors[b].add(a)
    joins={}; finishes={}
    def place(sid,active=()):
        if sid in joins:return
        if sid in active:raise ValueError('共有区間が循環しています')
        members=prepared.shared[sid]['members']
        if sid in successors:
            nxt=successors[sid];place(nxt,active+(sid,));end=joins[nxt]
        else:
            ends={tuple(positions[rid,leg+1]) for rid,leg in members}
            if prepared.shared[sid]['points'][-1].get('equipment_id'):
                if len(ends)!=1:raise ValueError('共有区間の終点が一致していません')
                end=next(iter(ends))
            else:
                # A declared interior common section can split again before
                # the next equipment. Preserve each member's own destination.
                targets=(sum(p[0] for p in ends)/len(ends),sum(p[1] for p in ends)/len(ends))
                starts=[positions[rid,leg] for rid,leg in members]
                source=(sum(p[0] for p in starts)/len(starts),sum(p[1] for p in starts)/len(starts))
                end=((source[0]+3*targets[0])/4,(source[1]+3*targets[1])/4)
        starts=[positions[rid,leg] for rid,leg in members]
        avg=(sum(p[0] for p in starts)/len(starts),sum(p[1] for p in starts)/len(starts))
        # Halfway toward downstream keeps successive merges in forward order.
        candidate=((avg[0]+end[0])/2,(avg[1]+end[1])/2)
        containing=[box for box in boxes.values() if segment_box(candidate,candidate,box)]
        if containing:
            choices=[(x,y) for l,t,r,b in containing for x,y in ((l-8,candidate[1]),(r+8,candidate[1]),(candidate[0],t-8),(candidate[0],b+8))]
            choices=[p for p in choices if domain[0]<=p[0]<=domain[2] and domain[1]<=p[1]<=domain[3] and not any(segment_box(p,p,b) for b in boxes.values())]
            if not choices:raise ValueError('合流点を設備外に配置できません')
            candidate=min(choices,key=lambda p:math.dist(avg,p)+math.dist(p,end))
        joins[sid]=candidate;finishes[sid]=end
    for sid in prepared.shared:place(sid)
    return {sid:(joins[sid],finishes[sid]) for sid in joins}
