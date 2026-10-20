"""Equipment-type requests -> explicit, reviewable physical assignments.

No recipe names, dates, drawing coordinates or reference-day answers are inputs
to the decision. Inventory constraints are data, never invented by the solver.
This proposes placement, not a validated production schedule or food-safety plan.
"""
from copy import deepcopy
import hashlib
import heapq
import itertools
import json
import math


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _prepare_assignment(graph, inventory, operator_choices):
    """Validate the same semantic boundary for new and persisted assignments."""
    nodes={n['id']:n for n in graph['nodes']}
    if len(nodes)!=len(graph['nodes']):raise ValueError('工程ID重複')
    items={x['id']:x for x in inventory}
    if len(items)!=len(inventory):raise ValueError('設備ID重複')
    for item in inventory:
        center=item.get('center')
        if not isinstance(center,(list,tuple)) or len(center)!=2 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in center):
            raise ValueError('設備の位置不正')
        capacity=item.get('capacity_l')
        if capacity is not None and (not isinstance(capacity,(int,float)) or not math.isfinite(capacity) or capacity<=0):
            raise ValueError('設備容量が不正')
    signature=digest(graph)
    choices=operator_choices or {'source_hash':signature,'equipment':{}}
    if choices.get('source_hash')!=signature:raise ValueError('設備確認値の入力が異なる')
    explicit=choices.get('equipment',{})
    if not set(explicit)<=nodes.keys():raise ValueError('設備確認値に不明な工程')
    for e in graph['edges']:
        if set(e)-{'id','from','to','materials','enter_phase','part'}:
            raise ValueError('日次工程の接続に未知属性/描画指定を含めない: '+str(e.get('id')))
    if any(e['from'] not in nodes or e['to'] not in nodes for e in graph['edges']):raise ValueError('工程接続先不存在')
    pending=set(nodes);done=set()
    while pending:
        ready={n for n in pending if all(e['from'] in done for e in graph['edges'] if e['to']==n)}
        if not ready:raise ValueError('工程接続が循環')
        done.update(ready);pending-=ready
    for n in nodes.values():
        if any(k in n for k in ('equipment','equipment_id','profile','entry','exit','align_to','placement','x','y','points','band')):
            raise ValueError('日次工程に設備個体/描画指定を含めない: '+n['id'])
        window=n.get('time_window')
        if window is not None and (len(window)!=2 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in window) or window[0]>=window[1]):
            raise ValueError('使用時間帯が不正: '+n['id'])
    domains={};excluded={}
    for nid,n in nodes.items():
        candidates=[];rejected={}
        for eid,item in sorted(items.items()):
            if n.get('equipment_type') not in item.get('types',[]):continue
            reason=None
            if not item.get('enabled',True):reason='使用不可'
            demand=n.get('required_capacity_l')
            if demand is not None:
                if not isinstance(demand,(int,float)) or not math.isfinite(demand) or demand<=0:raise ValueError('必要容量が不正')
                if item.get('capacity_l') is None:reason='容量未確認'
                elif demand>item['capacity_l']:reason='容量不足'
            food=n.get('required_food_group')
            if food is not None and food not in item.get('allowed_food_groups',[]):reason='用途適合未確認/禁止'
            if reason:rejected[eid]=reason
            else:candidates.append(eid)
        if nid in explicit:
            if explicit[nid] not in candidates:raise ValueError('明示設備が不明/不適合: '+nid+' / '+explicit[nid])
            candidates=[explicit[nid]]
        if not candidates:raise ValueError('設備候補なし: '+nid+' / '+str(n.get('equipment_type'))+' / '+str(rejected))
        domains[nid]=candidates;excluded[nid]=rejected
    return nodes,items,choices,domains,excluded


def _assignment_record(graph,inventory,assigned,cost,prepared):
    nodes,items,choices,domains,excluded=prepared
    explicit=choices['equipment']
    return {'schema_version':1,'source_hash':digest(graph),'inventory_hash':digest(inventory),
            'status':'draft_needs_review','business_correctness':'not_validated',
            'objective':{'transfer_manhattan':cost,'not_a_schedule':True},
            'operator_choices':deepcopy(choices),
            'assignments':[{'node_id':nid,'equipment_id':assigned[nid],
                            'equipment_type':nodes[nid]['equipment_type'],
                            'candidates':domains[nid],'excluded':excluded[nid],
                            'basis':'operator' if nid in explicit else 'type_constraints_and_transfer_distance',
                            'inventory_basis':items[assigned[nid]].get('basis','unconfirmed_inventory'),
                            'review_required':nid not in explicit,
                            'capacity_checked':nodes[nid].get('required_capacity_l') is not None,
                            'time_checked':nodes[nid].get('time_window') is not None}
                           for nid in sorted(nodes)]}


