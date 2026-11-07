import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import production_maintenance as maintenance


def descriptor(revision="maintenance", tag=None):
    traffic = [{"revisionName": revision, "percent": 100}]
    if tag:
        traffic.append({"revisionName": "candidate", "tag": tag, "url": "https://candidate.a.run.app"})
    return {"metadata": {"annotations": {}}, "status": {"traffic": traffic,
            "latestReadyRevisionName": "candidate", "url": "https://web.a.run.app"}}


@pytest.mark.parametrize("traffic", [[], [{"revisionName": "a", "percent": 50},
    {"revisionName": "b", "percent": 50}], [{"revisionName": "a", "percent": 100, "tag": "unknown"}]])
def test_unreviewed_traffic_rejected(traffic):
    with pytest.raises(RuntimeError):
        maintenance.serving({"status": {"traffic": traffic}})


def test_only_current_candidate_tag_is_allowed():
    service = descriptor(tag="verify-123")
    assert maintenance.serving(service, "verify-123") == "maintenance"
    with pytest.raises(RuntimeError):
        maintenance.serving(service, "verify-456")


def record_fixture(monkeypatch, tmp_path):
    record = {"run": "123", "sha": "a" * 40, "ref": "refs/heads/release/prod-test",
              "services": {name: {"revision": "old-" + name, "uid": "original", "images": ["original"]}
                           for name in maintenance.SERVICES},
              "maintenance_revision": "maintenance", "database_drained": True,
              "portal_bootstrap_complete": True, "reopened": False}
    path = tmp_path / "maintenance.json"
    monkeypatch.setattr(maintenance, "load", lambda: (path, record))
    return path, record


def test_restore_entrypoint_preserves_original_command_and_probes(monkeypatch, tmp_path):
    _, record = record_fixture(monkeypatch, tmp_path)
    old = {"image": "original", "ports": [{"containerPort": 8080}], "command": ["node"], "args": ["server.js"],
           "startupProbe": {"tcpSocket": {"port": 8080}}}
    revision = {"metadata": {"uid": "original"}, "spec": {"containers": [old]}}
    monkeypatch.setattr(maintenance, "cloud", lambda *_args: revision)
    service = descriptor()
    service["spec"] = {"template": {"metadata": {}, "spec": {"containers": [{"image": "maintenance",
                       "command": ["node"], "args": ["maintenance.js"], "livenessProbe": {}}]}}}
    result = maintenance.restore_frontend_entrypoint(service)
    restored = result["spec"]["template"]["spec"]["containers"][0]
    assert restored["args"] == ["server.js"]
    assert restored["startupProbe"] == old["startupProbe"]
    assert "livenessProbe" not in restored
    record["services"]["web-prod"]["uid"] = "changed"
    with pytest.raises(RuntimeError, match="original portal"):
        maintenance.restore_frontend_entrypoint(service)


def test_same_run_source_and_inventory_required(monkeypatch, tmp_path):
    path = tmp_path / "maintenance.json"
    monkeypatch.setattr(maintenance, "context", lambda: path)
    for key, value in {"GITHUB_RUN_ID": "123", "GITHUB_SHA": "source", "GITHUB_REF": "ref"}.items():
        monkeypatch.setenv(key, value)
    record = {"run": "123", "sha": "source", "ref": "ref", "services": dict.fromkeys(maintenance.SERVICES)}
    path.write_text(json.dumps(record))
    assert maintenance.load()[1] == record
    for key in ("run", "sha", "ref", "services"):
        altered = dict(record)
        altered[key] = {} if key == "services" else "other"
        path.write_text(json.dumps(altered))
        with pytest.raises(RuntimeError):
            maintenance.load()


