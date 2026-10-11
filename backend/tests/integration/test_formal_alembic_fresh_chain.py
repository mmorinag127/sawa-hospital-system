from pathlib import Path
import os
import sqlite3
import importlib.util
import json
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from src.services.monthly_menu_identity_schema import require_canonical_identity_schema
from src.monthly_menu_identity import (
    identity_index_ddl, legacy_index_ddl, LEGACY_INDEX, RAW_CONSTRAINT, RAW_COLUMNS,
)
from src.maintenance.formal_migration_schema import (
    read_catalog, require_revision_catalog, table_shapes, _without_identity,
    _sqlite_table_definition, formal_head_revision, formal_revision_chain, require_formal_head,
)


ROOT = Path(__file__).resolve().parents[2]
OWNED_CATALOG_ROOT = ROOT.parent / "tmp/hospital-v1-catalog-20261007"


@pytest.mark.parametrize("user,owner,database,message", (
    ("other", "required", "owned", "connected role mismatch"),
    ("required", "other", "owned", "database owner mismatch"),
    ("required", "required", "other", "connected database mismatch"),
))
def test_observer_rejects_role_and_owner_before_migration(user, owner, database, message):
    spec = importlib.util.spec_from_file_location("owned_observer", ROOT / "scripts/observe_hospital_catalogs.py")
    observer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observer)

    class Connection:
        def exec_driver_sql(self, sql):
            assert sql.startswith("SELECT current_user, current_database()")
            return self

        def one(self):
            return user, database, owner

    with pytest.raises(RuntimeError, match=message):
        observer.require_observation_role(Connection(), "required", "owned")


@pytest.fixture(autouse=True)
def explicit_formal_target_environment(monkeypatch):
    # The suite's runtime database is not the independently owned migration target.
    for name in ("DB_URI", "DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER"):
        monkeypatch.delenv(name, raising=False)


def _owned_postgres(uri):
    owner = sa.engine.make_url(uri)
    assert owner.drivername == "postgresql+psycopg2" and owner.host is None and owner.port is None
    assert owner.database == "postgres"
    assert set(owner.query) == {"host", "port"}, "only owned Unix socket target parameters are permitted"
    targets = {
        "55482": (OWNED_CATALOG_ROOT / "pg15", 15),
        "55483": (OWNED_CATALOG_ROOT / "pg16", 16),
    }
    port = owner.query["port"]
    assert port in targets, "only explicitly owned PG15/PG16 ports are permitted"
    root, major = targets[port]
    assert Path(owner.query["host"]).resolve() == (root / "socket").resolve()
    assert (root / "socket").is_dir()
    return owner, root, port, major


def _verify_postgres_owner(connection, root, port, major):
    assert Path(connection.exec_driver_sql("SHOW data_directory").scalar_one()).resolve() == (root / "pgdata").resolve()
    assert connection.exec_driver_sql("SHOW port").scalar_one() == port
    assert connection.exec_driver_sql("SHOW listen_addresses").scalar_one() == ""
    assert Path(connection.exec_driver_sql("SHOW unix_socket_directories").scalar_one()).resolve() == (root / "socket").resolve()
    assert int(connection.exec_driver_sql("SHOW server_version_num").scalar_one()) // 10000 == major


