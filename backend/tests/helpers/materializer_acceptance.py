"""Explicit June synthetic saved-sheet acceptance, not recognizer evidence."""
import json
from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256

import fitz

from src.db import session_scope
from src.models.facility import Facility
from src.models.menu import MonthlyMenu, MonthlyMenuEntry, MonthlyMenuItem, MenuMaster
from src.models.order import Order
from src.models.uploaded_pdf import UploadedPdf
from src.services import canonical_facility_lineage as lineage, config_service
from src.services import menu_service, order_workflow_v2_service as workflow
from tests.helpers.ingest_original import (
    configure_delivery, reserve_uploaded_original, register_authored_original,
    authored_pipeline_provider, process_uploaded_original,
)


def accepted_materializer(monkeypatch, tmp_path, names, quantities, *, accessory_masters=None):
    # All values here are authored test inputs, not inferred customer settings.
    period = "2026-06@2026-06-21~2026-06-27"
    facility = {
        "facility_id": "FAC00003", "facility_name": "Synthetic June materializer fixture",
        "areas": [{"area_id": "2F", "name": "Synthetic materializer area"}],
        "fax_template_id": "materializer-june",
        "fax_template": {"template_id": "materializer-june", "page": 1, "columns": [
            {"index": 0, "source_index": 0, "role": "date", "name": "date"},
            {"index": 1, "source_index": 1, "role": "daypart", "name": "daypart"},
            {"index": 2, "source_index": 2, "role": "menu_name", "name": "menu"},
            {"index": 3, "source_index": 3, "role": "quantity", "name": "qty.regular_2F",
             "diet_type": "regular", "area_id": "2F"},
        ]},
        "invoice_template": {"columns": [
            {"name": "date", "source": "date"}, {"name": "menu", "source": "menu_display"},
            {"name": "quantity", "source": "quantity", "diet_type": "regular", "area_id": "2F"},
        ], "include_menu_name": True},
    }
    authored = {"version_id": "authored-original", "valid_from": "2026-06-21",
                "valid_to": "2026-06-27", "facility": deepcopy(facility)}
    master = tmp_path / "materializer-facilities.json"
    master.write_text(json.dumps({"facilities": [{**facility, "versions": [authored]}]}))
    policy = tmp_path / "materializer-policy.json"
    policy.write_text(json.dumps({"ocr_retry_limit": 1, "quantity_rules": {"zero_as_empty": False},
                                  "backlog_policy": {"skip_ocr_for_stale": False}}))
    monkeypatch.delenv("FACILITY_MASTER_URI", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(master))
    monkeypatch.setattr(config_service, "INGEST_POLICY_PATH", policy)
    monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path / "owned-artifacts"))
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 40), "Synthetic FAC00003 / 2026-06-21 through 2026-06-27")
        for index, (name, quantity) in enumerate(zip(names, quantities, strict=True)):
            page.insert_text((40, 80 + index * 30),
                f"2026-06-21 昼 {name} regular / 2F {quantity}", fontname="japan", fontsize=10)
        body = pdf.tobytes()
    path = tmp_path / "materializer.pdf"
    path.write_bytes(body)
    with session_scope() as session:
        session.add(Facility(id="FAC00003", name=facility["facility_name"]))
        session.add(MonthlyMenu(id="2026-06", month_start=date(2026, 6, 1)))
        session.flush()
        for index, (name, values) in enumerate((accessory_masters or {}).items()):
            session.add(MenuMaster(id=f"materializer-accessory-{index}", name=name,
                normalized_name=menu_service._normalize_menu_name(name),
                qty_per_serving=values["qty_per_serving"], unit_type=values["unit_type"], category="添え"))
        for slot, name in enumerate(names):
            master_id = f"materializer-menu-{slot}"
            session.add(MenuMaster(id=master_id, name=name,
                normalized_name=menu_service._normalize_menu_name(name),
                qty_per_serving=1, unit_type="g", category="主菜"))
            session.add(MonthlyMenuEntry(id=f"materializer-entry-{slot}", monthly_menu_id="2026-06",
                menu_date=date(2026, 6, 21), daypart="lunch", name=name,
                slot_index=slot, diet_type="regular", facility_override="FAC00003", category="主菜"))
            session.add(MonthlyMenuItem(id=f"materializer-item-{slot}", monthly_menu_id="2026-06",
                menu_master_id=master_id, name=name, daypart="lunch", diet_type="regular",
                facility_override="FAC00003", qty_per_serving=1, unit_type="g",
                category="主菜", master_resolution_mode="manual"))
    with session_scope() as session:
        selected, _ = lineage.resolve(session, "FAC00003", date(2026, 6, 21))
        _, projection = lineage.materialize(session, selected)
        assert lineage.verify_projection(projection)["authored_version"] == authored
        projection_id = projection.id
    captured = config_service.capture_authored_processing_config(
        "FAC00003", selected_authored_version=authored)
    assert captured["facility"]["facility_id"] == "FAC00003"
    configure_delivery(monkeypatch)
    with session_scope() as session:
        # Seed only the explicit order identity, never ready evidence or receipts.
        session.add(Order(id="ORD-WF2", facility_code="FAC00003", week_code=period,
            document_uri=str(path),
            message_id="materializer-message", received_at=datetime(2026, 6, 21),
            template_version_id=projection_id))
        session.flush()
        session.add(UploadedPdf(id="materializer-upload", message_id="materializer-message",
            storage_uri=str(path), original_filename="materializer.pdf",
            content_sha256=sha256(body).hexdigest(), received_at=datetime(2026, 6, 21),
            facility_hint="FAC00003", week_hint=period, status="pending"))
    reserve_uploaded_original("materializer-upload", effective_date=date(2026, 6, 21))
    registered = register_authored_original("materializer-upload", effective_date=date(2026, 6, 21))
    assert registered["id"] == "ORD-WF2"
    sheet = {"fields": ["date", "daypart", "menu", "qty.regular_2F"],
             "rows": [["2026-06-21", "昼", name, quantity] for name, quantity in zip(names, quantities, strict=True)],
             "week_id": period}
    current, error = workflow.get_workflow_readonly("ORD-WF2")
    assert error is None, error
    context, error = workflow.confirm_context(order_id="ORD-WF2", facility_id="FAC00003",
        week_start="2026-06-21", week_end="2026-06-27", expected_revision=current["revision"])
    assert error is None, error
    # Only the external recognizer component is doubled. Ownership, execution,
    # first-pass evidence publication and adoption use the real services.
    authored_pipeline_provider(monkeypatch, table_rows=[
        ["06/21", "昼", name, str(quantity)] for name, quantity in zip(names, quantities, strict=True)])
    delivered = process_uploaded_original("materializer-upload")
    assert delivered["result"] == "business_done", delivered
    results, error = workflow.list_ocr_results("ORD-WF2")
    assert error is None, error
    assert len(results["results"]) == 1
    current, error = workflow.get_workflow_readonly("ORD-WF2")
    assert error is None, error
    selected, error = workflow.select_ocr_result("ORD-WF2", results["results"][0]["ocr_result_id"],
                                                expected_revision=current["revision"])
    assert error is None, error
    current, error = workflow.get_workflow_readonly("ORD-WF2")
    assert error is None, error
    saved, error = workflow.save_sheet(order_id="ORD-WF2", sheet=sheet,
                                      expected_revision=current["revision"])
    assert error is None, error
    bagging, error = workflow.run_bagging("ORD-WF2", expected_revision=saved["workflow"]["revision"])
    assert error is None, error
    return saved["saved_sheet"]["saved_sheet_id"], bagging
