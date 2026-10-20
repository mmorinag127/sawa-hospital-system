"""Index explicit receipt transfers for the template's delivery legend.

The legend supplements, never replaces, physical inspection-to-storage routes.
"""


def project_receipts(document, routes, topology, manifest):
    steps={s['id']:s for s in document['steps']}
    points={p['id']:p for p in manifest['points']}
    kept_routes=[]; kept_edges=[]; receipts=[]
    representations=manifest.get('supply_observations',{}).get('receipt_representation',{})
    for route,edge in zip(routes,topology):
        source=steps[edge['from_step_id']]; target=steps[edge['to_step_id']]
        origin=points.get(source.get('_supply_point_id'))
        storage=points.get(target.get('_supply_point_id'))
        if (origin and storage and origin['name']=='検品室'
                and target['operation']=='保管・払出'):
            name=storage['name']
            category='frozen' if name.startswith('冷凍') else 'chilled' if name.startswith('冷蔵') else 'dry'
            rule=representations.get(storage['id'])
            if rule and rule.get('mode') not in ('legend_only','floor_and_receipt_legend'):
                raise ValueError('搬入線の表示方法が不明です: '+storage['id'])
            representation=rule['mode'] if rule else 'floor_and_receipt_legend'
            receipts.append({**edge,'color':route['color'],'representation':representation,
                'inspection_point_id':origin['id'],'storage_point_id':storage['id'],
                'storage_name':name,'category':category,
                **({'representation_basis':rule} if rule else {})})
            if representation=='legend_only':
                continue
        kept_routes.append(route); kept_edges.append(edge)
    return kept_routes,kept_edges,receipts


def paint_receipts(layer, receipts, numbers, element):
    """Use the reserved bottom-left box; wrap numbers without losing entries."""
    categories=[('chilled','食材納品→検品室→冷蔵庫'),
                ('frozen','食材納品→検品室→冷凍庫'),
                ('dry','食材納品→検品室→食物倉庫')]
    rows=[]
    for key,title in categories:
        tokens=[]; seen=set()
        for transfer in receipts:
            if transfer['category']!=key: continue
            for number in sorted({numbers[mid] for mid in transfer['ingredient_ids']}):
                identity=transfer['color'],number,transfer['storage_point_id']
                if identity in seen: continue
                seen.add(identity)
                # Keep the destination explicit, not just a temperature class.
                name=transfer['storage_name'].replace('冷蔵庫','').replace('冷凍庫','')
                text=f"{name}{chr(0x2460+number-1) if number<=20 else number}"
                tokens.append((text,transfer['color']))
        lines=[[]]; width=0
        for text,color in tokens:
            size=sum(8 if ord(c)<128 else 15 for c in text)+10
            if width+size>300 and lines[-1]: lines.append([]); width=0
            lines[-1].append((text,color,width)); width+=size
        rows.append((title,lines))
    top=1197; bottom=top+sum(max(26,len(lines)*22+4) for _,lines in rows)
    element(layer,'rect',x=25,y=1190,width=580,height=max(95,bottom-1190+5),fill='white')
    element(layer,'rect',x=32,y=top,width=560,height=bottom-top,fill='white',stroke='#222',stroke_width=1)
    element(layer,'line',x1=276,y1=top,x2=276,y2=bottom,stroke='#222',stroke_width=1)
    y=top
    for title,lines in rows:
        element(layer,'text',x=37,y=y+19,font_size=16,fill='#222').text=title
        for i,line in enumerate(lines):
            for text,color,x in line:
                element(layer,'text',x=282+x,y=y+18+i*22,font_size=15,fill=color).text=text
        y+=max(26,len(lines)*22+4)
        if y<bottom: element(layer,'line',x1=32,y1=y,x2=592,y2=y,stroke='#222',stroke_width=1)
    return bottom