@pytest.mark.parametrize("uri", (
    "postgresql+psycopg2://mmorinag@/postgres?host=/private/tmp/sawa-c2-formal-pg15-20261006/socket&port=55464",
    "postgresql+psycopg2://mmorinag@/postgres?host=/private/tmp/sawa-c2-formal-alembic-20261006/socket&port=55465",
    "postgresql+psycopg2://mmorinag@/postgres?host=/private/tmp/sawa-c2-formal-pg15-20261006/socket&port=5432",
    "postgresql+psycopg2://mmorinag@localhost:55465/postgres",
    "postgresql+psycopg2://mmorinag@/other?host=/private/tmp/sawa-c2-formal-pg15-20261006/socket&port=55465",
))
def test_postgres_fixture_rejects_nonowned_targets_without_connect(uri, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("ownership rejection must precede connection")

    monkeypatch.setattr(sa, "create_engine", forbidden)
    with pytest.raises(AssertionError):
        _owned_postgres(uri)


@pytest.fixture(params=("sqlite", "postgresql"))
def formal_database(request, tmp_path, monkeypatch):
    admin = None
    database = None
    engine = None
    created = False
    try:
        if request.param == "sqlite":
            url = sa.URL.create("sqlite", database=str(tmp_path / "catalog.sqlite3"))
        else:
            uri = os.environ.get("C2_FORMAL_PG_URI")
            if not uri:
                pytest.skip("C2_FORMAL_PG_URI required for owned PostgreSQL catalog proof")
            owner, root, port, major = _owned_postgres(uri)
            admin = sa.create_engine(owner, isolation_level="AUTOCOMMIT")
            database = "c2_formal_test_" + uuid4().hex
            with admin.connect() as connection:
                _verify_postgres_owner(connection, root, port, major)
                connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
                created = True
            url = owner.set(database=database)
        engine = sa.create_engine(url)
        monkeypatch.delenv("DB_URI", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("ALEMBIC_DB_URI", url.render_as_string(hide_password=False))
        yield engine, Config(str(ROOT / "alembic.ini"))
    finally:
        if engine is not None:
            engine.dispose()
        if admin is not None:
            try:
                if created:
                    with admin.connect() as connection:
                        connection.exec_driver_sql(f'DROP DATABASE "{database}"')
            finally:
                admin.dispose()


@pytest.mark.parametrize("revision", formal_revision_chain())
def test_each_observed_revision_resumes_to_head(formal_database, revision):
    engine, config = formal_database
    command.upgrade(config, revision)
    with engine.connect() as connection:
        if engine.dialect.name == "postgresql":
            assert sa.inspect(connection).get_materialized_view_names() == []
        require_revision_catalog(connection, revision)
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == formal_head_revision()
        require_revision_catalog(connection, formal_head_revision())
        require_canonical_identity_schema(connection)


@pytest.mark.parametrize("mutation", ("extra-column", "wrong-type", "missing-pk"))
def test_version_table_schema_is_not_adopted(formal_database, mutation):
    engine, config = formal_database
    command.upgrade(config, "0001")
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        if mutation == "extra-column":
            operations.add_column("alembic_version", sa.Column("unknown", sa.Text()))
        else:
            with operations.batch_alter_table("alembic_version") as batch:
                if mutation == "wrong-type":
                    batch.alter_column("version_num", type_=sa.Text(), existing_type=sa.String(32))
                else:
                    batch.drop_constraint("alembic_version_pkc", type_="primary")
    before = _catalog_and_rows(engine)
    with pytest.raises(RuntimeError, match="catalog"):
        command.upgrade(config, "head")
    assert _catalog_and_rows(engine) == before


def test_column_order_and_sql_literal_are_preserved_by_catalog_comparison():
    definition = _sqlite_table_definition("CREATE TABLE example (a TEXT DEFAULT 'A B', b TEXT, PRIMARY KEY (a,b))")
    reordered = _sqlite_table_definition("CREATE TABLE example (b TEXT, a TEXT DEFAULT 'A B', PRIMARY KEY (a,b))")
    literal_changed = _sqlite_table_definition("CREATE TABLE example (a TEXT DEFAULT 'AB', b TEXT, PRIMARY KEY (a,b))")
    key_changed = _sqlite_table_definition("CREATE TABLE example (a TEXT DEFAULT 'A B', b TEXT, PRIMARY KEY (b,a))")
    assert definition != reordered
    assert definition != literal_changed
    assert definition != key_changed
    snapshot = {"tables": {"example": {"columns": [{"name": "a"}, {"name": "b"}], "pk": {"constrained_columns": ["a", "b"]}}}, "native": []}
    shape = table_shapes(snapshot, "sqlite")
    snapshot["tables"]["example"]["columns"].reverse()
    assert table_shapes(snapshot, "sqlite") != shape


@pytest.mark.parametrize("invalid", ("missing", "blank", "disagree", "partial-components"))
def test_standard_alembic_cli_rejects_ambiguous_target_before_connect(tmp_path, invalid):
    import subprocess
    import sys
    environment = os.environ.copy()
    for name in ("ALEMBIC_DB_URI", "DB_URI", "DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER"):
        environment.pop(name, None)
    candidate = tmp_path / "must-not-create.sqlite3"
    other = tmp_path / "also-must-not-create.sqlite3"
    if invalid != "missing":
        environment["ALEMBIC_DB_URI"] = " " if invalid == "blank" else f"sqlite:///{candidate}"
    if invalid == "disagree":
        environment["DB_URI"] = f"sqlite:///{other}"
    if invalid == "partial-components":
        environment["DB_HOST"] = "/cloudsql/explicit:region:instance"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
        env=environment, cwd=ROOT.parent, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert any(word in result.stderr for word in ("required", "nonblank", "disagree", "components"))
    assert not candidate.exists()
    assert not other.exists()


@pytest.mark.parametrize("state", ("head", "old", "missing-column", "missing-index"))
def test_owned_postgres_predeploy_and_startup_share_readonly_catalog(formal_database, monkeypatch, state):
    import sys
    engine, config = formal_database
    if engine.dialect.name != "postgresql":
        pytest.skip("actual PostgreSQL READ ONLY predeploy boundary")
    sys.path.insert(0, str(ROOT.parent / "scripts"))
    import run_stg_runtime_schema_preflight as runner

    command.upgrade(config, "0001" if state == "old" else "head")
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO facilities(id,name) VALUES ('preflight-preserve','値保持')")
        if state == "missing-column":
            connection.exec_driver_sql("ALTER TABLE facility_template_versions RENAME COLUMN template_digest TO unknown_digest")
        elif state == "missing-index":
            connection.exec_driver_sql("DROP INDEX ix_facility_template_versions_valid_to")
    owner, root, port, major = _owned_postgres(os.environ["C2_FORMAL_PG_URI"])
    monkeypatch.setattr(runner, "verify_connection_target", lambda connection: _verify_postgres_owner(connection, root, port, major))
    before = _catalog_and_rows(engine)
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sa.event.listen(engine, "before_cursor_execute", capture)
    try:
        with engine.connect() as connection, connection.begin():
            if state == "head":
                runner.validate_catalog(connection)
                require_formal_head(connection)
            else:
                with pytest.raises(runner.MigrationBlocked, match="formal migration head|missing columns|missing indexes"):
                    runner.validate_catalog(connection)
                with pytest.raises(RuntimeError, match="formal migration head|missing columns|missing indexes"):
                    require_formal_head(connection)
            assert connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
            assert connection.exec_driver_sql("SHOW lock_timeout").scalar_one() == "5s"
            assert connection.exec_driver_sql("SHOW statement_timeout").scalar_one() == "15s"
            with pytest.raises(sa.exc.DBAPIError, match="read-only"):
                connection.exec_driver_sql("UPDATE facilities SET name='must-not-save'")
            connection.rollback()
    finally:
        sa.event.remove(engine, "before_cursor_execute", capture)
    # The sole attempted UPDATE is the explicit negative assertion above.
    assert not any(statement.lstrip().upper().startswith(("CREATE", "ALTER", "DROP", "INSERT", "DELETE", "TRUNCATE")) for statement in statements)
    assert _catalog_and_rows(engine) == before


@pytest.mark.parametrize("mutation", ("collation", "unvalidated-fk"))
def test_postgres_native_attributes_stop_before_ddl(formal_database, mutation):
    engine, config = formal_database
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL native column and constraint attributes")
    command.upgrade(config, "0014")
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO facilities(id,name) VALUES ('native-preserve','値保持')")
        if mutation == "collation":
            connection.exec_driver_sql('ALTER TABLE facilities ALTER COLUMN name TYPE VARCHAR COLLATE "C"')
        else:
            connection.exec_driver_sql("ALTER TABLE orders DROP CONSTRAINT fk_orders_template_version_id")
            connection.exec_driver_sql("ALTER TABLE orders ADD CONSTRAINT fk_orders_template_version_id FOREIGN KEY (template_version_id) REFERENCES facility_template_versions(id) NOT VALID")
    before = _catalog_and_rows(engine)
    with pytest.raises(RuntimeError, match="catalog"):
        command.upgrade(config, "head")
    assert _catalog_and_rows(engine) == before


@pytest.mark.parametrize("state", ("head", "versionless", "old", "unknown-column"))
def test_startup_uses_readonly_formal_head_before_any_business_initialization(formal_database, monkeypatch, state):
    import src.main as main
    engine, config = formal_database
    if state != "versionless":
        command.upgrade(config, "0001" if state == "old" else "head")
    if state == "unknown-column":
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE facilities ADD COLUMN unknown_startup INTEGER")
    monkeypatch.setattr(main.db, "engine", engine)
    calls = []
    monkeypatch.setattr(main.menu_service, "ensure_menu_schema", lambda: calls.append("menu"))
    monkeypatch.setattr(main.facility_template_version_service, "ensure_facility_template_version_schema", lambda: calls.append("facility"))
    monkeypatch.setattr(main.facility_service, "sync_facility_names_from_master", lambda: calls.append("sync"))
    before = _catalog_and_rows(engine)
    ddl = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("CREATE", "ALTER", "DROP", "INSERT", "UPDATE", "DELETE")):
            ddl.append(statement)

    sa.event.listen(engine, "before_cursor_execute", record)
    try:
        if state == "head":
            main._initialize_menu_schema()
            assert calls == ["menu", "facility", "sync"]
        else:
            with pytest.raises(RuntimeError, match="runtime blocked|catalog"):
                main._initialize_menu_schema()
            assert calls == []
    finally:
        sa.event.remove(engine, "before_cursor_execute", record)
    assert ddl == []
    assert _catalog_and_rows(engine) == before


def test_postflight_rejects_normal_return_after_corrupting_real_ddl(formal_database, monkeypatch):
    engine, config = formal_database
    command.upgrade(config, "0001")
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO facilities(id,name) VALUES ('postflight','値保持')")
    before = _catalog_and_rows(engine)
    original = Operations.create_table
    observed = []

    def corrupt(operations, name, *args, **kwargs):
        result = original(operations, name, *args, **kwargs)
        if name == "ocr_jobs":
            operations.add_column(name, sa.Column("unknown_postflight", sa.Integer()))
            observed.append(name)
        return result

    monkeypatch.setattr(Operations, "create_table", corrupt)
    with pytest.raises(RuntimeError, match="catalog.*ocr_jobs"):
        command.upgrade(config, "head")
    assert observed == ["ocr_jobs"]
    assert _catalog_and_rows(engine) == before


def test_0028_postflight_rolls_back_real_ddl_and_preserves_json_quantity(formal_database, monkeypatch):
    engine, config = formal_database
    command.upgrade(config, "0027")
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO order_ocr_revisions (id,order_id,row_count,changed,sheet_save_only,fields,created_at) VALUES ('postflight-json','owned-order',3,false,false,'{\"quantity\":3.25}',CURRENT_TIMESTAMP)")
    before = _catalog_and_rows(engine)
    original = Operations.add_column
    observed = []

    def corrupt(operations, table_name, column, *args, **kwargs):
        result = original(operations, table_name, column, *args, **kwargs)
        if table_name == "order_ocr_revisions" and column.name == "metadata_json":
            original(operations, table_name, sa.Column("unknown_postflight", sa.Integer()))
            observed.append(column.name)
        return result

    monkeypatch.setattr(Operations, "add_column", corrupt)
    with pytest.raises(RuntimeError, match="catalog.*order_ocr_revisions"):
        command.upgrade(config, "head")
    assert observed == ["metadata_json"]
    assert _catalog_and_rows(engine) == before


@pytest.mark.parametrize("outcome", ("success", "ddl-error", "lock-conflict"))
def test_sqlite_supplied_pool_busy_timeout_is_restored(tmp_path, monkeypatch, outcome):
    import time
    url = f"sqlite:///{tmp_path / 'pool.sqlite3'}"
    monkeypatch.setenv("ALEMBIC_DB_URI", url)
    engine = sa.create_engine(url, connect_args={"timeout": 30}, poolclass=sa.pool.QueuePool)
    config = Config(str(ROOT / "alembic.ini"))
    observed = []
    owner = None

    def fail_after_ddl(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("CREATE TABLE facilities"):
            observed.append(statement)
            raise RuntimeError("owned post-DDL fault")

    try:
        with engine.connect() as connection:
            before = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()
            connection.rollback()
            config.attributes["connection"] = connection
            if outcome == "lock-conflict":
                owner = engine.connect()
                owner.exec_driver_sql("BEGIN IMMEDIATE")
            if outcome == "ddl-error":
                sa.event.listen(engine, "after_cursor_execute", fail_after_ddl)
            started = time.monotonic()
            if outcome == "success":
                command.upgrade(config, "head")
            else:
                with pytest.raises((RuntimeError, sa.exc.OperationalError), match="post-DDL fault|database is locked"):
                    command.upgrade(config, "head")
            if outcome == "lock-conflict":
                assert time.monotonic() - started < 5
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == before == 30000
            connection.rollback()
        if owner is not None:
            owner.close()
            owner = None
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == before
        if outcome == "ddl-error":
            assert observed
    finally:
        if outcome == "ddl-error":
            sa.event.remove(engine, "after_cursor_execute", fail_after_ddl)
        if owner is not None:
            owner.close()
        engine.dispose()


def _pg_unsupported_snapshot(engine):
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        tables = {}
        for name in inspector.get_table_names():
            quoted = connection.dialect.identifier_preparer.quote(name)
            tables[name] = {
                "columns": repr(inspector.get_columns(name)),
                "constraints": repr((inspector.get_pk_constraint(name), inspector.get_foreign_keys(name), inspector.get_check_constraints(name))),
                "indexes": repr(inspector.get_indexes(name)),
                "rows": sorted(repr(tuple(row)) for row in connection.exec_driver_sql(f"SELECT * FROM {quoted}")),
            }
        semantics = connection.exec_driver_sql("""
            SELECT c.relname,c.relkind,c.relispartition,c.relrowsecurity,c.relforcerowsecurity,
              (SELECT array_agg(pg_get_triggerdef(t.oid) ORDER BY t.tgname) FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal),
              (SELECT array_agg(p.polname || ':' || coalesce(pg_get_expr(p.polqual,p.polrelid),'') ORDER BY p.polname) FROM pg_policy p WHERE p.polrelid=c.oid),
              (SELECT array_agg(i.inhparent::regclass::text ORDER BY i.inhparent) FROM pg_inherits i WHERE i.inhrelid=c.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema() ORDER BY c.relname
        """).all()
        return tables, semantics


@pytest.mark.parametrize("mutation", ("trigger", "rls", "policy", "partition", "inheritance"))
def test_postgres_unsupported_semantics_stop_migration_and_runtime(formal_database, mutation):
    engine, config = formal_database
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL native schema semantics")
    command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO facilities(id,name) VALUES ('owned-preserve','値保持')")
        if mutation == "trigger":
            connection.exec_driver_sql("CREATE FUNCTION owned_trigger() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'")
            connection.exec_driver_sql("CREATE TRIGGER owned_trigger BEFORE INSERT ON facilities FOR EACH ROW EXECUTE FUNCTION owned_trigger()")
        elif mutation == "rls":
            connection.exec_driver_sql("ALTER TABLE facilities ENABLE ROW LEVEL SECURITY")
        elif mutation == "policy":
            connection.exec_driver_sql("CREATE POLICY owned_policy ON facilities USING (true)")
        elif mutation == "partition":
            connection.exec_driver_sql("CREATE TABLE owned_partition(id integer) PARTITION BY RANGE(id)")
        else:
            connection.exec_driver_sql("CREATE TABLE owned_child() INHERITS (facilities)")
    before = _pg_unsupported_snapshot(engine)
    with pytest.raises(RuntimeError, match="unsupported"):
        command.upgrade(config, "head")
    with engine.connect() as connection, pytest.raises(RuntimeError, match="unsupported"):
        require_formal_head(connection)
    assert _pg_unsupported_snapshot(engine) == before


@pytest.mark.parametrize("revision", (None, "0001"))
def test_postgres_routine_only_or_known_revision_stops_before_ddl(formal_database, revision):
    engine, config = formal_database
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL standalone routines")
    if revision is not None:
        command.upgrade(config, revision)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE FUNCTION owned_value() RETURNS integer LANGUAGE sql AS 'SELECT 7'")

    def snapshot():
        with engine.connect() as connection:
            return (
                _pg_unsupported_snapshot(engine),
                connection.exec_driver_sql("SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=current_schema() ORDER BY p.proname").all(),
                connection.exec_driver_sql("SELECT owned_value()").scalar_one(),
            )

    before = snapshot()
    with pytest.raises(RuntimeError, match="unsupported routine"):
        command.upgrade(config, "head")
    assert snapshot() == before


@pytest.mark.parametrize("formal_database", ("postgresql",), indirect=True)
@pytest.mark.parametrize("revision", (None, "0028", formal_head_revision()))
@pytest.mark.parametrize("kind", ("shell", "range", "multirange", "base", "domain", "enum", "composite"))
def test_postgres_standalone_types_stop_independently_before_ddl(formal_database, revision, kind):
    engine, config = formal_database
    if revision is not None:
        command.upgrade(config, revision)
    name = "unknown_" + kind
    with engine.begin() as connection:
        if revision is not None:
            connection.exec_driver_sql("INSERT INTO facilities(id,name) VALUES ('type-preserve','Preserved Facility')")
        if kind == "shell":
            connection.exec_driver_sql("CREATE TYPE unknown_shell")
        elif kind in {"range", "multirange"}:
            connection.exec_driver_sql("CREATE TYPE unknown_range AS RANGE (SUBTYPE=integer, MULTIRANGE_TYPE_NAME=unknown_multirange)")
        elif kind == "base":
            connection.exec_driver_sql("CREATE TYPE unknown_base")
            connection.exec_driver_sql("CREATE FUNCTION unknown_base_in(cstring) RETURNS unknown_base AS 'int4in' LANGUAGE internal IMMUTABLE STRICT")
            connection.exec_driver_sql("CREATE FUNCTION unknown_base_out(unknown_base) RETURNS cstring AS 'int4out' LANGUAGE internal IMMUTABLE STRICT")
            connection.exec_driver_sql("CREATE TYPE unknown_base (INPUT=unknown_base_in, OUTPUT=unknown_base_out, INTERNALLENGTH=4, PASSEDBYVALUE, ALIGNMENT=int4)")
        elif kind == "domain":
            connection.exec_driver_sql("CREATE DOMAIN unknown_domain AS integer")
        elif kind == "enum":
            connection.exec_driver_sql("CREATE TYPE unknown_enum AS ENUM ('explicit')")
        else:
            connection.exec_driver_sql("CREATE TYPE unknown_composite AS (explicit integer)")
    def snapshot():
        with engine.connect() as connection:
            return (
                _pg_unsupported_snapshot(engine),
                connection.exec_driver_sql("SELECT to_jsonb(t) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname=current_schema() ORDER BY t.typname").all(),
                connection.exec_driver_sql("SELECT to_jsonb(r) FROM pg_range r JOIN pg_type t ON t.oid=r.rngtypid JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname=current_schema() ORDER BY t.typname").all(),
                connection.exec_driver_sql("SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=current_schema() ORDER BY p.oid").all(),
            )
    before = snapshot()
    if kind == "shell":
        assert before[3] == []  # Shell rejection must not rely on routine detection.
    message = rf"unsupported standalone type:.*{name} \({kind}\)"
    with engine.connect() as connection, pytest.raises(RuntimeError, match=message):
        read_catalog(connection)
    with pytest.raises(RuntimeError, match=message):
        command.upgrade(config, "head")
    with engine.connect() as connection, pytest.raises(
        RuntimeError,
        match=message if revision == formal_head_revision() else "formal migration head.*is required",
    ):
        require_formal_head(connection)
    assert snapshot() == before


@pytest.mark.parametrize("formal_database", ("postgresql",), indirect=True)
def test_postgres_automatic_table_and_array_types_remain_supported(formal_database):
    engine, config = formal_database
    command.upgrade(config, "head")
    with engine.connect() as connection:
        types = connection.exec_driver_sql("SELECT t.typtype,t.typelem,c.relkind FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace LEFT JOIN pg_class c ON c.oid=t.typrelid WHERE n.nspname=current_schema()").all()
        assert any(kind == "c" and relation == "r" for kind, element, relation in types)
        assert any(kind == "b" and element != 0 for kind, element, relation in types)
        read_catalog(connection)
        require_formal_head(connection)


def _catalog_and_rows(engine):
    with engine.connect() as connection:
        catalog = read_catalog(connection)
        rows = {}
        for table in sa.inspect(connection).get_table_names():
            quoted = connection.dialect.identifier_preparer.quote(table)
            rows[table] = sorted((repr(tuple(row)) for row in connection.exec_driver_sql(f"SELECT * FROM {quoted}")))
        return catalog, rows


LOCK_SQL = "SELECT pg_try_advisory_xact_lock(1396791105, 1296648018)"


def _assert_competing_migration_cannot_lock(engine):
    with engine.connect() as competitor:
        if engine.dialect.name == "postgresql":
            assert competitor.exec_driver_sql(LOCK_SQL).scalar_one() is False
        else:
            competitor.exec_driver_sql("PRAGMA busy_timeout = 0")
            with pytest.raises(sa.exc.OperationalError, match="database is locked"):
                competitor.exec_driver_sql("BEGIN IMMEDIATE")


def test_formal_migration_lock_conflict_stops_without_mutation(formal_database):
    engine, config = formal_database
    command.upgrade(config, "0001")
    before = _catalog_and_rows(engine)
    with engine.connect() as owner:
        if engine.dialect.name == "postgresql":
            assert owner.exec_driver_sql(LOCK_SQL).scalar_one() is True
            error, message = RuntimeError, "migration lock is already held"
        else:
            owner.exec_driver_sql("BEGIN IMMEDIATE")
            error, message = sa.exc.OperationalError, "database is locked"
        with pytest.raises(error, match=message):
            command.upgrade(config, "head")
        assert _catalog_and_rows(engine) == before
    command.upgrade(config, "head")
    with engine.connect() as connection:
        require_revision_catalog(connection, formal_head_revision())


@pytest.mark.parametrize("outcome", ("success", "failure"))
def test_preflight_and_ddl_share_lock_and_transaction(formal_database, monkeypatch, outcome):
    import src.maintenance.formal_migration_schema as schema
    engine, config = formal_database
    command.upgrade(config, "0001")
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO facilities(id,name) VALUES ('lock-preserve','Preserved')")
    before = _catalog_and_rows(engine)
    original = schema.require_revision_catalog
    observed = {}
    ddl = []

    def checked_preflight(connection, revision):
        original(connection, revision)
        observed["connection"] = connection
        observed["transaction"] = connection.get_transaction()
        assert observed["transaction"] is not None
        _assert_competing_migration_cannot_lock(engine)

    def after_ddl(connection, cursor, statement, parameters, execution_context, executemany):
        if connection is observed.get("connection") and statement.lstrip().upper().startswith(("CREATE ", "ALTER ", "DROP ")):
            assert connection.get_transaction() is observed["transaction"]
            ddl.append(statement)
            _assert_competing_migration_cannot_lock(engine)
            if outcome == "failure":
                raise RuntimeError("injected locked migration post-DDL failure")

    monkeypatch.setattr(schema, "require_revision_catalog", checked_preflight)
    sa.event.listen(sa.engine.Engine, "after_cursor_execute", after_ddl)
    try:
        if outcome == "failure":
            with pytest.raises(RuntimeError, match="injected locked migration post-DDL failure"):
                command.upgrade(config, "head")
        else:
            command.upgrade(config, "head")
    finally:
        sa.event.remove(sa.engine.Engine, "after_cursor_execute", after_ddl)
        monkeypatch.setattr(schema, "require_revision_catalog", original)
    assert ddl
    if outcome == "failure":
        assert _catalog_and_rows(engine) == before
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            assert connection.exec_driver_sql(LOCK_SQL).scalar_one() is True
        else:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        connection.exec_driver_sql("UPDATE facilities SET name='Updated' WHERE id='lock-preserve'")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT name FROM facilities WHERE id='lock-preserve'").scalar_one() == "Updated"
        require_revision_catalog(connection, formal_head_revision())


@pytest.mark.parametrize("revision", (None, "0001", "0028"))
def test_postgres_materialized_view_stops_before_ddl(formal_database, revision):
    engine, config = formal_database
    if engine.dialect.name != "postgresql":
        pytest.skip("materialized views are a PostgreSQL boundary")
    if revision is not None:
        command.upgrade(config, revision)
        with engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO facilities(id,name) VALUES ('owned-preserve','Owned')")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE MATERIALIZED VIEW owned_materialized AS SELECT 7 AS preserved_value")

    def snapshot():
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            tables = {}
            for name in inspector.get_table_names():
                quoted = connection.dialect.identifier_preparer.quote(name)
                tables[name] = {
                    "columns": inspector.get_columns(name),
                    "pk": inspector.get_pk_constraint(name),
                    "unique": inspector.get_unique_constraints(name),
                    "fk": inspector.get_foreign_keys(name),
                    "checks": inspector.get_check_constraints(name),
                    "indexes": inspector.get_indexes(name),
                    "rows": connection.exec_driver_sql(f"SELECT * FROM {quoted}").all(),
                }
            return (
                json.dumps(tables, sort_keys=True, default=str),
                inspector.get_materialized_view_names(),
                inspector.get_view_definition("owned_materialized"),
                connection.exec_driver_sql("SELECT * FROM owned_materialized").all(),
                inspector.has_table("alembic_version"),
            )

    before = snapshot()
    assert before[1] == ["owned_materialized"] and before[3] == [(7,)]
    message = "automatic adoption" if revision is None else "unsupported catalog object"
    with pytest.raises(RuntimeError, match=message):
        command.upgrade(config, "head")
    assert snapshot() == before


@pytest.mark.parametrize("metadata", ("sql-null", "json-null", "object"))
def test_0028_downgrade_preserves_metadata_and_other_columns(formal_database, metadata):
    engine, config = formal_database
    command.upgrade(config, "0028")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO order_ocr_revisions "
            "(id, order_id, row_count, changed, sheet_save_only, fields, created_at) "
            "VALUES ('owned-downgrade', 'owned-order', 3, false, false, "
            "'{\"quantity\":3.25}', CURRENT_TIMESTAMP)"
        )
        if metadata != "sql-null":
            value = "null" if metadata == "json-null" else '{"quantity":3.25,"facility":"owned"}'
            expression = "CAST(:value AS JSON)" if engine.dialect.name == "postgresql" else ":value"
            connection.execute(sa.text(f"UPDATE order_ocr_revisions SET metadata_json = {expression}"), {"value": value})
        assert connection.exec_driver_sql(
            "SELECT metadata_json IS NOT NULL FROM order_ocr_revisions"
        ).scalar_one() == (metadata != "sql-null")
    before = _catalog_and_rows(engine)
    if metadata != "sql-null":
        with pytest.raises(RuntimeError, match="contains non-NULL values"):
            command.downgrade(config, "0027")
        assert _catalog_and_rows(engine) == before
    else:
        with engine.connect() as connection:
            columns = [column["name"] for column in sa.inspect(connection).get_columns("order_ocr_revisions")
                       if column["name"] != "metadata_json"]
            projection = ", ".join(connection.dialect.identifier_preparer.quote(name) for name in columns)
            rows = connection.exec_driver_sql(f"SELECT {projection} FROM order_ocr_revisions").all()
        command.downgrade(config, "0027")
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0027"
            assert [column["name"] for column in sa.inspect(connection).get_columns("order_ocr_revisions")] == columns
            assert connection.exec_driver_sql(f"SELECT {projection} FROM order_ocr_revisions").all() == rows
            require_revision_catalog(connection, "0027")


