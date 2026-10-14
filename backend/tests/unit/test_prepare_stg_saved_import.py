"""Transport unit checks; these do not claim live PostgreSQL or staging acceptance."""

import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_stg_saved_import as transport


@pytest.fixture
def inputs(tmp_path):
    args = SimpleNamespace(output=tmp_path / "output.json", source_sha="a" * 40)
    for name, value in (("initial", {"facilities": []}), ("policy", {}), ("orders", ["ORD1"])):
        path = tmp_path / (name + ".json")
        raw = json.dumps(value).encode()
        path.write_bytes(raw)
        setattr(args, name, path)
        setattr(args, name + "_sha256", hashlib.sha256(raw).hexdigest())
    return args


def replace_input(args, name, value):
    raw = json.dumps(value).encode()
    getattr(args, name).write_bytes(raw)
    setattr(args, name + "_sha256", hashlib.sha256(raw).hexdigest())


def test_exact_checked_inputs(inputs):
    assert transport.checked_inputs(inputs) == ({"facilities": []}, {}, ["ORD1"])
    assert not inputs.output.exists()


@pytest.mark.parametrize("name", ["initial", "policy", "orders"])
def test_changed_input_stops(inputs, name):
    path = getattr(inputs, name)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="input_hash_mismatch"):
        transport.checked_inputs(inputs)


@pytest.mark.parametrize("value", [[], ["ORD1", "ORD1"], [None], [""], [" ORD1"], {}, [1]])
def test_invalid_selection_stops(inputs, value):
    replace_input(inputs, "orders", value)
    with pytest.raises(ValueError, match="unique_explicit_order_ids_required"):
        transport.checked_inputs(inputs)


def test_existing_output_is_untouched(inputs):
    inputs.output.write_bytes(b"original")
    with pytest.raises(ValueError, match="output_new"):
        transport.checked_inputs(inputs)
    assert inputs.output.read_bytes() == b"original"


def test_input_cannot_be_output(inputs):
    original = inputs.initial.read_bytes()
    inputs.output = inputs.initial
    with pytest.raises(ValueError, match="paths_must_be_distinct"):
        transport.checked_inputs(inputs)
    assert inputs.initial.read_bytes() == original


def test_local_context_stops_before_cloud_access(inputs, monkeypatch):
    import run_stg_menu_master_migration as migration
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    cloud = MagicMock(side_effect=AssertionError("cloud must not be called"))
    monkeypatch.setattr(migration, "_load_service_db_config", cloud)
    with pytest.raises(Exception, match="staging Actions"):
        transport.run(inputs)
    cloud.assert_not_called()


@pytest.mark.parametrize("failure", [None, "head", "capture", "inventory"])
def test_capture_read_only_order_and_rollback(monkeypatch, failure):
    import sqlalchemy.orm
    import staging_db_target
    from src.maintenance import formal_migration_schema
    from src.services import initial_saved_source_service, initial_saved_import_service

    connection = MagicMock()
    connection.in_transaction.return_value = False
    connection.exec_driver_sql.return_value.scalar_one.return_value = "on"
    transaction = connection.begin.return_value
    transaction.is_active = True
    events = []
    connection.exec_driver_sql.side_effect = lambda sql: (
        events.append(sql) or SimpleNamespace(scalar_one=lambda: "on"))
    monkeypatch.setattr(staging_db_target, "verify_connection_target", lambda conn: events.append("target"))

    def head(conn):
        events.append("head")
        if failure == "head":
            raise ValueError("head mismatch")

    def capture(session, order):
        events.append("capture:" + order)
        if failure == "capture":
            raise ValueError("saved source mismatch")
        return {"selected": order}

    monkeypatch.setattr(formal_migration_schema, "require_formal_head", head)
    monkeypatch.setattr(initial_saved_source_service, "capture_saved_source", capture)
    prepare = MagicMock(return_value={"preserved": True})
    monkeypatch.setattr(initial_saved_import_service, "prepare_import_master", prepare)
    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value.execute.return_value.scalars.return_value.all.return_value = (
        ["ORD1", "new-saved-order"] if failure == "inventory" else ["ORD1"])
    monkeypatch.setattr(sqlalchemy.orm, "Session", session_factory)
    if failure:
        with pytest.raises(ValueError, match="mismatch|inventory_changed"):
            transport.capture(connection, {}, {}, ["ORD1"])
        prepare.assert_not_called()
    else:
        assert transport.capture(connection, {}, {}, ["ORD1"]) == {"preserved": True}
        prepare.assert_called_once_with({}, [{"selected": "ORD1"}], {})
        session_factory.assert_called_once_with(bind=connection, autoflush=False, join_transaction_mode="rollback_only")
    assert events[:5] == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
        "SET LOCAL statement_timeout = '60s'", "SHOW transaction_read_only", "target", "head",
    ]
    transaction.rollback.assert_called_once()
    transaction.commit.assert_not_called()
    connection.commit.assert_not_called()


def test_existing_transaction_is_not_adopted():
    connection = MagicMock()
    connection.in_transaction.return_value = True
    with pytest.raises(RuntimeError, match="owned_transaction_required"):
        transport.capture(connection, {}, {}, ["ORD1"])
    connection.begin.assert_not_called()
    connection.rollback.assert_not_called()


