"""Local dispatcher boundaries, without PDF intake or OCR acceptance claims."""
import json
import sqlite3
import time

import pytest

from src.services import agent_trial_transport as local


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


def test_foreign_generation_or_restarted_credentials_never_execute(local_root, monkeypatch):
    saved = local.contract("component-delivery", 1, "workflow")
    with pytest.raises(ValueError):
        local.validate(saved, "component-delivery", 2, "workflow")
    monkeypatch.setenv("SAWA_TRIAL_TOKEN", "another-isolated-token-"*3)
    with pytest.raises(ValueError):
        local.submit(saved, "component-delivery", 1, "workflow", lambda: pytest.fail("foreign callback"))


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
