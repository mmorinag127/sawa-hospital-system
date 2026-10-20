"""Gateway fences; native validation is an explicit double in these unit tests."""
import asyncio
import hashlib
from pathlib import Path

import httpx
import pytest

from experiments.agent_assistant.store import Store
from experiments.agent_assistant.tools import Tools


def scenario_setup(tmp_path, failure=None):
    store = Store(tmp_path/'ledger')
    store.set_budget(100)
    run, _ = store.create('operator', 'hospital-content-test', '帳票内容の検査', ['hospital'], 10)
    store.update(run['id'], status='running')
    run = store.get(run['id'])
    class Native:
        entered = None
        release = None
        def path(self, run_id):
            root = tmp_path/run_id
            root.mkdir(exist_ok=True)
            return root
        def local_jobs(self, run): return []
        async def request(self, run, domain, method, path, **kwargs):
            if path == '/outputs/validate-received':
                if self.entered:
                    self.entered.set()
                    await self.release.wait()
                body = kwargs['body']
                if failure == 'native-rejection': return httpx.Response(409, json={'detail':'output_business_content_mismatch'})
                receipt = {'valid':True, **body, 'sha256':hashlib.sha256(kwargs['files']['artifact_file'][1]).hexdigest(),
                    'revision':7, 'confirmed_snapshot_id':'accepted'}
                if failure == 'false-valid': receipt['valid'] = False
                if failure == 'wrong-sha': receipt['sha256'] = 'wrong'
                if failure == 'other-order': receipt['order_id'] = 'foreign'
                return httpx.Response(200, json=receipt)
            return httpx.Response(200, json={'state':'confirmed', 'revision':8 if failure=='stale-order' else 7,
                'confirmed_snapshot_id':'accepted'})
    native = Native()
    tools = Tools(store, native)
    artifacts = []
    for kind in ['labels', 'delivery_notes', 'aggregate']:
        name = 'hospital_export_'+kind
        artifact = tools.artifact(run['id'], b'explicit unit-test bytes', kind)
        artifacts.append(artifact['id'])
        store.command(run['id'], name, {'name':name, 'arguments':{'parameters':{'query':{'order_id':'owned'}}}})
        store.finish_command(run['id'], name, {'ok':True, 'artifact':artifact})
    store.command(run['id'], 'read', {'name':'hospital_get_workflow', 'arguments':{'parameters':{'ids':{'order_id':'owned'}}}})
    store.finish_command(run['id'], 'read', {'ok':True, 'data':{'state':'confirmed', 'revision':7,'confirmed_snapshot_id':'accepted'}})
    raw = {'summary':'帳票完了', 'evidence_commands':['read'], 'artifact_ids':artifacts}
    return store, native, tools, run, raw


@pytest.mark.parametrize('failure', ['native-rejection','false-valid','wrong-sha','other-order','stale-order'])
def test_invalid_native_content_receipt_cannot_complete(tmp_path, failure):
    async def scenario():
        store, _, tools, run, raw = scenario_setup(tmp_path, failure)
        result = await tools.execute(run['id'], 0, 'finish_work', raw, 'finish')
        assert not result.get('ok'), result
        assert store.get(run['id'])['status'] != 'completed'
    asyncio.run(scenario())


def test_all_received_artifacts_are_checked_and_persisted(tmp_path):
    async def scenario():
        store, _, tools, run, raw = scenario_setup(tmp_path)
        result = await tools.execute(run['id'], 0, 'finish_work', raw, 'finish')
        assert result['ok']
        checks = next(event for event in store.events(run['id']) if event['kind']=='hospital_output_validation')
        assert set(checks['payload']['artifacts']) == set(raw['artifact_ids'])
    asyncio.run(scenario())


def test_stop_during_content_validation_wins_over_late_success(tmp_path):
    async def scenario():
        store, native, tools, run, raw = scenario_setup(tmp_path)
        native.entered, native.release = asyncio.Event(), asyncio.Event()
        pending = asyncio.create_task(tools.execute(run['id'], 0, 'finish_work', raw, 'late-finish'))
        await native.entered.wait()
        store.fence(run['id'])
        native.release.set()
        result = await pending
        assert not result.get('ok'), result
        assert store.get(run['id'])['status']=='stopping'
        assert not any(event['kind']=='completion_evidence' for event in store.events(run['id']))
    asyncio.run(scenario())
