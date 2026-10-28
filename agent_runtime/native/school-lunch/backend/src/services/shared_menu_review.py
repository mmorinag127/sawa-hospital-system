"""Source-backed catalog review. Questions are not silently converted into facts."""
from copy import deepcopy
import json
from pathlib import Path
import unicodedata
import re

from fastapi import HTTPException

from src.shared_menu_catalog_keys import ANSWER_CHOICES, review_counts
DATA_FILE = Path(__file__).parents[1] / 'data' / 'july_2026_pattern_review.json'
FLOW_OVERRIDES = json.loads(DATA_FILE.with_name('july_2026_pattern_review_overrides.json').read_text())
ASSEMBLY = json.loads(DATA_FILE.with_name('july_2026_pattern_assembly.json').read_text())


def school_direct_document(session, document):
    """Explicit, pinned school-direct source plus reviewed daily exclusion.

    An empty/deleted kitchen graph alone is never an exemption.
    """
    source=document.get('shared_menu_source') or {}
    if (not source.get('revision_id') or document.get('steps') or not document.get('ingredients')
            or any((f.get('drawing') or {}).get('mode')!='annotation'
                   or f['drawing'].get('status')!='user-confirmed' for f in document['ingredients'])):
        return False
    from src.models.shared_menu import SharedMenuRevision
    revision=session.get(SharedMenuRevision,source['revision_id'])
    return bool(revision and (revision.provenance.get('pattern_review') or {}).get('handling')=='school_direct'
                and not revision.document['steps']
                and all((f.get('drawing') or {}).get('mode')=='annotation' for f in revision.document['ingredients']))


def coalesce_identical_preparation(document):
    """One same-place batch card for exactly equal source-pattern chains.

    Never merge similar names or different processing chains. Separate batches
    explicitly means these foods are NOT mixed together during preparation.
    """
    from src.schemas.menu_process import material_flow
    doc=deepcopy(document)
    foods={f['id']:f for f in doc['ingredients']}
    branches={}
    for step in doc['steps']:
        match=re.fullmatch(r'(f\d+)-p(\d+)-s\d+',step['id'])
        if match:
            branches.setdefault((match[1],match[2]),[]).append(step)
    signatures={}
    for (mid,pattern),chain in branches.items():
        if foods[mid]['drawing']['mode']=='annotation':
            continue
        signature=(pattern,tuple((s['operation'],s['equipment']['type'],s['equipment'].get('id'),
                                  s['conditions'].get('description','')) for s in chain))
        signatures.setdefault(signature,[]).append((mid,chain))
    replacements={};removed=set();groups=[]
    for members in signatures.values():
        if len(members)<2:
            continue
        first,chain=members[0]
        for mid,other in members[1:]:
            for kept,dropped in zip(chain,other):
                replacements[dropped['id']]=kept['id'];removed.add(dropped['id'])
            chain[0]['inputs'].extend(deepcopy(other[0]['inputs']))
        for step in chain:
            step['conditions']['processing_mode']='separate_batches'
        groups.append(([mid for mid,_ in members],[s['id'] for s in chain]))
    doc['steps']=[s for s in doc['steps'] if s['id'] not in removed]
    for step in doc['steps']:
        inputs=[];seen=set()
        for ref in step['inputs']:
            if ref.get('step_id') in replacements:
                ref['step_id']=replacements[ref['step_id']]
            key=(ref.get('ingredient_id'),ref.get('step_id'),ref.get('output_id'))
            if key not in seen:
                inputs.append(ref);seen.add(key)
        step['inputs']=inputs
    flow,_=material_flow(doc)
    for mids,shared in groups:
        assert all(set(mids)<=set(flow[sid]) for sid in shared)
        group='prep-'+mids[0]
        for mid in mids:
            foods[mid]['drawing'].update(mode='shared',group_id=group,shared_step_ids=shared,
                reason='同じ献立・同じ原パターンで工程と設備種類が完全一致する別バッチ処理の確認案')
    return doc


def review_summary(provenance):
    return review_counts(provenance)


