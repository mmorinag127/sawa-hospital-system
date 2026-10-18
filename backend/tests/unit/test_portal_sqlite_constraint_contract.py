"""Real SQLite schema and bootstrap refusal, without touching an operational DB."""
import ast
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from src.maintenance import portal_access_bootstrap_schema as schema
from src.maintenance.formal_migration_schema import read_catalog
from src.services.portal_access_bootstrap_service import (
    PortalAccessBootstrapError,
    run_portal_access_bootstrap_gate,
)


CANONICAL = "system_key IN ('hospital', 'shift', 'school-lunch')"
NAME = "ck_user_system_access_system_key"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def owned(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'bootstrap.sqlite3'}"
    engine = sa.create_engine(url)

    def fk(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    sa.event.listen(engine, "connect", fk)
    for key in ("DB_URI", "DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ALEMBIC_DB_URI", url)
    config = Config(str(ROOT / "alembic.ini"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO users(id,account,role,status,created_at) VALUES('retained','retained@example.invalid','operator','inactive',CURRENT_TIMESTAMP)")
        connection.exec_driver_sql("INSERT INTO audit_logs(id,actor,action,target,created_at) VALUES('retained','test:owned','retained','retained',CURRENT_TIMESTAMP)")
    try:
        yield engine
    finally:
        engine.dispose()
        sa.event.remove(engine, "connect", fk)


def replace_access(engine, checks):
    # These are exclusively this test's fresh migrated tables, not a repair path.
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE user_system_access")
        constraints = ", " + ", ".join(checks) if checks else ""
        connection.exec_driver_sql(
            "CREATE TABLE user_system_access (user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
            "system_key VARCHAR NOT NULL, enabled BOOLEAN DEFAULT 1 NOT NULL, "
            f"PRIMARY KEY(user_id,system_key){constraints})"
        )


def snapshot(engine):
    with engine.connect() as connection:
        rows = {}
        for table in sa.inspect(connection).get_table_names():
            quoted = connection.dialect.identifier_preparer.quote(table)
            rows[table] = sorted(repr(tuple(row)) for row in connection.exec_driver_sql(f"SELECT * FROM {quoted}"))
        return json.dumps(read_catalog(connection), sort_keys=True, default=str), rows


def bootstrap(connection):
    return run_portal_access_bootstrap_gate(
        connection,
        bootstrap_admin_email="bootstrap@example.invalid",
        deploy_verification_email="verification@example.invalid",
    )


def test_actual_formal_0026_constraint_and_values_are_preserved(owned):
    engine = owned
    with engine.begin() as connection:
        checks = sa.inspect(connection).get_check_constraints("user_system_access")
        assert checks == [{"sqltext": CANONICAL, "name": NAME}]
        assert schema._has_canonical_system_check(connection, checks)
        assert bootstrap(connection).bootstrap_performed
        for key in schema.SYSTEM_KEYS:
            connection.execute(sa.text("INSERT INTO user_system_access VALUES('retained',:key,1)"), {"key": key})
        for invalid in ("other", "Hospital", "", "hospital ", "HOSPITAL"):
            with pytest.raises(sa.exc.IntegrityError):
                with connection.begin_nested():
                    connection.execute(sa.text("INSERT INTO user_system_access VALUES('retained',:key,1)"), {"key": invalid})
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT system_key FROM user_system_access WHERE user_id='retained' ORDER BY system_key").scalars().all() == sorted(schema.SYSTEM_KEYS)


@pytest.mark.parametrize("expression", [
    CANONICAL,
    "  system_key  in  ( 'hospital' , 'shift' , 'school-lunch' )  ",
    '"system_key" IN (\'hospital\',\'shift\',\'school-lunch\')',
    "[system_key] IN ('hospital', 'shift', 'school-lunch')",
    "`system_key` IN ('hospital', 'shift', 'school-lunch')",
    "SYSTEM_KEY\nIN\n('hospital',\n'shift',\n'school-lunch')",
])
def test_ordinary_canonical_formatting_accepts_only_canonical_values(owned, expression):
    replace_access(owned, [f"CONSTRAINT {NAME} CHECK ({expression})"])
    with owned.begin() as connection:
        assert bootstrap(connection).bootstrap_performed
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.exec_driver_sql("INSERT INTO user_system_access VALUES('retained','unregistered-system',1)")


@pytest.mark.parametrize("checks", [
    [f"CONSTRAINT {NAME} CHECK ({CANONICAL} OR 1=1)"],
    [f"CONSTRAINT {NAME} CHECK ({CANONICAL} AND enabled=1)"],
    [f"CONSTRAINT {NAME} CHECK (lower(system_key) IN ('hospital','shift','school-lunch'))"],
    [f"CONSTRAINT {NAME} CHECK ('hospital' IN ('hospital','shift','school-lunch'))"],
    [f"CONSTRAINT {NAME} CHECK (system_key IN ('hospital','shift','school-lunch','other'))"],
    [f"CONSTRAINT {NAME} CHECK (system_key IN ('hospital','shift'))"],
    [f"CONSTRAINT {NAME} CHECK (system_key IN ('hospital','shift','school-lunch','hospital'))"],
    [f"CONSTRAINT {NAME} CHECK (system_key IN ('Hospital','shift','school-lunch'))"],
    [],
    [f"CONSTRAINT wrong_name CHECK ({CANONICAL})"],
    [f"CHECK ({CANONICAL})"],
    [f"CONSTRAINT {NAME} CHECK ({CANONICAL})", f"CONSTRAINT {NAME} CHECK ({CANONICAL})"],
    [f"CONSTRAINT {NAME} CHECK ({CANONICAL})", "CONSTRAINT extra_check CHECK(enabled IN (0,1))"],
    [f"CONSTRAINT {NAME} CHECK (1 /* hospital shift school-lunch */)"],
    [f"CONSTRAINT {NAME} CHECK ('hospital shift school-lunch' <> '')"],
])
def test_noncanonical_actual_schema_stops_bootstrap_without_any_adoption(owned, checks):
    replace_access(owned, checks)
    before = snapshot(owned)
    with pytest.raises(PortalAccessBootstrapError, match="system_key constraint"):
        with owned.begin() as connection:
            bootstrap(connection)
    assert snapshot(owned) == before


def test_permissive_or_really_accepts_invalid_data_but_gate_refuses(owned):
    replace_access(owned, [f"CONSTRAINT {NAME} CHECK ({CANONICAL} OR 1=1)"])
    with owned.begin() as connection:
        connection.exec_driver_sql("INSERT INTO user_system_access VALUES('retained','other',1)")
    before = snapshot(owned)
    with pytest.raises(PortalAccessBootstrapError):
        with owned.begin() as connection:
            bootstrap(connection)
    assert snapshot(owned) == before


@pytest.mark.parametrize("unavailable", [None, "", [], [{"name": NAME, "sqltext": None}]])
def test_unavailable_reflection_never_falls_back_to_ddl(owned, monkeypatch, unavailable):
    # Fault injection covers only missing Inspector information; the schema and
    # bootstrap transaction are real and the canonical DDL remains present.
    original = sa.engine.reflection.Inspector.get_check_constraints

    def missing(self, table, *args, **kwargs):
        if table == "user_system_access":
            return unavailable
        return original(self, table, *args, **kwargs)

    before = snapshot(owned)
    with monkeypatch.context() as context:
        context.setattr(sa.engine.reflection.Inspector, "get_check_constraints", missing)
        with pytest.raises(PortalAccessBootstrapError, match="system_key constraint"):
            with owned.begin() as connection:
                bootstrap(connection)
    assert snapshot(owned) == before


def test_unknown_dialect_cannot_be_accepted_by_matching_words():
    connection = SimpleNamespace(dialect=SimpleNamespace(name="unsupported"))
    assert not schema._has_canonical_system_check(connection, [{"name": NAME, "sqltext": CANONICAL}])


def test_postgres_branch_ast_and_exact_source_are_unchanged():
    path = "backend/src/maintenance/portal_access_bootstrap_schema.py"
    baseline = subprocess.check_output(["git", "show", f"c0e878b3226e26d21b84d872a7c521f3ca27058b:{path}"], cwd=ROOT.parent, text=True)
    current = (ROOT.parent / path).read_text()

    def pg_branch(source):
        function = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == "_has_canonical_system_check")
        branch = function.body[0]
        return ast.dump(branch), ast.get_source_segment(source, branch)

    assert pg_branch(current) == pg_branch(baseline)


@pytest.mark.parametrize("collation,primary_key,invalid", [
    ("NOCASE", "system_key", "HOSPITAL"),
    ("RTRIM", "system_key", "hospital "),
    ("NOCASE", "system_key COLLATE BINARY", "HOSPITAL"),
])
def test_column_collation_permits_invalid_value_but_bootstrap_refuses(owned, collation, primary_key, invalid):
    with owned.begin() as connection:
        connection.exec_driver_sql("DROP TABLE user_system_access")
        connection.exec_driver_sql(
            "CREATE TABLE user_system_access (user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
            f"system_key VARCHAR COLLATE {collation} NOT NULL, enabled BOOLEAN DEFAULT 1 NOT NULL, "
            f"PRIMARY KEY(user_id,{primary_key}), CONSTRAINT {NAME} CHECK ({CANONICAL}))"
        )
        # Actual SQLite enforcement differs despite an identical reflected CHECK.
        assert sa.inspect(connection).get_check_constraints("user_system_access") == [{"sqltext": CANONICAL, "name": NAME}]
        connection.execute(sa.text("INSERT INTO user_system_access VALUES('retained',:key,1)"), {"key": invalid})
        assert connection.exec_driver_sql("SELECT system_key FROM user_system_access").scalar_one() == invalid
        assert connection.exec_driver_sql("PRAGMA ignore_check_constraints").scalar_one() == 0
    before = snapshot(owned)
    with owned.connect() as connection:
        with pytest.raises(PortalAccessBootstrapError, match="system_key constraint"):
            with connection.begin():
                bootstrap(connection)
        assert connection.exec_driver_sql("PRAGMA ignore_check_constraints").scalar_one() == 0
    assert snapshot(owned) == before


def test_disabled_check_enforcement_stops_without_reenabling_or_mutation(owned):
    before_schema, _ = snapshot(owned)
    with owned.connect() as connection:
        # This setting is confined to the freshly owned test connection. The gate
        # must only observe it; turning enforcement back on would hide the defect.
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        assert connection.exec_driver_sql("PRAGMA ignore_check_constraints").scalar_one() == 1
        connection.exec_driver_sql("INSERT INTO user_system_access VALUES('retained','outside-canonical',1)")
        connection.commit()
        before = snapshot(owned)
        assert before[0] == before_schema
        with pytest.raises(PortalAccessBootstrapError, match="system_key constraint"):
            with connection.begin():
                bootstrap(connection)
        assert connection.exec_driver_sql("PRAGMA ignore_check_constraints").scalar_one() == 1
        assert connection.exec_driver_sql("SELECT system_key FROM user_system_access").scalar_one() == "outside-canonical"
        assert snapshot(owned) == before


def test_explicit_binary_collation_is_not_the_0026_declaration(owned):
    with owned.begin() as connection:
        connection.exec_driver_sql("DROP TABLE user_system_access")
        connection.exec_driver_sql(
            "CREATE TABLE user_system_access (user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
            "system_key VARCHAR COLLATE BINARY NOT NULL, enabled BOOLEAN DEFAULT 1 NOT NULL, "
            f"PRIMARY KEY(user_id,system_key), CONSTRAINT {NAME} CHECK ({CANONICAL}))"
        )
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.exec_driver_sql("INSERT INTO user_system_access VALUES('retained','HOSPITAL',1)")
    before = snapshot(owned)
    with pytest.raises(PortalAccessBootstrapError, match="system_key constraint"):
        with owned.begin() as connection:
            bootstrap(connection)
    assert snapshot(owned) == before


@pytest.mark.parametrize("unavailable", ["ddl_missing", "ddl_blank", "ddl_error", "pragma_missing", "pragma_string", "pragma_error"])
def test_mandatory_enforcement_evidence_unavailable_blocks(owned, monkeypatch, unavailable):
    before = snapshot(owned)
    with owned.connect() as connection:
        execute = connection.execute
        driver = connection.exec_driver_sql

        def read_ddl(statement, *args, **kwargs):
            if str(statement) == "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'user_system_access'":
                if unavailable == "ddl_error":
                    raise sa.exc.DatabaseError("synthetic owned DDL read failure", {}, RuntimeError("denied"))
                if unavailable in {"ddl_missing", "ddl_blank"}:
                    return SimpleNamespace(scalar=lambda: None if unavailable == "ddl_missing" else " ")
            return execute(statement, *args, **kwargs)

        def read_pragma(statement, *args, **kwargs):
            if statement == "PRAGMA ignore_check_constraints":
                if unavailable == "pragma_error":
                    raise sa.exc.DatabaseError("synthetic owned PRAGMA read failure", {}, RuntimeError("denied"))
                if unavailable in {"pragma_missing", "pragma_string"}:
                    return SimpleNamespace(scalar=lambda: None if unavailable == "pragma_missing" else "0")
            return driver(statement, *args, **kwargs)

        with monkeypatch.context() as context:
            context.setattr(connection, "execute", read_ddl)
            context.setattr(connection, "exec_driver_sql", read_pragma)
            with pytest.raises(PortalAccessBootstrapError, match="system_key constraint"):
                with connection.begin():
                    bootstrap(connection)
        assert connection.exec_driver_sql("PRAGMA ignore_check_constraints").scalar_one() == 0
    assert snapshot(owned) == before
