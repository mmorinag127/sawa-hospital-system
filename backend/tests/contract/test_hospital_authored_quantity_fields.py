"""Authored quantity identities survive provider evidence and sheet selection."""
import pytest
from tests.contract.test_orders_ocr_sheet_history_api import history_case
from tests.contract.auth_support import hospital_operator
from src.services import order_service


@pytest.mark.parametrize("diet", ["unknown", "placeholder"])
@pytest.mark.parametrize("value", ["20", "0", ""])
def test_selected_evidence_preserves_declared_field_and_neighbor_quantities(history_case, monkeypatch, diet, value):
    from tests.contract import test_orders_ocr_sheet_history_api as fixture_module
    provider=fixture_module._provider_output_files
    def recognized(workspace, *, records, regions, sheet_values):
        for record in records:
            text=value if record['role']=='quantity' else ''
            record.update(ocr_text=text,recognizer_raw_text=text,recognizer_accepted_candidate={'normalized_digits':text})
            sheet_values[record['sheet_cell']]=text
        return provider(workspace,records=records,regions=regions,sheet_values=sheet_values)
    monkeypatch.setattr(fixture_module,'_provider_output_files',recognized)
    fields=["date_mmdd","daypart","menu","qty.regular_x",f"qty.{diet}_x","remarks"]
    roles=["date","daypart","menu_name","quantity","quantity","note"]
    columns=[{"index":i,"source_index":i,"name":field,"header":field,"role":role} for i,(field,role) in enumerate(zip(fields,roles))]
    columns[3].update(diet_type="regular",area_id="X")
    columns[4].update(diet_type=diet,area_id="X")
    case=history_case(columns=columns, lines=[])
    source=case.produce(cell_values=value)
    assert source["blockers"]==[]
    assert source["sheet"]["fields"]==fields
    assert source["sheet"]["rows"][0][3:5]==[value,value]
    assert order_service._normalize_hakodate_sheet_field(f"qty.{diet}_x")==f"qty.{diet}_x"
    import json
    from src.db import session_scope
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    declared=json.loads(case.source.read_text())["facilities"][0]["versions"][0]["facility"]
    other="placeholder" if diet=="unknown" else "unknown"
    declared["fax_template"]["columns"][4].update(name=f"qty.{other}_x",diet_type=other)
    with session_scope() as session:
        payload=session.get(OrderOcrEvidenceRun,case.evidence).payload_json
    blocked=order_service._build_hakodate_evidence_assignment_from_payload(order_id=case.id,facility_id=case.facility,template_id=declared["fax_template_id"],payload=payload,facility_config=declared)
    assert "hakodate_target_field_unmapped" in blocked["blockers"]
    assert blocked["assignments"]==[]


def test_unknown_and_placeholder_targets_remain_distinct():
    assert order_service._hakodate_target_cell_field_candidates({"field":"qty.unknown_x"})==["qty.unknown_x"]
    assert order_service._hakodate_target_cell_field_candidates({"field":"qty.placeholder_x"})==["qty.placeholder_x"]
