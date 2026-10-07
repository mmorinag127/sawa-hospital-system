"""Synthetic selected-date bagging components, never customer/operator OCR proof."""
import json
from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
from uuid import uuid4

import fitz

from src.db import session_scope
from src.models.menu import MonthlyMenuEntry, MonthlyMenuItem, MenuMaster
from src.models.uploaded_pdf import UploadedPdf
from src.services import order_workflow_v2_service as workflow
from tests.helpers.ingest_original import (
    authored_sources, configure_delivery, reserve_uploaded_original,
    register_authored_original, authored_pipeline_provider, process_uploaded_original,
)


def accepted_bagging_order(monkeypatch, tmp_path, lines):
    day = date.fromisoformat(lines[0]["date"])
    facility = "bagging-component"
    configure_delivery(monkeypatch)
    path, _ = authored_sources(monkeypatch, tmp_path, facility_id=facility, menu_date=day)
    master_path = tmp_path / "authored-facilities.json"
    master = json.loads(master_path.read_text())
    configured = master["facilities"][0]
    columns = configured["fax_template"]["columns"][:3]
    for index, line in enumerate(lines, 3):
        columns.append({"index": index, "source_index": index, "role": "quantity",
                        "name": f"qty.{line['diet_type']}_{line['area_id']}",
                        "diet_type": line["diet_type"], "area_id": line["area_id"]})
    configured["fax_template"]["columns"] = columns
    configured["versions"][0]["facility"] = deepcopy({key: value for key, value in configured.items() if key != "versions"})
    master_path.write_text(json.dumps(master))
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 40), f"Synthetic component / {facility} / {day.isoformat()}")
        body = pdf.tobytes()
    from pathlib import Path
    Path(path).write_bytes(body)
    menu_name = lines[0]["menu_name"]
    with session_scope() as session:
        entry = session.get(MonthlyMenuEntry, "authored-physical-row")
        entry.name = menu_name
        entry.daypart = "breakfast" if lines[0]["daypart"] == "朝" else "lunch"
        entry.diet_type = lines[0]["diet_type"]
        entry.category = "主菜"
        # Explicit menu amounts; no unresolved default is supplied by a mock.
        master_id = str(uuid4())
        session.add(MenuMaster(id=master_id, name=menu_name, normalized_name=menu_name,
                               qty_per_serving=1, unit_type="g", category="主菜",
                               condiments=["ソース"] if lines[0]["bag_type"] == "condiment" else []))
        for diet in dict.fromkeys(line["diet_type"] for line in lines):
            if diet != entry.diet_type:
                session.add(MonthlyMenuEntry(id=str(uuid4()), monthly_menu_id=day.strftime("%Y-%m"),
                    menu_date=day, name=menu_name, daypart=entry.daypart, diet_type=diet, category="主菜",
                    facility_override=facility, slot_index=1))
            session.add(MonthlyMenuItem(id=str(uuid4()), monthly_menu_id=day.strftime("%Y-%m"),
                menu_master_id=master_id,
                name=menu_name, diet_type=diet, facility_override=facility,
                qty_per_serving=1, unit_type="g", category="主菜", daypart=entry.daypart,
                master_resolution_mode="manual"))
        upload_id, message = str(uuid4()), str(uuid4())
        session.add(UploadedPdf(id=upload_id, message_id=message, storage_uri=path,
            content_sha256=sha256(body).hexdigest(), original_filename="synthetic.pdf",
            received_at=datetime.combine(day, datetime.min.time()), facility_hint=facility,
            week_hint=f"{day:%Y-%m}@{day.isoformat()}~{day.isoformat()}", status="pending"))
    reserve_uploaded_original(upload_id)
    registered = register_authored_original(upload_id, effective_date=day)
    current, error = workflow.get_workflow_readonly(registered["id"])
    assert error is None, error
    context, error = workflow.confirm_context(order_id=registered["id"], facility_id=facility,
        week_start=day.isoformat(), week_end=day.isoformat(), expected_revision=current["revision"])
    assert error is None, error
    authored_pipeline_provider(monkeypatch, table_rows=[[
        day.strftime("%m/%d"), lines[0]["daypart"], menu_name,
        *[str(line["quantity_original"]) for line in lines]]])
    delivered = process_uploaded_original(upload_id)
    assert delivered["result"] == "business_done", delivered
    results, error = workflow.list_ocr_results(registered["id"])
    assert error is None, error
    current, error = workflow.get_workflow_readonly(registered["id"])
    assert error is None, error
    assert len(results["results"]) == 1
    selected, error = workflow.select_ocr_result(registered["id"], results["results"][0]["ocr_result_id"],
                                               expected_revision=current["revision"])
    assert error is None, error
    sheet = {"fields": ["date", "daypart", "menu", *[
        f"qty.{line['diet_type']}_{line['area_id']}" for line in lines]],
        "rows": [[day.isoformat(), lines[0]["daypart"], menu_name,
                  *[line["quantity_original"] for line in lines]]],
        "week_id": f"{day:%Y-%m}@{day.isoformat()}~{day.isoformat()}"}
    current, error = workflow.get_workflow_readonly(registered["id"])
    assert error is None, error
    saved, error = workflow.save_sheet(order_id=registered["id"], sheet=sheet,
                                      expected_revision=current["revision"])
    assert error is None, error
    from src.models.order import Order
    from src.models.order_sheet_draft import OrderSheetDraft
    with session_scope() as session:
        order = session.get(Order, registered["id"])
        draft = session.get(OrderSheetDraft, saved["saved_sheet"]["saved_sheet_id"])
        candidate = workflow._build_materialization_candidate_for_saved_sheet(session=session, order=order, saved_sheet=draft)
        resolution = workflow.candidate_menu_amount_resolution(order, candidate)
        assert resolution["status"] == "resolved", (resolution, candidate["lines"])
    bagging, error = workflow.run_bagging(registered["id"], expected_revision=saved["workflow"]["revision"])
    assert error is None, error
    from src.models.order_output_artifact import OrderBaggingResult
    from src.services.order_output_artifact_service import payload_digest
    with session_scope() as session:
        receipt = session.get(OrderBaggingResult, bagging["bagging_result"]["bagging_result_id"])
        assert receipt.payload_json == bagging["bagging_result"]
        assert receipt.payload_digest == payload_digest(receipt.payload_json)
        assert receipt.payload_json["materialization_digest"] == workflow._canonical_payload_digest(bagging["bagging_result"]["materialization_candidate"])
        assert workflow._canonical_payload_digest(receipt.payload_json["materialization_candidate"]) == receipt.payload_json["materialization_digest"]
    return registered
