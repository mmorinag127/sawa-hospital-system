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
