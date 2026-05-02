from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from src.db import Base, engine, session_scope
from src.models.document import OrderDocument
from src.models.facility import Facility, FacilityConfig
from src.models.ingest_job import IngestJob
from src.models.ocr_job import OcrJob
from src.models.order import Order, OrderLine
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.output import Bag, ManufacturingAggregateRow
from src.models.uploaded_pdf import UploadedPdf, UploadedPdfAttempt
from src.services.order_operational_cleanup_service import (
    CleanupScope,
    apply_order_cleanup,
    build_order_cleanup_plan,
    export_order_pdfs_for_cleanup,
)


Base.metadata.create_all(bind=engine)


def _id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:12]}"


def _create_order_graph(*, received_at: datetime, pdf_path: Path) -> tuple[str, str]:
    order_id = _id("ORDclean")
    message_id = _id("msg-clean-")
    evidence_id = _id("OEV")
    uploaded_id = _id("UPL")
    with session_scope() as session:
        session.add(
            Order(
                id=order_id,
                facility_code="FAC_KEEP",
                week_code="2026-05@2026-05-01~2026-05-02",
                status="要確認",
                document_uri=str(pdf_path),
                message_id=message_id,
                received_at=received_at,
            )
        )
        session.add(
            OrderDocument(
                id=_id("DOC"),
                facility_code="FAC_KEEP",
                week_code="2026-05",
                storage_uri=str(pdf_path),
                source_email_id=message_id,
                received_at=received_at,
            )
        )
        session.add(
            OrderLine(
                id=_id("OL"),
                order_id=order_id,
                menu_name="menu-a",
                quantity_original=1,
            )
        )
        session.add(Bag(id=_id("BAG"), order_id=order_id, quantity=1))
        session.add(
            OrderOcrEvidenceRun(
                id=evidence_id,
                order_id=order_id,
                schema_version="test",
                status="ready",
                payload_json={"test": True},
                artifact_digest=_id("digest"),
            )
        )
        session.add(
            OrderSheetDraft(
                id=_id("DRF"),
                order_id=order_id,
                base_evidence_run_id=evidence_id,
                draft_sheet_json={"rows": []},
            )
        )
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                evidence_run_id=evidence_id,
                state="sheet_saved",
                secondary_actions_json={"workflow_v2": {}},
            )
        )
        session.add(
            UploadedPdf(
                id=uploaded_id,
                message_id=message_id,
                content_sha256=_id("sha"),
                original_filename=pdf_path.name,
                storage_uri=str(pdf_path),
                received_at=received_at,
                current_order_id=order_id,
            )
        )
        session.add(
            UploadedPdfAttempt(
                id=_id("UPLA"),
                uploaded_pdf_id=uploaded_id,
                attempt_no=1,
                stage="uploaded",
            )
        )
        session.add(OcrJob(id=f"OCR-{order_id}", input_reference=str(pdf_path), status="done"))
        session.add(IngestJob(id=message_id, payload={"pdf_uri": str(pdf_path)}))
        session.add(
            ManufacturingAggregateRow(
                id=_id("MAR"),
                week_code="2026-05",
                facility_code="FAC_KEEP",
                quantity=1,
            )
        )
    return order_id, message_id


def test_cleanup_requires_explicit_scope() -> None:
    with pytest.raises(ValueError):
        build_order_cleanup_plan(CleanupScope())


def test_cleanup_plan_and_apply_delete_order_data_but_preserve_facility_config(tmp_path: Path) -> None:
    pdf_path = tmp_path / "fax.pdf"
    pdf_path.write_bytes(b"%PDF-test")
    facility_id = _id("FAC_KEEP_")
    with session_scope() as session:
        session.add(Facility(id=facility_id, name="facility-to-keep"))
        session.add(FacilityConfig(facility_id=facility_id, config_json={"template": "keep"}))

    order_id, message_id = _create_order_graph(
        received_at=datetime(2026, 5, 1, 10, 0, 0),
        pdf_path=pdf_path,
    )

    scope = CleanupScope(all_orders=True)
    plan = build_order_cleanup_plan(scope)

    assert order_id in plan["order_ids"]
    assert message_id in plan["message_ids"]
    assert plan["counts"]["orders"] >= 1
    assert plan["counts"]["order_lines"] >= 1
    assert plan["counts"]["order_ocr_evidence_runs"] >= 1
    assert plan["preserved_canonical_counts"]["facilities"] >= 1

    result = apply_order_cleanup(scope, confirm_token="CLEAN_ORDER_DATA")

    assert result["removed"]["orders"] >= 1
    with session_scope() as session:
        assert session.get(Order, order_id) is None
        assert session.get(Facility, facility_id) is not None
        assert session.get(FacilityConfig, facility_id) is not None


def test_cleanup_apply_rejects_missing_confirmation(tmp_path: Path) -> None:
    pdf_path = tmp_path / "fax.pdf"
    pdf_path.write_bytes(b"%PDF-test")
    order_id, _ = _create_order_graph(
        received_at=datetime(2026, 5, 2, 10, 0, 0),
        pdf_path=pdf_path,
    )

    with pytest.raises(ValueError):
        apply_order_cleanup(CleanupScope(received_from=date(2026, 5, 2), received_to=date(2026, 5, 2)), confirm_token="")

    with session_scope() as session:
        assert session.get(Order, order_id) is not None


def test_export_pdfs_for_cleanup_writes_manifest_and_copies_local_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(b"%PDF-export")
    order_id, _ = _create_order_graph(
        received_at=datetime(2026, 5, 1, 9, 0, 0),
        pdf_path=pdf_path,
    )
    export_dir = tmp_path / "export"

    manifest = export_order_pdfs_for_cleanup(
        scope=CleanupScope(received_from=date(2026, 5, 1), received_to=date(2026, 5, 1)),
        output_dir=export_dir,
    )

    exported_items = [item for item in manifest["items"] if item["order_id"] == order_id]
    assert exported_items
    assert exported_items[0]["status"] == "exported"
    assert Path(exported_items[0]["target_path"]).read_bytes() == b"%PDF-export"
    assert (export_dir / "manifest.json").exists()
