"""Validation and topology preparation for process-route layouts; no SVG geometry."""
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import math
import re

@dataclass(frozen=True)
class PreparedProcessRouteRules:
    request: dict
    routes_by_id: dict
    stages: dict
    exit_points: dict
    through_stages: dict
    dependencies: dict
    order: list
    layout: dict
    run_topology: list
    shared_run_legs: dict
    run_position: dict
    region_by_id: dict
    shared: dict
    leg_sections: dict
    transits: dict
    legacy_routes: list
    number_modes: dict

def prepare_process_route_rules(manifest, request, line_styles):
    if not isinstance(request,dict):raise ValueError('工程入力はJSONオブジェクトで指定してください')
    req=deepcopy(request)
    if type(req.get('schema_version')) is not int or req['schema_version']!=1:raise ValueError('工程入力の版が不明です')
    values=req.get('fields',{})
    if not isinstance(values,dict):raise ValueError('記入欄の形式が不正です')
    normalized={}
    for key,value in values.items():
        matches=[f for f in manifest['fields'] if key in (f['id'],f['name'])]
        if len(matches)!=1 or matches[0]['id'] in normalized:raise ValueError('不明・重複した記入欄: '+str(key))
        normalized[matches[0]['id']]=value
    req['fields']=normalized
    if req.get('date'):
        day=date.fromisoformat(req['date'])
        expected={'month':str(day.month),'day':str(day.day),'weekday':'月火水木金土日'[day.weekday()]}
        values=req.setdefault('fields',{})
        for key,value in expected.items():
            if values.get(key) not in (None,'',value):raise ValueError('対象日と図面の記入日が一致しません')
            values[key]=value
    routes=req.get('routes')
    if not isinstance(routes,list) or len(routes)>40:raise ValueError('動線は40件以内で指定してください')
    index={}
    for p in manifest['points']:
        if 'area_id' in p and (not isinstance(p['area_id'],str) or not p['area_id']):raise ValueError('室の識別子が不正です')
        if p.get('allocation','spread') not in ('fixed','spread'):raise ValueError('接続点の配置方式が不正です')
        if 'side' in p and p['side'] not in ('西接続','東接続','北接続','南接続'):raise ValueError('接続方向が不正です')
        for name in set([p['name'],*p['aliases']]):index.setdefault(name,[]).append(p)
    def resolve(name):
        if not isinstance(name,str) or len(index.get(name,[]))!=1:raise ValueError('不明または曖昧な設備・経由点: '+str(name))
        return index[name][0]
    rm={};stages={};exit_points={};through_stages={};old=[]
    for r in routes:
        if not isinstance(r,dict) or not isinstance(r.get('id'),str) or not r['id'] or r['id'] in rm:raise ValueError('動線IDは重複しない名前が必要です')
        rid=r['id'];rm[rid]=r
        if not isinstance(r.get('label'),str) or not r['label'].strip():raise ValueError('食品名を入力してください: '+rid)
        if 'material_number' not in r and 'material_numbers' not in r:raise ValueError('材料番号を入力してください: '+rid)
        if not isinstance(r.get('stages'),list) or not 2<=len(r['stages'])<=40:raise ValueError('工程は2〜40地点を指定してください')
        stages[rid]=[resolve(n) for n in r['stages']]
        number_stages=r.get('number_stages',list(range(len(stages[rid]))))
        if not isinstance(number_stages,list) or any(type(i)is not int or not 0<=i<len(stages[rid]) for i in number_stages) or len(set(number_stages))!=len(number_stages):raise ValueError('番号表示位置は重複しない工程番号で指定してください')
        exits=r.get('exit_stages',{})
        if not isinstance(exits,dict):raise ValueError('設備の出口指定が不正です')
        for key,name in exits.items():
            if not isinstance(key,str) or not key.isdigit() or str(int(key))!=key or not 0<=int(key)<len(stages[rid])-1:raise ValueError('出口指定の工程番号が不正です')
            entry=stages[rid][int(key)];out=resolve(name)
            same_equipment=entry.get('equipment_id') and entry.get('equipment_id')==out.get('equipment_id') and out['kind']=='equipment_port'
            same_area=entry['kind']=='area_anchor' and out['kind']=='area_anchor' and entry.get('area_id') and entry.get('area_id')==out.get('area_id')
            if not same_equipment and not same_area:raise ValueError('出口には同じ設備または同じ室の接続側を指定してください')
            explicit_return=(out.get('allocation')!='fixed' and
                             (key in r.get('equipment_breaks',{}) or int(key) in r.get('through_stages',[])))
            if out['id']!=entry['id'] or explicit_return:exit_points[(rid,int(key))]=out
        through=r.get('through_stages',[])
        if not isinstance(through,list) or any(type(i)is not int or not 0<=i<len(stages[rid])-1 for i in through) or len(set(through))!=len(through):raise ValueError('設備内通過は重複しない工程番号で指定してください')
        if any((rid,i) not in exit_points for i in through):raise ValueError('設備内通過には同じ設備の別の出口接続点が必要です')
        if any(not stages[rid][i].get('equipment_id') for i in through):raise ValueError('室の入口と出口の間を設備内通過として描画できません')
        breaks=r.get('equipment_breaks',{})
        if not isinstance(breaks,dict):raise ValueError('設備内の省略理由を指定してください')
        for key,reason in breaks.items():
            if not isinstance(key,str) or not key.isdigit() or key!=str(int(key)) or (rid,int(key)) not in exit_points or not stages[rid][int(key)].get('equipment_id') or int(key) in through or not isinstance(reason,str) or not reason.strip():
                raise ValueError('設備内の省略は有効な出口・理由が必要です。通過指定とは併用できません')
        for (owner,i),out in exit_points.items():
            if owner==rid and stages[rid][i].get('equipment_id') and i not in through and str(i) not in breaks:
                raise ValueError('設備の入口と出口が分断されます。設備内通過または省略理由を明示してください: '+rid+' 工程'+str(i+1))
        through_stages[rid]=set(through)
        styles=r.get('segment_styles',[r.get('style')]*(len(stages[rid])-1))
        if (not isinstance(r.get('color'),str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',r['color'])
                or not isinstance(styles,list) or len(styles)!=len(stages[rid])-1
                or any(style not in line_styles for style in styles)):
            raise ValueError('動線の色・線種指定が不正です')
        prepared={k:deepcopy(v) for k,v in r.items() if k not in ('stages','branch_from','pattern_id','exit_stages')}|{'points':r['stages']}
        if 'legend_lines' not in prepared and isinstance(r.get('label'),str):
            field=next(f for f in manifest['fields'] if f['id']=='food_names') if req.get('legend_placement')=='food_names' else manifest['route_legend']
            nums=r.get('material_numbers',[r['material_number']] if 'material_number'in r else [])
            capacity=int(field['width']/field['font_size']);prefix=len(nums)+1 if nums else 0
            label=r['label'];lines=[]
            while label:
                limit=max(1,capacity-(prefix if not lines else 0));lines.append(label[:limit]);label=label[limit:]
            prepared['legend_lines']=lines or ['']
        old.append(prepared)
    def identity(p):return p.get('equipment_id',p.get('area_id',p['id']))
    deps={}
    for rid,r in rm.items():
        if 'branch_from' in r and (not isinstance(r['branch_from'],str) or not r['branch_from']):raise ValueError('分岐元を選択してください')
        if not isinstance(r.get('member_route_ids',[]),list):raise ValueError('合流元は動線IDの一覧で指定してください')
        refs=r.get('member_route_ids',[])+([r['branch_from']] if r.get('branch_from') else [])
        if r.get('branch_from') and r.get('member_route_ids'):raise ValueError('分岐元と合流元は同時に指定できません')
        if any(ref not in rm or ref==rid for ref in refs):raise ValueError('分岐・合流元の動線が不明です')
        if any(identity(stages[ref][-1])!=identity(stages[rid][0]) for ref in refs):raise ValueError('分岐・合流は元の終点と先の始点の設備を一致させてください')
        deps[rid]=refs
    completed=set()
    def walk(rid,active):
        if rid in active:raise ValueError('工程の分岐・合流が循環しています')
        if rid in completed:return
        for dep in deps[rid]:walk(dep,active|{rid})
        completed.add(rid)
    for rid in rm:walk(rid,set())
    layout=req.get('layout',{})
    if not isinstance(layout,dict) or set(layout)-{'geometry_mode','route_order','via','label_sides','label_placement','label_placements','shared_sections','shared_runs','leg_regions','labels_on_path','text_positions','equipment_overlays','equipment_transits','leg_corridors','leg_gates'}:raise ValueError('不明な配置設定です')
    if layout.get('geometry_mode','center-flow') not in ('network','center-flow','fixed-orthogonal'):raise ValueError('動線の描画方式が不明です')
    if 'labels_on_path' in layout and type(layout['labels_on_path']) is not bool:raise ValueError('動線上の番号表示設定が不正です')
    placement=layout.get('label_placement')
    if placement is not None and placement not in ('equipment','on_path'):raise ValueError('番号表示方式が不明です')
    if placement is not None and 'labels_on_path' in layout and layout['labels_on_path'] != (placement=='on_path'):raise ValueError('番号表示方式と旧設定が矛盾しています')
    placements=layout.get('label_placements',{})
    if not isinstance(placements,dict) or set(placements)-set(rm):raise ValueError('番号表示方式の動線が不明です')
    for rid, items in placements.items():
        if not isinstance(items,dict):raise ValueError('番号表示方式は工程番号で指定してください')
        for stage, mode in items.items():
            if not isinstance(stage,str) or not stage.isdigit() or str(int(stage))!=stage or not 0<=int(stage)<len(stages[rid]) or mode not in ('equipment','on_path'):
                raise ValueError('番号表示方式または工程番号が不正です')
    # Endpoint order is the default lane heuristic, not ID/food/date ordering.
    automatic=sorted(rm,key=lambda rid:(stages[rid][0]['y'],stages[rid][-1]['y'],stages[rid][-1]['x'],rid))
    order=layout.get('route_order',automatic)
    if not isinstance(order,list) or len(order)!=len(rm) or set(order)!=set(rm):raise ValueError('線の順序には全動線を重複なく指定してください')
    for key in ('via','label_sides'):
        setting=layout.get(key,{})
        if not isinstance(setting,dict) or set(setting)-set(rm):raise ValueError('配置指定の動線が不明です')
        for rid,items in setting.items():
            if not isinstance(items,dict):raise ValueError('配置指定は地点・区間番号で指定してください')
            for n,value in items.items():
                limit=len(stages[rid])-(1 if key=='via' else 0)
                if not isinstance(n,str) or not n.isdigit() or str(int(n))!=n or not 0<=int(n)<limit:raise ValueError('配置指定の区間・地点番号が不正です')
                if key=='via':
                    if not isinstance(value,list) or len(value)>12:raise ValueError('経由点の指定が不正です')
                    for name in value:
                        if resolve(name)['kind']!='waypoint':raise ValueError('配置の経由点には通路ポイントを選んでください')
                elif value not in ('left','right','top','bottom'):raise ValueError('番号の表示側が不明です')
    # Shared transport is declared topology, never inferred from coincident
    # coordinates. Validate the complete member span before routing anything.
    shared_runs=layout.get('shared_runs',[])
    if not isinstance(shared_runs,list):raise ValueError('共有動線の形式が不正です')
    run_topology=[];shared_run_legs={}
    used_run_legs=set();run_ids=set()
    for run in shared_runs:
        if not isinstance(run,dict) or set(run)!={'id','join_point','split_point','members'} or not isinstance(run['id'],str) or not run['id'] or run['id'] in run_ids:
            raise ValueError('共有動線IDが不正・重複しています')
        run_ids.add(run['id'])
        join,split=resolve(run['join_point']),resolve(run['split_point'])
        if not isinstance(run['members'],list) or len(run['members'])<2:raise ValueError('共有動線には二つ以上の材料区間が必要です')
        members=[];signature=None
        for member in run['members']:
            if not isinstance(member,dict) or set(member)!={'route_id','from_stage','to_stage'}:raise ValueError('共有動線の材料指定が不正です')
            rid,first,last=member['route_id'],member['from_stage'],member['to_stage']
            if not isinstance(rid,str) or rid not in rm or type(first) is not int or type(last) is not int or not 0<=first<last<len(stages[rid]):raise ValueError('共有動線の材料・工程範囲が不正です')
            legs={(rid,i) for i in range(first,last)}
            if used_run_legs & legs:raise ValueError('共有動線の材料区間が重複しています')
            used_run_legs |= legs
            if stages[rid][first]['id']!=join['id'] or stages[rid][last]['id']!=split['id']:
                raise ValueError('共有動線の合流点・分岐点は工程の最初・最後と一致させてください')
            if any(m['route_id']==rid for m in members):raise ValueError('共有動線の材料が重複しています')
            sig=(tuple(p['name'] for p in stages[rid][first:last+1]),tuple(exit_points.get((rid,i),stages[rid][i])['id'] for i in range(first,last)),tuple(i in through_stages[rid] for i in range(first,last)),tuple(rm[rid].get('segment_styles',[rm[rid].get('style')]*(len(stages[rid])-1))[i] for i in range(first,last)),rm[rid]['color'].lower(),rm[rid].get('legend_group'))
            if signature is None:signature=sig
            elif signature!=sig:raise ValueError('共有動線の工程・出口・設備内通過・線種は全材料で一致させてください')
            members.append(dict(route_id=rid,from_stage=first,to_stage=last))
            shared_run_legs.update({(rid,i):run['id'] for i in range(first,last)})
        run_topology.append(dict(id=run['id'],join_point=join['name'],split_point=split['name'],members=members,material_ids=[m['route_id'] for m in members],material_numbers=[rm[m['route_id']].get('material_numbers',[rm[m['route_id']]['material_number']] if 'material_number' in rm[m['route_id']] else []) for m in members],stage_names=list(signature[0])))
    run_position={(m['route_id'],i):(run['id'],i-m['from_stage']) for run in run_topology for m in run['members'] for i in range(m['from_stage'],m['to_stage']+1)}
    regions=manifest.get('route_regions',[])
    if not isinstance(regions,list):raise ValueError('マスターの動線領域が不正です')
    region_by_id={}
    for region in regions:
        if not isinstance(region,dict) or set(region)!={'id','name','bounds'} or not isinstance(region['id'],str) or not region['id'] or region['id'] in region_by_id or not isinstance(region['name'],str) or not isinstance(region['bounds'],list) or len(region['bounds'])!=4 or any(type(v) not in (int,float) for v in region['bounds']) or not (region['bounds'][0]<region['bounds'][2] and region['bounds'][1]<region['bounds'][3]):raise ValueError('マスターの動線領域が不正です')
        region_by_id[region['id']]=region
    leg_regions=layout.get('leg_regions',{})
    if not isinstance(leg_regions,dict) or set(leg_regions)-set(rm):raise ValueError('動線領域の動線IDが不正です')
    for rid,legs in leg_regions.items():
        if not isinstance(legs,dict):raise ValueError('動線領域の区間指定が不正です')
        for key,region_id in legs.items():
            if not isinstance(key,str) or not key.isdigit() or str(int(key))!=key or not 0<=int(key)<len(stages[rid])-1 or not isinstance(region_id,str) or region_id not in region_by_id:raise ValueError('動線領域の区間または領域IDが不正です')
    sections=layout.get('shared_sections',[]);shared={};leg_sections=defaultdict(list)
    if not isinstance(sections,list):raise ValueError('共有描画区間の形式が不正です')
    for section in sections:
        if not isinstance(section,dict) or set(section)!={'id','points','members'} or not isinstance(section['id'],str) or not section['id'] or section['id'] in shared or section['id'] in run_ids:raise ValueError('共有描画区間IDが不正・重複しています')
        sid=section['id'];names=section['points'];members=section['members']
        if not isinstance(names,list) or len(names)<2:raise ValueError('共有描画区間には二つ以上の経由点が必要です')
        points=[resolve(n) for n in names]
        if any(p['kind']!='waypoint' for p in points[:-1]) or points[-1]['kind'] not in ('waypoint','equipment_port') or len({p['id'] for p in points})!=len(points):raise ValueError('共有描画区間は通路ポイントと末端の設備接続点を指定してください')
        if not isinstance(members,list) or len(members)<2:raise ValueError('共有描画区間には二つ以上の材料区間が必要です')
        keys=[];signatures=set()
        for member in members:
            if not isinstance(member,dict) or set(member)!={'route_id','leg'}:raise ValueError('共有描画区間の材料指定が不正です')
            rid,i=member['route_id'],member['leg']
            if not isinstance(rid,str) or rid not in rm or type(i)is not int or not 0<=i<len(stages[rid])-1 or (rid,i) in keys:raise ValueError('共有描画区間の材料・区間が不明または重複しています')
            if (rid,i) in shared_run_legs:raise ValueError('共有動線と共有描画区間は同じ材料区間を重複指定できません')
            if str(i) in layout.get('via',{}).get(rid,{}):raise ValueError('同一区間に共有描画と個別経由点は指定できません')
            keys.append((rid,i));r=rm[rid];styles=r.get('segment_styles',[r.get('style')]*(len(stages[rid])-1))
            if not isinstance(r.get('color'),str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',r['color']) or not isinstance(styles,list) or len(styles)!=len(stages[rid])-1 or any(s not in ('solid','dashed') for s in styles):raise ValueError('共有描画区間の色・線種指定が不正です')
            signatures.add((r['color'].lower(),styles[i],r.get('legend_group')))
        if len(signatures)!=1:raise ValueError('共有描画区間の料理・色・線種は全材料で一致させてください')
        if points[-1]['kind']=='equipment_port' and (points[-1].get('allocation')!='fixed' or any(stages[rid][i+1]['id']!=points[-1]['id'] for rid,i in keys)):raise ValueError('共有描画の設備末端は全材料の同じ固定到着点が必要です')
        shared[sid]=dict(points=points,members=keys,color=next(iter(signatures))[0],style=next(iter(signatures))[1])
        for key in keys:leg_sections[key].append(sid)
    for key,sids in leg_sections.items():
        seen=set();last=None
        for sid in sids:
            for p in shared[sid]['points']:
                if p['id']==last:continue
                if p['id'] in seen:raise ValueError('共有描画区間の順序が循環しています')
                seen.add(p['id']);last=p['id']
    transits={};settings=layout.get('equipment_transits',{})
    if not isinstance(settings,dict) or set(settings)-set(rm):raise ValueError('描画上の設備通過の動線が不明です')
    for rid,legs in settings.items():
        if not isinstance(legs,dict):raise ValueError('描画上の設備通過は区間番号で指定してください')
        for leg_index,entries in legs.items():
            if not isinstance(leg_index,str) or not leg_index.isdigit() or str(int(leg_index))!=leg_index or not 0<=int(leg_index)<len(stages[rid])-1:raise ValueError('描画上の設備通過区間が不正です')
            i=int(leg_index)
            if (rid,i) in leg_sections or leg_index in layout.get('via',{}).get(rid,{}):raise ValueError('描画上の設備通過と共有区間・個別経由点は同一区間に指定できません')
            if not isinstance(entries,list) or not entries:raise ValueError('描画上の設備通過には入口・出口を指定してください')
            resolved=[];seen=set()
            for entry in entries:
                if not isinstance(entry,dict) or set(entry)!={'entry','exit'}:raise ValueError('描画上の設備通過の入口・出口が不正です')
                p,out=resolve(entry['entry']),resolve(entry['exit'])
                if p['kind']!='equipment_port' or out['kind']!='equipment_port' or p.get('equipment_id')!=out.get('equipment_id') or (p['x'],p['y'])==(out['x'],out['y']) or p['equipment_id'] in seen:raise ValueError('描画上の設備通過は同じ設備の異なる接続点を一度だけ指定してください')
                seen.add(p['equipment_id']);resolved.append((p,out))
            transits[(rid,i)]=resolved
    for run in run_topology:
        signatures=[]
        for member in run['members']:
            rid=member['route_id']
            signatures.append([(layout.get('via',{}).get(rid,{}).get(str(i),[]),
                                [(a['id'],b['id']) for a,b in transits.get((rid,i),[])],
                                leg_regions.get(rid,{}).get(str(i)))
                               for i in range(member['from_stage'],member['to_stage'])])
        if any(v!=signatures[0] for v in signatures[1:]):raise ValueError('共有動線の経由点・通過設備・通過範囲は全材料で一致させてください')
    def label_mode(rid, stage):
        return placements.get(rid,{}).get(str(stage), placement or ('on_path' if layout.get('labels_on_path') else 'adjacent'))
    for run in run_topology:
        for offset in range(len(run['stage_names'])):
            modes={label_mode(member['route_id'],member['from_stage']+offset) for member in run['members']}
            if len(modes)!=1:raise ValueError('共有動線でまとめる番号の表示方式を一致させてください')
    for section in shared.values():
        modes={label_mode(rid,leg+1) for rid,leg in section['members']}
        if len(modes)!=1:raise ValueError('共有描画区間でまとめる番号の表示方式を一致させてください')
    number_modes={(rid,i):label_mode(rid,i) for rid,points in stages.items() for i in range(len(points))}
    return PreparedProcessRouteRules(req, rm, stages, exit_points, through_stages, deps, order, layout, run_topology, shared_run_legs, run_position, region_by_id, shared, leg_sections, transits, old, number_modes)
