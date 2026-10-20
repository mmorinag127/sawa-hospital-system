"""Equipment-centred flow graph. Business topology precedes all geometry."""
from collections import defaultdict
from copy import deepcopy
import math
import json
import xml.etree.ElementTree as E
from .named_svg_route_service import _equipment_bounds, _tag, resolve_line_styles, validate_pair
from .process_route_rules_service import prepare_process_route_rules
from .process_route_center_geometry import canvas, segment_box, free_path, intersection, overlap


def _centres(svg, manifest):
    root=E.fromstring(svg); parents={c:p for p in root.iter() for c in p}
    elements={el.get('id'):el for el in root.iter() if el.get('id')}
    boxes={p['equipment_id']:_equipment_bounds(elements[p['equipment_id']],parents) for p in manifest['points'] if p.get('equipment_id')}
    return {eid:((l+r)/2,(t+b)/2) for eid,(l,t,r,b) in boxes.items()},boxes


def visits_for(prepared, centres, boxes):
    points={('stage',rid,i):p for rid,ps in prepared.stages.items() for i,p in enumerate(ps)}
    points.update({('transit',rid,leg,i):pair[0] for (rid,leg),pairs in prepared.transits.items() for i,pair in enumerate(pairs)})
    parent={key:key for key in points}
    def find(k):
        if parent[k]!=k:parent[k]=find(parent[k])
        return parent[k]
    def union(keys):
        root=find(keys[0])
        for key in keys[1:]:parent[find(key)]=root
    for run in prepared.run_topology:
        for offset in range(len(run['stage_names'])):
            union([('stage',m['route_id'],m['from_stage']+offset) for m in run['members']])
    for rid,refs in prepared.dependencies.items():
        if refs:
            keys=[('stage',rid,0)]+[('stage',ref,len(prepared.stages[ref])-1) for ref in refs]
            fixed={(points[k]['x'],points[k]['y']) for k in keys if points[k].get('allocation')=='fixed'}
            if len(fixed)<=1:union(keys)
    # Union a shared trunk's destination, never its independent source visits.
    for section in prepared.shared.values():
        if section['points'][-1].get('equipment_id'):
            union([('stage',rid,leg+1) for rid,leg in section['members']])
    groups=defaultdict(dict); result={}; rank={rid:i for i,rid in enumerate(prepared.order)}
    for key,p in points.items():
        eid=p.get('equipment_id')
        if eid:groups[eid].setdefault(find(key),[]).append(key)
        else:result[key]=(float(p['x']),float(p['y']))
    for eid,units in groups.items():
        l,t,r,b=boxes[eid]; cx,cy=centres[eid]
        ordered=sorted(units,key=lambda unit:min((rank[k[1]],k[2:]) for k in units[unit]))
        gap=min(18.,(b-t-12)/max(1,len(ordered)-1))
        if gap<=0:raise ValueError('設備内の通過点を分離できません: '+eid)
        for i,unit in enumerate(ordered):
            pos=(cx,cy+(i-(len(ordered)-1)/2)*gap)
            fixed=[points[key] for key in units[unit] if points[key].get('allocation')=='fixed']
            if fixed:
                coords={(float(p['x']),float(p['y'])) for p in fixed}
                if len(coords)!=1:
                    raise ValueError('共有する設備訪問の固定接続位置が一致していません: '+str([(p['name'],p['x'],p['y']) for p in fixed]))
                pos=next(iter(coords))
            elif all(points[k].get('side') in ('北接続','南接続') for k in units[unit]):
                # A vertical course needs side-by-side columns, not a stack
                # of points on the same X. Align adjacent vertical equipment
                # using their common horizontal span.
                spans=[(l,r)]
                for key in units[unit]:
                    if key[0]!='stage':continue
                    _,rid,index=key
                    for neighbor in (index-1,index+1):
                        if 0<=neighbor<len(prepared.stages[rid]):
                            p=prepared.stages[rid][neighbor];other=p.get('equipment_id')
                            if other in boxes and other!=eid and p.get('side') in ('北接続','南接続'):
                                ol,ot,orr,ob=boxes[other]
                                if max(l,ol)<min(r,orr):spans.append((ol,orr))
                left,right=max(s[0] for s in spans),min(s[1] for s in spans)
                if left>=right:left,right=l,r
                x=(left+right)/2+(i-(len(ordered)-1)/2)*min(18.,(right-left-12)/max(1,len(ordered)-1))
                pos=(x,cy)
            for key in units[unit]:result[key]=pos
    return result


