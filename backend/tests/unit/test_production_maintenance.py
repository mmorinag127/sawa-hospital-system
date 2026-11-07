import json
import io
import copy
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


@pytest.mark.parametrize("change", [None, "new-tag", "changed-revision", "changed-uid", "retired"])
def test_captured_tags_are_identity_bound_and_retired_without_guessing(monkeypatch, change):
    route = {"tag": "source-export", "revision": "retained", "uid": "retained-id", "images": ["old-image"]}
    record = {"tagged_routes": {"worker-prod": [route]}}
    service = descriptor("current")
    if change != "retired":
        service["status"]["traffic"].append({"tag": "other" if change == "new-tag" else "source-export",
            "revisionName": "different" if change == "changed-revision" else "retained"})
    monkeypatch.setattr(maintenance, "cloud", lambda *_args: {
        "metadata": {"uid": "changed" if change == "changed-uid" else "retained-id"},
        "spec": {"containers": [{"image": "old-image"}]}})
    if change in {"new-tag", "changed-revision", "changed-uid"}:
        with pytest.raises(RuntimeError):
            maintenance.verify_captured_tags(record, "worker-prod", service, allow_retired=True)
    else:
        assert maintenance.verify_captured_tags(record, "worker-prod", service, allow_retired=True) == (
            [] if change == "retired" else [route])


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


def test_writer_tag_resolves_to_exact_serving_digest_before_reopening(monkeypatch, tmp_path):
    from types import SimpleNamespace
    record_fixture(monkeypatch, tmp_path)
    digest = 'registry/backend@sha256:' + 'c' * 64
    resolve = Mock(return_value=digest)
    monkeypatch.setitem(sys.modules, 'registry_image_digest', SimpleNamespace(resolve_image_digest=resolve))
    updates = []
    def cloud(*args):
        if args[1] == 'revisions':
            return {'spec': {'containers': [{'image': digest, 'env': [{'name': 'SAWA_RELEASE_READ_ONLY', 'value': '1'}]}]}}
        if args[2] == 'update':
            updates.append(args)
            return {}
        service = descriptor('new-worker')
        service['metadata']['annotations'] = {'run.googleapis.com/scalingMode': 'automatic' if updates else 'manual', 'run.googleapis.com/manualInstanceCount': '0'}
        return service
    monkeypatch.setattr(maintenance, 'cloud', cloud)
    maintenance.resume_writer('worker-prod', 'registry/backend:approved')
    resolve.assert_called_once_with('registry/backend:approved')
    assert len(updates) == 1 and '--scaling=auto' in updates[0]


def test_workflow_orders_maintenance_before_migration_and_candidate_before_promotion():
    root = Path(__file__).resolve().parents[3]
    source = (root / ".github/workflows/deploy-prod.yml").read_text()
    assert source.index("production_maintenance.py begin") < source.index("prod-db-bootstrap-gate:")
    import inspect
    begin_source = inspect.getsource(maintenance.begin)
    assert begin_source.index('preflight(capture_only=True)') < begin_source.index('capture()')
    assert begin_source.index('capture()') < begin_source.index('show(image)') < begin_source.index('stop_writers()')
    assert source.index("production_maintenance.py drain") < source.index("gcloud sql backups create", source.index("prod-db-bootstrap-gate:"))
    assert source.index("gcloud sql backups create", source.index("prod-db-bootstrap-gate:")) < source.index("python scripts/run_prod_schema_adoption.py\n")
    continuation = source.split('if [[ -n "$MIGRATED_CONTINUATION" ]]; then')[-1].split('exit 0')[0]
    assert 'run_prod_schema_adoption.py --verify-only' in continuation
    assert 'gcloud sql backups create' not in continuation
    assert source.index("Verify deferred live portal authentication") < source.index("production_maintenance.py promote")
    assert source.index("production_maintenance.py promote") < source.index("python scripts/activate_hospital_delivery.py")
    assert "python3 scripts/production_maintenance.py hold-on-failure" in source


