"""Explicit synthetic materials, work and registered equipment, never real inputs."""
from copy import deepcopy
import httpx
from experiments.agent_assistant.conversation import check_answer


def install(native):
    menu = deepcopy(native.data['/api/school-lunch/menu-executions'][0])
    menu.update(name='野菜スープ（合成画面試験）')
    native.data['/api/school-lunch/menu-executions'][0] = menu
    document = {'schema_version': 1, 'ingredients': [{'id': 'm1', 'name': 'にんじん', 'state': ''}, {'id': 'm2', 'name': 'じゃがいも', 'state': ''}], 'steps': [
        {'id': 'cut-carrot', 'operation': '切る', 'inputs': [{'ingredient_id': 'm1'}], 'equipment': {'type': '作業台', 'id': None}, 'basis': {}, 'conditions': {}, 'outputs': []},
        {'id': 'cut-potato', 'operation': '切る', 'inputs': [{'ingredient_id': 'm2'}], 'equipment': {'type': '作業台', 'id': None}, 'basis': {}, 'conditions': {}, 'outputs': []},
        {'id': 'cook', 'operation': '煮る', 'inputs': [{'step_id': 'cut-carrot'}, {'step_id': 'cut-potato'}], 'equipment': {'type': '回転釜', 'id': None}, 'basis': {}, 'conditions': {}, 'outputs': []}],
        'route_config': {'template_id': 'kitchen-rice', 'step_ports': {}}}
    for index, step in enumerate(document['steps']):
        step['execution'] = {'start_time':f'08:{index*10:02d}', 'end_time':f'08:{(index+1)*10:02d}',
            'assignees':['合成試験担当'], 'work_zone':'non_contaminated', 'phase':'cook',
            'kettle_use':None, 'uses_raw_meat':None}
    native.data['/api/school-lunch/menu-executions/101/process'] = {'menu': {'id': 101, 'name': menu['name'], 'facility_id': 1, 'service_date': menu['service_date'], 'version': menu['version']}, 'version': 1, 'recipe_revision': 1, 'revision': 1, 'status': 'draft', 'scope': 'day', 'document': document, 'proposal': None}
    equipment = [{'id': 'table-1', 'name': '下処理台1', 'type': '作業台', 'ports': []}, {'id': 'table-2', 'name': '下処理台2', 'type': '作業台', 'ports': []}, {'id': 'kettle-a', 'name': '回転釜A', 'type': '回転釜', 'ports': []}, {'id': 'kettle-b', 'name': '回転釜B', 'type': '回転釜', 'ports': []}]
    native.data['/api/school-lunch/menu-executions/101/process/route-options'] = {'templates': [{'template_id': 'kitchen-rice', 'template_version': 1, 'equipment': equipment}], 'colors': [], 'day_colors': []}
    native.data['/api/school-lunch/route-diagrams/layouts'][0]['locations'] = {'下処理台1': [0.2, 0.2], '下処理台2': [0.4, 0.2], '回転釜A': [0.2, 0.7], '回転釜B': [0.4, 0.7]}
    import base64, hashlib
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 600"><rect width="900" height="600" fill="white"/></svg>'
    native.data['/api/school-lunch/menu-executions/101/process/question-drawing'] = {'template_id':'kitchen-rice','template_version':1,'title':'合成試験図面','view_box':[0,0,900,600],'sha256':hashlib.sha256(svg).hexdigest(),'svg_base64':base64.b64encode(svg).decode(),'equipment':[{'id':e['id'],'name':e['name'],'bounds':[100+i*150,100,100,80]} for i,e in enumerate(equipment)]}
    request = native.request
    async def write(run, domain, method, path, **kwargs):
        if method == 'POST' and path == '/api/school-lunch/route-diagrams/layouts':
            native.writes.append((path,deepcopy(kwargs)))
            layouts=native.data[path]; ident=max(x['id'] for x in layouts)+1
            row={'id':ident,'facility_id':1,'name':kwargs['body']['name'],'version':1,'locations':{},'image':'data:image/png;base64,'+base64.b64encode(kwargs['files']['file'][1]).decode()}
            layouts.append(row);return httpx.Response(200,json=deepcopy(row))
        if method == 'PUT' and path.startswith('/api/school-lunch/route-diagrams/layouts/'):
            native.writes.append((path,deepcopy(kwargs)))
            row=next(x for x in native.data['/api/school-lunch/route-diagrams/layouts'] if x['id']==int(path.rsplit('/',1)[1]))
            assert kwargs['body']['version']==row['version']
            row.update(locations=deepcopy(kwargs['body']['locations']),version=row['version']+1)
            return httpx.Response(200,json=deepcopy(row))
        if method == 'POST' and path == '/api/school-lunch/route-diagrams/generate':
            native.writes.append((path,deepcopy(kwargs)))
            row=native.data['/api/school-lunch/route-diagrams'][0]
            layout=next(x for x in native.data['/api/school-lunch/route-diagrams/layouts'] if x['id']==kwargs['body']['layout_id'])
            row.update(layout_id=layout['id'],layout_version=layout['version'],version=row['version']+1)
            return httpx.Response(200,json=deepcopy(row))
        if method == 'PUT' and path == '/api/school-lunch/menu-executions/101/process':
            native.writes.append((path, deepcopy(kwargs)))
            value = native.data[path]; value.update(document=deepcopy(kwargs['body']['document']), version=value['version'] + 1, recipe_revision=value['recipe_revision'] + 1, revision=value['revision'] + 1)
            return httpx.Response(200, json=deepcopy(value))
        return await request(run, domain, method, path, **kwargs)
    native.request = write
    return document


async def reply(tools, run, result, selection):
    current = tools.store.get(run['id'])
    check_answer(tools.store.events(run['id']), selection, result['command_id'], selection if selection in result.get('options',[]) else None)
    selected = await tools.flow.questions.validate_reply(current, selection, result['command_id'])
    tools.flow.questions.accept_reply(current, selected)
    tools.store.event(run['id'], 'operator_reply', {'message': selection, 'question_id': result['command_id'], 'option': selection if selection in result.get('options',[]) else None})
    tools.store.update(run['id'], status='running')


async def complete(tools, run):
    choices = ['下処理台1', '下処理台2', '一緒に調理しました', '回転釜B']
    for index, selection in enumerate(choices):
        result = await tools.execute(run['id'], run['generation'], 'ask_lunch_process_question', {}, 'place-question-'+str(index))
        assert result.get('waiting_input'), result
        await reply(tools, run, result, selection)
    result = await tools.execute(run['id'], run['generation'], 'ask_lunch_process_question', {}, 'places-complete')
    assert result.get('complete'), result
    for index, parameters in enumerate(result['process_saves']):
        saved = await tools.execute(run['id'], run['generation'], 'lunch_save_process', {'parameters': parameters}, 'places-save-'+str(index))
        assert saved.get('ok'), saved
    return result