def validate_review(provenance, confirmed=False):
    review = provenance.get('pattern_review')
    if review is None:
        return
    if not isinstance(review, dict) or not isinstance(review.get('questions'), list):
        raise HTTPException(422, '確認事項の形式が不正です')
    seen = set()
    for question in review['questions']:
        if (not isinstance(question, dict) or not isinstance(question.get('id'), str)
                or question['id'] in seen or not isinstance(question.get('text'), str)
                or question.get('answer', '') not in ('', *ANSWER_CHOICES)):
            raise HTTPException(422, '確認事項のID・回答が不正です')
        seen.add(question['id'])
    if confirmed and review_summary(provenance)['unanswered_count']:
        raise HTTPException(422, '確認待ちの質問があります。回答するか、下書きとして保存してください')


def preserve_questions(previous, current):
    """Revision edits can answer a source question but cannot delete/change it."""
    old = previous.get('pattern_review')
    if not old:
        return
    new = current.get('pattern_review') or {}
    before = [(q['id'], q['text']) for q in old['questions']]
    after = [(q.get('id'), q.get('text')) for q in new.get('questions', [])]
    if old.get('source_key') != new.get('source_key') or before != after:
        raise HTTPException(422, '原資料の確認事項は削除できません。回答を変更してください')


def operation_equipment(operation, note=''):
    # Equipment KIND only. No individual kettle/sink or actual is invented.
    if any(word in operation for word in ('切る', '切り', '切断', 'カット')) and '手切り' not in operation and 'スライサー' in note:
        return 'スライサー'
    for word, kind in [('ミキサー', 'ミキサー'), ('スライサー', 'スライサー'),
                       ('スチコン', 'スチコン'), ('真空冷却', '冷却器'),
                       ('脱水', '脱水機'), ('D-2', '冷蔵庫'), ('炊飯', '炊飯器'),
                       ('揚げる', 'フライヤー'), ('焼く', 'ゼットオーブン'), ('蒸', 'スチコン'),
                       ('茹で', '回転釜'), ('ボイル', '回転釜'), ('炒', '回転釜'),
                       ('煮', '回転釜'), ('洗浄', 'シンク'), ('冷却', '冷却器')]:
        if word in operation:
            return kind
    return '作業台'


