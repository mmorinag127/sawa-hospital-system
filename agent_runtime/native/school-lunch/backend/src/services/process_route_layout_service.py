"""Generic diagram layout. Business stages are immutable; geometry is derived.

All inputs are explicit operator data. Obstacles are drawing objects, not a
physical/hygiene path model. Failure returns a marked, non-exportable preview.
"""
from collections import defaultdict
from copy import deepcopy
from bisect import bisect_left, bisect_right
import heapq
import json
import math
import re
import xml.etree.ElementTree as E

from .named_svg_route_service import render, validate_pair, resolve_line_styles, _equipment_bounds, _tag, _number, inactive_area_equipment
from .process_route_rules_service import prepare_process_route_rules

GAP = 8.0
DOMAIN = (304., 78., 1885., 1188.)


def intersects(a, b, pad=0):
    return a[0] < b[2]+pad and a[2] > b[0]-pad and a[1] < b[3]+pad and a[3] > b[1]-pad


def hits(a, b, box, pad=0):
    x,y,u,v = box[0]-pad,box[1]-pad,box[2]+pad,box[3]+pad
    if a[0] == b[0]:
        return x < a[0] < u and max(min(a[1],b[1]),y) < min(max(a[1],b[1]),v)
    return y < a[1] < v and max(min(a[0],b[0]),x) < min(max(a[0],b[0]),u)


def text_boxes(root):
    parents={c:p for p in root.iter() for c in p}
    result=[]
    for el in root.iter(_tag('text')):
        ancestors=[]; cur=el
        while cur is not None:
            ancestors.append(cur);cur=parents.get(cur)
        if any('display:none' in p.get('style','').replace(' ','') or p.get('display')=='none' for p in ancestors):continue
        rows=list(el.findall(_tag('tspan'))) or [el]
        cursor_x=float(el.get('x','0'));cursor_y=float(el.get('y','0'))
        for row in rows:
            s=visible_text(row)
            if not s or row.get('x',el.get('x')) is None:continue
            style=';'.join(p.get('style','') for p in ancestors)
            sizes=re.findall(r'font-size:\s*([\d.]+)',style)
            size=float(sizes[0] if sizes else row.get('font-size',el.get('font-size','16')))
            x=float(row.get('x',cursor_x))+float(row.get('dx','0'))
            y=float(row.get('y',cursor_y))+float(row.get('dy','0'))
            w=sum(size*(.62 if ord(c)<256 else 1) for c in s)
            if el.get('text-anchor')=='middle':x-=w/2
            # Baseline em box: glyph ascenders fit inside the em; do not invent
            # extra padding above a field's authoritative top boundary.
            result.append((x-1,y-size,x+w+1,y+2))
            cursor_x=x+w;cursor_y=y
    return result


def visible_text(element):
    """Ignore XML pretty-print indentation, not meaningful text whitespace."""
    pieces=[]
    def add(value):
        if value and not (value.isspace() and ('\n' in value or '\r' in value)):pieces.append(value)
    add(element.text)
    for child in element:
        pieces.append(visible_text(child));add(child.tail)
    return ''.join(pieces)


def compact(path):
    out=[]
    for p in path:
        if out and p==out[-1]:continue
        if len(out)>1 and (out[-2][0]==out[-1][0]==p[0] or out[-2][1]==out[-1][1]==p[1]) and (out[-1][0]-out[-2][0])*(p[0]-out[-1][0])+(out[-1][1]-out[-2][1])*(p[1]-out[-1][1])>0:out.pop()
        out.append(p)
    return out


def junction_pieces(a,b,curve):
    """Local tangent curves at either end, with a straight middle.

    A private edge can split from one trunk and merge into another. Those are
    two junctions, not one long curve or a change to the declared topology.
    """
    length=math.dist(a,b);ux,uy=((b[0]-a[0])/length,(b[1]-a[1])/length)
    start,end=curve.get('start'),curve.get('end')
    rs=min(start[2],length/3) if start else 0
    re=min(end[2],length/3) if end else 0
    first=(a[0]+ux*rs,a[1]+uy*rs)
    last=(b[0]-ux*re,b[1]-uy*re)
    parts=[]
    if start:
        tx,ty=start[1]
        parts.append(dict(kind=start[3],points=[tuple(a),(a[0]+tx*rs/2,a[1]+ty*rs/2),(first[0]-ux*rs/2,first[1]-uy*rs/2),first]))
    if first!=last:parts.append(dict(kind='line',points=[first,last]))
    if end:
        tx,ty=end[1]
        parts.append(dict(kind=end[3],points=[last,(last[0]+ux*re/2,last[1]+uy*re/2),(b[0]-tx*re/2,b[1]-ty*re/2),tuple(b)]))
    return parts


def crossing(a,b,c,d):
    if a[0]==b[0] and c[1]==d[1]:
        p=(a[0],c[1])
        return p if min(a[1],b[1])<p[1]<max(a[1],b[1]) and min(c[0],d[0])<p[0]<max(c[0],d[0]) else None
    if a[1]==b[1] and c[0]==d[0]:return crossing(c,d,a,b)
    return None


def near_parallel(a,b,c,d):
    vertical=a[0]==b[0]
    if vertical != (c[0]==d[0]):return False
    axis=1 if vertical else 0; fixed=1-axis
    return abs(a[fixed]-c[fixed])<GAP-.01 and max(min(a[axis],b[axis]),min(c[axis],d[axis])) < min(max(a[axis],b[axis]),max(c[axis],d[axis]))-.01


def drawing_boundaries(root):
    """Read orthogonal master walls; permit crossings, never tracing a wall.

    These strokes are visual clearance only, not inferred doors or hygiene
    rules. Unsupported master geometry is rejected rather than ignored.
    """
    edges=[]
    for group in root.iter(_tag('g')):
        if group.get('id') not in ('walls','zone-boundaries'):continue
        for el in group:
            if el.tag!=_tag('path'):continue
            tokens=re.findall(r'[A-Za-z]|[-+]?(?:\d*\.\d+|\d+)',el.get('d',''))
            i=0;point=None;origin=None
            while i<len(tokens):
                command=tokens[i];i+=1
                if command in ('M','L'):
                    dest=(float(tokens[i]),float(tokens[i+1]));i+=2
                elif command=='H':dest=(float(tokens[i]),point[1]);i+=1
                elif command=='V':dest=(point[0],float(tokens[i]));i+=1
                elif command=='Z':dest=origin
                else:raise ValueError('壁・区域境界は直交するM/L/H/V/Zで指定してください')
                if command=='M':origin=dest
                elif point!=dest:
                    if point[0]!=dest[0] and point[1]!=dest[1]:raise ValueError('斜めの区域境界は未対応です')
                    edges.append((point,dest))
                point=dest
    return edges


