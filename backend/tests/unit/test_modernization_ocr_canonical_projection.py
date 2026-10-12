"""Local producer/consumer and SQLAlchemy boundary definitions, not live OCR proof.

Shape authority: ocr_pipeline/app/main.py output_payload/pages/tables;
integration/test_ocr_structured_payload.py table matrices; fax_extractor semantic
rows/date_strings; models.menu.MonthlyMenuEntry. Only the external OpenAI OCR
response is replaced. A generated local PDF is verified through the real stored
Order/OrderDocument/UploadedPdf receipt, not a fabricated workflow receipt.
No pipeline job ownership, HTTP sheet, terminal job, or operator/stg claim.
"""

from copy import deepcopy
from datetime import date, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import fitz
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from src import db
from src.models.document import OrderDocument
from src.models.facility import Facility, FacilityConfig
from src.models.menu import (
    BaseMenuCycleItem, MenuEditRevision, MenuFacilityOverride, MenuMaster,
    MenuRule, MonthlyMenu, MonthlyMenuEntry, MonthlyMenuImport, MonthlyMenuItem,
)
from src.models.order import Order
from src.models.uploaded_pdf import UploadedPdf
from src.services import fax_extractor as fax, menu_service, order_service as orders
from src.services import openai_ocr_service, storage_service
from src.services.workflow_attempt_guard_service import capture_selected_pdf_receipt


FIELDS = ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.soft_x", "qty.mixer_x"]
RECEIVED = datetime(2026, 10, 1)
PERIOD = "2026-09@2026-09-30~2026-10-06"


def template():
    return {
        "template_id": "projection-unit", "page": 1,
        "_force_main_ocr_provider": "openai",
        "quantity_assignment_strategy": "legacy",
        "main_ocr_row_fields": list(FIELDS),
        "columns": [
            {"column_id": "date", "index": 0, "source_index": 0, "role": "date"},
            {"column_id": "daypart", "index": 1, "source_index": 1, "role": "daypart"},
            {"column_id": "menu", "index": 2, "source_index": 2, "role": "menu_name"},
            *[{"column_id": diet, "index": index, "source_index": index,
               "role": "quantity", "diet_type": diet, "area_id": "x"}
              for index, diet in enumerate(("regular", "soft", "mixer"), 3)],
        ],
    }


def structured_payload():
    table = {
        "table_id": "p1-t1", "page_index": 0,
        "rows": [
            ["日付", "区分", "献立", "常食", "軟菜", "ミキサー"],
            ["2026/09/30", "昼", "Canonical A", 7, 0, ""],
            ["", "昼", "Canonical B", 0, "", 7],
            ["2026/10/01", "夕", "Canonical C", "", 7, 0],
        ],
    }
    return {
        "version": "2", "status": "done", "stage": "done",
        "engine": "yomitoku", "pipeline_mode": "structured_v2",
        "template_id": "projection-unit", "tables": [table],
        "pages": [{"page_index": 0, "tables": [deepcopy(table)]}],
        "table_raw": "|日付|区分|献立|常食|軟菜|ミキサー|\n"
                     "|---|---|---|---|---|---|\n"
                     "|2026/08/01|朝|Foreign Markdown|99|98|97|",
        "date_strings": ["2026/08/01"],
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = sa.create_engine(sa.URL.create("sqlite", database=str(tmp_path / "projection.sqlite3")))
    metadata = sa.MetaData()

    def copy_table(table):
        if table.key in metadata.tables:
            return
        table.to_metadata(metadata)
        for foreign_key in table.foreign_keys:
            copy_table(foreign_key.column.table)

    for model in (Order, OrderDocument, UploadedPdf, Facility, FacilityConfig,
                  MonthlyMenu, MonthlyMenuItem, MonthlyMenuEntry, MonthlyMenuImport,
                  MenuEditRevision, MenuMaster, MenuFacilityOverride, MenuRule, BaseMenuCycleItem):
        copy_table(model.__table__)
    metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(engine, autoflush=False, future=True))
    monkeypatch.setattr(menu_service, "engine", engine)
    monkeypatch.setattr(menu_service, "_MENU_SCHEMA_INITIALIZED", False)
    menu_service.ensure_menu_schema()
    with db.session_scope() as session:
        session.add_all([
            MonthlyMenu(id="2026-09", month_start=date(2026, 9, 1)),
            MonthlyMenu(id="2026-10", month_start=date(2026, 10, 1)),
        ])
    yield engine
    engine.dispose()


