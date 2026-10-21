"""Version-bound daily work-place answers. Models cannot manufacture answers."""
import base64
import hashlib
import math
from copy import deepcopy
from uuid import uuid4

from .conversation import question_result
from .store import Conflict

MARKER = 'agent_daily_question'


def material_ids(document):
    materials = {item['id']: item['name'] for item in document['ingredients']}
    if len(materials) != len(document['ingredients']):
        raise Conflict('材料IDが重複しています')
    steps, outputs, result = {}, {}, {}
    for step in document['steps']:
        ids = set()
        for source in step['inputs']:
            if source.get('ingredient_id'):
                if source['ingredient_id'] not in materials: raise Conflict('工程の材料が一致しません')
                ids.add(source['ingredient_id'])
            elif source.get('step_id') in steps:
                if source.get('output_id'):
                    key = (source['step_id'], source['output_id'])
                    if key not in outputs: raise Conflict('工程の分岐を確認してください')
                    ids.update(outputs[key])
                else: ids.update(steps[source['step_id']])
            else: raise Conflict('工程の順序・材料接続を確認してください')
        if not ids or step['id'] in steps: raise Conflict('工程の材料接続が未確定です')
        steps[step['id']] = ids
        result[step['id']] = [key for key in materials if key in ids]
        for output in step.get('outputs', []):
            selected = set(output['ingredient_ids']) if output.get('ingredient_ids') is not None else ids
            if not selected or not selected <= ids: raise Conflict('工程の分岐材料が一致しません')
            outputs[(step['id'], output['id'])] = selected
    if set().union(*steps.values()) != set(materials):
        raise Conflict('作業工程のない食材があります。工程案を確認してください')
    return result


def lineage(document):
    names = {m['id']: m['name'] for m in document['ingredients']}
    return {step: [names[mid] for mid in ids] for step, ids in material_ids(document).items()}


def work_progress(documents):
    answered = total = 0
    for item in documents:
        for step in item['document']['steps']:
            groups = item.get('work_groups', {}).get(step['id'], {}).get('groups', [{}])
            total += len(groups)
            answered += sum('equipment' in group for group in groups)
    return {'answered': answered, 'total': total}


