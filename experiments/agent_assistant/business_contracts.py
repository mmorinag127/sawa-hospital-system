"""Business phases are application policy, independent of a model's session."""
from contextvars import ContextVar
from dataclasses import dataclass

from .tasks import TASKS

SERVER_EXECUTION = ContextVar('sawa_business_execution', default=False)


def server_tools(task_id):
    spec = TASKS[task_id]
    names = {'shift_save_settings', 'shift_apply_rules', 'shift_propose_rules', 'finish_work', 'complete_shift_work', 'complete_fax_work', 'complete_lunch_daily_work', *spec.get('terminal', ()), *spec.get('exports', ())}
    if task_id in ('hospital_pending_faxes', 'hospital_review_faxes'):
        names.update(('hospital_check_anomalies', 'hospital_review_sheet', 'hospital_save_sheet', 'hospital_bag_order', 'hospital_confirm_order', 'hospital_export_labels', 'hospital_export_delivery_notes', 'hospital_export_aggregate'))
    return names


@dataclass(frozen=True)
class BusinessContract:
    prepare: str
    complete: str
    deliverables: tuple[str, ...]


CONTRACTS = {
    'hospital_pending_faxes': BusinessContract('agent', 'faxes', ('ラベル', '納品書', '製造集計')),
    'hospital_review_faxes': BusinessContract('agent', 'faxes', ('ラベル', '納品書', '製造集計')),
    'hospital_outputs': BusinessContract('hospital_outputs', 'review', ('ラベル', '納品書', '製造集計')),
    'shift_next_month': BusinessContract('shift', 'shift', ('確定勤務表',)),
    'shift_wishes': BusinessContract('shift', 'shift', ('確定勤務表',)),
    'shift_absence': BusinessContract('shift', 'shift', ('確定勤務表',)),
    'shift_check': BusinessContract('shift_selected', 'review', ('人員不足・勤務制約の確認結果',)),
    'shift_export': BusinessContract('shift_selected', 'review', ('確定勤務表',)),
    'lunch_import': BusinessContract('agent', 'review', ('登録済み月間献立',)),
    'lunch_daily_bundle': BusinessContract('agent', 'daily', ('作業工程表', '動線図', '実施価格表')),
    'lunch_procurement': BusinessContract('agent', 'review', ('月間食材必要量・発注案',)),
    'lunch_month_exports': BusinessContract('lunch_month', 'review', ('月間作業工程表', '月間動線図')),
}


def owned(run):
    return run.get('task_id') in CONTRACTS and run.get('task_inputs', {}).get('business_workflow', {}).get('version') == 1


def model_definitions(tools, run):
    definitions = tools.definitions(run['domains'])
    if not owned(run):
        return definitions
    server = server_tools(run['task_id'])
    return [definition for definition in definitions if definition['name'] not in server]


def require_server(run, name):
    if not owned(run) or SERVER_EXECUTION.get():
        return
    if name in server_tools(run['task_id']):
        from .store import Conflict
        raise Conflict('承認した内容の保存・確定・帳票出力は業務進行サーバーが実行します')


def preparation_instruction(tools, run):
    import json
    text = TASKS[run['task_id']]['instruction'] if run.get('task_id') in TASKS else run['instruction']
    text += '\n利用者の選択：' + json.dumps(run.get('task_inputs', {}), ensure_ascii=False)
    text += '\n添付原本：' + json.dumps([event['payload'] for event in tools.store.events(run['id']) if event['kind'] == 'source'], ensure_ascii=False)
    if owned(run):
        text += '\nこの実行は内容確認の準備だけを担当する。確定・帳票出力・finish_work・complete各操作は業務サーバーが実行するため呼ばない。確認結果を提出すると業務サーバーが人の承認を受け取り進行する。接続・予算・ID・版・停止・再開などの内部操作を人に質問しない。不足する業務情報だけを具体的な選択肢で質問する。'
        review = tools.flow.review(run['id'])
        if review and review.get('decision'):
            text += '\n保存済みの利用者の訂正依頼：' + json.dumps(review['decision'], ensure_ascii=False)
    return text
