"""Owned SQLite/captured JSON boundary checks, not live OCR or provider proof."""
from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import db
from src.api import orders
from src.models.ingest_job import IngestJob
from src.models.menu import MonthlyMenu, MonthlyMenuItem, MonthlyMenuEntry
from src.services import facility_master_service, ingest_facility_selection_service
from src.services import ingest_job_service, menu_service, order_serialization_service
from src.services import order_service, uploaded_pdf_service

WEEK = "2026-01@2026-01-01~2026-01-03"


@pytest.fixture
def receipt(isolated_formal_schema, tmp_path, monkeypatch):
    authored = {"version_id": "read-version", "valid_from": "2026-01-01", "valid_to": "2026-01-31",
        "facility": {"facility_id": "F", "facility_name": "Boundary fixture", "areas": None,
            "quantity_setting": 0, "fax_template": {"template_id": "read-template",
                "units": "normalized", "table_box": [0, 0, 1, 1], "header_rows": 0,
                "columns": [{"index": 0, "source_index": 0, "role": "quantity",
                    "diet_type": "regular", "area_id": "X"}]}}}
    path = tmp_path / "canonical.json"
    path.write_text(json.dumps({"facilities": [{"facility_id": "F", "versions": [authored]}]}))
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(path))
    captured = ingest_facility_selection_service.capture_selection(
        "F", "2026-01-02", facility_master_service.get_snapshot().revision, "read-version", WEEK)
    pdf = {"id": "pdf", "message_id": "message", "storage_uri": "gs://fixture/read-only",
        "received_at": "2026-01-02T12:00:00", "facility_hint": "F", "week_hint": WEEK,
        "facility_name": None, "skip_ocr": False, "source_kind": "manual_upload",
        "original_filename": "boundary.bin", "content_sha256": "a" * 64}
    payload = uploaded_pdf_service.build_ingest_payload(pdf, facility_selection=captured)
    with db.session_scope() as session:
        session.add(IngestJob(id="message", generation="owned", payload=payload,
            input_digest=ingest_job_service.payload_digest(payload)))
    monkeypatch.setattr(uploaded_pdf_service, "get_uploaded_pdf_by_message_id", lambda _: deepcopy(pdf))
    return pdf, isolated_formal_schema


@pytest.mark.parametrize("failure", ["missing_week", "month_only", "invalid_week", "missing_job",
    "missing_selection", "digest", "damaged_selection", "foreign_owner"])
def test_read_keeps_blocked_hints_and_dispatch_refuses(receipt, monkeypatch, failure):
    pdf, engine = receipt
    if failure in {"missing_week", "month_only", "invalid_week"}:
        pdf["week_hint"] = {"missing_week": None, "month_only": "2026-01", "invalid_week": "invalid"}[failure]
    else:
        with db.session_scope() as session:
            job = session.get(IngestJob, "message")
            if failure == "missing_job":
                session.delete(job)
            elif failure == "digest":
                job.input_digest = "foreign"
            else:
                payload = deepcopy(job.payload)
                if failure == "missing_selection":
                    payload.pop("facility_selection")
                elif failure == "damaged_selection":
                    payload["facility_selection"]["canonical_lineage"]["authored_version"]["facility"]["quantity_setting"] = 1
                else:
                    payload["facility_hint"] = "foreign"
                job.payload = payload
                job.input_digest = ingest_job_service.payload_digest(payload)
    def forbid_write(connection, cursor, statement, parameters, context, executemany):
        assert statement.lstrip().split()[0].upper() in {"SELECT", "PRAGMA"}, statement
    sa.event.listen(engine, "before_cursor_execute", forbid_write)
    monkeypatch.setattr(orders.candidate_resolution_service, "resolve_order_list_candidates",
        lambda **_: pytest.fail("unverified hints must not become resolved candidates"))
    row = {"id": "order", "facility": "F", "week": None, "message_id": "message"}
    original = deepcopy(row)
    app = FastAPI()
    @app.get("/read")
    def read():
        result = deepcopy(row)
        orders._attach_order_list_candidate_summary(result, cached_payload={"dates": ["2026-01-02"]})
        return result
    try:
        response = TestClient(app).get("/read")
        assert response.status_code == 200
        summary = response.json()["candidate_resolution"]
        assert summary["reference_hints"]["week_hint"] == pdf["week_hint"]
        for key in ("facility", "week"):
            assert summary["resolutions"][key]["resolved_value"] is None
            assert summary["resolutions"][key]["blocked"] is True
            assert summary["resolutions"][key]["blocked_reasons"]
        assert summary["gate_summary"]["unresolved_types"] == ["facility", "week"]
        assert row == original
        with pytest.raises(ValueError):
            uploaded_pdf_service.build_ingest_payload(pdf)
    finally:
        sa.event.remove(engine, "before_cursor_execute", forbid_write)


