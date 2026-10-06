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
from src.models.monthly_menu_identity import (
    identity_index_ddl, legacy_index_ddl, LEGACY_INDEX, RAW_CONSTRAINT, RAW_COLUMNS,
)
from src.maintenance.formal_migration_schema import (
    read_catalog, require_revision_catalog, table_shapes, _without_identity,
    _sqlite_table_definition,
)


ROOT = Path(__file__).resolve().parents[2]


def _owned_postgres(uri):
    owner = sa.engine.make_url(uri)
    assert owner.drivername == "postgresql+psycopg2" and owner.host is None and owner.port is None
    assert owner.database == "postgres"
    targets = {
        "55464": (Path("/private/tmp/sawa-c2-formal-alembic-20261006"), 16),
        "55465": (Path("/private/tmp/sawa-c2-formal-pg15-20261006"), 15),
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
    assert connection.exec_driver_sql("SHOW listen_addresses").scalar_one() == "127.0.0.1"
    assert connection.exec_driver_sql("SHOW unix_socket_directories").scalar_one() == str(root / "socket")
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


@pytest.mark.parametrize("revision", [f"{number:04d}" for number in range(1, 29)])
def test_each_observed_revision_resumes_to_head(formal_database, revision):
    engine, config = formal_database
    command.upgrade(config, revision)
    with engine.connect() as connection:
        if engine.dialect.name == "postgresql":
            assert sa.inspect(connection).get_materialized_view_names() == []
        require_revision_catalog(connection, revision)
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0028"
        require_revision_catalog(connection, "0028")
        require_canonical_identity_schema(connection)


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
        require_revision_catalog(connection, "0028")


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
        require_revision_catalog(connection, "0028")


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
        require_revision_catalog(connection, "0028")
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
            require_revision_catalog(connection, "0028")
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


def test_owned_postgres_fresh_all_28_revisions(monkeypatch):
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
        assert {revision.revision for revision in revisions} == {f"{number:04d}" for number in range(1, 29)}
        monkeypatch.setenv("ALEMBIC_DB_URI", target.render_as_string(hide_password=False))
        command.upgrade(config, "0027")
        _verify_0028_transition(engine, config)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalars().all() == ["0028"]
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
            assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0028"
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
            _require_model_columns(connection)
    finally:
        engine.dispose()