def test_failure_hold_never_changes_unrelated_traffic(monkeypatch, tmp_path):
    record_fixture(monkeypatch, tmp_path)
    cloud = Mock(return_value=descriptor("someone-else"))
    monkeypatch.setattr(maintenance, "cloud", cloud)
    monkeypatch.setattr(maintenance, "assert_stopped", lambda: None)
    with pytest.raises(RuntimeError, match="unrelated"):
        maintenance.hold_on_failure()
    assert not any(call.args[:3] == ("run", "services", "update-traffic") for call in cloud.call_args_list)
    assert sum("--scaling=0" in call.args for call in cloud.call_args_list) == 2


def test_failure_hold_returns_only_to_maintenance(monkeypatch, tmp_path):
    path, record = record_fixture(monkeypatch, tmp_path)
    record.update(candidate_revision="candidate", candidate_tag="verify-123")
    cloud = Mock(return_value=descriptor("candidate", "verify-123"))
    monkeypatch.setattr(maintenance, "cloud", cloud)
    verified = Mock()
    monkeypatch.setattr(maintenance, "verify_display", verified)
    stopped = Mock()
    monkeypatch.setattr(maintenance, "assert_stopped", stopped)
    maintenance.hold_on_failure()
    mutation = cloud.call_args_list[1].args
    assert "--to-revisions=maintenance=100" in mutation
    assert "--remove-tags=verify-123" in mutation
    assert all("old-web-prod" not in str(call.args) for call in cloud.call_args_list)
    assert json.loads(path.read_text())["held_after_failure"] is True
    verified.assert_called_once()
    stopped.assert_called_once()
    assert sum("--scaling=0" in call.args for call in cloud.call_args_list) == 2


def test_promote_rejects_candidate_identity_change(monkeypatch, tmp_path):
    _, record = record_fixture(monkeypatch, tmp_path)
    record.update(candidate_revision="candidate", candidate_url="https://candidate.a.run.app",
                  candidate_uid="expected", candidate_images=["original"], writers_verified_open=True)
    monkeypatch.setattr(maintenance, "verify_display", lambda: None)
    cloud = Mock(side_effect=[descriptor(), {"metadata": {"uid": "wrong"},
                  "spec": {"containers": [{"image": "original"}]}}])
    monkeypatch.setattr(maintenance, "cloud", cloud)
    with pytest.raises(RuntimeError, match="identity"):
        maintenance.promote()
    assert cloud.call_count == 2


def test_stopped_writer_check_rejects_automatic_scaling(monkeypatch):
    monkeypatch.setattr(maintenance, "cloud", lambda *_args: descriptor())
    with pytest.raises(RuntimeError, match="not stopped"):
        maintenance.assert_stopped()


def test_candidate_is_verified_without_public_promotion(monkeypatch, tmp_path):
    _, record = record_fixture(monkeypatch, tmp_path)
    monkeypatch.setenv("IMAGE", "front@sha256:123")
    monkeypatch.setenv("SAWA_AGENT_RUNTIME_IMAGE", "agent@sha256:456")
    import registry_image_digest
    monkeypatch.setattr(registry_image_digest, "resolve_image_digest", lambda value: value)
    revision = {"metadata": {"uid": "candidate-uid"}, "spec": {"containers": [
        {"image": "front@sha256:123"}, {"image": "agent@sha256:456"}]}}
    cloud = Mock(side_effect=[descriptor(), revision, None, descriptor(tag="verify-123")])
    monkeypatch.setattr(maintenance, "cloud", cloud)
    monkeypatch.setattr(maintenance, "verify_display", lambda: None)
    assert maintenance.candidate_url() == "https://candidate.a.run.app"
    assert "--to-revisions=maintenance=100" in cloud.call_args_list[2].args
    assert record["candidate_uid"] == "candidate-uid"
    assert not record["reopened"]


def test_live_display_requires_503_and_maintenance_header(monkeypatch, tmp_path):
    record_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(maintenance, "cloud", lambda *_args: descriptor())
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.code = 200
    response.headers = {"X-Sawa-Maintenance": "1"}
    monkeypatch.setattr(maintenance, "urlopen", lambda *_args, **_kwargs: response)
    with pytest.raises(RuntimeError, match="response"):
        maintenance.verify_display()
    response.code = 503
    response.headers = {}
    with pytest.raises(RuntimeError, match="response"):
        maintenance.verify_display()
    response.headers = {"X-Sawa-Maintenance": "1"}
    maintenance.verify_display()