@pytest.mark.parametrize("attribute", ("COLLATE NOCASE", "STRICT", "WITHOUT ROWID"))
def test_sqlite_identity_exclusion_preserves_nonidentity_table_attributes(attribute):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        column = "name TEXT COLLATE NOCASE" if attribute == "COLLATE NOCASE" else "name TEXT"
        suffix = "" if attribute == "COLLATE NOCASE" else " " + attribute
        connection.exec_driver_sql(f"CREATE TABLE monthly_menu_items (id TEXT PRIMARY KEY, {column}){suffix}")
        actual = table_shapes(read_catalog(connection), "sqlite")["monthly_menu_items"]
    expected = dict(actual)
    expected["native"] = [dict(item) for item in actual["native"]]
    expected["native"][0]["table_definition"] = _sqlite_table_definition(
        "CREATE TABLE monthly_menu_items (id TEXT PRIMARY KEY, name TEXT)"
    )
    assert _without_identity(actual, "sqlite") != _without_identity(expected, "sqlite")
    engine.dispose()


def test_sqlite_identity_variant_with_unknown_collation_stops_before_ddl(tmp_path, monkeypatch):
    engine = sa.create_engine("sqlite:///" + str(tmp_path / "collation.sqlite3"))
    monkeypatch.setenv("ALEMBIC_DB_URI", str(engine.url))
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "0022")
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("monthly_menu_items") as batch:
            batch.alter_column("name", type_=sa.String(collation="NOCASE"), existing_type=sa.String())
    before = _catalog_and_rows(engine)
    with pytest.raises(RuntimeError, match="catalog blocked"):
        command.upgrade(config, "head")
    assert _catalog_and_rows(engine) == before
    engine.dispose()