def route_leg(start,end,obstacles,occupied,domain=DOMAIN,start_axis=0,end_axis=0,start_heading=0,end_heading=0,boundaries=(),end_span=None,parallel_gap=GAP,bend_cost=48):
    """Rectilinear visibility grid, crossing/bend cost, hard parallel clearance."""
    if domain[0]>domain[2] or domain[1]>domain[3] or any(not domain[0]<=x<=domain[2] or not domain[1]<=y<=domain[3] for x,y in (start,end)):return None
    if start==end:return [start]
    if any(l<p[0]<r and t<p[1]<b for p in (start,end) for l,t,r,b in obstacles):return None
    xs={start[0],end[0],domain[0],domain[2]};ys={start[1],end[1],domain[1],domain[3]}
    for l,t,r,b in obstacles:
        xs.update((l-3,r+3));ys.update((t-3,b+3))
    for a,b in [*occupied,*boundaries]:
        xs.update((a[0]-parallel_gap,a[0]+parallel_gap,b[0]-parallel_gap,b[0]+parallel_gap))
        ys.update((a[1]-parallel_gap,a[1]+parallel_gap,b[1]-parallel_gap,b[1]+parallel_gap))
    if end_span:
        varying,lo,hi=end_span
        (xs if varying==0 else ys).update((lo,hi))
    xs=sorted(x for x in xs if domain[0]<=x<=domain[2]);ys=sorted(y for y in ys if domain[1]<=y<=domain[3])
    if start[0] not in xs or end[0] not in xs or start[1] not in ys or end[1] not in ys:return None
    s=(xs.index(start[0]),ys.index(start[1]),start_heading);goal=(xs.index(end[0]),ys.index(end[1]))
    goals={goal}
    if end_span:
        varying,lo,hi=end_span
        goals={(i,goal[1]) for i,x in enumerate(xs) if lo<=x<=hi} if varying==0 else {(goal[0],j) for j,y in enumerate(ys) if lo<=y<=hi}
    axis=lambda d:1 if d in (1,2) else 2 if d else 0
    opposite={1:2,2:1,3:4,4:3}
    def intervals(values):
        merged=[]
        for lo,hi in sorted(values):
            if merged and lo<merged[-1][1]:merged[-1]=(merged[-1][0],max(hi,merged[-1][1]))
            else:merged.append((lo,hi))
        return [v[0] for v in merged],[v[1] for v in merged]
    horizontal=[(min(a[0],b[0]),max(a[0],b[0]),a[1]) for a,b in occupied if a[1]==b[1]]
    vertical=[(min(a[1],b[1]),max(a[1],b[1]),a[0]) for a,b in occupied if a[0]==b[0]]
    rows=[intervals([(l,r) for l,t,r,b in obstacles if t<y<b]+[(l,r) for l,r,v in horizontal if abs(y-v)<parallel_gap-.01]) for y in ys]
    cols=[intervals([(t,b) for l,t,r,b in obstacles if l<x<r]+[(t,b) for t,b,v in vertical if abs(x-v)<parallel_gap-.01]) for x in xs]
    row_cross=[sorted(v for t,b,v in vertical if t<y<b) for y in ys]
    col_cross=[sorted(v for l,r,v in horizontal if l<x<r) for x in xs]
    def blocked(low,high,collection):
        starts,ends=collection;n=bisect_left(starts,high)-1
        return n>=0 and ends[n]>low
    if end_heading:
        gi,gj=goal;di,dj={1:(-1,0),2:(1,0),3:(0,-1),4:(0,1)}[end_heading]
        ni,nj=gi+di,gj+dj
        if not 0<=ni<len(xs) or not 0<=nj<len(ys):return None
        lo,hi=sorted((xs[ni],xs[gi]) if di else (ys[nj],ys[gj]))
        if blocked(lo,hi,rows[gj] if di else cols[gi]):return None
    queue=[(0.,0.,s)];cost={s:0.};previous={};valid={}
    while queue:
        _,g,node=heapq.heappop(queue)
        if g!=cost.get(node):continue
        i,j,direction=node
        if (i,j) in goals and (not end_axis or axis(direction)==end_axis) and (not end_heading or direction==end_heading):
            path=[]
            while node in previous:
                path.append((xs[node[0]],ys[node[1]]));node=previous[node]
            path.append(start);return compact(path[::-1])
        for di,dj,newdir in ((1,0,1),(-1,0,2),(0,1,3),(0,-1,4)):
            if direction and newdir==opposite[direction]:continue
            ni,nj=i+di,j+dj
            if not 0<=ni<len(xs) or not 0<=nj<len(ys):continue
            a=(xs[i],ys[j]);b=(xs[ni],ys[nj]);key=tuple(sorted((a,b)))
            if key not in valid:
                low,high=sorted((a[0],b[0]) if dj==0 else (a[1],b[1]));collection=rows[j] if dj==0 else cols[i]
                crosses=row_cross[j] if dj==0 else col_cross[i]
                if blocked(low,high,collection):valid[key]=None
                else:
                    # Walls are visible schematic boundaries, not physical
                    # routing bans. Crossing is fine; following them is costly.
                    wall_length=sum(max(0,min(high,max(c[0 if dj==0 else 1],e[0 if dj==0 else 1]))-max(low,min(c[0 if dj==0 else 1],e[0 if dj==0 else 1]))) for c,e in boundaries if near_parallel(a,b,c,e))
                    valid[key]=8*(bisect_left(crosses,high)-bisect_left(crosses,low))+40*wall_length
            if valid[key] is None:continue
            prior_axis=axis(direction) or start_axis
            nc=g+abs(a[0]-b[0])+abs(a[1]-b[1])+valid[key]+(bend_cost if prior_axis and prior_axis!=axis(newdir) else 0)
            nxt=(ni,nj,newdir)
            if nc<cost.get(nxt,math.inf):
                cost[nxt]=nc;previous[nxt]=node
                if end_span:
                    varying,lo,hi=end_span
                    h=abs(b[1-varying]-end[1-varying])+max(lo-b[varying],0,b[varying]-hi)
                else:h=abs(b[0]-end[0])+abs(b[1]-end[1])
                heapq.heappush(queue,(nc+h,nc,nxt))
    return None


def route_course(anchors,obstacles,occupied,domain,start_axis=0,end_axis=0,boundaries=()):
    """Plan named waypoints together, carrying direction across their joins.

    Selecting each leg independently creates a cheapest local elbow and then
    immediately reverses it on the next leg. Keep both arrival-axis candidates
    until the whole course is evaluated; never remove a named waypoint.
    """
    states={0:(0,[tuple(anchors[0])])}
    for index,(a,b) in enumerate(zip(anchors,anchors[1:])):
        next_states={}
        arrivals=[1,2] if index==len(anchors)-2 and end_axis==1 else [3,4] if index==len(anchors)-2 and end_axis==2 else [1,2,3,4]
        for arrival in arrivals:
            for direction,(cost,prefix) in states.items():
                part=route_leg(tuple(a),tuple(b),obstacles,occupied,domain,start_axis=start_axis if index==0 else 0,start_heading=direction,end_heading=arrival,boundaries=boundaries)
                if part is None:continue
                axes=[1 if c[1]==d[1] else 2 for c,d in zip(part,part[1:])]
                length=sum(abs(c[0]-d[0])+abs(c[1]-d[1]) for c,d in zip(part,part[1:]))
                prior_axis=(1 if direction in (1,2) else 2) if direction else start_axis
                bends=sum(c!=d for c,d in zip(([prior_axis] if prior_axis else [])+axes,axes if prior_axis else axes[1:]))
                score=cost+length+48*bends
                if arrival not in next_states or score<next_states[arrival][0]:next_states[arrival]=(score,prefix+part[1:])
        states=next_states
        if not states:return None
    return min(states.values(),key=lambda s:s[0])[1]