@pytest.fixture
def staged_run(inputs, monkeypatch):
    from contextlib import nullcontext
    import run_stg_menu_master_migration as migration
    import sqlalchemy

    for key, value in {
        "GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "123",
        "GITHUB_REF": "refs/heads/develop", "GITHUB_REF_NAME": "develop",
        "PROJECT_ID": "sawahospitalsystem", "REGION": "asia-northeast2",
        "WEB_SERVICE": "web-stg", "WORKER_SERVICE": "worker-stg", "GITHUB_SHA": inputs.source_sha,
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(transport.subprocess, "check_output", lambda *a, **k: inputs.source_sha + "\n")
    config = SimpleNamespace(instance_connection_name=migration.STAGING_INSTANCE,
                             db_name=migration.STAGING_DATABASE, db_user="orders_app", db_password="test-secret")
    reader = MagicMock(return_value=config)
    monkeypatch.setattr(migration, "_load_service_db_config", reader)
    proxy = MagicMock(side_effect=lambda instance: nullcontext())
    monkeypatch.setattr(migration, "cloud_sql_proxy", proxy)
    engine = MagicMock()
    factory = MagicMock(return_value=engine)
    monkeypatch.setattr(sqlalchemy, "create_engine", factory)
    capture = MagicMock(return_value={"unchanged": [None, 6, 0, "unknown"]})
    monkeypatch.setattr(transport, "capture", capture)
    return SimpleNamespace(config=config, reader=reader, proxy=proxy, factory=factory,
                           engine=engine, capture=capture)


def test_transport_targets_only_staging_and_restores_environment(inputs, staged_run, monkeypatch, capsys):
    monkeypatch.setenv("DB_URI", "sqlite:///:memory:")
    transport.run(inputs)
    assert json.loads(inputs.output.read_text()) == {"unchanged": [None, 6, 0, "unknown"]}
    assert transport.os.environ["DB_URI"] == "sqlite:///:memory:"
    assert [call.args[2] for call in staged_run.reader.call_args_list] == ["web-stg", "worker-stg"]
    url = staged_run.factory.call_args.args[0]
    assert url.host == "127.0.0.1" and url.database == "orders" and url.username == "orders_app"
    assert url.query["options"] == "-c default_transaction_read_only=on"
    staged_run.engine.dispose.assert_called_once()
    report = capsys.readouterr().out
    assert "test-secret" not in report and "unknown" not in report
    assert json.loads(report)["orders"] == 1


def test_wrong_actions_source_stops_before_cloud(inputs, staged_run, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    with pytest.raises(ValueError, match="source_sha_mismatch"):
        transport.run(inputs)
    staged_run.reader.assert_not_called()
    staged_run.proxy.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("instance_connection_name", "sawahospitalsystem:asia-northeast2:orders-prod"),
    ("db_name", "wrong"), ("db_user", "wrong"),
])
def test_wrong_worker_target_stops_before_connection(inputs, staged_run, field, value):
    from copy import copy
    wrong = copy(staged_run.config)
    setattr(wrong, field, value)
    staged_run.reader.side_effect = [staged_run.config, wrong]
    with pytest.raises(ValueError, match="staging_target_required"):
        transport.run(inputs)
    staged_run.proxy.assert_not_called()
    staged_run.factory.assert_not_called()
    assert not inputs.output.exists()


def test_failed_capture_creates_no_artifact_and_releases_engine(inputs, staged_run, monkeypatch):
    monkeypatch.delenv("DB_URI", raising=False)
    staged_run.capture.side_effect = ValueError("invalid saved selection")
    with pytest.raises(ValueError, match="invalid saved selection"):
        transport.run(inputs)
    assert "DB_URI" not in transport.os.environ
    assert not inputs.output.exists()
    staged_run.engine.dispose.assert_called_once()


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'])
def test_ambiguous_json_stops_even_with_matching_hash(inputs, raw):
    inputs.policy.write_bytes(raw)
    inputs.policy_sha256 = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        transport.checked_inputs(inputs)


def test_bootstrap_workflow_is_develop_owner_only_and_does_not_deploy_or_overwrite():
    import yaml
    workflow = yaml.load((ROOT / ".github/workflows/bootstrap-stg-canonical.yml").read_text(), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    job = workflow["jobs"]["initialize"]
    assert job["if"] == "github.actor == github.repository_owner && github.ref == 'refs/heads/develop'"
    assert workflow["concurrency"]["group"] == "deploy-stg"
    runs = [step["run"] for step in job["steps"] if "run" in step]
    assert "sha256sum --check" in runs[1]
    assert "scripts/run_stg_schema_adoption.py" in runs[2]
    assert "scripts/prepare_stg_saved_import.py" in runs[3]
    assert "--if-generation-match=0" in runs[4]
    assert "cmp " in runs[4]
    assert "sawahospitalsystem-stg-templates" in runs[0]
    assert all("gcloud run deploy" not in run and "gcloud run services update" not in run for run in runs)
    uploads = [step for step in job["steps"] if step.get("uses") == "actions/upload-artifact@v4"]
    assert [step["with"]["path"] for step in uploads] == ["tmp/canonical-bootstrap/receipt.json"]