def test_maintenance_http_never_accepts_business_requests():
    root = Path(__file__).resolve().parents[3]
    script = r"""
const assert = require('node:assert/strict');
const http = require('node:http');
const {handler} = require('./infra/maintenance/server.cjs');
const server = http.createServer(handler);
server.listen(0, '127.0.0.1', async () => {
  try {
    const base = 'http://127.0.0.1:' + server.address().port;
    for (const [method, path, status] of [
      ['GET','/healthz',200], ['GET','/',503], ['GET','/login',503],
      ['GET','/api/auth/me',503], ['POST','/api/orders',503],
      ['DELETE','/api/orders/example',503], ['POST','/healthz',503]]) {
      const response = await fetch(base + path, {method});
      assert.equal(response.status, status);
      assert.equal(response.headers.get('x-sawa-maintenance'), '1');
      assert.equal(response.headers.get('cache-control'), 'no-store');
      if (path === '/') assert.match(await response.text(), /アップデート中/);
    }
  } catch (error) { console.error(error); process.exitCode = 1; }
  finally { server.closeAllConnections(); server.close(); }
});
"""
    subprocess.run(["node", "-e", script], cwd=root, check=True, capture_output=True, timeout=20)


@pytest.mark.parametrize("database,clients,error", [("orders", 0, None),
    ("other", 0, "unexpected"), ("orders", 1, "did not drain")])
def test_drain_requires_correct_empty_database(monkeypatch, tmp_path, database, clients, error):
    path, record = record_fixture(monkeypatch, tmp_path)
    record.update(writers_stopped=True, database_drained=False)
    monkeypatch.setattr(maintenance, "assert_stopped", Mock())
    connection = MagicMock()
    connection.execute.side_effect = [Mock(scalar_one=lambda: database), Mock(scalar_one=lambda: clients)]
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection
    if error:
        with pytest.raises(RuntimeError, match=error):
            maintenance.drain(engine, timeout=0)
        assert not record["database_drained"]
    else:
        maintenance.drain(engine, timeout=0)
        assert json.loads(path.read_text())["database_drained"] is True
        assert all("SELECT" in str(call.args[0]) for call in connection.execute.call_args_list)


def test_old_writer_is_never_reopened_on_migrated_database(monkeypatch, tmp_path):
    record_fixture(monkeypatch, tmp_path)
    cloud = Mock(return_value=descriptor("old-worker-prod"))
    monkeypatch.setattr(maintenance, "cloud", cloud)
    with pytest.raises(RuntimeError, match="never reopen"):
        maintenance.resume_writer("worker-prod", "new-image")
    assert cloud.call_count == 1


def test_workflow_orders_maintenance_before_migration_and_candidate_before_promotion():
    root = Path(__file__).resolve().parents[3]
    source = (root / ".github/workflows/deploy-prod.yml").read_text()
    assert source.index("production_maintenance.py show") < source.index("prod-db-bootstrap-gate:")
    assert source.index("production_maintenance.py show") < source.index("production_maintenance.py stop")
    assert source.index("production_maintenance.py drain") < source.index("gcloud sql backups create", source.index("prod-db-bootstrap-gate:"))
    assert source.index("gcloud sql backups create", source.index("prod-db-bootstrap-gate:")) < source.index("python scripts/run_prod_schema_adoption.py")
    assert source.index("Verify deferred live portal authentication") < source.index("production_maintenance.py promote")
    assert source.index("production_maintenance.py promote") < source.index("python scripts/activate_hospital_delivery.py")
    assert "python3 scripts/production_maintenance.py hold-on-failure" in source


