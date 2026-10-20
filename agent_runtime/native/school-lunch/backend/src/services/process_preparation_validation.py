"""Validate declared preparation against graph entries, not ingredient names.

The model classifies input state; the system checks that its own declared
requirement is implemented. Existing operator work is never rewritten here.
"""
from typing import Literal
from pydantic import Field
from src.schemas.menu_process import Strict


class MaterialPreparation(Strict):
    ingredient_id: str
    state: Literal['fresh_produce','raw_meat_fish','frozen','processed','seasoning','unknown']
    requirement: Literal['wash','thaw','soak','none','uncertain']
    reason: str = Field(min_length=1,max_length=500)


def requested_materials(plan):
    existing=set(plan.get('original_order',[]))
    used={ref['ingredient_id'] for step in plan['fixed_document']['steps']
          if step['id'] in existing for ref in step['inputs'] if ref.get('ingredient_id')}
    return [m['id'] for m in plan['fixed_document']['ingredients'] if m['id'] not in used]


def validate_preparation(plan, document, checks):
    required=requested_materials(plan)
    ids=[c.ingredient_id for c in checks]
    if len(set(ids))!=len(ids) or set(ids)!=set(required):
        raise ValueError('前処理点検の材料が欠落・重複・追加されています')
    names={m['id']:m['name'] for m in document['ingredients']}
    issues=[]
    for check in checks:
        if check.state=='fresh_produce' and check.requirement!='wash':
            raise ValueError('生鮮野菜と判断した材料には洗浄が必要です。加工済みの根拠がないまま不要にしないでください：'+check.ingredient_id)
        if check.state=='unknown' and check.requirement!='uncertain':
            raise ValueError('状態不明の材料を前処理不要と確定しないでください：'+check.ingredient_id)
        if check.requirement=='uncertain':
            issues.append(names[check.ingredient_id]+'：前処理要確認（'+check.reason+'）')
        starts=[s for s in document['steps'] if any(
            r.get('ingredient_id')==check.ingredient_id for r in s['inputs'])]
        if check.requirement=='none' and any(
                any(op in s['operation'] for op in ('洗浄','解凍'))
                and not any(n in s['operation'] for n in ('不要','しない','省略','なし'))
                for s in starts):
            raise ValueError(check.ingredient_id+'：前処理不要という判断と洗浄・解凍の入口工程が矛盾しています。根拠に従って判断と工程を整合させてください。')
        if check.requirement not in ('wash','thaw','soak'):continue
        operations={'wash':('洗浄',), 'thaw':('解凍',), 'soak':('水戻し','水で戻す','水に戻す')}
        expected=operations[check.requirement][0]
        def satisfies(step):
            return (any(op in step['operation'] for op in operations[check.requirement]) and not any(
                negative in step['operation'] for negative in ('不要','しない','省略','なし'))
                and (check.requirement not in ('wash','soak') or step['equipment']['type']=='シンク'))

        def soak_after_washing(step, visited):
            # Rinsing dry food before soaking does not mean soaking is absent.
            # Every material-bearing branch must reach it before other work.
            if satisfies(step): return True
            if step['id'] in visited or step['equipment']['type']!='シンク' or '洗浄' not in step['operation'] or any(
                    negative in step['operation'] for negative in ('不要','しない','省略','なし')):
                return False
            from src.schemas.menu_process import material_flow
            flow, outputs = material_flow(document)
            following = [target for target in document['steps'] if any(
                ref.get('step_id')==step['id'] and check.ingredient_id in (
                    outputs[(step['id'],ref['output_id'])] if ref.get('output_id') else flow[step['id']])
                for ref in target['inputs'])]
            return bool(following) and all(soak_after_washing(s,visited|{step['id']}) for s in following)

        if not starts or any(not (soak_after_washing(s,set()) if check.requirement=='soak' else satisfies(s))
                             for s in starts):
            raise ValueError(f'{check.ingredient_id}：{expected}が必要という判断と工程が矛盾しています。'
                             f'材料入口を{expected}へ接続し、その出力を既知加工のinputsスロットへ接続してください。'
                             '既知工程の削除や必要判断の根拠のない変更で回避しないでください。')
    return issues