@pytest.mark.parametrize('missing', [False, True])
def test_runtime_act_as_checks_every_distinct_runtime_before_changes(monkeypatch, missing):
    import hospital_production_preflight as preflight
    accounts = [f'{name}@sawahospitalsystem.iam.gserviceaccount.com' for name in ('web', 'worker', 'producer')]
    descriptors = {name: {'spec': {'template': {'spec': {'serviceAccountName': account}}}}
                   for name, account in zip(maintenance.SERVICES, accounts)}
    monkeypatch.setattr(preflight.subprocess, 'check_output', lambda *_a, **_k: 'private-test-token')
    seen = []
    def request(req, timeout):
        seen.append(req.full_url)
        assert json.loads(req.data) == {'permissions': ['iam.serviceAccounts.actAs']}
        return io.BytesIO(json.dumps({'permissions': [] if missing else ['iam.serviceAccounts.actAs']}).encode())
    monkeypatch.setattr(preflight, 'urlopen', request)
    if missing:
        with pytest.raises(RuntimeError, match='actAs permission missing'):
            preflight.require_runtime_act_as(descriptors)
    else:
        preflight.require_runtime_act_as(descriptors)
        assert len(seen) == 3


@pytest.mark.parametrize('state', ['original', 'maintenance', 'foreign', 'uid', 'digest', 'db-ran', 'permission'])
def test_same_run_begin_preserves_identities_and_phase_before_changes(monkeypatch, tmp_path, state):
    import hospital_production_preflight as preflight
    path, record = record_fixture(monkeypatch, tmp_path)
    record.update(database_drained=False, portal_bootstrap_complete=False)
    monkeypatch.setattr(maintenance, 'context', lambda: path)
    monkeypatch.setenv('GITHUB_REPOSITORY', 'owner/repo')
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    jobs = [{'name': 'prod-db-bootstrap-gate', 'status': 'completed', 'conclusion': 'success'}] if state == 'db-ran' else []
    monkeypatch.setattr(maintenance.subprocess, 'check_output', lambda *_a, **_k: json.dumps([{'jobs': jobs}]))
    monkeypatch.setattr(maintenance, 'download_record', lambda *_a, **_k: True)
    def cloud(*args):
        if args[1] == 'revisions':
            return {'metadata': {'uid': 'changed' if state == 'uid' else 'original'},
                    'spec': {'containers': [{'image': 'changed' if state == 'digest' else 'original'}]}}
        name = args[3]
        return descriptor(('old-web-prod' if state == 'original' else
                           'foreign' if state == 'foreign' else 'maintenance')
                          if name == 'web-prod' else 'old-' + name)
    monkeypatch.setattr(maintenance, 'cloud', cloud)
    permission = Mock(side_effect=RuntimeError('permission') if state == 'permission' else None)
    monkeypatch.setattr(preflight, 'require_runtime_act_as', permission)
    show, stop, verify = Mock(), Mock(), Mock()
    monkeypatch.setattr(maintenance, 'show', show)
    monkeypatch.setattr(maintenance, 'stop_writers', stop)
    monkeypatch.setattr(maintenance, 'verify_display', verify)
    if state in {'foreign', 'uid', 'digest', 'db-ran', 'permission'}:
        with pytest.raises(RuntimeError):
            maintenance.begin('image', '')
        show.assert_not_called()
        stop.assert_not_called()
    else:
        maintenance.begin('image', '')
        assert show.call_count == (state == 'original')
        assert verify.call_count == (state == 'maintenance')
        stop.assert_called_once()


@pytest.mark.parametrize('mismatch', [None, 'uid', 'image'])
def test_maintenance_display_checks_saved_revision_identity(monkeypatch, tmp_path, mismatch):
    _, record = record_fixture(monkeypatch, tmp_path)
    record.update(maintenance_uid='static-uid', maintenance_image='static-image')
    def cloud(*args):
        if args[1] == 'services':
            return descriptor()
        return {'metadata': {'uid': 'changed' if mismatch == 'uid' else 'static-uid'},
                'spec': {'containers': [{'image': 'changed' if mismatch == 'image' else 'static-image'}]}}
    monkeypatch.setattr(maintenance, 'cloud', cloud)
    response = MagicMock()
    response.__enter__.return_value = response
    response.code = 503
    response.headers = {'X-Sawa-Maintenance': '1'}
    request = Mock(return_value=response)
    monkeypatch.setattr(maintenance, 'urlopen', request)
    if mismatch:
        with pytest.raises(RuntimeError, match='identity changed'):
            maintenance.verify_display()
        request.assert_not_called()
    else:
        maintenance.verify_display()
        assert request.call_count == 3