def test_valid_captured_receipt_is_not_reselected(receipt, monkeypatch):
    pdf, _ = receipt
    monkeypatch.setattr(facility_master_service, "get_snapshot", lambda: pytest.fail("no latest JSON selection"))
    calls = []
    def resolve(**kwargs):
        calls.append(kwargs)
        return {"resolutions": {"week": {"resolved_value": kwargs["week_code"]}}}
    monkeypatch.setattr(orders.candidate_resolution_service, "resolve_order_list_candidates", resolve)
    row = {"facility": None, "week": None, "message_id": "message"}
    orders._attach_order_list_candidate_summary(row, cached_payload=None)
    assert row["candidate_resolution"]["resolutions"]["week"]["resolved_value"] == WEEK
    assert calls[0]["facility_code"] == "F"
    assert uploaded_pdf_service.build_ingest_payload(pdf)["facility_selection"]["quantity_setting"] == 0


def test_actual_orders_endpoint_keeps_unresolved_row(receipt, monkeypatch):
    from src.main import app
    pdf, _ = receipt
    pdf["week_hint"] = None
    row = {"id": "owned-order", "facility": None, "week": None, "week_value": None,
        "status": "要確認", "document": "fixture", "message_id": "message"}
    monkeypatch.setattr(order_service, "list_orders_page", lambda **kwargs: {
        "orders": [deepcopy(row)], "has_more": False, "next_cursor": None})
    monkeypatch.setattr(order_service, "_load_order_ocr_cache_map", lambda ids: {})
    monkeypatch.setattr(orders.workflow_state_service, "list_workflow_states", lambda ids: {})
    response = TestClient(app).get("/orders?include_ocr=false&include_candidate_summary=true")
    assert response.status_code == 200
    result = response.json()["orders"][0]
    assert result["id"] == row["id"] and result["status"] == row["status"]
    assert result["candidate_resolution"]["resolutions"]["week"]["resolved_value"] is None
    assert result["candidate_resolution"]["resolutions"]["week"]["blocked_reasons"] == ["week_hint_required"]


@pytest.mark.parametrize("diet", [None, "", "regular", "soft", "mixer"])
@pytest.mark.parametrize("quantity", [0, None])
def test_diet_projection_identity_and_sql_filter(isolated_formal_schema, diet, quantity):
    with db.session_scope() as session:
        session.add(MonthlyMenu(id="month", month_start=date(2026, 1, 1)))
        session.flush()
        session.add(MonthlyMenuItem(id="item", monthly_menu_id="month", name="Dish",
            daypart="朝食", diet_type=diet, qty_per_serving=quantity, unit_type="g"))
    with db.session_scope() as session:
        item = session.get(MonthlyMenuItem, "item")
        projection = menu_service._readonly_monthly_items(session, "month", [item], [])[0]
        assert projection["qty_per_serving"] == quantity
        assert menu_service._monthly_item_identity_key_from_item(item)[3] == (diet or "")
        if not diet:
            assert projection["confirmation_required"] is True
            assert "diet_type" in projection["unresolved_fields"]
            with pytest.raises(menu_service.MenuReadUnresolved):
                menu_service.require_resolved_menu_items([projection])
        elif quantity == 0:
            menu_service.require_resolved_menu_items([projection])
        for requested in (None, "regular", "soft", "mixer", "常食"):
            found = session.execute(sa.select(MonthlyMenuItem.id).where(
                menu_service._monthly_item_diet_filter(MonthlyMenuItem.diet_type, requested))).scalars().all()
            expected = bool(diet and requested and menu_service.normalize_diet_type(requested) == diet)
            assert found == (["item"] if expected else [])
        entry = MonthlyMenuEntry(menu_date=date(2026, 1, 2), daypart="朝食", slot_index=0, diet_type=diet)
        assert menu_service._monthly_entry_override_key(entry)[3] == (diet or "regular")


