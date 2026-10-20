"""Coalesce newly compiled identical work between explicit separate batches.

Source facts remain attached to each material. Saved/operator nodes are never
eligible. This changes the new draft representation, not the cooking method.
"""
from copy import deepcopy
import json

from src.schemas.menu_process import ProcessDocument, material_flow


def coalesce_new_wash_cut_chains(document, plan, inferred_ids, preparation_checks):
    """Same fresh-produce work, kept as separate batches, not physical mixing.

    Requires an already explicit common separate-batch successor and identical
    source cut instructions. Never infer compatibility from menu/material names.
    """
    doc = deepcopy(document)
    nodes = {s['id']: s for s in doc['steps']}
    original = set(plan.get('original_order', []))
    fresh = {c.ingredient_id for c in preparation_checks or []
             if c.state == 'fresh_produce' and c.requirement == 'wash'}
    protected = set((doc.get('route_config') or {}).get('step_ports', {}))
    for material in doc['ingredients']:
        drawing = material.get('drawing') or {}
        protected.update(drawing.get('independent_step_ids', []))
        protected.update(drawing.get('shared_step_ids', []))
        if drawing.get('merge_step_id'):
            protected.add(drawing['merge_step_id'])
    uses = {}
    for step in doc['steps']:
        for ref in step['inputs']:
            if ref.get('step_id'):
                uses.setdefault(ref['step_id'], []).append((step, ref))
    groups = {}
    before, _ = material_flow(doc)
    for cid in plan.get('new_fixed_ids', []):
        cut = nodes.get(cid)
        if not cut or cid in original | protected:
            continue
        if cut['outputs'] or cut['conditions'] or len(cut['inputs']) != 1:
            continue
        ref = cut['inputs'][0]
        wash = nodes.get(ref.get('step_id'))
        if (not wash or wash['id'] not in inferred_ids - original - protected
                or ref.get('output_id') or wash['operation'] != '洗浄'
                or wash['outputs'] or wash['conditions'] or len(wash['inputs']) != 1):
            continue
        if wash['inputs'][0].get('ingredient_id') not in fresh:
            continue
        if (wash['equipment']['type'] != 'シンク' or wash['equipment']['id']
                or not cut['equipment']['type'] or cut['equipment']['id']):
            continue
        if len(uses.get(wash['id'], [])) != 1 or len(uses.get(cid, [])) != 1:
            continue
        target, target_ref = uses[cid][0]
        if (target['id'] not in inferred_ids - original - protected
                or target_ref.get('output_id')
                or target['conditions'].get('processing_mode') != 'separate_batches'):
            continue
        source = cut.get('basis', {}).get('operation', '')
        if not source.startswith('source:'):
            continue
        key = (target['id'], cut['operation'], cut['equipment']['type'], source.split(':', 2)[-1])
        groups.setdefault(key, []).append((wash, cut))
    removed = set()
    changed = set()
    for key, pairs in groups.items():
        if len(pairs) < 2:
            continue
        ids = {s['id'] for pair in pairs for s in pair}
        materials = [before[wash['id']] for wash, _ in pairs]
        if sum(map(len, materials)) != len(set().union(*map(set, materials))):
            continue
        wash, cut = pairs[0]
        evidence = [{'step': deepcopy(s), 'material_ids': before[s['id']]}
                    for pair in pairs for s in pair]
        wash['inputs'] = [ref for w, _ in pairs for ref in w['inputs']]
        cut['inputs'] = [{'step_id': wash['id']}]
        for step in (wash, cut):
            step['conditions'] = {'processing_mode': 'separate_batches',
                                  'description': '各食材を混ぜずに別々に同じ作業を行う。'
                                  + ('原文指定: ' + key[-1] if step is cut else '')}
            step['basis']['grouping'] = 'rule:identical-new-fresh-wash-cut-chains'
            step['basis']['grouped_source_steps'] = json.dumps(evidence, ensure_ascii=False)
        target = nodes[key[0]]
        cut_ids = {c['id'] for _, c in pairs}
        offset = next(i for i, ref in enumerate(target['inputs']) if ref.get('step_id') in cut_ids)
        target['inputs'] = [ref for ref in target['inputs'] if ref.get('step_id') not in cut_ids]
        target['inputs'].insert(offset, {'step_id': cut['id']})
        removed.update(ids - {wash['id'], cut['id']})
        changed.update((wash['id'], cut['id']))
    doc['steps'] = [s for s in doc['steps'] if s['id'] not in removed]
    after, _ = material_flow(doc)
    if any(set(after[sid]) != set(mids) for sid, mids in before.items() if sid not in removed | changed):
        raise ValueError('共通洗浄加工への統合で下流材料が変わりました')
    return ProcessDocument.model_validate(doc).model_dump()