def _plan_once(svg_bytes,manifest,request):
    validate_pair(svg_bytes,manifest)
    # Line appearance is master data. Do not let a request invent a third
    # semantic line type or silently use an obsolete hard-coded dash pattern.
    line_styles=resolve_line_styles(manifest)
    domain=manifest.get('routing_bounds')
    if not isinstance(domain,list) or len(domain)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in domain) or not (0<=domain[0]<domain[2]<=1900 and 0<=domain[1]<domain[3]<=1340):
        raise ValueError('マスターに有効な描画可能領域 routing_bounds を明示してください')
    prepared_rules=prepare_process_route_rules(manifest, request, line_styles)
    req=prepared_rules.request; rm=prepared_rules.routes_by_id; stages=prepared_rules.stages
    routes=req['routes']
    exit_points=prepared_rules.exit_points; through_stages=prepared_rules.through_stages
    deps=prepared_rules.dependencies; order=prepared_rules.order; layout=prepared_rules.layout
    leg_regions=layout.get('leg_regions',{})
    run_topology=prepared_rules.run_topology; shared_run_legs=prepared_rules.shared_run_legs
    run_position=prepared_rules.run_position; region_by_id=prepared_rules.region_by_id
    shared=prepared_rules.shared; leg_sections=prepared_rules.leg_sections; transits=prepared_rules.transits
    old=prepared_rules.legacy_routes
    def identity(point):return point.get('equipment_id',point.get('area_id',point['id']))
    point_names=defaultdict(list)
    for point in manifest['points']:
        for name in set([point['name'],*point['aliases']]):point_names[name].append(point)
    def resolve(name):
        if not isinstance(name,str) or len(point_names.get(name,[]))!=1:raise ValueError('不明または曖昧な設備・経由点: '+str(name))
        return point_names[name][0]
    # Shared validation, fields and legend; replace ONLY the internally derived
    # route layer in this one generation function, never a saved output file.
    legacy={k:v for k,v in req.items() if k in ('template_id','template_version','fields','field_runs','legend_placement')}
    rich_ids={f['id'] for f in manifest['fields'] for key in req.get('field_runs',{}) if key in (f['id'],f['name'])}
    blanks={f['id']:'' for f in manifest['fields'] if f['id'] not in rich_ids and not (f['id']=='food_names' and req.get('legend_placement')=='food_names')}
    legacy.update(routes=old,fields=blanks|req.get('fields',{}))
    try:root=E.fromstring(render(svg_bytes,manifest,legacy))
    except ValueError as exc:
        if 'legend' in str(exc):raise ValueError('食品名の凡例が記入欄に収まりません。材料数・献立見出しを確認してください（文字は省略しません）') from exc
        raise
    for el in list(root):
        if el.get('id') in ('named-route-layer','named-route-annotations'):root.remove(el)
    referenced_equipment={p.get('equipment_id') for ps in stages.values() for p in ps}|{p.get('equipment_id') for p in exit_points.values()}|{p['equipment_id'] for pairs in transits.values() for pair in pairs for p in pair}
    overlays=layout.get('equipment_overlays',[])
    if not isinstance(overlays,list) or any(not isinstance(e,str) for e in overlays) or len(set(overlays))!=len(overlays):raise ValueError('図面追加設備の選択が不正です')
    optional_groups=[el for el in root.iter() if el.get('data-optional-equipment')=='true']
    optional_ids={e.get('id') for el in optional_groups for e in el.iter() if e.get('id')}
    known_equipment={p.get('equipment_id') for p in manifest['points'] if p.get('equipment_id')}
    if any(e not in optional_ids or e not in known_equipment for e in overlays):raise ValueError('図面追加設備が不明です')
    inactive_equipment=set()
    for element in root.iter():
        if element.get('data-optional-equipment')=='true':
            if any(e.get('id') in overlays for e in element.iter()):
                element.attrib.pop('display',None)
                element.set('style',re.sub(r'(?:^|;)\s*display\s*:\s*none(?:\s*!important)?\s*(?=;|$)','',element.get('style','')))
            else:inactive_equipment.update(e.get('id') for e in element.iter() if e.get('id'))
    if referenced_equipment & inactive_equipment:raise ValueError('参照する追加設備を図面設定で明示選択してください')
    inactive_equipment |= inactive_area_equipment(root, referenced_equipment)
    moves=layout.get('text_positions',[])
    if not isinstance(moves,list):raise ValueError('見出し位置指定の形式が不正です')
    moved=set();moved_elements=[]
    for move in moves:
        if not isinstance(move,dict) or not {'text','at'}<=set(move) or set(move)-{'text','at','element_id'} or not isinstance(move['text'],str):raise ValueError('見出し位置指定が不正・重複しています')
        if 'element_id' in move and (not isinstance(move['element_id'],str) or not move['element_id']):raise ValueError('対象文字IDを指定してください')
        matches=[el for el in root.iter(_tag('text')) if visible_text(el)==move['text'] and ('element_id' not in move or el.get('id')==move['element_id']) and all(c.tag==_tag('tspan') for c in el.iter() if c is not el)]
        target=resolve(move['at'])
        if len(matches)!=1 or target['kind']!='label_anchor':raise ValueError('一意の見出しと見出し用座標を指定してください')
        if id(matches[0]) in moved:raise ValueError('同じ文字の移動を重複指定できません')
        if matches[0].get('data-rendered-field-id') or matches[0].get('data-field-id'):raise ValueError('日替わり記入欄を静的見出しとして移動できません')
        element=matches[0]
        try:
            origin_x=float(element.get('x',next((c.get('x') for c in element.iter() if c.get('x') is not None),'nan')))
            origin_y=float(element.get('y',next((c.get('y') for c in element.iter() if c.get('y') is not None),'nan')))
            dx,dy=float(target['x'])-origin_x,float(target['y'])-origin_y
            if not math.isfinite(dx+dy):raise ValueError
            for child in element.iter():
                for axis,delta in [('x',dx),('y',dy)]:
                    if child.get(axis) is not None:child.set(axis,str(float(child.get(axis))+delta))
        except (TypeError,ValueError):raise ValueError('見出しの座標を安全に移動できません') from None
        element.set('x',str(target['x']));element.set('y',str(target['y']));moved.add(id(element));moved_elements.append(element)
    parents={c:p for p in root.iter() for c in p};els={e.get('id'):e for e in root.iter() if e.get('id')}
    equipment={p['equipment_id'] for p in manifest['points'] if p.get('equipment_id') and p['equipment_id'] not in inactive_equipment}
    boxes={eid:_equipment_bounds(els[eid],parents) for eid in equipment}
    boundary_edges=drawing_boundaries(root)
    texts=text_boxes(root)
    for element in moved_elements:
        local=E.Element(_tag('svg'));local.append(deepcopy(element));own=text_boxes(local)
        remaining=texts[:]
        for box in own:
            if box in remaining:remaining.remove(box)
            if not 0<=box[0]<box[2]<=1900 or not 0<=box[1]<box[3]<=1340 or any(intersects(box,other) for other in remaining):raise ValueError('移動した見出しが他の文字または図面範囲と衝突します')
    # Editable fields are protected even when blank.
    protected=texts+[(f['x'],f['y'],f['x']+f['width'],f['y']+f['height']) for f in manifest['fields']]
    points_by_id={p['id']:p for p in manifest['points']};fixed_label_boxes={}
    for rid,r in rm.items():
        nums=r.get('material_numbers',[r['material_number']] if 'material_number' in r else [])
        label=''.join(chr(0x2460+n-1) for n in nums)
        if r.get('number_format')=='range':label=label[0]+'〜'+label[-1]
        for i,p in enumerate(stages[rid]):
            if i in r.get('number_stages',range(len(stages[rid]))) and p.get('label_anchor'):
                anchor=points_by_id[p['label_anchor']]
                fixed_label_boxes[(rid,i)]=(anchor['x'],anchor['y']-14,anchor['x']+15*len(label),anchor['y']+3)
    # Fixed labels use the same 2-unit clearance during path search/direct
    # connections as the later label/line validation. A 1-unit reservation
    # followed by a 2-unit check can otherwise reject its own successful path.
    def number_mode(rid,i):
        return prepared_rules.number_modes[(rid,i)]
    # Number placement never changes transport geometry in the schematic
    # modes. Only legacy adjacent labels reserve space before routing.
    route_protected=protected+[(l-2,t-2,r+2,b+2) for (rid,i),(l,t,r,b) in fixed_label_boxes.items() if number_mode(rid,i)=='adjacent']
    ports={};exit_ports={};groups=defaultdict(list);issues=[]
    def issue(code,message,rid=None,**extra):issues.append(dict(code=code,message=message,**({'route_id':rid} if rid else {}),**extra))
    shared_port_values={}
    for rid in order:
        for i,p in enumerate(stages[rid]):
            unit=run_position.get((rid,i));key=(unit,'entry') if unit else (rid,i,False)
            groups[(identity(p),p.get('side','area'),key)].append((rid,i,p,False))
            if (rid,i) in exit_points:
                out=exit_points[(rid,i)];unit=run_position.get((rid,i)) if (rid,i) in shared_run_legs else None;key=(unit,'exit') if unit else (rid,i,True)
                groups[(identity(out),out.get('side','area'),key)].append((rid,i,out,True))
    # Each shared-run position is one allocation unit.  All members therefore
    # reference the same resolved port without granting member-array priority.
    allocation_units=defaultdict(list)
    for key,members in groups.items():allocation_units[key[:2]].append(members)
    for unit_groups in allocation_units.values():
      def preferred(members):
        values=[]
        for rid,i,p,is_exit in members:
            side=p.get('side')
            axis='x' if side in ('北接続','南接続') else 'y'
            if i in through_stages[rid] and p.get('equipment_id'):
                l,t,r,b=boxes[p['equipment_id']]
                values.append((l+r)/2 if axis=='x' else (t+b)/2)
                continue
            neighbor=(stages[rid][i+1] if is_exit or i==0 else exit_points.get((rid,i-1),stages[rid][i-1])) if (i>0 or i+1<len(stages[rid])) else p
            values.append(neighbor[axis])
        return sum(values)/len(values)
      # Free ports track the adjoining equipment's position. Fixed lanes stay
      # fixed. Stable source order prevents otherwise gratuitous lane swaps.
      unit_groups.sort(key=lambda members:(preferred(members),members[0][0],members[0][1]))
      free_positions={};reserved=[]
      for members in unit_groups:
        p=members[0][2]
        if p.get('allocation')=='fixed':reserved.append(p['x'] if p.get('side') in ('北接続','南接続') else p['y'])
      for members in unit_groups:
        p=members[0][2]
        if p.get('allocation')=='fixed' or p.get('kind')!='equipment_port':continue
        l,t,r,b=boxes[p['equipment_id']];horizontal=p.get('side') in ('北接続','南接続')
        lo,hi=(l+10,r-10) if horizontal else (t+10,b-10)
        target=max(lo,min(hi,preferred(members)))
        candidates=[target,lo,hi,*[v+sign*GAP for v in reserved for sign in (-1,1)]]
        candidates=[v for v in candidates if lo<=v<=hi and all(abs(v-other)>=GAP-.01 for other in reserved)]
        if candidates:
            value=min(candidates,key=lambda v:abs(v-target));free_positions[id(members)]=value;reserved.append(value)
        else:
            for rid,i,_,_ in members:issue('PORT_CAPACITY','設備接続側に線間隔を確保できません。別の接続側を選んでください',rid,stage=i)
      for n,members in enumerate(unit_groups):
        for n,(rid,i,p,is_exit) in enumerate(members):
            x,y=p['x'],p['y'];shift=(unit_groups.index(members)-(len(unit_groups)-1)/2)*GAP
            if p.get('allocation','spread') not in ('spread','fixed'):raise ValueError('接続点の配置方式が不正です')
            side=p.get('side','北接続' if p['kind']=='dispatch' else None)
            if p.get('allocation')=='fixed':pass
            elif p.get('equipment_id'):
                l,t,r,b=boxes[p['equipment_id']]
                # Allocate free ports from the adjacent equipment direction;
                # an infeasible side above remains a blocked preview.
                if side in ('北接続','南接続'):
                    x=free_positions.get(id(members),(l+r)/2+shift);y=t if side=='北接続' else b
                else:x=l if side=='西接続' else r;y=free_positions.get(id(members),(t+b)/2+shift)
                if not l<=x<=r or not t<=y<=b:issue('PORT_CAPACITY','設備接続側が混雑しています。別の接続側を選んでください',rid,stage=i)
                shape=els[p['equipment_id']]
                if shape.tag in (_tag('circle'),_tag('ellipse')):
                    cx,cy=(l+r)/2,(t+b)/2;rx,ry=(r-l)/2,(b-t)/2
                    if side in ('北接続','南接続'):y=cy+(-1 if side=='北接続' else 1)*ry*math.sqrt(max(0,1-((x-cx)/rx)**2))
                    else:x=cx+(-1 if side=='西接続' else 1)*rx*math.sqrt(max(0,1-((y-cy)/ry)**2))
            else:y+=shift
            (exit_ports if is_exit else ports)[(rid,i)]=(float(x),float(y),side)
    fixed_uses=defaultdict(list)
    for rid in order:
        for i,p in enumerate(stages[rid]):
            if p.get('allocation')=='fixed':fixed_uses[ports[(rid,i)][:2]].append((rid,i))
            if (rid,i) in exit_points and exit_points[(rid,i)].get('allocation')=='fixed':fixed_uses[exit_ports[(rid,i)][:2]].append((rid,i))
    for uses in fixed_uses.values():
        for n,(rid,i) in enumerate(uses):
            for other,j in uses[n+1:]:
                if (rid,i)==(other,j):continue
                related=(other in deps[rid] and i==0 and j==len(stages[other])-1) or (rid in deps[other] and j==0 and i==len(stages[rid])-1)
                shared_use=(run_position.get((rid,i)) is not None and run_position.get((rid,i))==run_position.get((other,j))) or any(any((rid,k) in s['members'] for k in (i-1,i)) and any((other,k) in s['members'] for k in (j-1,j)) for s in shared.values())
                if not related and not shared_use:issue('FIXED_PORT_CONFLICT','固定接続点を独立した材料が共有しています。共有区間を明示するか別の接続点を指定してください',rid,stage=i,other_route_id=other)
    def stub(p,v):
        x,y,side=v
        if p.get('equipment_id'):
            l,t,r,b=boxes[p['equipment_id']]
            return {'西接続':(l-10,y),'東接続':(r+10,y),'北接続':(x,t-10),'南接続':(x,b+10)}[side]
        if p['kind']=='area_anchor' and side:
            dx,dy={'西接続':(-10,0),'東接続':(10,0),'北接続':(0,-10),'南接続':(0,10)}[side]
            return x+dx,y+dy
        return x,y
    stubs={(rid,i):stub(p,ports[(rid,i)]) for rid in order for i,p in enumerate(stages[rid])}
    exit_stubs={key:stub(exit_points[key],v) for key,v in exit_ports.items()}
    stub_boxes=[]
    for key,end in stubs.items():
        start=ports[key][:2]
        stub_boxes.append((min(start[0],end[0])-3,min(start[1],end[1])-3,max(start[0],end[0])+3,max(start[1],end[1])+3))
    for key,end in exit_stubs.items():
        start=exit_ports[key][:2];stub_boxes.append((min(start[0],end[0])-3,min(start[1],end[1])-3,max(start[0],end[0])+3,max(start[1],end[1])+3))
    # Route the explicitly shared graph first. Number annotations are placed
    # only afterwards and cannot push an otherwise straight route into detours.
    labels=[];label_boxes=[];paths=[];shared_paths={};through_paths=[];transit_paths=[]
    common_obstacles=[(l-3,t-3,r+3,b+3) for l,t,r,b in boxes.values()]+[(l-1,t-1,r+1,b+1) for l,t,r,b in route_protected]
    for sid,section in shared.items():
        path=[]
        for pa,pb in zip(section['points'],section['points'][1:]):
            a,b=(float(pa['x']),float(pa['y'])),(float(pb['x']),float(pb['y']))
            terminal=b if pb['kind']=='equipment_port' else None
            if terminal:b=stub(pb,(*terminal,pb['side']))
            used=[(c,d) for pp in shared_paths.values() for c,d in zip(pp,pp[1:])]
            part=route_leg(a,b,common_obstacles,used,domain=domain,boundaries=boundary_edges)
            if part is None:issue('SHARED_NO_PATH','共有描画区間を配置できません',section_id=sid);path=[];break
            path.extend(part)
            if terminal:
                other_boxes=[box for eid,box in boxes.items() if eid!=pb['equipment_id']]+route_protected
                if any(hits(b,terminal,box) for box in other_boxes):issue('SHARED_NO_PATH','共有設備接続が文字・他設備と重なります',section_id=sid);path=[];break
                path.append(terminal)
        if path:shared_paths[sid]=compact(path)
    shared_edges={sid:list(zip(ps,ps[1:])) for sid,ps in shared_paths.items()}
    junction_members=defaultdict(set)
    for section in shared.values():
        for point in (section['points'][0],section['points'][-1]):
            junction_members[(float(point['x']),float(point['y']))].update(section['members'])
    def edges_of(path):return list(zip(path,path[1:]))
    def is_shared_edge(a,b,sids):
        return any(tuple(a)==tuple(c) and tuple(b)==tuple(d) for sid in sids for c,d in shared_edges.get(sid,[]))
    def occupied_for(rid,leg):
        # Declared shared sections alone may overlap. Adjacent private branches
        # still obey the same clearance as independent material routes.
        allowed=leg_sections.get((rid,leg),[])
        current_run=shared_run_legs.get((rid,leg))
        def blocked(pp):
            if pp.get('shared_run'):return pp['shared_run']!=current_run
            return pp['route_id']!=rid
        edges=[(a,b) for pp in paths if blocked(pp) for a,b in edges_of(pp['points']) if not is_shared_edge(a,b,allowed)]
        current_start=exit_ports.get((rid,leg),ports[(rid,leg)])[:2]
        for pp in paths:
            if pp['route_id']!=rid:continue
            for a,b in edges_of(pp['points']):
                # Only the actual common endpoint is allowed to touch its
                # adjacent leg. Other self-edges are occupied too, even when
                # solid and dashed portions share one material identity.
                if tuple(b)==tuple(current_start) and pp['leg']==leg-1:continue
                if not is_shared_edge(a,b,allowed):edges.append((a,b))
        # Private branches must approach an explicit junction, never run along
        # or backwards over even their own declared shared trunk.
        edges.extend((a,b) for es in shared_edges.values() for a,b in es)
        edges.extend((a,b) for pp in through_paths if blocked(pp) for a,b in edges_of(pp['points']))
        return edges
    def direct_connection(a,b,ap,bp,occupied,extra_obstacles=()):
        a,b=tuple(a),tuple(b)
        if any(not domain[0]<=x<=domain[2] or not domain[1]<=y<=domain[3] for x,y in (a,b)):return None
        if a[0]!=b[0] and a[1]!=b[1]:return None
        if a==b:return [a]
        directions={'西接続':(-1,0),'東接続':(1,0),'北接続':(0,-1),'南接続':(0,1)}
        for p,start,end in [(ap,a,b),(bp,b,a)]:
            if p.get('equipment_id') or (p['kind']=='area_anchor' and p.get('side')):
                dx,dy=directions[p.get('side','北接続' if p['kind']=='dispatch' else None)]
                if (end[0]-start[0])*dx+(end[1]-start[1])*dy<=0:return None
        obstacles=[box for eid,box in boxes.items() if eid not in (ap.get('equipment_id'),bp.get('equipment_id'))]+route_protected+list(extra_obstacles)
        if any(hits(a,b,box) for box in obstacles) or any(near_parallel(a,b,c,d) for c,d in occupied):return None
        return [a,b]
    def interior_path(p,out,a,b,occupied,allowed_domain=None):
        eid=p['equipment_id'];l,t,r,bb=boxes[eid]
        allowed_domain=domain if allowed_domain is None else allowed_domain
        internal_domain=(max(l,allowed_domain[0]),max(t,allowed_domain[1]),min(r,allowed_domain[2]),min(bb,allowed_domain[3]))
        axes={'西接続':1,'東接続':1,'北接続':2,'南接続':2}
        internal=route_leg(tuple(a),tuple(b),[box for other,box in boxes.items() if other!=eid]+route_protected,occupied,domain=internal_domain,start_axis=axes.get(p.get('side'),0),end_axis=axes.get(out.get('side'),0))
        shape=els[eid]
        if internal and shape.tag in (_tag('circle'),_tag('ellipse')):
            l,t,r,bb=boxes[eid];cx,cy=(l+r)/2,(t+bb)/2;rx,ry=(r-l)/2,(bb-t)/2
            if any(((x-cx)/rx)**2+((y-cy)/ry)**2>1.000001 for x,y in internal):return None
        return internal
    shared_leg_paths={};shared_through_paths={}
    def leg_domain(rid,i):
        region_id=leg_regions.get(rid,{}).get(str(i))
        if region_id is None:return domain
        l,t,r,b=region_by_id[region_id]['bounds']
        return (max(l,domain[0]),max(t,domain[1]),min(r,domain[2]),min(b,domain[3]))
    for rid in order:
        r=rm[rid]
        for i in range(len(stages[rid])-1):
            allowed_domain=leg_domain(rid,i)
            run_leg=shared_run_legs.get((rid,i))
            run_key=(run_leg,run_position[(rid,i)][1]) if run_leg else None
            if run_key in shared_leg_paths:
                cached=deepcopy(shared_leg_paths[run_key]);cached.update(route_id=rid,leg=i,shared_run=run_leg)
                paths.append(cached)
                continue
            ap,bp=stages[rid][i:i+2];ap=exit_points.get((rid,i),ap);a=exit_ports.get((rid,i),ports[(rid,i)]);b=ports[(rid,i+1)]
            # Connected equipment interior is a semantic stage, not a transport
            # segment; identical consecutive stages are rejected, not skipped.
            if identity(ap)==identity(bp):raise ValueError('同じ設備の連続工程は一つの工程にまとめてください')
            # Reserve an explicit junction for its declared member legs. An
            # unrelated perpendicular crossing through its exact point would
            # consume every branch approach despite the trunk remaining clear.
            junction_boxes=[(x-GAP,y-GAP,x+GAP,y+GAP) for (x,y),members in junction_members.items() if (rid,i) not in members]
            obstacles=[(l-3,t-3,r+3,bb+3) for l,t,r,bb in boxes.values()]+[(l-1,t-1,r+1,bb+1) for l,t,r,bb in route_protected+label_boxes]+junction_boxes
            start,end=exit_stubs.get((rid,i),stubs[(rid,i)]),stubs[(rid,i+1)]
            # Area anchors may lie on their printed area caption; that caption
            # alone is exempted at the explicitly named connection.
            obstacles=[o for o in obstacles if not any(o[0]<v[0]<o[2] and o[1]<v[1]<o[3] for p,v in [(ap,start),(bp,end)] if not p.get('equipment_id'))]
            via=[resolve(n) for n in layout.get('via',{}).get(rid,{}).get(str(i),[])]
            sids=leg_sections.get((rid,i),[]);path=[a[:2]];failed=False;cursor=start
            def inside_segment(point,c,d):
                return (c[0]==d[0]==point[0] and min(c[1],d[1])<point[1]<max(c[1],d[1])) or (c[1]==d[1]==point[1] and min(c[0],d[0])<point[0]<max(c[0],d[0]))
            declared_junctions={(p['x'],p['y']) for sid in sids for p in shared[sid]['points']}
            if any(tuple(v) not in declared_junctions and inside_segment(v,c,d) for v in (a[:2],b[:2]) for es in shared_edges.values() for c,d in es):
                issue('SHARED_ENDPOINT_CONFLICT','共有幹線の途中に未指定の接続があります。合流点を明示してください',rid,leg=i);continue
            if any(sid not in shared_paths for sid in sids):failed=True
            chunks=[('private',[(float(p['x']),float(p['y']))]) for p in via]
            chunks += [('shared',shared_paths[sid]) for sid in sids if sid in shared_paths]
            chunks.append(('private',[b[:2] if sids and shared_paths.get(sids[-1],[None])[-1]==b[:2] else end]))
            other_occupied=occupied_for(rid,i)
            # All declared endpoint segments reserve their lane before route
            # ordering. A later material must not discover an occupied arrival
            # only after its body has already been laid out.
            endpoint_edges=[]
            for key,v in stubs.items():
                if key[0]==rid or (run_leg and run_position.get(key) in (run_position[(rid,i)],run_position[(rid,i+1)])):continue
                edge=(ports[key][:2],v)
                if edge[0]!=edge[1] and not any(tuple(v) in (tuple(c),tuple(d)) for sid in sids for c,d in shared_edges.get(sid,[])):
                    endpoint_edges.append(edge)
            for key,v in exit_stubs.items():
                if key[0]!=rid and exit_ports[key][:2]!=v and not (run_leg and shared_run_legs.get(key)==run_leg):endpoint_edges.append((exit_ports[key][:2],v))
            other_occupied+=endpoint_edges
            own_stubs=[(a[:2],start),(end,b[:2])]
            end_span=None
            if bp.get('kind')=='equipment_port' and bp.get('allocation')!='fixed' and i+1 not in through_stages[rid] and not run_position.get((rid,i+1)) and not via and not sids and (rid,i) not in transits:
                # A free equipment port is an interval, not an arbitrary
                # center point that forces a last-minute dogleg. Maintain its
                # order relative to already allocated neighboring lanes.
                l,t,rr,bb=boxes[bp['equipment_id']]
                shape=els[bp['equipment_id']]
                if shape.tag==_tag('rect') or shape.find(_tag('rect')) is not None:
                    varying=0 if b[2] in ('北接続','南接続') else 1
                    lo,hi=(l+10,rr-10) if varying==0 else (t+10,bb-10)
                    for key,v in ports.items():
                        if key==(rid,i+1) or stages[key[0]][key[1]].get('equipment_id')!=bp['equipment_id'] or v[2]!=b[2]:continue
                        if v[varying]<b[varying]:lo=max(lo,v[varying]+GAP)
                        else:hi=min(hi,v[varying]-GAP)
                    if lo<=hi:end_span=(varying,lo,hi)
            if end_span:own_stubs=own_stubs[:1]
            search_occupied=other_occupied+[edge for edge in own_stubs if edge[0]!=edge[1]]
            # An unrelated line crossing a reserved stub tip consumes the
            # direction-constrained material's only departure/arrival junction.
            tip_boxes=[(v[0]-GAP,v[1]-GAP,v[0]+GAP,v[1]+GAP) for _,v in endpoint_edges if tuple(v) not in (tuple(start),tuple(end))]
            obstacles+=tip_boxes
            direct=direct_connection(a[:2],b[:2],ap,bp,other_occupied,junction_boxes+tip_boxes) if not via and not sids and (rid,i) not in transits else None
            leg_transits=[]
            if direct is not None:path=direct
            elif (rid,i) in transits:
                current_p=ap;current=a[:2];current_stub=start
                for entry,out in transits[(rid,i)]+[(bp,None)]:
                    destination=b[:2] if out is None else (float(entry['x']),float(entry['y']))
                    target_stub=end if out is None else stub(entry,(*destination,entry['side']))
                    part=direct_connection(current,destination,current_p,entry,other_occupied,junction_boxes+tip_boxes)
                    if part is None:
                        part=route_leg(current_stub,target_stub,obstacles,other_occupied+[(current,current_stub),(target_stub,destination)],domain=allowed_domain,boundaries=boundary_edges)
                        if part is not None:part=[current,*part,destination]
                    if part is None:failed=True;break
                    path+=part
                    if out is not None:
                        endpoint=(float(out['x']),float(out['y']))
                        inner=interior_path(entry,out,destination,endpoint,other_occupied,allowed_domain)
                        if inner is None:failed=True;break
                        path+=inner;leg_transits.append(dict(equipment_id=entry['equipment_id'],points=inner))
                        current_p=out;current=endpoint;current_stub=stub(out,(*endpoint,out['side']))
            elif via and not sids:
                axis=lambda u,v:0 if u==v else (1 if u[1]==v[1] else 2)
                course=route_course([start,*[(p['x'],p['y']) for p in via],end],obstacles,search_occupied,allowed_domain,axis(a[:2],start),axis(end,b[:2]),boundaries=boundary_edges)
                if course is None:failed=True
                else:path+=course
            else:
                for kind,chunk in chunks:
                    section=route_leg(cursor,chunk[0],obstacles,search_occupied,domain=allowed_domain,boundaries=boundary_edges,end_span=end_span)
                    if section is None:failed=True;break
                    if end_span:
                        varying,_,_=end_span;position=list(b);position[varying]=section[-1][varying];b=tuple(position)
                        ports[(rid,i+1)]=b;stubs[(rid,i+1)]=section[-1];end=section[-1]
                    path+=section
                    if kind=='shared':path+=chunk
                    cursor=chunk[-1]
            if failed:
                issue('NO_PATH','経路を配置できません。区間の経由点・接続側・線の順序を変更してください',rid,leg=i);continue
            path.append(b[:2])
            # Retain shared junction vertices; simplifying them away would lose
            # the exact membership boundary used for overlap validation.
            path=[p for k,p in enumerate(path) if k==0 or p!=path[k-1]] if sids or (rid,i) in transits else compact(path)
            if any(not domain[0]<=x<=domain[2] or not domain[1]<=y<=domain[3] for x,y in path):
                issue('ROUTING_BOUNDS_CONFLICT','接続点がマスターの描画可能領域外です',rid,leg=i);continue
            directions={'西接続':(-1,0),'東接続':(1,0),'北接続':(0,-1),'南接続':(0,1)}
            if len(path)>1 and any(p.get('side') and (p.get('equipment_id') or p['kind']=='area_anchor') and ((v[0]-u[0])*directions[p['side']][0]+(v[1]-u[1])*directions[p['side']][1]<=0) for p,u,v in [(ap,path[0],path[1]),(bp,path[-1],path[-2])]):
                issue('PORT_DIRECTION_CONFLICT','指定接続側の進入・退出方向を満たせません',rid,leg=i);continue
            # Endpoint stubs may meet their own equipment, but cannot penetrate
            # another protected object or an unrelated transport line.
            transit_ids={p['equipment_id'] for p,_ in transits.get((rid,i),[])}
            path_obstacles=[o for eid,o in boxes.items() if eid not in (ap.get('equipment_id'),bp.get('equipment_id')) and eid not in transit_ids]+label_boxes+junction_boxes
            # Every displayed segment, including port stubs, is checked against
            # the same protected text/fields. The area caption exception above
            # is restricted to the operator's exact named area endpoint.
            path_obstacles += [o for o in route_protected if not any(o[0]<v[0]<o[2] and o[1]<v[1]<o[3] for p,v in [(ap,start),(bp,end)] if not p.get('equipment_id'))]
            conflict=next(((s,e,o) for s,e in zip(path,path[1:]) for o in path_obstacles if hits(s,e,o)),None)
            if conflict:
                issue('STUB_COLLISION','接続位置で文字・番号・設備と重なります。接続側や番号位置を変更してください',rid,leg=i,collision=conflict);continue
            other_edges=occupied_for(rid,i)
            line_conflict=next(((s,e,c,d) for s,e in zip(path,path[1:]) if not is_shared_edge(s,e,sids) for c,d in other_edges if near_parallel(s,e,c,d)),None)
            if line_conflict:
                issue('LINE_COLLISION','別材料の線と接続部が重なります。接続側か線の順序を変更してください',rid,leg=i,collision=line_conflict);continue
            style=r.get('segment_styles',[r['style']]*(len(stages[rid])-1))[i]
            recorded=dict(route_id=rid,leg=i,points=[list(p) for p in path],style=style,color=r['color'],shared_sections=sids,**({'shared_run':run_leg} if run_leg else {}))
            paths.append(recorded)
            if run_key:shared_leg_paths[run_key]=deepcopy(recorded)
            transit_paths.extend(dict(route_id=rid,leg=i,style=style,color=r['color'],**part) for part in leg_transits)
        for i in sorted(through_stages[rid]):
            run_id=shared_run_legs.get((rid,i));run_key=run_position.get((rid,i)) if run_id else None
            if run_key in shared_through_paths:
                through_paths.append(deepcopy(shared_through_paths[run_key])|dict(route_id=rid,stage=i));continue
            p=stages[rid][i];eid=p['equipment_id'];a=ports[(rid,i)][:2];b=exit_ports[(rid,i)][:2]
            if a==b:issue('THROUGH_NO_PATH','設備内通過の入口と出口が同一点です',rid,stage=i);continue
            occupied=occupied_for(rid,i)
            internal=interior_path(p,exit_points[(rid,i)],a,b,occupied,leg_domain(rid,i))
            if internal is None:issue('THROUGH_NO_PATH','指定設備内の通過線を配置できません',rid,stage=i);continue
            style=rm[rid].get('segment_styles',[rm[rid]['style']]*(len(stages[rid])-1))[i]
            recorded=dict(route_id=rid,stage=i,equipment_id=eid,points=internal,style=style,color=rm[rid]['color'],**({'shared_run':run_id} if run_id else {}))
            through_paths.append(recorded)
            if run_key:shared_through_paths[run_key]=deepcopy(recorded)
    for pp in paths+through_paths+transit_paths+[dict(points=points,section_id=sid) for sid,points in shared_paths.items()]:
        if any(not domain[0]<=x<=domain[2] or not domain[1]<=y<=domain[3] for x,y in pp['points']):
            issue('ROUTING_BOUNDS','動線がマスターの描画範囲外です',pp.get('route_id'),section_id=pp.get('section_id'))
    # A constrained leg has no escape route: transport, direct connections,
    # and declared equipment-through portions all use the same intersection.
    for pp in paths+through_paths+transit_paths:
        rid=pp['route_id'];leg=pp.get('leg',pp.get('stage'))
        region_id=leg_regions.get(rid,{}).get(str(leg))
        if not region_id:continue
        l,t,r,b=region_by_id[region_id]['bounds']
        l,t,r,b=max(l,domain[0]),max(t,domain[1]),min(r,domain[2]),min(b,domain[3])
        if l>r or t>b or r-l<=0 or b-t<=0 or any(not (l<=x<=r and t<=y<=b) for x,y in pp['points']):
            issue('REGION_ESCAPE','指定動線領域の外へ出る経路は描画できません',rid,leg=leg,region_id=region_id)
    for internal in through_paths+transit_paths:
        if any(near_parallel(a,b,c,d) for other in paths+through_paths if other['route_id']!=internal['route_id'] and not (internal.get('shared_run') and other.get('shared_run')==internal['shared_run']) for a,b in edges_of(internal['points']) for c,d in edges_of(other['points'])):
            issue('INTERNAL_LINE_COLLISION','設備内の線が別材料の動線と重なります',internal['route_id'])
    all_edges=[(a,b) for pp in paths+through_paths for a,b in edges_of(pp['points'])]
    label_run_seen=set();label_groups={};label_tasks=[]
    for run in run_topology:
        for offset in range(len(run['stage_names'])):
            members=[(m['route_id'],m['from_stage']+offset) for m in run['members']]
            for key in members:label_groups[key]=(('run',run['id'],offset),members)
    for sid,section in shared.items():
        if all(number_mode(rid,leg+1)=='adjacent' for rid,leg in section['members']):continue
        if section['points'][-1]['kind']!='equipment_port':continue
        members=[(rid,leg+1) for rid,leg in section['members']]
        for key in members:label_groups[key]=(('section',sid),members)
    for rid in order:
        r=rm[rid];nums=r.get('material_numbers',[r['material_number']] if 'material_number' in r else [])
        if not nums:continue
        for i,p in enumerate(stages[rid]):
            if i not in r.get('number_stages',range(len(stages[rid]))):continue
            members=[dict(route_id=rid,stage=i,numbers=nums)]
            run_pos=run_position.get((rid,i));label_group=label_groups.get((rid,i));shared_label_section=None
            if label_group:
                if label_group[0] in label_run_seen:continue
                label_run_seen.add(label_group[0])
                if label_group[0][0]=='section':shared_label_section=label_group[0][1]
                members=[]
                for mrid,mi in label_group[1]:
                    mr=rm[mrid]
                    if mi in mr.get('number_stages',range(len(stages[mrid]))):
                        members.append(dict(route_id=mrid,stage=mi,numbers=mr.get('material_numbers',[mr['material_number']] if 'material_number' in mr else [])))
            members.sort(key=lambda m:(m['numbers'],m['route_id'],m['stage']))
            label=''.join(chr(0x2460+n-1) for member in members for n in member['numbers'])
            if not label_group and r.get('number_format')=='range':label=label[0]+'〜'+label[-1]
            mode=number_mode(rid,i)
            # A room/storage anchor has no equipment interior. Its number
            # identifies the adjacent flow; only equipment gets an inner label.
            if mode=='equipment' and not p.get('equipment_id'):mode='on_path'
            font_size=12 if mode in ('equipment','on_path') else 15
            w=font_size*len(label);h=font_size+2
            x,y,_=ports[(rid,i)];forced=layout.get('label_sides',{}).get(rid,{}).get(str(i));candidates=[]
            anchor=next((a for a in manifest['points'] if a['id']==p.get('label_anchor')),None)
            if anchor:
                candidates=[(anchor['x'],anchor['y']-14,anchor['x']+w,anchor['y']+3)]
            else:
                if p.get('equipment_id') and not forced:
                    l,t,rr,bb=boxes[p['equipment_id']]
                    if mode=='equipment':
                        # Associate the number with the stage's arrival first,
                        # keeping it inside the equipment and off the arrow.
                        for inset in (12,20,28,36):
                            cx=min(max(x,l+w/2+4),rr-w/2-4)
                            cy=min(max(y,t+h/2+inset),bb-h/2-inset)
                            candidate=(cx-w/2,cy-h/2,cx+w/2,cy+h/2)
                            if l+2<=candidate[0] and candidate[2]<=rr-2 and t+2<=candidate[1] and candidate[3]<=bb-2:candidates.append(candidate)
                    # Prefer a readable number inside its own equipment, but
                    # never over printed names, through-lines or other labels.
                    for cy in ((t+bb)/2,t+h/2+4,bb-h/2-4):
                        for cx in ((l+rr)/2,l+w/2+4,rr-w/2-4):
                            candidate=(cx-w/2,cy-h/2,cx+w/2,cy+h/2)
                            if l<=candidate[0] and candidate[2]<=rr and t<=candidate[1] and candidate[3]<=bb:candidates.append(candidate)
                for distance in (8,12,16,20,24,32,40,48,60):
                    for offset in (0,8,-8,16,-16,24,-24):
                        for side in ([forced] if forced else ['right','left','top','bottom']):
                            bx,by={'right':(x+distance,y-h/2+offset),'left':(x-distance-w,y-h/2+offset),'top':(x-w/2+offset,y-distance-h),'bottom':(x-w/2+offset,y+distance)}[side]
                            candidates.append((bx,by,bx+w,by+h))
            # Original sheets deliberately print route numbers over their own
            # transport stroke. This opt-in must not reserve or suppress the
            # label against that same material's line.
            own_edges=[(a,b) for pp in paths+through_paths if pp['route_id']==rid and (pp.get('leg') in (i-1,i) or pp.get('stage')==i) for a,b in edges_of(pp['points'])]
            if mode=='on_path':
                candidates=[]
                # Only this stage's adjacent strokes are eligible; prefer the
                # nearest readable place and keep the number on that stroke.
                line_candidates=[]
                for a,b in own_edges:
                    length=abs(a[0]-b[0])+abs(a[1]-b[1])
                    if not length:continue
                    for distance in [length/2,*range(8,int(length),8)]:
                        mx=a[0]+(b[0]-a[0])*distance/length;my=a[1]+(b[1]-a[1])*distance/length
                        proximity=abs(mx-x)+abs(my-y)
                        # Keep the stroke inside the glyph box, while allowing
                        # a grouped label to sit to either side of that stroke.
                        offsets=[(0,0)]
                        offsets += [(v,0) for v in (-(w/2-4),w/2-4)] if a[0]==b[0] else [(0,-4),(0,4)]
                        for dx,dy in offsets:
                            box=(mx+dx-w/2,my+dy-h/2,mx+dx+w/2,my+dy+h/2)
                            # Numbers belong inside the equipment when space
                            # permits, not astride its outline/arrival arrow.
                            inside=False
                            if p.get('equipment_id'):
                                el,et,er,eb=boxes[p['equipment_id']]
                                inside=el+3<=box[0] and box[2]<=er-3 and et+3<=box[1] and box[3]<=eb-3
                            line_candidates.append((proximity+abs(dx)+abs(dy)+(0 if inside else 200),box))
                candidates=[b for _,b in sorted(line_candidates)]
                if forced:
                    candidates=[b for b in candidates if {'left':b[2]<=x,'right':b[0]>=x,'top':b[3]<=y,'bottom':b[1]>=y}[forced]]
            unrelated=[box for eid,box in boxes.items() if eid!=p.get('equipment_id')]
            member_keys={(m['route_id'],m['stage']) for m in members}
            if mode=='equipment' and p.get('equipment_id'):
                el,et,er,eb=boxes[p['equipment_id']]
                # Preserve equipment ownership. Do not move a number outside
                # its stage, or bend a route to make the number fit.
                candidates=[b for b in candidates if el+2<=b[0] and b[2]<=er-2 and et+2<=b[1] and b[3]<=eb-2]
            reserved=[] if mode in ('equipment','on_path') else [box for key,box in fixed_label_boxes.items() if key not in member_keys]
            # Labels may sit just outside the kitchen border (as source sheets
            # do). They must remain inside the actual sheet and avoid every
            # protected caption/field; the route-search domain is not a text box.
            def own_path(pp):return pp['route_id']==rid or (run_pos and pp.get('shared_run')==run_pos[0]) or (shared_label_section and shared_label_section in pp.get('shared_sections',[]))
            protected_edges=all_edges if mode=='adjacent' else [(a,c) for pp in paths+through_paths if not own_path(pp) for a,c in edges_of(pp['points'])]
            stubs_to_avoid=stub_boxes
            if mode in ('equipment','on_path'):
                stubs_to_avoid=[]
                # Actual unrelated stubs are already in protected_edges. Do
                # not reserve absent or duplicate group stubs as phantom text.
            arrow_boxes=[(pp['points'][-1][0]-5,pp['points'][-1][1]-5,pp['points'][-1][0]+5,pp['points'][-1][1]+5) for pp in paths if own_path(pp)] if mode in ('equipment','on_path') else []
            valid=[b for b in candidates if 0<=b[0] and b[2]<=1900 and 0<=b[1] and b[3]<=1340 and not any(intersects(b,o,2) for o in protected+([] if mode in ('equipment','on_path') else label_boxes)+unrelated+stubs_to_avoid+reserved+arrow_boxes) and not any(hits(a,c,b,2) for a,c in protected_edges)]
            record=dict(route_id=rid,stage=i,members=members,text=label,color=r['color'],font_size=font_size,port=list(ports[(rid,i)][:2]))
            if mode in ('equipment','on_path'):
                label_tasks.append((record,list(dict.fromkeys(valid))));continue
            selected=next(iter(valid),None)
            if selected is None:issue('LABEL_COLLISION','番号を配置できません。表示側・接続側・線の順序を変更してください',rid,stage=i);continue
            label_boxes.append(selected);labels.append(record|dict(box=list(selected)))
    if label_tasks:
        # Place the most constrained annotations first. A broad route must not
        # consume another material's only readable position because of ID order.
        assignments={};attempts=0
        def assign(remaining):
            nonlocal attempts
            if not remaining:return True
            domains={i:[b for b in label_tasks[i][1] if not any(intersects(b,other,2) for other in assignments.values())] for i in remaining}
            current=min(remaining,key=lambda i:(len(domains[i]),i))
            if not domains[current]:return False
            for box in domains[current]:
                attempts+=1
                if attempts>10000:return False
                assignments[current]=box
                if assign([i for i in remaining if i!=current]):return True
                del assignments[current]
            return False
        possible=[i for i,(_,options) in enumerate(label_tasks) if options]
        success=assign(possible)
        if not success:assignments.clear()
        for i,(record,_) in enumerate(label_tasks):
            if i not in assignments:issue('LABEL_COLLISION','指定された設備内または線上に番号を配置できません。番号位置・共有区間を確認してください',record['route_id'],stage=record['stage'])
            else:labels.append(record|dict(box=list(assignments[i])));label_boxes.append(assignments[i])
    layer=E.SubElement(root,_tag('g'),{'id':'process-route-layer'})
    # A shared-section boundary is an explicit topology event.  Keep the
    # underlying rectilinear geometry authoritative, but round the private
    # branch into/out of its declared trunk when it is painted.  In particular,
    # do not infer a junction from an ordinary perpendicular crossing.
    curve_edges={};curve_blocks=[]
    def edge_section(path,a,b):
        return next((sid for sid in path.get('shared_sections',[])
                     if is_shared_edge(a,b,[sid])),None)
    def unit(a,b):
        length=math.hypot(b[0]-a[0],b[1]-a[1])
        return ((b[0]-a[0])/length,(b[1]-a[1])/length) if length else (0.,0.)
    def add_curve(path,index,at_start,tangent,kind):
        a,b=path['points'][index:index+2]
        direction=unit(a,b)
        if abs(direction[0]*tangent[1]-direction[1]*tangent[0]) < .001:return
        # A cubic needs room at both ends.  Short lanes retain their exact
        # connection as a line rather than introducing a loop or a detached
        # visual hole.
        distance=math.hypot(b[0]-a[0],b[1]-a[1])
        if distance<8:
            curve_blocks.append((path,index,kind))
            return
        amount=min(12.,max(8.,distance*.8))
        item=curve_edges.setdefault((id(path),index),{})
        item['start' if at_start else 'end']=(unit(a,b),tangent,amount,kind)
    for path in paths+through_paths:
        points=path['points']
        sections=[edge_section(path,a,b) for a,b in edges_of(points)]
        for index in range(len(sections)-1):
            before,after=sections[index],sections[index+1]
            if bool(before)==bool(after):continue
            # The shared edge gives the tangent of the common trunk.  The
            # private edge is the only edge rounded, so common membership and
            # dash phase stay exact and are still emitted once.
            if before is None:
                add_curve(path,index,False,unit(points[index+1],points[index+2]),'merge')
            else:
                add_curve(path,index+1,True,unit(points[index],points[index+1]),'split')
    # A shared run can start/end between separately recorded legs (including a
    # normal equipment-through leg).  Only bend an exterior transport edge;
    # an omitted/internal equipment segment is never invented by the renderer.
    for rid in order:
        records=sorted([p for p in paths+through_paths if p['route_id']==rid],key=lambda p:2*p['leg']+1 if 'leg' in p else 2*p['stage'])
        for previous,current in zip(records,records[1:]):
            if tuple(previous['points'][-1])!=tuple(current['points'][0]) or bool(previous.get('shared_run'))==bool(current.get('shared_run')):continue
            if not previous.get('shared_run') and current.get('shared_run') and previous in paths:
                add_curve(previous,len(previous['points'])-2,False,unit(current['points'][0],current['points'][1]),'merge')
            elif previous.get('shared_run') and not current.get('shared_run'):
                add_curve(current,0,True,unit(previous['points'][-2],previous['points'][-1]),'split')
    for blocked_path,index,kind in curve_blocks:
        issue('JUNCTION_CURVE_CONFLICT','明示した合流・分岐点の曲線接続に必要な余白がありません',blocked_path['route_id'],leg=blocked_path.get('leg'),stage=blocked_path.get('stage'),junction_kind=kind)
    def segment_box(a,b,box):
        """Liang-Barsky clipping works for the sampled (non-orthogonal) curve."""
        x,y=a;dx,dy=b[0]-x,b[1]-y;lo,hi=0.,1.
        for p,q in ((-dx,x-box[0]),(dx,box[2]-x),(-dy,y-box[1]),(dy,box[3]-y)):
            if p==0:
                if q<0:return False
                continue
            t=q/p
            if p<0:
                if t>hi:return False
                lo=max(lo,t)
            else:
                if t<lo:return False
                hi=min(hi,t)
        return True
    def orient(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    def segment_intersects(a,b,c,d):
        # Collinear overlap and endpoint touch are both collisions unless it is
        # the explicit shared-trunk endpoint filtered by the caller.
        return max(min(a[0],b[0]),min(c[0],d[0]))<=min(max(a[0],b[0]),max(c[0],d[0])) and max(min(a[1],b[1]),min(c[1],d[1]))<=min(max(a[1],b[1]),max(c[1],d[1])) and orient(a,b,c)*orient(a,b,d)<=0 and orient(c,d,a)*orient(c,d,b)<=0
    all_route_edges=[(owner,a,b) for owner in paths+through_paths for a,b in edges_of(owner['points'])]
    def endpoint_equipment(path):
        if 'equipment_id' in path:return {path['equipment_id']}
        if 'leg' not in path:return set()
        rid,index=path['route_id'],path['leg']
        return {p.get('equipment_id') for p in (stages[rid][index],stages[rid][index+1]) if p.get('equipment_id')}
    crossings=[]
    # White under-strokes only at non-merge crossings distinguish paths. They
    # are intentional bridge notation, not an omitted route segment.
    done=[];drawn_shared=set();drawn_runs=set();shared_phase={}
    for sid,pp in shared_paths.items():
        distance=0
        for a,b in edges_of(pp):
            shared_phase[(sid,tuple(a),tuple(b))]=distance%sum(line_styles['dashed']['dasharray'])
            distance+=abs(a[0]-b[0])+abs(a[1]-b[1])
    route_index={r['id']:i for i,r in enumerate(routes)}
    # Compute phase in food-flow order, rather than restarting at each leg or
    # equipment boundary. Keep drawing order unchanged for crossing notation.
    phases={}
    for rid in order:
        distance=0;previous=None
        for path in sorted([p for p in paths+through_paths if p['route_id']==rid],key=lambda p:2*p['leg']+1 if 'leg' in p else 2*p['stage']):
            if previous is None or previous['style']!=path['style'] or tuple(previous['points'][-1])!=tuple(path['points'][0]):distance=0
            phases[id(path)]=distance
            distance+=sum(abs(a[0]-b[0])+abs(a[1]-b[1]) for a,b in edges_of(path['points']))
            previous=path
    for path in paths+through_paths:
        rid=path['route_id'];pts=path['points'];phase=phases[id(path)]
        for k,(a,b) in enumerate(zip(pts,pts[1:])):
            run_edge=(path.get('shared_run'),tuple(a),tuple(b)) if path.get('shared_run') else None
            if run_edge and run_edge in drawn_runs:
                phase+=abs(a[0]-b[0])+abs(a[1]-b[1]);continue
            if run_edge:drawn_runs.add(run_edge)
            shared_id=next((sid for sid in path.get('shared_sections',[]) if is_shared_edge(a,b,[sid])),None)
            shared_key=(shared_id,tuple(a),tuple(b))
            if shared_id and shared_key in drawn_shared:
                phase+=abs(a[0]-b[0])+abs(a[1]-b[1]);continue
            if shared_id:drawn_shared.add(shared_key)
            for c,d,other in done:
                point=crossing(a,b,c,d)
                if point:
                    crossings.append(dict(point=list(point),over=rid,under=other))
                    dx,dy=(4,0) if a[1]==b[1] else (0,4)
                    E.SubElement(layer,_tag('line'),{'x1':str(point[0]-dx),'y1':str(point[1]-dy),'x2':str(point[0]+dx),'y2':str(point[1]+dy),'stroke':'white','stroke-width':'5','data-crossing':'non-merge'})
            attr={'x1':str(a[0]),'y1':str(a[1]),'x2':str(b[0]),'y2':str(b[1]),'stroke':path['color'],'stroke-width':'1.6','fill':'none','data-route-id':rid}
            if 'leg' in path:attr['data-leg']=str(path['leg'])
            else:attr.update({'data-through-stage':str(path['stage']),'data-equipment-id':path['equipment_id']})
            transit=next((part for part in transit_paths if part['route_id']==rid and part['leg']==path.get('leg') and any(tuple(a)==tuple(c) and tuple(b)==tuple(d) for c,d in edges_of(part['points']))),None)
            if transit:attr.update({'data-transit-leg':str(path['leg']),'data-transit-equipment':transit['equipment_id']})
            if shared_id:attr.update({'data-shared-section':shared_id,'data-member-route-ids':json.dumps([r for r,_ in shared[shared_id]['members']],ensure_ascii=False),'data-member-legs':json.dumps(shared[shared_id]['members'],ensure_ascii=False)})
            if path.get('shared_run'):
                members=next(run['members'] for run in run_topology if run['id']==path['shared_run'])
                attr.update({'data-shared-run':path['shared_run'],'data-member-route-ids':json.dumps([m['route_id'] for m in members],ensure_ascii=False)})
            if path['style']=='dashed':
                dash=line_styles['dashed']['dasharray']
                attr.update({'stroke-dasharray':' '.join(str(v) for v in dash),'stroke-dashoffset':str(shared_phase[shared_key] if shared_id else phase%sum(dash))})
            if 'leg' in path and k==len(pts)-2:attr['marker-end']='url(#named-route-arrow-%d)'%route_index[rid]
            curve=curve_edges.get((id(path),k))
            pieces=junction_pieces(a,b,curve) if curve else []
            unsafe=False
            for piece in pieces:
                if piece['kind']=='line':piece['length']=math.dist(*piece['points']);continue
                begin,c1,c2,finish=piece['points'];samples=[]
                for n in range(25):
                    t=n/24;u=1-t
                    samples.append(tuple(u**3*begin[j]+3*u*u*t*c1[j]+3*u*t*t*c2[j]+t**3*finish[j] for j in (0,1)))
                piece['length']=sum(math.dist(s,e) for s,e in edges_of(samples))
                own_equipment=endpoint_equipment(path)
                protected_curve=[box for eid,box in boxes.items() if eid not in own_equipment]+route_protected
                unsafe|=any(segment_box(s,e,box) for s,e in edges_of(samples) for box in protected_curve)
                # The actual connected trunk is exempt; all other transport
                # edges, including routes painted later, must remain clear.
                unsafe|=any(segment_intersects(s,e,c,d) for s,e in edges_of(samples) for owner,c,d in all_route_edges if owner is not path and tuple(a) not in (tuple(c),tuple(d)) and tuple(b) not in (tuple(c),tuple(d)))
            if unsafe:
                issue('JUNCTION_CURVE_CONFLICT','明示した合流・分岐点の曲線接続に必要な余白がありません',rid,leg=path.get('leg'),stage=path.get('stage'))
                pieces=[]
            if pieces:
                distance=0
                for index,piece in enumerate(pieces):
                    paint={key:value for key,value in attr.items() if key not in ('x1','y1','x2','y2','marker-end')}
                    if path['style']=='dashed':paint['stroke-dashoffset']=str((phase+distance)%sum(line_styles['dashed']['dasharray']))
                    if index==len(pieces)-1 and 'marker-end' in attr:paint['marker-end']=attr['marker-end']
                    if piece['kind']=='line':
                        begin,finish=piece['points']
                        E.SubElement(layer,_tag('line'),paint|{'x1':str(begin[0]),'y1':str(begin[1]),'x2':str(finish[0]),'y2':str(finish[1])})
                    else:
                        begin,c1,c2,finish=piece['points']
                        E.SubElement(layer,_tag('path'),paint|{'d':'M %s %s C %s %s, %s %s, %s %s'%(*begin,*c1,*c2,*finish),'data-junction-kind':piece['kind'],'data-junction-rendering':'explicit-curve'})
                    distance+=piece['length']
            else:E.SubElement(layer,_tag('line'),attr)
            phase+=abs(a[0]-b[0])+abs(a[1]-b[1]);done.append((a,b,rid))
    annotation=E.SubElement(root,_tag('g'),{'id':'process-number-layer'})
    for label in labels:
        l,t,r,b=label['box']
        # An opaque backing, not only white text strokes: the inside of a
        # circled numeral must also hide the line. Geometry remains continuous.
        E.SubElement(annotation,_tag('rect'),{'x':str(l-.5),'y':str(t-.5),'width':str(r-l+1),'height':str(b-t+1),'fill':'white','stroke':'none','data-number-background':label['route_id'],'data-stage':str(label['stage'])})
        E.SubElement(annotation,_tag('text'),{'x':str(l),'y':str(t+label['font_size']-1),'style':'font-size:'+str(label['font_size'])+'px;fill:'+label['color'],'data-route-id':label['route_id'],'data-stage':str(label['stage']),'data-members':json.dumps(label['members'],ensure_ascii=False)}).text=label['text']
    topology=dict(shared_runs=run_topology,shared_sections=[dict(id=sid,points=[p['name'] for p in section['points']],members=[dict(route_id=rid,leg=leg) for rid,leg in section['members']]) for sid,section in shared.items()],equipment_connections=[dict(route_id=rid,kind='branch' if rm[rid].get('branch_from') else 'merge',member_route_ids=refs,arrivals=[stages[ref][-1]['name'] for ref in refs],departure=stages[rid][0]['name'],equipment_or_area=identity(stages[rid][0])) for rid,refs in deps.items() if refs])
    E.SubElement(root,_tag('metadata'),{'id':'process-route-spec'}).text=json.dumps({'input':req,'resolved_order':order,'dependencies':deps,'topology':topology},ensure_ascii=False,sort_keys=True)
    if issues:
        E.SubElement(root,_tag('text'),{'x':'320','y':'1320','style':'font-size:20px;fill:#b42318'}).text='配置未完了 — '+str(len(issues))+'件を修正してください（出力不可）'
    return dict(ok=not issues,svg=E.tostring(root,encoding='unicode'),issues=issues,
                geometry=dict(paths=paths,through_paths=through_paths,transit_paths=transit_paths,topology=topology,shared_sections={sid:dict(points=pp,members=shared[sid]['members']) for sid,pp in shared_paths.items()},labels=labels,crossings=crossings,stage_order={rid:r['stages'] for rid,r in rm.items()},route_order=order,ports={rid:[list(ports[(rid,i)][:2]) for i in range(len(stages[rid]))] for rid in rm},exit_ports={rid:{str(i):list(v[:2]) for (rr,i),v in exit_ports.items() if rr==rid} for rid in rm},dependencies=deps))


def plan(svg_bytes,manifest,request):
    """Try alternative lane priorities only when the operator left order auto.

    This searches drawing positions, never alternatives to a business stage.
    A failed attempt is not accepted as output; all attempts use identical
    validation, and the best unresolved preview remains explicitly blocked.
    """
    mode=request.get('layout',{}).get('geometry_mode','network' if manifest.get('flow_network') else 'center-flow')
    if mode=='network':
        from .process_route_network_service import plan as plan_network
        return plan_network(svg_bytes,manifest,request)
    if mode=='center-flow':
        from .process_route_center_flow_service import plan_center_flow
        return plan_center_flow(svg_bytes,manifest,request)
    if mode!='fixed-orthogonal':raise ValueError('動線の描画方式が不明です')
    first=_plan_once(svg_bytes,manifest,request)
    if first['ok'] or 'route_order' in request.get('layout',{}):return first
    best=first;seen={tuple(first['geometry']['route_order'])}
    for _ in range(4):
        order=best['geometry']['route_order']
        failed={v['route_id'] for v in best['issues'] if v.get('route_id')}
        candidates=[[rid for rid in order if rid in failed]+[rid for rid in order if rid not in failed],list(reversed(order))]
        candidate=next((v for v in candidates if tuple(v) not in seen),None)
        if candidate is None:break
        seen.add(tuple(candidate));q=deepcopy(request);q.setdefault('layout',{})['route_order']=candidate
        result=_plan_once(svg_bytes,manifest,q)
        if result['ok']:return result
        if len(result['issues'])<=len(best['issues']):best=result
    return best