def seed_payload(menu):
    """Compile editable PROPOSALS, not confirmed historical procedures.

    Each ingredient keeps its own preparation chain: listing several foods in a
    text pattern is not evidence that they are physically mixed. Final collection
    is explicitly pending, with the source join note attached for operator review.
    """
    ingredients, steps, associations = [], [], []
    seasoning = set(menu['seasonings'])
    frozen = ('ブロッコリー', 'ほうれん', 'こまつな', '小松菜', '枝豆', 'えだまめ',
              'グリンピース', 'いんげん', 'しめじ', 'えのき', '錦糸卵', '液卵', '油揚げ')
    canned = ('うずら卵', 'ヤングコーン', 'れんこん', '大豆(水煮')
    for i, food in enumerate(menu['ingredients'], 1):
        name = food['name']
        normalized = unicodedata.normalize('NFKC', name)
        state = ('冷凍' if any(word in normalized for word in frozen) or '冷凍' in normalized else
                 '水煮' if any(word in normalized for word in canned) or '水煮' in normalized else
                 'カット済み' if normalized in ('白菜', 'はくさい') else '')
        # Commodity-specific email rules do not turn raw cut cob corn or shell eggs into frozen products.
        if '便利とうふ' in normalized or '冷凍' in normalized:
            state = '冷凍'
        ingredients.append({'id': f'f{i}', 'name': name, 'state': state,
            'drawing': {'role': 'seasoning' if i in seasoning else 'main',
                'mode': 'annotation' if i in seasoning or menu['id'] == 2 else 'independent',
                'reason': '回答メールの調味料省略方針' if i in seasoning else '7月パターン確認案。まだ未承認',
                'status': 'pending'}})
    ends = {}
    if menu['id'] != 2:
        for index, pattern in enumerate(menu['patterns'], 1):
            associations.append({'pattern_key': f'M{menu["id"]:02d}-P{index:02d}',
                'food_ids': [f'f{v}' for v in pattern['food_ids']],
                'proposal': pattern['flow'], 'equipment_note': pattern['place']})
            for food_id in pattern['food_ids']:
                previous = None
                flow = FLOW_OVERRIDES.get(str(menu['id']), {}).get(str(food_id), pattern['flow'])
                operations = [op for op in flow.split(' → ') if not op.endswith(
                    ('へ', '後入れ', '最後に投入', '最後に加える')) and op != '搬出']
                common_cook = any(food_id in stage['foods'] and stage['equipment']=='回転釜'
                                  for stage in ASSEMBLY.get(str(menu['id']), []))
                # The common cooking card owns this work; don't cook each food
                # once on its branch and then cook the same recipe a second time.
                while len(operations)>1 and common_cook and re.search(r'(炒める|炒め煮|煮る|加熱)$', operations[-1]):
                    operations.pop()
                for n, operation in enumerate(operations, 1):
                    # Textual routing destinations are notes, not extra cooking sites.
                    if operation.endswith(('へ', '後入れ', '最後に投入', '最後に加える')) or operation == '搬出':
                        continue
                    step_id = f'f{food_id}-p{index}-s{n}'
                    steps.append({'id': step_id, 'operation': operation,
                        'inputs': [{'step_id': previous, 'edge_id': f'e-{step_id}'}] if previous else [{'ingredient_id': f'f{food_id}'}],
                        'equipment': {'type': operation_equipment(operation, pattern['place']), 'id': None},
                        'conditions': {'description': pattern['place'], 'proposal_pattern': f'M{menu["id"]:02d}-P{index:02d}'},
                        'basis': {'review': 'pending', 'source': pattern['basis'], 'equipment': 'unconfirmed-proposal'},
                        'outputs': []})
                    previous = step_id
                if previous:
                    ends[food_id] = {'step_id': previous, 'edge_id': f'e-end-{food_id}'}
        previous_join = None
        for number, assembly in enumerate(ASSEMBLY.get(str(menu['id']), []), 1):
            inputs = [ends.pop(food_id) for food_id in assembly['foods']]
            if assembly.get('after') and previous_join:
                previous_step = next(step for step in steps if step['id']==previous_join)
                if previous_step['equipment']['type']==assembly['equipment']:
                    previous_step['inputs'].extend(inputs)
                    previous_step['operation'] += ' ／ ' + assembly['operation']
                    continue
                inputs.append(ends.pop(previous_join))
            join_id = f'cook-join-{number}'
            steps.append({'id':join_id,'operation':assembly['operation'], 'inputs':inputs,
                'equipment':{'type':assembly['equipment'],'id':None},
                'conditions':{'description':menu['join']},
                'basis':{'review':'pending','source':'手配表の調理方法・メールからの合流案。ユーザー確認前'},'outputs':[]})
            ends[join_id]={'step_id':join_id,'edge_id':f'e-{join_id}'}
            previous_join=join_id
        terminal = next((s for s in steps if len(ends)==1 and s['id']==next(iter(ends.values()))['step_id']), None)
        if terminal and re.search(r'仕分け|配送|一時保管', terminal['operation']):
            # The supplied email explicitly describes finished products held
            # cold or sorted for delivery. This is not raw-ingredient storage.
            terminal['conditions']['component_name'] = '提供用完成品の仕分け・一時保管'
        finished = terminal and (re.search(r'盛[り]?付|提供|配缶|搬出', terminal['operation'])
                                 or terminal['conditions'].get('component_name'))
        if ends and (len(ends) > 1 or seasoning or not finished):
            steps.append({'id': 'collection', 'operation': '盛付・仕分け（合流方法は要確認）',
                'inputs': list(ends.values()) + [{'ingredient_id': f'f{i}'} for i in sorted(seasoning)],
                'equipment': {'type': '作業台', 'id': None},
                'conditions': {'description': menu['join']},
                'basis': {'review': 'pending', 'source': 'パターン確認案。工程接続も確認対象'}, 'outputs': []})
    review = {'source_key': f'july-2026-M{menu["id"]:02d}', 'handling': 'school_direct' if menu['id'] == 2 else 'kitchen',
              'questions': [{'id': f'M{menu["id"]:02d}-Q{i}', 'text': q, 'answer': ''}
                            for i, q in enumerate(menu['questions'], 1)]}
    if menu['id'] != 2:
        review['questions'].append({'id': f'M{menu["id"]:02d}-graph',
            'text': '工程カードの設備種類・食材の合流・盛付までの接続は、この献立の標準として正しいですか。違う場合は詳細グラフで変更してください。', 'answer': ''})
    return {'name': menu['name'], 'expected_version': 0, 'status': 'draft', 'source_month': '2026-07',
        'provenance': {'origin': 'provided-july-arrangement-and-email',
            'source_menu_id': menu['id'], 'occurrences': deepcopy(menu['occurrences']),
            'instructions': [menu['instructions']] if menu['instructions'] else [],
            'known': menu['known'], 'pattern_review': review},
        'pattern_associations': associations,
        'document': coalesce_identical_preparation({'schema_version': 2, 'ingredients': ingredients, 'steps': steps,
                     'route_config': None}), 'change_note': '提供7月全件の未確認案。実績・確認済み値は含みません'}