def plan_center_flow(svg, manifest, request):
    validate_pair(svg,manifest); styles=resolve_line_styles(manifest)
    prepared=prepare_process_route_rules(manifest,request,styles)
    root,inactive=canvas(svg,manifest,prepared)
    from .process_route_layout_service import text_boxes
    centres,all_boxes=_centres(svg,manifest)
    boxes={eid:b for eid,b in all_boxes.items() if eid not in inactive}
    visits=visits_for(prepared,centres,boxes)
    positions={(rid,i):visits['stage',rid,i] for rid,ps in prepared.stages.items() for i in range(len(ps))}
    departures=dict(positions)
    for key,p in prepared.exit_points.items():
        departures[key]=(float(p['x']),float(p['y']))
    domain=manifest.get('routing_bounds')
    if not isinstance(domain,list) or len(domain)!=4:raise ValueError('描画可能領域が必要です')
    texts=text_boxes(root)
    # Shared topology is supplied before geometry. Its named junctions must
    # not migrate into a different equipment compartment based on averages.
    sections={sid:((s['points'][0]['x'],s['points'][0]['y']),(s['points'][-1]['x'],s['points'][-1]['y'])) for sid,s in prepared.shared.items()}
    occupied=[];shared_paths={};paths=[];transits=[];through=[];run_paths={}
    from .process_route_layout_service import compact
    def passage(a,b,entry):
        # One joint course through the named compartment/column. Do not send
        # its entrance and exit independently to a whole-equipment centre.
        compartment=entry.get('compartment_bounds')
        if compartment is not None:
            l,t,r,bottom=compartment
            if not (l<=a[0]<=r and l<=b[0]<=r and a[1]==b[1]==t):
                raise ValueError('槽内通過の入出位置が区画と一致していません')
            depth=entry.get('passage_depth')
            if not isinstance(depth,(int,float)) or not 0<depth<bottom-t:
                raise ValueError('槽内通過の深さが区画内ではありません')
            return compact([tuple(a),(a[0],t+depth),(b[0],t+depth),tuple(b)])
        elbow=(a[0],b[1]) if entry.get('side') in ('北接続','南接続') else (b[0],a[1])
        return compact([tuple(a),elbow,tuple(b)])
    for rid,stages in prepared.stages.items():
        route=prepared.routes_by_id[rid]
        segment_styles=route.get('segment_styles',[route['style']]*(len(stages)-1))
        for i in sorted(prepared.through_stages[rid]):
            pp=passage(positions[rid,i],departures[rid,i],stages[i])
            style=segment_styles[min(i,len(segment_styles)-1)]
            record=dict(route_id=rid,stage=i,equipment_id=stages[i]['equipment_id'],points=[list(p) for p in pp],color=route['color'],style=style,dasharray=styles[style]['dasharray'])
            run=prepared.run_position.get((rid,i))
            if run:record['shared_run']=run[0]
            for other in through:
                if other['route_id']==rid or (record.get('shared_run') and record.get('shared_run')==other.get('shared_run')):continue
                if any(overlap(a,b,c,d)>1e-6 for a,b in zip(pp,pp[1:]) for c,d in zip(other['points'],other['points'][1:])):
                    raise ValueError('独立した設備内動線が重なります。別の縦列・区画を指定してください')
            through.append(record)
    # Reserve the actual internal courses, not only isolated visit dots.
    for p in through:occupied.extend(zip(p['points'],p['points'][1:]))
    connections=[]
    for rid,refs in prepared.dependencies.items():
        for ref in refs:
            last=len(prepared.stages[ref])-1
            a,b=departures[ref,last],positions[rid,0]
            if a==b:continue
            route=prepared.routes_by_id[ref]
            style=route.get('segment_styles',[route['style']])[-1]
            pp=passage(a,b,prepared.stages[ref][-1])
            connections.append(dict(route_id=ref,stage=last,equipment_id=prepared.stages[rid][0]['equipment_id'],points=[list(p) for p in pp],color=route['color'],style=style,dasharray=styles[style]['dasharray'],connection_to=rid))
            occupied.extend(zip(pp,pp[1:]))
    equipment_joins={positions[rid,0]:prepared.stages[rid][0].get('equipment_id') for rid,refs in prepared.dependencies.items() if refs}
    port_defs={}
    for p in [p for ps in prepared.stages.values() for p in ps]+list(prepared.exit_points.values())+[p for pairs in prepared.transits.values() for pair in pairs for p in pair]:
        if p.get('kind')=='equipment_port' and p.get('allocation')=='fixed':
            port_defs[p['x'],p['y']]=p
    def outside_join_edges(edges,box):
        l,t,r,b=box;remaining=[]
        for a,z in edges:
            if a[1]==z[1] and t<a[1]<b:
                lo,hi=sorted((a[0],z[0]))
                remaining.extend([((lo,a[1]),(min(hi,l),a[1]))] if lo<min(hi,l) else [])
                remaining.extend([((max(lo,r),a[1]),(hi,a[1]))] if max(lo,r)<hi else [])
            elif a[0]==z[0] and l<a[0]<r:
                lo,hi=sorted((a[1],z[1]))
                remaining.extend([((a[0],lo),(a[0],min(hi,t)))] if lo<min(hi,t) else [])
                remaining.extend([((a[0],max(lo,b)),(a[0],hi))] if max(lo,b)<hi else [])
            else:remaining.append((a,z))
        return remaining
    def route_between(a,b,allowed,region):
        if tuple(a)==tuple(b):return [tuple(a)]
        obstacles=[box for eid,box in boxes.items() if eid not in allowed]
        # Reserve all future visits before sequential routing. Without this a
        # vertical stroke through centre X can seal another material's visit
        # above/below it even though their centre-Y slots are distinct.
        obstacles += [(x-3,y-3,x+3,y+3) for x,y in set(visits.values()) if (x,y) not in (tuple(a),tuple(b)) and not any(l<=x<=r and t<=y<=bottom for eid,(l,t,r,bottom) in boxes.items() if eid in allowed)]
        obstacles += [box for box in texts if not any(box[0]>=boxes[e][0] and box[2]<=boxes[e][2] and box[1]>=boxes[e][1] and box[3]<=boxes[e][3] for e in allowed if e in boxes) and not segment_box(a,a,box) and not segment_box(b,b,box)]
        blocked_edges=occupied
        # Five or more explicitly merged ingredients cannot each occupy a
        # different compass direction at one point. The declared merge allows
        # a common stroke INSIDE that equipment, never outside it or at an
        # unrelated independent visit.
        for p in (tuple(a),tuple(b)):
            eid=equipment_joins.get(p)
            if eid in boxes:blocked_edges=outside_join_edges(blocked_edges,boxes[eid])
        tips=[]
        for endpoint,other in ((a,b),(b,a)):
            p=port_defs.get(tuple(endpoint))
            same_interior=False
            if p:
                l,t,r,bottom=boxes[p['equipment_id']]
                same_interior=l<=other[0]<=r and t<=other[1]<=bottom
            if p and not same_interior:
                dx,dy={'北接続':(0,-1),'南接続':(0,1),'西接続':(-1,0),'東接続':(1,0)}[p['side']]
                tips.append((endpoint[0]+8*dx,endpoint[1]+8*dy))
                # Transport must meet the named edge from outside. Only the
                # explicitly planned passage may cross this equipment.
                obstacles.append(boxes[p['equipment_id']])
            else:tips.append(tuple(endpoint))
        middle=free_path(tips[0],tips[1],obstacles,region,blocked_edges)
        return compact([tuple(a),*middle,tuple(b)])
    for sid,(a,b) in sections.items():
        allowed={p.get('equipment_id') for p in prepared.shared[sid]['points'] if p.get('equipment_id')}
        limits=[prepared.region_by_id[value]['bounds'] for rid,leg in prepared.shared[sid]['members'] if (value:=prepared.layout.get('leg_regions',{}).get(rid,{}).get(str(leg)))]
        region=[max(v[0] for v in [domain,*limits]),max(v[1] for v in [domain,*limits]),min(v[2] for v in [domain,*limits]),min(v[3] for v in [domain,*limits])]
        anchors=[(p['x'],p['y']) for p in prepared.shared[sid]['points']]
        pp=[]
        for start,end in zip(anchors,anchors[1:]):
            part=route_between(start,end,allowed,region)
            pp.extend(part if not pp else part[1:])
        shared_paths[sid]=dict(points=[list(p) for p in pp],members=prepared.shared[sid]['members'])
        occupied.extend(zip(pp,pp[1:]))
    for rid in prepared.order:
        route=prepared.routes_by_id[rid];stages=prepared.stages[rid]
        segment_styles=route.get('segment_styles',[route['style']]*(len(stages)-1))
        for leg in range(len(stages)-1):
            run=prepared.run_position.get((rid,leg)) if (rid,leg) in prepared.shared_run_legs else None
            if run and run in run_paths:points=deepcopy(run_paths[run])
            else:
                region_id=prepared.layout.get('leg_regions',{}).get(rid,{}).get(str(leg))
                region=prepared.region_by_id[region_id]['bounds'] if region_id else domain
                allowed={p['equipment_id'] for p in (stages[leg],stages[leg+1]) if p.get('equipment_id')}
                pairs=prepared.transits.get((rid,leg),[])
                allowed|={p['equipment_id'] for pair in pairs for p in pair}
                selected=prepared.leg_sections.get((rid,leg),[])
                anchors=[departures[rid,leg]]
                transit_courses=[]
                for entry,out in pairs:
                    a=(float(entry['x']),float(entry['y']));b=(float(out['x']),float(out['y']))
                    anchors.extend((a,b));transit_courses.append((a,b,passage(a,b,entry),entry))
                anchors.append(sections[selected[0]][0] if selected else positions[rid,leg+1])
                points=[]
                for a,b in zip(anchors,anchors[1:]):
                    direct=next((pp for x,y,pp,entry in transit_courses if x==a and y==b),None)
                    try:part=direct if direct is not None else route_between(a,b,allowed,region)
                    except ValueError as exc:raise ValueError(f'{rid} 区間{leg+1}: {exc} ({a} → {b})') from exc
                    points.extend(part if not points else part[1:]); occupied.extend(zip(part,part[1:]))
                for sid in selected:
                    part=[tuple(p) for p in shared_paths[sid]['points']]
                    if points[-1]!=part[0]:raise ValueError('共有区間が接続されていません')
                    points.extend(part[1:])
                if selected and points[-1]!=positions[rid,leg+1]:
                    part=route_between(points[-1],positions[rid,leg+1],allowed,region)
                    points.extend(part[1:]);occupied.extend(zip(part,part[1:]))
                if run:run_paths[run]=deepcopy(points)
            path=dict(route_id=rid,leg=leg,points=[list(p) for p in points],color=route['color'],style=segment_styles[leg],dasharray=styles[segment_styles[leg]]['dasharray'])
            if run:path['shared_run']=run[0]
            if prepared.leg_sections.get((rid,leg)):path['shared_sections']=prepared.leg_sections[rid,leg]
            paths.append(path)
            for i,pair in enumerate(prepared.transits.get((rid,leg),[])):
                a,b=pair
                pp=passage((a['x'],a['y']),(b['x'],b['y']),a)
                transits.append(dict(route_id=rid,leg=leg,equipment_id=a['equipment_id'],points=[list(p) for p in pp],color=route['color'],style=segment_styles[leg]))
    labels=[];grouped={};named_points={p['id']:p for p in manifest['points']}
    for rid in prepared.order:
        route=prepared.routes_by_id[rid];nums=route.get('material_numbers',[route['material_number']] if 'material_number' in route else [])
        for i in route.get('number_stages',range(len(route['stages']))):
            pos=positions[rid,i]
            anchor=prepared.stages[rid][i].get('label_anchor')
            if anchor and prepared.number_modes[rid,i]=='equipment':
                point=named_points[anchor];pos=(point['x'],point['y'])
            if prepared.number_modes[rid,i]=='on_path' and i:
                incoming=next(p for p in paths if p['route_id']==rid and p['leg']==i-1)['points']
                if len(incoming)>1:
                    a,b=incoming[-2:];distance=math.dist(a,b);offset=min(23.,distance/2)
                    if distance:pos=(b[0]+(a[0]-b[0])*offset/distance,b[1]+(a[1]-b[1])*offset/distance)
            key=(pos,route['color']); member=dict(route_id=rid,stage=i,numbers=nums)
            if key in grouped:
                grouped[key]['members'].append(member);grouped[key]['text']+=''.join(chr(0x2460+n-1) for n in nums)
            else:
                record=dict(route_id=rid,stage=i,color=route['color'],text=''.join(chr(0x2460+n-1) for n in nums),members=[member],position=list(pos),font_size=14)
                grouped[key]=record;labels.append(record)
    # Pack neighbouring vertical courses in spatial order, not recipe order.
    # Otherwise an earlier outer column can consume both available rows and
    # force the later middle column's number onto somebody else's line.
    column_labels=defaultdict(list)
    for index,label in enumerate(labels):
        rid,i=label['route_id'],label['stage']
        inner=next((p['points'] for p in through if p['route_id']==rid and p['stage']==i),[])
        if len(inner)>1 and inner[0][0]==inner[1][0] and not prepared.stages[rid][i].get('compartment_bounds'):
            column_labels[prepared.stages[rid][i].get('equipment_id')].append(index)
    for indices in column_labels.values():
        ordered=sorted((labels[j] for j in indices),key=lambda l:l['position'][0])
        for index,label in zip(indices,ordered):labels[index]=label
    placed=[]
    def collide(a,b):return a[0]<b[2]+1 and a[2]>b[0]-1 and a[1]<b[3]+1 and a[3]>b[1]-1
    for label in labels:
        label['members'].sort(key=lambda m:m['numbers'])
        label['text']=''.join(chr(0x2460+n-1) for n in sorted({n for m in label['members'] for n in m['numbers']}))
        x,y=label['position'];w=14*len(label['text'])
        rid,i=label['route_id'],label['stage'];point=prepared.stages[rid][i]
        equipment=boxes.get(point.get('equipment_id'))
        inner=next((p['points'] for p in through if p['route_id']==rid and p['stage']==i),[])
        vertical_column=bool(len(inner)>1 and inner[0][0]==inner[1][0] and not point.get('compartment_bounds'))
        candidates=[(x,y)]
        candidates += [(x+dx,y) for dx in (-(w/2+18),w/2+18,-w-28,w+28)]
        if prepared.number_modes[rid,i]=='on_path':
            path=next((p for p in paths if p['route_id']==rid and p['leg']==max(0,i-1)),None)
            if path:
                a,b=path['points'][-2:] if i else path['points'][:2]
                for offset in (35,50,65):
                    d=math.dist(a,b);base=b if i else a;other=a if i else b
                    if d>offset:candidates.append((base[0]+(other[0]-base[0])*offset/d,base[1]+(other[1]-base[1])*offset/d))
                # Dense shared endpoints may need a number farther along the
                # existing stroke; never bend the route to make label space.
                for a,b in reversed(list(zip(path['points'],path['points'][1:]))):
                    d=math.dist(a,b)
                    for offset in range(9,int(d),18):
                        candidates.append((b[0]+(a[0]-b[0])*offset/d,b[1]+(a[1]-b[1])*offset/d))
        candidates += [(x+dx,y+dy) for dy in (-17,17,-34,34) for dx in (0,-w/2,w/2)]
        if equipment and prepared.number_modes[rid,i]=='equipment':
            l,t,r,b=equipment
            grid=[(xx,yy) for xx in range(math.ceil(l+w/2+1),math.floor(r-w/2),4) for yy in range(math.ceil(t+8),math.floor(b-8),4)]
            if vertical_column:
                grid=[(x,yy) for yy in range(math.ceil(t+8),math.floor(b-8))]
                candidates=sorted(grid,key=lambda p:math.dist(p,(x,y)))
            else:candidates += sorted(grid,key=lambda p:math.dist(p,(x,y)))
        legal=[]
        for xx,yy in candidates:
            # A number on a vertical equipment course must not migrate onto
            # its neighbour's column and visually reverse material identity.
            if vertical_column and xx!=x:continue
            box=[xx-w/2,yy-7,xx+w/2,yy+8]
            vx,vy,vw,vh=manifest['view_box']
            if not (vx+1<=box[0] and box[2]<=vx+vw-1 and vy+1<=box[1] and box[3]<=vy+vh-1):continue
            if prepared.number_modes[rid,i]=='equipment' and equipment and not (equipment[0]<=box[0] and box[2]<=equipment[2] and equipment[1]<=box[1] and box[3]<=equipment[3]):continue
            if any(collide(box,other) for other in texts+placed):continue
            legal.append(box)
        if not legal:raise ValueError('材料番号が設備名・他の番号と重なります: '+rid+' 工程'+str(i+1))
        label['box']=legal[0];placed.append(legal[0])
    topology=dict(shared_runs=deepcopy(prepared.run_topology),shared_sections=[dict(id=sid,points=[p['name'] for p in s['points']],members=[dict(route_id=r,leg=l) for r,l in s['members']]) for sid,s in prepared.shared.items()],equipment_connections=[dict(route_id=rid,kind='branch' if prepared.routes_by_id[rid].get('branch_from') else 'merge',member_route_ids=refs) for rid,refs in prepared.dependencies.items() if refs])
    geometry=dict(paths=paths,through_paths=through,transit_paths=transits,labels=labels,crossings=[],topology=topology,shared_sections=shared_paths,stage_order={rid:r['stages'] for rid,r in prepared.routes_by_id.items()},route_order=prepared.order,ports={rid:[list(positions[rid,i]) for i in range(len(ps))] for rid,ps in prepared.stages.items()},exit_ports={rid:{str(i):list(departures[rid,i]) for owner,i in prepared.exit_points if owner==rid} for rid in prepared.routes_by_id},dependencies=prepared.dependencies,center_flow=True,leg_regions=deepcopy(prepared.layout.get('leg_regions',{})))
    geometry['connection_paths']=connections
    paint(root,geometry,prepared)
    return dict(ok=True,issues=[],geometry=geometry,svg=E.tostring(root,encoding='unicode'))