@pytest.mark.parametrize('failure', [None, 'nested', 'actor', 'migration', 'source', 'uid', 'image', 'permission', 'artifact'])
def test_cross_run_start_uses_original_build_and_preserves_original_record(monkeypatch, tmp_path, failure):
    import hospital_production_preflight as preflight
    import restore_hospital_database as restore
    path = tmp_path / 'hospital-maintenance.json'
    sha = 'b' * 40
    image = f'{maintenance.REGION}-docker.pkg.dev/{maintenance.PROJECT}/backend/frontend@sha256:' + 'c' * 64
    original = {'run': '99', 'sha': sha, 'ref': 'refs/heads/release/prod-old',
                'services': {name: {'revision': 'old-' + name, 'uid': 'old-uid', 'images': ['old-image']}
                             for name in maintenance.SERVICES}, 'tagged_routes': {},
                'maintenance_revision': 'maintenance', 'maintenance_visible': True,
                'database_drained': False, 'queues': {}, 'schedules': {}}
    rollback = {key: copy.deepcopy(original[key]) for key in ('run', 'sha', 'ref', 'services')}
    rollback['rollback_strategy'] = 'restore_database'
    if failure == 'nested':
        initial = copy.deepcopy(original)
        initial['run'] = '88'
        original['maintenance_owner_run'] = '88'
        original['resume_origin'] = {'run': '88', 'sha': sha, 'maintenance': initial}
    if failure == 'artifact':
        rollback['sha'] = 'd' * 40
    metadata = {'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'failure',
                'path': '.github/workflows/deploy-prod.yml', 'actor': {'login': 'other' if failure == 'actor' else 'owner'},
                'triggering_actor': {'login': 'owner'}, 'head_branch': 'release/prod-old',
                'head_sha': sha, 'run_attempt': 1}
    jobs = [{'name': 'production-approval', 'conclusion': 'failure'},
            {'name': 'build-frontend', 'conclusion': 'success', 'id': 10}]
    jobs += [{'name': name, 'conclusion': 'success' if failure == 'migration' else 'skipped'}
             for name in ('prod-db-bootstrap-gate', 'deploy-backend', 'deploy-frontend')]
    monkeypatch.setattr(maintenance, 'context', lambda: path)
    for key, value in {'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_RUN_ID': '123',
                       'GITHUB_SHA': 'a' * 40, 'GITHUB_REF': 'refs/heads/release/prod-new',
                       'ROLLBACK_STRATEGY': 'restore_database'}.items():
        monkeypatch.setenv(key, value)
    def output(command, **kwargs):
        if command[:2] == ['git', 'diff']:
            return 'backend/src/changed.py' if failure == 'source' else ''
        if command[:3] == ['gh', 'run', 'view']:
            assert command[3] == ('88' if failure == 'nested' else '99')
            return ' original prod-frontend-' + sha[:12] + '-20261107-070238-maintenance: digest: sha256:' + 'c' * 64 + ' size: 3042\n'
        if '/jobs?' in command[-1]:
            return json.dumps([{'jobs': jobs}])
        return json.dumps(metadata)
    def run(command, **kwargs):
        if command[:3] == ['gh', 'run', 'download']:
            destination = Path(command[command.index('--dir') + 1])
            (destination / 'hospital-maintenance.json').write_text(json.dumps(original))
            (destination / 'hospital-rollback.json').write_text(json.dumps(rollback))
        else:
            assert command[:3] == ['git', 'merge-base', '--is-ancestor']
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(maintenance.subprocess, 'check_output', output)
    monkeypatch.setattr(maintenance.subprocess, 'run', run)
    monkeypatch.setattr(restore, 'assert_database_inventory', lambda: None)
    monkeypatch.setattr(preflight, 'require_runtime_act_as',
                        Mock(side_effect=RuntimeError('permission') if failure == 'permission' else None))
    calls = []
    def cloud(*args):
        calls.append(args)
        assert args[2] in {'describe', 'list'}
        if args[2] == 'list':
            return [{'metadata': {'name': name}, 'spec': {'template': {'metadata': {'annotations': {
                'run.googleapis.com/cloudsql-instances': f'{maintenance.PROJECT}:{maintenance.REGION}:orders-prod'}}}}}
                    for name in maintenance.SERVICES]
        if args[1] == 'services':
            name = args[3]
            result = descriptor('maintenance' if name == 'web-prod' else 'old-' + name)
            result['metadata']['labels'] = {'sawa-maintenance-run': '88' if failure == 'nested' else '99'}
            return result
        if args[3] == 'maintenance':
            return {'metadata': {'uid': 'static-uid'}, 'spec': {'containers': [{
                'image': 'wrong-image' if failure == 'image' else image,
                'command': ['node'], 'args': ['/sawa-maintenance/server.cjs']}]}}
        return {'metadata': {'uid': 'wrong-uid' if failure == 'uid' else 'old-uid'},
                'spec': {'containers': [{'image': 'old-image'}]}}
    monkeypatch.setattr(maintenance, 'cloud', cloud)
    monkeypatch.setattr(maintenance, 'verify_display', Mock())
    if failure not in {None, 'nested'}:
        with pytest.raises(RuntimeError):
            maintenance.resume_start('99')
        assert not path.exists()
    else:
        maintenance.resume_start('99')
        saved = json.loads(path.read_text())
        assert saved['run'] == '123'
        assert saved['resume_origin']['maintenance'] == original
        assert saved['services'] == original['services']
        assert saved['maintenance_image'] == image
        assert saved['maintenance_owner_run'] == ('88' if failure == 'nested' else '99')


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