@pytest.mark.parametrize("diet", [None, "", "regular", "常食", "soft", "mixer"])
def test_amount_adoption_requires_explicit_item_diet(monkeypatch, diet):
    explicit = menu_service.normalize_diet_type(diet) or "regular"
    line = SimpleNamespace(menu_name="Dish", date=date(2026, 1, 2), daypart="朝食", diet_type=explicit)
    item = {"id": "item", "month_id": "month", "name": "Dish", "daypart": "朝食",
        "menu_dates": ["2026-01-02"], "diet_type": diet, "qty_per_serving": 0, "unit_type": "g"}
    monkeypatch.setattr(order_serialization_service, "_collect_menu_items_for_week", lambda *args: [item])
    order = SimpleNamespace(lines=[line], week_code=WEEK, facility_code="F")
    meta, resolution = order_serialization_service._resolve_menu_amounts(order)
    if not diet:
        assert meta == {} and resolution["blocker"] == "menu_amount_unresolved"
        with pytest.raises(menu_service.MenuReadUnresolved):
            order_serialization_service._build_menu_amount_meta(order)
    else:
        assert resolution["status"] == "resolved"
        assert meta[order_serialization_service._line_menu_identity(line)]["qty_per_serving"] == 0


@pytest.mark.parametrize("blocker", ["week_unresolved", "week_hint_required", "week_hint_invalid"])
@pytest.mark.parametrize("revision", [None, "exact-context-revision"])
def test_blocked_review_uses_only_context_revision(monkeypatch, blocker, revision):
    from src.services import order_current_state_service
    def forbidden(*args, **kwargs):
        pytest.fail("blocked context must not look up cache/latest draft")
    monkeypatch.setattr(order_service, "_current_sheet_revision_id", forbidden)
    monkeypatch.setattr(order_current_state_service, "get_current_state_payload", forbidden)
    summary = {"current_sheet_revision_id": "old", "ocr_apply_blockers": ["old-stop"],
        "ocr_confirm_blockers": ["old-stop"], "ocr_can_apply_draft": True, "ocr_can_confirm": True}
    original = deepcopy(summary)
    context = {"order_id": "owned", "rows": [], "blockers": [blocker], "resolved_week_id": None}
    if revision:
        context["current_sheet_revision_id"] = revision
    merged = order_service._merge_order_review_summary_with_sheet_context(summary, current_sheet_context=context)
    assert summary == original
    assert merged.get("current_sheet_revision_id") == revision
    if revision is None:
        assert "current_sheet_revision_id" not in merged
    assert merged["ocr_draft_row_count"] == 0
    assert blocker in merged["ocr_apply_blockers"] and blocker in merged["ocr_confirm_blockers"]
    assert "old-stop" in merged["ocr_apply_blockers"]
    assert merged["ocr_can_apply_draft"] is False and merged["ocr_can_confirm"] is False


@pytest.mark.parametrize("difference", ["monthly_menu_id", "name", "facility_override", "daypart", "category", "diet_type"])
def test_monthly_item_lookup_never_adopts_neighbor(isolated_formal_schema, difference):
    identity = dict(monthly_menu_id="month", name="Dish", facility_override="F",
        daypart="朝食", category="主菜", diet_type="regular")
    neighbor = dict(identity, **{difference: {
        "monthly_menu_id": "other-month", "name": "Other", "facility_override": "OTHER",
        "daypart": "昼食", "category": "副菜", "diet_type": "soft"}[difference]})
    with db.session_scope() as session:
        session.add_all([MonthlyMenu(id="month", month_start=date(2026, 1, 1)),
            MonthlyMenu(id="other-month", month_start=date(2026, 2, 1))])
        session.flush()
        session.add(MonthlyMenuItem(id="neighbor", qty_per_serving=0, **neighbor))
    with db.session_scope() as session:
        for finder in (menu_service._find_existing_monthly_item, menu_service._find_best_monthly_item_for_entry):
            assert finder(session, month_id="month", name="Dish", facility_override="F",
                daypart="朝食", category="主菜", diet_type="regular") is None
        assert session.get(MonthlyMenuItem, "neighbor").qty_per_serving == 0


@pytest.mark.parametrize("diet", [None, "", " "])
def test_missing_diet_never_selects_any_item_or_entry(isolated_formal_schema, diet):
    with db.session_scope() as session:
        session.add(MonthlyMenu(id="month", month_start=date(2026, 1, 1)))
        session.flush()
        for index, stored in enumerate([None, "", "regular", "soft"]):
            session.add(MonthlyMenuItem(id=f"item-{index}", monthly_menu_id="month", name=f"Dish-{index}",
                daypart="朝食", diet_type=stored, qty_per_serving=None))
            session.add(MonthlyMenuEntry(id=f"entry-{index}", monthly_menu_id="month", name="Dish",
                menu_date=date(2026, 1, 2), daypart="朝食", slot_index=0, diet_type=stored))
    with db.session_scope() as session:
        for index in range(4):
            assert menu_service._find_best_monthly_item_for_entry(session, month_id="month",
                name=f"Dish-{index}", facility_override=None, daypart="朝食", diet_type=diet) is None
        assert menu_service._find_existing_monthly_entry(session, month_id="month",
            menu_date=date(2026, 1, 2), daypart="朝食", slot_index=0,
            facility_override=None, diet_type=diet) is None