def test_failed_mutation_preserves_intent_before_cloud_call(monkeypatch, tmp_path):
    path = tmp_path / "record.json"
    record = {"run": "123"}
    def cloud(*_args):
        persisted = json.loads(path.read_text())
        assert persisted["operations"] == [{"operation": "pause:queue", "state": "started"}]
        raise RuntimeError("cloud failed after receiving mutation")
    monkeypatch.setattr(maintenance, "cloud", cloud)
    with pytest.raises(RuntimeError):
        maintenance.mutation(path, record, "pause:queue", "tasks", "queues", "pause", "queue")
    assert json.loads(path.read_text())["operations"][0]["state"] == "started"
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("failed_step", ["inspection", "portal", "display", "worker-prod"])
def test_failure_hold_attempts_both_writers_even_if_other_recovery_fails(monkeypatch, tmp_path, failed_step):
    path, record = record_fixture(monkeypatch, tmp_path)
    record.update(candidate_revision="candidate", candidate_tag="verify-123")
    attempts = []
    def cloud(*args):
        if args[:3] == ("run", "services", "describe"):
            if failed_step == "inspection":
                raise RuntimeError("service inspection unavailable")
            return descriptor("candidate", "verify-123")
        if args[:3] == ("run", "services", "update-traffic") and failed_step == "portal":
            raise RuntimeError("traffic update failed")
        if "--scaling=0" in args:
            attempts.append(args[3])
            if args[3] == failed_step:
                raise RuntimeError("writer stop failed")
    monkeypatch.setattr(maintenance, "cloud", cloud)
    monkeypatch.setattr(maintenance, "verify_display", Mock(side_effect=
        RuntimeError("display check failed") if failed_step == "display" else None))
    monkeypatch.setattr(maintenance, "assert_stopped", lambda: None)
    with pytest.raises(RuntimeError, match="recovery incomplete"):
        maintenance.hold_on_failure()
    assert attempts == list(maintenance.WRITERS)
    saved = json.loads(path.read_text())
    assert saved["held_after_failure"] is False
    assert saved["failure_hold_unconfirmed"]


def test_stop_requires_visible_maintenance_before_any_writer_mutation(monkeypatch, tmp_path):
    record_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(maintenance, "verify_display", Mock(side_effect=RuntimeError("display missing")))
    cloud = Mock()
    monkeypatch.setattr(maintenance, "cloud", cloud)
    with pytest.raises(RuntimeError, match="display missing"):
        maintenance.stop_writers()
    cloud.assert_not_called()


def test_recovery_restores_existing_sidecar_without_running_it_in_maintenance(monkeypatch, tmp_path):
    _, record = record_fixture(monkeypatch, tmp_path)
    old = [{"name": "web", "image": "original", "ports": [{"containerPort": 8080}]},
           {"name": "agent-runtime", "image": "agent"}]
    record["services"]["web-prod"]["images"] = ["original", "agent"]
    revision = {"metadata": {"uid": "original", "annotations": {
        "run.googleapis.com/container-dependencies": '{"web":["agent-runtime"]}'}},
        "spec": {"containers": old}}
    monkeypatch.setattr(maintenance, "cloud", lambda *_args: revision)
    service = descriptor()
    service["spec"] = {"template": {"metadata": {}, "spec": {"containers": [{"image": "maintenance"}]}}}
    restored = maintenance.restore_frontend_entrypoint(service)
    assert restored["spec"]["template"]["spec"]["containers"] == old
    assert restored["spec"]["template"]["spec"]["containers"] is not old


