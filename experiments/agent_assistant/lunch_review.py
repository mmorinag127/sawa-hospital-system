"""Present saved daily facts without changing native evidence or approval."""


def build_lunch_review(review, questions):
    if not review or not questions:
        return None
    equipment = {template['template_id']: {row['id']: row['name'] for row in template['equipment']}
                 for source in questions['sources']['menus'] for template in source['options']['templates']}
    result = []
    for entry in review['evidence']:
        if entry['operation'] != 'lunch_get_process':
            continue
        data = entry['data']; document = data['document']
        names = {row['id']: row['name'] for row in document['ingredients']}
        inventory = equipment.get((document.get('route_config') or {}).get('template_id'), {})
        for step in document['steps']:
            work = step.get('daily_work')
            if not work:
                continue
            actual = step.get('execution') or {}
            for placement in work['placements']:
                ident = placement['equipment']['id']
                result.append({'献立': data['menu']['name'],
                               '食材': '・'.join(names[mid] for mid in placement['ingredient_ids']),
                               '作業': step['operation'],
                               '調理方法': {'individual': '単独の作業', 'together': '一緒に調理', 'separate': '別々に調理'}[work['mode']],
                               '使用設備': inventory.get(ident, '設備ID：' + ident),
                               '開始': actual.get('start_time') or '未入力',
                               '終了': actual.get('end_time') or '未入力',
                               '担当': '・'.join(actual.get('assignees', [])) or '未入力'})
    return result or None