def assignment_proposals(graph, inventory, operator_choices=None, max_states=100000):
    """Yield complete assignments in nondecreasing transfer-distance order.

    Types and explicit constraints filter candidates before any scoring. A hard
    requirement with missing capacity/food-use evidence blocks that candidate.
    Explicit operator choices are source-bound and cannot be displaced by cost.
    Exclusive resources cannot be reused without an explicit nonoverlap window
    or a common location_group. Nonexclusive work surfaces are not schedules.
    """
    prepared=_prepare_assignment(graph,inventory,operator_choices)
    nodes,items,choices,domains,excluded=prepared

    def compatible(nid,eid,assigned):
        group=nodes[nid].get('location_group')
        for other,selected in assigned.items():
            same_group=group is not None and group==nodes[other].get('location_group')
            if same_group:
                if selected!=eid:return False
                continue
            if selected!=eid or not items[eid].get('exclusive',False):continue
            a,b=nodes[nid].get('time_window'),nodes[other].get('time_window')
            if not (a and b and (a[1]<=b[0] or b[1]<=a[0])):return False
        return True

    def distance(a,b):
        return sum(abs(x-y) for x,y in zip(items[a]['center'],items[b]['center']))

    def lower_bound(assigned):
        return sum(min(distance(a,b) for a in ([assigned[e['from']]] if e['from'] in assigned else domains[e['from']])
                       for b in ([assigned[e['to']]] if e['to'] in assigned else domains[e['to']]))
                   for e in graph['edges'])

    degree={n:sum(n in (e['from'],e['to']) for e in graph['edges']) for n in nodes}
    counter=itertools.count();heap=[(lower_bound({}),next(counter),{})];states=0;solutions=0
    while heap:
        cost,_,assigned=heapq.heappop(heap);states+=1
        if states>max_states:raise ValueError('設備割当探索上限。部分割当を採用しない')
        if len(assigned)==len(nodes):
            solutions+=1
            yield _assignment_record(graph,inventory,assigned,cost,prepared)
            continue
        feasible={n:[eid for eid in domains[n] if compatible(n,eid,assigned)] for n in nodes if n not in assigned}
        if any(not values for values in feasible.values()):continue
        nid=min(feasible,key=lambda n:(len(feasible[n]),-degree[n],n))
        for eid in feasible[nid]:
            child={**assigned,nid:eid};heapq.heappush(heap,(lower_bound(child),next(counter),child))
    if not solutions:raise ValueError('設備の同時使用/同一作業場所を満たす割当なし')


def validate_saved_assignment(graph,inventory,record):
    """Revalidate persisted assignments; never trust a stale selected ID."""
    if record.get('source_hash')!=digest(graph):raise ValueError('保存設備割当の工程が変更された')
    if record.get('inventory_hash')!=digest(inventory):raise ValueError('保存設備割当の施設設定が変更された')
    values=record.get('assignments',[])
    mapping={v['node_id']:v['equipment_id'] for v in values}
    if len(mapping)!=len(values) or set(mapping)!={n['id'] for n in graph['nodes']}:raise ValueError('保存設備割当の工程欠落/重複')
    choices=record.get('operator_choices')
    if not isinstance(choices,dict) or 'equipment' not in choices:raise ValueError('保存設備割当の根拠欠落')
    prepared=_prepare_assignment(graph,inventory,choices)
    if any(mapping[nid]!=eid for nid,eid in choices['equipment'].items()):
        raise ValueError('保存割当と元の明示設備が不一致')
    checked=next(assignment_proposals(graph,inventory,{'source_hash':digest(graph),'equipment':mapping}))
    expected=_assignment_record(graph,inventory,mapping,checked['objective']['transfer_manhattan'],prepared)
    if sorted(values,key=lambda a:a['node_id'])!=expected['assignments']:
        raise ValueError('保存設備割当の根拠/候補/確認状態が不一致')
    if any(record.get(k)!=expected[k] for k in ('schema_version','status','business_correctness')):
        raise ValueError('保存設備割当の根拠/状態が不一致')
    # A feasibility check is not operator approval. Original source-bound
    # choices and derived provenance must both match before accepting the data.
    result=deepcopy(record);result['revalidated']=True
    result['objective']=checked['objective']
    return result
