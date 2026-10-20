"""Read routing barriers from the same orthogonal walls that users see.

Gate permissions still come only from the facility network. This reader does
not infer doors from an example route or open a wall to make a search succeed.
"""
import re


def wall_segments(root):
    walls = next((el for el in root.iter() if el.get('data-layer') == 'walls'), None)
    if walls is None:
        raise ValueError('厨房SVGに壁レイヤーがありません')
    result = []
    def add(eid, a, b):
        if a == b:
            return
        if a[0] != b[0] and a[1] != b[1]:
            raise ValueError('直交していない壁です: ' + eid)
        axis = 'x' if a[0] == b[0] else 'y'
        fixed = 0 if axis == 'x' else 1
        result.append(dict(id=f'svg:{eid}:{len(result)}', axis=axis,
                           position=a[fixed], span=sorted((a[1-fixed], b[1-fixed]))))
    for el in walls:
        eid = el.get('id', '')
        if el.get('transform') or walls.get('transform'):
            raise ValueError('変換された壁の座標は未対応です: ' + eid)
        tag = el.tag.rsplit('}', 1)[-1]
        if tag == 'rect':
            x,y,w,h = (float(el.get(k)) for k in ('x','y','width','height'))
            corners = [(x,y),(x+w,y),(x+w,y+h),(x,y+h),(x,y)]
            for a,b in zip(corners,corners[1:]): add(eid,a,b)
        elif tag == 'path':
            tokens = re.findall(r'[A-Za-z]|[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?', el.get('d',''))
            i=0; command=None; point=(0.,0.); origin=point
            while i < len(tokens):
                if tokens[i].isalpha():
                    command=tokens[i]; i+=1
                if command not in ('M','L','H','V','Z'):
                    raise ValueError('未対応の壁パス命令です: '+eid)
                if command == 'Z':
                    add(eid,point,origin); point=origin; command=None
                    continue
                count=2 if command in ('M','L') else 1
                values=[float(v) for v in tokens[i:i+count]]; i+=count
                new=(values[0],values[1]) if count==2 else (values[0],point[1]) if command=='H' else (point[0],values[0])
                if command=='M': origin=new; command='L'
                else: add(eid,point,new)
                point=new
        else:
            raise ValueError('未対応の壁図形です: '+eid)
    return result


def missing_wall_crossings(path, root, network):
    """List visible-wall crossings not covered by an explicit facility gate."""
    barriers={w['id']:w for w in network['barriers']}
    missing=[]
    for a,b in zip(path,path[1:]):
        for wall in wall_segments(root):
            axis=0 if wall['axis']=='x' else 1
            if a[1-axis]!=b[1-axis]: continue
            lo,hi=sorted((a[axis],b[axis]))
            at=a[1-axis]
            if not (lo < wall['position'] < hi and wall['span'][0] <= at <= wall['span'][1]): continue
            permitted=any(
                (registered:=barriers[g['barrier_id']])['axis']==wall['axis']
                and registered['position']==wall['position']
                and registered['span'][0]<=at<=registered['span'][1]
                and g['span'][0]<=at<=g['span'][1]
                for g in network['gates'])
            if not permitted:
                missing.append(dict(wall_id=wall['id'], position=wall['position'], axis=wall['axis'], at=at))
    return missing


def compartment_cells(root):
    """Recover closed diagram compartments; outlines alone are not doors."""
    walls=wall_segments(root)
    xs=sorted({p for w in walls for p in ([w['position']] if w['axis']=='x' else w['span'])})
    ys=sorted({p for w in walls for p in ([w['position']] if w['axis']=='y' else w['span'])})
    cells={(i,j):(xs[i],ys[j],xs[i+1],ys[j+1]) for i in range(len(xs)-1) for j in range(len(ys)-1)}
    components={}; groups={}
    for cell in cells:
        if cell in components: continue
        group=len(groups); groups[group]=[]; queue=[cell]; components[cell]=group
        while queue:
            current=queue.pop(); groups[group].append(cells[current])
            i,j=current; l,t,r,b=cells[current]
            for neighbor,axis,position,at in (((i-1,j),'x',l,(t+b)/2),((i+1,j),'x',r,(t+b)/2),
                                               ((i,j-1),'y',t,(l+r)/2),((i,j+1),'y',b,(l+r)/2)):
                if neighbor not in cells or neighbor in components: continue
                if any(w['axis']==axis and w['position']==position and w['span'][0]<=at<=w['span'][1] for w in walls): continue
                components[neighbor]=group; queue.append(neighbor)
    return list(groups.values())


def containing_compartment(compartments, point):
    return next((cells for cells in compartments
                 if any(l<=point[0]<r and t<=point[1]<b for l,t,r,b in cells)), [])


def protected_storage_cells(root, manifest, compartments=None):
    """Find storage interiors from closed SVG walls, not tiny label boxes."""
    compartments=compartment_cells(root) if compartments is None else compartments
    result={}
    area_ids=set(manifest.get('supply_area_ids',[]))
    for point in manifest['points']:
        if point['id'] not in area_ids or point['name']=='検品室': continue
        found=containing_compartment(compartments,(point['x'],point['y']))
        if not found:
            raise ValueError('保管場所の部屋をSVGで特定できません: '+point['name'])
        result['area:'+point['id']]=found
    return result