def paint(root,geometry,prepared):
    # This is a renderer contract, not only a router preference. Every caller,
    # shared section and transit must obey it before any output can be saved.
    for path in geometry['paths']+geometry['through_paths']+geometry.get('connection_paths',[])+geometry['transit_paths']+list(geometry['shared_sections'].values()):
        if any(a[0]!=b[0] and a[1]!=b[1] for a,b in zip(path['points'],path['points'][1:])):
            raise ValueError('斜めの動線は禁止です。水平・垂直の経路を指定してください')
    layer=E.SubElement(root,_tag('g'),{'id':'process-route-layer','data-geometry-mode':geometry.get('mode','center-flow')})
    shared_edges={}
    for sid,s in geometry['shared_sections'].items():
        for a,b in zip(s['points'],s['points'][1:]):shared_edges[tuple(a),tuple(b)]=sid
    declared=set()
    for s in geometry['shared_sections'].values():declared.update((tuple(s['points'][0]),tuple(s['points'][-1])))
    for run in prepared.run_topology:
        m=run['members'][0];declared.update(tuple(geometry['ports'][m['route_id']][i]) for i in (m['from_stage'],m['to_stage']))
    for rid,refs in prepared.dependencies.items():
        if refs:declared.add(tuple(geometry['ports'][rid][0]))
    for connection in geometry['topology'].get('equipment_connections',[]):
        declared.update(tuple(point) for point in connection.get('junctions',[]))
    incoming=defaultdict(set);outgoing=defaultdict(set)
    for path in geometry['paths']+geometry['through_paths']+geometry.get('connection_paths',[]):
        for a,b in zip(path['points'],path['points'][1:]):
            incoming[tuple(b)].add(tuple(a));outgoing[tuple(a)].add(tuple(b))
    def unit(a,b):
        length=math.dist(a,b)
        return ((b[0]-a[0])/length,(b[1]-a[1])/length)
    def curve(a,b):
        a,b=tuple(a),tuple(b);direction=unit(a,b);radius=min(14.,math.dist(a,b)/4)
        begin=(a in declared and len(outgoing[a])>1 and len(incoming[a])==1)
        end=(b in declared and len(incoming[b])>1 and len(outgoing[b])==1)
        if not begin and not end:return None
        d=f'M {a[0]} {a[1]}'
        if begin:
            tangent=unit(next(iter(incoming[a])),a)
            finish=(a[0]+radius*direction[0],a[1]+radius*direction[1])
            d+=f' C {a[0]+radius*.55*tangent[0]} {a[1]+radius*.55*tangent[1]}, {finish[0]-radius*.4*direction[0]} {finish[1]-radius*.4*direction[1]}, {finish[0]} {finish[1]}'
        if end:
            tangent=unit(b,next(iter(outgoing[b])))
            start=(b[0]-radius*direction[0],b[1]-radius*direction[1])
            d+=f' L {start[0]} {start[1]} C {start[0]+radius*.4*direction[0]} {start[1]+radius*.4*direction[1]}, {b[0]-radius*.55*tangent[0]} {b[1]-radius*.55*tangent[1]}, {b[0]} {b[1]}'
        else:d+=f' L {b[0]} {b[1]}'
        return d,('branch-merge' if begin and end else 'branch' if begin else 'merge')
    done=[];seen=set();phases=defaultdict(float)
    route_index={r['id']:i for i,r in enumerate(prepared.request['routes'])}
    for path in sorted(geometry['paths']+geometry['through_paths']+geometry.get('connection_paths',[]),key=lambda p:(route_index[p['route_id']],2*p['leg']+1 if 'leg' in p else 2*p['stage'])):
        rid=path['route_id'];phase=phases[rid,path['style']]
        for i,(a,b) in enumerate(zip(path['points'],path['points'][1:])):
            sid=shared_edges.get((tuple(a),tuple(b)));token=(sid or path.get('shared_run'),tuple(a),tuple(b));distance=math.dist(a,b)
            if token[0] and token in seen:phase+=distance;continue
            if token[0]:seen.add(token)
            for c,d,owner in done:
                p=intersection(a,b,c,d)
                if p:
                    ux,uy=(b[0]-a[0])/distance,(b[1]-a[1])/distance
                    E.SubElement(layer,_tag('line'),{'x1':str(p[0]-4*ux),'y1':str(p[1]-4*uy),'x2':str(p[0]+4*ux),'y2':str(p[1]+4*uy),'stroke':'white','stroke-width':'5','data-crossing':'non-merge'})
                    geometry['crossings'].append(dict(point=list(p),over=rid,under=owner))
            attrs={'x1':str(a[0]),'y1':str(a[1]),'x2':str(b[0]),'y2':str(b[1]),'stroke':path['color'],'stroke-width':'1.6','fill':'none','stroke-linejoin':'round','data-route-id':rid}
            if 'leg' in path:attrs['data-leg']=str(path['leg'])
            else:attrs['data-through-stage']=str(path['stage']);attrs['data-equipment-id']=path['equipment_id']
            if path['dasharray']:attrs.update({'stroke-dasharray':' '.join(map(str,path['dasharray'])),'stroke-dashoffset':str(-phase%sum(path['dasharray']))})
            if sid:attrs['data-shared-section']=sid
            if path.get('shared_run'):attrs['data-shared-run']=path['shared_run']
            if i==len(path['points'])-2:attrs['marker-end']='url(#named-route-arrow-%d)'%route_index[rid]
            curved=curve(a,b)
            if curved:
                paint_attrs={k:v for k,v in attrs.items() if k not in ('x1','x2','y1','y2')}
                E.SubElement(layer,_tag('path'),paint_attrs|{'d':curved[0],'data-junction-kind':curved[1],'data-junction-rendering':'explicit-curve'})
            else:E.SubElement(layer,_tag('line'),attrs)
            phase+=distance;done.append((a,b,rid))
        phases[rid,path['style']]=phase
    annotation=E.SubElement(root,_tag('g'),{'id':'process-number-layer'})
    for label in geometry['labels']:
        l,t,r,b=label['box']
        E.SubElement(annotation,_tag('rect'),{'x':str(l-.5),'y':str(t-.5),'width':str(r-l+1),'height':str(b-t+1),'fill':'white','stroke':'none','data-number-background':label['route_id'],'data-stage':str(label['stage'])})
        node=E.SubElement(annotation,_tag('text'),{'x':str(l),'y':str(t+13),'style':'font-size:14px;fill:'+label['color'],'data-route-id':label['route_id'],'data-stage':str(label['stage']),'data-members':json.dumps(label['members'],ensure_ascii=False)})
        if label.get('runs'):
            for run in label['runs']:
                E.SubElement(node,_tag('tspan'),{'style':'fill:'+run['color']}).text=run['text']
        else:node.text=label['text']
    E.SubElement(root,_tag('metadata'),{'id':'process-route-spec'}).text=json.dumps(dict(input=prepared.request,resolved_order=prepared.order,dependencies=prepared.dependencies,topology=geometry['topology']),ensure_ascii=False,sort_keys=True)
