"""Do not execute a model-declared ingredient substitution as a valid draft.

This checks declared conflicts, not completeness of language understanding.
The original source and operator work remain authoritative and untouched.
"""
import re
from pydantic import Field
from src.schemas.menu_process import Strict, material_flow


class SourceConflict(Strict):
    source_quote: str = Field(min_length=1, max_length=2000)
    affected_ingredient_ids: list[str]
    reason: str = Field(min_length=1, max_length=1000)


def validate_source_conflicts(plan, document, conflicts, inferred_ids):
    texts = [plan.get('source_instructions', ''), *[
        row.get('instruction', '') for row in plan.get('material_instructions', [])]]
    normalized = [re.sub(r'\s+', '', text) for text in texts]
    materials = {m['id']: m['name'] for m in document['ingredients']}
    flow, _ = material_flow(document)
    issues = []
    for conflict in conflicts:
        quote = re.sub(r'\s+', '', conflict.source_quote)
        if not quote or not any(quote in text for text in normalized):
            raise ValueError('食材不一致の根拠引用が手配表の原文にありません')
        affected = set(conflict.affected_ingredient_ids)
        if not affected <= materials.keys() or len(affected) != len(conflict.affected_ingredient_ids):
            raise ValueError('食材不一致の対象IDが未登録または重複しています')
        unresolved = affected
        for step in document['steps']:
            if step['id'] not in inferred_ids or not unresolved.intersection(flow[step['id']]):
                continue
            # An empty draft card is editable but not a guessed cooking action.
            if step['operation'] or step['equipment']['type'] or step['outputs']:
                raise ValueError('食材不一致を認識しながら別食材の調理工程を作っています：'
                                 + step['id'] + '。該当材料を代用せず未接続/未設定のまま残してください。'
                                 '注意文への記載だけでは工程の転用を許可しません。')
        names = '、'.join(materials[mid] for mid in conflict.affected_ingredient_ids)
        issues.append('手配表の食材不一致' + ('（'+names+'）' if names else '') + '：'+conflict.reason)
    return issues