def import_july(session):
    from sqlalchemy import or_, select, text
    from src.models.shared_menu import SharedMenu, SharedMenuRevision
    from src.schemas.shared_menus import SharedMenuWrite
    from src.services import shared_menu_service
    connection = session.connection()
    if connection.dialect.name == 'postgresql':
        session.execute(text('SELECT pg_advisory_xact_lock(53072026)'))
    elif connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql('BEGIN IMMEDIATE')
    source = json.loads(DATA_FILE.read_text())
    bodies = [seed_payload(menu) for menu in source['menus']]
    incoming_names = {body['name'] for body in bodies}
    incoming_keys = {body['provenance']['pattern_review']['source_key'] for body in bodies}
    existing = session.execute(select(SharedMenu, SharedMenuRevision).join(
        SharedMenuRevision, SharedMenu.head_revision_id == SharedMenuRevision.id).where(or_(
            SharedMenu.name.in_(incoming_names),
            SharedMenuRevision.provenance['pattern_review']['source_key'].as_string().in_(incoming_keys),
        ))).all()
    names = {m.name for m, _ in existing}
    keys = {(r.provenance.get('pattern_review') or {}).get('source_key') for _, r in existing}
    by_name = {}
    for m, r in existing:
        by_name.setdefault(m.name, []).append((m, r))
    created, skipped, annotated = [], [], []
    for body in bodies:
        key = body['provenance']['pattern_review']['source_key']
        if body['name'] in names or key in keys:
            candidates = by_name.get(body['name'], [])
            match = candidates[0] if len(candidates) == 1 else None
            if (match and key not in keys and match[1].status == 'draft'
                    and [m['name'] for m in match[1].document['ingredients']] == [m['name'] for m in body['document']['ingredients']]):
                catalog, revision = match
                retained = {**body, 'expected_version': catalog.version,
                    'document': deepcopy(revision.document),
                    'pattern_associations': deepcopy(revision.pattern_associations),
                    'provenance': {**deepcopy(revision.provenance), 'pattern_review': body['provenance']['pattern_review'],
                                   'july_review_source': body['provenance']},
                    'change_note': '既存の工程・食材・設備を保持し、7月確認事項のみ追加'}
                shared_menu_service.revise(session, catalog.id, SharedMenuWrite.model_validate(retained))
                annotated.append({'id': catalog.id, 'name': catalog.name})
                keys.add(key)
                continue
            skipped.append(body['name'])
            continue
        saved = shared_menu_service.create(session, SharedMenuWrite.model_validate(body))
        created.append({'id': saved['id'], 'name': saved['name']})
        names.add(body['name']); keys.add(key)
    return {'created': created, 'annotated': annotated, 'skipped': skipped, 'source_count': len(source['menus'])}
