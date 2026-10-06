import os
from pathlib import Path
import sys
from datetime import date
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from src import db
from src.main import app
from src.models.menu import MonthlyMenu, MonthlyMenuItem, MonthlyMenuEntry, MenuMaster, MenuFacilityOverride, MenuRule
from src.models.facility import Facility, FacilityConfig
from src.services import menu_service, order_serialization_service
from src.models.order import Order, OrderLine
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import order_service, order_workflow_v2_service, output_builder, total_service


ROOT = Path(__file__).resolve().parents[3]
OWNED = Path("/private/tmp/sawa-c2-hospital-menu-readonly-20261006")


def seed_order(name="Dish"):
    with db.session_scope() as session:
        order = Order(id="operator-order", document_uri="owned-fixture.pdf", message_id="owned-fixture", week_code="2099-06", status="確定")
        session.add(order)
        session.flush()
        session.add(OrderLine(id="operator-line", order_id=order.id, line_id="source-line", date=date(2099, 6, 7),
                              daypart="朝食", menu_name=name, diet_type="regular", area_id="area-explicit",
                              quantity_original=11, quantity_corrected=13, change_note="operator correction"))


def candidate(name="Dish"):
    return {"lines": [{"line_id": "candidate-line", "date": "2099-06-07", "daypart": "朝食", "menu_name": name,
                       "diet_type": "regular", "area_id": "new-area", "quantity_original": 17, "quantity_corrected": 19}]}


@pytest.mark.parametrize("state", ["missing", "invalid", "conflicting", "resolved"])
def test_operator_order_http_keeps_original_lines_and_strict_consumers_stop(readonly_database, state):
    seed(entries_only=state == "missing")
    seed_order()
    with db.session_scope() as session:
        if state == "invalid":
            session.get(MonthlyMenuItem, "item").unit_type = "invalid"
        if state == "conflicting":
            session.add(MonthlyMenuItem(id="conflict", monthly_menu_id="2099-06", name="Dish", daypart="朝食", category="副菜",
                                        diet_type="regular", qty_per_serving=41, unit_type="g"))
    with TestClient(app) as client:
        before = snapshot(readonly_database)
        response = client.get("/orders/operator-order")
    assert response.status_code == 200
    payload = response.json()
    line = payload["lines"][0]
    for key, value in {"line_id": "source-line", "date": "2099-06-07", "daypart": "朝食", "menu_name": "Dish",
                       "diet_type": "regular", "area_id": "area-explicit", "quantity_original": 11,
                       "quantity_corrected": 13, "change_note": "operator correction"}.items():
        assert line[key] == value
    resolution = payload["menu_amount_resolution"]
    if state == "resolved":
        assert resolution["status"] == "resolved" and resolution["affected_lines"] == []
        assert line["menu_qty_per_serving"] == 37 and line["actual_amount"] == 481
        assert line["menu_unit_type"] == line["actual_unit_type"] == "g"
    else:
        assert resolution["blocker"] == "menu_amount_unresolved"
        assert resolution["affected_lines"][0]["line_id"] == "source-line"
        assert resolution["affected_lines"][0]["unresolved_fields"]
        assert all(line[field] is None for field in ("menu_qty_per_serving", "menu_unit_type", "actual_amount", "actual_unit_type"))
        with pytest.raises(menu_service.MenuReadUnresolved):
            order_service.get_order_by_id("operator-order")
        with pytest.raises(menu_service.MenuReadUnresolved):
            total_service.build_totals(date(2099, 6, 7), date(2099, 6, 7))
    assert snapshot(readonly_database) == before


@pytest.mark.parametrize("persisted,candidate_name", [("Dish", "Unknown"), ("Old unresolved", "Dish")])
def test_step4_uses_adopted_candidate_not_old_order_lines(readonly_database, monkeypatch, persisted, candidate_name):
    seed()
    seed_order(persisted)
    adopted = candidate(candidate_name)
    with db.session_scope() as session:
        session.add(OrderSheetDraft(id="draft", order_id="operator-order", draft_sheet_json={"fixture": "explicit"}))
        session.flush()
        session.add(OrderWorkflowState(order_id="operator-order", draft_id="draft", state="sheet_saved"))
    monkeypatch.setattr(order_workflow_v2_service, "_build_materialization_candidate_for_saved_sheet", lambda **kwargs: adopted)
    before = snapshot(readonly_database)
    with db.session_scope() as session:
        order = session.get(Order, "operator-order")
        workflow = session.get(OrderWorkflowState, order.id)
        projection = order_workflow_v2_service._workflow_menu_amount_resolution(session, order, workflow)
        assert projection["status"] == ("unresolved" if candidate_name == "Unknown" else "resolved")
        if candidate_name == "Unknown":
            assert projection["affected_lines"][0]["menu_name"] == "Unknown"
    calls = []
    def enrich(order):
        calls.append(order["lines"])
        raise RuntimeError("injected enrichment failure")
    monkeypatch.setattr(output_builder, "build_order_lines_for_outputs", enrich)
    _, error = order_workflow_v2_service.run_bagging("operator-order")
    assert error == ("menu_amount_unresolved" if candidate_name == "Unknown" else "bagging_output_enrichment_failed")
    assert calls == ([] if candidate_name == "Unknown" else [adopted["lines"]])
    assert snapshot(readonly_database) == before
    bagging = {"bagging_result_id": "bagging", "source_saved_sheet_id": "draft", "materialization_candidate": adopted}
    bundle = {"source_bagging_result_id": "bagging", "source_saved_sheet_id": "draft"}
    monkeypatch.setattr(order_workflow_v2_service, "_workflow_meta_with_step4_artifacts", lambda *args: {"bagging_result": bagging, "output_bundle": bundle})
    if candidate_name == "Unknown":
        _, error = order_workflow_v2_service.final_confirm("operator-order")
        assert error == "menu_amount_unresolved"
        assert snapshot(readonly_database) == before


