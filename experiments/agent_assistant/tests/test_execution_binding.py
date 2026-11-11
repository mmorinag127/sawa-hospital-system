import json
from types import SimpleNamespace

import pytest

from experiments.agent_assistant import execution_binding
from experiments.agent_assistant.store import Conflict, Store


def test_binding_pins_prompt_tools_source_and_provider_without_secrets(tmp_path, monkeypatch):
    store = Store(tmp_path/'state')
    store.set_budget(1000)
    run, _ = store.create('operator', 'binding-request', 'test', ['hospital'], 100, provider='gemini')
    manifest = tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'source_revision': 'a'*40, 'files': {'api.py': 'b'*64}}))
    tools = SimpleNamespace(sandbox=SimpleNamespace(configs={'hospital': {'source_manifest': str(manifest)}}))
    definitions = [{'name': 'read', 'input_schema': {}}]
    monkeypatch.setattr(execution_binding, 'model_definitions', lambda *_: definitions)
    monkeypatch.setenv('GEMINI_AGENT_MODEL', 'gemini-test')
    monkeypatch.setenv('GEMINI_API_KEY', 'never-record-this-secret')
    assert execution_binding.record(store, tools, run, 'system') is None
    saved = execution_binding.record(store, tools, run, 'system', starting=True)
    assert execution_binding.record(store, tools, run, 'system') == saved
    assert 'never-record-this-secret' not in json.dumps(saved)
    assert saved['native_sources']['hospital']['source_revision'] == 'a'*40
    assert len(store.events(run['id'], kinds=('execution_binding',))) == 1
    with pytest.raises(Conflict):
        execution_binding.record(store, tools, run, 'changed-system')
    definitions.append({'name': 'write', 'input_schema': {}})
    with pytest.raises(Conflict):
        execution_binding.record(store, tools, run, 'system')
    definitions.pop()
    manifest.write_text(json.dumps({'source_revision': 'c'*40, 'files': {'api.py': 'b'*64}}))
    with pytest.raises(Conflict):
        execution_binding.record(store, tools, run, 'system')
    assert store.latest_event(run['id'], 'execution_binding')['payload'] == saved


@pytest.mark.parametrize('failure', [None, 'generation', 'old_hash', 'new_hash', 'mutation', 'provider', 'tools', 'stopped'])
def test_owner_native_upgrade_keeps_history_and_refuses_unrelated_drift(tmp_path, monkeypatch, failure):
    store = Store(tmp_path/'state')
    store.set_budget(1000)
    run, _ = store.create('operator', 'upgrade-request', 'test', ['hospital'], 100, provider='gemini')
    manifest = tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'source_revision': 'a'*40, 'files': {'api.py': 'b'*64}}))
    tools = SimpleNamespace(sandbox=SimpleNamespace(configs={'hospital': {'source_manifest': str(manifest)}}))
    definitions = [{'name': 'read', 'input_schema': {}}]
    monkeypatch.setattr(execution_binding, 'model_definitions', lambda *_: definitions)
    monkeypatch.setenv('GEMINI_AGENT_MODEL', 'gemini-test')
    original = execution_binding.record(store, tools, run, 'system', starting=True)
    store.update(run['id'], status='reconciliation_required')
    manifest.write_text(json.dumps({'source_revision': 'c'*40, 'files': {'api.py': 'd'*64}}))
    current = store.get(run['id'])
    plan = execution_binding.native_upgrade(store, tools, current, 'system')
    args = {key: plan[key] for key in ('previous_sha256', 'current_sha256')}
    args['generation'] = current['generation']
    if failure == 'generation': args['generation'] += 1
    if failure == 'old_hash': args['previous_sha256'] = '0'*64
    if failure == 'new_hash': args['current_sha256'] = '0'*64
    if failure == 'mutation': store.command(run['id'], 'unknown-write', {'name': 'hospital_start_fax', 'arguments': {}})
    if failure == 'provider': monkeypatch.setenv('GEMINI_AGENT_MODEL', 'different-model')
    if failure == 'tools': definitions.append({'name': 'new-write', 'input_schema': {}})
    if failure == 'stopped': store.update(run['id'], status='stopped')
    if failure:
        with pytest.raises(Conflict):
            execution_binding.approve_native_upgrade(store, tools, store.get(run['id']), 'system', **args)
        assert store.latest_event(run['id'], 'execution_binding')['payload'] == original
    else:
        store.command(run['id'], 'read-wait', {'name': 'wait_for_fax_work', 'arguments': {}})
        execution_binding.approve_native_upgrade(store, tools, current, 'system', **args)
        bindings = store.events(run['id'], kinds=('execution_binding',))
        assert len(bindings) == 2
        assert bindings[0]['payload'] == original
        assert execution_binding.record(store, tools, current, 'system') == bindings[-1]['payload']
        assert store.get(run['id'])['cap'] == 100
        assert store.get(run['id'])['generation'] == current['generation']
        assert store.latest_event(run['id'], 'native_runtime_upgrade')['payload']['previous_sha256'] == args['previous_sha256']
        with pytest.raises(Conflict):
            execution_binding.approve_native_upgrade(store, tools, current, 'system', **args)
