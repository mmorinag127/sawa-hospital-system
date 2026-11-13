"""Local dispatcher boundaries, without PDF intake or OCR acceptance claims."""
import json
import hashlib
import sqlite3
import time

import pytest

from src.services import agent_trial_transport as local


def test_owner_display_names_are_order_and_saved_sheet_bound(local_root, monkeypatch):
    from src.services.output_builder import _bind_label_area_names
    contexts = {key: {'order_id':key, 'saved_sheet_id':'S'+key, 'sheet_sha256':'unchanged'} for key in ('OA','OB')}
    monkeypatch.setattr(local, '_area_name_context', lambda key: contexts[key])
    approved = {'saved_sheet_id':'SOA','names':{'2F':'Flower','3F':'Moon'},'reason':'Explicit owner approval for OA only'}
    assert local.trial_area_names('OA', approved) == approved['names']
    assert local.trial_area_names('OA', approved) == approved['names']
    assert local.trial_area_names('OB') == {}
    bags = [{'order_id':'OA','area_id':'2F','quantity':7}, {'order_id':'OA','area_id':'3F','quantity':3}]
    assert _bind_label_area_names(bags, {'areas':[]}) == [
        {**bags[0], 'area_name':'Flower'}, {**bags[1], 'area_name':'Moon'}]
    assert 'area_name' not in bags[0]
    with pytest.raises(ValueError, match='label_area_identity_unresolved'):
        _bind_label_area_names([{**bags[0], 'order_id':'OB'}], {'areas':[]})
    with pytest.raises(ValueError, match='already_exists'):
        local.trial_area_names('OA', {**approved, 'names':{'2F':'Different'}})
    contexts['OA']['sheet_sha256'] = 'edited-after-approval'
    with pytest.raises(ValueError, match='saved_sheet_changed'):
        local.trial_area_names('OA')
    monkeypatch.delenv('SAWA_AGENT_TRIAL_TRANSPORT')
    with pytest.raises(ValueError, match='label_area_identity_unresolved'):
        _bind_label_area_names(bags, {'areas':[]})
    with pytest.raises(ValueError, match='not_enabled'):
        local.trial_area_names('OA', approved)


@pytest.mark.parametrize('patch', [
    {'saved_sheet_id':'another-sheet'}, {'names':{}}, {'names':{'2F':''}},
    {'names':{' 2F':'Flower'}}, {'reason':''}, {'unexpected':True}])
def test_owner_display_invalid_registration_is_not_persisted(local_root, monkeypatch, patch):
    monkeypatch.setattr(local, '_area_name_context', lambda key: {'saved_sheet_id':'S1'})
    with pytest.raises(ValueError):
        local.trial_area_names('OA', {'saved_sheet_id':'S1','names':{'2F':'Flower'},'reason':'Owner approval', **patch})
    assert not (local_root/'owner-area-names'/'OA.json').exists()


def test_area_registration_endpoint_is_absent_outside_isolated_runtime(monkeypatch):
    from fastapi import HTTPException
    from src.api.orders import register_trial_area_names
    monkeypatch.delenv('SAWA_AGENT_TRIAL_TRANSPORT', raising=False)
    with pytest.raises(HTTPException) as caught:
        register_trial_area_names('OA', {'saved_sheet_id':'S1','names':{'2F':'Flower'},'reason':'Not allowed here'})
    assert caught.value.status_code == 404


@pytest.fixture
def local_root(tmp_path, monkeypatch):
    root = tmp_path / "request" / "data" / "hospital"
    root.mkdir(parents=True)
    (root.parent.parent / "before" / "hospital").mkdir(parents=True)
    (root / "trial-manifest.json").write_text('{"purpose":"sawa-agent-isolated-trial"}')
    monkeypatch.setenv("SAWA_AGENT_TRIAL_TRANSPORT", "request_local")
    monkeypatch.setenv("SAWA_TRIAL_DATA", str(root))
    monkeypatch.setenv("DB_URI", "sqlite:///"+str(root / "database.sqlite3"))
    monkeypatch.setenv("SAWA_TRIAL_TOKEN", "local-component-token-"*3)
    with sqlite3.connect(root / "database.sqlite3") as db:
        db.execute("CREATE TABLE component(value INTEGER)")
        db.execute("INSERT INTO component VALUES(7)")
    return root


