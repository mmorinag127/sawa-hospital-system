"""SQLite and isolated PostgreSQL proofs for ordinary menu editor CAS contracts."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4
import importlib.util
import os

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from src import db
from src.models.menu import MenuMaster, MenuRule
from src.services import base_menu_service, menu_edit_revision_service, menu_rule_service


ROOT = Path(__file__).resolve().parents[3]


def _migration_module():
    spec = importlib.util.spec_from_file_location("migration_0030", ROOT / "backend/migrations/0030_menu_editor_revisions.py")
    migration = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(migration)
    return migration


def _apply_0030(connection):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    with Operations.context(MigrationContext.configure(connection)):
        _migration_module().upgrade()


def _downgrade_0030(connection):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    with Operations.context(MigrationContext.configure(connection)):
        _migration_module().downgrade()


def _pre0030_metadata():
    metadata = sa.MetaData()
    sa.Table("base_menu_cycle_items", metadata, sa.Column("id", sa.String(), primary_key=True), sa.Column("cycle_day", sa.Integer(), nullable=False), sa.Column("daypart", sa.String()), sa.Column("category", sa.String()), sa.Column("name", sa.String(), nullable=False), sa.Column("diet_type", sa.String()), sa.Column("slot_index", sa.Integer()))
    sa.Table("menu_rules", metadata, sa.Column("id", sa.String(), primary_key=True), sa.Column("rule_type", sa.String(), nullable=False), sa.Column("match_type", sa.String()), sa.Column("menu_pattern", sa.String()), sa.Column("facility_id", sa.String()), sa.Column("daypart", sa.String()), sa.Column("category", sa.String()), sa.Column("diet_type", sa.String()), sa.Column("unit_type", sa.String()), sa.Column("qty_per_serving", sa.Float()), sa.Column("priority", sa.Integer()), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False))
    MenuMaster.__table__.to_metadata(metadata)
    return metadata


@pytest.fixture(params=["sqlite", "postgresql"])
def revision_store(request, tmp_path, monkeypatch):
    owner = None
    schema = None
    if request.param == "sqlite":
        engine = sa.create_engine(sa.URL.create("sqlite", database=str(tmp_path / "revisions.sqlite3")), connect_args={"check_same_thread": False, "timeout": 10})
    else:
        uri = os.getenv("C1_POSTGRES_URI")
        if not uri:
            pytest.skip("C1_POSTGRES_URI required for dedicated PostgreSQL revision proof")
        owner = sa.create_engine(uri)
        schema = f"menu_editor_revision_{uuid4().hex}"
        with owner.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        engine = sa.create_engine(sa.make_url(uri).update_query_dict({"options": f"-csearch_path={schema}"}))
    try:
        _pre0030_metadata().create_all(engine)
        with engine.begin() as connection:
            _apply_0030(connection)
        monkeypatch.setattr(db, "SessionLocal", sessionmaker(engine, autoflush=False))
        monkeypatch.setattr(base_menu_service, "engine", engine)
        yield engine
    finally:
        engine.dispose()
        if owner is not None and schema is not None:
            with owner.begin() as connection:
                connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
            owner.dispose()


def test_0030_migrates_pre0030_schema_and_creates_explicit_scopes(revision_store):
    with revision_store.connect() as connection:
        assert "revision" in {column["name"] for column in sa.inspect(connection).get_columns("menu_rules")}
        scopes = connection.execute(sa.text("SELECT scope, revision FROM menu_edit_revisions ORDER BY scope")).all()
    assert scopes == [("base-menu-cycle-items", 1), ("menu-rules:facility", 1), ("menu-rules:global", 1), ("menu-rules:menu", 1)]


def test_0030_downgrade_removes_only_new_revision_columns(tmp_path):
    engine = sa.create_engine(sa.URL.create("sqlite", database=str(tmp_path / "new-revisions.sqlite3")))
    try:
        _pre0030_metadata().create_all(engine)
        with engine.begin() as connection:
            _apply_0030(connection)
            ownership = connection.execute(sa.text(
                "SELECT table_name, ownership FROM menu_editor_revision_column_ownership ORDER BY table_name"
            )).all()
            assert ownership == [("base_menu_cycle_items", "0030"), ("menu_rules", "0030")]
            _downgrade_0030(connection)
            inspector = sa.inspect(connection)
            assert "revision" not in {column["name"] for column in inspector.get_columns("base_menu_cycle_items")}
            assert "revision" not in {column["name"] for column in inspector.get_columns("menu_rules")}
            assert not inspector.has_table("menu_edit_revisions")
            assert not inspector.has_table("menu_editor_revision_column_ownership")
    finally:
        engine.dispose()


def test_0030_downgrade_preserves_preexisting_valid_revision_columns_and_data(tmp_path):
    engine = sa.create_engine(sa.URL.create("sqlite", database=str(tmp_path / "preexisting-revisions.sqlite3")))
    try:
        metadata = _pre0030_metadata()
        for table in ("base_menu_cycle_items", "menu_rules"):
            metadata.tables[table].append_column(sa.Column("revision", sa.Integer(), nullable=False))
        metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(sa.text(
                "INSERT INTO menu_rules(id, rule_type, active, created_at, updated_at, revision) "
                "VALUES ('existing-rule', 'global', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 7)"
            ))
            connection.execute(sa.text(
                "INSERT INTO base_menu_cycle_items(id, cycle_day, name, revision) "
                "VALUES ('existing-item', 1, 'Rice', 9)"
            ))
            _apply_0030(connection)
            _downgrade_0030(connection)
            assert connection.execute(sa.text("SELECT revision FROM menu_rules WHERE id = 'existing-rule'")).scalar_one() == 7
            assert connection.execute(sa.text("SELECT revision FROM base_menu_cycle_items WHERE id = 'existing-item'")).scalar_one() == 9
            inspector = sa.inspect(connection)
            assert "revision" in {column["name"] for column in inspector.get_columns("menu_rules")}
            assert "revision" in {column["name"] for column in inspector.get_columns("base_menu_cycle_items")}
            assert not inspector.has_table("menu_edit_revisions")
            assert not inspector.has_table("menu_editor_revision_column_ownership")
    finally:
        engine.dispose()


def test_0030_downgrade_stops_before_ddl_for_invalid_ownership_metadata(tmp_path):
    engine = sa.create_engine(sa.URL.create("sqlite", database=str(tmp_path / "invalid-ownership.sqlite3")))
    try:
        _pre0030_metadata().create_all(engine)
        with engine.begin() as connection:
            _apply_0030(connection)
            connection.execute(sa.text(
                "DELETE FROM menu_editor_revision_column_ownership WHERE table_name = 'menu_rules'"
            ))
            before_tables = set(sa.inspect(connection).get_table_names())
            before_columns = {
                table: tuple(column["name"] for column in sa.inspect(connection).get_columns(table))
                for table in ("base_menu_cycle_items", "menu_rules")
            }
            with pytest.raises(RuntimeError, match="ownership metadata is incomplete or unknown"):
                _downgrade_0030(connection)
            assert set(sa.inspect(connection).get_table_names()) == before_tables
            assert {
                table: tuple(column["name"] for column in sa.inspect(connection).get_columns(table))
                for table in ("base_menu_cycle_items", "menu_rules")
            } == before_columns
    finally:
        engine.dispose()


def test_collection_read_keeps_existing_transaction_and_supports_repeated_reads(revision_store):
    with db.SessionLocal() as session:
        scope = menu_edit_revision_service.BASE_MENU_SCOPE
        if revision_store.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN")
        assert menu_edit_revision_service.read_locked_current(session, scope) == 1
        transaction = session.get_transaction()
        assert menu_edit_revision_service.read_locked_current(session, scope) == 1
        assert session.get_transaction() is transaction
        if revision_store.dialect.name == "sqlite":
            assert session.connection().connection.driver_connection.in_transaction
        session.rollback()


def test_0030_rejects_invalid_existing_revision_schema(tmp_path):
    engine = sa.create_engine(sa.URL.create("sqlite", database=str(tmp_path / "invalid.sqlite3")))
    try:
        metadata = _pre0030_metadata()
        metadata.tables["menu_rules"].append_column(sa.Column("revision", sa.String(), nullable=True))
        metadata.create_all(engine)
        with engine.begin() as connection:
            with pytest.raises(RuntimeError, match="menu_rules.revision"):
                _apply_0030(connection)
    finally:
        engine.dispose()


@pytest.mark.parametrize("value", [0, -1])
def test_0030_rejects_nonpositive_existing_revision_before_any_ddl(tmp_path, value):
    engine = sa.create_engine(sa.URL.create("sqlite", database=str(tmp_path / f"invalid-{value}.sqlite3")))
    try:
        metadata = _pre0030_metadata()
        metadata.tables["menu_rules"].append_column(sa.Column("revision", sa.Integer(), nullable=False))
        metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(sa.text("INSERT INTO menu_rules(id, rule_type, active, created_at, updated_at, revision) VALUES ('bad', 'global', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :revision)"), {"revision": value})
            with pytest.raises(RuntimeError, match="non-positive"):
                _apply_0030(connection)
            assert "revision" not in {column["name"] for column in sa.inspect(connection).get_columns("base_menu_cycle_items")}
            assert not sa.inspect(connection).has_table("menu_edit_revisions")
    finally:
        engine.dispose()


def test_0030_rejects_null_existing_revision_without_repair(tmp_path):
    engine = sa.create_engine(sa.URL.create("sqlite", database=str(tmp_path / "invalid-null.sqlite3")))
    try:
        metadata = _pre0030_metadata()
        metadata.tables["menu_rules"].append_column(sa.Column("revision", sa.Integer(), nullable=True))
        metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(sa.text("INSERT INTO menu_rules(id, rule_type, active, created_at, updated_at, revision) VALUES ('bad', 'global', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)"))
            with pytest.raises(RuntimeError, match="INTEGER NOT NULL"):
                _apply_0030(connection)
            assert "revision" not in {column["name"] for column in sa.inspect(connection).get_columns("base_menu_cycle_items")}
    finally:
        engine.dispose()


def test_base_full_replace_requires_current_read_version_and_source_master(revision_store):
    initial = base_menu_service.list_items()
    saved = base_menu_service.replace_items([{"cycle_day": 1, "name": "Fish"}], initial["revision"])
    assert saved["created"] == 1 and saved["revision"] != initial["revision"]
    with pytest.raises(menu_edit_revision_service.RevisionConflict):
        base_menu_service.replace_items([], initial["revision"])
    with db.session_scope() as session:
        session.add(MenuMaster(id="master", name="Fish", normalized_name="fish", qty_per_serving=10))
    stale_source = base_menu_service.list_items()
    with db.session_scope() as session:
        session.get(MenuMaster, "master").qty_per_serving = 20
    with pytest.raises(menu_edit_revision_service.RevisionConflict):
        base_menu_service.replace_items(stale_source["items"], stale_source["revision"])


def test_base_replace_rejects_actual_partial_get_snapshot(revision_store):
    initial = base_menu_service.list_items()
    base_menu_service.replace_items(
        [{"cycle_day": 1, "name": "Fish"}, {"cycle_day": 2, "name": "Rice"}], initial["revision"]
    )
    full = base_menu_service.list_items()
    partial = base_menu_service.list_items(cycle_day=1)
    full_token = menu_edit_revision_service.decode_token(full["revision"], menu_edit_revision_service.BASE_MENU_SCOPE)
    partial_token = menu_edit_revision_service.decode_token(partial["revision"], menu_edit_revision_service.BASE_MENU_SCOPE)
    assert full_token.revision == partial_token.revision
    assert full_token.query == {"cycle_day": None} and full_token.coverage == "full"
    assert partial_token.query == {"cycle_day": 1} and partial_token.coverage == "partial"
    with pytest.raises(menu_edit_revision_service.RevisionConflict, match="full base-menu snapshot"):
        base_menu_service.replace_items(partial["items"], partial["revision"])
    assert len(base_menu_service.list_items()["items"]) == 2


def test_base_full_get_snapshot_allows_create_delete_replacement(revision_store):
    initial = base_menu_service.list_items()
    base_menu_service.replace_items(
        [{"cycle_day": 1, "name": "Fish"}, {"cycle_day": 2, "name": "Rice"}], initial["revision"]
    )
    full = base_menu_service.list_items()
    saved = base_menu_service.replace_items([full["items"][0]], full["revision"])
    assert saved["created"] == 1
    assert [item["name"] for item in saved["items"]] == ["Fish"]


def test_base_normal_update_keeps_the_collection_contract(revision_store):
    initial = base_menu_service.list_items()
    created = base_menu_service.replace_items([{"cycle_day": 1, "name": "Fish"}], initial["revision"])
    item = created["items"][0]
    updated = base_menu_service.update_item(item["id"], {"category": "main"}, created["revision"])
    assert updated is not None
    with db.session_scope() as session:
        assert session.get(base_menu_service.BaseMenuCycleItem, item["id"]).category == "main"


@pytest.mark.parametrize("revision", [0, -1])
def test_source_master_with_invalid_persisted_revision_is_an_explicit_stop(revision_store, revision):
    initial = base_menu_service.list_items()
    base_menu_service.replace_items([{"cycle_day": 1, "name": "Fish"}], initial["revision"])
    with db.session_scope() as session:
        master = MenuMaster(id="master", name="Fish", normalized_name="fish", qty_per_serving=10)
        session.add(master)
        session.flush()
        session.execute(sa.text("UPDATE menu_masters SET revision = :revision WHERE id = 'master'"), {"revision": revision})
    with pytest.raises(menu_edit_revision_service.RevisionConflict, match="source revisions"):
        base_menu_service.list_items()


def test_source_master_null_revision_is_an_explicit_stop():
    with pytest.raises(menu_edit_revision_service.RevisionConflict, match="source revisions"):
        menu_edit_revision_service.encode(
            menu_edit_revision_service.BASE_MENU_SCOPE,
            1,
            [("master", None)],
            query={"cycle_day": None},
            coverage="full",
        )


@pytest.mark.parametrize("revision", [0, -1])
def test_invalid_stored_scope_revision_is_an_explicit_stop(revision_store, revision):
    with db.session_scope() as session:
        session.execute(
            sa.text("UPDATE menu_edit_revisions SET revision = :revision WHERE scope = :scope"),
            {"revision": revision, "scope": menu_edit_revision_service.BASE_MENU_SCOPE},
        )
    with pytest.raises(menu_edit_revision_service.RevisionConflict, match="invalid stored"):
        base_menu_service.list_items()


def test_decode_rejects_boolean_revision_token(revision_store):
    token = menu_edit_revision_service.encode(menu_edit_revision_service.BASE_MENU_SCOPE, 1)
    import base64
    import json

    payload = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    payload["revision"] = True
    invalid = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    with pytest.raises(menu_edit_revision_service.RevisionConflict):
        menu_edit_revision_service.decode(invalid, menu_edit_revision_service.BASE_MENU_SCOPE)


@pytest.mark.parametrize(
    ("query", "coverage"),
    [({"cycle_day": 1}, "full"), ({"cycle_day": None}, "partial")],
)
def test_encode_rejects_query_coverage_mismatch(query, coverage):
    with pytest.raises(menu_edit_revision_service.RevisionConflict, match="coverage"):
        menu_edit_revision_service.encode(
            menu_edit_revision_service.BASE_MENU_SCOPE,
            1,
            query=query,
            coverage=coverage,
        )


def test_encode_rejects_duplicate_source_keys():
    with pytest.raises(menu_edit_revision_service.RevisionConflict, match="duplicate"):
        menu_edit_revision_service.encode(
            menu_edit_revision_service.BASE_MENU_SCOPE,
            1,
            [("master", 1), ("master", 2)],
        )


@pytest.mark.parametrize(
    ("sources", "query", "coverage"),
    [
        ([["master", 1], ["master", 2]], {"cycle_day": None}, "full"),
        ([], {"cycle_day": 1}, "full"),
        ([], {"cycle_day": None}, "partial"),
    ],
)
def test_decode_rejects_duplicate_sources_and_query_coverage_mismatches(sources, query, coverage):
    import base64
    import json

    payload = {
        "scope": menu_edit_revision_service.BASE_MENU_SCOPE,
        "revision": 1,
        "sources": sources,
        "query": query,
        "coverage": coverage,
    }
    token = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    with pytest.raises(menu_edit_revision_service.RevisionConflict):
        menu_edit_revision_service.decode_token(token, menu_edit_revision_service.BASE_MENU_SCOPE)


def test_base_replace_rejects_duplicate_source_snapshot_token(revision_store):
    import base64
    import json

    before = base_menu_service.list_items()
    payload = {
        "scope": menu_edit_revision_service.BASE_MENU_SCOPE,
        "revision": 1,
        "sources": [["master", 1], ["master", 2]],
        "query": {"cycle_day": None},
        "coverage": "full",
    }
    token = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    with pytest.raises(menu_edit_revision_service.RevisionConflict, match="duplicate"):
        base_menu_service.replace_items([], token)
    assert base_menu_service.list_items() == before


def test_rule_create_update_delete_require_current_version_and_rollback(revision_store):
    before = menu_rule_service.list_rules("global")
    with pytest.raises(menu_edit_revision_service.RevisionPreconditionRequired):
        menu_rule_service.create_rule({"rule_type": "global"}, None)
    created = menu_rule_service.create_rule({"rule_type": "global", "daypart": "昼食"}, before["revision"])["rule"]
    stale = before["revision"]
    current = menu_rule_service.list_rules("global")
    with pytest.raises(menu_edit_revision_service.RevisionConflict):
        menu_rule_service.update_rule(created["id"], {"daypart": "夕食"}, stale)
    with db.session_scope() as session:
        assert session.get(MenuRule, created["id"]).daypart == "昼食"
    assert menu_rule_service.delete_rule(created["id"], current["revision"])["deleted"] is True


def test_missing_item_does_not_advance_base_scope(revision_store):
    before = base_menu_service.list_items()
    assert base_menu_service.update_item("missing", {"name": "ignored"}, before["revision"]) is None
    assert base_menu_service.list_items()["revision"] == before["revision"]


def test_parallel_transactions_have_one_cas_winner(revision_store):
    baseline = menu_rule_service.list_rules("menu")["revision"]
    barrier = Barrier(2)

    def create(name):
        barrier.wait()
        try:
            return menu_rule_service.create_rule({"rule_type": "menu", "match_type": "exact", "menu_pattern": name}, baseline)
        except menu_edit_revision_service.RevisionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, ("A", "B")))
    assert sum(result is not None for result in results) == 1


def test_actual_get_source_snapshot_interleaves_with_master_change_and_rejects_dirty_save(revision_store, monkeypatch):
    initial = base_menu_service.list_items()
    base_menu_service.replace_items([{"cycle_day": 1, "name": "Fish"}], initial["revision"])
    with db.session_scope() as session:
        session.add(MenuMaster(id="master", name="Fish", normalized_name="fish", qty_per_serving=10))

    dirty_read = base_menu_service.list_items()
    rules_read = menu_rule_service.list_rules("global")
    scope_before = menu_edit_revision_service.decode(
        dirty_read["revision"], menu_edit_revision_service.BASE_MENU_SCOPE
    )[0]
    resolver_finished = Event()
    writer_ready = Event()
    start = Barrier(2, timeout=5)
    interleave = Barrier(2, timeout=5)
    actual_resolver = base_menu_service._defaults_and_sources

    def observed_actual_resolver(session, items, *, lock_sources):
        defaults, sources = actual_resolver(session, items, lock_sources=lock_sources)
        assert lock_sources is True
        resolver_finished.set()
        interleave.wait()
        assert writer_ready.wait(timeout=5)
        return defaults, sources

    monkeypatch.setattr(base_menu_service, "_defaults_and_sources", observed_actual_resolver)

    def reader():
        start.wait()
        return base_menu_service.list_items()

    def source_writer():
        start.wait()
        assert resolver_finished.wait(timeout=5)
        interleave.wait()
        with db.session_scope() as session:
            master = session.get(MenuMaster, "master")
            assert master is not None
            master.qty_per_serving = 20
            writer_ready.set()
            session.flush()
            return master.revision

    executor = ThreadPoolExecutor(max_workers=2)
    reader_result = executor.submit(reader)
    writer_result = executor.submit(source_writer)
    try:
        snapshot = reader_result.result(timeout=15)
        source_revision = writer_result.result(timeout=15)
    except Exception:
        start.abort()
        interleave.abort()
        reader_result.cancel()
        writer_result.cancel()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        monkeypatch.setattr(base_menu_service, "_defaults_and_sources", actual_resolver)

    assert rules_read["revision"] == menu_rule_service.list_rules("global")["revision"]
    assert snapshot["items"][0]["qty_per_serving"] == 10
    snapshot_scope, snapshot_sources = menu_edit_revision_service.decode(
        snapshot["revision"], menu_edit_revision_service.BASE_MENU_SCOPE
    )
    assert snapshot_scope == scope_before
    assert snapshot_sources == [("master", source_revision - 1)]

    current = base_menu_service.list_items()
    assert current["items"][0]["qty_per_serving"] == 20
    assert current["revision"] != snapshot["revision"]
    with pytest.raises(menu_edit_revision_service.RevisionConflict):
        base_menu_service.replace_items(dirty_read["items"], dirty_read["revision"])
    unchanged = base_menu_service.list_items()
    assert unchanged == current


def test_missing_scope_is_a_blocker_without_runtime_repair(revision_store):
    with db.session_scope() as session:
        session.delete(session.get(menu_edit_revision_service.MenuEditRevision, menu_edit_revision_service.rule_scope("facility")))
    with pytest.raises(menu_edit_revision_service.RevisionSchemaNotMigrated):
        menu_rule_service.list_rules("facility")