def endpoint_geometry(network, start, end):
    """Connect terminal compartments without making other rooms shortcuts.

    Registered barriers (including zone boundaries) always retain their exact
    gate restrictions. Only unregistered outline portions of the two endpoint
    compartments permit symbolic work/food transfer, as on the base template.
    These are diagram connections, never claims about real physical doors.
    """
    terminals=[]
    for point in (start,end):
        for cell in containing_compartment(network.get('compartments',[]),point):
            if cell not in terminals: terminals.append(cell)
    gates={g['id']:g for g in network['gates']}
    for wall in network['barriers']:
        axis=0 if wall['axis']=='x' else 1
        spans=[]
        for cell in terminals:
            if wall['position'] not in (cell[axis],cell[axis+2]): continue
            lo=max(wall['span'][0],cell[1-axis]); hi=min(wall['span'][1],cell[3-axis])
            if lo<hi: spans.append((lo,hi))
        spans.sort(); merged=[]
        for lo,hi in spans:
            if merged and lo<=merged[-1][1]: merged[-1]=(merged[-1][0],max(hi,merged[-1][1]))
            else: merged.append((lo,hi))
        for protected in wall.get('controlled_spans',[]):
            split=[]
            for lo,hi in merged:
                if protected[1]<=lo or protected[0]>=hi: split.append((lo,hi))
                else:
                    if lo<protected[0]: split.append((lo,protected[0]))
                    if protected[1]<hi: split.append((protected[1],hi))
            merged=split
        for i,span in enumerate(merged):
            gid=f'endpoint-transfer:{wall["id"]}:{i}'
            gates[gid]=dict(id=gid,barrier_id=wall['id'],span=list(span),kind='endpoint_compartment_transfer')
    return terminals,gates


def storage_obstacles(network, allowed, *, receipt=False):
    """Preserve room protection except explicit receipt-only transport bands.

    Region existence is not permission. Only registered receipt_transit_regions
    may clip a storage obstacle, and no wall/gate permission is altered here.
    """
    permissions={}
    if receipt:
        regions={r['id']:tuple(r['bounds']) for r in network.get('regions',[]) if 'id' in r}
        for rule in network.get('receipt_transit_regions',[]):
            if rule['region_id'] not in regions:
                raise ValueError('搬入用描画帯が未登録です: '+rule['region_id'])
            permissions.setdefault(rule['storage_id'],[]).append(regions[rule['region_id']])
    result=[]
    for eid,cells in network.get('protected_storage_cells',{}).items():
        if eid in allowed: continue
        pieces=[(l-.01,t-.01,r+.01,b+.01) for l,t,r,b in cells]
        for cl,ct,cr,cb in permissions.get(eid,[]):
            remaining=[]
            for l,t,r,b in pieces:
                il,it,ir,ib=max(l,cl),max(t,ct),min(r,cr),min(b,cb)
                if il>=ir or it>=ib:
                    remaining.append((l,t,r,b)); continue
                remaining.extend(rect for rect in (
                    (l,t,r,it),(l,ib,r,b),(l,it,il,ib),(ir,it,r,ib))
                    if rect[0]<rect[2] and rect[1]<rect[3])
            pieces=remaining
        result.extend(pieces)
    return result


def route_regions(network, *, receipt=False):
    """Receiving and cooking movements have different registered corridors."""
    if not receipt:
        return [tuple(r['bounds']) for r in network['regions']]
    ids=network.get('receipt_route_regions')
    if not ids:
        raise ValueError('搬入用の通路が未登録です。調理中の通路には置換しません。')
    by_id={r['id']:r for r in network['regions'] if 'id' in r}
    missing=set(ids)-set(by_id)
    if missing:
        raise ValueError('搬入用の通路が見つかりません: '+','.join(sorted(missing)))
    return [tuple(by_id[rid]['bounds']) for rid in ids]


def routing_network(root, network, manifest=None):
    """Replace the duplicated wall map, retaining only existing gate consent.

    Explicit transport barriers remain restricted. Additional drawn outlines
    are compartment borders; only an edge's terminal compartments may cross
    them, through endpoint_geometry rather than global permission.
    """
    from copy import deepcopy
    result=deepcopy(network)
    originals={wall['id']:wall for wall in network['barriers']}
    result['barriers']=wall_segments(root)
    result['gates']=[]
    for wall in result['barriers']:
        wall['controlled_spans']=[
            [max(wall['span'][0],w['span'][0]),min(wall['span'][1],w['span'][1])]
            for w in originals.values() if w['axis']==wall['axis'] and w['position']==wall['position']
            and max(wall['span'][0],w['span'][0])<min(wall['span'][1],w['span'][1])]
        for gate in network['gates']:
            source=originals[gate['barrier_id']]
            if source['axis']!=wall['axis'] or source['position']!=wall['position']:
                continue
            lo=max(wall['span'][0],source['span'][0],gate['span'][0])
            hi=min(wall['span'][1],source['span'][1],gate['span'][1])
            if lo < hi:
                result['gates'].append({**gate,'id':gate['id']+':'+wall['id'],
                                        'barrier_id':wall['id'],'span':[lo,hi]})
    if manifest is not None:
        result['compartments']=compartment_cells(root)
        result['protected_storage_cells']=protected_storage_cells(root,manifest,result['compartments'])
    return result