@pytest.mark.parametrize("literal", ("' '", "'\t'", "'\n'", "'Mixed Case'", "'it''s'"))
def test_sqlite_native_table_literals_remain_exact(literal):
    actual = _sqlite_table_definition(f"CREATE TABLE t (id TEXT DEFAULT {literal})")
    assert actual != _sqlite_table_definition("CREATE TABLE t (id TEXT DEFAULT '')")
    assert actual == _sqlite_table_definition(f"CREATE\nTABLE t( id TEXT DEFAULT {literal} )")


@pytest.mark.parametrize("addition", ("COLLATE NOCASE", "DESC", "ON CONFLICT IGNORE"))
def test_raw_constraint_name_does_not_hide_unknown_sqlite_attributes(addition):
    columns = ", ".join(f"{name} TEXT" for name in RAW_COLUMNS)
    keys = list(RAW_COLUMNS)
    suffix = ""
    if addition == "ON CONFLICT IGNORE":
        suffix = " " + addition
    else:
        keys[1] += " " + addition
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE TABLE monthly_menu_items ({columns}, CONSTRAINT {RAW_CONSTRAINT} "
            f"UNIQUE ({', '.join(keys)}){suffix})"
        )
        actual = table_shapes(read_catalog(connection), "sqlite")["monthly_menu_items"]
    stripped = _without_identity(actual, "sqlite")
    assert any(part[0] == ["word", "constraint"]
               for part in stripped["native"][0]["table_definition"]["declarations"])
    engine.dispose()