@pytest.fixture
def producer(store, tmp_path, monkeypatch):
    path = tmp_path / "synthetic-provider-boundary.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((40, 40), "Local projection boundary, not operator FAX")
        path.write_bytes(document.tobytes())
    order_id, document_id, message_id = [str(uuid4()) for _ in range(3)]
    with db.session_scope() as session:
        session.add(OrderDocument(id=document_id, storage_uri=str(path), source_email_id=message_id))
        session.add(UploadedPdf(id=str(uuid4()), storage_uri=str(path), message_id=message_id,
                               original_filename=path.name, received_at=RECEIVED,
                               content_sha256=sha256(path.read_bytes()).hexdigest()))
        session.add(Order(id=order_id, current_document_id=document_id, document_uri=str(path),
                          message_id=message_id, received_at=RECEIVED, week_code=PERIOD))
        session.flush()
        receipt = capture_selected_pdf_receipt(session, session.get(Order, order_id))
    selected_bytes = storage_service.load_selected_pdf_bytes(str(path), receipt)
    calls = []

    def extract(payload, selected_template):
        def external_response(**kwargs):
            assert kwargs["pdf_bytes"] == selected_bytes
            assert kwargs["template"] == selected_template
            calls.append(deepcopy(payload))
            return {**deepcopy(payload), "_ocr_raw_payload": deepcopy(payload)}

        monkeypatch.setattr(openai_ocr_service, "run_openai_ocr", external_response)
        return fax.extract_fax_data(selected_bytes, selected_template)

    return extract, calls


def test_actual_producer_reparse_parity_no_markdown_identity_or_dates(producer):
    extract, calls = producer
    payload, selected = structured_payload(), template()
    original = deepcopy(payload)
    result = extract(payload, selected)
    raw_rows = orders._extract_sheet_rows_from_payload_uncanonicalized(payload, selected)
    assert result.table_rows == raw_rows
    assert [row[2] for row in raw_rows] == ["Canonical A", "Canonical B", "Canonical C"]
    assert [row[3:] for row in raw_rows] == [["7", "0", ""], ["0", "", "7"], ["", "7", "0"]]
    assert raw_rows[1][0] == ""
    assert result.date_strings == ["2026/09/30", "2026/10/01"]
    assert orders._collect_sheet_dates_from_payload(payload, RECEIVED, selected, explicit_week=PERIOD) == [date(2026, 9, 30), date(2026, 10, 1)]
    assert len(calls) == 1 and payload == original


