"""Review candidates from arrangement rows and the supplied facility rules.

No specimen routes, date-specific answer graph, equipment instance, actual time
or operator confirmation is generated. Missing cooking/merge choices stay
visible in each candidate. These rules are proposals for the supplied kitchen,
not universal food-safety instructions.
"""
import hashlib
import json
import unicodedata

MAIL_SOURCE = '学校給食AI活用について・2026-10-05回答'
FROZEN = ('ブロッコリー', 'ほうれん草', '小松菜', '枝豆', 'とうもろこし',
          'グリンピース', 'いんげん', 'しめじ', 'えのき', '液卵', '錦糸卵', '油揚げ')
BOILED = ('うずら卵', 'ヤングコーン', 'れんこん', '大豆')
SLICED = ('キャベツ', 'にんじん', '玉ねぎ', '玉葱', 'きゅうり', '大根', 'だいこん')

def normalized(value):
    return unicodedata.normalize('NFKC', value).replace(' ', '').replace('\u3000', '')

def identity(menu):
    """Quantity/date changes do not create a new procedure; recipe changes do."""
    value = [menu['name'], [(i['name'], i.get('cut', '')) for i in menu['ingredients']],
             menu.get('instructions', [])]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def candidate(menu):
    materials = []
    steps = []
    associations = []
    questions = []
    name = normalized(menu['name'])
    def step(mid, previous, operation, equipment, rule, description=''):
        sid = f'{mid}-s{len(steps)+1}'
        steps.append({'id': sid, 'operation': operation,
                      'inputs': [{'step_id': previous}] if previous else [{'ingredient_id': mid}],
                      'equipment': {'type': equipment, 'id': None}, 'outputs': [],
                      'conditions': {'description': description},
                      'basis': {'review': 'pending', 'rule': rule, 'source': MAIL_SOURCE}})
        return sid
    for index, raw in enumerate(menu['ingredients']):
        mid = f'material-{index+1}'
        food = normalized(raw['name'])
        state = '冷凍' if any(x in food for x in FROZEN) else '水煮' if any(x in food for x in BOILED) else ''
        if '白菜' in food:
            state = 'カット済み'
        materials.append({'id': mid, 'name': raw['name'], 'state': state})
        if name == '牛乳':
            associations.append({'material_id':mid,'rule':'school-delivery','label':'学校へ直接納品・工程表/動線対象外'})
            continue
        if name == 'ご飯' or name.startswith('麦ご飯'):
            continue
        before = len(steps)
        previous = None
        if state in ('冷凍', '水煮'):
            previous = step(mid, previous, '開封・解凍洗浄', 'シンク', 'opening-wash', '回答メール：解凍・洗浄は開封時に実施。個別の要否を確認。')
        if 'ひじき' in food or '干し' in food or '乾燥' in food:
            previous = step(mid, previous, '戻す', '作業台', 'dry-soak', '野菜加工作業場の作業台、ボウルで戻す。商品ごとの適用を確認。')
        if 'にんにく' in food or 'しょうが' in food or '生姜' in food:
            previous = step(mid, previous, 'ミキサー処理', 'ミキサー', 'garlic-ginger-mixer')
        elif raw.get('cut'):
            manual = '乱切' in raw['cut'] or any(x in food for x in ('もやし','厚揚げ','竹輪','かぼちゃ'))
            eq = 'スライサー' if not manual and any(x in food for x in SLICED) else '作業台'
            previous = step(mid, previous, '切る', eq, 'source-cut', raw['cut'] + '（設備の適用・個体は要確認）')
        if 'れんこん' in food or 'こんにゃく' in food:
            previous = step(mid, previous, '下茹で', '回転釜', 'always-blanch')
        # Unknown cooking, mixing and finishing is not replaced by a made-up
        # common batch. The operator connects these ingredient branches.
        previous = step(mid, previous, '投入先・後工程を確認', None, 'unresolved-destination', '手配表の調理手順を確認し、主調理・合流先へ接続してください。')
        associations.append({'material_id': mid, 'label': f"{menu['name']} / {raw['name']}",
                             'step_ids': [s['id'] for s in steps[before:]], 'status': 'draft'})
    if name == 'ご飯' or name.startswith('麦ご飯'):
        steps = [
            {'id':'rice-cook','operation':'炊飯','inputs':[{'ingredient_id':i['id']} for i in materials],
             'equipment':{'type':'炊飯器','id':None},'conditions':{},'basis':{'review':'pending','source':MAIL_SOURCE},'outputs':[]},
            {'id':'rice-serve','operation':'盛付','inputs':[{'step_id':'rice-cook'}],
             'equipment':{'type':'作業台','id':None},'conditions':{},'basis':{'review':'pending','source':MAIL_SOURCE},'outputs':[]}]
        questions.append('搬入・保管・洗米の設備と接続を確認。炊飯以降の工程案。')
    elif name != '牛乳':
        questions.append('主調理・合流・盛付の接続は未確定。調理手順に従って編集が必要。')
    return {'schema_version':2,'ingredients':materials,'steps':steps,'route_config':None}, associations, questions

def build_candidates(source):
    groups = {}
    for day in source['days']:
        for menu in day['menus']:
            key = identity(menu)
            if key not in groups:
                document, associations, questions = candidate(menu)
                groups[key] = {'name':menu['name'],'expected_version':0,'status':'draft',
                    'source_month':day['date'][:7], 'document':document,
                    'pattern_associations':associations,
                    'provenance':{'source_sha256':source['sha256'],'source_menu_key':key,
                        'instructions':menu.get('instructions',[]),'questions':questions,'occurrences':[]},
                    'change_note':'手配表と回答メールによる初期候補・未確認'}
            groups[key]['provenance']['occurrences'].append({'date':day['date'],'school':day['school'],'cell':menu['source_cell']})
    return list(groups.values())
