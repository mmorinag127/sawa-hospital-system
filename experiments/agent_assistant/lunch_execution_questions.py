"""Operator answers for the pinned native StepExecution contract."""
import re
from .store import Conflict

FIELDS = {
    'start_time': '開始時刻', 'end_time': '終了時刻', 'assignees': '担当者',
    'work_zone': '汚染区分', 'phase': '作業段階',
}
OPTIONS = {
    'work_zone': [('contaminated', '汚染区域'), ('contaminated_meat_fish', '汚染区域（肉・魚）'), ('non_contaminated', '非汚染区域')],
    'phase': [('prep', '下処理'), ('wash_open', '洗浄・開封'), ('cook', '調理'), ('heat', '加熱'), ('plate', '盛付'), ('other', 'その他')],
}


def missing(document):
    return [(step, field) for step in document['steps'] for field in FIELDS
            if not (step.get('execution') or {}).get(field)]


def question(item):
    pending = missing(item['document'])
    if not pending:
        return None
    step, field = pending[0]
    text = f"「{item['name']}」の {step['operation']} の{FIELDS[field]}を教えてください。"
    if field in ('start_time', 'end_time'):
        text += ' 08:30の形式で入力してください。'
    elif field == 'assignees':
        text += ' 複数人の場合は「、」で区切って入力してください。'
    return step['id'], field, text, [{'value': value, 'label': label} for value, label in OPTIONS.get(field, [])]


def parse(field, message):
    value = message.strip()
    if field in ('start_time', 'end_time'):
        if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', value):
            raise Conflict('時刻は08:30のように24時間表記で入力してください')
        return value
    if field == 'assignees':
        names = [name.strip() for name in re.split('[、,\n]', value)]
        if not names or len(names) > 100 or any(not name for name in names):
            raise Conflict('担当者名を入力してください。複数人は「、」で区切ってください')
        return names
    raise Conflict('表示された汚染区分・作業段階を選んでください')


def apply(step, field, value):
    if field not in FIELDS:
        raise Conflict('工程の回答項目が一致しません')
    execution = {'start_time': None, 'end_time': None, 'assignees': [], 'work_zone': None,
                 'phase': None, 'kettle_use': None, 'uses_raw_meat': None,
                 **(step.get('execution') or {})}
    execution[field] = value
    step['execution'] = execution