@pytest.mark.parametrize("tables", [None, []])
@pytest.mark.parametrize("alternative", ["markdown", "semantic"])
def test_declared_structured_absence_stops_actual_producer_and_reparse(producer, tables, alternative):
    payload = structured_payload()
    payload.pop("tables")
    if tables is not None:
        payload["tables"] = tables
    payload["pages"] = []
    if alternative == "semantic":
        payload["rows"] = [{"date_mmdd": "2026/08/01", "menu": "Foreign", "qty.regular_x": 99}]
    extract, _ = producer
    for operation in (lambda: extract(payload, template()),
                      lambda: orders._extract_sheet_rows_from_payload(payload, template()),
                      lambda: orders._collect_sheet_dates_from_payload(payload, RECEIVED, template(), explicit_week=PERIOD)):
        with pytest.raises(fax.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
            operation()


@pytest.mark.parametrize("failure", ["missing", "partial", "duplicate", "ambiguous", "out_of_range", "position_fallback"])
def test_explicit_quantity_binding_failures_stop_both_consumers(producer, failure):
    payload, selected = structured_payload(), template()
    if failure == "missing":
        selected.pop("columns")
    elif failure == "partial":
        selected["columns"].pop()
    elif failure == "duplicate":
        selected["columns"].append(deepcopy(selected["columns"][3]))
    elif failure == "out_of_range":
        selected["columns"][3]["source_index"] = 60
    elif failure == "ambiguous":
        payload["column_mapping_resolution"] = {"requires_user_choice": True}
    else:
        payload["column_mapping_resolution"] = {"decision_source": "position_fallback"}
    extract, _ = producer
    for operation in (lambda: extract(payload, selected),
                      lambda: orders._extract_sheet_rows_from_payload(payload, selected)):
        with pytest.raises(fax.CanonicalOcrProjectionError, match="^sheet_quantity_column_unmapped$"):
            operation()


def test_operator_binding_without_template_quantity_columns_is_shared(producer):
    selected, payload = template(), structured_payload()
    selected["columns"] = selected["columns"][:3]
    payload["column_mapping_resolution"] = {
        "resolved_value": "3:qty.regular_x|4:qty.soft_x|5:qty.mixer_x",
    }
    result = producer[0](payload, selected)
    assert result.table_rows == orders._extract_sheet_rows_from_payload_uncanonicalized(payload, selected)
    assert result.table_rows[0][3:] == ["7", "0", ""]


@pytest.mark.parametrize("mapping", [
    "3:qty.regular_x|3:qty.soft_x|5:qty.mixer_x",
    "3:qty.regular_x|4:qty.regular_x|5:qty.mixer_x",
    "3:qty.regular_x|4:qty.soft_x|60:qty.mixer_x",
])
def test_duplicate_or_out_of_range_operator_binding_stops(producer, mapping):
    payload, selected = structured_payload(), template()
    selected.pop("columns")
    payload["column_mapping_resolution"] = {"resolved_value": mapping}
    for operation in (lambda: producer[0](payload, selected),
                      lambda: orders._extract_sheet_rows_from_payload(payload, selected)):
        with pytest.raises(fax.CanonicalOcrProjectionError, match="^sheet_quantity_column_unmapped$"):
            operation()


@pytest.mark.parametrize("conflicting", [False, True])
def test_registered_table_aliases_are_not_additional_rows(producer, conflicting):
    payload = structured_payload()
    if conflicting:
        payload["pages"][0]["tables"][0]["rows"][1][3] = 99
        for operation in (lambda: producer[0](payload, template()),
                          lambda: orders._extract_sheet_rows_from_payload(payload, template())):
            with pytest.raises(fax.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
                operation()
    else:
        assert len(producer[0](payload, template()).table_rows) == 3
        assert len(orders._extract_sheet_rows_from_payload(payload, template())) == 3


@pytest.mark.parametrize("shape", ["flat", "nested", "indexed"])
def test_semantic_provider_needs_no_physical_binding(producer, shape):
    selected = template()
    selected.pop("columns")
    row = {"date_mmdd": "2026/10/01", "daypart": "昼", "menu": "Semantic", "qty.regular_x": 7,
           "qty.soft_x": 0, "qty.mixer_x": ""}
    if shape == "nested":
        row = {"date_mmdd": "2026/10/01", "daypart": "昼", "menu": "Semantic",
               "qty": {"regular_x": 7, "soft_x": 0, "mixer_x": ""}}
    elif shape == "indexed":
        row["row_index"] = 0
    payload = {"rows": [row], "date_strings": ["2026/10/01"]}
    result = producer[0](payload, selected)
    assert result.table_rows == orders._extract_sheet_rows_from_payload_uncanonicalized(payload, selected)
    assert result.table_rows == [["2026/10/01", "昼", "Semantic", "7", "0", ""]]


def test_explicit_markdown_provider_contract_uses_same_binding(producer):
    payload = {
        "table_raw": "|日付|区分|献立|常食|軟菜|ミキサー|\n"
                     "|---|---|---|---|---|---|\n"
                     "|2026/10/01|昼|Markdown canonical|7|0||",
        "date_strings": ["2026/08/01"],
    }
    selected = template()
    result = producer[0](payload, selected)
    assert result.table_rows == [["2026/10/01", "昼", "Markdown canonical", "7", "0", ""]]
    assert result.table_rows == orders._extract_sheet_rows_from_payload_uncanonicalized(payload, selected)
    assert result.date_strings == ["2026/10/01"]


def test_owned_recognizer_dates_override_earlier_pipeline_and_missing_tables():
    payload, selected = structured_payload(), template()
    payload["tables"], payload["pages"] = [], []
    payload["_reparse_recognizer_result"] = {
        "fields": FIELDS, "rows": [["2026/10/01", "昼", "Owned semantic", 7, 0, ""]],
        "date_strings": ["2026/10/01"], "provider": "openai",
    }
    assert orders._extract_sheet_rows_from_payload_uncanonicalized(payload, selected) == payload["_reparse_recognizer_result"]["rows"]
    assert orders._collect_sheet_dates_from_payload(payload, RECEIVED, selected, explicit_week=PERIOD) == [date(2026, 10, 1)]
    assert fax.date_strings_from_row_source(payload, selected) == ["2026/10/01", "2026/10/01"]


def test_date_field_not_first_two_and_explicit_semantic_blank_geometry(producer):
    selected = template()
    selected.pop("columns")
    selected["main_ocr_row_fields"] = ["menu", "daypart", "date_mmdd", *FIELDS[3:]]
    payload = {"rows": [["Menu 08/01 is not a date", "昼", "", 7, 0, ""]],
               "date_strings": ["2026/09/30"]}
    result = producer[0](payload, selected)
    assert result.table_rows[0][2] == ""
    assert result.date_strings == ["2026/09/30"]
    assert orders._collect_sheet_dates_from_payload(payload, RECEIVED, selected, explicit_week=PERIOD) == [date(2026, 9, 30)]
    payload["rows"][0][2] = "2026/10/01"
    payload["date_strings"] = []
    assert orders._collect_sheet_dates_from_payload(payload, RECEIVED, selected, explicit_week=PERIOD) == [date(2026, 10, 1)]


def test_structured_date_field_reordered_ignores_auxiliary_date_metadata(producer):
    selected, payload = template(), structured_payload()
    selected["main_ocr_row_fields"] = ["menu", "daypart", "date_mmdd", *FIELDS[3:]]
    result = producer[0](payload, selected)
    assert [row[0] for row in result.table_rows] == ["Canonical A", "Canonical B", "Canonical C"]
    assert [row[2] for row in result.table_rows] == ["2026/09/30", "", "2026/10/01"]
    assert result.date_strings == ["2026/09/30", "2026/10/01"]
    assert orders._collect_sheet_dates_from_payload(payload, RECEIVED, selected, explicit_week=PERIOD) == [date(2026, 9, 30), date(2026, 10, 1)]


def test_nonbody_table_is_not_an_alternate_quantity_or_date_source(producer):
    payload = structured_payload()
    payload["tables"].insert(0, {"table_id": "footer", "page_index": 0,
                                 "rows": [["備考", "2026/08/01"]]})
    result = producer[0](payload, template())
    assert [row[2] for row in result.table_rows] == ["Canonical A", "Canonical B", "Canonical C"]
    assert result.date_strings == ["2026/09/30", "2026/10/01"]
    assert result.table_rows == orders._extract_sheet_rows_from_payload_uncanonicalized(payload, template())


def add_entry(day, daypart, slot, diet="regular", name=None):
    with db.session_scope() as session:
        session.add(MonthlyMenuEntry(id=str(uuid4()), monthly_menu_id=day.strftime("%Y-%m"),
            menu_date=day, daypart=daypart, slot_index=slot, diet_type=diet,
            name=name if name is not None else f"{diet}-{day}-{daypart}-{slot}"))


def test_real_monthly_regular_eight_per_day_fifty_six_cross_month(store):
    first = date(2026, 9, 30)
    for offset in range(7):
        day = first + timedelta(days=offset)
        for daypart, count in (("breakfast", 2), ("lunch", 3), ("dinner", 3)):
            for slot in range(count):
                for diet in ("regular", "soft", "mixer"):
                    add_entry(day, daypart, slot, diet)
            add_entry(day, daypart, count + 5, "soft")
            add_entry(day, daypart, count + 6, "mixer")
    entries = orders._build_position_menu_entries(PERIOD)
    assert len(entries) == 56
    assert {day: sum(entry["menu_date"] == day for entry in entries)
            for day in (first + timedelta(days=i) for i in range(7))} == {
                first + timedelta(days=i): 8 for i in range(7)}
    assert all(entry["diet_type"] == "regular" and entry["menu_name"].startswith("regular-") for entry in entries)
    assert all(set(entry["diet_variants"]) == {"regular", "soft", "mixer"} for entry in entries)
    assert entries[0]["menu_date"] == first and entries[-1]["menu_date"] == date(2026, 10, 6)


@pytest.mark.parametrize("daypart", ["breakfast", "lunch", "dinner"])
@pytest.mark.parametrize("count", [4, 5, 6])
def test_dynamic_physical_slots_do_not_assume_fixed_three(store, daypart, count):
    day = date(2026, 10, 1)
    for slot in range(count):
        add_entry(day, daypart, slot, "soft", "Logical soft")
        add_entry(day, daypart, slot, "regular", f"Regular {slot}")
    entries = orders._build_position_menu_entries(PERIOD)
    assert [(entry["slot_index"], entry["menu_name"]) for entry in entries] == [
        (slot, f"Regular {slot}") for slot in range(count)]
    assert all(entry["diet_variants"]["soft"]["name"] == "Logical soft" for entry in entries)


@pytest.mark.parametrize("broken,code", [
    ("slot", "physical_menu_slot_identity_required"),
    ("diet", "physical_menu_diet_identity_required"),
    ("name", "physical_menu_variant_name_required"),
    ("duplicate", "physical_menu_row_duplicate"),
])
def test_invalid_or_ambiguous_regular_identity_stops_real_monthly_read(store, broken, code):
    day = date(2026, 10, 1)
    add_entry(day, "lunch", None if broken == "slot" else 0,
              None if broken == "diet" else "regular", "" if broken == "name" else "Regular")
    if broken == "duplicate":
        add_entry(day, "lunch", 0, "regular", "Competing regular")
    with pytest.raises(ValueError, match=f"^{code}$"):
        orders._build_position_menu_entries(PERIOD)


def test_soft_and_mixer_only_never_invent_regular_physical_rows(store):
    add_entry(date(2026, 10, 1), "lunch", 0, "soft")
    add_entry(date(2026, 10, 1), "lunch", 1, "mixer")
    assert orders._build_position_menu_entries(PERIOD) == []


# These are in-memory shared-consumer boundaries, not public bootstrap/job proof.
def bound_projection(target, source, selected=None, *, overlay=True):
    return orders._apply_payload_quantities_numeric_only(
        rows=target, fields=FIELDS,
        quantity_index=orders._build_sheet_quantity_index(FIELDS, template=selected or template()),
        payload_rows=source, allow_heuristics=False,
        overlay_structural_fields_from_sheet_rows=overlay,
        allow_row_index_fallback=False, template=selected or template(),
    )


def reparse_baseline(rows, raw_output=None):
    # _resolve_reparse_current_sheet_baseline_rows consumes this actual shape.
    baseline = {"fields": list(FIELDS), "rows": deepcopy(rows),
                "row_ids": [f"observed-row-{i}" for i in range(len(rows))],
                "baseline_source": "sheet"}
    if raw_output is not None:
        baseline["raw_output"] = deepcopy(raw_output)
    return baseline


def test_bound_mixed_column_preserves_each_raw_cell_and_individual_review():
    source = [["2026/10/01", "昼", f"Menu {i}", a, str(i + 1), ""]
              for i, a in enumerate(["7", "0", "?", "", "?", "?"])]
    before = deepcopy(source)
    target = [{"values": [*row[:3], "99", "98", "97"]} for row in source]
    stats = bound_projection(target, source)
    values = [row["values"] for row in target]
    assert values == before
    assert source == before
    assert stats["exact"] == 6 and stats["unmapped"] == 0
    assert all(stats[key] == 0 for key in ("partial", "neighbor", "row_index", "span_copy"))
    issues = orders._canonical_quantity_cell_issues(values, FIELDS)
    assert [(issue["row_index"], issue["column_index"], issue["raw"], issue["value"])
            for issue in issues] == [(2, 3, "?", "?"), (4, 3, "?", "?"), (5, 3, "?", "?")]
    assert all(issue["field"] == "qty.regular_x" and issue["issue_code"] == "review_required"
               and issue["severity"] == "warning" and issue["source"] == "ocr_payload"
               for issue in issues)


@pytest.mark.parametrize("overlay", [False, True])
def test_wrong_meal_cannot_be_hidden_by_structural_overlay(overlay):
    target = [{"values": ["2026/10/01", "昼", "Same menu", "12", "3", "4"]}]
    source = [["2026/10/01", "夕", "Same menu", "7", "0", ""]]
    before = deepcopy((target, source))
    with pytest.raises(fax.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        bound_projection(target, source, overlay=overlay)
    assert (target, source) == before


def test_reordered_exact_identities_transcribe_without_row_index_rescue():
    target = [{"values": ["2026/10/01", meal, "Same menu", "99", "99", "99"]}
              for meal in ("朝", "昼", "夕")]
    source = [["2026/10/01", "夕", "Same menu", "7", "0", ""],
              ["2026/10/01", "朝", "Same menu", "0", "", "4"],
              ["2026/10/01", "昼", "Same menu", "3", "2", "1"]]
    before = deepcopy(source)
    stats = bound_projection(target, source)
    assert [row["values"][3:] for row in target] == [["0", "", "4"], ["3", "2", "1"], ["7", "0", ""]]
    assert stats["exact"] == 3
    assert all(stats[key] == 0 for key in ("partial", "neighbor", "row_index"))
    mapping, counts, stages = orders._build_payload_row_mapping_by_menu_priority(
        rows=target, fields=FIELDS, payload_rows=source, template=template())
    assert mapping == {0: 1, 1: 2, 2: 0}
    assert counts == {"exact": 3, "partial": 0, "neighbor": 0, "row_index": 0}
    assert stages == {0: "exact", 1: "exact", 2: "exact"}
    assert source == before


@pytest.mark.parametrize("duplicate_side", ["source", "target"])
def test_duplicate_identity_never_selects_first_candidate(duplicate_side):
    row = ["2026/10/01", "昼", "Duplicate", "7", "0", ""]
    target, source = [{"values": deepcopy(row)}], [deepcopy(row)]
    if duplicate_side == "source":
        source.append([*row[:3], "99", "98", "97"])
    else:
        target.append({"values": [*row[:3], "99", "98", "97"]})
    before = deepcopy((target, source))
    for mapper, extra in ((orders._build_payload_row_mapping_by_menu_priority, {}),
                          (orders._build_payload_row_mapping_by_row_index_numeric_only,
                           {"quantity_columns": [3, 4, 5], "allow_row_index_fallback": False})):
        with pytest.raises(fax.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
            mapper(rows=target, fields=FIELDS, payload_rows=source, template=template(), **extra)
    with pytest.raises(fax.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        bound_projection(target, source)
    assert (target, source) == before


@pytest.mark.parametrize("raw_meal", ["unknown", "朝昼", "breakfast/lunch"])
def test_unknown_or_ambiguous_meal_survives_normalization_then_blocks(raw_meal):
    source = [["2026/10/01", meal, f"Menu {i}", "7", "0", ""]
              for i, meal in enumerate(["朝", raw_meal, "夕"])]
    before = deepcopy(source)
    canonical = orders._canonicalize_sheet_daypart_rows(rows=source, fields=FIELDS)
    assert canonical == before
    payload = {"rows": deepcopy(source)}
    assert orders._extract_sheet_rows_from_payload(payload, template()) == before
    target = [{"values": deepcopy(row)} for row in source]
    target[1]["values"][1] = "昼"
    with pytest.raises(fax.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        bound_projection(target, canonical)
    assert source == before and payload["rows"] == before


def test_equal_month_day_with_explicit_different_year_blocks():
    target = [{"values": ["2026/10/01", "昼", "Same menu", "12", "3", "4"]}]
    source = [["2025/10/01", "昼", "Same menu", "7", "0", ""]]
    before = deepcopy((target, source))
    with pytest.raises(fax.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        bound_projection(target, source)
    assert (target, source) == before


def test_authored_continuations_resolve_both_baseline_and_source_without_raw_mutation():
    selected = template()
    selected["fill_forward_roles"] = ["date", "daypart"]
    source = [["2026/09/30", "昼", "First", "7", "0", ""],
              ["", "", "Continuation", "0", "", "4"],
              ["2026/10/01", "夕", "Next date", "3", "2", "1"]]
    target = [[*row[:3], "99", "98", "97"] for row in source]
    before = deepcopy((target, source, selected))
    projected, stats = orders._project_payload_quantities_onto_sheet_row_lists(
        rows=target, fields=FIELDS, payload_rows=source,
        strict_raw_ocr_only=True, template=selected)
    assert projected == source
    assert projected[1][:2] == ["", ""]
    assert stats["exact"] == 3 and stats["unmapped"] == 0
    assert (target, source, selected) == before


@pytest.mark.parametrize("roles", [[], ["date"], ["daypart"], ["menu"]])
def test_missing_authored_continuation_role_blocks_instead_of_inheriting(roles):
    selected = template()
    selected["fill_forward_roles"] = roles
    source = [["2026/10/01", "昼", "First", "7", "0", ""],
              ["", "", "Second", "0", "", "4"]]
    before = deepcopy(source)
    with pytest.raises(fax.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        orders._project_payload_quantities_onto_sheet_row_lists(
            rows=source, fields=FIELDS, payload_rows=source,
            strict_raw_ocr_only=True, template=selected)
    assert source == before


def test_authored_continuation_cannot_carry_meal_across_changed_date():
    selected = template()
    selected["fill_forward_roles"] = ["date", "daypart"]
    source = [["2026/09/30", "昼", "First", "7", "0", ""],
              ["2026/10/01", "", "Second", "0", "", "4"]]
    target = [{"values": deepcopy(row)} for row in source]
    target[1]["values"][1] = "昼"
    before = deepcopy((target, source))
    with pytest.raises(fax.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        bound_projection(target, source, selected)
    assert (target, source) == before


@pytest.mark.parametrize("alias,canonical", [
    ("朝食", "朝"), ("breakfast", "朝"),
    ("昼食", "昼"), ("lunch", "昼"),
    ("夕食", "夕"), ("dinner", "夕"),
])
def test_explicit_known_meal_aliases_match_without_inventing_sequence(alias, canonical):
    source = [["2026/10/01", alias, "Menu", "7", "0", ""]]
    target = [{"values": ["2026/10/01", canonical, "Menu", "12", "3", "4"]}]
    before = deepcopy(source)
    stats = bound_projection(target, source)
    assert target[0]["values"] == ["2026/10/01", canonical, "Menu", "7", "0", ""]
    assert stats["exact"] == 1 and stats["row_index"] == 0
    assert orders._canonicalize_sheet_daypart_rows(rows=source, fields=FIELDS)[0][1] == canonical
    assert source == before


@pytest.mark.parametrize("contradiction", ["daypart", "year"])
def test_carried_target_identity_fills_blanks_but_never_overrides_contradiction(contradiction):
    source = [["2026/10/01", "昼", "Menu", "7", "0", ""]]
    target = [{"values": ["", "", "Menu", "12", "3", "4"],
               "identity": ("2026/10/01", "昼", "Menu")}]
    stats = bound_projection(target, source)
    assert target[0]["values"] == ["", "", "Menu", "7", "0", ""]
    assert stats["exact"] == 1
    target[0]["values"][1 if contradiction == "daypart" else 0] = (
        "夕" if contradiction == "daypart" else "2025/10/01")
    before = deepcopy(target)
    with pytest.raises(fax.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        bound_projection(target, source)
    assert target == before


def test_reparse_authoritative_baseline_keeps_configured_bindings_and_uncertain_raw():
    selected = template()
    selected["fill_forward_roles"] = ["date", "daypart"]
    payload = structured_payload()
    table = payload["tables"][0]
    table["rows"] = [table["rows"][0],
        ["2026/10/01", "昼", "First", "7", "1", ""],
        ["", "", "Second", "0", "2", ""],
        ["", "", "Third", "?", "3", ""],
        ["", "", "Fourth", "", "4", ""]]
    payload["pages"][0]["tables"][0] = deepcopy(table)
    raw_rows = [list(row) for row in table["rows"][1:]]
    baseline = reparse_baseline([[*row[:3], "99", "98", "97"] for row in raw_rows], payload)
    before = deepcopy((baseline, selected))
    pairs = orders._resolved_quantity_source_target_pairs(payload, FIELDS, selected)
    assert pairs == [(3, 3), (4, 4), (5, 5)]
    projected, stats = orders._build_reparse_authoritative_quantity_baseline_rows(
        baseline=baseline, template=selected)
    assert projected == raw_rows
    assert stats["exact"] == 4 and stats["unmapped"] == 0
    assert [(issue["row_index"], issue["raw"]) for issue in
            orders._canonical_quantity_cell_issues(projected, FIELDS)] == [(2, "?")]
    assert (baseline, selected) == before


@pytest.mark.parametrize("returned_fields,resolved", [
    (["qty.regular_x", "qty.soft_x"], True),
    (["qty.regular_x"], False),
])
def test_latest_explicit_candidate_resolves_only_fields_actually_returned(returned_fields, resolved):
    old = [["2026/10/01", "昼", "Menu", "?", "?", ""]]
    baseline = reparse_baseline(old)
    candidate = [["2026/10/01", "昼", "Menu", "7", "0", ""]]
    before = deepcopy((baseline, candidate, old))
    merged, result = orders._validate_and_merge_reparse_full_table_candidate(
        baseline=baseline, candidate_rows=candidate,
        candidate_returned_row_indexes=[0],
        candidate_returned_field_map={0: set(returned_fields)},
        authoritative_quantity_rows=old)
    if resolved:
        assert result["error"] is None
        assert merged == candidate
        assert result["detail"]["quantity_change_count"] == 2
    else:
        assert merged == [] and result["error"] == "sheet_ocr_review_required"
        assert [(issue["field"], issue["raw"]) for issue in
                result["detail"]["cell_issues"]] == [("qty.soft_x", "?")]
    assert (baseline, candidate, old) == before


@pytest.mark.parametrize("foreign_count", [0, 2])
def test_authoritative_reparse_row_count_cannot_adopt_foreign_geometry(foreign_count):
    row = ["2026/10/01", "昼", "Menu", "7", "0", ""]
    baseline = reparse_baseline([row])
    authoritative = [deepcopy(row) for _ in range(foreign_count)]
    before = deepcopy((baseline, authoritative))
    merged, result = orders._validate_and_merge_reparse_full_table_candidate(
        baseline=baseline, candidate_rows=[deepcopy(row)],
        authoritative_quantity_rows=authoritative)
    assert merged == [] and result["error"] == "sheet_canonical_mismatch"
    assert (baseline, authoritative) == before


def test_merge_without_authoritative_rebase_preserves_unreturned_operator_cells():
    # Covers the merge contract used by clean saved drafts, not its ownership gate.
    baseline = reparse_baseline([["2026/10/01", "昼", "Menu", "12", "3", "4"]])
    candidate = [["2026/10/01", "昼", "Menu", "99", "98", "0"]]
    before = deepcopy(baseline)
    merged, result = orders._validate_and_merge_reparse_full_table_candidate(
        baseline=baseline, candidate_rows=candidate,
        candidate_returned_row_indexes=[0],
        candidate_returned_field_map={0: {"qty.mixer_x"}},
        authoritative_quantity_rows=None)
    assert result["error"] is None
    assert merged == [["2026/10/01", "昼", "Menu", "12", "3", "0"]]
    assert result["detail"]["authoritative_quantity_reset_count"] == 0
    assert result["detail"]["quantity_change_count"] == 1
    assert baseline == before
