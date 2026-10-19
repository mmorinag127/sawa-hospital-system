"""Synthetic boundary tests only; no staging reads or product guard success injection."""
from copy import deepcopy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("reviewed_diagnostic", ROOT / "scripts/diagnose_reviewed_ocr.py")
diag = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diag)


@pytest.fixture
def actions(monkeypatch, tmp_path):
    env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": diag.REPOSITORY,
        "GITHUB_REPOSITORY_OWNER": "mmorinag127", "GITHUB_ACTOR": "mmorinag127",
        "GITHUB_TRIGGERING_ACTOR": "mmorinag127", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/develop", "DIAGNOSTIC_ENVIRONMENT": "staging",
        "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SHA": "a" * 40,
        "RUNNER_TEMP": str(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT", raising=False)
    return env


@pytest.mark.parametrize("key,value", [
    ("GITHUB_ACTIONS", "false"), ("GITHUB_REPOSITORY", "other/repo"),
    ("GITHUB_ACTOR", "other"), ("GITHUB_TRIGGERING_ACTOR", "other"),
    ("GITHUB_REF", "refs/heads/main"), ("GITHUB_EVENT_NAME", "repository_dispatch"),
    ("DIAGNOSTIC_ENVIRONMENT", "production"), ("GITHUB_REPOSITORY_OWNER", "other"),
])
def test_context_stops_before_any_read(actions, monkeypatch, tmp_path, key, value):
    monkeypatch.setenv(key, value)
    read = Mock(side_effect=AssertionError("read forbidden"))
    monkeypatch.setattr(diag, "command", read)
    with pytest.raises(diag.Blocked, match="context_invalid"):
        diag.context(tmp_path)
    read.assert_not_called()


def test_exact_two_sources_and_clean_product(actions, monkeypatch, tmp_path):
    read = Mock(side_effect=[(diag.PRODUCT_SHA + "\n").encode(), b"", (actions["GITHUB_SHA"] + "\n").encode()])
    monkeypatch.setattr(diag, "command", read)
    assert diag.context(tmp_path) == actions["GITHUB_SHA"]
    assert read.call_args_list[0].args[0] == ["git", "-C", str(tmp_path), "rev-parse", "HEAD"]
    read.side_effect = [b"wrong\n"]
    with pytest.raises(diag.Blocked, match="product_source_changed"):
        diag.context(tmp_path)


def row():
    pdf = {"document_id": diag.DOCUMENT, "order_id": diag.ORDER, "sha256": diag.PDF_SHA,
        "storage_uri": diag.PDF_URI, "generation": "1792028285165557",
        "message_id": "synthetic-message", "uploaded_pdf_id": "synthetic-upload"}
    payload = {"hakodate_repair_source_receipt": {"selected_pdf_receipt": pdf},
        "_ocr_execution": {"selected_pdf_receipt": deepcopy(pdf)}, "hakodate_recognition_review": {"count": 10}}
    manifest = {diag.KEY: {"evidence_run_id": diag.EVIDENCE, "artifact_digest": diag.ARTIFACT_SHA}}
    return {"id": diag.EVIDENCE, "order_id": diag.ORDER, "artifact_digest": diag.ARTIFACT_SHA,
        "payload_raw": json.dumps(payload), "manifest_raw": json.dumps(manifest),
        "current_document_id": diag.DOCUMENT, "storage_uri": diag.PDF_URI,
        "source_email_id": pdf["message_id"], "uploaded_pdf_id": pdf["uploaded_pdf_id"],
        "content_sha256": diag.PDF_SHA, "selected_evidence": diag.EVIDENCE,
        "workflow_state": "ocr_selected", "draft_id": None, "workflow_blockers": []}


@pytest.fixture
def synthetic_row(monkeypatch):
    value = row()
    # Only synthetic input pins change in this unit process, never diagnostic runtime constants.
    monkeypatch.setattr(diag, "PAYLOAD_SHA", diag.sha(value["payload_raw"].encode()))
    monkeypatch.setattr(diag, "MANIFEST_SHA", diag.sha(value["manifest_raw"].encode()))
    return value


def test_raw_input_hashes_and_acceptance_preserved(synthetic_row):
    before = deepcopy(synthetic_row)
    payload, manifest, _ = diag.validate_row(synthetic_row)
    assert payload == synthetic_row["payload_raw"].encode()
    assert manifest == synthetic_row["manifest_raw"].encode()
    assert synthetic_row == before
    synthetic_row["manifest_raw"] += " "
    with pytest.raises(diag.Blocked, match="input_hash_changed"):
        diag.validate_row(synthetic_row)


@pytest.mark.parametrize("field,value", [
    ("id", "foreign"), ("order_id", "foreign"), ("artifact_digest", "0" * 64),
    ("current_document_id", "foreign"), ("storage_uri", "foreign"), ("content_sha256", "0" * 64),
    ("source_email_id", "foreign"), ("uploaded_pdf_id", "foreign"),
    ("selected_evidence", "foreign"), ("workflow_state", "draft_ready"),
    ("draft_id", "existing"), ("workflow_blockers", ["stop"]),
])
def test_foreign_or_changed_current_source_stops(synthetic_row, field, value):
    synthetic_row[field] = value
    with pytest.raises(diag.Blocked):
        diag.validate_row(synthetic_row)


@pytest.mark.parametrize("uri", [
    "https://other.invalid/file", "gs://other/file?generation=1", "gs://sawahospitalsystem-stg-raw/artifacts/a",
    "gs://sawahospitalsystem-stg-raw/artifacts/a?generation=1&generation=2",
    "gs://sawahospitalsystem-stg-raw/artifacts/a?generation=1#2",
    "gs://sawahospitalsystem-stg-raw/artifacts/a?generation=0",
    "gs://sawahospitalsystem-stg-raw/artifacts/a?generation=1&other=2",
])
def test_uri_generation_is_explicit_and_closed(uri):
    with pytest.raises((diag.Blocked, ValueError)):
        diag.uri_parts(uri)


def test_fixture_exact_uri_generation_path_size_and_hash(tmp_path):
    data = b"synthetic original"
    uri = diag.PDF_URI
    entry = {"generation": "1792028285165557", "path": "asset-0.bin", "bytes": len(data), "sha256": diag.sha(data)}
    (tmp_path / entry["path"]).write_bytes(data)
    assets = {uri: entry}
    assert diag.fixture_bytes(tmp_path, assets, uri, {"verification": True}) == data
    with pytest.raises(diag.Blocked, match="unknown_uri"):
        diag.fixture_bytes(tmp_path, assets, uri.replace("5557", "5558"), {"verification": True})
    with pytest.raises(diag.Blocked, match="unknown_uri"):
        diag.fixture_bytes(tmp_path, assets, uri, {})
    entry["path"] = "../foreign"
    with pytest.raises(diag.Blocked, match="fixture_path_changed"):
        diag.fixture_bytes(tmp_path, assets, uri, {"verification": True})
    entry["path"] = "asset-0.bin"
    (tmp_path / entry["path"]).write_bytes(data + b"changed")
    with pytest.raises(diag.Blocked, match="artifact_changed"):
        diag.fixture_bytes(tmp_path, assets, uri, {"verification": True})


def test_capture_is_readonly_snapshot_one_parameterized_row(monkeypatch, tmp_path, capsys):
    import psycopg2
    private_password = "SYNTHETIC-PRIVATE-PASSWORD"
    entries = [{"name": key, "value": value} for key, value in
        {"DB_DRIVER": "postgresql", "DB_NAME": "synthetic", "DB_USER": "synthetic"}.items()]
    entries.append({"name": "DB_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "synthetic-password", "key": "1"}}})
    service = {"spec": {"template": {"metadata": {"annotations": {"run.googleapis.com/cloudsql-instances": diag.INSTANCE}},
        "spec": {"containers": [{"env": entries}]}}}}
    reads = []
    def read(args):
        reads.append(args)
        return private_password.encode() if args[1:4] == ["secrets", "versions", "access"] else json.dumps(service).encode()
    monkeypatch.setattr(diag, "command", read)
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [{"transaction_read_only": "on"}, {"transaction_isolation": "repeatable read"}]
    cursor.fetchall.return_value = [row()]
    connection = Mock()
    connection.cursor.return_value = cursor
    connect = Mock(return_value=connection)
    monkeypatch.setattr(psycopg2, "connect", connect)
    process = Mock()
    process.poll.return_value = None
    popen = Mock(return_value=process)
    monkeypatch.setattr(diag.subprocess, "Popen", popen)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: MagicMock())
    assert diag.read_row(Path("synthetic-proxy"), tmp_path)["id"] == diag.EVIDENCE
    connection.set_session.assert_called_once_with(isolation_level="REPEATABLE READ", readonly=True)
    assert "default_transaction_read_only=on" in connect.call_args.kwargs["options"]
    calls = cursor.execute.call_args_list
    assert [call.args[0].split()[0] for call in calls] == ["SHOW", "SHOW", "SELECT"]
    assert calls[-1].args[1] == (diag.ORDER, diag.EVIDENCE)
    assert "FOR UPDATE" not in calls[-1].args[0]
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()
    process.terminate.assert_called_once()
    process.wait.assert_called_once()
    assert all(args[1:4] in (["run", "services", "describe"], ["secrets", "versions", "access"]) for args in reads)
    assert private_password not in capsys.readouterr().out


def test_capture_assets_validates_aliases_and_keeps_exact_uris(monkeypatch, tmp_path):
    pdf = {"storage_uri": diag.PDF_URI, "generation": "1792028285165557", "sha256": diag.sha(b"PDF")}
    data = {}
    def receipt(name, content):
        uri = "gs://sawahospitalsystem-stg-raw/artifacts/" + name + "?generation=123"
        data[uri] = content
        return {"uri": uri, "sha256": diag.sha(content), "size_bytes": len(content)}
    checkpoint = {"checkpoint": receipt("checkpoint", b"checkpoint"), "image": receipt("image", b"image"),
        "source": {"selected_pdf_receipt": pdf}}
    observation = receipt("observation", json.dumps({"checkpoint": checkpoint}).encode())
    image = receipt("overlay", b"image")
    payload = {"hakodate_repair_source_receipt": {"selected_pdf_receipt": pdf},
        "hakodate_canonical_pipeline": {"artifact_packet": {"metadata": receipt("metadata", b"metadata"),
            "outputs": {"preprocess_observation": observation, "overlay": image, "overlay_png": deepcopy(image)}}}}
    data[diag.PDF_URI] = b"PDF"
    calls = []
    def read(args):
        calls.append(args)
        if args[1:4] == ["storage", "objects", "describe"]:
            return b'{"generation":"1792028285165557"}'
        assert args[1:3] == ["storage", "cat"]
        uri, generation = args[3].split("#")
        return data[uri + "?generation=" + generation]
    monkeypatch.setattr(diag, "command", read)
    original = deepcopy(payload)
    assets = diag.capture_assets(payload, tmp_path)
    assert set(assets) == set(data)
    assert len([call for call in calls if call[1:3] == ["storage", "cat"]]) == len(data)
    assert assets[diag.PDF_URI]["latest_generation"] == "1792028285165557"
    assert all((tmp_path / entry["path"]).stat().st_mode & 0o777 == 0o600 for entry in assets.values())
    assert payload == original
    data[image["uri"]] = b"tampered"
    with pytest.raises(diag.Blocked, match="artifact_changed"):
        diag.capture_assets(payload, tmp_path)


def test_offline_blocks_db_socket_and_dns(monkeypatch):
    from sqlalchemy.engine import Engine
    import psycopg2
    targets = [(socket.socket, "connect"), (socket.socket, "connect_ex"), (socket.socket, "sendto"),
        (socket, "create_connection"), (socket, "getaddrinfo"), (socket, "gethostbyname"),
        (socket, "gethostbyname_ex"), (socket, "gethostbyaddr"),
        (Engine, "connect"), (Engine, "raw_connection"), (psycopg2, "connect")]
    if hasattr(socket.socket, "sendmsg"):
        targets.append((socket.socket, "sendmsg"))
    for module, name in targets:
        monkeypatch.setattr(module, name, lambda *a, **k: None)
    diag.block_external()
    for module, name in targets:
        with pytest.raises(diag.Blocked, match="external_io_forbidden"):
            getattr(module, name)(None)


def test_private_cleanup_is_owned_and_finally_even_on_capture_failure(actions, monkeypatch, tmp_path):
    secret = "SYNTHETIC-RAW-PAYLOAD"
    def fail(*args):
        diag.write_private(diag.owned_path() / "payload.json", secret.encode())
        raise RuntimeError(secret)
    monkeypatch.setattr(diag, "read_row", fail)
    with pytest.raises(RuntimeError):
        diag.run(tmp_path, Path("proxy"), tmp_path / "summary")
    assert not diag.owned_path().exists()
    diag.owned_path().mkdir()
    diag.write_json(diag.owned_path() / "owner.json", {"foreign": True})
    with pytest.raises(diag.Blocked, match="cleanup_owner_changed"):
        diag.cleanup(diag.owned_path())
    assert diag.owned_path().exists()


def test_safe_failure_has_no_raw_secret_or_exception_text(actions, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(diag, "context", lambda *_: "a" * 40)
    monkeypatch.setattr(diag, "run", Mock(side_effect=RuntimeError("PRIVATE-DATA")))
    monkeypatch.setattr("sys.argv", ["diagnostic", "--product", str(tmp_path), "--proxy", "proxy",
        "--summary", str(tmp_path / "summary")])
    assert diag.main() == 1
    raw = (tmp_path / "summary/capture-error.json").read_text()
    assert "PRIVATE-DATA" not in raw
    assert json.loads(raw)["error"] == "unexpected_RuntimeError"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("headless_status", [0, 1])
def test_both_offline_processes_run_with_no_credentials_and_private_cleanup(
        actions, synthetic_row, monkeypatch, tmp_path, headless_status):
    synthetic_row.update(status="diagnostic_only", template_version_id="synthetic", producer_version="synthetic",
        source="synthetic", schema_version="synthetic", created_at=datetime(2026, 10, 19))
    monkeypatch.setattr(diag, "read_row", lambda *args: synthetic_row)
    monkeypatch.setattr(diag, "capture_assets", lambda *args: {})
    monkeypatch.setenv("DB_PASSWORD", "PRIVATE")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/private/credential")
    summary = tmp_path / "summary"
    summary.mkdir()
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        build = args[args.index("--build") + 1]
        assert "--offline" in args
        assert "DB_PASSWORD" not in kwargs["env"]
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in kwargs["env"]
        assert kwargs["env"]["DATABASE_URL"] == "sqlite:///:memory:"
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        assert ("PYTHONPATH" in kwargs["env"]) == (build == "gui")
        diag.write_json(summary / (build + ".json"), {"synthetic": True})
        return SimpleNamespace(returncode=headless_status if build == "headless" else 0)
    monkeypatch.setattr(diag.subprocess, "run", run)
    assert diag.run(tmp_path, Path("proxy"), summary) == (headless_status == 0)
    assert len(calls) == 2
    assert not diag.owned_path().exists()
    assert set(path.name for path in summary.iterdir()) == {"headless.json", "gui.json"}


def test_workflow_run_blocks_have_bash_syntax():
    workflow = yaml.load((ROOT / ".github/workflows/diagnose-reviewed-ocr.yml").read_text(), Loader=yaml.BaseLoader)
    for step in workflow["jobs"]["diagnose"]["steps"]:
        if "run" in step:
            result = subprocess.run(["bash", "-n"], input=step["run"], text=True, capture_output=True)
            assert result.returncode == 0, step["name"]


def test_dedicated_workflow_is_manual_owner_staging_nondeploy_and_summary_only():
    text = (ROOT / ".github/workflows/diagnose-reviewed-ocr.yml").read_text()
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    assert set(workflow["jobs"]) == {"diagnose"}
    job = workflow["jobs"]["diagnose"]
    for boundary in ("github.actor == github.repository_owner", "github.triggering_actor == github.repository_owner",
        "refs/heads/develop", "workflow_dispatch", diag.REPOSITORY):
        assert boundary in job["if"]
    assert (job["runs-on"], job["environment"], job["timeout-minutes"]) == ("ubuntu-24.04", "staging", "15")
    assert job["permissions"] == {"contents": "read", "actions": "read", "id-token": "write"}
    checkouts = [step["with"] for step in job["steps"] if step.get("uses") == "actions/checkout@v6"]
    assert checkouts == [{"path": "diagnostic", "persist-credentials": "false"},
        {"ref": diag.PRODUCT_SHA, "path": "product", "persist-credentials": "false"}]
    auth = next(step for step in job["steps"] if step.get("uses") == "google-github-actions/auth@v3")
    assert auth["with"]["service_account"] == "${{ secrets.GCP_DEPLOY_SERVICE_ACCOUNT_STG }}"
    upload = job["steps"][-1]
    assert upload["uses"] == "actions/upload-artifact@v4" and upload["if"] == "always()"
    assert upload["with"]["path"] == "${{ runner.temp }}/reviewed-ocr-summary/*.json"
    assert upload["with"]["retention-days"] == "7"
    cleanup = next(step for step in job["steps"] if "--cleanup" in step.get("run", ""))
    assert cleanup["if"] == "always()"
    assert "uv sync --project product/backend --extra dev --frozen" in text
    assert "--target \"$RUNNER_TEMP/reviewed-opencv-gui\" opencv-python==5.0.0.93" in text
    for forbidden in ("run deploy", "add-iam", "prod-", "repository_dispatch", "<<", "continue-on-error"):
        assert forbidden not in text


def test_real_guard_delegation_and_separate_offline_process_are_not_replaced():
    source = (ROOT / "scripts/diagnose_reviewed_ocr.py").read_text()
    for call in ("artifacts.validate_payload(row.payload_json)", "workflow._evidence_selection_blocker(row)",
        "orders._validate_selected_hakodate_artifacts(vars(row), captured)",
        "evidence._serialize_evidence_run(row, include_payload=True)", "return real(*args, **kwargs)"):
        assert call in source
    assert '"--offline", "--build", build' in source
    assert "load_bytes_from_uri" in source and "_get_thread_gcs_client" in source
    assert 'for build in ("headless", "gui")' in source
    assert 'sha(data) == entry["sha256"]' in source
    assert '"input_hashes": before' in source
