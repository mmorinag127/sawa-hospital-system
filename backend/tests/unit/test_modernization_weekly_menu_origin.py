"""Owned component proof, not HTTP/auth, actual OCR, or staging proof."""

from copy import deepcopy
from datetime import date
from uuid import uuid4

import pytest

from src.db import session_scope
from src.models.menu import MonthlyMenuEntry
from src.models.order import Order
from src.services import canonical_facility_lineage as lineage, order_service
from tests.unit.test_h2_operator_repair_revision import _make_repair_order


WEEKLY_SOURCES = (
    "weekly_menu", "weekly_menu+ocr_payload", "weekly_menu+payload_row",
    "weekly_menu_skeleton", "weekly_menu_blocked",
    "hakodate_weekly_menu_template", "forced_weekly_menu",
)
NONWEEKLY_SOURCES = (
    None, "", "unknown", "weekly_menu_typo", "hakodate_weekly_menu_templat",
    "forced_weekly_men", "hakodate_ocr_evidence_sheet", "ocr_table",
    "edited_sheet", "draft_sheet",
)


@pytest.mark.parametrize("source", WEEKLY_SOURCES)
def test_explicit_weekly_origins_and_trace(source):
    assert order_service._source_uses_weekly_menu_shell(source) is True
    assert order_service._source_uses_weekly_menu_shell(f" {source} ") is True
    trace = order_service._build_sheet_trace_rows(
        rows=[{"values": ["10/07", "昼", "own"]}],
        fields=["date_mmdd", "daypart", "menu"], quantity_index={},
        source=source, mapped_mode="none",
    )
    assert trace == [["weekly_menu"] * 3]


@pytest.mark.parametrize("source", NONWEEKLY_SOURCES)
def test_nonweekly_origins_are_not_menu_authority(source):
    assert order_service._source_uses_weekly_menu_shell(source) is False
    if source is not None:
        trace = order_service._build_sheet_trace_rows(
            rows=[{"values": ["10/07", "昼", "own"]}],
            fields=["date_mmdd", "daypart", "menu"], quantity_index={},
            source=source, mapped_mode="none",
        )
        assert trace == [["ocr_table" if source == "ocr_table" else "none"] * 3]


@pytest.fixture
def captured_base(isolated_formal_schema, monkeypatch, tmp_path):
    order_id, _, configured = _make_repair_order(
        monkeypatch, tmp_path,
        extra_columns=[{"column_id": "remarks", "index": 4,
                        "source_index": 4, "role": "remarks"}],
    )
    with session_scope() as session:
        for slot in (1, 2):
            session.add(MonthlyMenuEntry(
                id=str(uuid4()), monthly_menu_id="2026-10",
                menu_date=date(2026, 10, 7), daypart="昼", slot_index=slot,
                name=f"own-{slot}", category=("副菜1", "副菜2")[slot - 1],
                diet_type="regular",
            ))
        projection = lineage.read_projection(
            session, session.get(Order, order_id).template_version_id,
        )
        assert lineage.verify_projection(projection)["authored_version"] == configured["selected_authored_version"]
    captured = order_service._capture_synchronous_cache_write_input(order_id)
    sheet, error = order_service._build_hakodate_weekly_menu_base_sheet(
        order_id, synchronous_write_input=captured,
    )
    assert error is None
    assert sheet["source"] == "hakodate_weekly_menu_template"
    assert len(sheet["rows"]) == 3
    return sheet


@pytest.mark.parametrize("source", (
    "hakodate_weekly_menu_template", "forced_weekly_menu",
    "hakodate_ocr_evidence_sheet", "edited_sheet", "draft_sheet",
))
def test_real_captured_shell_merge_preserves_raw_operator_values(captured_base, source):
    fresh = deepcopy(captured_base)
    fresh["source"] = source
    current = deepcopy(captured_base)
    current["source"] = "edited_sheet"
    fields = current["fields"]
    shell = [fields.index(field) for field in ("date_mmdd", "daypart", "menu")]
    quantity, remarks = fields.index("qty.regular_x"), fields.index("remarks")
    for row, value, note in zip(current["rows"], (0, None, ""), (None, 0, "")):
        for index, text in zip(shell, ("operator-date", "operator-daypart", "operator-menu")):
            row[index] = text
        row[quantity], row[remarks] = value, note
    before_current, before_fresh = deepcopy(current), deepcopy(fresh)
    weekly = source in {"hakodate_weekly_menu_template", "forced_weekly_menu"}
    assert order_service._fresh_sheet_has_authoritative_weekly_shell(fresh) is weekly
    merged = order_service._merge_current_draft_quantity_values_into_semantic_sheet(
        current, fresh, copy_shared_quantity_values=True,
    )
    expected = deepcopy(current["rows"])
    if weekly:
        for row, fresh_row in zip(expected, fresh["rows"]):
            for index in shell:
                row[index] = fresh_row[index]
    assert merged["rows"] == expected
    assert [type(row[quantity]) for row in merged["rows"]] == [int, type(None), str]
    assert [type(row[remarks]) for row in merged["rows"]] == [type(None), int, str]
    assert merged["physical_menu_row_ids"] == current["physical_menu_row_ids"]
    assert merged["row_ids"] == current["row_ids"]
    assert merged["physical_menu_row_count"] == 3
    assert current == before_current and fresh == before_fresh


@pytest.mark.parametrize("fault", ("missing", "duplicate"))
def test_weekly_origin_does_not_rescue_invalid_physical_identity(captured_base, fault):
    current, fresh = deepcopy(captured_base), deepcopy(captured_base)
    if fault == "missing":
        del current["physical_menu_row_ids"][1]
    else:
        current["physical_menu_row_ids"][1] = current["physical_menu_row_ids"][0]
    before = deepcopy(current)
    with pytest.raises(ValueError, match=f"^saved_draft_row_identity_{fault}$"):
        order_service._merge_current_draft_quantity_values_into_semantic_sheet(
            current, fresh, copy_shared_quantity_values=True,
        )
    assert current == before and fresh == captured_base
