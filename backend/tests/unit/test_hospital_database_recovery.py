import copy
import json
from pathlib import Path
import sys
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import restore_hospital_database as recovery


@pytest.fixture
def context(monkeypatch, tmp_path):
    values = {"RUNNER_TEMP": str(tmp_path), "GITHUB_SHA": "a" * 40,
              "GITHUB_REF": "refs/heads/release/prod-example", "GITHUB_RUN_ID": "30",
              "ORIGINAL_RUN": "10", "RECOVERY_RUN": "20"}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(recovery, "require_context", lambda _: "prod")
    targets = {name: {"revision": name + "-old", "uid": name,
                      "images": [name + "@sha256:" + "a" * 64]} for name in recovery.SERVICES}
    record = {"sha": values["GITHUB_SHA"], "ref": values["GITHUB_REF"], "run": "20",
              "original_run": "10", "instance": "orders-prod", "database_restored": True,
              "traffic_restored": True, "services_still_disabled": True, "services": targets,
              "scaling": {name: "auto" for name in recovery.SERVICES},
              "baseline_backup": "11", "post_update_backup": "12",
              "original_backup_configuration": {"enabled": True},
              "original_traffic": {name: [{"revisionName": name + "-new", "percent": 100}]
                                   for name in recovery.SERVICES}}
    (tmp_path / "recovery").mkdir()
    path = tmp_path / "recovery/database-recovery.json"
    path.write_text(json.dumps(record))
    services = {name: {"metadata": {"annotations": {
        "run.googleapis.com/scalingMode": "manual", "run.googleapis.com/manualInstanceCount": "0"}},
        "status": {"traffic": [{"revisionName": target["revision"], "percent": 100}]}}
        for name, target in targets.items()}
    calls = []

    def cloud(*args):
        calls.append(args)
        if args[:2] == ("auth", "list"):
            return [{"account": f"sawa-github-deploy-prod@{recovery.PROJECT}.iam.gserviceaccount.com"}]
        if args[:3] == ("sql", "backups", "describe"):
            return {"instance": "orders-prod", "status": "SUCCESSFUL", "id": args[3]}
        if args[:3] == ("sql", "instances", "describe"):
            return {"state": "RUNNABLE", "settings": {"backupConfiguration": {"enabled": True}}}
        if args[:3] == ("sql", "databases", "list"):
            return [{"name": "orders"}, {"name": "postgres"}]
        if args[:3] == ("run", "jobs", "list"):
            return []
        if args[:3] == ("run", "revisions", "describe"):
            name = next(name for name, target in targets.items() if target["revision"] == args[3])
            return {"metadata": {"uid": name, "labels": {"serving.knative.dev/service": name}},
                    "spec": {"containers": [{"name": "main", "image": targets[name]["images"][0]}]},
                    "status": {"imageDigest": targets[name]["images"][0],
                               "conditions": [{"type": "Ready", "status": "True"}]}}
        if args[:3] == ("run", "services", "describe"):
            return copy.deepcopy(services[args[3]])
        if args[:3] == ("run", "services", "update"):
            scaling = "automatic" if "--scaling=auto" in args else "manual"
            services[args[3]]["metadata"]["annotations"]["run.googleapis.com/scalingMode"] = scaling
            return None
        if args[:3] == ("run", "services", "update-traffic"):
            return None
        raise AssertionError(args)

    monkeypatch.setattr(recovery, "cloud", cloud)
    return path, record, services, calls, cloud


@pytest.mark.parametrize("field,value", [("database_restored", False), ("sha", "other"),
                                         ("traffic_restored", False), ("run", "19")])
def test_reopen_rejects_unproven_recovery(context, monkeypatch, field, value):
    path, record, _, calls, _ = context
    monkeypatch.setenv("DATABASE_RECOVERY_CONFIRMATION", "REOPEN_RESTORED_ORDERS_PROD")
    record[field] = value
    path.write_text(json.dumps(record))
    with pytest.raises(RuntimeError):
        recovery.reopen()
    assert not any(call[:3] == ("run", "services", "update") for call in calls)