@pytest.mark.parametrize("empty", [None, ""])
@pytest.mark.parametrize("quantity", [0, None])
def test_empty_item_identity_is_exact_base_scope_and_preserves_quantity(isolated_formal_schema, empty, quantity):
    with db.session_scope() as session:
        session.add(MonthlyMenu(id="month", month_start=date(2026, 1, 1)))
        session.flush()
        session.add_all([
            MonthlyMenuItem(id="exact", monthly_menu_id="month", name="Dish", facility_override=empty,
                daypart=empty, category=empty, diet_type="regular", qty_per_serving=quantity),
            MonthlyMenuItem(id="foreign", monthly_menu_id="month", name="Dish", facility_override="F",
                daypart=empty, category=empty, diet_type="regular", qty_per_serving=999),
            MonthlyMenuItem(id="meal", monthly_menu_id="month", name="Dish", daypart="朝食",
                category=empty, diet_type="regular"),
            MonthlyMenuItem(id="category", monthly_menu_id="month", name="Dish", daypart=empty,
                category="主菜", diet_type="regular")])
    with db.session_scope() as session:
        for finder in (menu_service._find_existing_monthly_item, menu_service._find_best_monthly_item_for_entry):
            result = finder(session, month_id="month", name="Dish", facility_override=None,
                daypart=None, category=None, diet_type="常食")
            assert result.id == "exact" and result.qty_per_serving == quantity
        assert menu_service._find_existing_monthly_item(session, "month", "Dish", None,
            diet_type="regular", exclude_id="exact") is None


@pytest.mark.parametrize("difference", ["monthly_menu_id", "menu_date", "daypart", "slot_index", "facility_override", "diet_type"])
def test_monthly_entry_lookup_requires_exact_identity(isolated_formal_schema, difference):
    identity = dict(monthly_menu_id="month", menu_date=date(2026, 1, 2), daypart="",
        slot_index=None, facility_override=None, diet_type="regular")
    neighbor = dict(identity, **{difference: {
        "monthly_menu_id": "other-month", "menu_date": date(2026, 1, 3), "daypart": "朝食",
        "slot_index": 0, "facility_override": "F", "diet_type": "soft"}[difference]})
    with db.session_scope() as session:
        session.add_all([MonthlyMenu(id="month", month_start=date(2026, 1, 1)),
            MonthlyMenu(id="other-month", month_start=date(2026, 2, 1))])
        session.flush()
        session.add(MonthlyMenuEntry(id="neighbor", name="Dish", **neighbor))
    with db.session_scope() as session:
        arguments = dict(identity)
        arguments["month_id"] = arguments.pop("monthly_menu_id")
        assert menu_service._find_existing_monthly_entry(session, **arguments) is None
        session.add(MonthlyMenuEntry(id="exact", name="Dish", **identity))
        session.flush()
        arguments["diet_type"] = "常食"
        assert menu_service._find_existing_monthly_entry(session, **arguments).id == "exact"


@pytest.mark.parametrize("kind", ["item", "entry"])
def test_exact_duplicate_monthly_identity_stops(isolated_formal_schema, kind):
    with db.session_scope() as session:
        session.add(MonthlyMenu(id="month", month_start=date(2026, 1, 1)))
        session.flush()
        if kind == "item":
            # Only the owned fixture database is relaxed to represent damaged legacy data.
            session.execute(sa.text("DROP INDEX uq_monthly_menu_items_scope_identity"))
            session.add_all([MonthlyMenuItem(id=value, monthly_menu_id="month", name="Dish",
                daypart="朝食", category="主菜", diet_type="regular", qty_per_serving=0)
                for value in ("one", "two")])
        else:
            session.add_all([MonthlyMenuEntry(id=value, monthly_menu_id="month", name="Dish",
                menu_date=date(2026, 1, 2), daypart="朝食", slot_index=0, diet_type="regular")
                for value in ("one", "two")])
    with db.session_scope() as session:
        if kind == "item":
            for finder in (menu_service._find_existing_monthly_item, menu_service._find_best_monthly_item_for_entry):
                with pytest.raises(ValueError, match="monthly_menu_item_identity_ambiguous"):
                    finder(session, month_id="month", name="Dish", facility_override=None,
                        daypart="朝食", category="主菜", diet_type="regular")
        else:
            with pytest.raises(ValueError, match="monthly_menu_entry_identity_ambiguous"):
                menu_service._find_existing_monthly_entry(session, month_id="month",
                    menu_date=date(2026, 1, 2), daypart="朝食", slot_index=0,
                    facility_override=None, diet_type="regular")