class LunchQuestions:
    def __init__(self, flow): self.flow, self.store = flow, flow.store

    @staticmethod
    def active(run): return run.get('task_id') == 'lunch_daily_bundle'

    def state(self, run):
        if not self.active(run): raise Conflict('学校給食の日次作業専用です')
        return self.store.get(run['id'])['task_inputs'].get('process_questions')

    def save(self, run, state):
        current = self.store.get(run['id'])
        if current['generation'] != run['generation'] or current['status'] not in ('running', 'waiting_input'):
            raise Conflict('依頼は停止されました。作業場所の回答を保存しません')
        context = current['task_inputs']
        context['process_questions'] = state
        self.store.update(run['id'], task_inputs=context)

    async def sources(self, run):
        context = self.store.get(run['id'])['task_inputs']; inputs = context['inputs']
        query = {'facility_id': inputs['facility_id'], 'month': inputs['month']}
        if not inputs.get('layout_id'): raise Conflict('使用する図面を先に回答してください')
        layouts = await self.flow.read(run, 'lunch_get_layouts', {'query': query})
        layout = next((x for x in layouts if x['id'] == inputs['layout_id'] and x['version'] == inputs['layout_version']), None)
        if not layout: raise Conflict('選択した図面が見つからない、または図面が変更されています')
        menus = await self.flow.read(run, 'lunch_get_menus', {'query': query})
        expected = {str(t['data']['id']): t['data']['version'] for t in context['targets']}
        actual = {str(m['id']): m['version'] for m in menus if m['service_date'] == inputs['service_date']}
        if expected != actual: raise Conflict('対象日の献立・版が変わっています。選び直してください')
        result = []
        for target in context['targets']:
            ident = target['data']['id']; params = {'ids': {'menu_id': ident}, 'query': query}
            process = await self.flow.read(run, 'lunch_get_process', params)
            options = await self.flow.read(run, 'lunch_get_process_route_options', params)
            if process['menu']['id'] != ident or process['menu']['facility_id'] != inputs['facility_id'] or process['menu']['service_date'] != inputs['service_date'] or process['menu']['version'] != expected[str(ident)]:
                raise Conflict('工程の学校・日付・献立版が一致しません')
            result.append({'menu_id': ident, 'process': process, 'options': options})
        return {'layout': {k: layout[k] for k in ('id', 'version', 'locations')}, 'menus': result}

    async def drawing(self, run, menu_id, template_id):
        inputs = self.store.get(run['id'])['task_inputs']['inputs']
        data = await self.flow.read(run, 'lunch_get_process_drawing', {'ids': {'menu_id': menu_id}, 'query': {'facility_id': inputs['facility_id'], 'template_id': template_id}})
        if data.get('template_id') != template_id:
            raise Conflict('質問の原本図面が使用する図面と一致しません')
        try: content = base64.b64decode(data['svg_base64'], validate=True)
        except (ValueError, KeyError): raise Conflict('原本図面を読み取れません') from None
        checksum = hashlib.sha256(content).hexdigest()
        if data.get('sha256') != checksum or len(content) > 2_000_000:
            raise Conflict('原本図面の内容またはサイズが一致しません')
        box = data.get('view_box')
        if not isinstance(box, list) or len(box) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in box) or min(box[2:]) <= 0:
            raise Conflict('原本図面の座標範囲が不正です')
        ids = set()
        for equipment in data['equipment']:
            bounds = equipment.get('bounds')
            if equipment['id'] in ids or not isinstance(bounds, list) or len(bounds) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds):
                raise Conflict('原本図面の設備座標が不正です')
            ids.add(equipment['id'])
            x, y, w, h = bounds
            if w <= 0 or h <= 0 or x < box[0] or y < box[1] or x+w > box[0]+box[2] or y+h > box[1]+box[3]:
                raise Conflict('設備が原本図面の範囲外です')
        folder = self.flow.tools.sandbox.path(run['id']) / 'question-drawings'
        folder.mkdir(exist_ok=True)
        target = folder / (checksum + '.svg')
        if target.exists() and target.read_bytes() != content: raise Conflict('保存済み原本図面が変更されています')
        if not target.exists(): target.write_bytes(content)
        return {k: v for k, v in data.items() if k != 'svg_base64'}

    async def checked(self, run):
        state = self.state(run)
        if not state: raise Conflict('食材ごとの作業場所・使用する釜を先に質問してください')
        if state.get('policy_version') != 2:
            raise Conflict('一緒・別々の確認を含む質問をやり直してください')
        if await self.sources(run) != state['sources']:
            review = self.flow.review(run['id'])
            if review: review['state'] = 'stale'; self.flow.save_review(run['id'], review)
            raise Conflict('質問後に献立・工程・設備・図面が変更されています。質問をやり直してください')
        for ident, expected in state.get('drawings', {}).items():
            if await self.drawing(run, int(ident), expected['template_id']) != expected:
                raise Conflict('質問後に原本図面または設備の位置が変更されています。再質問してください')
        return state

    async def ask(self, run, command_id, restart=False):
        state = self.state(run)
        if restart and state:
            review = self.flow.review(run["id"])
            changed = state.get("policy_version") != 2 or await self.sources(run) != state["sources"]
            if not changed:
                for ident, picture in state.get('drawings', {}).items():
                    if await self.drawing(run, int(ident), picture['template_id']) != picture:
                        changed = True
                        break
            if not changed and (not review or review["state"] != "needs_correction"):
                raise Conflict("回答をやり直すには入力変更または利用者の訂正依頼が必要です")
            self.save(run, None)
            state = None
        if state: state = await self.checked(run)
        else:
            sources = await self.sources(run)
            documents = []
            session_id = uuid4().hex
            for source in sources['menus']:
                process = source['process']; document = deepcopy(process['document'])
                if not document.get('steps'):
                    proposal = process.get('proposal')
                    if not proposal or proposal.get('base_version') != process['version']:
                        raise Conflict('食材の工程案が未作成です。既存の工程推論APIで案を取得してから質問してください')
                    document = deepcopy(proposal['document'])
                document["daily_work_source"] = session_id
                flows = lineage(document)
                for step in document['steps']: step.pop('daily_work', None)
                documents.append({'menu_id': source['menu_id'], 'name': process['menu']['name'], 'document': document, 'lineage': flows, 'material_ids': material_ids(document), 'work_groups': {}, 'answers': {}, 'saved': False})
            state = {'policy_version': 2, 'id': session_id, 'sources': sources, 'documents': documents, 'pending': None}
        if state['pending']: raise Conflict('表示中の質問への回答が必要です')
        for item, source in zip(state['documents'], state['sources']['menus']):
            document = item['document']; templates = source['options']['templates']
            selected = (document.get('route_config') or {}).get('template_id')
            if not selected:
                choices = []
                for template in templates:
                    picture = await self.drawing(run, item['menu_id'], template['template_id'])
                    if picture['template_version'] != template['template_version'] or not isinstance(picture.get('title'), str) or not picture['title'].strip():
                        raise Conflict('原本図面の名称・版を確認できません')
                    choices.append({'value': template['template_id'], 'label': picture['title']})
                return self.question(run, state, command_id, item, 'template', 'template', f"「{item['name']}」で使用した厨房図面はどれですか？", choices)
            template = next((t for t in templates if t['template_id'] == selected), None)
            if not template: raise Conflict('工程の厨房図面が登録候補にありません')
            for step in document['steps']:
                if step['id'] in item['answers']: continue
                ids = item['material_ids'][step['id']]
                work = item['work_groups'].get(step['id'])
                names = {m['id']: m['name'] for m in document['ingredients']}
                if work is None:
                    if len(ids) > 1:
                        ingredients = '・'.join(names[mid] for mid in ids)
                        return self.question(run, state, command_id, item, step['id'], 'grouping',
                            f"「{item['name']}」の {ingredients}\n「{step['operation']}」は、一緒に調理しましたか？",
                            [{'value': 'together', 'label': '一緒に調理しました'}, {'value': 'separate', 'label': '別々に調理しました'}])
                    work = {'mode': 'individual', 'groups': [{'ingredient_ids': ids}]}
                    item['work_groups'][step['id']] = work
                index, group = next((i,g) for i,g in enumerate(work['groups']) if 'equipment' not in g)
                equipment = step.get('equipment') or {}; kind = equipment.get('type')
                candidates = [x for x in template['equipment'] if x.get('type') and (not kind or x['type'] == kind)]
                choices = [{'value': x, 'label': x['name'] if sum(c['name'] == x['name'] for c in candidates) == 1 else f"{x['name']}（ID:{x['id']}）"} for x in candidates]
                picture = await self.drawing(run, item['menu_id'], selected)
                if picture['template_version'] != template['template_version']:
                    raise Conflict('候補設備と原本図面の版が一致しません')
                geometry = {e['id']: e for e in picture['equipment']}
                if any(c['value']['id'] not in geometry or geometry[c['value']['id']]['name'] != c['value']['name'] for c in choices):
                    raise Conflict('候補設備と原本の箇所を一意に対応付けられません')
                state.setdefault('drawings', {})[str(item['menu_id'])] = picture
                ingredient = '・'.join(names[mid] if sum(m['name']==names[mid] for m in document['ingredients'])==1 else names[mid]+'（ID:'+mid+'）' for mid in group['ingredient_ids']); suffix = 'どの釜を使いましたか？' if kind == '回転釜' else 'どこで作業しましたか？'
                return self.question(run, state, command_id, item, step['id'], 'equipment', f"「{item['name']}」の {ingredient}\n{step['operation']} は、{suffix}", choices, picture, index)
        self.save(run, state)
        return {'ok': True, 'complete': True, 'process_saves': [self.payload(state, item) for item in state['documents'] if not item['saved']]}

    def question(self, run, state, command_id, item, key, field, text, choices, picture=None, group_index=None):
        if not choices: raise Conflict('この工程で使用できる登録済みの場所・設備がありません。図面・設備登録を確認してください')
        if len({c['label'] for c in choices}) != len(choices): raise Conflict('設備の表示名とIDが重複しています')
        state['pending'] = {'question_id': command_id, 'menu_id': item['menu_id'], 'key': key, 'field': field, 'choices': choices, 'group_index': group_index}
        self.save(run, state)
        # More than eight registered candidates remain available by exact text;
        # the UI displays every registered choice through its dedicated field.
        result = question_result({'question': text, 'options': [c['label'] for c in choices[:8]]}, command_id)
        result.update(process_choices=[c['label'] for c in choices], process_context={'menu_name': item['name'], 'ingredients': item['lineage'].get(key, []), 'operation': next((s['operation'] for s in item['document']['steps'] if s['id'] == key), '厨房図面'), 'kind': field, 'material_ids': item['material_ids'].get(key, []) if group_index is None else item['work_groups'][key]['groups'][group_index]['ingredient_ids'], **work_progress(state['documents'])})
        if picture is not None:
            geometry = {e['id']: e for e in picture['equipment']}
            result['question_map'] = {k: picture[k] for k in ('template_id', 'template_version', 'sha256', 'view_box', 'title')}
            result['question_map']['hotspots'] = [{'id': c['value']['id'], 'name': c['value']['name'], 'option': c['label'], 'bounds': geometry[c['value']['id']]['bounds']} for c in choices]
        return result

    async def validate_reply(self, run, message, question_id):
        state = self.state(run) if self.active(run) else None
        if not state or not state['pending']: return None
        state = await self.checked(run); pending = state['pending']
        if pending['question_id'] != question_id: raise Conflict('最新の作業場所の質問に回答してください')
        selected = [c for c in pending['choices'] if message in (c['label'], c['value'].get('name') if isinstance(c['value'], dict) else c['value'])]
        if len(selected) != 1: raise Conflict('表示された登録済みの場所・設備を選んでください。未登録の場所は採用しません')
        return selected[0]

    def accept_reply(self, run, selected):
        if selected is None: return
        state = self.state(run); pending = state['pending']
        item = next(i for i in state['documents'] if i['menu_id'] == pending['menu_id'])
        if pending['field'] == 'template':
            item['document']['route_config'] = {**(item['document'].get('route_config') or {}), 'template_id': selected['value']}
        elif pending['field'] == 'grouping':
            ids = item['material_ids'][pending['key']]
            groups = [ids] if selected['value'] == 'together' else [[mid] for mid in ids]
            item['work_groups'][pending['key']] = {'mode': selected['value'], 'grouping_question_id': pending['question_id'], 'groups': [{'ingredient_ids': group} for group in groups]}
        else:
            step = next(s for s in item['document']['steps'] if s['id'] == pending['key'])
            work = item['work_groups'][step['id']]
            equipment = selected['value']
            group = work['groups'][pending['group_index']]
            group.update(equipment={'type': equipment['type'], 'id': equipment['id']}, question_id=pending['question_id'])
            if all('equipment' in g for g in work['groups']):
                step['equipment'] = deepcopy(work['groups'][0]['equipment']) if work['mode'] in ('individual', 'together') else {'type': step['equipment'].get('type'), 'id': None}
                step['daily_work'] = {'mode': work['mode'], 'placements': deepcopy(work['groups'])}
                if 'grouping_question_id' in work: step['daily_work']['grouping_question_id'] = work['grouping_question_id']
                step['basis'] = {**step.get('basis', {}), 'review': 'user-confirmed', MARKER: state['id']}
                (item['document'].get('route_config') or {}).get('step_ports', {}).pop(step['id'], None)
                item['answers'][step['id']] = deepcopy(step['daily_work'])
        state['pending'] = None; self.save(run, state)
        self.store.event(run['id'], 'process_answer', {'menu_id': item['menu_id'], 'step_id': pending['key'], 'selection': selected['label'], 'kind': pending['field'], 'group_index': pending['group_index']})

    def payload(self, state, item):
        source = next(x['process'] for x in state['sources']['menus'] if x['menu_id'] == item['menu_id'])
        return {'ids': {'menu_id': item['menu_id']}, 'query': {'facility_id': source['menu']['facility_id']}, 'body': {'expected_version': source['version'], 'menu_version': source['menu']['version'], 'expected_recipe_revision': source['recipe_revision'], 'scope': 'day', 'status': 'draft', 'document': item['document'], 'change_note': '利用者が食材ごとの作業場所・使用設備を回答'}, 'reason': '質問IDを記録した利用者の当日回答を保存'}

    async def gate(self, run, name, args=None):
        if not self.active(run): return
        state = await self.checked(run)
        if state['pending'] or any(len(i['answers']) != len(i['document']['steps']) for i in state['documents']):
            raise Conflict('未回答の作業場所・使用する釜があります')
        if name == 'lunch_save_process':
            item = next((i for i in state['documents'] if str(i['menu_id']) == str(args.ids.get('menu_id'))), None)
            if not item or item['saved'] or args.body != self.payload(state, item)['body']:
                raise Conflict('回答した作業場所・釜と保存する工程が一致しません')
        elif not all(i['saved'] for i in state['documents']): raise Conflict('回答した工程を保存・再取得してから帳票を作成してください')
        return state

    async def after_save(self, run, args):
        if not self.active(run): return
        state = self.state(run)
        item = next(i for i in state['documents'] if str(i['menu_id']) == str(args.ids['menu_id']))
        source = next(s for s in state['sources']['menus'] if s['menu_id'] == item['menu_id'])
        saved = await self.flow.read(run, 'lunch_get_process', {'ids': args.ids, 'query': args.query})
        before = deepcopy(item['document']); after = deepcopy(saved['document'])
        # Native save records field provenance; no semantic field may drift.
        for document in (before, after):
            for step in document['steps']: step.pop('basis', None)
        if before != after or any(s.get('basis', {}).get(MARKER) != state['id'] for s in saved['document']['steps']):
            raise Conflict('保存後の工程・使用設備が利用者の回答と一致しません')
        item['saved'] = True; source['process'] = saved
        self.save(run, state)
