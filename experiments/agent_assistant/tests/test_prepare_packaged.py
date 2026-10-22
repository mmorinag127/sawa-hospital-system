import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from experiments.agent_assistant.prepare_packaged import prepare
from experiments.agent_assistant.runtime import source_manifest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "image"
    shutil.copytree(ROOT / "agent_runtime/baselines", root / "baselines")
    shutil.copyfile(ROOT / "agent_runtime/release.json", root / "release.json")
    backend = root / "native/hospital/backend"
    backend.parent.mkdir(parents=True)
    backend.symlink_to(ROOT / "backend", target_is_directory=True)
    return root


def rows(path):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {name: sorted(db.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall(), key=repr)
                for name in tables if name != "alembic_version"}


def test_formal_migration_preserves_all_data_and_seals_only_hospital(bundle, monkeypatch):
    from sqlalchemy import create_engine
    from src.maintenance.formal_migration_schema import require_formal_head

    baseline = bundle / "baselines/hospital"
    database = baseline / "database.sqlite3"
    before = rows(database)
    release_before = json.loads((bundle / "release.json").read_text())
    source_before = (ROOT / "agent_runtime/baselines/hospital/database.sqlite3").read_bytes()
    outsider = bundle.parent / "must-not-be-opened.sqlite3"
    for key in ("DB_URI", "DATABASE_URL", "ALEMBIC_DB_URI"):
        monkeypatch.setenv(key, "sqlite:///" + str(outsider))
    monkeypatch.setenv("DB_HOST", "must-not-be-used")
    prepare(bundle, sys.executable)
    engine = create_engine("sqlite:///" + str(database))
    with engine.connect() as connection:
        require_formal_head(connection)
    engine.dispose()
    after = rows(database)
    assert all(after[name] == values for name, values in before.items())
    assert set(after) - set(before) == {"portal_sessions", "portal_login_attempts"}
    assert after["portal_sessions"] == after["portal_login_attempts"] == []
    release = json.loads((bundle / "release.json").read_text())
    assert release["domains"]["hospital"]["baseline_files"] == source_manifest(baseline)
    for domain in ("shift", "school-lunch"):
        assert release["domains"][domain] == release_before["domains"][domain]
        assert source_manifest(bundle / "baselines" / domain) == release["domains"][domain]["baseline_files"]
    assert not outsider.exists()
    assert (ROOT / "agent_runtime/baselines/hospital/database.sqlite3").read_bytes() == source_before
    prepare(bundle, sys.executable)
    assert rows(database) == after


@pytest.mark.parametrize("domain", ["hospital", "shift", "school-lunch"])
def test_changed_baseline_cannot_be_resealed(bundle, domain, monkeypatch):
    before = (bundle / "release.json").read_bytes()
    (bundle / "baselines" / domain / "unexpected.txt").write_text("changed")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(RuntimeError, match="differs from the declared source"):
        prepare(bundle, sys.executable)
    assert not calls
    assert (bundle / "release.json").read_bytes() == before


def test_failed_formal_migration_does_not_publish_a_release_hash(bundle, monkeypatch):
    before = (bundle / "release.json").read_bytes()
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        prepare(bundle, sys.executable)
    assert (bundle / "release.json").read_bytes() == before


def test_build_prepares_baseline_before_native_startup_smoke():
    dockerfile = (ROOT / "agent_runtime/Dockerfile").read_text()
    assert dockerfile.index("-m experiments.agent_assistant.prepare_packaged") < dockerfile.index("-m experiments.agent_assistant.check_packaged")


def test_migration_cannot_reseal_changes_to_original_documents(bundle, monkeypatch):
    before = (bundle / "release.json").read_bytes()
    def change_source(*args, **kwargs):
        (bundle / "baselines/hospital/facility-master.json").write_text("{}")
    monkeypatch.setattr(subprocess, "run", change_source)
    with pytest.raises(RuntimeError, match="changed non-database"):
        prepare(bundle, sys.executable)
    assert (bundle / "release.json").read_bytes() == before
