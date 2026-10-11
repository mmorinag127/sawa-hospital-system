"""Explicit owned history inputs; manual evidence selection, real bagging/adoption.

The nullable quantity remains an empty source cell, not a zero or a fabricated
output line. Artifact IDs and timestamps are synthetic history test metadata.
"""

from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
import json

import fitz
from openpyxl import Workbook

from src.db import session_scope
from src.models.document import OrderDocument
from src.models.facility import Facility
from src.models.menu import MenuMaster, MonthlyMenu, MonthlyMenuEntry, MonthlyMenuItem
from src.models.order import Order
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_output_artifact import OrderBaggingResult, OrderOutputBundle
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.uploaded_pdf import UploadedPdf
from src.services import canonical_facility_lineage as lineage
from src.services import menu_service, order_form_service, order_service
from src.services import order_output_artifact_service as artifacts
from src.services import order_workflow_v2_service as workflow


def seed_history_inputs(engine, root, monkeypatch):
    day = date(2026, 2, 15)
    quantities = [0, None, 6, 9]
    names = [f"synthetic-menu-{index}" for index in range(4)]
    rows = [[day.isoformat(), "昼", "主菜", name, value, "", "", ""]
            for name, value in zip(names, quantities, strict=True)]
    source = root / "history-source.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "2月15日～2月21日"
    sheet["A2"] = "【発　注　連　絡　表】"
    sheet["D7"] = "献立"
    for cell, label in (("E7", "常食"), ("F7", "肉禁"), ("G7", "魚禁"), ("H7", "職員")):
        sheet[cell] = label
    for index, row in enumerate(rows, 11):
        for column, value in enumerate(row, 1):
            sheet.cell(index, column, value)
    workbook.save(source)
    workbook.close()
    family = "fax_layout_regular_forbidden_v1"
    columns = [{"index": i, "source_index": i, "role": role}
               for i, role in enumerate(("date", "daypart", "category", "menu_name"))]
    columns += [{"index": i, "source_index": i, "role": "quantity", "diet_type": diet, "area_id": "X"}
                for i, diet in enumerate(("regular", "no_meat", "no_fish", "staff"), 4)]
    facility = {
        "facility_id": "synthetic-facility", "facility_name": "Synthetic fixture facility",
        "areas": None, "fax_template_id": family,
        "order_form_source_workbook": source.name,
        "fax_template": {"template_id": family, "units": "normalized", "table_box": [0, 0, 1, 1],
                         "header_rows": 0, "columns": columns},
        "bag_types": [{"bag_type_id": "HISTORY-1000G", "label": "1000g", "max_qty": 1000, "unit": "g"}],
        "packaging_policy": {"split_key": ["facility", "date", "daypart", "menu_name", "diet_type", "area_id", "bag_type"]},
    }
    master = root / "history-facilities.json"
    master.write_text(json.dumps({"facilities": [{"facility_id": "synthetic-facility",
        "facility_name": "Synthetic fixture facility", "versions": [{"version_id": "history-February",
        "valid_from": "2026-02-01", "valid_to": "2026-02-28", "facility": facility}]}]}, ensure_ascii=False))
    output = root / "output"
    output.mkdir()
    monkeypatch.delenv("FACILITY_MASTER_URI", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(master))
    monkeypatch.setenv("ORDERS_OUTPUT_DIR", str(output))
    monkeypatch.setattr(order_form_service, "_FAX_SOURCE_TEMPLATE_DIR", root)
    monkeypatch.setattr(order_form_service, "_OUTPUT_DIR", output)
    monkeypatch.setattr(menu_service, "engine", engine)
    with session_scope() as session:
        session.add(Facility(id="synthetic-facility", name="Synthetic fixture facility"))
        session.flush()
        selected, _ = lineage.resolve(session, "synthetic-facility", day)
        _, projection = lineage.materialize(session, selected)
        template_id = projection.id
        session.add(MonthlyMenu(id="2026-02", month_start=date(2026, 2, 1)))
        session.flush()
        for index, name in enumerate(names):
            master_id = f"HISTORY-MASTER-{index}"
            session.add(MenuMaster(id=master_id, name=name, normalized_name=menu_service._normalize_menu_name(name),
                                  unit_type="g", qty_per_serving=100, category="主菜", daypart="昼"))
            session.flush()
            session.add(MonthlyMenuItem(id=f"HISTORY-ITEM-{index}", monthly_menu_id="2026-02",
                menu_master_id=master_id, name=name, diet_type="regular", category="主菜", daypart="昼",
                unit_type="g", qty_per_serving=100))
            session.add(MonthlyMenuEntry(id=f"HISTORY-ENTRY-{index}", monthly_menu_id="2026-02",
                menu_date=day, daypart="昼", category="主菜", diet_type="regular", name=name, slot_index=index))
        for owner in ("synthetic-order", "other-order"):
            pdf = root / f"{owner}.pdf"
            document = fitz.open()
            document.new_page().insert_text((50, 50),
                f"Synthetic fixture facility / 2026-02-15..21 / {owner}; quantities 0, blank, 6, 9")
            document.save(pdf)
            document.close()
            session.add(Order(id=owner, document_uri=str(pdf), message_id=owner,
                facility_code="synthetic-facility", week_code="2026-02@2026-02-15~2026-02-21",
                received_at=datetime(2026, 2, 15, 9), template_version_id=template_id, status="要確認"))
            session.flush()
            document_id = f"{owner}-document"
            session.add(OrderDocument(id=document_id, facility_code="synthetic-facility",
                week_code="2026-02@2026-02-15~2026-02-21", storage_uri=str(pdf), source_email_id=owner,
                received_at=datetime(2026, 2, 15, 9), status="done"))
            session.flush()
            order = session.get(Order, owner)
            order.current_document_id = document_id
            session.add(UploadedPdf(id=f"{owner}-pdf", message_id=owner, storage_uri=str(pdf),
                original_filename=pdf.name, content_sha256=sha256(pdf.read_bytes()).hexdigest(),
                received_at=datetime(2026, 2, 15, 9), page_count=1,
                current_document_id=document_id, current_order_id=owner))
            evidence_id = f"{owner}-evidence"
            evidence = {"tables": [{"rows": deepcopy(rows)}]}
            session.add(OrderOcrEvidenceRun(id=evidence_id, order_id=owner, template_version_id=template_id,
                schema_version="v2", producer_version="synthetic-history/v1", source="unit-fixture",
                status="ready", payload_json=evidence, artifact_digest=sha256(json.dumps(evidence).encode()).hexdigest()))
            session.flush()
            payload = {"base_evidence_run_id": evidence_id,
                "fields": ["date", "daypart", "category", "menu_name", "qty.regular_x", "qty.no_meat_x", "qty.no_fish_x", "qty.staff_x"],
                "rows": deepcopy(rows)}
            session.add(OrderSheetDraft(id=f"{owner}-sheet", order_id=owner, template_version_id=template_id,
                base_evidence_run_id=evidence_id, base_template_resolution_id=family,
                draft_sheet_json=payload, draft_state="saved"))
            session.flush()
            session.add(OrderWorkflowState(order_id=owner, state="sheet_saved", draft_id=f"{owner}-sheet",
                evidence_run_id=evidence_id, template_version_id=template_id, blockers_json=[],
                secondary_actions_json={workflow.WORKFLOW_V2_META_KEY: {
                    "facility_id": "synthetic-facility", "week_start": "2026-02-15", "week_end": "2026-02-21",
                    "template_version_id": template_id}}))
            session.flush()
            order_service._record_current_order_version(session, order, session.get(OrderDocument, document_id))
    for owner in ("synthetic-order", "other-order"):
        with session_scope() as session:
            revision = session.get(OrderWorkflowState, owner).revision
            original_sheet = deepcopy(session.get(OrderSheetDraft, f"{owner}-sheet").draft_sheet_json)
            original_evidence = deepcopy(session.get(OrderOcrEvidenceRun, f"{owner}-evidence").payload_json)
        original_files = {path: path.read_bytes() for path in (master, source, root / f"{owner}.pdf")}
        produced, error = workflow.run_bagging(owner, expected_revision=revision)
        assert error is None, error
        assert produced is not None
        with session_scope() as session:
            saved = session.get(OrderSheetDraft, f"{owner}-sheet")
            assert saved.draft_sheet_json == original_sheet
            assert session.get(OrderOcrEvidenceRun, f"{owner}-evidence").payload_json == original_evidence
            assert [row[4] for row in saved.draft_sheet_json["rows"]] == quantities
            bag = deepcopy(produced["bagging_result"])
            assert [(line["source_row_index"], line["quantity_original"])
                    for line in bag["materialization_candidate"]["lines"]] == [(0, 0), (2, 6), (3, 9)]
            assert bag["summary"]["total_quantity"] == 15
            # Retain original history IDs without replacing any generated receipt.
            bag["bagging_result_id"] = f"{owner}-bag"
            bag["quantities"] = deepcopy(quantities)
            artifacts.save_bagging_result_artifact(session, payload=bag)
            for index, key in enumerate(("old", "current", "newest")):
                bundle = workflow._build_output_bundle_payload(order=session.get(Order, owner), bagging_result=bag)
                bundle.update(output_bundle_id=f"{owner}-{key}", quantities=deepcopy(quantities),
                              file_path="fixture://must-not-leak")
                row = artifacts.save_output_bundle_artifact(session, payload=bundle)
                row.created_at = datetime(2026, 2, 15, 9, 0, index, 123456)
            state = session.get(OrderWorkflowState, owner)
            state.secondary_actions_json = {workflow.WORKFLOW_V2_META_KEY: {
                "output_bundle_id": f"{owner}-current", "bagging_result_id": bag["bagging_result_id"]}}
            session.delete(session.get(OrderOutputBundle, produced["output_bundle"]["output_bundle_id"]))
            session.flush()
            session.delete(session.get(OrderBaggingResult, produced["bagging_result"]["bagging_result_id"]))
        assert all(path.read_bytes() == content for path, content in original_files.items())
