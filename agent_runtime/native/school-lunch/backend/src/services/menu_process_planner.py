"""Compile immutable cooking facts first; Gemini may return only a bounded patch.

No date, drawing, physical equipment ID, or layout is used here. Source clauses
we cannot resolve are left in the inference context, never silently guessed.
"""
from copy import deepcopy
import hashlib
import json
import re
import unicodedata

from pydantic import Field
from src.schemas.menu_process import Strict, Step, ProcessDocument, material_flow
from src.services.process_preparation_validation import MaterialPreparation, validate_preparation
from src.services.process_source_conflicts import SourceConflict, validate_source_conflicts
from src.services.process_finishing_validation import validate_continuous_finishing


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def normalize(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).replace('★', '')


def material_aliases(name):
    text = normalize(name)
    base = re.split(r'[（(]', text)[0]
    aliases = {text, base}
    # Lexical synonyms only. These do not prescribe any cooking procedure.
    if base == 'こまつな': aliases.add('小松菜')
    if base == '玉葱': aliases.add('たまねぎ')
    if base == 'とうもろこし': aliases.add('コーン')
    return {x for x in aliases if len(x) >= 2}


def source_operations(text):
    """Recognize explicit operations, retaining text and order; no guessed cut."""
    patterns = [
        (r'いちょう切り', 'いちょう切り'), (r'短冊(?:切り|切)?', '短冊切り'),
        (r'みじん切り', 'みじん切り'), (r'千切り?', '千切り'),
        (r'薄切り', '薄切り'), (r'輪切り', '輪切り'), (r'ざく切り', 'ざく切り'),
        (r'角切り', '角切り'),
        (r'くし形', 'くし形切り'), (r'斜め小口', '斜め小口切り'),
        (r'一口大', '一口大に切る'), (r'すりおろ[すし]|おろす', 'すりおろす'),
        (r'下[茹ゆ]で', '下茹で'), (r'油抜き', '油抜き'),
        (r'塩[茹ゆ](?:で)?', '塩茹で'), (r'カット', '切断'),
        (r'戻して|もどして', '水戻し'),
    ]
    found = []
    for pattern, operation in patterns:
        for match in re.finditer(pattern, text):
            clause_start = max(text.rfind('。', 0, match.start()), text.rfind('、', 0, match.start())) + 1
            prefix = text[clause_start:match.start()]
            suffix = text[match.end():match.end() + 12]
            # Negated/conditional prose is not an unconditional cooking fact.
            # Leave it to the unresolved source context rather than fixing it.
            if re.search(r'場合|なら|必要に応じ|好みで', prefix) or re.match(r'(?:には|は|に)?(?:しない|せず|不要|禁止|せん|しなく)', suffix):
                continue
            found.append((match.start(), operation))
    return [op for _, op in sorted(found)]