def test_resolved_enrichment_keeps_exact_output_and_quantities(readonly_database, monkeypatch):
    seed()
    seed_order("Old unresolved")
    adopted = candidate()
    from copy import deepcopy
    original = deepcopy(adopted)
    enriched = [dict(adopted["lines"][0], menu_qty_per_serving=37, menu_unit_type="g")]
    expected = [{"quantity": 19, "actual_amount": 703, "actual_unit_type": "g"}]
    def enrich(payload):
        assert payload["lines"] == original["lines"]
        return enriched
    def bags(payload, *, order_lines):
        assert payload["lines"] == original["lines"]
        assert order_lines == enriched
        return expected
    monkeypatch.setattr(output_builder, "build_order_lines_for_outputs", enrich)
    monkeypatch.setattr(output_builder, "build_bag_payload_for_outputs", bags)
    with db.session_scope() as session:
        order = session.get(Order, "operator-order")
        assert order_serialization_service.candidate_menu_amount_resolution(order, adopted)["status"] == "resolved"
        assert order_workflow_v2_service._build_bag_rows_for_candidate(order=order, lines=adopted["lines"]) == expected
    assert adopted == original


@pytest.mark.parametrize("kind", ["missing", "mismatched", "empty"])
def test_workflow_broken_draft_is_readonly_explicit_stop(readonly_database, monkeypatch, kind):
    seed()
    seed_order()
    with db.session_scope() as session:
        order = session.get(Order, "operator-order")
        workflow = OrderWorkflowState(order_id=order.id, draft_id="absent", state="sheet_saved")
        if kind != "missing":
            session.add(Order(id="different-order", document_uri="owned-other.pdf", message_id="owned-other", status="確定"))
            session.flush()
            draft = OrderSheetDraft(id="draft", order_id="different-order", draft_sheet_json={})
            if kind == "empty":
                draft.order_id = order.id
            session.add(draft)
            session.flush()
            workflow.draft_id = draft.id
        monkeypatch.setattr(order_workflow_v2_service, "_build_materialization_candidate_for_saved_sheet", lambda **kwargs: {"lines": [], "error": "draft_lines_empty"})
        result = order_workflow_v2_service._workflow_menu_amount_resolution(session, order, workflow)
        assert result["status"] == "unresolved"
        assert result["blocker"] == ("draft_lines_empty" if kind == "empty" else "saved_sheet_missing")
        session.rollback()


def test_step4_empty_candidate_keeps_existing_stop(readonly_database, monkeypatch):
    seed()
    seed_order()
    with db.session_scope() as session:
        session.add(OrderSheetDraft(id="draft", order_id="operator-order", draft_sheet_json={}))
        session.flush()
        session.add(OrderWorkflowState(order_id="operator-order", draft_id="draft", state="sheet_saved"))
    monkeypatch.setattr(order_workflow_v2_service, "_build_materialization_candidate_for_saved_sheet", lambda **kwargs: {"lines": [], "error": "draft_lines_empty"})
    before = snapshot(readonly_database)
    assert order_workflow_v2_service.run_bagging("operator-order")[1] == "draft_lines_empty"
    assert snapshot(readonly_database) == before