@pytest.mark.parametrize("variant", ("legacy", "canonical", "coexistence"))
def test_observed_identity_variants_preserve_catalog(formal_database, variant):
    engine, config = formal_database
    command.upgrade(config, "0022")
    with engine.begin() as connection:
        if variant != "legacy":
            connection.exec_driver_sql(identity_index_ddl())
        if variant == "canonical":
            connection.exec_driver_sql(f"DROP INDEX {LEGACY_INDEX}")
    with engine.connect() as connection:
        require_revision_catalog(connection, "0022")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        require_revision_catalog(connection, formal_head_revision())
        require_canonical_identity_schema(connection)


@pytest.mark.parametrize("variant", ("raw", "raw-legacy", "raw-canonical", "raw-both"))
def test_raw_identity_variant_requires_preserved_nonidentity_shape(formal_database, variant):
    engine, config = formal_database
    command.upgrade(config, "0022")
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP INDEX {LEGACY_INDEX}")
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("monthly_menu_items") as batch:
            batch.create_unique_constraint(RAW_CONSTRAINT, list(RAW_COLUMNS))
        if variant in ("raw-legacy", "raw-both"):
            connection.exec_driver_sql(legacy_index_ddl())
        if variant in ("raw-canonical", "raw-both"):
            connection.exec_driver_sql(identity_index_ddl())
    if engine.dialect.name == "sqlite":
        # The observed fresh chain has no menu_master_id FK. The existing
        # raw-constraint batch guard requires both known ORM FKs, so this
        # combination must stop rather than infer an unproved batch shape.
        before = _catalog_and_rows(engine)
        with pytest.raises(RuntimeError, match="foreign keys"):
            command.upgrade(config, "head")
        assert _catalog_and_rows(engine) == before
    else:
        command.upgrade(config, "head")
        with engine.connect() as connection:
            require_revision_catalog(connection, formal_head_revision())
            require_canonical_identity_schema(connection)


