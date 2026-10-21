"""Synthetic boundary tests only; no staging reads or product guard success injection."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
from tempfile import TemporaryDirectory
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


@pytest.fixture
def proxy_capture(monkeypatch, tmp_path):
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
    state = SimpleNamespace(roots=[], modes=[], processes=[], listeners=[], reads=reads,
        connect=connect, connection=connection, cursor=cursor, password=private_password)
    real_temporary = diag.TemporaryDirectory
    def temporary(*args, **kwargs):
        context = real_temporary(*args, **kwargs)
        state.roots.append(Path(context.name))
        state.modes.append(Path(context.name).stat().st_mode & 0o777)
        return context
    monkeypatch.setattr(diag, "TemporaryDirectory", temporary)
    def launch(args, **kwargs):
        directory = Path(args[args.index("--unix-socket") + 1])
        endpoint = directory / diag.INSTANCE / ".s.PGSQL.5432"
        endpoint.parent.mkdir()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(endpoint))
        listener.listen(1)
        state.listeners.append(listener)
        process = Mock()
        process.poll.return_value = None
        def waited(*args, **kwargs):
            assert directory.exists(), "proxy must finish before its owned directory is removed"
            listener.close()
            return 0
        process.wait.side_effect = waited
        state.processes.append(process)
        return process
    popen = Mock(side_effect=launch)
    state.popen = popen
    monkeypatch.setattr(diag.subprocess, "Popen", popen)
    def forbidden(*args, **kwargs):
        pytest.fail("readiness must not consume the only SQL connection slot")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    yield state
    for listener in state.listeners:
        listener.close()
    assert all(not directory.exists() for directory in state.roots)


def test_capture_is_readonly_snapshot_one_parameterized_row(proxy_capture, tmp_path, capsys):
    state = proxy_capture
    assert diag.read_row(Path("synthetic-proxy"), tmp_path)["id"] == diag.EVIDENCE
    state.connect.assert_called_once()
    state.connection.set_session.assert_called_once_with(isolation_level="REPEATABLE READ", readonly=True)
    assert state.connect.call_args.kwargs["options"] == (
        "-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=1000")
    calls = state.cursor.execute.call_args_list
    assert [call.args[0].split()[0] for call in calls] == ["SHOW", "SHOW", "SELECT"]
    assert calls[-1].args[1] == (diag.ORDER, diag.EVIDENCE)
    assert "FOR UPDATE" not in calls[-1].args[0]
    state.connection.rollback.assert_called_once()
    state.connection.close.assert_called_once()
    state.processes[0].terminate.assert_called_once()
    state.processes[0].wait.assert_called_once_with(timeout=8)
    assert all(args[1:4] in (["run", "services", "describe"], ["secrets", "versions", "access"]) for args in state.reads)
    assert state.password not in capsys.readouterr().out


def test_single_slot_capture_uses_unique_short_private_unix_paths(proxy_capture, tmp_path):
    state = proxy_capture
    for _ in range(2):
        state.cursor.fetchone.side_effect = [{"transaction_read_only": "on"},
            {"transaction_isolation": "repeatable read"}]
        before = state.connect.call_count
        assert diag.read_row(Path("synthetic-proxy"), tmp_path)["id"] == diag.EVIDENCE
        assert state.connect.call_count == before + 1
        directory = state.roots[-1]
        assert directory.parent == Path("/tmp") and state.modes[-1] == 0o700
        endpoint = directory / diag.INSTANCE / ".s.PGSQL.5432"
        assert len(os.fsencode(endpoint)) < 104
        assert state.connect.call_args.kwargs["host"] == str(endpoint.parent)
        assert state.connect.call_args.kwargs["port"] == 5432
        assert state.connect.call_args.kwargs["connect_timeout"] == 8
        assert state.popen.call_args.args[0] == ["synthetic-proxy", "--gcloud-auth", "--unix-socket",
            str(directory), "--max-connections", "1", diag.INSTANCE]
        assert state.popen.call_args.kwargs == {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        assert not directory.exists()
    assert state.roots[0] != state.roots[1]


def test_readiness_waits_for_socket_without_a_connection_probe(proxy_capture, monkeypatch, tmp_path):
    observed = []
    real = Path.is_socket
    def delayed(path):
        observed.append(path)
        return len(observed) > 1 and real(path)
    monkeypatch.setattr(Path, "is_socket", delayed)
    sleep = Mock()
    monkeypatch.setattr(diag.time, "sleep", sleep)
    diag.read_row(Path("synthetic-proxy"), tmp_path)
    sleep.assert_called_once_with(0.1)
    assert len(observed) == 2
    proxy_capture.connect.assert_called_once()


def test_overlong_socket_path_stops_before_proxy_or_database(proxy_capture, monkeypatch, tmp_path):
    roots = []
    @contextmanager
    def overlong(**kwargs):
        with TemporaryDirectory(prefix="x" * 100, dir=tmp_path) as directory:
            roots.append(Path(directory))
            yield directory
    monkeypatch.setattr(diag, "TemporaryDirectory", overlong)
    with pytest.raises(diag.Blocked, match="proxy_socket_path_invalid"):
        diag.read_row(Path("synthetic-proxy"), tmp_path)
    proxy_capture.popen.assert_not_called()
    proxy_capture.connect.assert_not_called()
    assert roots and all(not directory.exists() for directory in roots)


@pytest.mark.parametrize("failure", ["db-connect", "query", "proxy-exit", "readiness-timeout", "spawn"])
def test_proxy_and_owned_directory_cleanup_on_failure(proxy_capture, monkeypatch, tmp_path, failure):
    import psycopg2
    state = proxy_capture
    sentinel = tmp_path / "not-owned-by-proxy"
    sentinel.write_text("preserve")
    if failure == "db-connect":
        state.connect.side_effect = psycopg2.OperationalError("synthetic unavailable")
    elif failure == "query":
        state.cursor.fetchall.side_effect = psycopg2.OperationalError("synthetic query failure")
    elif failure == "proxy-exit":
        real_launch = state.popen.side_effect
        def exited(*args, **kwargs):
            process = real_launch(*args, **kwargs)
            process.poll.return_value = 1
            return process
        state.popen.side_effect = exited
    elif failure == "readiness-timeout":
        monkeypatch.setattr(diag.time, "monotonic", Mock(side_effect=[0, 36]))
    else:
        state.popen.side_effect = OSError("synthetic spawn failure")
    expected = diag.Blocked if failure in {"proxy-exit", "readiness-timeout"} else (
        OSError if failure == "spawn" else psycopg2.OperationalError)
    with pytest.raises(expected):
        diag.read_row(Path("synthetic-proxy"), tmp_path)
    assert state.connect.call_count == (1 if failure in {"db-connect", "query"} else 0)
    assert state.connection.close.call_count == (1 if failure == "query" else 0)
    for process in state.processes:
        process.wait.assert_called_once_with(timeout=8)
        assert process.terminate.call_count == (0 if failure == "proxy-exit" else 1)
    assert sentinel.read_text() == "preserve"
    assert all(not directory.exists() for directory in state.roots)


def test_proxy_termination_timeout_kills_then_waits_before_cleanup(proxy_capture, tmp_path):
    state = proxy_capture
    launch = state.popen.side_effect
    def slow(*args, **kwargs):
        process = launch(*args, **kwargs)
        finish = process.wait.side_effect
        def wait(*args, **kwargs):
            if process.wait.call_count == 1:
                raise subprocess.TimeoutExpired("synthetic-proxy", 8)
            return finish(*args, **kwargs)
        process.wait.side_effect = wait
        return process
    state.popen.side_effect = slow
    diag.read_row(Path("synthetic-proxy"), tmp_path)
    process = state.processes[0]
    process.kill.assert_called_once()
    assert [call.kwargs for call in process.wait.call_args_list] == [{"timeout": 8}, {"timeout": 5}]


def test_sigterm_handler_unwinds_connection_proxy_and_directory(proxy_capture, monkeypatch, tmp_path):
    state = proxy_capture
    handlers = {}
    monkeypatch.setattr(diag.signal, "signal", lambda number, handler: handlers.update({number: handler}))
    monkeypatch.setattr(diag, "context", lambda *_: "a" * 40)
    monkeypatch.setattr(diag, "candidate_context", lambda *_: None)
    def run(product, proxy, summary, candidate, *, original_only):
        assert original_only is False
        return diag.read_row(proxy, tmp_path)
    monkeypatch.setattr(diag, "run", run)
    def terminated():
        handlers[signal.SIGTERM](signal.SIGTERM, None)
    state.cursor.fetchall.side_effect = terminated
    monkeypatch.setattr("sys.argv", ["diagnostic", "--product", str(tmp_path), "--proxy", "synthetic-proxy",
        "--candidate", str(tmp_path / "candidate"),
        "--summary", str(tmp_path / "summary")])
    with pytest.raises(SystemExit) as stopped:
        diag.main()
    assert stopped.value.code == 1
    state.connection.close.assert_called_once()
    state.processes[0].terminate.assert_called_once()
    state.processes[0].wait.assert_called_once_with(timeout=8)
    assert all(not directory.exists() for directory in state.roots)


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
    monkeypatch.setattr(diag, "candidate_context", lambda *_: None)
    monkeypatch.setattr(diag, "run", Mock(side_effect=RuntimeError("PRIVATE-DATA")))
    monkeypatch.setattr("sys.argv", ["diagnostic", "--product", str(tmp_path), "--proxy", "proxy",
        "--candidate", str(tmp_path / "candidate"),
        "--summary", str(tmp_path / "summary")])
    assert diag.main() == 1
    raw = (tmp_path / "summary/capture-error.json").read_text()
    assert "PRIVATE-DATA" not in raw
    assert json.loads(raw)["error"] == "unexpected_RuntimeError"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("offline_mode", [False, True])
@pytest.mark.parametrize("original_only", [False, True])
def test_cli_propagates_explicit_original_mode_without_changing_default(
        actions, monkeypatch, tmp_path, offline_mode, original_only):
    monkeypatch.setattr(diag, "context", lambda *_: "a" * 40)
    monkeypatch.setattr(diag, "candidate_context", lambda *_: None)
    action = Mock(return_value=True)
    monkeypatch.setattr(diag, "offline" if offline_mode else "run", action)
    monkeypatch.setattr(diag.signal, "signal", lambda *args: None)
    argv = ["diagnostic", "--product", str(tmp_path), "--candidate", str(tmp_path / "candidate"),
        "--proxy", "proxy", "--summary", str(tmp_path / "summary")]
    if offline_mode:
        argv += ["--offline"]
    if original_only:
        argv += ["--original-only"]
    monkeypatch.setattr("sys.argv", argv)
    assert diag.main() == 0
    action.assert_called_once()
    assert action.call_args.kwargs["original_only"] == original_only


@pytest.mark.parametrize("headless_status", [0, 1])
@pytest.mark.parametrize("original_only", [False, True])
def test_both_offline_processes_run_with_no_credentials_and_private_cleanup(
        actions, synthetic_row, monkeypatch, tmp_path, headless_status, original_only):
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
        assert ("--original-only" in args) == original_only
        assert kwargs["timeout"] == 300
        assert "DB_PASSWORD" not in kwargs["env"]
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in kwargs["env"]
        assert kwargs["env"]["DATABASE_URL"] == "sqlite:///:memory:"
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        assert ("PYTHONPATH" in kwargs["env"]) == (build == "gui")
        variant = args[args.index("--variant") + 1]
        diag.write_json(summary / (variant + "-" + build + ".json"), {"synthetic": True})
        diag.write_json(diag.owned_path() / (variant + "-" + build + "-results.json"), {"synthetic": True})
        return SimpleNamespace(returncode=headless_status if build == "headless" else 0)
    monkeypatch.setattr(diag.subprocess, "run", run)
    assert diag.run(tmp_path, Path("proxy"), summary, tmp_path / "candidate",
        original_only=original_only) == (headless_status == 0)
    assert len(calls) == 4
    assert not diag.owned_path().exists()
    assert set(path.name for path in summary.iterdir()) == {
        "baseline-headless.json", "baseline-gui.json", "candidate-headless.json", "candidate-gui.json",
        "comparison.json"}


@pytest.mark.parametrize("original_only", [False, True])
def test_offline_original_mode_runs_four_real_guards_and_default_keeps_faults(
        actions, monkeypatch, tmp_path, original_only):
    # Synthetic invalid input exercises the real guards, never injects successful validation.
    from src.services import (canonical_ocr_artifact_service, order_service, order_workflow_v2_service,
        ocr_evidence_service, hakodate_preprocess_observation, hakodate_ocr_checkpoint_service,
        hakodate_recognizer_contact_sheet, hakodate_recognizer_input_evidence)
    import cv2
    from sqlalchemy.engine import Engine
    import psycopg2
    targets = [(socket.socket, name) for name in ("connect", "connect_ex", "sendto", "sendmsg")
        if hasattr(socket.socket, name)]
    targets += [(socket, name) for name in ("create_connection", "getaddrinfo", "gethostbyname",
        "gethostbyname_ex", "gethostbyaddr")]
    targets += [(Engine, "connect"), (Engine, "raw_connection"), (psycopg2, "connect")]
    for module, name in targets:
        monkeypatch.setattr(module, name, getattr(module, name))
    monkeypatch.setattr(diag.sys, "path", list(diag.sys.path))
    monkeypatch.setattr(cv2, "__version__", "5.0.0")
    monkeypatch.setattr(cv2, "__file__", str(ROOT / "backend/.venv/cv2/__init__.py"))
    monkeypatch.setattr(diag.importlib.metadata, "version",
        lambda name: {"Pillow": "13.0.0", "numpy": "2.4.2", "opencv-python-headless": "5.0.0.93"}[name])
    private = tmp_path / "private"
    private.mkdir()
    payload = json.loads(row()["payload_raw"])
    payload["_ocr_execution"]["processing_start_input"] = {}
    manifest = json.loads(row()["manifest_raw"])
    diag.write_json(private / "owner.json", diag.marker())
    diag.write_json(private / "payload.json", payload)
    diag.write_json(private / "manifest.json", manifest)
    monkeypatch.setattr(diag, "PAYLOAD_SHA", diag.sha((private / "payload.json").read_bytes()))
    monkeypatch.setattr(diag, "MANIFEST_SHA", diag.sha((private / "manifest.json").read_bytes()))
    diag.write_json(private / "header.json", {"id": diag.EVIDENCE, "order_id": diag.ORDER,
        "status": "diagnostic_only", "template_version_id": "synthetic", "producer_version": "synthetic",
        "source": "synthetic", "schema_version": "synthetic", "artifact_digest": diag.ARTIFACT_SHA,
        "created_at": "2026-10-19T00:00:00"})
    diag.write_json(private / "bundle.json", {"assets": {diag.PDF_URI + "?generation=1":
        {"generation": "1", "latest_generation": "1"}}})
    output = tmp_path / "summary.json"
    assert diag.offline(ROOT, private, output, "headless", original_only=original_only) == original_only
    result = json.loads(output.read_bytes())
    assert result["mode"] == ("original-only" if original_only else "all")
    assert result["original_validation_passed"] is False
    assert result["inputs_unchanged"] is True
    assert len(result["results"]) == (4 if original_only else 34)
    assert [(item["case"], item["phase"]) for item in result["results"][:4]] == [
        ("original", phase) for phase in ("canonical_payload", "selection_blocker",
            "selected_validation", "serialize_evidence")]
    expected = {"original"} if original_only else {"original", "acceptance_changed", "source_changed",
        "wrong_order", "wrong_revision", "latest_pdf_generation", "multiple_invalid",
        "unavailable_0", "bytes_changed_0", "in_flight_delete_0"}
    assert {item["case"] for item in result["results"]} == expected
    full = json.loads((private / "baseline-headless-results.json").read_bytes())
    assert len(full) == len(result["results"])
    assert full[0]["exception"]


def test_workflow_run_blocks_have_bash_syntax():
    workflow = yaml.load((ROOT / ".github/workflows/deploy-stg.yml").read_text(), Loader=yaml.BaseLoader)
    for step in workflow["jobs"]["diagnose-reviewed-ocr"]["steps"]:
        if "run" in step:
            result = subprocess.run(["bash", "-n"], input=step["run"], text=True, capture_output=True)
            assert result.returncode == 0, step["name"]


def test_dedicated_workflow_is_manual_owner_staging_nondeploy_and_summary_only():
    raw = (ROOT / ".github/workflows/deploy-stg.yml").read_text()
    workflow = yaml.load(raw, Loader=yaml.BaseLoader)
    text = raw.split("  diagnose-reviewed-ocr:\n", 1)[1].split("\n  diagnose-contact-replay:", 1)[0]
    assert not (ROOT / ".github/workflows/diagnose-reviewed-ocr.yml").exists()
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert inputs["mode"]["options"] == ["normal", "recover-web-image"]
    assert inputs["mode"]["description"] == "Normal deployment or explicitly approved web image recovery"
    assert inputs["diagnose_reviewed_ocr"]["type"] == "boolean"
    assert inputs["diagnose_reviewed_ocr"]["default"] == "false"
    job = workflow["jobs"]["diagnose-reviewed-ocr"]
    assert job["needs"] == "source-gate"
    assert "needs.source-gate.result == 'success'" in job["if"]
    assert "inputs.mode == 'normal'" in job["if"]
    assert "inputs.diagnose_reviewed_ocr == true" in job["if"]
    for boundary in ("github.actor == github.repository_owner", "github.triggering_actor == github.repository_owner",
        "refs/heads/develop", "workflow_dispatch", diag.REPOSITORY):
        assert boundary in job["if"]
    assert (job["runs-on"], job["environment"], job["timeout-minutes"]) == ("ubuntu-24.04", "staging", "15")
    assert job["permissions"] == {"contents": "read", "actions": "read", "id-token": "write"}
    checkouts = [step["with"] for step in job["steps"] if step.get("uses") == "actions/checkout@v6"]
    assert checkouts == [{"path": "diagnostic", "persist-credentials": "false"},
        {"ref": diag.PRODUCT_SHA, "path": "product", "persist-credentials": "false"},
        {"ref": "${{ github.sha }}", "path": "candidate", "persist-credentials": "false"}]
    auth = next(step for step in job["steps"] if step.get("uses") == "google-github-actions/auth@v3")
    assert auth["with"]["service_account"] == "${{ secrets.GCP_DEPLOY_SERVICE_ACCOUNT_STG }}"
    upload = job["steps"][-1]
    assert upload["uses"] == "actions/upload-artifact@v4" and upload["if"] == "always()"
    assert upload["with"]["path"] == "${{ runner.temp }}/reviewed-ocr-summary/*.json"
    assert upload["with"]["retention-days"] == "7"
    cleanup = next(step for step in job["steps"] if "--cleanup" in step.get("run", ""))
    assert cleanup["if"] == "always()"
    capture = next(step for step in job["steps"] if step.get("name") == "Capture one read-only snapshot and run disconnected profiles")
    assert "--original-only" in capture["run"]
    assert "--original-only" not in cleanup["run"]
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


def test_diagnostic_is_selected_in_existing_saved_source_ci_command():
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
    selected = next(step for step in steps if step.get("name") == "Verify initial saved-source preservation")
    assert selected["working-directory"] == "backend"
    assert selected["run"].count("tests/unit/test_reviewed_ocr_diagnostic.py") == 1
    assert "--confcutdir=tests/unit" in selected["run"]
    assert "--junitxml=.logs/saved-source-junit.xml -q" in selected["run"]


@pytest.mark.parametrize("value", [None, [], {}, {"records": [1, True, "1"], "contact": b"\x00\xff"}])
def test_complete_comparison_keeps_types_and_every_byte(value):
    encoded = diag.result_bytes(value)
    assert encoded == diag.result_bytes(deepcopy(value))
    assert diag.result_bytes(1) != diag.result_bytes(True) != diag.result_bytes("1")
    assert diag.result_bytes(b"x") != diag.result_bytes("x")
    assert diag.result_bytes(b"x") != diag.result_bytes(b"y")


@pytest.mark.parametrize("difference", [None, "quantity", "payload", "bytes", "error", "error_order"])
def test_before_after_comparison_checks_full_private_results_not_boolean(tmp_path, difference):
    private, summary = tmp_path / "private", tmp_path / "summary"
    private.mkdir()
    summary.mkdir()
    original = {"selectable": True, "quantity": 7, "payload": ["raw", ""], "bytes": b"original",
        "errors": ["first_guard", "second_guard"]}
    changed = deepcopy(original)
    if difference == "quantity": changed["quantity"] = 8
    elif difference == "payload": changed["payload"][1] = "1"
    elif difference == "bytes": changed["bytes"] = b"changed"
    elif difference == "error": changed["errors"][0] = "other_guard"
    elif difference == "error_order": changed["errors"].reverse()
    for build in ("headless", "gui"):
        diag.write_private(private / ("baseline-" + build + "-results.json"), diag.result_bytes(original))
        diag.write_private(private / ("candidate-" + build + "-results.json"), diag.result_bytes(changed))
    assert diag.compare_results(private, summary) is (difference is None)
    raw = (summary / "comparison.json").read_text()
    assert "original" not in raw and "quantity" not in raw and "first_guard" not in raw


@pytest.mark.parametrize("difference", ["sha", "dirty", "lock", "project", "missing"])
def test_candidate_source_and_identical_runtime_are_mandatory(actions, tmp_path, monkeypatch, difference):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    for root in (baseline, candidate):
        (root / "backend").mkdir(parents=True)
        (root / "backend/uv.lock").write_bytes(b"same lock")
        (root / "backend/pyproject.toml").write_bytes(b"same project")
    if difference in ("lock", "project"):
        name = "uv.lock" if difference == "lock" else "pyproject.toml"
        (candidate / "backend" / name).write_bytes(b"changed")
    monkeypatch.setattr(diag, "command", Mock(side_effect=[
        b"different" if difference == "sha" else actions["GITHUB_SHA"].encode(),
        b"M product" if difference == "dirty" else b""]))
    with pytest.raises(diag.Blocked):
        diag.candidate_context(baseline, None if difference == "missing" else candidate)


@pytest.mark.parametrize("errors", [
    ["first", "different_later", "wrapper", "wrapper"],
    ["first", "later", "wrapper"],
    ["first", "wrapper", "later", "wrapper"],
])
def test_same_value_first_error_and_exception_cannot_hide_later_error_or_wrapper_loss(tmp_path, errors):
    private, summary = tmp_path / "private", tmp_path / "summary"
    private.mkdir()
    summary.mkdir()
    original = {"value": {"selectable": False}, "exception": ["builtins", "ValueError", "first"],
        "first_guard_error": "first", "errors_in_order": ["first", "later", "wrapper", "wrapper"]}
    changed = deepcopy(original)
    changed["errors_in_order"] = errors
    for build in ("headless", "gui"):
        diag.write_json(private / ("baseline-" + build + "-results.json"), original)
        diag.write_json(private / ("candidate-" + build + "-results.json"), changed)
    assert diag.compare_results(private, summary) is False
    assert all(not item["full_results_equal"] for item in json.loads((summary / "comparison.json").read_text())["comparisons"])
    source = (ROOT / "scripts/diagnose_reviewed_ocr.py").read_text()
    assert 'full_results[-1]["errors_in_order"] = list(failures)' in source


def staging_workflow():
    return yaml.load((ROOT / ".github/workflows/deploy-stg.yml").read_text(), Loader=yaml.BaseLoader)


def dispatch_environment():
    return {"PATH": os.defpath, "GITHUB_REPOSITORY": diag.REPOSITORY,
        "GITHUB_REF": "refs/heads/develop", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "DEPLOY_MODE": "normal", "REPOSITORY_OWNER": "mmorinag127",
        "REVIEWED_DIAGNOSTIC": "true", "REVIEWED_DIAGNOSTIC_JSON": "true",
        "ACTOR": "mmorinag127", "TRIGGERING_ACTOR": "mmorinag127",
        "RECOVERY_CONFIRMATION": "", "EXPECTED_REVISION": "", "EXPECTED_IMAGE": "",
        "OCR_MANIFEST": "", "OCR_EXISTING_RUN": "", "MENU_MANIFEST": "", "REVIEW_ORDER": "",
        "OUTPUT_ORDER": "", "OUTPUT_DATE": "", "VERIFY_MENU": "false", "CONTACT_REPLAY": "false"}


def mode_validation(env):
    step = staging_workflow()["jobs"]["source-gate"]["steps"][0]
    return subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True)


@pytest.mark.parametrize("key,value", [
    ("GITHUB_REPOSITORY", "other/repo"), ("GITHUB_REF", "refs/heads/main"),
    ("GITHUB_EVENT_NAME", "push"), ("GITHUB_EVENT_NAME", "repository_dispatch"),
    ("REPOSITORY_OWNER", "other"), ("ACTOR", "other"), ("TRIGGERING_ACTOR", "other"),
    ("RECOVERY_CONFIRMATION", "RECOVER_WEB_STG_IMAGE_AND_KEEP_STG_IMAGES"),
    ("EXPECTED_REVISION", "web-stg-reviewed"), ("EXPECTED_IMAGE", "image"),
    ("OCR_MANIFEST", "manifest"), ("OCR_EXISTING_RUN", "123"), ("MENU_MANIFEST", "manifest"),
    ("REVIEW_ORDER", "ORD581f55d6"), ("OUTPUT_ORDER", "ORD581f55d6"), ("OUTPUT_DATE", "2026-10-19"),
    ("VERIFY_MENU", "true"), ("CONTACT_REPLAY", "true"),
    ("VERIFY_MENU", "unknown"), ("CONTACT_REPLAY", "unknown"),
    ("DEPLOY_MODE", "recover-web-image"), ("DEPLOY_MODE", "diagnose-reviewed-ocr"),
])
def test_diagnostic_mode_rejects_foreign_context_and_every_conflicting_input(key, value, tmp_path):
    env = dispatch_environment()
    env[key] = value
    assert mode_validation(env).returncode != 0
    outcomes, outputs = dispatch_route(env, tmp_path)
    assert outcomes["source-gate"] == "failure" and outputs == {}
    assert all(result == "skipped" for name, result in outcomes.items() if name != "source-gate")


def test_valid_diagnostic_disables_both_change_outputs(tmp_path):
    env = dispatch_environment()
    assert mode_validation(env).returncode == 0
    env["GITHUB_OUTPUT"] = str(tmp_path / "outputs")
    changes = next(s for s in staging_workflow()["jobs"]["source-gate"]["steps"] if s.get("id") == "changes")
    subprocess.run(["bash", "-c", changes["run"]], env=env, check=True, capture_output=True)
    assert (tmp_path / "outputs").read_text() == "backend_changed=false\nfrontend_changed=false\n"


def job_allowed(expression, **overrides):
    values = {"inputs.mode": "normal", "inputs.diagnose_reviewed_ocr": True, "github.repository": diag.REPOSITORY,
        "github.actor": "mmorinag127", "github.triggering_actor": "mmorinag127",
        "github.repository_owner": "mmorinag127", "github.ref": "refs/heads/develop",
        "github.event_name": "workflow_dispatch", "inputs.diagnose_saved_contact_replay": False,
        "needs.source-gate.result": "success", "needs.source-gate.outputs.backend_changed": "false",
        "needs.source-gate.outputs.frontend_changed": "false", "needs.build-frontend.result": "skipped",
        "needs.automation-bootstrap.result": "skipped", "needs.menu-master-migration.result": "skipped",
        "needs.deploy-backend.result": "skipped", **overrides}
    expression = expression.replace("always()", "True")
    expression = re.sub(r"\b(?:needs|github|inputs)\.[\w.-]+", lambda m: repr(values[m[0]]), expression)
    expression = re.sub(r"!(?!=)", "not ", expression)
    expression = re.sub(r"\btrue\b", "True", expression)
    return eval("(" + expression.replace("&&", " and ").replace("||", " or ") + ")",
        {"__builtins__": {}}, {})


def dispatch_route(env, tmp_path):
    jobs = staging_workflow()["jobs"]
    outcomes = {"source-gate": "success" if mode_validation(env).returncode == 0 else "failure"}
    outputs = {}
    if outcomes["source-gate"] == "success":
        changes = next(s for s in jobs["source-gate"]["steps"] if s.get("id") == "changes")
        output_file = tmp_path / "route-outputs"
        subprocess.run(["bash", "-c", changes["run"]], env={**env, "GITHUB_OUTPUT": str(output_file)},
            check=True, capture_output=True)
        outputs = dict(line.split("=", 1) for line in output_file.read_text().splitlines())
    pending = {name: job for name, job in jobs.items() if name != "source-gate"}
    while pending:
        ready = []
        for name, job in pending.items():
            dependencies = job.get("needs", [])
            if isinstance(dependencies, str):
                dependencies = [dependencies]
            if any(dependency not in outcomes for dependency in dependencies):
                continue
            ready.append(name)
            expression = job["if"]
            implicit_success = all(outcomes[dependency] == "success" for dependency in dependencies)
            values = {"inputs.mode": env["DEPLOY_MODE"],
                "inputs.diagnose_reviewed_ocr": env.get("REVIEWED_DIAGNOSTIC_JSON") == "true",
                "inputs.diagnose_saved_contact_replay": env.get("CONTACT_REPLAY") == "true",
                "github.repository": env["GITHUB_REPOSITORY"], "github.ref": env["GITHUB_REF"],
                "github.event_name": env["GITHUB_EVENT_NAME"], "github.actor": env["ACTOR"],
                "github.triggering_actor": env["TRIGGERING_ACTOR"],
                "github.repository_owner": env["REPOSITORY_OWNER"],
                "needs.source-gate.outputs.backend_changed": outputs.get("backend_changed", ""),
                "needs.source-gate.outputs.frontend_changed": outputs.get("frontend_changed", ""),
                **{"needs." + dependency + ".result": outcome for dependency, outcome in outcomes.items()}}
            allowed = (implicit_success or "always()" in expression) and job_allowed(expression, **values)
            outcomes[name] = "success" if allowed else "skipped"
        assert ready, "workflow dependency graph cannot progress"
        for name in ready:
            del pending[name]
    return outcomes, outputs


def test_diagnostic_source_gate_and_dependency_graph_disable_every_side_effect_job(tmp_path):
    jobs = staging_workflow()["jobs"]
    assert set(jobs) == {"source-gate", "diagnose-reviewed-ocr", "diagnose-contact-replay", "recover-web",
        "automation-bootstrap", "menu-master-migration", "build-backend", "build-frontend", "build-agent",
        "deploy-backend", "deploy-frontend"}
    outcomes, outputs = dispatch_route(dispatch_environment(), tmp_path)
    assert outputs == {"backend_changed": "false", "frontend_changed": "false"}
    assert outcomes["source-gate"] == outcomes["diagnose-reviewed-ocr"] == "success"
    assert all(result == "skipped" for name, result in outcomes.items()
        if name not in {"source-gate", "diagnose-reviewed-ocr"})
    expression = jobs["diagnose-reviewed-ocr"]["if"]
    assert job_allowed(expression)
    for key, value in [("inputs.diagnose_reviewed_ocr", False), ("inputs.mode", "recover-web-image"),
        ("github.actor", "other"), ("github.triggering_actor", "other"),
        ("github.ref", "refs/heads/main"), ("github.repository", "other/repo"),
        ("github.event_name", "push"), ("needs.source-gate.result", "failure")]:
        assert not job_allowed(expression, **{key: value})


@pytest.mark.parametrize("event,mode", [("push", ""), ("workflow_dispatch", "normal"),
    ("workflow_dispatch", "recover-web-image")])
def test_existing_normal_and_recovery_input_guards_still_apply(event, mode):
    env = dispatch_environment()
    env.update(GITHUB_EVENT_NAME=event, DEPLOY_MODE=mode,
        REVIEWED_DIAGNOSTIC="false", REVIEWED_DIAGNOSTIC_JSON="false")
    if mode == "recover-web-image":
        env.update(RECOVERY_CONFIRMATION="RECOVER_WEB_STG_IMAGE_AND_KEEP_STG_IMAGES",
            EXPECTED_REVISION="web-stg-reviewed", EXPECTED_IMAGE=
            "asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/frontend@sha256:" + "a" * 64)
    assert mode_validation(env).returncode == 0
    if mode == "recover-web-image":
        env["RECOVERY_CONFIRMATION"] = ""
    else:
        env["EXPECTED_REVISION"] = "web-stg-reviewed"
    assert mode_validation(env).returncode != 0


@pytest.mark.parametrize("flag,encoded", [
    ("true", '"true"'), ("false", '"false"'), ("1", "1"), ("0", "0"),
    ("True", "true"), ("TRUE", "true"), ("true", "null"), ("true", ""),
    ("", "true"), ("false", "true"), ("true", "false"), ("", "[]"), ("", "{}"),
])
def test_diagnostic_flag_rejects_strings_numbers_missing_or_inconsistent_types(flag, encoded, tmp_path):
    env = dispatch_environment()
    env.update(REVIEWED_DIAGNOSTIC=flag, REVIEWED_DIAGNOSTIC_JSON=encoded)
    assert mode_validation(env).returncode != 0
    outcomes, outputs = dispatch_route(env, tmp_path)
    assert outcomes["source-gate"] == "failure" and outputs == {}
    assert all(result == "skipped" for name, result in outcomes.items() if name != "source-gate")


@pytest.mark.parametrize("flag,encoded", [("false", "false"), ("", "null"), ("", "")])
def test_false_or_absent_diagnostic_keeps_normal_change_outputs(flag, encoded, tmp_path):
    env = dispatch_environment()
    env.update(REVIEWED_DIAGNOSTIC=flag, REVIEWED_DIAGNOSTIC_JSON=encoded,
        GITHUB_OUTPUT=str(tmp_path / "outputs"))
    assert mode_validation(env).returncode == 0
    changes = next(s for s in staging_workflow()["jobs"]["source-gate"]["steps"] if s.get("id") == "changes")
    subprocess.run(["bash", "-c", changes["run"]], env=env, check=True, capture_output=True)
    assert (tmp_path / "outputs").read_text() == "backend_changed=true\nfrontend_changed=true\n"