def test_local_delivery_executes_once_and_records_callback_result(local_root):
    saved = local.contract("component-delivery", 1, "ingest")
    def callback():
        with sqlite3.connect(local_root / "database.sqlite3") as db:
            db.execute("UPDATE component SET value=value+1")
        return {"component_returned": True}
    local.submit(saved, "component-delivery", 1, "ingest", callback)
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        files = [path for path in (local_root / "local-deliveries").iterdir() if not path.name.startswith(".")]
        result = json.loads(files[0].read_text())
        if result["state"] != "accepted":
            break
        time.sleep(.01)
    assert result["state"] == "returned"
    local.submit(saved, "component-delivery", 1, "ingest", callback)
    with sqlite3.connect(local_root / "database.sqlite3") as db:
        assert db.execute("SELECT value FROM component").fetchone()[0] == 8


def test_foreign_generation_is_refused_but_rotating_http_token_keeps_delivery_identity(local_root, monkeypatch):
    saved = local.contract("component-delivery", 1, "workflow")
    with pytest.raises(ValueError):
        local.validate(saved, "component-delivery", 2, "workflow")
    monkeypatch.setenv("SAWA_TRIAL_TOKEN", "another-isolated-token-"*3)
    assert local.contract('component-delivery', 1, 'workflow') == saved
    forged = {**saved, 'credential_digest': '0' * 64}
    with pytest.raises(ValueError):
        local.submit(forged, 'component-delivery', 1, 'workflow', lambda: pytest.fail('foreign callback'))


def test_six_deliveries_are_fifo_serial_and_keep_context(local_root):
    from contextvars import ContextVar
    import threading
    current = ContextVar('component_delivery_context')
    release = threading.Event()
    entered = threading.Event()
    observed = []
    active = 0
    peak = 0
    def execute(number):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if number == 0:
            entered.set()
            assert release.wait(5)
        observed.append((number, current.get()))
        active -= 1
        return {'number': number}
    for number in range(6):
        current.set(number)
        identity = 'serial-' + str(number)
        local.submit(local.contract(identity, 1, 'ingest'), identity, 1, 'ingest', lambda n=number: execute(n))
    assert entered.wait(5)
    assert not observed
    duplicate = local.contract('serial-5', 1, 'ingest')
    assert local.submit(duplicate, 'serial-5', 1, 'ingest', lambda: pytest.fail('duplicate'))['action'] == 'already_accepted'
    release.set()
    local._QUEUE.join()
    assert peak == 1
    assert observed == [(number, number) for number in range(6)]


def test_serial_worker_allows_successor_without_deadlocking(local_root):
    calls = []
    def parent():
        calls.append('parent')
        local.submit(local.contract('child', 1, 'workflow'), 'child', 1, 'workflow', lambda: calls.append('child'))
    local.submit(local.contract('parent', 1, 'ingest'), 'parent', 1, 'ingest', parent)
    local._QUEUE.join()
    assert calls == ['parent', 'child']


def test_restart_requeues_exact_worker_contract_not_http_intake(local_root, monkeypatch):
    original = local._enqueue
    monkeypatch.setattr(local, '_enqueue', lambda *_: None)
    saved = local.contract('saved-worker', 1, 'ingest')
    local.submit(saved, 'saved-worker', 1, 'ingest', lambda: pytest.fail('old worker'))
    # Legacy checkpoint upgrade must preserve the already captured transport ID.
    (local_root / 'local-deliveries/.identity').unlink()
    monkeypatch.setenv('SAWA_TRIAL_TOKEN', 'rotated-current-http-token-' * 3)
    assert local.configuration()['credential_digest'] == saved['credential_digest']
    monkeypatch.setattr(local, '_enqueue', original)
    from src.services import ingest_delivery_service
    calls = []
    monkeypatch.setattr(ingest_delivery_service, 'process', lambda identity, generation: calls.append((identity, generation)))
    assert local.resume_deliveries() == {'accepted': 1}
    assert local.resume_deliveries()['accepted'] in (0, 1)
    local._QUEUE.join()
    assert calls == [('saved-worker', 1)]
    assert local.resume_deliveries() == {'accepted': 0}


def test_worker_recovery_validates_whole_batch_before_dispatch(local_root, monkeypatch):
    monkeypatch.setattr(local, '_enqueue', lambda *_: None)
    saved = local.contract('saved-worker', 1, 'ingest')
    local.submit(saved, 'saved-worker', 1, 'ingest', lambda: None)
    bad = local.contract('foreign-worker', 2, 'ingest')
    bad['name'] = '/not-this-copy/tasks/foreign-worker-g2'
    folder = local_root / 'local-deliveries'
    (folder / hashlib.sha256(bad['name'].encode()).hexdigest()).write_text(json.dumps({'state': 'accepted', 'contract': bad}))
    calls = []
    monkeypatch.setattr(local, '_enqueue', lambda *_: calls.append(True))
    with pytest.raises(ValueError, match='saved_contract_differs'):
        local.resume_deliveries()
    assert calls == []


