import base64
import os
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from src.db import DB_URI, engine
from src import db as runtime_db
import src.models  # noqa: F401


@sa.event.listens_for(engine, 'connect')
def identify_test_bootstrap_connection(_connection, record):
    record.info['sawa_impact_bootstrap_database'] = engine.url.database


@sa.event.listens_for(sa.pool.Pool, 'checkout')
def audit_test_database_checkout(_connection, _record, _proxy):
    sys.audit('sawa.test.database-checkout', _record.info.get('sawa_impact_bootstrap_database'), _connection)


_TESTS_ROOT = (Path(__file__).resolve().parent / "tests").resolve()


# Keep the existing test suite behavior stable while production defaults fail closed.
os.environ.setdefault("AUTH_DISABLED", "true")
os.environ.setdefault("OPERATOR_USER", "operator")
os.environ.setdefault("OPERATOR_PASSWORD", "operator-pass")
os.environ.setdefault("ADMIN_USER", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin-pass")


@pytest.fixture(autouse=True)
def restore_test_auth_environment():
    """Restore after nested actor contexts and the test's monkeypatch teardown."""
    keys = ("AUTH_DISABLED", "AUTH_PROVIDER")
    before = {key: os.environ.get(key) for key in keys}
    yield
    for key, value in before.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

with pytest.MonkeyPatch.context() as target:
    target.setenv("ALEMBIC_DB_URI", DB_URI)
    config = Config(str(Path(__file__).resolve().parent / "alembic.ini"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def _basic_auth_headers(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def operator_auth_headers() -> dict[str, str]:
    return _basic_auth_headers(
        os.environ["OPERATOR_USER"],
        os.environ["OPERATOR_PASSWORD"],
    )


@pytest.fixture
def admin_auth_headers() -> dict[str, str]:
    return _basic_auth_headers(
        os.environ["ADMIN_USER"],
        os.environ["ADMIN_PASSWORD"],
    )


@pytest.fixture(scope="session")
def isolated_formal_sqlite_template(tmp_path_factory):
    from tests.integration.sqlite_fixture_template import migrated_sqlite_template
    return migrated_sqlite_template(tmp_path_factory,
        alembic_ini=Path(__file__).resolve().parent / "alembic.ini",
        name="isolated-formal-schema")


@pytest.fixture(scope="session")
def delivery_sqlite_template(tmp_path_factory):
    from tests.integration.sqlite_fixture_template import file_digest

    template = tmp_path_factory.mktemp("delivery-empty-schema") / "empty.sqlite3"
    sys.audit('sawa.test.generated-sqlite-template', str(template.parent))
    builder = sa.create_engine(f"sqlite:///{template}")
    try:
        runtime_db.Base.metadata.create_all(builder)
    finally:
        builder.dispose()
    return template, file_digest(template)


@pytest.fixture
def isolated_formal_schema(tmp_path, monkeypatch, request):
    """Own every row/schema per case; never clear another test's retained history."""
    admin = isolated = None
    created = False
    database = None
    template = template_digest = None
    uri = os.environ.get("C2_ISOLATED_PG_URI")
    try:
        if uri is None:
            url = sa.URL.create("sqlite", database=str(tmp_path / "isolated-formal.sqlite3"))
            from tests.integration.sqlite_fixture_template import (
                file_digest, formal_schema_source_digest, sqlite_engine_from_template,
            )
            template, template_digest, schema_source = request.getfixturevalue("verified_formal_sqlite_template")
            if formal_schema_source_digest() != schema_source:
                raise RuntimeError("Formal migration source changed after template verification")
            isolated = sqlite_engine_from_template(template, Path(url.database),
                                                  expected_digest=template_digest)
        else:
            if not uri.strip() or uri != uri.strip():
                raise RuntimeError("C2_ISOLATED_PG_URI must be explicit and nonblank")
            url = sa.make_url(uri)
            assert url.drivername == "postgresql+psycopg2"
            assert url.host is None and url.port is None and url.database == "postgres"
            assert url.username == "mmorinag" and url.password is None
            assert set(url.query) == {"host", "port"}
            profiles = {
                "55469": (Path("/private/tmp/sawa-c2-hospital-catalog-20261006/pg15"), 15),
                "55470": (Path("/private/tmp/sawa-c2-hospital-catalog-20261006/pg16"), 16),
            }
            assert url.query["port"] in profiles
            root, major = profiles[url.query["port"]]
            assert url.query["host"] == str(root / "socket")
            assert (root / "socket").resolve() == root / "socket"
            assert (root / "OWNER").read_text().strip() == str(Path(__file__).resolve().parents[1])
            admin = sa.create_engine(url, isolation_level="AUTOCOMMIT")
            database = "c2_case_" + uuid4().hex
            with admin.connect() as connection:
                assert Path(connection.exec_driver_sql("SHOW data_directory").scalar_one()).resolve() == root / "pgdata"
                assert connection.exec_driver_sql("SHOW port").scalar_one() == url.query["port"]
                assert connection.exec_driver_sql("SHOW unix_socket_directories").scalar_one() == str(root / "socket")
                assert connection.exec_driver_sql("SHOW listen_addresses").scalar_one() == ""
                assert int(connection.exec_driver_sql("SHOW server_version_num").scalar_one()) // 10000 == major
                assert connection.exec_driver_sql("SELECT current_user").scalar_one() == url.username
                assert not connection.execute(sa.text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": database}).scalar()
                connection.exec_driver_sql('CREATE DATABASE "' + database + '"')
                created = True
            url = url.set(database=database)
        if isolated is None:
            isolated = sa.create_engine(url)
        for name in ("DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER"):
            monkeypatch.delenv(name, raising=False)
        target = url.render_as_string(hide_password=False)
        monkeypatch.setenv("DB_URI", target)
        monkeypatch.setenv("ALEMBIC_DB_URI", target)
        if uri is not None:
            config = Config(str(Path(__file__).resolve().parent / "alembic.ini"))
            with isolated.connect() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
        if uri is not None:
            from src.maintenance.formal_migration_schema import require_formal_head
            with isolated.connect() as connection:
                require_formal_head(connection)
        if isolated.dialect.name == "sqlite":
            isolated.dispose()
            @sa.event.listens_for(isolated, "connect")
            def require_foreign_keys(connection, _record):
                connection.execute("PRAGMA foreign_keys=ON")
        # Existing services hold imported engine aliases; session_scope uses this module's globals.
        monkeypatch.setattr(runtime_db, "SessionLocal", sessionmaker(isolated, autoflush=False, autocommit=False, future=True))
        for module in tuple(sys.modules.values()):
            if module is None or getattr(module, "engine", None) is not engine:
                continue
            name = getattr(module, "__name__", "")
            eligible = name.startswith("src.") or module is request.module
            if not eligible and getattr(module, "__file__", None):
                source = Path(module.__file__).resolve()
                eligible = source != _TESTS_ROOT and source.is_relative_to(_TESTS_ROOT)
            if eligible:
                monkeypatch.setattr(module, "engine", isolated)
        monkeypatch.setitem(sys.modules, "src.db", runtime_db)
        import src
        monkeypatch.setattr(src, "db", runtime_db)
        yield isolated
    finally:
        if isolated is not None:
            isolated.dispose()
        if template is not None:
            assert file_digest(template) == template_digest, "formal SQLite template was modified"
        if admin is not None:
            try:
                if created:
                    with admin.connect() as connection:
                        connection.exec_driver_sql('DROP DATABASE "' + database + '"')
            finally:
                admin.dispose()


@pytest.fixture(scope="session")
def verified_formal_sqlite_template(isolated_formal_sqlite_template):
    """Validate once; each case must receive these exact verified database bytes."""
    from tests.integration.sqlite_fixture_template import file_digest, formal_schema_source_digest
    from src.maintenance.formal_migration_schema import require_formal_head

    template = isolated_formal_sqlite_template
    before = file_digest(template)
    schema_source = formal_schema_source_digest()
    verifier = sa.create_engine(f"sqlite:///file:{template}?mode=ro&uri=true")
    try:
        with verifier.connect() as connection:
            require_formal_head(connection)
    finally:
        verifier.dispose()
    if file_digest(template) != before:
        raise RuntimeError("Formal schema verification modified the SQLite template")
    if formal_schema_source_digest() != schema_source:
        raise RuntimeError("Formal migration source changed during template verification")
    return template, before, schema_source