def test_reopen_preserves_paused_transports(context, monkeypatch, tmp_path):
    _, _, _, calls, _ = context
    monkeypatch.setenv("DATABASE_RECOVERY_CONFIRMATION", "REOPEN_RESTORED_ORDERS_PROD")
    recovery.reopen()
    updates = [call[3] for call in calls if call[:3] == ("run", "services", "update")]
    assert updates == list(reversed(recovery.SERVICES))
    assert not any(call[0] in {"tasks", "scheduler"} for call in calls)
    receipt = json.loads((tmp_path / "database-reopen.json").read_text())
    assert receipt["business_flow_verified"] is False
    assert receipt["transports_resumed"] is False


def test_reopen_failure_stops_all_services(context, monkeypatch):
    _, _, services, calls, original = context
    monkeypatch.setenv("DATABASE_RECOVERY_CONFIRMATION", "REOPEN_RESTORED_ORDERS_PROD")

    def fail(*args):
        if args[:4] == ("run", "services", "update", "worker-prod") and "--scaling=auto" in args:
            raise RuntimeError("provider failed")
        return original(*args)

    monkeypatch.setattr(recovery, "cloud", fail)
    with pytest.raises(RuntimeError):
        recovery.reopen()
    assert all(service["metadata"]["annotations"]["run.googleapis.com/scalingMode"] == "manual"
               for service in services.values())
    assert len([call for call in calls if "--scaling=0" in call]) == 3


def test_finish_never_restores_database_or_enables_services(context, monkeypatch, tmp_path):
    _, _, _, calls, _ = context
    monkeypatch.setenv("DATABASE_RECOVERY_CONFIRMATION", "FINISH_RESTORED_ORDERS_PROD")
    recovery.finish()
    assert not any(call[:3] in {("sql", "backups", "restore"), ("run", "services", "update")}
                   for call in calls)
    receipt = json.loads((tmp_path / "database-recovery.json").read_text())
    assert receipt["run"] == "30" and receipt["continued_from"] == "20"
    assert receipt["services_still_disabled"] is True


def test_tagged_revision_blocks_reopening(context, monkeypatch):
    _, _, services, calls, _ = context
    monkeypatch.setenv("DATABASE_RECOVERY_CONFIRMATION", "REOPEN_RESTORED_ORDERS_PROD")
    services["worker-prod"]["status"]["traffic"].append({"revisionName": "other", "tag": "old"})
    with pytest.raises(RuntimeError, match="tagged"):
        recovery.reopen()
    assert not any(call[:3] == ("run", "services", "update") for call in calls)


@pytest.mark.parametrize("consumer", ["database", "job"])
def test_foreign_consumers_block_instance_restore(monkeypatch, consumer):
    attachment = f"{recovery.PROJECT}:{recovery.REGION}:orders-prod"
    job = {"spec": {"template": {"spec": {"template": {"metadata": {
        "annotations": {"run.googleapis.com/cloudsql-instances": attachment}}}}}}}
    monkeypatch.setattr(recovery, "cloud", lambda *args:
                        [{"name": "orders"}, {"name": "unrelated"}] if consumer == "database" else
                        ([{"name": "orders"}] if args[0] == "sql" else [job]))
    with pytest.raises(RuntimeError):
        recovery.assert_database_inventory()


def test_matching_backup_policy_never_patches(monkeypatch):
    mutation = Mock(side_effect=AssertionError("unexpected patch"))
    monkeypatch.setattr(recovery, "urlopen", mutation)
    recovery.restore_backup_configuration({"enabled": True}, {
        "settings": {"backupConfiguration": {"enabled": True}}})
    mutation.assert_not_called()


def test_backup_policy_patch_excludes_output_only_fields(monkeypatch):
    expected = {"enabled": True, "startTime": "18:00", "backupTier": "STANDARD",
                "transactionalLogStorageState": "CLOUD_STORAGE"}
    monkeypatch.setattr(recovery.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="token"))
    import io
    requests = []

    def respond(request, timeout):
        requests.append(json.loads(request.data))
        return io.BytesIO(b'{"targetId":"orders-prod","name":"operation"}')

    monkeypatch.setattr(recovery, "urlopen", respond)
    monkeypatch.setattr(recovery, "cloud", lambda *a: {
        "settings": {"backupConfiguration": expected}})
    recovery.restore_backup_configuration(expected, {"settings": {
        "settingsVersion": "12", "backupConfiguration": {"enabled": False}}})
    assert requests == [{"settings": {"settingsVersion": "12", "backupConfiguration": {
        "enabled": True, "startTime": "18:00"}}}]


