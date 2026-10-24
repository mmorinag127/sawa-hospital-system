from collections import Counter
from copy import deepcopy

import pytest

from src.api import orders as api


@pytest.mark.parametrize('count', [0, 1, 50])
@pytest.mark.parametrize('lightweight', [False, True])
def test_same_page_without_per_order_context_reads(monkeypatch, count, lightweight):
    reads = Counter()
    rows = [{'id': f'O{i}', 'ocr_job_id': f'OCR-O{i}', 'facility': 'FAC00001',
             'week': '2026-10', 'week_value': '2026-10@2026-10-04:2026-10-10',
             'status': 'ready', 'received_at': '2026-10-20T00:00:00'} for i in range(count)]
    cache = {row['id']: {'status': 'done', 'pages': [{}]} for row in rows}
    workflows = {row['id']: {'state': 'review', 'candidate_resolution': {'blocked': True},
                            'critical_decisions': [], 'apply_gate': {'ready': False}} for row in rows[::2]}
    def bulk_cache(ids):
        reads['cache_bulk'] += 1
        return deepcopy(cache)
    def one_cache(order_id):
        reads['cache_single'] += 1
        return deepcopy(cache.get(order_id))
    def bulk_workflow(ids):
        reads['workflow_bulk'] += 1
        return deepcopy(workflows)
    def one_workflow(order_id, **kw):
        reads['workflow_single'] += 1
        return deepcopy(workflows.get(order_id))
    monkeypatch.setattr(api.order_service, 'list_orders_page', lambda **kw: {
        'orders': deepcopy(rows), 'has_more': True, 'next_cursor': {'id': 'cursor'}})
    monkeypatch.setattr(api.order_service, '_load_order_ocr_cache_map', bulk_cache)
    monkeypatch.setattr(api.order_service, 'get_cached_ocr_payload', one_cache)
    monkeypatch.setattr(api.order_service, 'get_order_workflow_state', one_workflow)
    monkeypatch.setattr(api.workflow_state_service, 'list_workflow_states', bulk_workflow)
    def bulk_jobs(ids):
        reads['jobs_bulk'] += 1
        return {row['id']: {'job_id': row['ocr_job_id'], 'error': None,
                'job': {'id': row['ocr_job_id'], 'status': 'running', 'metrics': {}}}
                for row in rows if row['id'] in ids}
    monkeypatch.setattr(api, 'get_recorded_order_job_bindings', bulk_jobs)
    monkeypatch.setattr(api, '_attach_order_review_summary', lambda order, **kw: order.update(review='unchanged'))
    kwargs = {'include_ocr': not lightweight, 'include_candidate_summary': True, 'limit': 50}
    actual = api.list_orders(**kwargs)
    assert actual['has_more'] is True and actual['next_cursor'] == {'id': 'cursor'}
    assert len(actual['orders']) == count
    for original, projected in zip(rows, actual['orders'], strict=True):
        assert {key: projected[key] for key in original} == original
        assert projected['ocr_status'] == 'running'
        assert projected['ocr_job_binding_error'] is None
        if not lightweight:
            assert projected['review'] == 'unchanged'
    assert all('ocr_status' not in row for row in rows)
    assert reads['cache_single'] == reads['workflow_single'] == 0
    assert reads['cache_bulk'] == reads['workflow_bulk'] == reads['jobs_bulk'] == 1


def test_workflow_read_failure_is_not_an_empty_success(monkeypatch):
    monkeypatch.setattr(api.order_service, 'list_orders_page', lambda **kw: {'orders': [{'id': 'O1'}]})
    monkeypatch.setattr(api.order_service, '_load_order_ocr_cache_map', lambda ids: {})
    def fail(ids):
        raise RuntimeError('database unavailable')
    monkeypatch.setattr(api.workflow_state_service, 'list_workflow_states', fail)
    with pytest.raises(RuntimeError, match='database unavailable'):
        api.list_orders(include_ocr=True, limit=50)