def compile_plan(name, instructions, material_instructions, current, constraints=None):
    document = deepcopy(current)
    constraints = deepcopy(constraints or {})
    used = {i.get('ingredient_id') for s in document['steps'] for i in s['inputs']}
    rows = {x['id']: x.get('instruction', '') for x in material_instructions}
    slots, required_origins, rule_sources = [], {}, []
    new_fixed_ids = []
    deleted_ids = {s['id'] for s in constraints.get('deleted_steps', [])}
    aliases = {m['id']: material_aliases(m['name']) for m in document['ingredients']}
    body = normalize(instructions)

    def fixed_step(sid, operation, inputs, equipment, source):
        if sid in deleted_ids:
            return None
        item = {'id': sid, 'operation': operation, 'inputs': inputs,
                'equipment': {'type': equipment, 'id': None},
                'conditions': {}, 'basis': {'operation': source}, 'outputs': []}
        if inputs: item['basis']['inputs'] = source
        if equipment: item['basis']['equipment'] = source
        document['steps'].append(item)
        new_fixed_ids.append(sid)
        rule_sources.append({'step_id': sid, 'source': source})
        return item

    grains = [m for m in document['ingredients'] if normalize(m['name']) in
              {'精白米', '水稲穀粒(精白米)', '強化米', '押麦'} and m['id'] not in used]
    # Rice is a fixed cooking procedure; fillings of mixed rice remain separate.
    # Never classify a dish as rice merely because its title contains 米.
    if grains and len(grains) == len(document['ingredients']) and not body and not document['steps']:
        sid = 'fixed-rice'
        cook = fixed_step(sid, '炊飯', [{'ingredient_id': m['id']} for m in grains],
                          '炊飯器', 'rule:rice-cooking-v1（既知の米飯工程）')
        if cook:
            fixed_step('fixed-rice-serve', '盛付', [{'step_id': sid}], '作業台',
                       'rule:rice-serving-v1（既知の米飯工程）')
            used.update(m['id'] for m in grains)
    elif grains and re.search(r'炊き[あ上]げ|炊飯', body) and not document['steps']:
        # The rice operation/device is still known for explicitly mixed rice.
        # Gemini may attach non-rice ingredients, never replace the grain input.
        cook = fixed_step('fixed-rice', '炊飯', [{'ingredient_id': m['id']} for m in grains],
                          '炊飯器', 'rule:rice-cooking-v1（手配表の炊飯指定）')
        if cook:
            slots.append({'step_id': cook['id'], 'field': 'additional_inputs',
                          'instruction': '手配表の炊き込み材料だけ追加。既存の米入力は保持されるため回答しない。'})
            used.update(m['id'] for m in grains)

    for material in document['ingredients']:
        mid = material['id']
        if mid in used:
            continue
        direct = normalize(rows.get(mid, ''))
        # Only assign a menu-level paragraph if it explicitly names this material
        # and no other registered material. Multi-material / numbered prose stays
        # in the read-only source context for unresolved topology inference.
        scoped = ''
        named = [key for key, words in aliases.items() if any(word in body for word in words)]
        if named == [mid] and any(re.search(re.escape(word) + r'[はを]', body) for word in aliases[mid]):
            scoped = body
        text = direct or scoped
        operations = source_operations(text)
        # Current kitchen's user-confirmed preparation rule. Processed products
        # and explicit incompatible cuts must not be re-ground automatically.
        garlic = re.match(r'^(にんにく|ニンニク|大蒜)(?:\(|$)', normalize(material['name']))
        processed = re.search(r'粉|パウダー|チューブ|ペースト|加工済|おろし済|みじん切り済',
                              normalize(material['name'] + material.get('state', '')))
        garlic_mixer = bool(garlic and not processed and not re.search(r'スライス', text) and
                            (not operations or operations == ['みじん切り'] or operations == ['すりおろす']))
        if garlic_mixer and not operations:
            operations = ['粉砕']
        previous = None
        for index, operation in enumerate(operations):
            sid = f'fixed-{mid}-{index}'
            # Oil removal names the purpose, not the method or equipment.
            equipment = '回転釜' if operation in {'下茹で', '塩茹で'} else 'シンク' if operation == '水戻し' else None
            if garlic_mixer:
                equipment = 'ミキサー'
            inputs = [{'step_id': previous}] if previous else []
            source = f'source:{mid}:{text}' if text else 'rule:garlic-mixer-v1（利用者確認済みの施設工程）'
            step = fixed_step(sid, operation, inputs, equipment, source)
            if step is None:
                continue
            if garlic_mixer:
                step['basis']['equipment'] = 'rule:garlic-mixer-v1（利用者確認済みの施設工程）'
            if operation == '油抜き':
                step['conditions']['heat_treatment'] = 'unresolved'
                slots.append({'step_id': sid, 'field': 'heat_treatment',
                              'instruction': '油抜きの方法から加熱有無を判断。value={kind:heatedまたはnot_heated,reason:方法と根拠}。熱湯/茹でる方法ならheated。拭き取りだけならnot_heated。方法を判断できなければ埋めずissuesに記載。'})
            if not previous:
                slots.append({'step_id': sid, 'field': 'inputs', 'material_ids': [mid],
                              'instruction': 'この材料だけの前処理を接続。必要なら洗浄等をadded_stepsに追加。別材料を混ぜない。'})
                required_origins[sid] = {'exact': [mid]}
            previous = sid

    # Gaps are explicit, not a request to rewrite existing operations or inputs.
    for step in document['steps']:
        if not step['equipment']['type'] and step['basis'].get('equipment') != 'user':
            slots.append({'step_id': step['id'], 'field': 'equipment',
                          'instruction': '使用設備の種類のみ。実機IDはnull。不明なら未解決。'})
        if not step['inputs'] and not any(s['step_id'] == step['id'] and s['field'] == 'inputs' for s in slots) and step['basis'].get('inputs') != 'user':
            slots.append({'step_id': step['id'], 'field': 'inputs', 'instruction': 'この工程の材料/前工程を接続。'})

    consumed_materials = {i.get('ingredient_id') for s in document['steps'] for i in s['inputs']}
    consumed_steps = {(i.get('step_id'), i.get('output_id')) for s in document['steps'] for i in s['inputs']}
    open_outputs = []
    for s in document['steps']:
        if re.search(r'盛[り]?付|提供|配缶', s['operation']):
            continue
        for output in s['outputs'] or [None]:
            oid = output['id'] if output else None
            if (s['id'], oid) not in consumed_steps:
                open_outputs.append({'step_id': s['id'], 'output_id': oid})
    remaining = [m['id'] for m in document['ingredients'] if m['id'] not in consumed_materials]
    plan = {'version': 1, 'fixed_document': document, 'slots': slots,
            'source_instructions': instructions,
            'material_instructions': material_instructions,
            'unconsumed_material_ids': remaining, 'open_outputs': open_outputs,
            'allow_added_steps': bool(remaining or open_outputs),
            'required_origins': required_origins, 'rule_sources': rule_sources,
            'original_order': [s['id'] for s in current['steps']],
            'new_fixed_ids': new_fixed_ids, 'user_constraints': constraints}
    plan['hash'] = digest(plan)
    return plan


