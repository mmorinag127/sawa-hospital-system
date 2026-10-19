from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from src.api import orders as api
from src.main import app


@pytest.mark.parametrize('runtime', [True, False])
def test_monitor_keeps_visible_fields_pagination_and_full_runtime_path(monkeypatch, runtime):
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
    monkeypatch.setattr(api, 'get_ocr_jobs', lambda ids: {x: {'status': 'done'} for x in ids})
    def status(order, job):
        calls.append(('status', order['id']))
        order['ocr_status'] = order['status']
    def cached(order, order_id, job):
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
    monkeypatch.setattr(api, '_attach_order_workflow_context', summary)
    client = TestClient(app)
    params = {'include_ocr': 'true', 'include_runtime': str(runtime).lower(), 'limit': 5}
    full = client.get('/orders', params=params)
    assert full.status_code == 200
    before_calls = list(calls)
    calls.clear()
    compact = client.get('/orders', params={**params, 'response_view': 'ocr_monitor'})
    assert compact.status_code == 200
    assert calls == before_calls
    if runtime:
        assert {name for name, _ in calls} == {'status', 'cached', 'stale', 'summary'}
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
