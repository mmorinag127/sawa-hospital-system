"""Preservation and refusal proofs on captured schemas using an owned real PostgreSQL."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from src.maintenance import formal_migration_schema as schema
from src.maintenance import live_schema_adoption as adoption
from live_schema_adoption_support import owned_postgres, restore_captured, target, ROOT


SOURCE_CONTRACT = ROOT / "backend/alembic/catalog_contracts.json"
PRESERVED_ORDER_TABLES = (
    "orders",
    "order_sheet_drafts",
    "order_confirmed_snapshots",
    "order_workflow_states",
    "order_lines",
    "monthly_menus",
    "base_menu_cycle_items",
    "menu_rules",
)


@pytest.fixture(autouse=True)
def generated_contract(monkeypatch):
    value = os.environ.get("LIVE_SCHEMA_ADOPTION_CONTRACT")
    if value is None:
        return
    path = Path(value)
    assert path.is_file()
    monkeypatch.setattr(schema, "CONTRACT", path)
    monkeypatch.setattr(adoption, "CONTRACT", path)


def captured(environment):
    contract = json.loads(schema.CONTRACT.read_text())
    baseline = contract["adoption_baselines"]["live-20261014-" + environment]
    return {table: contract["shapes"][ref] for table, ref in baseline["tables"].items()}


def test_generated_contract_preserves_reviewed_baselines_and_uses_fresh_postgresql_catalog():
    generated_path = os.environ.get("LIVE_SCHEMA_ADOPTION_CONTRACT")
    if generated_path is None:
        pytest.skip("generated contract is supplied only by the GitHub Actions proof")
    generated = json.loads(Path(generated_path).read_text())
    source = json.loads(SOURCE_CONTRACT.read_text())
    assert generated["adoption_baselines"] == source["adoption_baselines"]
    catalog_path = Path(generated_path).parent / "postgresql-0035.json"
    catalog = json.loads(catalog_path.read_text())
    expected = generated["revisions"]["postgresql"]["0035"]
    assert expected["tables"] == {
        table: hashlib.sha256(
            json.dumps(shape, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for table, shape in schema.table_shapes(catalog, "postgresql").items()
    }


def preserved_rows(conn):
    contract = json.loads(schema.CONTRACT.read_text())
    baseline = contract["adoption_baselines"]["live-20261014-stg"]
    result = {}
    quote = conn.dialect.identifier_preparer.quote
    for table in PRESERVED_ORDER_TABLES:
        columns = [column["name"] for column in contract["shapes"][baseline["tables"][table]]["columns"]]
        statement = "SELECT " + ",".join(quote(column) for column in columns)
        statement += " FROM " + quote(table) + " ORDER BY " + quote(columns[0])
        result[table] = [
            json.dumps(dict(row), sort_keys=True, default=str, ensure_ascii=False)
            for row in conn.exec_driver_sql(statement).mappings()
        ]
    return result


def insert_saved_order_fixture(conn):
    conn.execute(sa.text("""
        INSERT INTO orders(id,facility_code,week_code,status,current_document_id,superseded_document_ids,
                           document_uri,message_id,received_at,lines_updated_at,archived_at,archived_by,template_version_id)
        VALUES ('saved-order','FAC-01','2026-W42','confirmed','document-selected',CAST(:superseded AS JSON),
                'gs://private/saved-order.pdf','message-saved',TIMESTAMP '2026-10-14 09:00:00',
                TIMESTAMP '2026-10-14 09:02:00',NULL,NULL,NULL)
    """), {"superseded": json.dumps(["document-old"])})
    conn.execute(sa.text("""
        INSERT INTO order_sheet_drafts(id,order_id,base_evidence_run_id,base_template_resolution_id,base_menu_snapshot_id,
                                       draft_sheet_json,draft_state,blockers_json,warnings_json,latest_patch_candidate_id,
                                       edited_by,edited_at,created_at,template_version_id)
        VALUES ('saved-draft','saved-order',NULL,'resolution-selected','menu-snapshot-selected',CAST(:sheet AS JSON),
                'reviewed',CAST(:blockers AS JSON),CAST(:warnings AS JSON),'candidate-selected','operator',
                TIMESTAMP '2026-10-14 09:03:00',TIMESTAMP '2026-10-14 09:01:00',NULL)
    """), {
        "sheet": json.dumps({"rows": [["regular", 17.5]], "unknown_fixture_field": {"retain": ["value", 3]}}),
        "blockers": json.dumps([]),
        "warnings": json.dumps([{"code": "reviewed", "unknown_fixture_field": True}]),
    })
    conn.execute(sa.text("""
        INSERT INTO order_confirmed_snapshots(id,order_id,draft_id,snapshot_digest,snapshot_json,confirmed_by,
                                              confirmed_at,created_at,template_version_id,saved_sheet_id,
                                              bagging_result_id,output_bundle_id)
        VALUES ('saved-confirmation','saved-order','saved-draft','digest-saved',CAST(:snapshot AS JSON),'operator',
                TIMESTAMP '2026-10-14 09:04:00',TIMESTAMP '2026-10-14 09:04:00',NULL,'saved-draft',NULL,NULL)
    """), {"snapshot": json.dumps({"quantity": 17.5, "selected_id": "saved-draft", "unknown_fixture_field": "keep"})})
    conn.execute(sa.text("""
        INSERT INTO order_workflow_states(order_id,evidence_run_id,draft_id,confirmed_snapshot_id,state,headline,
                                          primary_action,secondary_actions_json,blockers_json,warnings_json,
                                          confidence_band,last_transition_at,template_version_id)
        VALUES ('saved-order',NULL,'saved-draft','saved-confirmation','confirmed','保存済み注文','none',
                CAST(:actions AS JSON),CAST(:blockers AS JSON),CAST(:warnings AS JSON),'high',
                TIMESTAMP '2026-10-14 09:04:00',NULL)
    """), {
        "actions": json.dumps(["view"]),
        "blockers": json.dumps([]),
        "warnings": json.dumps([{"selected_id": "saved-confirmation", "unknown_fixture_field": {"keep": 1}}]),
    })
    conn.execute(sa.text("""
        INSERT INTO order_lines(id,order_id,line_id,date,daypart,menu_name,diet_type,area_id,bag_type,
                                quantity_original,quantity_corrected,change_note,confirmed_snapshot_id,line_digest)
        VALUES ('saved-line','saved-order','line-1',DATE '2026-10-15','lunch','menu','regular','A','bag',17.5,18.0,
                'confirmed quantity','saved-confirmation','line-digest')
    """))
    conn.execute(sa.text("""
        INSERT INTO monthly_menus(id,month_start,filename) VALUES ('saved-month',DATE '2026-10-01','saved.json')
    """))
    conn.execute(sa.text("""
        INSERT INTO base_menu_cycle_items(id,cycle_day,daypart,category,name,diet_type,slot_index)
        VALUES ('saved-cycle',1,'lunch','main','menu','regular',0)
    """))
    conn.execute(sa.text("""
        INSERT INTO menu_rules(id,rule_type,match_type,menu_pattern,facility_id,daypart,category,diet_type,unit_type,
                               qty_per_serving,priority,active,created_at,updated_at)
        VALUES ('saved-rule','global','exact','menu',NULL,'lunch','main','regular','portion',17.5,1,true,
                TIMESTAMP '2026-10-14 09:00:00',TIMESTAMP '2026-10-14 09:00:00')
    """))


def transport():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("stg_adoption_test", ROOT / "scripts/run_stg_schema_adoption.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pg():
    with owned_postgres() as (engine, root):
        with target(engine.url):
            yield engine


def restore(engine, environment="stg"):
    with engine.begin() as conn:
        restore_captured(conn, captured(environment))
        contract = json.loads(schema.CONTRACT.read_text())
        assert adoption._require_baseline(conn, contract)[0] == "live-20261014-" + environment
        conn.exec_driver_sql("INSERT INTO audit_logs(id,actor,action,target,created_at) VALUES ('old','operator','saved','order',NULL)")
        for number in range(5):
            conn.execute(sa.text("INSERT INTO users(id,account,role,status,created_at) VALUES (:id,:id,'operator','active',NULL)"), {"id": f"old-{number}"})


@pytest.mark.parametrize("environment", ["stg", "prod"])
def test_captured_adoption_preserves_nulls_and_matches_fresh(pg, environment):
    # The production capture has zero NULLs; include them here as a close preservation case too.
    restore(pg, environment)
    with pg.begin() as conn:
        insert_saved_order_fixture(conn)
        before_saved_rows = preserved_rows(conn)
    with pg.connect() as conn:
        result = adoption.adopt(conn)
        assert result["source_unchanged"]
        schema.require_formal_head(conn)
        assert conn.exec_driver_sql("SELECT count(*) FROM audit_logs WHERE created_at IS NULL").scalar_one() == 1
        assert conn.exec_driver_sql("SELECT count(*) FROM users WHERE created_at IS NULL").scalar_one() == 5
        assert conn.exec_driver_sql("SELECT id,account FROM users ORDER BY id").mappings().first() == {"id": "old-0", "account": "old-0"}
        actual = schema.table_shapes(schema.read_catalog(conn), "postgresql")
        contract = json.loads(schema.CONTRACT.read_text())
        expected = contract["revisions"]["postgresql"]["0035"]
        for table, ref in expected["tables"].items():
            shape = schema._normalize_reflected_index_aliases(contract["shapes"][ref], "postgresql")
            assert schema._named_columns(actual[table]) == schema._named_columns(shape)
        assert preserved_rows(conn) == before_saved_rows
        assert conn.exec_driver_sql(
            "SELECT revision FROM menu_edit_revisions WHERE scope='monthly-menu:saved-month'"
        ).scalar_one() == 1
        assert {column["name"] for column in sa.inspect(conn).get_columns("base_menu_cycle_items")} >= {"revision"}
        assert {column["name"] for column in sa.inspect(conn).get_columns("menu_rules")} >= {"revision"}


@pytest.mark.parametrize("ddl", [
    "ALTER TABLE users ADD COLUMN unknown_value TEXT",
    "ALTER TABLE users ALTER COLUMN account SET DEFAULT 'unknown'",
    "CREATE TABLE unknown_table (id INT)",
    "ALTER TABLE users ADD CONSTRAINT unknown_check CHECK (account <> 'a b')",
])
def test_unknown_schema_is_not_adopted(pg, ddl):
    restore(pg)
    with pg.begin() as conn:
        conn.exec_driver_sql(ddl)
        before = schema.read_catalog(conn)
    with pg.connect() as conn:
        with pytest.raises(RuntimeError, match="baseline"):
            adoption.adopt(conn)
        assert schema.read_catalog(conn) == before
        assert not sa.inspect(conn).has_table("alembic_version")


def test_final_verification_failure_rolls_back_all_changes(pg, monkeypatch):
    restore(pg)
    with pg.connect() as conn:
        before = schema.read_catalog(conn)
    def reject(*args):
        raise RuntimeError("injected final verification failure")
    monkeypatch.setattr(adoption, "require_revision_catalog", reject)
    with pg.connect() as conn:
        with pytest.raises(RuntimeError, match="injected final"):
            adoption.adopt(conn)
        assert schema.read_catalog(conn) == before
        assert conn.exec_driver_sql("SELECT count(*) FROM users WHERE created_at IS NULL").scalar_one() == 5


@pytest.mark.parametrize("lock_kind", ["migration", "writer"])
def test_existing_lock_blocks_adoption_without_mutation(pg, lock_kind):
    restore(pg)
    with pg.begin() as holder:
        if lock_kind == "migration":
            holder.execute(sa.text("SELECT pg_advisory_xact_lock(:n,:k)"),
                           {"n": adoption.MIGRATION_LOCK[0], "k": adoption.MIGRATION_LOCK[1]})
        else:
            holder.exec_driver_sql("UPDATE users SET status=status WHERE id='old-0'")
        with pg.connect() as conn:
            with pytest.raises((RuntimeError, sa.exc.DBAPIError)):
                adoption.adopt(conn)
            assert not sa.inspect(conn).has_table("alembic_version")


def test_adoption_holds_write_lock_until_commit(pg, monkeypatch):
    restore(pg)
    original = adoption._normalize
    checked = []
    def inspect_lock(conn, *args):
        with pg.connect() as writer:
            writer.exec_driver_sql("SET lock_timeout='100ms'")
            with pytest.raises(sa.exc.DBAPIError) as error:
                writer.exec_driver_sql("UPDATE users SET status='changed' WHERE id='old-0'")
            assert error.value.orig.pgcode == "55P03"
            writer.rollback()
        checked.append(True)
        return original(conn, *args)
    monkeypatch.setattr(adoption, "_normalize", inspect_lock)
    with pg.connect() as conn:
        adoption.adopt(conn)
    assert checked == [True]
    with pg.begin() as conn:
        conn.exec_driver_sql("UPDATE users SET status='active' WHERE id='old-0'")


def test_repeated_transport_checks_tracked_head_and_rejects_drift(pg):
    restore(pg)
    module = transport()
    with pg.connect() as conn:
        module.upgrade_connection(conn)
        conn.rollback()
        module.upgrade_connection(conn)
        conn.rollback()
        conn.exec_driver_sql("ALTER TABLE users ADD COLUMN unexpected TEXT")
        conn.commit()
        with pytest.raises(RuntimeError, match="catalog"):
            module.upgrade_connection(conn)


def test_normal_env_still_refuses_nonempty_untracked(pg):
    restore(pg)
    with pytest.raises(RuntimeError, match="automatic adoption"):
        command.upgrade(Config(str(schema.ALEMBIC_CONFIG)), "head")


def test_sqlite_fresh_head_and_null_preservation(tmp_path):
    engine = sa.create_engine("sqlite:///" + str(tmp_path / "owned.sqlite"))
    try:
        with target(engine.url):
            command.upgrade(Config(str(schema.ALEMBIC_CONFIG)), "head")
        with engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO users(id,account,role,status,created_at) VALUES ('old','old','operator','active',NULL)")
            schema.require_formal_head(conn)
            assert conn.exec_driver_sql("SELECT created_at FROM users WHERE id='old'").scalar_one() is None
    finally:
        engine.dispose()


@pytest.mark.parametrize("revision", ["0035", "0036"])
def test_successor_requires_explicit_supported_policy(revision):
    assert schema.column_identity_policy({"column_identity": "name"}, "postgresql", revision) == "name"
    assert schema.column_identity_policy({"column_identity": "position"}, "sqlite", revision) == "position"
    for data in ({}, {"column_identity": "ignore"}, {"column_identity": None}):
        with pytest.raises(RuntimeError, match="policy"):
            schema.column_identity_policy(data, "postgresql", revision)
    with pytest.raises(RuntimeError, match="policy"):
        schema.column_identity_policy({"column_identity": "name"}, "sqlite", revision)
    with pytest.raises(RuntimeError, match="historical"):
        schema.column_identity_policy({"column_identity": "name"}, "postgresql", "0034")


def test_named_column_policy_preserves_all_other_metadata_and_key_order():
    shape = deepcopy(captured("stg")["facility_areas"])
    permuted = deepcopy(shape)
    permuted["columns"].reverse()
    for item in permuted["native"]:
        if "column_name" in item:
            item["ordinal"] += 20
    assert schema._named_columns(shape) == schema._named_columns(permuted)
    for category in ("pk", "index", "literal", "unknown"):
        altered = deepcopy(shape)
        if category == "pk":
            altered["pk"]["constrained_columns"].reverse()
        elif category == "index":
            next(item for item in altered["native"] if "index_name" in item)["definition"] += " UNKNOWN"
        elif category == "literal":
            altered["columns"][0]["default"] = "'A B'"
        else:
            altered["native"][0]["unknown"] = True
        assert schema._named_columns(shape) != schema._named_columns(altered)
    assert schema.column_identity_policy({}, "postgresql", "0034") == "position"


def test_staging_transport_refuses_non_actions_before_connection(monkeypatch):
    module = transport()
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    def forbidden(*args):
        raise AssertionError("must not connect")
    monkeypatch.setattr(module, "_load_service_db_config", forbidden)
    with pytest.raises(RuntimeError):
        module.run()
