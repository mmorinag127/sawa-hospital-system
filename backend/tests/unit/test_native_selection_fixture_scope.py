"""Component input refusal, not OCR accuracy or a live upload scenario."""
from datetime import datetime
from pathlib import Path

import pytest

from src.db import get_engine
from src.services.canonical_facility_store import read_snapshot
from tests.helpers.native_selection import select_authored_native_result
from tests.helpers.owned_order import authored_layout_registration, owned_registration  # noqa: F401
from tests.integration.test_ocr_sheet_history import history_registration, selected_history_order  # noqa: F401
from tests.unit.test_disabled_facility_columns import _snapshot


@pytest.mark.parametrize("field", ["note", "remarks"])
def test_declared_note_alias_preserves_target_and_raw_input(field):
    from copy import deepcopy
    from tests.helpers.authored_cell_values import declared_field
    target = {"role": "note", "field": field, "sheet_cell": "F11"}
    original = deepcopy(target)
    values = {"qty.regular_x": "7?", "remarks": "007 備考"}
    assert values[declared_field(target, list(values))] == "007 備考"
    assert target == original
    assert values == {"qty.regular_x": "7?", "remarks": "007 備考"}


@pytest.mark.parametrize("target", [
    {"role": "quantity", "field": "remarks"},
    {"role": "note", "field": "qty.regular_x"},
    {"role": "aux", "field": "aux.notes"},
])
def test_declared_note_alias_never_repairs_an_incompatible_role(target):
    from copy import deepcopy
    from tests.helpers.authored_cell_values import declared_field
    original = deepcopy(target)
    with pytest.raises(ValueError, match="hakodate_target_role_"):
        declared_field(target, ["qty.regular_x", "remarks"])
    assert target == original


def test_declared_note_exact_field_is_preserved():
    from tests.helpers.authored_cell_values import declared_field
    assert declared_field({"role": "note", "field": "note"}, ["note"]) == "note"


@pytest.mark.parametrize("field", ["note", "remarks"])
@pytest.mark.parametrize("raw", [("same", "same"), ("007", "different")])
def test_declared_note_aliases_are_rejected_even_when_raw_values_match(field, raw):
    from tests.helpers.authored_cell_values import declared_field
    values = dict(zip(["note", "remarks"], raw))
    with pytest.raises(AssertionError, match="ambiguous declared note aliases"):
        declared_field({"role": "note", "field": field}, list(values))
    assert tuple(values.values()) == raw


@pytest.mark.parametrize("field", ["note", "remarks"])
def test_unused_declared_note_is_rejected_even_when_quantity_is_used(field):
    from tests.helpers.authored_cell_values import assert_declared_cells_used
    quantity = ("2025-01-01", "朝", "Menu A", "qty.regular_x")
    note = ("2025-01-01", "朝", "Menu A", field)
    with pytest.raises(AssertionError, match="authored quantity/note cells"):
        assert_declared_cells_used({quantity, note}, {quantity})


def test_unknown_declared_note_field_is_not_guessed():
    from tests.helpers.authored_cell_values import declared_field
    with pytest.raises(AssertionError, match="no declared input field"):
        declared_field({"role": "note", "field": "note"}, ["unrelated"])


@pytest.mark.parametrize("raw", [("same", "same"), ("007", "different")])
def test_native_entry_refuses_two_note_aliases_before_any_order_or_artifact_access(monkeypatch, tmp_path, raw):
    from tests.helpers.native_selection import produce_authored_native_result
    columns = [{"name": name, "role": "note", "source_index": index}
               for index, name in enumerate(["note", "remarks"])]
    rows = [list(raw)]
    with pytest.raises(AssertionError, match="ambiguous declared note aliases"):
        produce_authored_native_result(monkeypatch, order_id="must-not-be-read",
            table_rows=rows, columns=columns, unlisted_cells="", artifact_root=tmp_path / "artifacts")
    assert rows == [list(raw)]
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.parametrize("ocr_rows,lines", [
    (None, [{"quantity_original": 0}]),
    ([], [{"quantity_original": 7}]),
    ([], []),
    ([["01/01", "朝", "Menu A", "7"]], []),
])
def test_explicit_blank_mode_never_discards_any_supplied_input(monkeypatch, tmp_path, ocr_rows, lines):
    calls = []

    def forbidden_registration(**inputs):
        calls.append(inputs)
        raise AssertionError("invalid mode must be refused before registration")

    register = selected_history_order.__wrapped__(forbidden_registration, monkeypatch, tmp_path)
    with pytest.raises(AssertionError, match="blank source cannot replace"):
        register(ocr_rows=ocr_rows, blank_source=True, lines=lines)
    assert calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("rows,reason", [
    ([["02/15", "朝", "Menu A", "9"], ["02/15", "朝", "Menu A", "8"]], "duplicate authored cell identity"),
    ([["02/15", "朝", "Menu A", "9"], ["02/15", "朝食", "Menu A", "8"]], "duplicate authored cell identity"),
    ([["02/15", "朝", "Menu A", "9"], ["02/15", "朝", "Ｍｅｎｕ A", "8"]], "duplicate authored cell identity"),
    ([["02/15", "61", "Menu A", "9"]], "authored daypart must have a canonical identity"),
    ([["02/01", "朝", "Menu A", "9"]], "resolve uniquely inside the explicit period"),
])
def test_invalid_native_authored_cells_are_refused_before_queue_or_source_changes(
    owned_registration, monkeypatch, tmp_path, rows, reason,
):
    order = owned_registration(message_id="native-fixture-scope", facility_id="AUTHORED-NATIVE-SCOPE",
        received_at=datetime(2026, 2, 15, 9), week="2026-02@2026-02-15~2026-02-21",
        effective_date="2026-02-15", lines=[])
    columns = [
        {"source_index": 0, "name": "date_mmdd", "role": "date"},
        {"source_index": 1, "name": "daypart", "role": "daypart"},
        {"source_index": 2, "name": "menu", "role": "menu_name"},
        {"source_index": 3, "name": "qty.regular_2f", "role": "quantity"},
    ]
    before = _snapshot(get_engine())
    pdf = Path(order["document"])
    original, revision = pdf.read_bytes(), read_snapshot().revision
    with pytest.raises(AssertionError, match=reason):
        select_authored_native_result(monkeypatch, order_id=order["id"], table_rows=rows,
            columns=columns, unlisted_cells="", artifact_root=tmp_path / "artifacts")
    assert _snapshot(get_engine()) == before
    assert pdf.read_bytes() == original
    assert read_snapshot().revision == revision
    assert not (tmp_path / "artifacts").exists()


def test_unmapped_line_quantity_cannot_be_silently_converted_to_blank(selected_history_order, monkeypatch):
    from src.db import session_scope
    from src.models.order import OrderLine
    from src.services import order_workflow_v2_service as workflow

    def forbidden(*args, **kwargs):
        pytest.fail("An unmapped source quantity must be refused before native production")

    monkeypatch.setattr(workflow, "mark_ocr_run_queued", forbidden)
    with pytest.raises(AssertionError, match="no declared physical column"):
        selected_history_order(message_id="native-unmapped-line", facility_id="FAC00001",
            received_at=datetime(2026, 2, 15, 9), week="2026-02@2026-02-15~2026-02-21",
            effective_date="2026-02-15", lines=[{"date": "2026-02-15", "daypart": "朝",
                "menu_name": "Menu A", "diet_type": "regular", "area_id": "UNDECLARED",
                "quantity_original": 123}])
    with session_scope() as session:
        line = session.query(OrderLine).filter(OrderLine.area_id == "UNDECLARED").one()
        assert line.quantity_original == 123