def test_recovery_does_not_retry_unknown_provider_mutation(local_root, monkeypatch):
    monkeypatch.setattr(local, '_enqueue', lambda *_: None)
    saved = local.contract('provider-work', 1, 'auto-llm')
    local.submit(saved, 'provider-work', 1, 'auto-llm', lambda: None)
    with pytest.raises(ValueError, match='provider_response_unknown'):
        local.resume_deliveries()


def test_local_mode_cannot_target_another_database(local_root, monkeypatch):
    monkeypatch.setenv("DB_URI", "sqlite:////another/database.sqlite3")
    with pytest.raises(ValueError):
        local.configuration()


def test_relative_artifact_reference_survives_independent_copy(local_root,monkeypatch,tmp_path):
    import shutil
    from src.services.storage_service import StorageService,load_bytes_from_uri
    marker=local_root/'trial-manifest.json'
    content=json.loads(marker.read_text());content['request_relative_storage']=True
    marker.write_text(json.dumps(content))
    monkeypatch.chdir(local_root)
    uri=StorageService(local_root/'ocr-artifacts').save_bytes('job/raw.json',b'{"literal":"20"}')
    assert uri=='ocr-artifacts/job/raw.json'
    fork=tmp_path/'fork/data/hospital';shutil.copytree(local_root,fork)
    (fork.parent.parent/'before/hospital').mkdir(parents=True)
    monkeypatch.setenv('SAWA_TRIAL_DATA',str(fork));monkeypatch.setenv('DB_URI','sqlite:///'+str(fork/'database.sqlite3'))
    monkeypatch.chdir(fork)
    (local_root/uri).write_bytes(b'source changed after copy')
    assert load_bytes_from_uri(uri)==b'{"literal":"20"}'
    outside=tmp_path/'must-not-be-created'
    with pytest.raises(ValueError,match='outside_copy'):
        StorageService(fork).save_bytes(str(outside),b'forbidden')
    assert not outside.exists()


def test_normal_storage_keeps_absolute_reference(local_root,monkeypatch):
    from src.services.storage_service import StorageService
    monkeypatch.delenv('SAWA_AGENT_TRIAL_TRANSPORT')
    path=StorageService(local_root).save_bytes('ordinary.json',b'unchanged')
    assert path==str(local_root/'ordinary.json')


def test_captured_workbook_is_read_without_production_fallback(local_root):
    from src.services.storage_service import load_bytes_from_uri
    body=b'synthetic workbook'
    (local_root/'assets').mkdir()
    (local_root/'assets/workbook').write_bytes(body)
    uri='gs://source-only/template.xlsx'
    marker={'purpose':'sawa-agent-isolated-trial','initialization':'register-authoritative-pending-faxes-v1',
        'source_assets':{uri:{'path':'assets/workbook','sha256':hashlib.sha256(body).hexdigest(),'generation':'1'}}}
    (local_root/'trial-manifest.json').write_text(json.dumps(marker))
    assert load_bytes_from_uri(uri)==body
    with pytest.raises(ValueError,match='not_captured'):
        load_bytes_from_uri('gs://source-only/not-captured.xlsx')
    (local_root/'assets/workbook').write_bytes(b'changed')
    with pytest.raises(ValueError,match='digest_changed'):
        load_bytes_from_uri(uri)


@pytest.mark.parametrize('kind',['ingest','workflow'])
def test_trial_copy_never_dispatches_a_copied_cloud_contract(local_root,monkeypatch,kind):
    from google.cloud import tasks_v2
    monkeypatch.setattr(tasks_v2,'CloudTasksClient',lambda:pytest.fail('trial must not contact a shared cloud queue'))
    if kind=='ingest':
        from src.services import ingest_delivery_dispatch_service as dispatch
        from src.services import ingest_delivery_service as delivery
        monkeypatch.setattr(delivery,'settle_saved',lambda *_:None)
    else:
        from src.services import workflow_ocr_delivery_dispatch_service as dispatch
        delivery=dispatch.delivery
    monkeypatch.setattr(delivery,'prepare_dispatch',lambda *_:{'task_contract':{'name':'projects/shared/tasks/example','http_request':{}}})
    with pytest.raises(ValueError,match='request_local_saved_contract_required'):
        dispatch.dispatch('copied-delivery',1)
