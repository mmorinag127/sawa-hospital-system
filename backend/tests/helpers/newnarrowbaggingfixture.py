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
from src.services import order_workflow_v2_service as workflow, menu_service
from tests.helpers.ingest_original import (
    authored_sources, configure_delivery, reserve_uploaded_original,
    register_authored_original, authored_pipeline_provider, process_uploaded_original,
)


def accepted_bagging_order(monkeypatch, tmp_path, lines, *, invoice_template=None, menu_category="主菜", confirm=False, omit_invoice=False, menu_qty_per_serving=1, menu_unit_type="g", output_diet_types=None, accessory_masters=None):
    day = date.fromisoformat(lines[0]["date"])
    facility = f"bagging-component-{uuid4().hex}"
    configure_delivery(monkeypatch)
    path, _ = authored_sources(monkeypatch, tmp_path, facility_id=facility, menu_date=day)
    master_path = tmp_path / "authored-facilities.json"
    master = json.loads(master_path.read_text())
    configured = master["facilities"][0]
    authored_areas = {
        "2F": {"area_id": "2F", "name": "Synthetic component second floor"},
        "3F": {"area_id": "3F", "name": "Synthetic component third floor"},
    }
    columns = configured["fax_template"]["columns"][:3]
    quantities = list(dict.fromkeys((line["diet_type"], line["area_id"]) for line in lines))
    configured["areas"] = []
    for area in dict.fromkeys(area for _, area in quantities):
        if area in {None, "", "X"}:
            continue
        assert area in authored_areas, ("explicit synthetic area definition required", area)
        configured["areas"].append(deepcopy(authored_areas[area]))
    for index, (diet, area) in enumerate(quantities, 3):
        columns.append({"index": index, "source_index": index, "role": "quantity",
                        "name": f"qty.{diet}_{area}",
                        "diet_type": diet, "area_id": area})
        if output_diet_types and diet in output_diet_types:
            columns[-1]["output_diet_type"] = output_diet_types[diet]
    configured["fax_template"]["columns"] = columns
    if invoice_template is None:
        # Explicit authored tabular component contract; not a production fallback.
        invoice_template = {"columns": [
            {"name": "日付", "source": "date"},
            {"name": "区分", "source": "daypart"},
            {"name": "献立", "source": "menu_display"},
            *[{"name": f"qty.{diet}_{area}", "source": "quantity", "diet_type": diet,
               "area_id": area} for diet, area in quantities]], "include_menu_name": True}
    if invoice_template is not None:
        configured["invoice_template"] = deepcopy(invoice_template)
    if omit_invoice:
        configured.pop("invoice_template", None)
    configured["versions"][0]["facility"] = deepcopy({key: value for key, value in configured.items() if key != "versions"})
    master_path.write_text(json.dumps(master))
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 40), f"Synthetic component / {facility} / {day.isoformat()}")
        y = 75
        for line in lines:
            if y > 780:
                page = pdf.new_page()
                y = 40
            page.insert_text((30, y),
                f"{line['date']} {line['daypart']} {line['menu_name']} "
                f"{line['diet_type']} / {line['area_id']} / {line['quantity_original']}",
                fontname="japan", fontsize=9)
            y += 25
        body = pdf.tobytes()
    from pathlib import Path
    Path(path).write_bytes(body)
    groups = {}
    for line in lines:
        assert date.fromisoformat(line["date"]) == day
        groups.setdefault((line["daypart"], line["menu_name"]), []).append(line)
    with session_scope() as session:
        for name, values in (accessory_masters or {}).items():
            session.add(MenuMaster(id=str(uuid4()), name=name, normalized_name=menu_service._normalize_menu_name(name),
                qty_per_serving=values["qty_per_serving"], unit_type=values["unit_type"], category="添え"))
        entry = session.get(MonthlyMenuEntry, "authored-physical-row")
        session.delete(entry)
        slots_by_daypart = {}
        for (daypart, menu_name), group in groups.items():
            slot = slots_by_daypart.get(daypart, 0)
            slots_by_daypart[daypart] = slot + 1
            canonical_daypart = {"朝": "breakfast", "昼": "lunch", "夕": "supper"}[daypart]
            master_id = str(uuid4())
            session.add(MenuMaster(id=master_id, name=menu_name, normalized_name=menu_service._normalize_menu_name(menu_name),
                qty_per_serving=menu_qty_per_serving, unit_type=menu_unit_type, category=menu_category,
                condiments=["ソース"] if group[0]["bag_type"] == "condiment" else []))
            # Physical regular identity is authored separately from requested
            # logical quantities. It never adds a regular order column/value.
            for diet in dict.fromkeys(["regular", *[line["diet_type"] for line in group]]):
                session.add(MonthlyMenuEntry(id=str(uuid4()), monthly_menu_id=day.strftime("%Y-%m"),
                    menu_date=day, name=menu_name, daypart=canonical_daypart, diet_type=diet,
                    category=menu_category, facility_override=facility, slot_index=slot))
                session.add(MonthlyMenuItem(id=str(uuid4()), monthly_menu_id=day.strftime("%Y-%m"),
                    menu_master_id=master_id, name=menu_name, diet_type=diet, facility_override=facility,
                    qty_per_serving=menu_qty_per_serving, unit_type=menu_unit_type, category=menu_category, daypart=canonical_daypart,
                    master_resolution_mode="manual"))
        upload_id, message = str(uuid4()), str(uuid4())
        session.add(UploadedPdf(id=upload_id, message_id=message, storage_uri=path,
            content_sha256=sha256(body).hexdigest(), original_filename="synthetic.pdf",
            received_at=datetime.combine(day, datetime.min.time()), facility_hint=facility,
            week_hint=f"{day:%Y-%m}@{day.isoformat()}~{day.isoformat()}", status="pending"))
    reserve_uploaded_original(upload_id, effective_date=day)
    registered = register_authored_original(upload_id, effective_date=day)
    current, error = workflow.get_workflow_readonly(registered["id"])
    assert error is None, error
    context, error = workflow.confirm_context(order_id=registered["id"], facility_id=facility,
        week_start=day.isoformat(), week_end=day.isoformat(), expected_revision=current["revision"])
    assert error is None, error
    rows = []
    for (daypart, menu_name), group in groups.items():
        amounts = {(line["diet_type"], line["area_id"]): line["quantity_original"] for line in group}
        rows.append([day.strftime("%m/%d"), daypart, menu_name,
                     *[str(amounts[key]) if key in amounts else "" for key in quantities]])
    authored_pipeline_provider(monkeypatch, table_rows=rows)
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
        f"qty.{diet}_{area}" for diet, area in quantities]],
        "rows": [[day.isoformat(), row[1], row[2],
                  *[int(value) if value != "" else None for value in row[3:]]] for row in rows],
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
    if confirm:
        confirmed, error = workflow.final_confirm(registered["id"], expected_revision=bagging["workflow"]["revision"])
        assert error is None, error
        assert confirmed is not None
    return registered
