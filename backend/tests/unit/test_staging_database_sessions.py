"""Exercise the actual database/session API against isolated synthetic stores."""
import pytest
from sqlalchemy import create_engine, text
from src import db, staging_data as datasets


def test_session_factory_control_auth_and_direct_engine_share_the_selected_target(monkeypatch, tmp_path):
    engines = {name: create_engine("sqlite:///" + str(tmp_path / (name + ".sqlite"))) for name in ("test", "replay-case")}
    for name, engine in engines.items():
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE selection_probe (value TEXT)"))
            connection.execute(text("INSERT INTO selection_probe VALUES (:value)"), {"value": name})
    monkeypatch.setattr(db, "engine", engines["test"])
    monkeypatch.setattr(db, "_replay_engine", lambda _uri: engines["replay-case"])
    monkeypatch.setattr(datasets, "database_uri", lambda uri, _feature: uri)
    monkeypatch.setattr(datasets, "manifest", lambda: {"id": "replay-case"})
    monkeypatch.setattr(datasets, "manifest_for", lambda identity: _selected_fixture(datasets.manifest(), identity))
    monkeypatch.setenv("K_SERVICE", "worker-stg")
    monkeypatch.delenv("APP_ENV", raising=False)
    with datasets.dataset_context("replay-case"):
        with db.session_scope() as session:
            assert session.execute(text("SELECT value FROM selection_probe")).scalar_one() == "replay-case"
            session.execute(text("UPDATE selection_probe SET value='replay-change'"))
        with db.control_session_scope() as session:
            assert session.execute(text("SELECT value FROM selection_probe")).scalar_one() == "test"
        with db.get_engine().connect() as connection:
            assert connection.execute(text("SELECT value FROM selection_probe")).scalar_one() == "replay-change"
    with db.SessionLocal() as session:
        assert session.execute(text("SELECT value FROM selection_probe")).scalar_one() == "test"
    for engine in engines.values():
        engine.dispose()


def _selected_fixture(value, identity):
    if value["id"] != identity:
        raise datasets.DatasetUnavailable("staging_dataset_snapshot_unavailable")
    return value