@pytest.mark.parametrize('state', ['empty', 'pristine', 'marker', 'stopped', 'uid'])
def test_partial_start_artifact_only_allows_pristine_restart(monkeypatch, tmp_path, state):
    monkeypatch.setattr(maintenance, 'require_context', lambda _env: 'prod')
    for key, value in {'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '2', 'GITHUB_SHA': 'source',
                       'GITHUB_REF': 'refs/heads/release/prod-test', 'RUNNER_TEMP': str(tmp_path),
                       'GITHUB_REPOSITORY': 'owner/repository'}.items():
        monkeypatch.setenv(key, value)
    artifacts = [] if state == 'empty' else [{'name': 'hospital-maintenance-start-123-1', 'expired': False}]
    monkeypatch.setattr(maintenance.subprocess, 'check_output', lambda *_a, **_k: json.dumps([{'artifacts': artifacts}]))
    def download(args, **kwargs):
        destination = Path(args[args.index('--dir') + 1])
        record = {'run': '123', 'sha': 'source', 'ref': 'refs/heads/release/prod-test',
                  'services': {name: {'revision': 'old', 'uid': 'uid', 'images': ['image']}
                               for name in maintenance.SERVICES}}
        (destination / 'hospital-rollback.json').write_text(json.dumps(record))
    monkeypatch.setattr(maintenance.subprocess, 'run', download)
    def cloud(*args):
        assert args[2] == 'describe'
        if args[1] == 'revisions':
            return {'metadata': {'uid': 'changed' if state == 'uid' else 'uid'},
                    'spec': {'containers': [{'image': 'image'}]}}
        service = descriptor('old')
        if state == 'marker':
            service['metadata']['labels'] = {'sawa-maintenance-run': '123'}
        if state == 'stopped':
            service['metadata']['annotations']['run.googleapis.com/scalingMode'] = 'manual'
        return service
    monkeypatch.setattr(maintenance, 'cloud', cloud)
    if state in {'empty', 'pristine'}:
        assert maintenance.download_record('start', allow_pristine=True) is False
    else:
        with pytest.raises(RuntimeError):
            maintenance.download_record('start', allow_pristine=True)
    assert not (tmp_path / 'hospital-maintenance.json').exists()


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
