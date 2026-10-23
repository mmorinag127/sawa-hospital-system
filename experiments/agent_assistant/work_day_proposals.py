"""Suggest registered days from the exact owned school snapshot, never a calendar default."""
from datetime import date, datetime
from zoneinfo import ZoneInfo
import sqlite3


def propose(database, facility_id, *, today=None):
    today = today or datetime.now(ZoneInfo('Asia/Tokyo')).date()
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        menus = db.execute('SELECT id,service_date,name FROM school_lunch_menu_executions WHERE facility_id=? ORDER BY service_date,id', (facility_id,)).fetchall()
        schedules = {row[0] for row in db.execute('SELECT service_date FROM school_lunch_daily_work_schedules WHERE facility_id=?', (facility_id,))}
        routes = {row[0] for row in db.execute('SELECT service_date FROM school_lunch_route_diagrams WHERE facility_id=?', (facility_id,))}
        prices = {row[0] for row in db.execute('SELECT implementation_date FROM implementation_price_drafts WHERE facility_id=?', (facility_id,))}
    grouped = {}
    for menu in menus:
        day = menu['service_date']
        date.fromisoformat(day)
        grouped.setdefault(day, []).append(menu['name'])
    result = []
    for day, names in grouped.items():
        missing = [name for name, dates in [('作業工程表', schedules), ('動線図', routes), ('実施価格', prices)] if day not in dates]
        result.append({'service_date': day, 'menu_names': names, 'menu_count': len(names), 'missing': missing,
                       'reason': '・'.join(missing) + 'が未作成です' if missing else '三帳票の保存があります。最新の献立との一致を確認します'})
    def priority(item):
        day = date.fromisoformat(item['service_date'])
        return (not bool(item['missing']), day < today, abs((day - today).days), item['service_date'])
    return sorted(result, key=priority)