def coalesce_new_preparation(document, plan, inferred_ids):
    doc=deepcopy(document)
    nodes={s['id']:s for s in doc['steps']}
    new_fixed=set(plan.get('new_fixed_ids',[]))
    original=set(plan.get('original_order',[]))
    mutable=set(inferred_ids)|new_fixed
    protected=set((doc.get('route_config') or {}).get('step_ports',{}))
    for material in doc['ingredients']:
        drawing=material.get('drawing') or {}
        protected.update(drawing.get('independent_step_ids',[]))
        protected.update(drawing.get('shared_step_ids',[]))
        if drawing.get('merge_step_id'):
            protected.add(drawing['merge_step_id'])
    before,_=material_flow(doc)
    groups={}
    for step in doc['steps']:
        sid=step['id']
        if (sid not in new_fixed or sid in original|protected or step.get('outputs')
                or len(step['inputs'])!=1 or step.get('conditions')
                or step.get('equipment',{}).get('id')
                or not step.get('equipment',{}).get('type')):
            continue
        source=step['inputs'][0]
        parent=nodes.get(source.get('step_id'))
        if (not parent or parent['id'] not in mutable-original
                or parent.get('conditions',{}).get('processing_mode')!='separate_batches'
                or not source.get('output_id')):
            continue
        output=next((o for o in parent['outputs'] if o['id']==source['output_id']),None)
        if not output or not output.get('ingredient_ids') or output.get('quantity')!='全量':
            continue
        uses=[(n,ref) for n in doc['steps'] for ref in n['inputs'] if ref.get('step_id')==sid]
        if len(uses)!=1:
            continue
        target,ref=uses[0]
        if (target['id'] not in mutable-original or ref.get('output_id')
                or target.get('conditions',{}).get('processing_mode')!='separate_batches'):
            continue
        source_fact=step.get('basis',{}).get('operation','')
        if not source_fact.startswith('source:'):
            continue
        # Dimensions and other source wording must agree, not just "cut".
        instruction=source_fact.split(':',2)[-1]
        key=(parent['id'],target['id'],step['operation'],step['equipment']['type'],instruction)
        groups.setdefault(key,[]).append(step)
    removed=set();changed=set()
    for key, members in groups.items():
        if len(members)<2:
            continue
        ids={s['id'] for s in members}
        material_sets=[set(before[s['id']]) for s in members]
        if sum(map(len,material_sets))!=len(set.union(*material_sets)):
            continue
        first=members[0]
        evidence=[{'step_id':s['id'],'material_ids':before[s['id']],
                   'operation':s['operation'],'basis':s['basis']} for s in members]
        first['inputs']=[ref for s in members for ref in s['inputs']]
        first['conditions']={'processing_mode':'separate_batches',
            'description':'各食材を混ぜずに別々に処理する。原文指定: '+key[-1]}
        first['basis']['grouped_source_steps']=json.dumps(evidence,ensure_ascii=False)
        first['basis']['grouping']='rule:identical-new-source-work-between-separate-batches'
        target=nodes[key[1]]
        refs=[r for r in target['inputs'] if r.get('step_id') in ids]
        label='・'.join(r.get('label','') for r in refs if r.get('label'))
        if len(label)>500:
            raise ValueError('共通前処理の入力ラベルが長すぎます')
        offset=next(i for i,r in enumerate(target['inputs']) if r.get('step_id') in ids)
        target['inputs']=[r for r in target['inputs'] if r.get('step_id') not in ids]
        target['inputs'].insert(offset,{'step_id':first['id'],'label':label})
        removed.update(ids-{first['id']});changed.add(first['id'])
    doc['steps']=[s for s in doc['steps'] if s['id'] not in removed]
    after,_=material_flow(doc)
    if any(set(after[sid])!=set(mids) for sid,mids in before.items() if sid not in removed|changed):
        raise ValueError('共通前処理の統合で下流材料が変わりました')
    return ProcessDocument.model_validate(doc).model_dump()
