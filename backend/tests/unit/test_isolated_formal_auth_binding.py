"""Real auth registration in owned formal schemas, not live Google proof."""
from pathlib import Path
import sys
from types import ModuleType
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text

import test_portal_google_auth as bare
from tests.contract import test_portal_google_auth as qualified
from tests.contract.auth_support import registered_hospital_operator
from src import db
from src.main import app


BOOTSTRAP = db.engine


def _stored(engine, user_id):
    with engine.connect() as connection:
        user = connection.execute(text("SELECT account, role, status FROM users WHERE id=:id"), {"id": user_id}).first()
        grants = connection.execute(text("SELECT system_key, enabled FROM user_system_access WHERE user_id=:id"), {"id": user_id}).all()
    return user, grants


@pytest.mark.parametrize("helper", [qualified, bare], ids=["qualified", "bare"])
def test_loaded_auth_helpers_register_and_cleanup_only_in_owned_schema(isolated_formal_schema, monkeypatch, helper):
    assert qualified is not bare
    assert Path(qualified.__file__).resolve() == Path(bare.__file__).resolve()
    assert qualified.db.engine is bare.db.engine is isolated_formal_schema
    assert db.SessionLocal.kw["bind"] is isolated_formal_schema
    account = f"isolated-alias-{uuid4().hex}@example.invalid"
    user_id = helper._seed_user(account)
    try:
        user, grants = _stored(isolated_formal_schema, user_id)
        assert tuple(user) == (account, "operator", "active")
        assert grants == [("hospital", True)]
        assert _stored(BOOTSTRAP, user_id) == (None, [])
    finally:
        helper._cleanup(user_id)
    assert _stored(isolated_formal_schema, user_id) == (None, [])
    assert _stored(BOOTSTRAP, user_id) == (None, [])

    client = TestClient(app)
    with registered_hospital_operator(monkeypatch) as operator:
        user, grants = _stored(isolated_formal_schema, operator.user_id)
        assert tuple(user) == (operator.account, "operator", "active")
        assert grants == [("hospital", True)]
        assert _stored(BOOTSTRAP, operator.user_id) == (None, [])
        assert client.get("/health/backlog", headers=operator.headers).status_code == 200
        assert client.get("/health/backlog").status_code == 401
        assert client.get("/system/db/download", headers=operator.headers).status_code == 403
        assert operator.verifier.called
    assert _stored(isolated_formal_schema, operator.user_id) == (None, [])
    assert _stored(BOOTSTRAP, operator.user_id) == (None, [])
    with registered_hospital_operator(monkeypatch, systems=("shift",)) as denied:
        response = client.get("/health/backlog", headers=denied.headers)
        assert response.status_code == 403
        assert response.json() == {"detail": "System access denied"}
    assert _stored(isolated_formal_schema, denied.user_id) == (None, [])
    assert _stored(BOOTSTRAP, denied.user_id) == (None, [])


@pytest.fixture
def restored_engines():
    originals = []
    yield originals
    for module, original in originals:
        assert module.engine is original


@pytest.fixture
def origin_probes(restored_engines, monkeypatch, tmp_path):
    independent = create_engine(f"sqlite:///{tmp_path / 'independent.sqlite'}")
    local = ModuleType("unprefixed_local_origin_probe")
    local.__file__, local.engine = __file__, BOOTSTRAP
    custom = ModuleType("custom_local_origin_probe")
    custom.__file__, custom.engine = __file__, independent
    external = ModuleType("tests.external_origin_probe")
    external.__file__, external.engine = str(tmp_path / "external.py"), BOOTSTRAP
    root_only = ModuleType("tests.root_directory_probe")
    root_only.__file__, root_only.engine = str(Path(__file__).resolve().parents[1]), BOOTSTRAP
    for module in (local, custom, external, root_only):
        restored_engines.append((module, module.engine))
        monkeypatch.setitem(sys.modules, module.__name__, module)
    try:
        yield local, custom, external, root_only, independent
    finally:
        independent.dispose()


def test_binding_requires_owned_file_origin_and_bootstrap_identity(origin_probes, isolated_formal_schema):
    local, custom, external, root_only, independent = origin_probes
    assert local.engine is isolated_formal_schema
    assert custom.engine is independent
    assert external.engine is BOOTSTRAP
    assert root_only.engine is BOOTSTRAP
