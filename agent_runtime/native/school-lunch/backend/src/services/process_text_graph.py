"""Lossless cooking-phase projection into a draft place graph.

No date, menu recipe, expected drawing or reference coordinates are used here.
The phase nodes retain late additions; the renderer co-locates consecutive work
at the same equipment without making an extra external journey.
"""
import json
from src.schemas.menu_process import ProcessDocument, material_flow
from src.services.process_heat_history import heating_work


def to_document(answer, menu, *, template_id):
    foods=answer['materials']
    source={str(m['id']):m for m in menu['ingredients']}
    if set(source)!={f['material_id'] for f in foods} or len(foods)!=len(source):
        raise ValueError('工程案と手配表の材料が一致しません')
    nodes={}; component_nodes={}; warnings=[]

    def node(sid, works, inputs, *, separate=False):
        kinds={w['equipment'] for w in works}
        if len(kinds)!=1:raise ValueError('異なる設備の作業をまとめられません: '+sid)
        operations=list(dict.fromkeys(w['work'] for w in works))
        nodes[sid]={'id':sid,'operation':'／'.join(operations),'inputs':inputs,
                    'equipment':{'type':None if next(iter(kinds))=='不明' else next(iter(kinds)),'id':None},
                    'conditions':{'processing_mode':'separate_batches'} if separate else {},
                    'basis':{'review':'pending','source':'text-process-inference',
                             'operation':' / '.join(dict.fromkeys(w['basis'] for w in works))},
                    'outputs':[]}
        return sid

    # Establish every component phase before attaching incoming material paths.
    for ci,part in enumerate(answer['components']):
        ids=[];notes=[];place_index=0;previous_kind=None
        for wi,work in enumerate(part['works']):
            if work['equipment']=='不明' and work['work']=='提供' and wi==len(part['works'])-1 and not part['next_component'] and ids:
                notes.append(work['work']+'（場所未設定）')
                warnings.append(part['name']+'：提供場所は未設定。直前の盛付工程に未設定注記を保持しました。')
                continue
            sid=f'c{ci}-w{wi}'
            node(sid,[work],[{'step_id':ids[-1]}] if ids else [])
            nodes[sid]['conditions']['component_name']=part['name']
            if work['equipment']!=previous_kind:place_index+=1
            nodes[sid]['conditions']['location_group']=f'c{ci}-place{place_index}'
            previous_kind=work['equipment']
            ids.append(sid)
        if not ids:raise ValueError('空の料理部分です: '+part['name'])
        component_nodes[part['name']]=ids
        if notes:nodes[ids[-1]]['conditions']['terminal_unlocated_work']=' / '.join(notes)

    def entry(name,index):
        if name not in component_nodes or not 0<=index<len(component_nodes[name]):
            raise ValueError('投入工程が存在しません: '+name)
        return component_nodes[name][index]

    for part in answer['components']:
        if part['next_component']:
            nodes[entry(part['next_component'],part['next_work_index'])]['inputs'].append(
                {'step_id':component_nodes[part['name']][-1]})

    # Only declared numbering peers with identical equipment sequences and
    # destination may share PREPARATION cards. They remain separate batches.
    prep_groups={}
    def thermal_signature(work):
        try:heat=heating_work({'id':'signature','operation':work['work']})
        except ValueError:heat=('unresolved',work['work'])
        cooling=work['equipment']=='冷却器' or any(t in work['work'] for t in ('冷却','冷ます','放冷'))
        return heat,cooling
    for fi,food in enumerate(foods):
        key=(food['group'], tuple((w['equipment'],thermal_signature(w)) for w in food['preparation']),
             food['component'],food['entry_work_index']) if food['draw'] and food['group'] else ('solo',fi)
        prep_groups.setdefault(key,[]).append(food)
    for gi,members in enumerate(prep_groups.values()):
        previous=[{'ingredient_id':'m'+f['material_id']} for f in members]
        for wi in range(len(members[0]['preparation'])):
            sid=f'p{gi}-w{wi}'
            node(sid,[f['preparation'][wi] for f in members],previous,separate=len(members)>1)
            nodes[sid]['conditions']['work_phase']='preparation'
            nodes[sid]['conditions']['material_operations']=json.dumps(
                {'m'+f['material_id']:f['preparation'][wi]['work'] for f in members},ensure_ascii=False)
            previous=[{'step_id':sid}]
        if len(members)>1 and not members[0]['preparation']:
            # Empty preparation is not itself a mixing event.
            previous=[{'ingredient_id':'m'+f['material_id']} for f in members]
        target=entry(members[0]['component'],members[0]['entry_work_index'])
        nodes[target]['inputs'].extend(previous)

    ordered=[];done=set()
    while len(done)<len(nodes):
        ready=[sid for sid,n in nodes.items() if sid not in done and
               all(not ref.get('step_id') or ref['step_id'] in done for ref in n['inputs'])]
        if not ready:raise ValueError('循環または存在しない前工程があります')
        for sid in ready:
            if not nodes[sid]['inputs']:raise ValueError('材料から到達しない工程です: '+sid)
            for ei,ref in enumerate(nodes[sid]['inputs']):
                if ref.get('step_id'):ref['edge_id']=f'e-{sid}-{ei}'
            ordered.append(nodes[sid]);done.add(sid)
    ingredients=[{'id':'m'+f['material_id'],'name':source[f['material_id']]['ingredient']['name'],'state':''} for f in foods]
    doc={'schema_version':2,'ingredients':ingredients,'steps':ordered,
         'route_config':{'template_id':template_id,'step_ports':{}}}
    flow,_=material_flow(doc)
    for food,material in zip(foods,ingredients):
        mid=material['id']
        peers=['m'+f['material_id'] for f in foods if f['draw'] and f['group']==food['group']]
        path=[s['id'] for s in ordered if mid in flow[s['id']]]
        shared=[s['id'] for s in ordered if set(peers)<=set(flow[s['id']])] if food['draw'] and len(peers)>1 else []
        if food['draw'] and len(peers)>1 and not shared:raise ValueError('共通番号の共通工程がありません')
        material['drawing']={'role':'main' if food['draw'] else 'seasoning',
            'mode':'shared' if shared else 'independent' if food['draw'] else 'annotation',
            'group_id':f'group-{foods.index(next(f for f in foods if f["group"]==food["group"]))}' if shared else '',
            'independent_step_ids':[sid for sid in path if sid not in shared],
            'shared_step_ids':shared,'reason':food['reason'],'status':'pending'}
    validated=ProcessDocument.model_validate(doc).model_dump()
    return validated,warnings
