"""Derive drawing heat history from work, never dates or material names.

Cooling does not undo heating. Separate-batch output selections preserve each
material's history. This is diagram semantics, not proof of safe temperatures.
"""
import re
from copy import deepcopy
import hashlib
from src.schemas.menu_process import material_flow

HEATING = ('炊飯','焼く','焼成','蒸す','蒸し','茹で','ゆで','煮る','煮込','煮含','煮詰','炒め',
           '揚げる','加熱','沸かす','わかす','沸かし','わかし','沸騰','湯通し')


def heating_work(step):
    operation=step.get('operation','')
    conditions=step.get('conditions') or {}
    explicit=conditions.get('heat_treatment')
    if explicit is not None:
        if explicit == 'unresolved':
            raise ValueError('工程の加熱有無が未確定です。方法を確認してください: '+step['id'])
        if explicit not in ('heated','not_heated'):
            raise ValueError('加熱処理の指定が不正です: '+step['id'])
        return explicit=='heated'
    if re.search(r'非加熱|加熱しない|加熱せず|火を通さない',operation):
        return False
    if re.search(r'加熱済み|加熱後|ゆでた|茹でた|焼いた',operation):
        return False  # A mention of earlier work is not a heating operation.
    if any(word in operation for word in HEATING):
        return True
    # A generic cooking card may describe the action in its existing details.
    # Do not read upstream-history phrases such as "ゆでた野菜を冷ます".
    if operation in ('調理','たれ作り','タレ作り','だし取り','だしをとる','油抜き'):
        description=conditions.get('description','')
        if re.search(r'非加熱|加熱しない|加熱せず|火を通さない',description):
            return False
        heated = bool(re.search(r'加熱する|加熱し[、て]|火を通す|沸かす|沸かし|煮立て|熱湯|[茹ゆ]で[るて]',description))
        if operation == '油抜き' and not heated:
            raise ValueError('油抜きの加熱有無が未確定です。熱湯・茹でる等の方法または加熱設定を確認してください: '+step['id'])
        return heated
    return False


def heat_history(document, *, reset_on_cooling=False):
    """Return heated material IDs at each node, independent of node ordering."""
    steps={s['id']:s for s in document['steps']}
    ordered=[];active=set();done=set()
    def order(sid):
        if sid in done:return
        if sid in active:raise ValueError('工程グラフに循環があります')
        active.add(sid)
        for source in steps[sid].get('inputs',[]):
            if source.get('step_id'):order(source['step_id'])
        active.remove(sid);done.add(sid);ordered.append(steps[sid])
    for sid in steps:order(sid)
    flow,outputs=material_flow({**document,'steps':ordered})
    states={}
    for step in ordered:
        sid=step['id'];heated=set();unheated=set()
        for source in step.get('inputs',[]):
            if source.get('ingredient_id'):
                unheated.add(source['ingredient_id']);continue
            if not source.get('step_id'):continue
            previous=source['step_id']
            allowed=outputs[(previous,source['output_id'])] if source.get('output_id') else flow[previous]
            heated.update(states[previous]&set(allowed))
            unheated.update(set(allowed)-states[previous])
        cooling = (any(word in step.get('operation','') for word in ('冷却','冷ます','放冷'))
                   or (step.get('equipment') or {}).get('type') == '冷却器')
        if reset_on_cooling and cooling:heated=set()
        elif heating_work(step):heated=set(flow[sid])
        elif heated & unheated and not reset_on_cooling:
            raise ValueError('同じ材料の加熱済み・未加熱の取り分けが合流しています: '+sid)
        states[sid]=heated
    return states


def project_heat_styles(document,routes,topology):
    """Apply after material projection, so omitted seasonings add no raw lane."""
    states=heat_history(document)
    overrides=document['route_config'].get('edge_styles',{})
    projected_routes=[]; projected_edges=[]
    for route,edge in zip(routes,topology):
        ids=set(edge['ingredient_ids']); heated=ids&states[edge['from_step_id']]
        edge['heated_ingredient_ids']=sorted(heated)
        if route['id'] in overrides:
            route['style']=overrides[route['id']]
            edge['style_basis']='explicit_edge_style'
        elif heated==ids:
            route['style']='dashed';edge['style_basis']='heated_material_history'
        elif not heated:
            route['style']='solid';edge['style_basis']='before_recorded_heating'
        else:
            # Preserve both histories instead of requiring an operator to label
            # the complete mixture as heated or unheated. Each projected line
            # keeps exactly its own materials; the recipe graph is unchanged.
            for label, members, style in (('heated', heated, 'dashed'), ('unheated', ids-heated, 'solid')):
                branch=deepcopy(route); branch_edge=deepcopy(edge)
                branch['id']='heat-'+hashlib.sha256((route['id']+':'+label).encode()).hexdigest()[:24]
                branch['style']=style
                branch_edge['edge_id']=branch['id']
                branch_edge['ingredient_ids']=[mid for mid in edge['ingredient_ids'] if mid in members]
                branch_edge['heated_ingredient_ids']=sorted(members) if label=='heated' else []
                branch_edge['source_edge_id']=route['id']
                branch_edge['style_basis']='separate_material_heat_histories'
                projected_routes.append(branch); projected_edges.append(branch_edge)
            continue
        projected_routes.append(route); projected_edges.append(edge)
    routes[:]=projected_routes; topology[:]=projected_edges