class Fill(Strict):
    step_id: str
    field: str
    value: list[dict] | dict


class HeatTreatmentFill(Strict):
    kind: str = Field(pattern='^(heated|not_heated)$')
    reason: str = Field(min_length=1, max_length=1000)


class ProcessPatch(Strict):
    plan_hash: str
    added_steps: list[Step] = Field(default_factory=list, max_length=300)
    fills: list[Fill] = Field(default_factory=list, max_length=600)
    issues: list[str] = Field(default_factory=list, max_length=50)
    # None is legacy recorded-patch replay only. New Gemini calls require this
    # field in response_schema and complete_plan; fixed non-LLM rules need none.
    preparation_checks: list[MaterialPreparation] | None = None
    source_conflicts: list[SourceConflict] | None = None


def collapse_shared_batch_connections(document, mutable_ids):
    """Canonicalize parallel full-batch links, never cooking operations.

    Only new inferred nodes are eligible. A complete, disjoint ingredient
    partition going to one new consumer is equivalent to one full-batch link.
    Different destinations, measured portions and operator nodes stay intact.
    """
    before, _ = material_flow(document)
    nodes = {s['id']: s for s in document['steps']}
    for source in document['steps']:
        outputs = source['outputs']
        if (source['id'] not in mutable_ids or len(outputs) < 2
                or source['conditions'].get('processing_mode') != 'separate_batches'
                or any(not o.get('ingredient_ids') or o.get('quantity') != '全量' for o in outputs)):
            continue
        usages = [(target, ref) for target in document['steps'] for ref in target['inputs']
                  if ref.get('step_id') == source['id']]
        destinations = {s['id'] for s, _ in usages}
        if (len(destinations) != 1 or not destinations <= mutable_ids
                or len(usages) != len(outputs)
                or {r.get('output_id') for _, r in usages} != {o['id'] for o in outputs}):
            continue
        target = nodes[next(iter(destinations))]
        labels = [r.get('label') or next(o['label'] for o in outputs if o['id'] == r['output_id'])
                  for _, r in usages]
        if len('・'.join(labels)) > 500:
            continue
        first = next(i for i,r in enumerate(target['inputs']) if r.get('step_id') == source['id'])
        target['inputs'] = [r for r in target['inputs'] if r.get('step_id') != source['id']]
        target['inputs'].insert(first, {'step_id':source['id'], 'label':'・'.join(labels)})
        source['outputs'] = []
        source['basis']['outputs'] = 'inferred:shared-batch-canonicalization'
    after, _ = material_flow(document)
    if before != after:
        raise ValueError('共通接続の正規化で対象食材が変わりました')
    return ProcessDocument.model_validate(document).model_dump()


