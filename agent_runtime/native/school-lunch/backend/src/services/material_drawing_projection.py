"""Drawing is a projection of recipe lineage, never deletion of ingredients."""
def require_decisions(document):
    missing=[m['name'] for m in document['ingredients'] if not m.get('drawing')]
    if missing:
        raise ValueError('動線の描画対象が未判定です。工程編集の「描画対象を推論」で判定してください：'+'、'.join(missing))


def drawn_ids(document):
    require_decisions(document)
    return {m['id'] for m in document['ingredients'] if m['drawing']['mode'] != 'annotation'}


def project_connections(document, routes, topology):
    selected=drawn_ids(document)
    drawn_routes=[]; drawn_edges=[]; omitted=[]
    for route,edge in zip(routes,topology):
        ids=[mid for mid in edge['ingredient_ids'] if mid in selected]
        if not ids:
            omitted.append(edge)
            continue
        drawn_routes.append(route)
        drawn_edges.append({**edge,'all_ingredient_ids':edge['ingredient_ids'],'ingredient_ids':ids})
    return drawn_routes,drawn_edges,omitted


def shared_visit_groups(document, materials_by_step):
    """Only explicitly declared common work may share a equipment visit."""
    by_id={m['id']:m for m in document['ingredients']}
    groups={}
    for step in document['steps']:
        members=[by_id[mid] for mid in materials_by_step[step['id']]
                 if by_id[mid].get('drawing',{}).get('mode')!='annotation']
        if not members: continue
        keys={m.get('drawing',{}).get('group_id') for m in members}
        if len(keys)!=1 or not next(iter(keys)): continue
        if not all(m['drawing']['mode']=='shared' and (step.get('_supply_point_id') or
                   step['id'] in m['drawing']['shared_step_ids']) for m in members): continue
        key=(next(iter(keys)),step['equipment']['id'],step['operation'])
        groups.setdefault(key,[]).append(step['id'])
    return list(groups.values())