@pytest.mark.parametrize("revision", ("0001", "0014", "0015", "0022", "0023", "0028"))
@pytest.mark.parametrize("mutation", ("missing-column", "wrong-type", "unknown-index"))
def test_revision_catalog_mismatch_stops_before_ddl(formal_database, revision, mutation):
    engine, config = formal_database
    command.upgrade(config, revision)
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO facilities(id,name) VALUES ('owned-preserve','Owned')")
        if mutation == "unknown-index":
            connection.exec_driver_sql("CREATE INDEX unknown_facility_index ON facilities(name)")
        elif mutation == "missing-column":
            connection.exec_driver_sql("ALTER TABLE facilities DROP COLUMN name")
        else:
            operations = Operations(MigrationContext.configure(connection))
            with operations.batch_alter_table("facilities") as batch:
                batch.alter_column("name", type_=sa.Text(), existing_type=sa.String())
    before = _catalog_and_rows(engine)
    with pytest.raises(RuntimeError, match="catalog"):
        command.upgrade(config, "head")
    assert _catalog_and_rows(engine) == before


@pytest.mark.parametrize("mutation", ("missing-index", "missing-fk", "unknown-check"))
def test_constraint_index_catalog_mismatch_stops_before_ddl(formal_database, mutation):
    engine, config = formal_database
    command.upgrade(config, "0014")
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO facilities(id,name) VALUES ('owned-preserve','Owned')")
        if mutation == "missing-index":
            connection.exec_driver_sql("DROP INDEX ix_shipping_tracking_logs_tracking_key")
        else:
            operations = Operations(MigrationContext.configure(connection))
            with operations.batch_alter_table("orders") as batch:
                if mutation == "missing-fk":
                    batch.drop_constraint("fk_orders_template_version_id", type_="foreignkey")
                else:
                    batch.create_check_constraint("unknown_order_check", "id <> 'unknown'")
    before = _catalog_and_rows(engine)
    with pytest.raises(RuntimeError, match="catalog"):
        command.upgrade(config, "head")
    assert _catalog_and_rows(engine) == before


