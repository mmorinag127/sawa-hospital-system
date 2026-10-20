"""Compile editable semantic decisions into physical operation graphs.

No date/name routing, drawing geometry, database access or LLM call lives here.
Model decisions remain proposals. Source locks and explicit operator decisions
are independently recorded; compilation never grants user approval.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict
from src.services.menu_process_source_rules import extract_constraints, validate_constraints


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


Prep = Literal['wash', 'peel_manual', 'peel_machine', 'cut_manual', 'grate_manual', 'slice',
               'blend', 'soak', 'thaw', 'blanch', 'drain_hand', 'open', 'unknown']
Method = Literal['boil', 'simmer', 'stir_fry', 'fry', 'steam', 'bake', 'roast',
                 'mix', 'coat', 'stock', 'rice', 'ready', 'unknown']


class MaterialChoice(Strict):
    id: str
    state: Literal['fresh_produce', 'raw_meat_fish', 'frozen', 'dried', 'processed',
                   'ready', 'seasoning', 'water', 'unknown']
    storage: Literal['米倉庫', '食物倉庫', '冷蔵', '冷凍', '厨房内供給', '要確認']
    washed: Literal['yes', 'no', 'unknown']
    peeled: Literal['yes', 'no', 'not_needed', 'unknown']
    preparation: list[Prep]
    reason: str
    uncertainties: list[str]


class MaterialUse(Strict):
    kind: Literal['material']
    id: str
    part: Literal['']


class BatchUse(Strict):
    kind: Literal['batch']
    id: str
    part: str


Use = MaterialUse | BatchUse


class Phase(Strict):
    description: str
    method: Method
    uses: list[Use]
    source_quote: str


class Part(Strict):
    id: str
    material_ids: list[str]
    kind: Literal['whole', 'portion', 'separation', 'extract', 'residue']
    quantity: str
    destination: Literal['next', 'serve', 'discard', 'unknown']


class Batch(Strict):
    id: str
    label: str
    equipment: Literal['回転釜', 'スチコン', 'ゼットオーブン', 'フライヤー',
                       '清潔側作業台', '魚肉用作業台', '炊飯器', '要確認']
    phases: list[Phase]
    finishing: list[Literal['cool', 'drain_machine', 'drain_hand', 'cut_clean']]
    parts: list[Part]
    reason: str


class Conflict(Strict):
    source_quote: str
    affected_ids: list[str]
    reason: str


class Decisions(Strict):
    materials: list[MaterialChoice]
    batches: list[Batch]
    conflicts: list[Conflict]
    uncertainties: list[str]


PREP = {
    'wash': ('洗浄', '野菜用シンク'), 'peel_manual': ('皮むき・下処理', '前処理作業台'),
    'peel_machine': ('機械皮むき', '球根皮剥機'), 'cut_manual': ('切裁', '前処理作業台'),
    'slice': ('切裁', 'スライサー'), 'blend': ('すりおろし・粉砕', 'ミキサー'),
    'grate_manual': ('すりおろし', '前処理作業台'),
    'soak': ('水戻し', '中央シンク'), 'thaw': ('解凍', '中央シンク'),
    'blanch': ('下茹で', '回転釜'), 'drain_hand': ('水切り', '前処理作業台'),
    'open': ('開封・取出し', '加工室作業台'), 'unknown': ('前処理要確認', '要確認'),
}
FINISH = {'cool': ('冷却', '冷却器'), 'drain_machine': ('脱水', '脱水機'),
          'drain_hand': ('水切り', '清潔側作業台'), 'cut_clean': ('切裁', '清潔側作業台')}
METHODS = {'boil': '茹でる', 'simmer': '煮る', 'stir_fry': '炒める', 'fry': '揚げる',
           'steam': '蒸す', 'bake': '焼く', 'roast': '炒る', 'mix': '和える・混ぜる',
           'coat': '下味・下衣', 'stock': 'だしを取る', 'rice': '炊飯',
           'ready': '提供準備', 'unknown': '調理方法要確認'}
METHOD_EQUIPMENT = {
    'boil': {'回転釜'}, 'simmer': {'回転釜', 'スチコン'}, 'stir_fry': {'回転釜'},
    'fry': {'フライヤー'}, 'steam': {'スチコン'},
    'bake': {'スチコン', 'ゼットオーブン'}, 'roast': {'回転釜', 'スチコン', 'ゼットオーブン'},
    'mix': {'清潔側作業台', '回転釜'}, 'coat': {'魚肉用作業台', '清潔側作業台'},
    'stock': {'回転釜'}, 'rice': {'炊飯器'}, 'ready': {'清潔側作業台'},
}


def norm(x):
    return unicodedata.normalize('NFKC', x).replace(' ', '').replace('\u3000', '')


def signature(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def aliases(name):
    base = re.split('[（(]', norm(name))[0]
    result = {base}
    groups = [('じゃがいも', 'じゃが芋'), ('玉葱', '玉ねぎ', 'たまねぎ'),
              ('西洋かぼちゃ', 'かぼちゃ'), ('青ピーマン', 'ピーマン'),
              ('こまつな', '小松菜'), ('にんにく', 'ニンニク')]
    for group in groups:
        if base in group:
            result.update(group)
    return sorted(result, key=lambda x: (-len(x), x))


def plain_rice(menu):
    return (bool(menu['materials']) and not menu['instructions'].strip()
            and all(norm(m['name']) in {'水稲穀粒(精白米)', '精白米', '強化米', '白米'}
                    for m in menu['materials']))


def source_facts(menu):
    facts = {}
    body = norm(menu['instructions'])
    clauses = [s for s in re.split('[。\n]', body) if s]
    for material in menu['materials']:
        name, note = norm(material['name']), norm(material['material_process'])
        f = {'locks': {}, 'required_actions': [], 'quotes': []}
        if '冷凍' in name:
            f['locks'].update(state='frozen', storage='冷凍')
        # 「冷」alone does not specify fridge versus freezer.
        if name == '水':
            f['locks'].update(state='water', storage='厨房内供給')
        if re.search('洗浄済', name):
            f['locks']['washed'] = 'yes'
        if re.search('皮むき済|皮剥き済', name):
            f['locks']['peeled'] = 'yes'
        if re.search('水煮|缶詰|ペースト|粉末', name) and '冷凍' not in name:
            f['locks']['state'] = 'processed'
        if re.search('ミキサー', note):
            f['required_actions'].append('blend')
        elif re.search('おろす|すりおろ', note):
            f['required_actions'].append('grate')
        if re.search('切|カット|短冊|くし形|千切|イチョウ|いちょう|ささがき', note):
            f['cut_specification'] = material['material_process']
        # A delivered shape is not an on-site action. An explicit imperative
        # remains an action even when the ingredient is canned/precooked.
        if re.search('切る|切って|カットする|カットして', note):
            f['required_actions'].append('cut')
        for pattern, action in [('下茹|下ゆで|塩茹|塩ゆで', 'boil'), ('炒る|炒り', 'roast'),
                                ('戻す|戻し', 'soak'), ('だしを取|出汁を取', 'stock')]:
            if re.search(pattern, note):
                f['required_actions'].append(action)
        if re.fullmatch(r'[\d./]+(?:g|kg|ヶ|個|本|切)(?:[×x*][\d.]+)?', note):
            f['quantity_only'] = note
        equipment_locks = []
        for clause in clauses:
            if not any(a in clause for a in aliases(name)):
                continue
            if re.search('切る|カット|に切', clause):
                if 'cut' not in f['required_actions']:
                    f['required_actions'].append('cut')
                f.pop('quantity_only', None)
            for eq in ['スチコン', 'ゼットオーブン', 'フライヤー', 'ミキサー']:
                if eq not in clause:
                    continue
                # Do not attribute "milk and mixer-processed apple" to milk.
                before, after = clause.split(eq, 1)
                subject = re.split('[,，、]', before)[-1]
                direct = any(re.search(re.escape(a) + r'[^。]*[はを][^。]*$', subject)
                             or re.search(r'^.{0,12}' + re.escape(a), after) for a in aliases(name))
                if direct:
                    equipment_locks.append({'equipment': eq, 'source_quote': clause})
            f['quotes'].append(clause)
        f['equipment_locks'] = equipment_locks
        facts[material['id']] = f
    return facts


def source_fragments(menu):
    return list(dict.fromkeys([s for s in re.split('[。\n]', menu['instructions']) if s.strip()]
           + [x for m in menu['materials'] for x in [m['name'],m['material_process']] if x]))


def decision_schema(menu=None):
    value = Decisions.model_json_schema()
    def compact(v):
        if isinstance(v, dict):
            return {k: compact(x) for k, x in v.items() if k not in {'title', 'default'}}
        if isinstance(v, list):
            return [compact(x) for x in v]
        return v
    value = compact(value)
    # Whole/quantity-split membership is determined by the compiler. Do not
    # spend inference on repeating a material union already known in code.
    part = value['$defs']['Part']
    branches = []
    for kinds, bounds in [(['whole', 'portion'], {'maxItems': 0}),
                           (['separation', 'extract', 'residue'], {'minItems': 1})]:
        branch = deepcopy(part)
        branch['properties']['kind'] = {'type':'string','enum':kinds}
        branch['properties']['material_ids'].update(bounds)
        branches.append(branch)
    value['$defs']['Part'] = {'anyOf': branches}
    if menu is not None:
        fragments = source_fragments(menu)
        value['$defs']['Phase']['properties']['source_quote'] = {'type':'string','enum':['']+fragments}
        value['$defs']['Conflict']['properties']['source_quote'] = {'type':'string','enum':fragments}
    return value


def rice_decisions(menu):
    ids = [m['id'] for m in menu['materials']]
    return {'materials': [dict(id=mid, state='processed', storage='米倉庫', washed='unknown',
                 peeled='not_needed', preparation=[], reason='ordinary_rice', uncertainties=[]) for mid in ids],
            'batches': [dict(id='rice', label='普通ご飯', equipment='炊飯器',
                phases=[dict(description='炊飯前処理を含む炊飯', method='rice',
                    uses=[dict(kind='material', id=mid, part='') for mid in ids], source_quote='')],
                finishing=[], parts=[dict(id='whole', material_ids=ids, kind='whole', quantity='全量',
                                          destination='serve')], reason='ordinary_rice')],
            'conflicts': [], 'uncertainties': []}


def compile_process(menu, decisions, *, operator_choices=None):
    """No fallbacks: invalid proposal raises; unresolved choices stay visible.

    operator_choices is an explicit field patch with matching source signature;
    it is never populated from observations or by the model.
    """
    d = Decisions.model_validate(decisions).model_dump()
    mids = [m['id'] for m in menu['materials']]
    if len(set(mids)) != len(mids) or not mids:
        raise ValueError('材料IDが空または重複')
    if Counter(x['id'] for x in d['materials']) != Counter(mids):
        raise ValueError('材料IDの欠落/追加/重複')
    facts = source_facts(menu)
    source_constraints = extract_constraints(menu, norm, aliases)
    issues = list(d['uncertainties'])
    blocked = set()
    source = norm(menu['instructions'])
    source_texts = [source] + [norm(value) for m in menu['materials'] for value in [m['name'],m['material_process']]]
    for c in d['conflicts']:
        if not c['source_quote'] or not any(norm(c['source_quote']) in text for text in source_texts):
            raise ValueError('資料不一致の引用が原文にない')
        if not set(c['affected_ids']) <= set(mids):
            raise ValueError('資料不一致の材料IDが原文にない')
        blocked.update(c['affected_ids'])
        issues.append('資料不一致: ' + c['reason'])
    decisions_by_id = {x['id']: x for x in d['materials']}
    overrides = operator_choices or {'source_hash': signature(menu), 'materials': {}}
    if overrides['source_hash'] != signature(menu):
        raise ValueError('確認値の入力原本が異なる')
    if not set(overrides['materials']) <= set(mids):
        raise ValueError('確認値の材料IDが原文にない')
    for mid, values in overrides['materials'].items():
        allowed = {'state', 'storage', 'washed', 'peeled', 'preparation'}
        if not set(values) <= allowed:
            raise ValueError('確認値の変更項目が不正')
        decisions_by_id[mid].update(values)
        MaterialChoice.model_validate(decisions_by_id[mid])
    nodes, edges, streams, seen_uses = [], [], {}, Counter()
    paths = {mid: [] for mid in mids}

    def node(nid, operation, equipment, material_ids, phases, basis):
        nodes.append(dict(id=nid, operation=operation, equipment=equipment,
                          material_ids=sorted(material_ids), phases=phases, basis=basis))
        for mid in material_ids:
            paths[mid].append(nid)
        return nid

    def connect(parent, child, ids, phase='', part=''):
        edges.append(dict(from_id=parent, to_id=child, material_ids=sorted(ids), enter_phase=phase, part=part))

    effective = []
    for m in menu['materials']:
        mid = m['id']; choice = decisions_by_id[mid]; f = facts[mid]
        fixed = plain_rice(menu)
        provenance = {field: 'inferred' for field in ['state', 'storage', 'washed', 'peeled', 'preparation']}
        for field, value in f['locks'].items():
            if choice[field] != value:
                raise ValueError(f'原文明示値と矛盾 {mid}.{field}: {value}')
            provenance[field] = 'source'
        for field in overrides['materials'].get(mid, {}):
            if field not in f['locks']:
                provenance[field] = 'user'
        if fixed:
            provenance = {field: 'fixed:ordinary_rice' for field in provenance}
        prep = list(choice['preparation'])
        if choice['state'] == 'water' and prep:
            raise ValueError('配合水に前処理を割り当てない')
        if choice['state'] in {'raw_meat_fish', 'frozen'} and 'wash' in prep:
            raise ValueError(f'{mid}: 生肉魚/冷凍品へ生鮮野菜の洗浄規則を適用しない。preparationからwashを除く')
        if choice['state'] == 'fresh_produce' and choice['washed'] != 'yes':
            if 'wash' not in prep:
                prep.insert(0, 'wash')
            elif prep[0] != 'wash' and prep[0] != 'peel_machine':
                raise ValueError(f'{mid}: 洗浄前に別の加工を置かない')
        if choice['washed'] == 'yes' and 'wash' in prep:
            raise ValueError(f'{mid}: 洗浄済みと再洗浄選択が矛盾。washedは納品時点の状態')
        if choice['peeled'] in {'yes', 'not_needed'} and set(prep) & {'peel_manual', 'peel_machine'}:
            raise ValueError(f'{mid}: 皮むき済み/不要と皮むき選択が矛盾。peeledは納品時点の状態')
        if len(prep) != len(set(prep)):
            raise ValueError('同じ前処理が重複')
        if f.get('quantity_only') and set(prep) & {'cut_manual', 'slice'}:
            raise ValueError(f'数量規格から切断を創作 {mid}')
        # Explicit material processes are known. Their missing choices are not
        # erased: machine/hand alternatives must be selected before compilation.
        for action in f['required_actions']:
            if action == 'blend' and 'blend' not in prep:
                raise ValueError(f'明示ミキサーの欠落 {mid}')
            if action == 'grate' and not set(prep) & {'blend', 'grate_manual'}:
                raise ValueError(f'おろし加工方法の欠落 {mid}')
            if action == 'cut' and not set(prep) & {'cut_manual', 'slice', 'blend'}:
                # A clean-side cut after heating is also a valid location.
                if not any('cut_clean' in b['finishing'] and any(u['kind']=='material' and u['id']==mid
                        for p in b['phases'] for u in p['uses']) for b in d['batches']):
                    raise ValueError(f'明示切裁の欠落 {mid}')
        if f.get('cut_specification') and choice['state'] == 'fresh_produce' and not set(prep) & {'cut_manual', 'slice', 'blend'}:
            raise ValueError(f'未加工生鮮品の切裁指定が未反映 {mid}')
        supply = node('supply:' + mid, '保管' if choice['state'] != 'water' else '厨房内供給',
                      choice['storage'], {mid}, [], provenance['storage'])
        if choice['state'] != 'water':
            receipt = node('receipt:' + mid, '検品', '検品室', {mid}, [], 'rule:external_receipt')
            connect(receipt, supply, {mid})
            paths[mid] = [receipt, supply]
        last = supply
        for index, action in enumerate(prep):
            op, eq = PREP[action]
            if action == 'cut_manual' and choice['state'] == 'raw_meat_fish':
                eq = '魚肉用作業台'
            previous = next(n for n in nodes if n['id'] == last)
            if last.startswith('prep:') and previous['equipment'] == eq and eq != '要確認':
                previous['operation'] += '・' + op
                previous['phases'].append(m['material_process'])
                continue
            nid = node(f'prep:{mid}:{index}', op, eq, {mid}, [m['material_process']],
                       'rule:preparation_from_' + provenance['preparation'])
            connect(last, nid, {mid}); last = nid
        if mid in blocked:
            nid = node('unresolved:' + mid, '資料不一致・工程要確認', '要確認', {mid}, [], 'unresolved')
            connect(last, nid, {mid}); last = nid
        streams[('material', mid, '')] = dict(node=last, material_ids={mid}, destination='unknown' if mid in blocked else 'next')
        effective.append(dict(**choice, resolved_preparation=prep, provenance=provenance,
                              source_facts=f, review='fixed' if fixed else 'needs_review'))
        issues.extend(f'{mid}: {x}' for x in choice['uncertainties'])

    batches = {b['id']: b for b in d['batches']}
    if len(batches) != len(d['batches']):
        raise ValueError('調理バッチID重複')
    pending = set(batches); done = set()
    while pending:
        ready = [bid for bid in pending if all(u['kind'] == 'material' or u['id'] in done
                 for p in batches[bid]['phases'] for u in p['uses'])]
        if not ready:
            raise ValueError('調理バッチの循環/参照不存在')
        for bid in sorted(ready):
            b = batches[bid]; ids = set(); feeds = []; phases = []
            if not b['phases'] or not b['parts']:
                raise ValueError('調理バッチに作業/出力がない')
            last_source_position = -1
            for index, p in enumerate(b['phases']):
                pid = f'p{index+1}'
                if p['source_quote'] and not any(norm(p['source_quote']) in text for text in source_texts):
                    raise ValueError('作業の引用が原文にない')
                if p['source_quote'] and norm(p['source_quote']) in source:
                    position = source.index(norm(p['source_quote']))
                    if position < last_source_position:
                        raise ValueError('同一バッチで原文の投入順を逆転')
                    last_source_position = position
                if b['equipment'] != '要確認' and p['method'] != 'unknown' and b['equipment'] not in METHOD_EQUIPMENT[p['method']]:
                    raise ValueError(f'作業と設備が不適合 {bid}/{p["method"]}/{b["equipment"]}')
                phase_ids = set()
                for u in p['uses']:
                    key = (u['kind'], u['id'], u['part'])
                    if key not in streams:
                        raise ValueError(f'調理材料/出力参照不存在 {key}')
                    stream = streams[key]
                    if stream['destination'] != 'next':
                        raise ValueError(f'提供/廃棄/不明とした出力を調理へ使用 {key}。資料不一致の影響材料は使わない')
                    seen_uses[key] += 1
                    if seen_uses[key] != 1:
                        raise ValueError('材料/中間品の二重投入。明示的な分量分岐が必要')
                    ids.update(stream['material_ids']); phase_ids.update(stream['material_ids'])
                    feeds.append((stream, pid, u['part']))
                phases.append(dict(id=pid, description=p['description'], method=p['method'],
                                   arriving_material_ids=sorted(phase_ids), material_ids=sorted(ids),
                                   source_quote=p['source_quote']))
            if not ids:
                raise ValueError('材料のない調理バッチ')
            nid = node('batch:' + bid, '・'.join(dict.fromkeys(METHODS[p['method']] for p in b['phases'])),
                       b['equipment'], ids, phases, 'fixed:ordinary_rice' if plain_rice(menu) else 'inferred:batch_semantics')
            nodes[-1]['arrival_origins'] = {mid: sorted({s['node'] for s, _, _ in feeds if mid in s['material_ids']})
                                           for mid in ids}
            for stream, pid, part in feeds:
                connect(stream['node'], nid, stream['material_ids'], pid, part)
            last = nid
            if 'drain_machine' in b['finishing'] and ('cool' not in b['finishing'] or
                    b['finishing'].index('drain_machine') < b['finishing'].index('cool')):
                raise ValueError('機械脱水は冷却の後。加熱後の共通順に矛盾')
            for index, action in enumerate(b['finishing']):
                op, eq = FINISH[action]
                fid = node(f'finish:{bid}:{index}', op, eq, ids, [], 'rule:finishing_from_inferred_choice')
                connect(last, fid, ids); last = fid
            used_ids = set()
            part_ids = [p['id'] for p in b['parts']]
            if len(set(part_ids)) != len(part_ids):
                raise ValueError('出力名重複')
            for p in b['parts']:
                if p['kind'] in {'whole', 'portion'}:
                    if p['material_ids'] and set(p['material_ids']) != ids:
                        raise ValueError(f'全量出力 {bid}/{p["id"]} はmaterial_idsを[]とする。構成材料はコードが計算')
                    pidset = set(ids)
                else:
                    pidset = set(p['material_ids'])
                if not pidset or not pidset <= ids or len(set(p['material_ids'])) != len(p['material_ids']):
                    raise ValueError('出力に材料追加/空/重複')
                used_ids |= pidset
                streams[('batch', bid, p['id'])] = dict(p, node=last, material_ids=pidset)
            if used_ids != ids:
                raise ValueError('出力から材料が欠落')
            for mid in ids:
                parts = [p for p in b['parts'] if p['kind'] in {'whole','portion'} or mid in p['material_ids']]
                if len(parts) > 1 and not (all(p['kind']=='portion' and p['quantity'] for p in parts)
                         or {p['kind'] for p in parts} <= {'extract', 'residue'}):
                    raise ValueError('分割理由なしの材料複製')
            done.add(bid); pending.remove(bid)

    serving = []; discarded = []; unresolved = []
    for key, stream in streams.items():
        if stream['destination'] == 'next':
            if seen_uses[key] != 1:
                raise ValueError(f'材料/中間品の行先欠落 {key}')
        elif seen_uses[key]:
            raise ValueError('終端を再消費')
        elif stream['destination'] == 'serve':
            serving.append((key, stream))
        elif stream['destination'] == 'discard':
            discarded.append((key, stream))
        else:
            unresolved.append((key, stream))
    if serving:
        ids = set().union(*(s['material_ids'] for _, s in serving))
        eq = '米飯受渡し台' if plain_rice(menu) else '盛付台'
        last = node('serve', '盛付・配分', eq, ids, [], 'rule:serving')
        for key, s in serving:
            connect(s['node'], last, s['material_ids'], part=key[2])
        out = node('dispatch', '搬出', '搬出準備室', ids, [], 'rule:dispatch')
        connect(last, out, ids)
    for key, s in discarded:
        nid = node('discard:' + ':'.join(key), '除去物の廃棄', '要確認', s['material_ids'], [], 'inferred:residue')
        connect(s['node'], nid, s['material_ids'], part=key[2])
        issues.append('除去物の廃棄先を確認')
    for key, _ in unresolved:
        issues.append('工程未確定: ' + ':'.join(key))

    # Check source-locked operations/equipment on the material's actual chain,
    # never merely somewhere in another ingredient's graph.
    for mid, f in facts.items():
        relevant = [n for n in nodes if mid in n['material_ids']]
        if f.get('quantity_only') and any(n['operation']=='切裁' for n in relevant):
            raise ValueError(f'数量規格から後続切断を創作 {mid}')
        for lock in f['equipment_locks']:
            if mid not in blocked and not any(n['equipment'] == lock['equipment'] for n in relevant):
                raise ValueError(f'材料の明示設備が欠落 {mid}/{lock["equipment"]}')
        for action in f['required_actions']:
            if mid in blocked:
                continue
            choices = decisions_by_id[mid]['preparation']
            methods = {p['method'] for n in relevant for p in n['phases']
                       if isinstance(p, dict) and mid in p['material_ids']}
            if action in {'roast', 'stock'} and action not in methods:
                raise ValueError(f'明示加工の欠落 {mid}/{action}')
            if action == 'boil' and not ('blanch' in choices or 'boil' in methods):
                raise ValueError(f'明示茹で加工の欠落 {mid}')
            if action == 'soak' and 'soak' not in choices:
                raise ValueError(f'明示戻し加工の欠落 {mid}')
    validate_constraints(source_constraints, nodes, blocked)
    return dict(schema_version=1, source_hash=signature(menu), menu=deepcopy(menu),
                decisions=d, operator_choices=deepcopy(overrides), effective_materials=effective,
                source_constraints=source_constraints, nodes=nodes, edges=edges,
                material_nodes=paths, issues=sorted(set(issues)),
                status='draft_needs_review', business_correctness='not_evaluated')
