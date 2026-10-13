"""March stored-order components with real owned OCR selection and sheet saves.

HistoryCase supplies capture, queue/claim, producer, CAS HTTP and read auditing.
Only the external recognizer and rendered provider files are component doubles.
"""
from datetime import date, datetime
import json

import pytest

from src.services import draft_sheet_service, order_service, order_current_state_service
from tests.contract.auth_support import registered_hospital_operator
from tests.contract.test_orders_ocr_sheet_history_api import HistoryCase, _line


PERIOD = "2026-03@2026-03-22~2026-03-28"


@pytest.fixture
def authored_sheet_case(isolated_formal_schema, tmp_path, monkeypatch):
    with registered_hospital_operator(monkeypatch) as actor:
        cases = []

        def create(*, quantity, area="2F", cell_values=None, include_remarks=True, produce=True):
            assert not cases, "one owned source/order per case"
            fields = ["date_mmdd", "daypart", "menu", f"qty.regular_{area.lower()}", "remarks"]
            headers = ["日付", "区分", "メニュー", "常食2F" if area == "2F" else "常食", "備考"]
            roles = ["date", "daypart", "menu_name", "quantity", "note"]
            if not include_remarks:
                fields, headers, roles = fields[:-1], headers[:-1], roles[:-1]
            columns = [{"index": i, "source_index": i, "name": field, "header": header, "role": role}
                       for i, (field, header, role) in enumerate(zip(fields, headers, roles, strict=True))]
            columns[3].update(diet_type="regular", area_id=area)
            case = HistoryCase(isolated_formal_schema, tmp_path, monkeypatch, actor,
                week=PERIOD, period=PERIOD, received=datetime(2026, 3, 22, 9),
                lines=[_line("2026-03-22", "Menu A", quantity, daypart="朝", area=area)],
                columns=columns, menu_entries=[{
                    "id": "march-22-breakfast-menu-a", "monthly_menu_id": "2026-03",
                    "menu_date": date(2026, 3, 22), "daypart": "朝", "name": "Menu A",
                    "slot_index": 0, "diet_type": "regular",
                }])
            cases.append(case)
            if produce:
                case.selected_sheet = case.produce(cell_values=str(quantity) if cell_values is None else cell_values)["sheet"]
            return case

        yield create
        for case in cases:
            (tmp_path / "authored-sheet-observations.json").write_text(
                json.dumps(case.observations, indent=2), encoding="utf-8")


def prepare_rerun(case, *, cell_values="9"):
    """Real queue/claim and capture; only rendering/recognition are component doubles."""
    calls = case.prepare_provider(cell_values=cell_values)
    assert order_service.get_latest_ocr_evidence_run(case.id, backfill_from_cache=False) is None
    return calls


def save_draft(case, sheet):
    case.save(sheet)
    draft = draft_sheet_service.get_selected_sheet_draft(case.id)
    assert draft is not None and draft["draft_sheet_json"] == sheet
    assert draft["base_evidence_run_id"] == case.evidence
    return draft


def publish_current(case):
    current = order_service.get_current_sheet_context(case.id)
    assert current is not None
    order_current_state_service.persist_current_state(
        order_id=case.id, state_json=current, draft_id=current["draft_id"],
        evidence_run_id=current["base_evidence_run_id"], template_version_id=current["template_version_id"],
    )
    return order_current_state_service.get_current_state_payload(case.id)
