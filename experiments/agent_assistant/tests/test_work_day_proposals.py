"""Registered days and asynchronous discovery; no model calls or cloud writes."""
import asyncio
from datetime import date
import sqlite3
import threading
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from experiments.agent_assistant.app import create_app
from experiments.agent_assistant.work_day_proposals import propose


def test_registered_days_are_ranked_by_missing_outputs_and_nearness_not_calendar_defaults(tmp_path):
    path = tmp_path / 'database.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''
        CREATE TABLE school_lunch_menu_executions(id INTEGER,facility_id INTEGER,service_date TEXT,name TEXT);
        CREATE TABLE school_lunch_daily_work_schedules(facility_id INTEGER,service_date TEXT);
        CREATE TABLE school_lunch_route_diagrams(facility_id INTEGER,service_date TEXT);
        CREATE TABLE implementation_price_drafts(facility_id INTEGER,implementation_date TEXT);
        INSERT INTO school_lunch_menu_executions VALUES(1,8,'2026-07-01','にんじん'),(2,8,'2026-07-01','じゃがいも'),(3,8,'2026-07-02','ごはん'),(4,8,'2026-06-01','過去の献立'),(5,9,'2026-07-03','別の学校');
        INSERT INTO school_lunch_daily_work_schedules VALUES(8,'2026-07-01');
        INSERT INTO school_lunch_route_diagrams VALUES(8,'2026-07-01');
        INSERT INTO implementation_price_drafts VALUES(8,'2026-07-01');
        ''')
    days = propose(path, 8, today=date(2026, 7, 1))
    assert [day['service_date'] for day in days] == ['2026-07-02', '2026-06-01', '2026-07-01']
    assert days[0]['missing'] == ['作業工程表', '動線図', '実施価格']
    assert days[-1]['menu_names'] == ['にんじん', 'じゃがいも']
    assert '最新の献立との一致を確認' in days[-1]['reason']
    assert propose(path, 99, today=date(2026, 7, 1)) == []


@pytest.mark.parametrize('task',['lunch_daily_bundle','hospital_pending_faxes'])
def test_slow_discovery_returns_acceptance_and_can_be_polled_without_blocking_other_requests(tmp_path,task):
    app = create_app({}, tmp_path, managed_client=SimpleNamespace(close=AsyncMock()))
    release = threading.Event()
    started = threading.Event()
    calls = []
    async def slow(task, inputs, owner, authorization):
        calls.append((task, inputs))
        started.set()
        await asyncio.to_thread(release.wait)
        return {'selection_id': 'exact-registered-selection', 'work_days': [{'service_date': '2026-07-01'}], 'choices': [], 'schools': [], 'layouts': []}
    app.state.tools.flow.options = slow
    try:
        with TestClient(app) as web:
            web.headers['X-Trial-Token'] = app.state.token
            body = {'request_key': 'registered-work-days', 'inputs': {'facility_id': 8}}
            endpoint = '/tasks/'+task+'/target-lookups'
            accepted = web.post(endpoint, json=body)
            assert accepted.status_code == 202
            assert started.wait(2)
            assert web.get('/tasks').status_code == 200
            pending = web.get('/target-lookups/registered-work-days').json()
            assert pending['state'] == 'queued' and pending['result'] is None
            assert web.post(endpoint, json=body).status_code == 202
            assert len(calls) == 1
            changed = {**body, 'inputs': {'facility_id': 9}}
            assert web.post(endpoint, json=changed).status_code == 409
            release.set()
            for _ in range(30):
                result = web.get('/target-lookups/registered-work-days').json()
                if result['state'] == 'completed':
                    break
            assert result['state'] == 'completed'
            assert result['result']['selection_id'] == 'exact-registered-selection'
            assert len(calls) == 1
    finally:
        release.set()
