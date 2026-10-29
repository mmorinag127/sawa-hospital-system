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