@pytest.mark.parametrize("phase", ["start", "database"])
@pytest.mark.parametrize("mismatch", [False, True])
def test_retry_download_uses_prior_attempt_only_for_same_source(monkeypatch, tmp_path, phase, mismatch):
    monkeypatch.setattr(maintenance, "require_context", lambda _env: "prod")
    for key, value in {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SHA": "source",
                       "GITHUB_REF": "refs/heads/release/prod-test", "RUNNER_TEMP": str(tmp_path),
                       "GITHUB_REPOSITORY": "owner/repository"}.items():
        monkeypatch.setenv(key, value)
    prefix = "hospital-maintenance-" + ("start-" if phase == "start" else "")
    artifact = prefix + "123-1"
    monkeypatch.setattr(maintenance.subprocess, "check_output", lambda *_args, **_kwargs:
                        json.dumps([{"artifacts": [{"name": artifact, "expired": False}]}]))
    def download(args, **_kwargs):
        assert args[args.index("--name") + 1] == artifact
        destination = Path(args[args.index("--dir") + 1])
        record = {"run": "123", "sha": "other" if mismatch else "source",
                  "ref": "refs/heads/release/prod-test", "services": {}}
        (destination / "hospital-maintenance.json").write_text(json.dumps(record))
        (destination / "hospital-rollback.json").write_text(json.dumps(record))
    monkeypatch.setattr(maintenance.subprocess, "run", download)
    if mismatch:
        with pytest.raises(RuntimeError, match="source mismatch"):
            maintenance.download_record(phase)
        assert not (tmp_path / "hospital-maintenance.json").exists()
    else:
        maintenance.download_record(phase)
        assert json.loads((tmp_path / "hospital-maintenance.json").read_text())["sha"] == "source"


def test_runtime_readonly_rejects_mutation_before_handler(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.http_middleware import configure_http_middleware
    app = FastAPI()
    calls = []
    @app.api_route("/business", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def business():
        calls.append(1)
        return {"ok": True}
    configure_http_middleware(app)
    monkeypatch.setenv("SAWA_RELEASE_READ_ONLY", "1")
    with TestClient(app) as client:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = client.request(method, "/business")
            assert response.status_code == 503
            assert response.headers["retry-after"] == "300"
        assert not calls
        assert client.get("/business").status_code == 200
        monkeypatch.delenv("SAWA_RELEASE_READ_ONLY")
        assert client.post("/business").status_code == 200
        assert len(calls) == 2


@pytest.mark.parametrize("failure", [None, "wrong-image", "missing-gate", "update-failed"])
def test_open_writers_checks_release_and_gate_before_mutation(monkeypatch, tmp_path, failure):
    from types import SimpleNamespace
    path, record = record_fixture(monkeypatch, tmp_path)
    record.update(candidate_revision="candidate", candidate_uid="candidate-id",
                  candidate_images=["front"], candidate_url="https://candidate.a.run.app")
    monkeypatch.setattr(maintenance, "verify_display", lambda: None)
    monkeypatch.setitem(sys.modules, "registry_image_digest", SimpleNamespace(resolve_image_digest=lambda image: image))
    monkeypatch.setenv("PRODUCTION_BACKEND_IMAGE", "new-image")
    monkeypatch.setenv("PRODUCTION_PRODUCER_IMAGE", "new-image")
    opened = set()
    mutations = []
    def cloud(*args):
        if args[:3] == ("run", "services", "describe"):
            return descriptor(args[3])
        if args[:3] == ("run", "revisions", "describe"):
            if args[3] == "candidate":
                return {"metadata": {"uid": "candidate-id"}, "spec": {"containers": [{"image": "front"}]}}
            return {"spec": {"containers": [{"image": "wrong" if failure == "wrong-image" else "new-image",
                "env": [] if args[3] in opened or failure == "missing-gate" else [
                    {"name": "SAWA_RELEASE_READ_ONLY", "value": "1"}]}]},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        assert args[:3] == ("run", "services", "update")
        assert "--remove-env-vars=SAWA_RELEASE_READ_ONLY" in args
        assert json.loads(path.read_text())["operations"][-1]["state"] == "started"
        mutations.append(args[3])
        if failure == "update-failed":
            raise RuntimeError("cloud update failed")
        opened.add(args[3])
    monkeypatch.setattr(maintenance, "cloud", cloud)
    if failure:
        with pytest.raises(RuntimeError):
            maintenance.open_writers()
        assert not record.get("writers_verified_open")
        if failure != "update-failed":
            assert not mutations
    else:
        maintenance.open_writers()
        assert set(mutations) == set(maintenance.WRITERS)
        assert record["writers_verified_open"] is True
