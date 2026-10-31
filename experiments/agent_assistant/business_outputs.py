"""Execute only the approved native plan; build export bindings from its receipts."""
import json

from .fax_workflow import digest
from .store import Conflict
from .sandbox import run_blocking
from .task_flow import rows, school_export_query
from .tasks import TASKS


def export_parameters(run, review):
    task_id = run['task_id']
    context = run['task_inputs']
    if task_id == 'hospital_outputs':
        return [(operation, {'query': {'order_id': target['key']}})
                for target in context['targets'] for operation in TASKS[task_id]['exports']]
    if task_id == 'shift_export':
        return [('shift_export', {'ids': entry['parameters']['ids'], 'query': {**entry['parameters']['query'], 'format': 'xlsx'}})
                for entry in review['evidence'] if entry['operation'] == 'shift_get_run']
    if not TASKS[task_id]['exports']:
        return []
    query = school_export_query(run)
    if task_id == 'lunch_procurement':
        evidence = [entry for entry in review['evidence'] if entry['operation'] == 'lunch_get_procurement']
        if len(evidence) != 1 or not evidence[0]['data'].get('fingerprint'):
            raise Conflict('承認した発注案の保存状態を一つに確定できません')
        return [('lunch_export_procurement', {'query': {**query, 'expected_fingerprint': evidence[0]['data']['fingerprint']}})]
    if task_id == 'lunch_month_exports':
        exports = []
        for read, operation in [('lunch_get_schedules', 'lunch_export_month_schedules'), ('lunch_get_routes', 'lunch_export_month_routes')]:
            evidence = [entry for entry in review['evidence'] if entry['operation'] == read]
            if len(evidence) != 1:
                raise Conflict('承認した月間帳票の一覧を一つに確定できません')
            sources = []
            for item in rows(evidence[0]['data']):
                source = {key: item[key] for key in ('id', 'version', 'service_date')}
                source['facility_id'] = item.get('facility_id', item.get('facility', {}).get('id'))
                if read == 'lunch_get_routes':
                    source.update({key: item[key] for key in ('layout_id', 'layout_version')})
                sources.append(source)
            exports.append((operation, {'query': {**query, 'expected_sources': json.dumps(sources)}}))
        return exports
    if TASKS[task_id]['exports']:
        raise Conflict('この業務の帳票取得手順が定義されていません')
    return []


async def complete_review(engine, run, review):
    tools = engine.tools
    await tools.flow.recheck(run, review)
    command = 'business:' + review['id']
    for index, action in enumerate(review['actions']):
        current = await run_blocking(tools.flow.review, run['id'])
        if digest(action) in current['applied']:
            continue
        await engine.call(run, action['operation'], {'parameters': {**action['parameters'], 'reason': '利用者が確認し承認した内容だけを保存'}}, command + ':apply:' + str(index))
    review = await run_blocking(tools.flow.review, run['id'])
    artifacts = []
    for index, (operation, parameters) in enumerate(export_parameters(run, review)):
        result = await engine.call(run, operation, {'parameters': parameters}, command + ':export:' + str(index))
        if not result.get('artifact'):
            raise Conflict('承認した帳票を取得できません')
        artifacts.append(result['artifact']['id'])
    evidence = []
    reads = [(entry['operation'], entry['parameters']) for entry in review['evidence']]
    if run['task_id'] == 'lunch_import':
        reads.append(('lunch_get_menus', {'query': {key: run['task_inputs']['inputs'][key] for key in ('facility_id', 'month')}}))
    for index, (name, parameters) in enumerate(reads):
        result = await engine.call(run, name, {'parameters': parameters}, command + ':verify:' + str(index))
        evidence.append(result['command_id'])
    return await engine.call(run, 'finish_work', {'summary': '承認した内容の保存・出力と、保存結果の再確認が完了しました。', 'evidence_commands': evidence, 'artifact_ids': artifacts}, command + ':finish')
