import ast
from collections import Counter
from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from src.api import orders as api


BASE = '737e40f'


def baseline_functions():
    root = Path(__file__).resolve().parents[3]
    source = subprocess.check_output(
        ['git', 'show', f'{BASE}:backend/src/api/orders.py'], cwd=root, text=True,
    )
    names = {'list_orders', '_apply_cached_status_override', '_attach_order_workflow_context'}
    nodes = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name in names]
    for node in nodes:
        node.decorator_list = []
    namespace = dict(vars(api))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), BASE, 'exec'), namespace)
    return namespace


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
    monkeypatch.setattr(api, 'get_ocr_jobs', lambda ids: {
        key: {'id': key, 'status': 'running', 'metrics': {}} for key in ids})
    monkeypatch.setattr(api, '_attach_order_review_summary', lambda order, **kw: order.update(review='unchanged'))
    old = baseline_functions()
    kwargs = {'include_ocr': not lightweight, 'include_candidate_summary': True, 'limit': 50}
    expected = old['list_orders'](**kwargs)
    before = dict(reads)
    reads.clear()
    assert api.list_orders(**kwargs) == expected
    assert reads['cache_single'] == reads['workflow_single'] == 0
    assert reads['cache_bulk'] == reads['workflow_bulk'] == 1
    if not lightweight:
        assert before.get('cache_single', 0) == before.get('workflow_single', 0) == count


def test_workflow_read_failure_is_not_an_empty_success(monkeypatch):
    monkeypatch.setattr(api.order_service, 'list_orders_page', lambda **kw: {'orders': [{'id': 'O1'}]})
    monkeypatch.setattr(api.order_service, '_load_order_ocr_cache_map', lambda ids: {})
    def fail(ids):
        raise RuntimeError('database unavailable')
    monkeypatch.setattr(api.workflow_state_service, 'list_workflow_states', fail)
    with pytest.raises(RuntimeError, match='database unavailable'):
        api.list_orders(include_ocr=True, limit=50)
