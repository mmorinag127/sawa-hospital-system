"""Schema destinations come only from registered, verified stg manifests."""
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import stg_replay_schema_targets as targets


def registry():
    return {"schema": 1, "active": "replay-first", "refresh": None, "snapshots": {
        identity: {"uri": "gs://sawa-stg-raw/stg-replay/" + identity + "/manifest.json",
                   "sha256": "a" * 64, "snapshot_at": "2026-10-28T00:00:00Z"}
        for identity in ("replay-first", "replay-second")}}


def test_only_hospital_databases_are_deduplicated():
    seen = []
    def read(identity):
        seen.append(identity)
        return {"id": identity, "components": {
            "order": {"database": "sawa_replay_owned"}, "shift": {"database": "do_not_migrate"}}}
    assert targets.registered_databases(registry(), read) == ["sawa_replay_owned"]
    assert seen == ["replay-first", "replay-second"]


@pytest.mark.parametrize("database", ["orders", "postgres", "production", "sawa_replay_x;DROP DATABASE orders", None])
def test_non_snapshot_destinations_are_rejected(database):
    with pytest.raises(targets.StagingConfigBlocked, match="database-rejected"):
        targets.registered_databases(registry(), lambda identity: {
            "id": identity, "components": {"order": {"database": database}}})


def test_manifest_identity_mismatch_is_rejected():
    with pytest.raises(targets.StagingConfigBlocked, match="identity-mismatch"):
        targets.registered_databases(registry(), lambda identity: {
            "id": "replay-unregistered", "components": {"order": {"database": "sawa_replay_owned"}}})


def test_service_configuration_rejects_secret_or_duplicate_routing(monkeypatch):
    valid = {targets.KEYS[0]: "gs://sawa-stg-raw/stg-replay/replay-first/manifest.json", targets.KEYS[1]: "a" * 64}
    entries = [{"name": key, "value": value} for key, value in valid.items()]
    descriptor = {"spec": {"template": {"spec": {"containers": [{"env": entries}]}}}}
    calls = []
    def describe(*args):
        calls.append(args)
        return descriptor
    monkeypatch.setattr(targets.transport, "_run_gcloud_json", describe)
    assert targets.service_configuration("worker-stg") == valid
    assert calls == [("run", "services", "describe", "worker-stg", "--project=sawahospitalsystem", "--region=asia-northeast2")]
    entries.append(entries[0])
    with pytest.raises(targets.StagingConfigBlocked, match="config-source-rejected"):
        targets.service_configuration("worker-stg")
    entries[:] = [{"name": targets.KEYS[0], "valueFrom": {}}]
    with pytest.raises(targets.StagingConfigBlocked, match="config-source-rejected"):
        targets.service_configuration("worker-stg")
    with pytest.raises(targets.StagingConfigBlocked, match="service-rejected"):
        targets.service_configuration("worker-prod")


def test_replay_environment_restores_original_context_even_on_failure(monkeypatch):
    for name in (*targets.KEYS, "K_SERVICE", "APP_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "original")
    with pytest.raises(RuntimeError, match="synthetic"):
        with targets.replay_environment({targets.KEYS[0]: "uri", targets.KEYS[1]: "digest"}):
            assert os.environ["K_SERVICE"] == "worker-stg"
            assert os.environ["APP_ENV"] == "stg"
            raise RuntimeError("synthetic")
    assert os.environ["APP_ENV"] == "original"
    assert all(name not in os.environ for name in (*targets.KEYS, "K_SERVICE"))
