"""C2-3 regression tests for migration 0023 identity schema replacement."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from c1_pg_ownership import additional_profile, require_server

from src.models.menu import MenuMaster, MonthlyMenu, MonthlyMenuItem
from src.services.monthly_menu_identity_schema import (
    IDENTITY_INDEX,
    LEGACY_INDEX,
    RAW_COLUMNS,
    RAW_CONSTRAINT,
    MonthlyMenuIdentitySchemaError,
    require_canonical_identity_schema,
)


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "backend/migrations/0023_monthly_menu_item_identity_indexes.py"


def load_migration():
    spec = importlib.util.spec_from_file_location("monthly_identity_0023", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_upgrade(connection, migration=None):
    with Operations.context(MigrationContext.configure(connection)):
        (migration or load_migration()).upgrade()


def run_downgrade(connection, migration=None):
    with Operations.context(MigrationContext.configure(connection)):
        (migration or load_migration()).downgrade()


def run_full_chain(connection):
    for revision in range(1, 24):
        path = next((ROOT / "backend/migrations").glob(f"{revision:04}_*.py"))
        spec = importlib.util.spec_from_file_location(f"migration_{revision:04}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()


@pytest.fixture(params=["sqlite", "postgresql"])
def database(request, tmp_path):
    if request.param == "sqlite":
        engine = sa.create_engine(sa.URL.create("sqlite", database=str(tmp_path / "identity.sqlite3")))
        yield engine
        engine.dispose()
        return
    raw = os.getenv("C2_MENU_PG_URI")
    if not raw:
        pytest.skip("C2_MENU_PG_URI required for owned PostgreSQL proof")
    url = sa.make_url(raw)
    profile = additional_profile(url, "identity")
    owner = sa.create_engine(url)
    try:
        with owner.connect() as connection:
            require_server(connection, profile)
    except BaseException:
        owner.dispose()
        raise
    schema = "c23_" + uuid4().hex
    with owner.begin() as connection:
        connection.execute(sa.schema.CreateSchema(schema))
    engine = sa.create_engine(url.update_query_dict({"options": f"-csearch_path={schema}"}))
    try:
        yield engine
    finally:
        engine.dispose()
        with owner.begin() as connection:
            connection.execute(sa.schema.DropSchema(schema, cascade=True))
        owner.dispose()


def create_orm_schema(engine, *, strip_constraint=False, extra_unique=False):
    """Build the explicit pre-0023 raw schema, not current ORM metadata."""
    metadata = sa.MetaData()
    for model in (MenuMaster, MonthlyMenu, MonthlyMenuItem):
        model.__table__.to_metadata(metadata)
    monthly = metadata.tables["monthly_menu_items"]
    monthly.indexes.difference_update({item for item in monthly.indexes if item.name == IDENTITY_INDEX})
    if not strip_constraint:
        monthly.append_constraint(sa.UniqueConstraint(*RAW_COLUMNS, name=RAW_CONSTRAINT))
    if extra_unique:
        monthly.append_constraint(sa.UniqueConstraint("unit_type", name="uq_monthly_menu_items_unknown_unit_type"))
    metadata.create_all(engine)


def create_canonical_orm_schema(engine):
    """Build current ORM metadata exactly as a fresh bootstrap does."""
    metadata = sa.MetaData()
    for model in (MenuMaster, MonthlyMenu, MonthlyMenuItem):
        model.__table__.to_metadata(metadata)
    metadata.create_all(engine)


ITEM_COLUMNS = (
    "id, monthly_menu_id, menu_master_id, name, unit_type, qty_per_serving, temp_type, "
    "daypart, category, diet_type, facility_override, master_resolution_mode, bag_max_qty, bag_max_unit"
)


def insert_rows(engine, rows):
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO monthly_menus(id) VALUES ('month')"))
        connection.execute(sa.text(
            "INSERT INTO menu_masters(id, revision, name, normalized_name) "
            "VALUES ('master', 1, 'master', 'master')"
        ))
        connection.execute(
            sa.text(
                f"INSERT INTO monthly_menu_items({ITEM_COLUMNS}) VALUES ("
                ":id, 'month', :menu_master_id, 'fish', :unit_type, :qty_per_serving, :temp_type, "
                ":daypart, :category, :diet_type, :facility_override, :master_resolution_mode, "
                ":bag_max_qty, :bag_max_unit)"
            ),
            [{
                "menu_master_id": None,
                "unit_type": None,
                "qty_per_serving": None,
                "temp_type": None,
                "master_resolution_mode": None,
                "bag_max_qty": None,
                "bag_max_unit": None,
                **row,
            } for row in rows],
        )


def rows(engine):
    with engine.connect() as connection:
        return connection.execute(sa.text(f"SELECT {ITEM_COLUMNS} FROM monthly_menu_items ORDER BY id")).all()


def named_index_exists(engine, name):
    if engine.dialect.name == "postgresql":
        with engine.connect() as connection:
            return connection.execute(sa.text("SELECT to_regclass(:name) IS NOT NULL"), {"name": name}).scalar_one()
    with engine.connect() as connection:
        return connection.execute(sa.text("SELECT 1 FROM sqlite_master WHERE type='index' AND name=:name"), {"name": name}).first() is not None


def foreign_keys(engine):
    with engine.connect() as connection:
        return {
            (
                tuple(item["constrained_columns"]),
                item["referred_table"],
                tuple(item["referred_columns"]),
            )
            for item in sa.inspect(connection).get_foreign_keys("monthly_menu_items")
        }


EXPECTED_FOREIGN_KEYS = {
    (("monthly_menu_id",), "monthly_menus", ("id",)),
    (("menu_master_id",), "menu_masters", ("id",)),
}


def test_recognized_orm_constraint_replaces_to_single_canonical_identity(database):
    create_orm_schema(database)
    insert_rows(database, [{
        "id": "one", "menu_master_id": "master", "unit_type": "tray", "qty_per_serving": 12.5,
        "temp_type": "cold", "daypart": None, "category": None, "diet_type": None,
        "facility_override": None, "master_resolution_mode": "exact", "bag_max_qty": 24.0,
        "bag_max_unit": "bag",
    }])
    before = rows(database)
    with database.begin() as connection:
        run_upgrade(connection)
    assert rows(database) == before
    with database.connect() as connection:
        require_canonical_identity_schema(connection)
    assert named_index_exists(database, IDENTITY_INDEX)
    assert not named_index_exists(database, LEGACY_INDEX)
    assert foreign_keys(database) == EXPECTED_FOREIGN_KEYS


def test_fresh_orm_schema_matches_canonical_identity_and_distinguishes_all_scope_keys(database):
    create_canonical_orm_schema(database)
    with database.connect() as connection:
        require_canonical_identity_schema(connection)
    insert_rows(database, [
        {"id": "base", "daypart": "lunch", "category": "main", "diet_type": "regular", "facility_override": "A"},
        {"id": "daypart", "daypart": "dinner", "category": "main", "diet_type": "regular", "facility_override": "A"},
        {"id": "category", "daypart": "lunch", "category": "side", "diet_type": "regular", "facility_override": "A"},
        {"id": "diet", "daypart": "lunch", "category": "main", "diet_type": "soft", "facility_override": "A"},
        {"id": "facility", "daypart": "lunch", "category": "main", "diet_type": "regular", "facility_override": "B"},
    ])
    assert [row.id for row in rows(database)] == ["base", "category", "daypart", "diet", "facility"]
    with pytest.raises(sa.exc.IntegrityError):
        with database.begin() as connection:
            connection.execute(sa.text(
                "INSERT INTO monthly_menu_items(id, monthly_menu_id, name, daypart, category, diet_type, facility_override) "
                "VALUES ('duplicate', 'month', 'fish', 'lunch', 'main', 'regular', 'A')"
            ))


@pytest.mark.parametrize("null_column", ("daypart", "category", "diet_type", "facility_override"))
def test_fresh_orm_schema_rejects_null_empty_identity_collision_and_preserves_row_values(database, null_column):
    create_canonical_orm_schema(database)
    identity = {
        "daypart": "lunch",
        "category": "main",
        "diet_type": "regular",
        "facility_override": "A",
    }
    identity[null_column] = None
    insert_rows(database, [{
        "id": "one",
        "menu_master_id": "master",
        "unit_type": "tray",
        "qty_per_serving": 12.5,
        "temp_type": "cold",
        "master_resolution_mode": "exact",
        "bag_max_qty": 24.0,
        "bag_max_unit": "bag",
        **identity,
    }])
    before = rows(database)
    with pytest.raises(sa.exc.IntegrityError):
        with database.begin() as connection:
            connection.execute(sa.text(
                "INSERT INTO monthly_menu_items("
                "id, monthly_menu_id, menu_master_id, name, unit_type, qty_per_serving, "
                "daypart, category, diet_type, facility_override, bag_max_qty, bag_max_unit) "
                "VALUES ('duplicate', 'month', 'master', 'fish', 'tray', 99, "
                ":daypart, :category, :diet_type, :facility_override, 99, 'changed')"
            ), {name: "" if name == null_column else value for name, value in identity.items()})
    assert rows(database) == before
    preserved = before[0]
    assert (preserved.menu_master_id, preserved.qty_per_serving, preserved.bag_max_qty, preserved.bag_max_unit) == (
        "master", 12.5, 24.0, "bag"
    )


def create_recognized_index(connection, name):
    if name == LEGACY_INDEX:
        connection.execute(sa.text(
            "CREATE UNIQUE INDEX uq_monthly_menu_items_scope_name "
            "ON monthly_menu_items(monthly_menu_id, name, COALESCE(facility_override, ''))"
        ))
        return
    connection.execute(sa.text(
        "CREATE UNIQUE INDEX uq_monthly_menu_items_scope_identity "
        "ON monthly_menu_items(monthly_menu_id, name, COALESCE(daypart, ''), "
        "COALESCE(category, ''), COALESCE(diet_type, ''), COALESCE(facility_override, ''))"
    ))


@pytest.mark.parametrize(
    "coexisting_indexes",
    [(LEGACY_INDEX,), (IDENTITY_INDEX,), (LEGACY_INDEX, IDENTITY_INDEX)],
    ids=("raw-plus-legacy", "raw-plus-canonical", "raw-plus-both"),
)
def test_raw_constraint_and_recognized_indexes_coexist_to_one_canonical_identity(database, coexisting_indexes):
    create_orm_schema(database)
    with database.begin() as connection:
        for index in coexisting_indexes:
            create_recognized_index(connection, index)
    insert_rows(database, [{
        "id": "one", "menu_master_id": "master", "unit_type": "tray", "qty_per_serving": 12.5,
        "temp_type": "cold", "daypart": None, "category": None, "diet_type": None,
        "facility_override": None, "master_resolution_mode": "exact", "bag_max_qty": 24.0,
        "bag_max_unit": "bag",
    }])
    before = rows(database)
    with database.begin() as connection:
        run_upgrade(connection)
    assert rows(database) == before
    with database.connect() as connection:
        require_canonical_identity_schema(connection)
    assert foreign_keys(database) == EXPECTED_FOREIGN_KEYS


def test_sqlite_postcondition_failure_rolls_back_raw_constraint_indexes_and_rows(monkeypatch):
    engine = sa.create_engine("sqlite://")
    try:
        create_orm_schema(engine)
        with engine.begin() as connection:
            create_recognized_index(connection, LEGACY_INDEX)
        insert_rows(engine, [{
            "id": "one", "menu_master_id": "master", "unit_type": "tray", "qty_per_serving": 12.5,
            "temp_type": "cold", "daypart": None, "category": None, "diet_type": None,
            "facility_override": None, "master_resolution_mode": "exact", "bag_max_qty": 24.0,
            "bag_max_unit": "bag",
        }])
        before = rows(engine)
        migration = load_migration()

        def fail_postcondition(_connection):
            raise MonthlyMenuIdentitySchemaError("injected postcondition failure")

        monkeypatch.setattr(migration, "require_canonical_identity_schema", fail_postcondition)
        with pytest.raises(RuntimeError, match="injected postcondition failure"):
            with engine.begin() as connection:
                run_upgrade(connection, migration)
        assert rows(engine) == before
        assert named_index_exists(engine, LEGACY_INDEX)
        assert not named_index_exists(engine, IDENTITY_INDEX)
        with engine.connect() as connection:
            raw_constraints = {
                item.get("name") for item in sa.inspect(connection).get_unique_constraints("monthly_menu_items")
            }
        assert RAW_CONSTRAINT in raw_constraints
    finally:
        engine.dispose()


def test_legacy_create_failure_rolls_back_canonical_index_and_rows(database, monkeypatch):
    create_orm_schema(database, strip_constraint=True)
    with database.begin() as connection:
        run_upgrade(connection)
    insert_rows(database, [{
        "id": "one", "menu_master_id": "master", "unit_type": "tray", "qty_per_serving": 12.5,
        "temp_type": "cold", "daypart": None, "category": None, "diet_type": None,
        "facility_override": None, "master_resolution_mode": "exact", "bag_max_qty": 24.0,
        "bag_max_unit": "bag",
    }])
    before = rows(database)
    migration = load_migration()

    def fail_legacy_create(_connection):
        raise RuntimeError("injected legacy create failure")

    monkeypatch.setattr(migration, "_create_legacy_index", fail_legacy_create)
    with pytest.raises(RuntimeError, match="injected legacy create failure"):
        with database.begin() as connection:
            run_downgrade(connection, migration)
    assert rows(database) == before
    assert named_index_exists(database, IDENTITY_INDEX)
    assert not named_index_exists(database, LEGACY_INDEX)
    with database.connect() as connection:
        require_canonical_identity_schema(connection)


@pytest.mark.parametrize("state", ("unknown-canonical", "raw-constraint", "unknown-legacy"))
def test_downgrade_unknown_or_noncanonical_entrance_stops_before_ddl(database, state):
    create_orm_schema(database, strip_constraint=state != "raw-constraint")
    with database.begin() as connection:
        if state == "raw-constraint":
            create_recognized_index(connection, IDENTITY_INDEX)
        elif state == "unknown-canonical":
            connection.execute(sa.text(
                "CREATE UNIQUE INDEX uq_monthly_menu_items_scope_identity "
                "ON monthly_menu_items(monthly_menu_id, name)"
            ))
        else:
            run_upgrade(connection)
            connection.execute(sa.text(
                "CREATE INDEX uq_monthly_menu_items_scope_name ON monthly_menu_items(name)"
            ))
    before_indexes = {
        name for name in (IDENTITY_INDEX, LEGACY_INDEX) if named_index_exists(database, name)
    }
    with pytest.raises(RuntimeError, match="menu schema is not migrated"):
        with database.begin() as connection:
            run_downgrade(connection)
    assert {name for name in (IDENTITY_INDEX, LEGACY_INDEX) if named_index_exists(database, name)} == before_indexes


def test_sqlite_unknown_expression_index_stops_before_batch_recreate():
    engine = sa.create_engine("sqlite://")
    try:
        create_orm_schema(engine)
        with engine.begin() as connection:
            connection.execute(sa.text(
                "CREATE INDEX monthly_menu_items_unknown_expression "
                "ON monthly_menu_items(LOWER(name))"
            ))
        with pytest.raises(RuntimeError, match="would not preserve unknown monthly_menu_items indexes"):
            with engine.begin() as connection:
                run_upgrade(connection)
        assert named_index_exists(engine, "monthly_menu_items_unknown_expression")
        assert not named_index_exists(engine, IDENTITY_INDEX)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "daypart_expression",
    ("COALESCE(daypart, ' ')", "COALESCE(daypart, '\\t')", "COALESCE(daypart, '\\n')",
     "COALESCE(daypart, 'missing')", "IFNULL(daypart, '')"),
)
def test_sqlite_identity_literal_or_expression_spoof_stops_without_ddl(daypart_expression):
    engine = sa.create_engine("sqlite://")
    try:
        create_orm_schema(engine, strip_constraint=True)
        with engine.begin() as connection:
            connection.execute(sa.text(
                "CREATE UNIQUE INDEX uq_monthly_menu_items_scope_identity ON monthly_menu_items("
                f"monthly_menu_id, name, {daypart_expression}, COALESCE(category, ''), "
                "COALESCE(diet_type, ''), COALESCE(facility_override, ''))"
            ))
        insert_rows(engine, [{"id": "one", "daypart": None, "category": None, "diet_type": None,
                              "facility_override": None}])
        before = rows(engine)
        with pytest.raises(RuntimeError, match="unknown uq_monthly_menu_items_scope_identity"):
            with engine.begin() as connection:
                run_upgrade(connection)
        assert rows(engine) == before
        assert named_index_exists(engine, IDENTITY_INDEX)
        with engine.connect() as connection:
            with pytest.raises(MonthlyMenuIdentitySchemaError, match="six-key NULL-coalescing"):
                require_canonical_identity_schema(connection)
    finally:
        engine.dispose()


def test_sqlite_unknown_unique_constraint_stops_before_batch_recreate():
    engine = sa.create_engine("sqlite://")
    try:
        create_orm_schema(engine, extra_unique=True)
        with pytest.raises(RuntimeError, match="would not preserve unknown monthly_menu_items unique constraints"):
            with engine.begin() as connection:
                run_upgrade(connection)
        with engine.connect() as connection:
            unique_constraints = {
                item.get("name") for item in sa.inspect(connection).get_unique_constraints("monthly_menu_items")
            }
        assert RAW_CONSTRAINT in unique_constraints
        assert "uq_monthly_menu_items_unknown_unit_type" in unique_constraints
        assert not named_index_exists(engine, IDENTITY_INDEX)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        (
            "CREATE TRIGGER monthly_menu_items_unknown_trigger AFTER INSERT ON monthly_menu_items "
            "BEGIN SELECT 1; END",
            "would not preserve monthly_menu_items triggers",
        ),
        (
            "ALTER TABLE monthly_menu_items ADD COLUMN unknown_guard INTEGER CHECK (unknown_guard >= 0)",
            "would not preserve monthly_menu_items check constraints",
        ),
    ],
)
def test_sqlite_unknown_batch_objects_stop_before_recreate(sql, message):
    engine = sa.create_engine("sqlite://")
    try:
        create_orm_schema(engine)
        with engine.begin() as connection:
            connection.execute(sa.text(sql))
        with pytest.raises(RuntimeError, match=message):
            with engine.begin() as connection:
                run_upgrade(connection)
        assert not named_index_exists(engine, IDENTITY_INDEX)
    finally:
        engine.dispose()


def test_sqlite_unknown_foreign_key_stops_before_batch_recreate():
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    try:
        sa.Table("monthly_menus", metadata, sa.Column("id", sa.String, primary_key=True))
        sa.Table("menu_masters", metadata, sa.Column("id", sa.String, primary_key=True))
        sa.Table("unexpected_targets", metadata, sa.Column("id", sa.String, primary_key=True))
        sa.Table(
            "monthly_menu_items",
            metadata,
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("monthly_menu_id", sa.String, sa.ForeignKey("monthly_menus.id"), nullable=False),
            sa.Column("menu_master_id", sa.String, sa.ForeignKey("menu_masters.id")),
            sa.Column("unexpected_target_id", sa.String, sa.ForeignKey("unexpected_targets.id")),
            sa.Column("name", sa.String, nullable=False),
            sa.Column("daypart", sa.String),
            sa.Column("category", sa.String),
            sa.Column("diet_type", sa.String),
            sa.Column("facility_override", sa.String),
            sa.UniqueConstraint(*RAW_COLUMNS, name=RAW_CONSTRAINT),
        )
        metadata.create_all(engine)
        with pytest.raises(RuntimeError, match="would not preserve unknown monthly_menu_items foreign keys"):
            with engine.begin() as connection:
                run_upgrade(connection)
        assert not named_index_exists(engine, IDENTITY_INDEX)
    finally:
        engine.dispose()


def test_sqlite_incoming_cascade_foreign_key_stops_before_batch_recreate():
    engine = sa.create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        create_orm_schema(engine)
        with engine.begin() as connection:
            connection.execute(sa.text(
                "CREATE TABLE monthly_menu_item_children("
                "id TEXT PRIMARY KEY, monthly_menu_item_id TEXT NOT NULL, "
                "FOREIGN KEY(monthly_menu_item_id) REFERENCES monthly_menu_items(id) ON DELETE CASCADE)"
            ))
            connection.execute(sa.text(
                "CREATE INDEX ix_monthly_menu_item_children_item ON monthly_menu_item_children(monthly_menu_item_id)"
            ))
        insert_rows(engine, [{
            "id": "one", "menu_master_id": "master", "unit_type": "tray", "qty_per_serving": 12.5,
            "temp_type": "cold", "daypart": None, "category": None, "diet_type": None,
            "facility_override": None, "master_resolution_mode": "exact", "bag_max_qty": 24.0,
            "bag_max_unit": "bag",
        }])
        with engine.begin() as connection:
            connection.execute(sa.text(
                "INSERT INTO monthly_menu_item_children(id, monthly_menu_item_id) VALUES ('child', 'one')"
            ))
        before_parent = rows(engine)
        with engine.connect() as connection:
            before_child = connection.execute(sa.text(
                "SELECT id, monthly_menu_item_id FROM monthly_menu_item_children"
            )).all()
            before_indexes = connection.execute(sa.text(
                "SELECT tbl_name, name, COALESCE(sql, '') FROM sqlite_master "
                "WHERE type='index' ORDER BY tbl_name, name"
            )).all()
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        with pytest.raises(RuntimeError, match="would not preserve incoming monthly_menu_items foreign keys"):
            with engine.begin() as connection:
                run_upgrade(connection)
        assert rows(engine) == before_parent
        with engine.connect() as connection:
            assert connection.execute(sa.text(
                "SELECT id, monthly_menu_item_id FROM monthly_menu_item_children"
            )).all() == before_child
            assert connection.execute(sa.text(
                "SELECT tbl_name, name, COALESCE(sql, '') FROM sqlite_master "
                "WHERE type='index' ORDER BY tbl_name, name"
            )).all() == before_indexes
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert not named_index_exists(engine, IDENTITY_INDEX)
    finally:
        engine.dispose()


def test_recognized_legacy_index_rerun_and_rollback_preserve_rows(database):
    create_orm_schema(database, strip_constraint=True)
    with database.begin() as connection:
        connection.execute(sa.text(
            "CREATE UNIQUE INDEX uq_monthly_menu_items_scope_name "
            "ON monthly_menu_items(monthly_menu_id, name, COALESCE(facility_override, ''))"
        ))
    insert_rows(database, [{"id": "one", "daypart": "lunch", "category": None, "diet_type": None, "facility_override": None}])
    before = rows(database)
    with database.begin() as connection:
        run_upgrade(connection)
    with database.begin() as connection:
        run_upgrade(connection)
    with database.connect() as connection:
        require_canonical_identity_schema(connection)
    with database.begin() as connection:
        run_downgrade(connection)
    assert rows(database) == before
    assert named_index_exists(database, LEGACY_INDEX)
    assert not named_index_exists(database, IDENTITY_INDEX)


def test_unknown_same_name_schema_stops_before_ddl_and_preserves_rows(database):
    create_orm_schema(database, strip_constraint=True)
    with database.begin() as connection:
        connection.execute(sa.text("CREATE UNIQUE INDEX uq_monthly_menu_items_scope_name ON monthly_menu_items(monthly_menu_id, name)"))
    insert_rows(database, [{"id": "one", "daypart": None, "category": None, "diet_type": None, "facility_override": None}])
    before = rows(database)
    with pytest.raises(RuntimeError, match="unknown uq_monthly_menu_items_scope_name"):
        with database.begin() as connection:
            run_upgrade(connection)
    assert rows(database) == before
    assert named_index_exists(database, LEGACY_INDEX)
    assert not named_index_exists(database, IDENTITY_INDEX)


def test_null_coalescing_duplicate_stops_before_ddl_and_rolls_back(database):
    create_orm_schema(database)
    insert_rows(database, [
        {"id": "one", "daypart": None, "category": None, "diet_type": None, "facility_override": None},
        {"id": "two", "daypart": None, "category": None, "diet_type": None, "facility_override": None},
    ])
    before = rows(database)
    with pytest.raises(RuntimeError, match="NULL-coalescing"):
        with database.begin() as connection:
            run_upgrade(connection)
    assert rows(database) == before
    assert not named_index_exists(database, IDENTITY_INDEX)


def test_runtime_guard_rejects_same_name_spoof(database):
    create_orm_schema(database, strip_constraint=True)
    with database.begin() as connection:
        connection.execute(sa.text("CREATE UNIQUE INDEX uq_monthly_menu_items_scope_identity ON monthly_menu_items(monthly_menu_id, name)"))
    with database.connect() as connection:
        with pytest.raises(MonthlyMenuIdentitySchemaError, match="six-key NULL-coalescing"):
            require_canonical_identity_schema(connection)


def test_empty_pristine_bootstrap_is_canonicalized_without_data(database):
    create_orm_schema(database, strip_constraint=True)
    with database.begin() as connection:
        run_upgrade(connection)
    with database.connect() as connection:
        require_canonical_identity_schema(connection)


def test_downgrade_three_key_collision_stops_and_preserves_canonical_rows(database):
    create_orm_schema(database, strip_constraint=True)
    with database.begin() as connection:
        connection.execute(sa.text(
            "CREATE UNIQUE INDEX uq_monthly_menu_items_scope_name "
            "ON monthly_menu_items(monthly_menu_id, name, COALESCE(facility_override, ''))"
        ))
    insert_rows(database, [
        {"id": "one", "daypart": "breakfast", "category": None, "diet_type": None, "facility_override": None},
    ])
    with database.begin() as connection:
        run_upgrade(connection)
        connection.execute(sa.text(
            "INSERT INTO monthly_menu_items(id, monthly_menu_id, name, daypart) "
            "VALUES ('two', 'month', 'fish', 'lunch')"
        ))
    before = rows(database)
    with pytest.raises(RuntimeError, match="three-key legacy identity collision"):
        with database.begin() as connection:
            run_downgrade(connection)
    assert rows(database) == before
    with database.connect() as connection:
        require_canonical_identity_schema(connection)


@pytest.mark.parametrize("database", ["postgresql"], indirect=True)
def test_runtime_guard_rejects_descending_same_name_identity_index(database):
    assert database.dialect.name == "postgresql"
    create_orm_schema(database, strip_constraint=True)
    with database.begin() as connection:
        connection.execute(sa.text(
            "CREATE UNIQUE INDEX uq_monthly_menu_items_scope_identity "
            "ON monthly_menu_items(monthly_menu_id DESC, name, COALESCE(daypart, ''), "
            "COALESCE(category, ''), COALESCE(diet_type, ''), COALESCE(facility_override, ''))"
        ))
    with database.connect() as connection:
        with pytest.raises(MonthlyMenuIdentitySchemaError, match="six-key NULL-coalescing"):
            require_canonical_identity_schema(connection)


def test_fresh_full_chain_reaches_canonical_identity_schema(database):
    with database.begin() as connection:
        run_full_chain(connection)
    with database.connect() as connection:
        require_canonical_identity_schema(connection)