@pytest.fixture(params=["sqlite", "postgresql"])
def readonly_database(request, tmp_path, monkeypatch):
    admin = isolated = None
    created = False
    name = "c2_menu_readonly_" + uuid4().hex
    try:
        if request.param == "sqlite":
            assert tmp_path.resolve().is_relative_to(OWNED)
            url = sa.URL.create("sqlite", database=str(tmp_path / "menu.sqlite3"))
        else:
            uri = os.environ.get("C2_MENU_READONLY_PG_URI")
            if uri is None:
                pytest.skip("owned PG55471 not provided; PostgreSQL proof not executed")
            assert uri and uri == uri.strip()
            url = make_url(uri)
            assert url.drivername == "postgresql+psycopg2"
            assert url.host is None and url.port is None
            assert url.database == "postgres" and url.username == "mmorinag" and url.password is None
            assert dict(url.query) == {"host": str(OWNED / "pg/socket"), "port": "55471"}
            assert (OWNED / "pg").resolve() == OWNED / "pg"
            assert (OWNED / "pg/OWNER").read_text().strip() == str(ROOT)
            pid = (OWNED / "pg/pgdata/postmaster.pid").read_text().splitlines()
            assert Path(pid[1]).resolve() == OWNED / "pg/pgdata"
            assert pid[3] == "55471" and pid[4] == str(OWNED / "pg/socket") and pid[5] == ""
            admin = sa.create_engine(url, isolation_level="AUTOCOMMIT")
            with admin.connect() as connection:
                assert Path(connection.exec_driver_sql("SHOW data_directory").scalar_one()).resolve() == OWNED / "pg/pgdata"
                assert connection.exec_driver_sql("SHOW port").scalar_one() == "55471"
                assert connection.exec_driver_sql("SHOW unix_socket_directories").scalar_one() == str(OWNED / "pg/socket")
                assert connection.exec_driver_sql("SHOW listen_addresses").scalar_one() == ""
                assert int(connection.exec_driver_sql("SHOW server_version_num").scalar_one()) // 10000 == 16
                assert connection.exec_driver_sql("SELECT current_user").scalar_one() == "mmorinag"
                assert not connection.execute(sa.text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": name}).scalar()
                connection.exec_driver_sql('CREATE DATABASE "' + name + '"')
                created = True
            url = url.set(database=name)
        isolated = sa.create_engine(url)
        for variable in ("DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER"):
            monkeypatch.delenv(variable, raising=False)
        target = url.render_as_string(hide_password=False)
        monkeypatch.setenv("DB_URI", target)
        monkeypatch.setenv("ALEMBIC_DB_URI", target)
        config = Config(str(ROOT / "backend/alembic.ini"))
        with isolated.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        original = db.engine
        monkeypatch.setattr(db, "SessionLocal", sessionmaker(isolated, autoflush=False, future=True))
        for module in tuple(sys.modules.values()):
            if module is not None and getattr(module, "__name__", "").startswith("src."):
                if getattr(module, "engine", None) is original:
                    monkeypatch.setattr(module, "engine", isolated)
        monkeypatch.setattr(menu_service, "_MENU_SCHEMA_INITIALIZED", False)
        yield isolated
    finally:
        if isolated is not None:
            isolated.dispose()
        if admin is not None:
            try:
                if created:
                    with admin.connect() as connection:
                        connection.exec_driver_sql('DROP DATABASE "' + name + '"')
            finally:
                admin.dispose()


def seed(*, entries_only=False):
    with db.session_scope() as session:
        session.add(MonthlyMenu(id="2099-06", month_start=date(2099, 6, 1)))
        session.add(MenuMaster(id="master", name="Dish", normalized_name="dish", unit_type="g", qty_per_serving=999, temp_type=None))
        session.flush()
        if not entries_only:
            session.add(MonthlyMenuItem(id="item", monthly_menu_id="2099-06", menu_master_id="master", name="Dish", qty_per_serving=37, unit_type="g", daypart="朝食", category="主菜", diet_type="regular"))
        session.add(MonthlyMenuEntry(id="entry", monthly_menu_id="2099-06", menu_date=date(2099, 6, 7), name="Dish", daypart="朝食", category="主菜", diet_type="regular", slot_index=0))


def snapshot(engine):
    from src.maintenance.formal_migration_schema import read_catalog
    with engine.connect() as connection:
        result = {"catalog": read_catalog(connection)}
        for name in sa.inspect(connection).get_table_names():
            table = sa.Table(name, sa.MetaData(), autoload_with=connection)
            result[name] = sorted(repr(tuple(row)) for row in connection.execute(sa.select(table)))
        return result


def test_four_reads_no_dml_ddl_with_another_writer(readonly_database):
    seed()
    before = snapshot(readonly_database)
    recent = menu_service.list_recent_menus()
    assert len(recent) == 1 and recent[0]["id"] == "2099-06"
    statements = []
    with readonly_database.begin() as writer:
        writer.execute(sa.text("UPDATE menu_masters SET qty_per_serving=998 WHERE id='master'"))
        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        sa.event.listen(readonly_database, "before_cursor_execute", capture)
        try:
            payloads = [menu_service.get_menu("2099-06"), menu_service.get_latest_menu()]
            lists = [menu_service.get_menu_items("2099-06"), menu_service.get_menu_items_for_facility("2099-06", None)]
            assert menu_service.list_recent_menus() == recent
            assert all(payload["items"][0]["qty_per_serving"] == 37 for payload in payloads)
            assert all(items[0]["unit_type"] == "g" and items[0]["qty_per_serving"] == 37 for items in lists)
            assert payloads[0]["entries"][0]["menu_date"] == "2099-06-07"
        finally:
            sa.event.remove(readonly_database, "before_cursor_execute", capture)
        writer.rollback()
    assert statements
    assert all(statement.lstrip().split()[0].upper() in {"SELECT", "PRAGMA", "BEGIN", "SET"} for statement in statements)
    assert snapshot(readonly_database) == before


def test_entries_only_display_stop_and_explicit_save(readonly_database, operator_auth_headers):
    seed(entries_only=True)
    with db.session_scope() as session:
        session.add(MenuRule(id="rule", rule_type="menu", match_type="exact", menu_pattern="Dish", unit_type="g", qty_per_serving=99, active=True, priority=1))
    before = snapshot(readonly_database)
    client = TestClient(app)
    response = client.get("/monthly-menus/2099-06", headers=operator_auth_headers)
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["id"] == "MMI_REPAIR_entry"
    assert item["menu_master_id"] is None and item["temp_type"] is None
    assert item["qty_per_serving"] is None and item["unit_type"] is None
    assert item["confirmation_required"] is True
    assert set(item["unresolved_fields"]) == {"qty_per_serving", "unit_type"}
    for read in (lambda: menu_service.get_menu("2099-06"), menu_service.get_latest_menu,
                 lambda: menu_service.get_menu_items("2099-06"), lambda: menu_service.get_menu_items_for_facility("2099-06", None)):
        with pytest.raises(menu_service.MenuReadUnresolved):
            read()
    assert snapshot(readonly_database) == before
    assert menu_service.update_item_status("2099-06", item["id"], {"qty_per_serving": 37, "unit_type": "g", "daypart": "朝食", "category": "主菜", "diet_type": "regular"}) == "updated"
    saved = menu_service.get_menu("2099-06")["items"][0]
    assert saved["qty_per_serving"] == 37 and saved["unit_type"] == "g"
    assert not saved.get("confirmation_required")


def test_explicit_master_link_only_and_no_temperature_inference(readonly_database):
    seed()
    with db.session_scope() as session:
        item = session.get(MonthlyMenuItem, "item")
        item.qty_per_serving = None
        item.unit_type = None
        master = session.get(MenuMaster, "master")
        master.name = "ポークチャップ"
    before = snapshot(readonly_database)
    payload = menu_service.get_menu_items("2099-06")[0]
    assert payload["qty_per_serving"] == 999 and payload["unit_type"] == "g"
    assert payload["temp_type"] is None
    with db.session_scope() as session:
        session.get(MonthlyMenuItem, "item").menu_master_id = None
    with pytest.raises(menu_service.MenuReadUnresolved):
        menu_service.get_menu_items("2099-06")
    after = snapshot(readonly_database)
    assert after["menu_masters"] == before["menu_masters"]


def test_facility_tag_base_and_diet_selection_preserves_saved_values(readonly_database):
    seed()
    with db.session_scope() as session:
        session.add(Facility(id="facility", name="Facility"))
        session.flush()
        session.add(FacilityConfig(facility_id="facility", config_json={"menu_override_tags": ["special"]}))
        session.add_all([
            MonthlyMenuItem(id="tag", monthly_menu_id="2099-06", name="Dish", qty_per_serving=41, unit_type="g", daypart="朝食", category="主菜", diet_type="regular", facility_override="TAG:special"),
            MonthlyMenuItem(id="facility", monthly_menu_id="2099-06", name="Dish", qty_per_serving=43, unit_type="g", daypart="朝食", category="主菜", diet_type="regular", facility_override="facility"),
            MonthlyMenuItem(id="diet", monthly_menu_id="2099-06", name="Dish", qty_per_serving=17, unit_type="g", daypart="朝食", category="主菜", diet_type="soft"),
        ])
    rows = menu_service.get_menu_items_for_facility("2099-06", "facility")
    assert {(row["id"], row["qty_per_serving"]) for row in rows} == {("facility", 43), ("diet", 17)}
    with db.session_scope() as session:
        session.delete(session.get(MonthlyMenuItem, "facility"))
    rows = menu_service.get_menu_items_for_facility("2099-06", "facility")
    assert {(row["id"], row["qty_per_serving"]) for row in rows} == {("tag", 41), ("diet", 17)}
    rows = menu_service.get_menu_items_for_facility("2099-06", None)
    assert {(row["id"], row["qty_per_serving"]) for row in rows} == {("item", 37), ("diet", 17)}


def test_same_priority_scope_identity_is_projected_as_ambiguity_without_selecting_by_id(readonly_database, monkeypatch):
    seed()
    with readonly_database.begin() as connection:
        connection.exec_driver_sql("DROP INDEX uq_monthly_menu_items_scope_identity")
    monkeypatch.setattr(menu_service, "ensure_menu_schema", lambda: None)
    with db.session_scope() as session:
        session.add_all([
            MonthlyMenuItem(id="facility-a", monthly_menu_id="2099-06", name="Dish", qty_per_serving=37,
                            unit_type="g", daypart="朝食", category="主菜", diet_type="regular", facility_override="facility"),
            MonthlyMenuItem(id="facility-z", monthly_menu_id="2099-06", name="Dish", qty_per_serving=37,
                            unit_type="g", daypart="朝食", category="主菜", diet_type="regular", facility_override="facility"),
        ])
    with db.session_scope() as session:
        session.add(Facility(id="facility", name="Facility"))
    projection = menu_service.get_menu_for_facility("2099-06", "facility", allow_unresolved=True)
    item = projection["items"][0]
    assert item["qty_per_serving"] is None and item["unit_type"] is None
    assert item["id"] is None and item["menu_master_id"] is None
    assert item["facility_override"] is None and item["master_resolution_mode"] is None
    assert item["unresolved_fields"] == ["menu_identity"]
    assert item["ambiguity"] == {"code": "menu_identity_ambiguous", "candidate_ids": ["facility-a", "facility-z"]}
    with pytest.raises(menu_service.MenuReadUnresolved):
        menu_service.get_menu_for_facility("2099-06", "facility")
    # The facility scope still wins over the lower-priority base scope when unique.
    with db.session_scope() as session:
        session.delete(session.get(MonthlyMenuItem, "facility-a"))
        session.delete(session.get(MonthlyMenuItem, "facility-z"))
        session.add(MonthlyMenuItem(id="facility-only", monthly_menu_id="2099-06", name="Dish", qty_per_serving=43,
                                    unit_type="g", daypart="朝食", category="主菜", diet_type="regular", facility_override="facility"))
    assert menu_service.get_menu_for_facility("2099-06", "facility")["items"][0]["qty_per_serving"] == 43


def test_other_facility_item_is_excluded_from_facility_read(readonly_database):
    seed()
    with db.session_scope() as session:
        session.add_all([
            Facility(id="facility", name="Facility"),
            MonthlyMenuItem(id="other-only", monthly_menu_id="2099-06", name="Other Dish", qty_per_serving=71,
                            unit_type="g", daypart="朝食", category="主菜", diet_type="regular", facility_override="other-facility"),
        ])
    rows = menu_service.get_menu_items_for_facility("2099-06", "facility")
    assert {row["id"] for row in rows} == {"item"}


def test_base_candidate_is_not_made_ambiguous_by_other_facility_scope(readonly_database):
    seed()
    with db.session_scope() as session:
        session.add_all([
            Facility(id="facility", name="Facility"),
            MonthlyMenuItem(id="other-scope", monthly_menu_id="2099-06", name="Dish", qty_per_serving=71,
                            unit_type="g", daypart="朝食", category="主菜", diet_type="regular", facility_override="other-facility"),
        ])
    rows = menu_service.get_menu_items_for_facility("2099-06", "facility")
    assert [(row["id"], row["qty_per_serving"], row.get("ambiguity")) for row in rows] == [("item", 37, None)]


def test_facility_inherits_group_scope_when_no_facility_scope_exists(readonly_database):
    seed()
    with db.session_scope() as session:
        session.add(Facility(id="facility", name="Facility"))
        session.flush()
        session.add(FacilityConfig(facility_id="facility", config_json={"menu_override_tags": ["group"]}))
        session.add(MonthlyMenuItem(id="group", monthly_menu_id="2099-06", name="Dish", qty_per_serving=43,
                                    unit_type="g", daypart="朝食", category="主菜", diet_type="regular", facility_override="TAG:group"))
    rows = menu_service.get_menu_items_for_facility("2099-06", "facility")
    assert [(row["id"], row["qty_per_serving"]) for row in rows] == [("group", 43)]


def test_base_identity_ambiguity_stops_all_menu_read_entry_points(readonly_database, monkeypatch):
    seed()
    with readonly_database.begin() as connection:
        connection.exec_driver_sql("DROP INDEX uq_monthly_menu_items_scope_identity")
    monkeypatch.setattr(menu_service, "ensure_menu_schema", lambda: None)
    with db.session_scope() as session:
        session.add(MonthlyMenuItem(id="same-identity", monthly_menu_id="2099-06", name="Dish", qty_per_serving=37,
                                    unit_type="g", daypart="朝食", category="主菜", diet_type="regular"))
    reads = (
        lambda: menu_service.get_menu("2099-06"),
        menu_service.get_latest_menu,
        lambda: menu_service.get_menu_items("2099-06"),
        lambda: menu_service.get_menu_items_for_facility("2099-06", None),
    )
    for read in reads:
        with pytest.raises(menu_service.MenuReadUnresolved):
            read()
    projection = menu_service.get_menu("2099-06", allow_unresolved=True)
    assert projection["items"][0]["ambiguity"]["code"] == "menu_identity_ambiguous"


def test_requested_month_is_not_substituted_and_explicit_crossmonth_dates_survive(readonly_database):
    seed()
    with db.session_scope() as session:
        session.get(MonthlyMenuEntry, "entry").menu_date = date(2099, 7, 1)
    assert menu_service.get_menu("2099-07") is None
    assert menu_service.get_menu_for_facility("2099-07", None) is None
    assert menu_service.get_menu("2099-06")["entries"][0]["menu_date"] == "2099-07-01"


def test_serializer_propagates_stop_and_rejects_missing_or_ambiguous_values(readonly_database, monkeypatch):
    from types import SimpleNamespace
    line = SimpleNamespace(menu_name="Dish", daypart="朝食", diet_type="regular", date=date(2099, 6, 7))
    order = SimpleNamespace(lines=[line], week_code="2099-W23", facility_code=None)
    item = {"id": "item", "month_id": "2099-06", "menu_master_id": "master", "name": "Dish", "daypart": "朝食",
            "diet_type": "regular", "menu_dates": ["2099-06-07"], "qty_per_serving": 37, "unit_type": "g"}
    def unresolved(*args, **kwargs):
        raise menu_service.MenuReadUnresolved([{"name": "Dish"}])
    monkeypatch.setattr(order_serialization_service, "_collect_menu_items_for_week", unresolved)
    with pytest.raises(menu_service.MenuReadUnresolved):
        order_serialization_service._build_menu_amount_meta(order)
    monkeypatch.setattr(order_serialization_service, "_collect_menu_items_for_week", lambda *args: [])
    monkeypatch.setattr(menu_service, "resolve_menu_defaults", lambda *args: pytest.fail("name fallback invoked"))
    with pytest.raises(menu_service.MenuReadUnresolved):
        order_serialization_service._build_menu_amount_meta(order)
    monkeypatch.setattr(order_serialization_service, "_collect_menu_items_for_week", lambda *args: [item])
    assert order_serialization_service._build_menu_amount_meta(order)[order_serialization_service._line_menu_identity(line)] == {"qty_per_serving": 37.0, "unit_type": "g"}
    monkeypatch.setattr(order_serialization_service, "_collect_menu_items_for_week", lambda *args: [item, dict(item, id="same-amount-distinct-identity")])
    with pytest.raises(menu_service.MenuReadUnresolved):
        order_serialization_service._build_menu_amount_meta(order)
    monkeypatch.setattr(order_serialization_service, "_collect_menu_items_for_week", lambda *args: [dict(item, menu_dates=[])])
    with pytest.raises(menu_service.MenuReadUnresolved):
        order_serialization_service._build_menu_amount_meta(order)


@pytest.mark.parametrize("quantity", ["invalid", "37", float("nan"), float("inf"), float("-inf"), True, None])
def test_serializer_invalid_quantity_stops(readonly_database, monkeypatch, quantity):
    from types import SimpleNamespace
    order = SimpleNamespace(lines=[SimpleNamespace(menu_name="Dish")], week_code="2099-W23", facility_code=None)
    monkeypatch.setattr(order_serialization_service, "_collect_menu_items_for_week", lambda *args: [{"name": "Dish", "qty_per_serving": quantity, "unit_type": "g"}])
    with pytest.raises(menu_service.MenuReadUnresolved):
        order_serialization_service._build_menu_amount_meta(order)


@pytest.mark.parametrize("quantity,unit", [(float("nan"), "g"), (float("inf"), "g"), (True, "g"), ("37", "g"), ("invalid", "g"), (37, "unknown"), (0, "g")])
def test_operator_and_downstream_share_validation_without_overwriting_values(readonly_database, quantity, unit):
    from types import SimpleNamespace
    item = SimpleNamespace(id="invalid", monthly_menu_id="2099-06", menu_master_id=None, name="Dish", unit_type=unit, qty_per_serving=quantity, temp_type=None, daypart="朝食", category="主菜", diet_type="regular", facility_override=None, master_resolution_mode="month_only", bag_max_qty=None, bag_max_unit=None)
    with menu_service._menu_read_session() as session:
        payload = menu_service._readonly_monthly_items(session, "2099-06", [item], [])[0]
    assert payload["qty_per_serving"] is quantity
    assert payload["unit_type"] == unit
    if quantity == 0 and not isinstance(quantity, bool) and unit == "g":
        assert not payload.get("confirmation_required")
        menu_service.require_resolved_menu_items([payload])
    else:
        assert payload["confirmation_required"] is True
        assert payload["unresolved_fields"] == menu_service._unresolved_menu_fields(payload)
        with pytest.raises(menu_service.MenuReadUnresolved):
            menu_service.require_resolved_menu_items([payload])


def test_facility_read_uses_one_snapshot_connection(readonly_database):
    seed()
    menu_service.ensure_menu_schema()
    connections = set()
    def capture(connection, cursor, statement, parameters, context, executemany):
        connections.add(id(connection))
    sa.event.listen(readonly_database, "before_cursor_execute", capture)
    try:
        assert menu_service.get_menu_for_facility("2099-06", "facility")["items"][0]["qty_per_serving"] == 37
    finally:
        sa.event.remove(readonly_database, "before_cursor_execute", capture)
    assert len(connections) == 1


def test_missing_canonical_schema_stops_without_repair(readonly_database):
    seed()
    with readonly_database.begin() as connection:
        connection.exec_driver_sql("DROP INDEX uq_monthly_menu_items_scope_identity")
    before = snapshot(readonly_database)
    with pytest.raises(menu_service.MenuSchemaNotMigrated, match="migration 0023"):
        menu_service.get_menu("2099-06", allow_unresolved=True)
    assert snapshot(readonly_database) == before


def test_serializer_identity_and_irrelevant_unresolved_items(readonly_database, monkeypatch):
    from types import SimpleNamespace
    line = SimpleNamespace(menu_name="Dish", daypart="朝食", diet_type="regular", date=date(2099, 6, 7))
    order = SimpleNamespace(lines=[line], week_code="2099-W23", facility_code=None)
    items = [
        {"id": "item", "month_id": "2099-06", "name": "Dish", "daypart": "朝食", "diet_type": "regular", "menu_dates": ["2099-06-07"], "qty_per_serving": 0, "unit_type": "g"},
        {"name": "Dish", "daypart": "夕食", "diet_type": "regular", "qty_per_serving": 99, "unit_type": "g"},
        {"name": "Dish", "daypart": "朝食", "diet_type": "soft", "qty_per_serving": None, "unit_type": None},
        {"name": "Dish", "daypart": "朝食", "diet_type": "regular", "menu_dates": ["2099-06-14"], "qty_per_serving": None, "unit_type": None},
        {"name": "Other", "qty_per_serving": None, "unit_type": None},
    ]
    monkeypatch.setattr(order_serialization_service, "_collect_menu_items_for_week", lambda *args: items)
    meta = order_serialization_service._build_menu_amount_meta(order)
    assert meta[order_serialization_service._line_menu_identity(line)] == {"qty_per_serving": 0.0, "unit_type": "g"}
    items.append(dict(items[0]))
    assert order_serialization_service._build_menu_amount_meta(order) == meta
    items.append(dict(items[0], category="another explicitly saved category"))
    with pytest.raises(menu_service.MenuReadUnresolved):
        order_serialization_service._build_menu_amount_meta(order)
    items.pop()
    items.append(dict(items[0], qty_per_serving=1))
    with pytest.raises(menu_service.MenuReadUnresolved):
        order_serialization_service._build_menu_amount_meta(order)


@pytest.mark.parametrize("difference", [{"id": "different"}, {"menu_master_id": "other-master"}, {"facility_override": "other-facility"}, {"month_id": "2099-07"}])
def test_same_amount_distinct_canonical_sources_stop(readonly_database, monkeypatch, difference):
    from types import SimpleNamespace
    order = SimpleNamespace(lines=[SimpleNamespace(menu_name="Dish")], week_code="2099-06", facility_code=None)
    item = {"id": "item", "month_id": "2099-06", "menu_master_id": "master", "name": "Dish", "qty_per_serving": 37, "unit_type": "g"}
    monkeypatch.setattr(order_serialization_service, "_collect_menu_items_for_week", lambda *args: [item, dict(item, **difference)])
    meta, resolution = order_serialization_service._resolve_menu_amounts(order)
    assert meta == {}
    assert resolution["blocker"] == "menu_amount_unresolved"
    assert resolution["affected_lines"][0]["unresolved_fields"] == ["menu_identity"]
    with pytest.raises(menu_service.MenuReadUnresolved):
        order_serialization_service._build_menu_amount_meta(order)


def test_repeated_canonical_reference_and_explicit_unit_alias_are_resolved(readonly_database, monkeypatch):
    from types import SimpleNamespace
    line = SimpleNamespace(menu_name="Dish", daypart="朝食", diet_type="regular", date=date(2099, 6, 7))
    order = SimpleNamespace(lines=[line], week_code="2099-06", facility_code=None)
    item = {"id": "item", "month_id": "2099-06", "menu_master_id": "master", "name": "Dish", "daypart": "朝食", "diet_type": "regular", "menu_dates": ["2099-06-07"], "qty_per_serving": 0, "unit_type": "枚"}
    monkeypatch.setattr(order_serialization_service, "_collect_menu_items_for_week", lambda *args: [item, dict(item)])
    assert order_serialization_service._build_menu_amount_meta(order)[order_serialization_service._line_menu_identity(line)] == {"qty_per_serving": 0.0, "unit_type": "cut"}
    assert item["unit_type"] == "枚" and item["qty_per_serving"] == 0


@pytest.mark.parametrize("candidate", [
    {},
    {"lines": []},
    {"lines": "not-a-list"},
    {"lines": ["not-a-dict"]},
    {"lines": [{"date": None, "menu_name": "Dish", "quantity_original": 0}]},
    {"lines": [{"date": "not-a-date", "menu_name": "Dish", "quantity_original": 0}]},
    {"lines": [{"date": "2099-06-07", "menu_name": "", "quantity_original": 0}]},
])
def test_candidate_projection_rejects_broken_input_and_preserves_zero_quantity(readonly_database, candidate):
    seed()
    seed_order()
    with db.session_scope() as session:
        order = session.get(Order, "operator-order")
        result = order_serialization_service.candidate_menu_amount_resolution(order, candidate)
    assert result["status"] == "unresolved"
    assert result["blocker"] == "menu_amount_unresolved"


def test_candidate_zero_quantity_is_resolved_not_treated_as_missing(readonly_database):
    seed()
    seed_order()
    zero = {"lines": [{"line_id": "zero", "date": "2099-06-07", "daypart": "朝食", "menu_name": "Dish",
                       "diet_type": "regular", "quantity_original": 0, "quantity_corrected": None}]}
    with db.session_scope() as session:
        order = session.get(Order, "operator-order")
        assert order_serialization_service.candidate_menu_amount_resolution(order, zero)["status"] == "resolved"


@pytest.mark.parametrize("missing_field", ["date", "daypart", "diet_type"])
def test_named_candidate_line_requires_every_explicit_identity_field(readonly_database, missing_field):
    seed()
    seed_order()
    line = candidate()["lines"][0]
    line[missing_field] = None
    with db.session_scope() as session:
        result = order_serialization_service.candidate_menu_amount_resolution(
            session.get(Order, "operator-order"), {"lines": [line]}
        )
    assert result["affected_lines"][0]["unresolved_fields"] == [missing_field]


@pytest.mark.parametrize("missing_field", ["date", "daypart", "diet_type"])
def test_named_persisted_line_requires_every_explicit_identity_field(readonly_database, monkeypatch, missing_field):
    from types import SimpleNamespace
    line = SimpleNamespace(menu_name="Dish", date=date(2099, 6, 7), daypart="朝食", diet_type="regular")
    setattr(line, missing_field, None)
    item = {"id": "item", "month_id": "2099-06", "menu_master_id": "master", "name": "Dish",
            "daypart": "朝食", "diet_type": "regular", "menu_dates": ["2099-06-07"],
            "qty_per_serving": 37, "unit_type": "g"}
    monkeypatch.setattr(order_serialization_service, "_collect_menu_items_for_week", lambda *args: [item])
    _meta, result = order_serialization_service._resolve_menu_amounts(
        SimpleNamespace(lines=[line], week_code="2099-06", facility_code=None)
    )
    assert result["affected_lines"][0]["unresolved_fields"] == [missing_field]


def test_explicit_diet_alias_resolves_without_defaulting_missing_diet(readonly_database, monkeypatch):
    from types import SimpleNamespace
    line = SimpleNamespace(menu_name="Dish", date=date(2099, 6, 7), daypart="朝食", diet_type="常食")
    item = {"id": "item", "month_id": "2099-06", "menu_master_id": "master", "name": "Dish",
            "daypart": "朝食", "diet_type": None, "menu_dates": ["2099-06-07"],
            "qty_per_serving": 37, "unit_type": "g"}
    monkeypatch.setattr(order_serialization_service, "_collect_menu_items_for_week", lambda *args: [item])
    assert order_serialization_service._build_menu_amount_meta(
        SimpleNamespace(lines=[line], week_code="2099-06", facility_code=None)
    )[order_serialization_service._line_menu_identity(line)] == {"qty_per_serving": 37.0, "unit_type": "g"}


def test_named_zero_quantity_remains_valid_with_explicit_identity(readonly_database, monkeypatch):
    from types import SimpleNamespace
    line = SimpleNamespace(menu_name="Dish", date=date(2099, 6, 7), daypart="朝食", diet_type="regular")
    item = {"id": "item", "month_id": "2099-06", "menu_master_id": "master", "name": "Dish",
            "daypart": "朝食", "diet_type": "regular", "menu_dates": ["2099-06-07"],
            "qty_per_serving": 37, "unit_type": "g"}
    monkeypatch.setattr(order_serialization_service, "_collect_menu_items_for_week", lambda *args: [item])
    order = SimpleNamespace(lines=[line], week_code="2099-06", facility_code=None)
    assert order_serialization_service.menu_amount_resolution(order)["status"] == "resolved"
    candidate = {"lines": [{"line_id": "zero", "menu_name": "Dish", "date": "2099-06-07",
                             "daypart": "朝食", "diet_type": "regular", "quantity_original": 0}]}
    assert order_serialization_service.candidate_menu_amount_resolution(order, candidate)["status"] == "resolved"


def test_get_item_requires_explicit_master_fk_and_keeps_missing_fk_readable(readonly_database):
    seed()
    with db.session_scope() as session:
        item = session.get(MonthlyMenuItem, "item")
        item.menu_master_id = None
        item.qty_per_serving = None
    payload = menu_service.get_item("item")
    assert payload["id"] == "item"
    assert payload["month_id"] == "2099-06"
    assert payload["qty_per_serving"] is None
    assert payload["menu_master_id"] is None
    assert payload["confirmation_required"] is True
    assert payload["unresolved_fields"] == ["qty_per_serving"]
    with db.session_scope() as session:
        item = session.get(MonthlyMenuItem, "item")
        item.menu_master_id = "master"
        item.qty_per_serving = None
    assert menu_service.get_item("item")["qty_per_serving"] == 37


def test_zero_quantity_builder_empty_bag_rows_are_preserved(readonly_database, monkeypatch):
    seed()
    seed_order()
    monkeypatch.setattr(output_builder, "build_order_lines_for_outputs", lambda payload: payload["lines"])
    monkeypatch.setattr(output_builder, "build_bag_payload_for_outputs", lambda *args, **kwargs: [])
    with db.session_scope() as session:
        order = session.get(Order, "operator-order")
        zero = candidate()["lines"]
        zero[0]["quantity_original"] = 0
        zero[0]["quantity_corrected"] = 0
        assert order_workflow_v2_service._build_bag_rows_for_candidate(order=order, lines=zero) == []


def test_nonlist_bag_builder_result_is_an_explicit_stop(readonly_database, monkeypatch):
    seed()
    seed_order()
    monkeypatch.setattr(output_builder, "build_order_lines_for_outputs", lambda payload: payload["lines"])
    monkeypatch.setattr(output_builder, "build_bag_payload_for_outputs", lambda *args, **kwargs: {"invalid": True})
    with db.session_scope() as session:
        order = session.get(Order, "operator-order")
        with pytest.raises(order_workflow_v2_service.BaggingOutputUnresolved, match="bagging_output_invalid"):
            order_workflow_v2_service._build_bag_rows_for_candidate(order=order, lines=candidate()["lines"])


def test_operator_http_read_does_not_wait_for_or_change_another_writer(readonly_database):
    seed(entries_only=True)
    seed_order()
    with TestClient(app) as client:
        before = snapshot(readonly_database)
        with readonly_database.begin() as writer:
            writer.execute(sa.text("UPDATE menu_masters SET qty_per_serving=998 WHERE id='master'"))
            response = client.get("/orders/operator-order")
            assert response.status_code == 200
            payload = response.json()
            assert payload["menu_amount_resolution"]["blocker"] == "menu_amount_unresolved"
            assert payload["lines"][0]["quantity_corrected"] == 13
            assert payload["lines"][0]["actual_amount"] is None
            writer.rollback()
        assert snapshot(readonly_database) == before


def test_step4_http_enrichment_error_creates_no_artifacts(readonly_database, monkeypatch):
    seed()
    seed_order("Old unresolved")
    with db.session_scope() as session:
        session.add(OrderSheetDraft(id="draft", order_id="operator-order", draft_sheet_json={"fixture": "explicit"}))
        session.flush()
        session.add(OrderWorkflowState(order_id="operator-order", draft_id="draft", state="sheet_saved"))
    monkeypatch.setattr(order_workflow_v2_service, "_build_materialization_candidate_for_saved_sheet", lambda **kwargs: candidate())
    def fail_enrichment(*args, **kwargs):
        raise RuntimeError("injected enrichment failure")
    monkeypatch.setattr(output_builder, "build_order_lines_for_outputs", fail_enrichment)
    with TestClient(app) as client:
        before = snapshot(readonly_database)
        response = client.post("/orders/operator-order/workflow-v2/bagging")
        assert response.status_code == 400
        assert response.json()["detail"] == "bagging_output_enrichment_failed"
        assert snapshot(readonly_database) == before


def test_explicit_ingest_projection_read_and_menu_save_preserve_source_lines(readonly_database):
    from datetime import datetime
    from src.workers.ingest_mail_adapter import IngestEmailPayload
    seed(entries_only=True)
    source = {"line_id": "explicit-input-line", "date": "2099-06-07", "daypart": "朝食",
              "menu_name": "Dish", "diet_type": "regular", "area_id": "explicit-area",
              "quantity_original": 11, "quantity_corrected": 13}
    payload = IngestEmailPayload(message_id="owned-explicit-registration", pdf_uri="owned-registration.pdf",
                                 received_at=datetime(2099, 6, 7, 9), week_hint="2099-06@2099-06-07~2099-06-13")
    registered = order_service.create_order_from_ingest(payload, lines=[dict(source)])
    assert registered["menu_amount_resolution"]["blocker"] == "menu_amount_unresolved"
    for key, value in source.items():
        assert registered["lines"][0][key] == value
    duplicate = order_service.create_order_from_ingest(payload, lines=[dict(source)])
    assert duplicate["id"] == registered["id"]
    with TestClient(app) as client:
        response = client.get("/orders/" + registered["id"])
        assert response.status_code == 200
        assert response.json()["menu_amount_resolution"]["blocker"] == "menu_amount_unresolved"
    assert menu_service.update_item_status("2099-06", "MMI_REPAIR_entry",
        {"qty_per_serving": 37, "unit_type": "g", "daypart": "朝食", "category": "主菜", "diet_type": "regular"}) == "updated"
    resolved = order_service.get_order_by_id(registered["id"])
    assert resolved["menu_amount_resolution"]["status"] == "resolved"
    for key, value in source.items():
        assert resolved["lines"][0][key] == value
    assert resolved["lines"][0]["actual_amount"] == 481
    adopted = candidate()
    adopted["lines"] = [dict(source)]
    with db.session_scope() as session:
        order = session.get(Order, registered["id"])
        assert order_serialization_service.candidate_menu_amount_resolution(order, adopted)["status"] == "resolved"
    before = snapshot(readonly_database)
    _, error = order_workflow_v2_service.run_bagging(registered["id"])
    assert error == "saved_sheet_required"
    assert snapshot(readonly_database) == before


def test_month_only_week_stays_unselected_with_menu_date_options(readonly_database):
    seed(entries_only=True)
    seed_order()
    options, error = order_service.get_order_week_options("operator-order")
    assert error is None and options
    assert all(item["selected"] is False for item in options)
    with db.session_scope() as session:
        order = session.get(Order, "operator-order")
        selection = order_service._resolve_order_current_week_selection(order)
        assert selection["stored_week_value"] == "2099-06"
        assert selection["resolved_week_value"] is None
        assert selection["blocker_code"] == "week_choice_required"
        assert order_service._resolve_order_current_sheet_week_value(order) is None
