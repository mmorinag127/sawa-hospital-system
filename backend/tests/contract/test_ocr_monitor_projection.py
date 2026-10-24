from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.api import orders as api
from src.main import app


@pytest.mark.parametrize('runtime', [True, False])
def test_monitor_keeps_visible_fields_pagination_without_unused_review(monkeypatch, runtime):
    fields = ('id', 'facility', 'week', 'status', 'received_at', 'ocr_status', 'ocr_error')
    original = {'orders': [
        {'id': f'O{i}', 'facility': None if i == 0 else 'FAC00001', 'week': '2026-10',
         'status': state, 'received_at': '2026-10-20T00:00:00', 'ocr_job_id': f'J{i}',
         'ocr_error': 'failure' if state == 'failed' else None,
         'large_detail': ['diagnostic'] * 1000}
        for i, state in enumerate(('pending', 'running', 'done', 'failed', 'stalled'))
    ], 'has_more': True, 'next_cursor': {'id': 'O4', 'received_at': '2026-10-20T00:00:00'}}
    calls = []
    monkeypatch.setattr(api.order_service, 'list_orders_page', lambda **kw: deepcopy(original))
    monkeypatch.setattr(api.order_service, '_load_order_ocr_cache_map', lambda ids: {})
    monkeypatch.setattr(api.workflow_state_service, 'list_workflow_states', lambda ids: {})
    monkeypatch.setattr(api, 'get_recorded_order_job_bindings', lambda ids: {
        row['id']: {'job_id': row['ocr_job_id'], 'job': {'id': row['ocr_job_id'], 'status': 'done'}, 'error': None}
        for row in original['orders'] if row['id'] in ids})
    def status(order, job):
        calls.append(('status', order['id']))
        order['ocr_status'] = order['status']
    def cached(order, order_id, job, **kw):
        calls.append(('cached', order_id))
        return job
    def stale(order, job):
        calls.append(('stale', order['id']))
        return job
    def summary(order, **kw):
        calls.append(('summary', order['id']))
        order['review_detail'] = ['review'] * 1000
    monkeypatch.setattr(api, '_apply_job_status_to_order', status)
    monkeypatch.setattr(api, '_apply_cached_status_override', cached)
    monkeypatch.setattr(api, '_apply_stale_ocr_status', stale)
    monkeypatch.setattr(api, '_mark_stale_order_reparse_job', stale)
    monkeypatch.setattr(api, '_attach_order_review_summary', summary)
    monkeypatch.setattr(api, '_apply_order_workflow_context', lambda order, workflow: summary(order))
    client = TestClient(app)
    params = {'include_ocr': 'true', 'include_runtime': str(runtime).lower(), 'limit': 5}
    full = client.get('/orders', params=params)
    assert full.status_code == 200
    before_calls = list(calls)
    calls.clear()
    compact = client.get('/orders', params={**params, 'response_view': 'ocr_monitor'})
    assert compact.status_code == 200
    if runtime:
        assert {name for name, _ in before_calls} == {'status', 'cached', 'stale', 'summary'}
        assert {name for name, _ in calls} == {'status', 'stale'}
    else:
        assert calls == before_calls == []
    expected = {**full.json(), 'orders': [
        {key: row[key] for key in fields if key in row} for row in full.json()['orders']
    ]}
    assert compact.json() == expected
    assert len(compact.content) < len(full.content) / 20
    print(f'monitor response bytes full={len(full.content)} compact={len(compact.content)}')
    assert original['orders'][0].get('review_detail') is None


def test_monitor_empty_page_and_invalid_view(monkeypatch):
    page = {'orders': [], 'has_more': False, 'next_cursor': None}
    monkeypatch.setattr(api.order_service, 'list_orders_page', lambda **kw: deepcopy(page))
    client = TestClient(app)
    assert client.get('/orders?response_view=ocr_monitor').json() == page
    assert client.get('/orders?response_view=unknown').status_code == 422


@pytest.mark.parametrize('state', ['pending', 'running', 'done', 'failed', 'stalled', 'recovering'])
def test_monitor_current_job_values_match_full_list(monkeypatch, state):
    page = {'orders': [
        {'id': 'O1', 'ocr_job_id': 'J1', 'ocr_status': 'old', 'ocr_error': 'old error'},
        {'id': 'O2', 'ocr_job_id': 'missing', 'ocr_status': 'saved'},
        {'id': 'O3', 'ocr_status': 'none'},
    ], 'has_more': False, 'next_cursor': None}
    jobs = {'J1': {'id': 'J1', 'status': state, 'error_message': 'job error',
                   'metrics': {'error': 'metrics error'}, 'updated_at': datetime.utcnow() - timedelta(days=1)}}
    monkeypatch.setattr(api.order_service, 'list_orders_page', lambda **kw: deepcopy(page))
    monkeypatch.setattr(api, 'get_recorded_order_job_bindings', lambda ids: {
        row['id']: {'job_id': row.get('ocr_job_id'), 'job': deepcopy(jobs.get(row.get('ocr_job_id'))), 'error': None}
        for row in page['orders'] if row['id'] in ids})
    monkeypatch.setattr(api.order_service, '_load_order_ocr_cache_map', lambda ids: {})
    monkeypatch.setattr(api.workflow_state_service, 'list_workflow_states', lambda ids: {})
    monkeypatch.setattr(api, '_attach_order_review_summary', lambda *a, **kw: None)
    client = TestClient(app)
    full = client.get('/orders?include_ocr=true')
    assert full.status_code == 200
    expected = api._order_list_response(full.json(), 'ocr_monitor')
    def unrelated(*a, **kw):
        raise AssertionError('monitor must not load unused detailed review sources')
    monkeypatch.setattr(api.order_service, '_load_order_ocr_cache_map', unrelated)
    monkeypatch.setattr(api.workflow_state_service, 'list_workflow_states', unrelated)
    monkeypatch.setattr(api, '_attach_order_review_summary', unrelated)
    compact = client.get('/orders?include_ocr=true&response_view=ocr_monitor')
    assert compact.status_code == 200
    assert compact.json() == expected


def test_monitor_source_failure_stops_and_full_review_still_validates(monkeypatch):
    monkeypatch.setattr(api.order_service, 'list_orders_page', lambda **kw: {
        'orders': [{'id': 'O1', 'ocr_job_id': 'J1'}], 'has_more': False, 'next_cursor': None})
    monkeypatch.setattr(api.order_service, '_load_order_ocr_cache_map', lambda ids: {})
    monkeypatch.setattr(api.workflow_state_service, 'list_workflow_states', lambda ids: {})
    monkeypatch.setattr(api, 'get_recorded_order_job_bindings', lambda ids: {
        'O1': {'job_id': 'J1', 'job': {'id': 'J1', 'status': 'done'}, 'error': None}})
    def failed(*a, **kw):
        raise RuntimeError('source unavailable')
    monkeypatch.setattr(api, '_attach_order_review_summary', failed)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get('/orders?include_ocr=true').status_code == 500
    assert client.get('/orders?include_ocr=true&response_view=ocr_monitor').status_code == 200
    monkeypatch.setattr(api, 'get_recorded_order_job_bindings', failed)
    assert client.get('/orders?include_ocr=true&response_view=ocr_monitor').status_code == 500
    monkeypatch.setattr(api.order_service, 'list_orders_page', failed)
    assert client.get('/orders?include_ocr=false&response_view=ocr_monitor').status_code == 500
    assert client.get('/orders?response_view=ocr_monitor&limit=1001').status_code == 422
