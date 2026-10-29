"""Synthetic metadata paging; not a PDF, OCR or provider acceptance test."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from experiments.agent_assistant.app import create_app
from experiments.agent_assistant.fax_workflow import FaxWorkflow
from experiments.agent_assistant.store import Store


def seed(store, ident, *, artifacts=True):
    paths = []
    if artifacts:
        directory = Path(store.root if hasattr(store, 'root') else store.path.parent)/'runs'/ident/'artifacts'
        directory.mkdir(parents=True, exist_ok=True)
        for index in range(103):
            path = directory/f'output-{index}.txt'
            path.write_bytes(b'synthetic metadata fixture')
            paths.append(path)
    with store.tx() as db:
        db.execute('INSERT INTO fax_batches VALUES(?)', (ident,))
        for index in range(12):
            snapshot = {'name': f'synthetic-{index}.pdf', 'order_id': f'order-{index}', 'facility': 'synthetic', 'week': '2026-10-19'}
            review = {'id': f'review-{index}', 'summary': 'synthetic review',
                      'sheet': {'rows': [['001', 0, '', None]], 'unknown': {'preserved': True}}}
            db.execute('INSERT INTO fax_items(run_id,fax_id,snapshot,state,review) VALUES(?,?,?,?,?)',
                (ident, str(index), json.dumps(snapshot), 'awaiting_approval' if index in (7, 11) else 'processing', json.dumps(review)))
        for index, path in enumerate(paths):
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?)',
                (f'artifact-{index}', ident, str(path), path.name, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size))


def test_fax_pages_preserve_every_item_review_and_global_waiting_count(tmp_path):
    store = Store(tmp_path)
    store.set_budget(1000)
    run, _ = store.create('operator', 'resource-paging', 'synthetic', ['hospital'], 100)
    seed(store, run['id'])
    workflow = FaxWorkflow(SimpleNamespace(store=store))
    first = workflow.page(run['id'])
    second = workflow.page(run['id'], after=first['next_after'])
    third = workflow.page(run['id'], after=second['next_after'])
    assert [len(page['items']) for page in (first, second, third)] == [5, 5, 2]
    assert first['summary'] == {'total': 12, 'states': {'processing': 10, 'awaiting_approval': 2}}
    assert first['items'] + second['items'] + third['items'] == workflow.items(run['id'])
    assert first['items'][0]['review']['sheet'] == {'rows': [['001', 0, '', None]], 'unknown': {'preserved': True}}
    assert [item['fax_id'] for item in workflow.page(run['id'], state='awaiting_approval')['items']] == ['7', '11']
    assert workflow.for_order(run['id'], 'order-11')['fax_id'] == '11'
    assert workflow.item(run['id'], '11') == third['items'][-1]
    assert workflow.item('other-run', '11') is None
    assert workflow.labels(run['id'], [{'kind': 'fax_review', 'payload': {'fax_id': '11'}}]) == {'11': 'synthetic-11.pdf'}


def test_http_detail_limits_resources_and_keeps_cursor_owner_bound(tmp_path):
    app = create_app({}, tmp_path)
    store = app.state.store
    store.set_budget(1000)
    run, _ = store.create('local-trial-operator', 'resource-http', 'synthetic', ['hospital'], 100,
        task_id='hospital_pending_faxes')
    seed(store, run['id'])
    store.update(run['id'], status='stopped')
    with TestClient(app) as client:
        client.get('/')
        first = client.get(f"/runs/{run['id']}").json()
        assert len(first['fax_items']) == 5 and len(first['artifacts']) == 50
        second = client.get(f"/runs/{run['id']}", params={'faxes_after': first['fax_page']['next_after'],
            'artifacts_after': first['artifact_page']['next_after']}).json()
        third = client.get(f"/runs/{run['id']}", params={'artifacts_after': second['artifact_page']['next_after']}).json()
        assert len(second['fax_items']) == 5 and len(second['artifacts']) == 50 and len(third['artifacts']) == 3
        assert third['artifact_page']['next_after'] is None
        assert len({item['id'] for page in (first, second, third) for item in page['artifacts']}) == 103
        assert first['fax_page']['summary']['states']['awaiting_approval'] == 2
        assert client.get(f"/runs/{run['id']}", params={'fax_state': 'unknown'}).status_code == 422
        assert client.get(f"/runs/{run['id']}", params={'faxes_after': -1}).status_code == 422
        assert client.get(f"/runs/{run['id']}/artifacts/unrelated-artifact").status_code == 404
