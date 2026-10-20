"""Training-page observations, not held-out menu answers or fixed recipes.

Keep the observed visit distinct from an unlabelled culinary operation. These
examples are suggestions to a new draft; they never rewrite an operator graph.
"""
from copy import deepcopy
import json
from pathlib import Path


EXAMPLES = [
    {
        'id': 'heated-aromatic-mincing', 'page': 1,
        'observation': '厚揚げのそぼろ煮のしょうがは手配表Page1の18行で「みじん切り」。図面ではミキサーを経て本調理へ進む。',
        'application': '加熱するたれや煮物の香味野菜を細かくみじん加工する場合、手作業を既定にせず、この施設のミキサー使用例を参照して設備を選ぶ。',
        'limit': '薄切り・輪切り・千切りへ広げない。しょうが以外への適用は施設例からの類推と明記し、手切りが明示されればその指定を優先する。',
    },
    {
        'id': 'cold-salad-vegetable-slicing', 'page': 6,
        'observation': '冷製和え物の生鮮野菜はシンク・前処理台を経てスライサーへ進む。',
        'application': '薄い切裁を行う冷製和え物の生鮮野菜にはスライサーの使用例を参照する。',
        'limit': '手切り指定や機械に適さない形状へ適用しない。設備選択の類推であり作業者確定ではない。',
    },
    {
        'id': 'processed-material-preparation', 'page': 1,
        'observation': '煮物の加工大豆製品と加工こんにゃくは、中央の作業台・シンクを経て本調理へ進む。',
        'application': '加工済みで切裁不要でも、開封・投入準備等の作業を省略して原材料から釜へ直結してよいとは限らない。手配表の明示作業を保持し、施設の準備場所を経る案を検討する。',
        'limit': '図には開封・水切りなどの作業名はない。方法を固定せず、未記名の準備作業と明示する。',
    },
    {
        'id': 'cold-noodle-draining', 'page': 5,
        'observation': '冷製麺料理の麺の番号は、釜・冷却・脱水機・盛付側の作業台に記載される。',
        'application': '茹でて冷やす冷製麺も、和え物の野菜だけでなく水切り場所を経る使用例がある。元の切断指示があれば保持する。',
        'limit': '温製麺や汁へ直接入れる麺へ一律適用しない。脱水時間・回転条件はこの図から分からない。',
    },
]

DRAWING_EXAMPLES = [
    {
        'id': 'separate-preparation-shared-number', 'page': 1,
        'observation': '同じ煮物の加工大豆製品と加工こんにゃくが同じ食品番号。個別の前処理内容は同じとは限らない。',
        'application': '同じ番号は同時混合を意味しない。共通の準備・投入系統として扱う根拠があれば個別作業を残して共有番号を提案できる。',
        'limit': '最後に同じ献立へ入るだけで全材料を一括にしない。主材、別系統の生鮮加工、後入れ等の違いを説明する。',
    },
    {
        'id': 'different-cuts-shared-number', 'page': 6,
        'observation': '肉じゃがのにんじんと玉ねぎは共通の食品番号。切り方が異なっても同じ前処理系統を共有する。',
        'application': '切り方・作業名の完全一致だけを番号共有の必要条件にしない。個別の切り方と作業は保持し、洗浄・同じ切裁設備・投入先という前処理系統を比較する。水戻しの乾物や、切裁のない加工品まで同じグループに広げない。',
        'limit': '異なる作業カードを同じカードと偽らない。shared_step_idsには実際の共通カードのみを指定する。',
    },
]


def examples_for(document, *, drawing=False):
    if (document.get('route_config') or {}).get('template_id') not in {'kitchen-rice', 'kitchen-bread'}:
        return []
    return [dict(deepcopy(item), source='R8.7作業導線図.pdf', status='training-observation')
            for item in (DRAWING_EXAMPLES if drawing else EXAMPLES)]


def correction_policy_for(document):
    """Versioned facts for the selected kitchen template, never a global rule."""
    template_id = (document.get('route_config') or {}).get('template_id')
    path = Path(__file__).parents[1] / 'data' / 'kitchen_svg' / 'process-correction-policy.json'
    policy = json.loads(path.read_text(encoding='utf-8'))
    if template_id not in policy['template_ids']:
        return {'version': policy['version'], 'facts': []}
    return {'version': policy['version'], 'facts': deepcopy(policy['facts'])}