@pytest.mark.parametrize("failure", [None, "backup", "restore"])
def test_restore_stops_ingress_and_preserves_post_update_backup_before_restore(context, monkeypatch, tmp_path, failure):
    _, record, services, calls, original = context
    monkeypatch.setenv("DATABASE_RECOVERY_CONFIRMATION", "RESTORE_ORDERS_PROD_DATABASE")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    current = {name: target["revision"] for name, target in record["services"].items()}
    monkeypatch.setenv("CURRENT_REVISIONS_JSON", json.dumps(current))
    for target in record["services"].values():
        target["scaling"] = "auto"
    baseline_record = {**record, "run": "10", "project": recovery.PROJECT, "region": recovery.REGION,
                       "rollback_strategy": "restore_database"}
    (tmp_path / "rollback").mkdir()
    (tmp_path / "rollback/hospital-rollback.json").write_text(json.dumps(baseline_record))
    (tmp_path / "rollback/orders-prod-recovery-point.json").write_text('{"id":"11"}')
    monkeypatch.setitem(sys.modules, "run_prod_schema_adoption", SimpleNamespace(
        production_database=lambda: SimpleNamespace(db_user="orders_app", db_password="not-logged")))
    connection = Mock()
    connection.execute.return_value.one.return_value = ("orders", "orders_app")
    connection.execute.return_value.scalar_one.return_value = 0
    manager = Mock()
    manager.__enter__ = Mock(return_value=connection)
    manager.__exit__ = Mock(return_value=False)
    engine = Mock()
    engine.connect.return_value = manager
    import sqlalchemy
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *args, **kwargs: engine)

    def provider(*args):
        if args[:3] == ("sql", "backups", "describe"):
            return {"id": "11", "instance": "orders-prod", "status": "SUCCESSFUL",
                    "description": "release-" + record["sha"] + "-10"}
        if args[:3] == ("sql", "instances", "describe"):
            return {"databaseVersion": "POSTGRES_15", "state": "RUNNABLE",
                    "settings": {"backupConfiguration": {"enabled": True}}}
        if args[:3] == ("run", "services", "list"):
            return [{"metadata": {"name": name}, "spec": {"template": {"metadata": {"annotations": {
                "run.googleapis.com/cloudsql-instances": recovery.PROJECT + ":" + recovery.REGION + ":orders-prod"
            }}}}} for name in recovery.SERVICES]
        if args[:3] in {("tasks", "queues", "list"), ("scheduler", "jobs", "list")}:
            return []
        if args[:3] == ("sql", "backups", "create"):
            calls.append(args)
            assert all(service["metadata"]["annotations"]["run.googleapis.com/scalingMode"] == "manual"
                       for service in services.values())
            if failure == "backup":
                raise RuntimeError("backup failed")
            return None
        if args[:3] == ("sql", "backups", "list"):
            return [{"id": "12", "status": "SUCCESSFUL",
                     "description": "before-restore-" + record["sha"] + "-30-1"}]
        if args[:3] == ("sql", "backups", "restore"):
            calls.append(args)
            assert (tmp_path / "database-recovery.json").exists()
            assert any(call[:3] == ("sql", "backups", "create") for call in calls)
            if failure == "restore":
                raise RuntimeError("restore failed")
            return None
        return original(*args)

    monkeypatch.setattr(recovery, "cloud", provider)
    if failure:
        with pytest.raises(RuntimeError):
            recovery.run()
    else:
        recovery.run()
        result = json.loads((tmp_path / "database-recovery.json").read_text())
        assert result["database_restored"] and result["traffic_restored"]
        assert result["services_still_disabled"] and not result["transports_resumed"]
    assert not any("--scaling=auto" in call for call in calls)
    if failure == "backup":
        assert not any(call[:3] == ("sql", "backups", "restore") for call in calls)
    engine.dispose.assert_called()