def apply_patch(plan, raw):
    patch = ProcessPatch.model_validate(raw)
    if patch.plan_hash != plan['hash']:
        raise ValueError('推論の基準工程が一致しません')
    doc = deepcopy(plan['fixed_document'])
    known = {s['id']: s for s in doc['steps']}
    allowed = {(x['step_id'], x['field']) for x in plan['slots']}
    filled = set()
    for change in patch.fills:
        key = (change.step_id, change.field)
        if key not in allowed or key in filled:
            raise ValueError('固定項目・指定外項目の変更を拒否しました')
        filled.add(key)
        if change.field == 'equipment' and not isinstance(change.value, dict):
            raise ValueError('設備の差分はオブジェクトで指定してください')
        if change.field in {'inputs', 'additional_inputs'} and not isinstance(change.value, list):
            raise ValueError('入力の差分は配列で指定してください')
        if change.field == 'equipment' and change.value.get('id') is not None:
            raise ValueError('実機IDは推論対象外です')
        if change.field == 'heat_treatment':
            treatment = HeatTreatmentFill.model_validate(change.value)
            known[change.step_id]['conditions']['heat_treatment'] = treatment.kind
            known[change.step_id]['conditions']['heat_treatment_reason'] = treatment.reason
            known[change.step_id]['basis']['heat_treatment'] = 'inferred'
        elif change.field == 'additional_inputs':
            known[change.step_id]['inputs'].extend(change.value)
            known[change.step_id]['basis']['additional_inputs'] = 'inferred'
        else:
            known[change.step_id][change.field] = change.value
            known[change.step_id]['basis'][change.field] = 'inferred'
    if patch.added_steps and not plan['allow_added_steps']:
        raise ValueError('既知工程に追加の推論は不要です')
    for step in patch.added_steps:
        if not step.id.startswith('new-') or step.id in known:
            raise ValueError('追加工程のIDが既知工程と衝突しています')
        if step.equipment.id is not None:
            raise ValueError('実機IDは推論対象外です')
        if step.basis.get('review') == 'user-confirmed':
            raise ValueError('Geminiはユーザー確認済みの根拠を設定できません')
        if not step.inputs:
            raise ValueError('推論の追加工程には登録材料または前工程の入力が必要です。未登録材料の供給・調達工程を作らずissuesに記載してください')
        if step.equipment.type:
            if '脱水' in step.operation and step.equipment.type != '脱水機':
                raise ValueError('脱水は脱水機への移動を伴います。冷却と脱水など設備が異なる作業を1カードにまとめないでください')
            if '洗浄' in step.operation and step.equipment.type != 'シンク':
                raise ValueError('洗浄はシンクの作業です。洗浄と切裁機での加工を1カードにまとめないでください')
            if step.equipment.type == '冷却器' and re.search(r'カット|切断|切る', step.operation):
                raise ValueError('冷却器で切断は行えません。冷却と切断は使用設備ごとに分けてください')
        item = step.model_dump()
        item['basis'] = {key: 'inferred' for key in ('operation', 'inputs', 'equipment', 'conditions', 'outputs')}
        doc['steps'].append(item)
        known[step.id] = item

    # Deterministic topological sort. Only inserted nodes move; user order stays.
    pending = [Step.model_validate(s).model_dump() for s in doc['steps']]
    ordered, seen = [], set()
    order = plan['original_order']
    while pending:
        for index, step in enumerate(pending):
            dependencies = {i['step_id'] for i in step['inputs'] if i.get('step_id')}
            if step['id'] in order:
                dependencies.update(order[:order.index(step['id'])])
            if dependencies <= seen:
                ordered.append(pending.pop(index)); seen.add(step['id']); break
        else:
            raise ValueError('循環または存在しない入力参照があります')
    doc['steps'] = ordered
    inferred_ids = {s.id for s in patch.added_steps}
    finishing_error = None
    finishing_issues = []
    try:
        finishing_issues = validate_continuous_finishing(plan, doc, inferred_ids)
    except ValueError as error:
        finishing_error = error
    # Report source-target violations alongside duplicate-use errors. Showing
    # only the latter encouraged an invented split of recipe water into wash
    # water before the next attempt could reach this source invariant.
    origin_errors = []
    try:
        proposal_origins, _ = material_flow(doc)
        for sid, requirement in plan['required_origins'].items():
            actual = set(proposal_origins[sid])
            expected = set(requirement['exact'])
            if actual != expected:
                origin_errors.append(
                    f"手配表で指定された作業の対象材料が変わっています: {sid} "
                    f"指定={sorted(expected)} 回答={sorted(actual)}。指定外の材料をこの作業へ追加しないでください。")
    except (KeyError, ValueError):
        # Structural faults receive the normal schema error below; never
        # accept a graph just because preliminary lineage could not be read.
        pass
    try:
        validated = ProcessDocument.model_validate(doc).model_dump()
    except ValueError as error:
        details = ([str(finishing_error)] if finishing_error is not None else []) + origin_errors
        if details:
            raise ValueError('\n'.join([*details, str(error)])) from error
        raise
    if finishing_error is not None:
        raise finishing_error
    if origin_errors:
        raise ValueError('\n'.join(origin_errors))
    validated = collapse_shared_batch_connections(validated, {s.id for s in patch.added_steps})
    origins, _ = material_flow(validated)
    material_names = {m['id']: normalize(m['name']) for m in validated['ingredients']}
    for step in validated['steps']:
        if (step['id'] in inferred_ids and len(step['outputs']) > 1 and origins[step['id']]
                and all(material_names[mid] in {'水', '調理用水'} for mid in origins[step['id']])):
            explicit_text = normalize(plan.get('source_instructions', '')) + '。' + '。'.join(
                normalize(row.get('instruction','')) for row in plan.get('material_instructions', [])
                if row['id'] in origins[step['id']])
            explicit_split = re.search(r'水(?:を|は)[^。]{0,40}(?:取り分け|分け)(?!ない|ず|なく)', explicit_text)
            if not explicit_split:
                raise ValueError('登録水を作業用水へ分ける根拠が原本にありません。架空の水の取り分け工程を追加せず、洗浄・戻し・下茹での作業用水は説明に残し、登録水は料理に加える工程で使ってください。不明点はissuesへ記載してください')
        requirement = plan['required_origins'].get(step['id'])
        if requirement and set(origins[step['id']]) != set(requirement['exact']):
            raise ValueError('手配表で指定された作業の対象材料が変わっています')
    # Missing slots remain explicit; they cannot be mistaken for completed facts.
    missing = [f"{sid}: {field}未確定" for sid, field in sorted(allowed - filled)]
    incomplete = [f"{s['id']}: {label}未確定" for s in validated['steps']
                  for missing_value, label in [(not s['inputs'], 'inputs'), (not s['equipment']['type'], 'equipment')]
                  if missing_value]
    used_materials = {i.get('ingredient_id') for s in validated['steps'] for i in s['inputs']}
    incomplete += [f"{m['name']}: 工程未接続" for m in validated['ingredients'] if m['id'] not in used_materials]
    # Report open branches at proposal review, not only during route rendering.
    # A draft may intentionally be incomplete; retain every output/operator edit.
    used_outputs = {(i.get('step_id'), i.get('output_id'))
                    for s in validated['steps'] for i in s['inputs']}
    incomplete += [f"{s['id']}: 取り分け出力未接続（{o['label']} / {o['id']}）"
                   for s in validated['steps'] for o in s['outputs']
                   if (s['id'], o['id']) not in used_outputs]
    if patch.preparation_checks is not None:
        incomplete += validate_preparation(plan,validated,patch.preparation_checks)
    if patch.source_conflicts is not None:
        incomplete += validate_source_conflicts(plan, validated, patch.source_conflicts, inferred_ids)
    issues = list(dict.fromkeys(patch.issues + missing + incomplete + finishing_issues))
    if issues and validated['steps']:
        # Put review notes only on inferred nodes, never inside an immutable node.
        target = next((s for s in reversed(validated['steps']) if s['id'] in inferred_ids), None)
        if target:
            target['conditions']['要確認'] = ' / '.join(issues)[:2000]
    from src.services.process_shared_preparation import coalesce_new_preparation, coalesce_new_wash_cut_chains
    validated = coalesce_new_preparation(validated, plan, inferred_ids)
    validated = coalesce_new_wash_cut_chains(validated, plan, inferred_ids, patch.preparation_checks)
    validated = collapse_shared_batch_connections(validated, inferred_ids | set(plan['new_fixed_ids']))
    return validated, issues