def test_owned_postgres_fresh_all_source_revisions(monkeypatch):
    monkeypatch.delenv("DB_URI", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    uri = os.environ.get("C2_FORMAL_PG_URI")
    if not uri:
        pytest.skip("C2_FORMAL_PG_URI not supplied; owned PostgreSQL requires explicit opt-in")
    url, owned_root, port, major = _owned_postgres(uri)
    socket = owned_root / "socket"
    assert socket.is_dir()
    database = f"c2_formal_test_{uuid4().hex}"
    assert url.drivername == "postgresql+psycopg2"
    assert url.database == "postgres"
    assert url.host is None and url.port is None
    assert Path(url.query["host"]).resolve() == socket.resolve()
    assert url.query["port"] == port
    admin = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    engine = None
    created = False
    try:
        with admin.connect() as connection:
            _verify_postgres_owner(connection, owned_root, port, major)
            connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
            created = True
        target = url.set(database=database)
        engine = sa.create_engine(target)
        with engine.connect() as connection:
            assert sa.inspect(connection).get_table_names() == []
        config = Config(str(ROOT / "alembic.ini"))
        revisions = list(ScriptDirectory.from_config(config).walk_revisions())
        assert {revision.revision for revision in revisions} == set(formal_revision_chain())
        monkeypatch.setenv("ALEMBIC_DB_URI", target.render_as_string(hide_password=False))
        command.upgrade(config, "0027")
        _verify_0028_transition(engine, config)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalars().all() == [formal_head_revision()]
            _require_model_columns(connection)
            require_canonical_identity_schema(connection)
            inspector = sa.inspect(connection)
            columns = {column["name"]: column for column in inspector.get_columns("shipping_tracking_logs")}
            expected = {
                "id": (sa.String, False), "tracking_key": (sa.String, False),
                "tracking_number": (sa.String, False), "ship_date": (sa.Date, True),
                "facility_name": (sa.String, True), "status": (sa.String, False),
                "delivered": (sa.Boolean, False), "arrival_text": (sa.String, True),
                "error": (sa.String, True), "source": (sa.String, False),
                "looked_up_at": (sa.DateTime, False), "created_at": (sa.DateTime, False),
            }
            assert columns.keys() == expected.keys()
            for name, (column_type, nullable) in expected.items():
                assert isinstance(columns[name]["type"], column_type), name
                assert columns[name]["nullable"] is nullable, name
                if name not in {"source", "delivered"}:
                    assert columns[name]["default"] is None, name
            assert columns["delivered"]["default"] == "false"
            assert columns["source"]["default"] == "'manual'::character varying"
            assert inspector.get_pk_constraint("shipping_tracking_logs")["constrained_columns"] == ["id"]
            indexes = {index["name"]: index for index in inspector.get_indexes("shipping_tracking_logs")}
            assert set(indexes) == {"ix_shipping_tracking_logs_tracking_key", "ix_shipping_tracking_logs_looked_up_at", "ix_shipping_tracking_logs_ship_date"}
            for name in ("tracking_key", "looked_up_at", "ship_date"):
                assert indexes[f"ix_shipping_tracking_logs_{name}"]["column_names"] == [name]
                assert not indexes[f"ix_shipping_tracking_logs_{name}"]["unique"]
            assert inspector.get_foreign_keys("shipping_tracking_logs") == []
            for table in ("orders", "ocr_jobs", "order_ocr_evidence_runs", "order_sheet_drafts", "order_confirmed_snapshots", "order_workflow_states", "order_current_states"):
                fk = next(item for item in inspector.get_foreign_keys(table) if item["name"] == f"fk_{table}_template_version_id")
                assert fk["constrained_columns"] == ["template_version_id"]
                assert fk["referred_table"] == "facility_template_versions"
                assert fk["referred_columns"] == ["id"]
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{database}"')
        admin.dispose()


def test_fresh_sqlite_upgrade_uses_all_numbered_revisions(tmp_path, monkeypatch):
    database = tmp_path / "fresh.sqlite3"
    config = Config(str(ROOT / "alembic.ini"))
    monkeypatch.setenv("ALEMBIC_DB_URI", f"sqlite:///{database}")

    command.upgrade(config, "head")

    engine = sa.create_engine(f"sqlite:///{database}")
    try:
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == formal_head_revision()
            _require_model_columns(connection)
            require_canonical_identity_schema(connection)
            inspector = sa.inspect(connection)
            assert inspector.has_table("shipping_tracking_logs")
            columns = {column["name"]: column for column in inspector.get_columns("shipping_tracking_logs")}
            expected = {
                "id": (sa.String, False), "tracking_key": (sa.String, False),
                "tracking_number": (sa.String, False), "ship_date": (sa.Date, True),
                "facility_name": (sa.String, True), "status": (sa.String, False),
                "delivered": (sa.Boolean, False), "arrival_text": (sa.String, True),
                "error": (sa.String, True), "source": (sa.String, False),
                "looked_up_at": (sa.DateTime, False), "created_at": (sa.DateTime, False),
            }
            assert columns.keys() == expected.keys()
            for name, (column_type, nullable) in expected.items():
                assert isinstance(columns[name]["type"], column_type), name
                assert columns[name]["nullable"] is nullable, name
                if name not in {"source", "delivered"}:
                    assert columns[name]["default"] is None, name
            assert columns["delivered"]["default"] == "0"
            assert columns["source"]["default"] == "'manual'"
            assert inspector.get_pk_constraint("shipping_tracking_logs")["constrained_columns"] == ["id"]
            shipping_indexes = {item["name"]: item for item in inspector.get_indexes("shipping_tracking_logs")}
            assert set(shipping_indexes) == {f"ix_shipping_tracking_logs_{name}" for name in ("tracking_key", "looked_up_at", "ship_date")}
            for name in ("tracking_key", "looked_up_at", "ship_date"):
                assert shipping_indexes[f"ix_shipping_tracking_logs_{name}"]["column_names"] == [name]
                assert not shipping_indexes[f"ix_shipping_tracking_logs_{name}"]["unique"]
            assert inspector.get_foreign_keys("shipping_tracking_logs") == []
            for table in ("orders", "ocr_jobs", "order_ocr_evidence_runs", "order_sheet_drafts", "order_confirmed_snapshots", "order_workflow_states", "order_current_states"):
                fk = next(item for item in inspector.get_foreign_keys(table) if item["name"] == f"fk_{table}_template_version_id")
                assert fk["constrained_columns"] == ["template_version_id"]
                assert fk["referred_table"] == "facility_template_versions"
                assert fk["referred_columns"] == ["id"]
            indexes = connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'index' AND tbl_name = 'monthly_menu_items'"
                )
            ).scalars()
            assert "uq_monthly_menu_items_scope_identity" in set(indexes)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("prepare", "message"),
    (
        ("CREATE TABLE existing_rows (id INTEGER PRIMARY KEY)", "no alembic_version"),
        (
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL); "
            "INSERT INTO alembic_version VALUES ('unknown')",
            "unknown revision",
        ),
        (
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL); "
            "INSERT INTO alembic_version VALUES ('0001'); "
            "INSERT INTO alembic_version VALUES ('0002')",
            "exactly one revision",
        ),
        (
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL); "
            "INSERT INTO alembic_version VALUES ('0027')",
            "catalog is partial",
        ),
    ),
)
def test_nonempty_or_unknown_sqlite_catalog_stops_before_ddl(tmp_path, monkeypatch, prepare, message):
    database = tmp_path / "blocked.sqlite3"
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        for statement in prepare.split("; "):
            connection.execute(sa.text(statement))
        connection.exec_driver_sql("CREATE TABLE preserved_rows (id INTEGER PRIMARY KEY, quantity REAL, payload TEXT)")
        connection.exec_driver_sql("INSERT INTO preserved_rows VALUES (1, 3.25, '{\"facility\":\"owned\"}')")
    engine.dispose()
    before = _sqlite_snapshot(database)

    config = Config(str(ROOT / "alembic.ini"))
    monkeypatch.setenv("ALEMBIC_DB_URI", f"sqlite:///{database}")
    with pytest.raises(RuntimeError, match=message):
        command.upgrade(config, "head")
    assert _sqlite_snapshot(database) == before


