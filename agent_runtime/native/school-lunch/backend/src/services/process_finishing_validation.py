"""Reject relocation of an explicitly continuous finishing instruction.

This is deliberately bounded to source 'add ... and finish' clauses and
new main-cooking -> finishing nodes. It neither chooses a physical device nor
rewrites operator work or infers every recipe's missing location.
"""
import re
import unicodedata


def validate_continuous_finishing(plan, document, inferred_ids):
    source = re.sub(r'\s+', '', unicodedata.normalize('NFKC', plan.get('source_instructions','')))
    clauses = re.split(r'[。\n]', source)
    transfer = r'盛[り]?付|盛る|配缶|器[にはへ]|皿[にはへ]|台[にはへ]|移[しす]'
    continuations = [clause for index, clause in enumerate(clauses)
                     if re.search(r'(?:入れ|加え)(?:て)?仕上げ', clause)
                     and not re.search(transfer, clause)
                     and not (index and re.search(transfer, clauses[index-1]))
                     and not re.search(r'仕上げ(?:ない|ず)|場合|なら|必要に応じ', clause)]
    if not continuations:
        return []
    steps = {s['id']:s for s in document['steps']}
    issues = []
    for step in document['steps']:
        description = step.get('conditions', {}).get('description', '')
        active_finish = re.search(r'(?:入れ|加え)(?:て)?仕上げ(?!ない|ず|た|済)', description)
        if step['id'] not in inferred_ids or not ('仕上げ' in step['operation'] or active_finish):
            continue
        if step['equipment']['type'] not in {'作業台','回転釜'}:
            continue
        for ref in step['inputs']:
            previous = steps.get(ref.get('step_id'))
            if (not previous or previous['id'] not in inferred_ids or ref.get('output_id')
                    or previous['equipment']['type'] != '回転釜'
                    or not re.search(r'炒|煮|調理', previous['operation'])):
                continue
            if step['equipment']['type'] == previous['equipment']['type']:
                issues.append(previous['id']+' → '+step['id']
                              +'：本調理と仕上げが同じ釜の継続作業か未確定。'
                              '設備種類だけで同一/別設備と決めず、同じ場所なら1カードにまとめてください。')
                continue
            raise ValueError('手配表の「入れて/加えて仕上げる」という連続作業を、'
                             '本調理から別の仕上げ・盛付カードへ移しています：'
                             + previous['id']+' → '+step['id']
                             + '。同じ場所での投入順は本調理カードの説明に保持し、'
                             '仕上げ材料をそのカードへ接続してください。盛付だけを次のカードにします。'
                             '別の場所へ移す原文がある場合はその根拠を確認し、無断で移動を追加しないでください。')
    return issues
