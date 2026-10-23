"""Business-readable proposals derived from the discovery snapshot, never hidden selection."""
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import sqlite3


def business_today():
    return datetime.now(ZoneInfo('Asia/Tokyo')).date()


def adjacent_month(month, offset):
    year, number = map(int, month.split('-'))
    index = year * 12 + number - 1 + offset
    return f'{index // 12:04}-{index % 12 + 1:02}'


def month_label(month):
    year, number = map(int, month.split('-'))
    return f'{year}年{number}月'


def rank_months(months, preferred):
    return sorted(set(months), key=lambda value: (value != preferred, abs(int(value[:4])*12+int(value[5:])-int(preferred[:4])*12-int(preferred[5:])), value))


def school_months(database, facility_id=None):
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute('SELECT facility_id,service_date FROM school_lunch_menu_executions' + (' WHERE facility_id=?' if facility_id else ''), (facility_id,) if facility_id else ()).fetchall()
    counts = Counter()
    for school, day in rows:
        date.fromisoformat(day)
        counts[(school, day[:7])] += 1
    return counts


def batches(choices, task_id):
    if not choices:
        return []
    if task_id == 'hospital_review_faxes':
        return [{'label': f'要確認FAXをまとめて確認する（{len(choices)}件）', 'keys': [item['key'] for item in choices], 'reason': '原本・施設・週を照合し、確認できたものから順に質問します'}]
    if task_id == 'hospital_outputs':
        groups = {}
        for item in choices:
            data = item['data']
            week = item.get('week')
            if not week:
                continue
            groups.setdefault(str(week), []).append(item)
        return [{'label': f'{week} の確定済み注文を出力（{len(items)}件）', 'keys': [item['key'] for item in items], 'reason': '施設と対象週を確認して、ラベル・納品書・製造集計をまとめて作成します'} for week, items in sorted(groups.items(), reverse=True)]
    if task_id in ('shift_check', 'shift_export'):
        return [{'label': item['label'], 'keys': [item['key']], 'reason': '確定済みの勤務表です' if task_id == 'shift_export' else '保存済みの勤務案から人員不足・制約違反を確認します'} for item in choices]
    return []


def absence_days(today=None):
    today = today or business_today()
    return [{'date': (today+timedelta(days=i)).isoformat(), 'label': ('今日' if i == 0 else '明日') + '（' + (today+timedelta(days=i)).strftime('%m/%d') + '）'} for i in range(2)]


def school_output_counts(database, facility_id=None):
    result = {}
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        for table, label in [('school_lunch_daily_work_schedules', '作業工程表'), ('school_lunch_route_diagrams', '動線図')]:
            dates = db.execute(f'SELECT facility_id,service_date FROM {table}' + (' WHERE facility_id=?' if facility_id else ''), (facility_id,) if facility_id else ()).fetchall()
            for school, day in dates:
                date.fromisoformat(day)
                result.setdefault(day[:7], {}).setdefault(label, set()).add((school, day))
    return {month: {label: len(days) for label, days in values.items()} for month, values in result.items()}