def _sqlite_snapshot(database):
    with sqlite3.connect(database) as connection:
        ddl = connection.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        rows = {}
        for object_type, name, _, _ in ddl:
            if object_type == "table":
                quoted = name.replace('"', '""')
                rows[name] = connection.execute(f'SELECT * FROM "{quoted}" ORDER BY rowid').fetchall()
        return ddl, rows


@pytest.mark.parametrize("offline", (False, True))
def test_missing_database_uri_stops(tmp_path, monkeypatch, offline):
    monkeypatch.delenv("ALEMBIC_DB_URI", raising=False)
    with pytest.raises(RuntimeError, match="ALEMBIC_DB_URI is required"):
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head", sql=offline)


def test_sqlite_actual_ddl_and_version_rollback(tmp_path, monkeypatch):
    database = tmp_path / "rollback.sqlite3"
    monkeypatch.setenv("ALEMBIC_DB_URI", f"sqlite:///{database}")
    observed = []

    def inject_failure(connection, cursor, statement, parameters, execution_context, executemany):
        if "CREATE TABLE ocr_jobs" in statement:
            assert sa.inspect(connection).has_table("facilities")
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0002"
            observed.append("DDL and version 0002 exist before injected failure")
            raise RuntimeError("injected migration 0003 failure")

    sa.event.listen(sa.engine.Engine, "before_cursor_execute", inject_failure)
    try:
        with pytest.raises(RuntimeError, match="injected migration 0003 failure"):
            command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    finally:
        sa.event.remove(sa.engine.Engine, "before_cursor_execute", inject_failure)
    assert observed == ["DDL and version 0002 exist before injected failure"]
    assert _sqlite_snapshot(database) == ([], {})


def test_sqlite_0027_rerun_preserves_snapshot(tmp_path, monkeypatch):
    database = tmp_path / "rerun.sqlite3"
    monkeypatch.setenv("ALEMBIC_DB_URI", f"sqlite:///{database}")
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "0027")
    before = _sqlite_snapshot(database)
    command.upgrade(config, "0027")
    assert _sqlite_snapshot(database) == before


@pytest.mark.parametrize("shape", ("lower-partial", "unknown-view"))
def test_existing_catalog_holes_are_exposed(tmp_path, monkeypatch, shape):
    database = tmp_path / "hole.sqlite3"
    with sqlite3.connect(database) as connection:
        if shape == "lower-partial":
            connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32))")
            connection.execute("INSERT INTO alembic_version VALUES ('0001')")
        else:
            connection.execute("CREATE VIEW unknown_view AS SELECT 42 AS value")
    before = _sqlite_snapshot(database)
    monkeypatch.setenv("ALEMBIC_DB_URI", f"sqlite:///{database}")
    try:
        with pytest.raises(RuntimeError, match="catalog|adoption"):
            command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    finally:
        assert _sqlite_snapshot(database) == before


def _require_model_columns(connection):
    from src.models import Base
    inspector = sa.inspect(connection)
    assert set(Base.metadata.tables) <= set(inspector.get_table_names())
    for name, table in Base.metadata.tables.items():
        assert set(table.columns.keys()) <= {column["name"] for column in inspector.get_columns(name)}, name


def _verify_0028_transition(engine, config):
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO order_ocr_revisions (id, order_id, row_count, changed, sheet_save_only, fields, created_at) VALUES ('owned-0028', 'owned-order', 3, false, false, '{\"quantity\":3.25}', CURRENT_TIMESTAMP)")
    with engine.connect() as connection:
        before = connection.exec_driver_sql("SELECT id, order_id, row_count, changed, sheet_save_only, fields, created_at FROM order_ocr_revisions").all()
    reached = []

    def fail_after_add(connection, cursor, statement, parameters, execution_context, executemany):
        if "ALTER TABLE order_ocr_revisions ADD COLUMN metadata_json" in statement:
            reached.append(True)
            raise RuntimeError("injected 0028 post-DDL failure")

    sa.event.listen(sa.engine.Engine, "after_cursor_execute", fail_after_add)
    try:
        with pytest.raises(RuntimeError, match="injected 0028 post-DDL failure"):
            command.upgrade(config, "0028")
    finally:
        sa.event.remove(sa.engine.Engine, "after_cursor_execute", fail_after_add)
    assert reached == [True]
    with engine.connect() as connection:
        assert "metadata_json" not in {column["name"] for column in sa.inspect(connection).get_columns("order_ocr_revisions")}
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0027"
        assert connection.exec_driver_sql("SELECT id, order_id, row_count, changed, sheet_save_only, fields, created_at FROM order_ocr_revisions").all() == before
    command.upgrade(config, "0028")
    with engine.begin() as connection:
        assert connection.exec_driver_sql("SELECT id, order_id, row_count, changed, sheet_save_only, fields, created_at FROM order_ocr_revisions").all() == before
        assert connection.exec_driver_sql("SELECT metadata_json FROM order_ocr_revisions").scalar_one() is None
        connection.exec_driver_sql("UPDATE order_ocr_revisions SET metadata_json = '{\"facility\":\"owned\",\"quantity\":3.25}'")
    spec = importlib.util.spec_from_file_location("revision_0028_test", ROOT / "migrations/0028_order_ocr_revision_metadata_json.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        values = connection.exec_driver_sql("SELECT * FROM order_ocr_revisions").all()
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        assert connection.exec_driver_sql("SELECT * FROM order_ocr_revisions").all() == values
    # Reject a wrong existing type before executing migration DDL.
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE order_ocr_revisions RENAME COLUMN metadata_json TO preserved_metadata")
        connection.exec_driver_sql("ALTER TABLE order_ocr_revisions ADD COLUMN metadata_json TEXT")
    with engine.connect() as connection:
        columns = sa.inspect(connection).get_columns("order_ocr_revisions")
        values = connection.exec_driver_sql("SELECT * FROM order_ocr_revisions").all()
    with pytest.raises(module.RevisionSchemaError, match="JSON NULL"):
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
    with engine.connect() as connection:
        assert [(column["name"], str(column["type"]), column["nullable"], column["default"]) for column in sa.inspect(connection).get_columns("order_ocr_revisions")] == [(column["name"], str(column["type"]), column["nullable"], column["default"]) for column in columns]
        assert connection.exec_driver_sql("SELECT * FROM order_ocr_revisions").all() == values
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE order_ocr_revisions DROP COLUMN metadata_json")
        connection.exec_driver_sql("ALTER TABLE order_ocr_revisions RENAME COLUMN preserved_metadata TO metadata_json")


def test_sqlite_0027_to_0028_preserves_rows_json(tmp_path, monkeypatch):
    database = tmp_path / "transition.sqlite3"
    monkeypatch.setenv("ALEMBIC_DB_URI", f"sqlite:///{database}")
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "0027")
    engine = sa.create_engine(f"sqlite:///{database}")
    try:
        _verify_0028_transition(engine, config)
        with engine.connect() as connection:
            require_revision_catalog(connection, "0028")
    finally:
        engine.dispose()
