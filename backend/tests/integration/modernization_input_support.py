"""Owned synthetic registration inputs; no OCR execution or live PDF proof."""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

import pytest
import fitz
from openpyxl import Workbook

from src.db import session_scope
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.uploaded_pdf import UploadedPdf
from src.models.menu import MenuMaster, MonthlyMenu, MonthlyMenuEntry, MonthlyMenuItem
from src.services import menu_service
from src.services import template_field_schema_service
from src.workers.ingest_mail_adapter import IngestEmailPayload
from src.services import facility_service
from src.services import canonical_facility_store, config_service, facility_master_service
from src.services import ingest_delivery_dispatch_service as dispatch
from src.services import ingest_delivery_service as delivery
from src.services import ingest_facility_selection_service as selection
from src.services import ingest_job_service as jobs
from src.services import order_service
from src.services.sheet_week_service import parse_sheet_week_value


@pytest.fixture
def authored_ingest_master(tmp_path, monkeypatch, registration_environment):
    source = Path(__file__).resolve().parents[2] / "src/data/facility_master.template.json"
    master = json.loads(source.read_bytes())
    master["facilities"].append({
        "facility_id": "FAC001", "facility_name": "Synthetic ingest fixture FAC001", "areas": None,
        "fax_template_id": "synthetic-FAC001",
        "fax_template": {
            "template_id": "synthetic-FAC001", "units": "normalized",
            "table_box": [0, 0, 1, 1], "header_rows": 0,
            "columns": [{"index": 0, "source_index": 0, "role": "quantity",
                         "diet_type": "regular", "area_id": "X"}],
        },
    })
    for facility in master["facilities"]:
        if "fax_template" not in facility:
            facility["fax_template"] = deepcopy(facility["fax_template_override"])
            facility["fax_template"]["template_id"] = facility["fax_template_id"]
        template = facility["fax_template"]
        for column in template["columns"]:
            column["name"] = template_field_schema_service.canonical_field_name_from_template_column(column)
        template["main_ocr_row_fields"] = template_field_schema_service.derive_row_fields_from_columns(template["columns"])
        if "invoice_template" in facility:
            invoice = facility["invoice_template"]
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = invoice["sheet_name"]
            for column in invoice["columns"]:
                sheet.cell(1, column["column_index"], column.get("header", column["name"]))
            invoice_path = tmp_path / (facility["facility_id"] + "-invoice.xlsx")
            workbook.save(invoice_path)
            workbook.close()
            invoice["template_uri"] = str(invoice_path)
        digest = sha256(json.dumps(facility, sort_keys=True).encode()).hexdigest()
        facility["versions"] = [canonical_facility_store.initial_facility_version(facility, digest)]
    path = tmp_path / "authored-ingest-master.json"
    path.write_text(json.dumps(master, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(path))
    monkeypatch.delenv("FACILITY_MASTER_URI", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    yield
    config_service.reload_configs()


@pytest.fixture
def registration_environment(monkeypatch):
    for key, value in {
        "PROJECT_ID": "fixture-project", "LOCATION": "fixture-location", "QUEUE": "fixture-queue",
        "TARGET_URL": "https://fixture.invalid", "SERVICE_ACCOUNT_EMAIL": "fixture@fixture.iam.gserviceaccount.com",
        "AUDIENCE": "fixture-audience", "DISPATCH_DEADLINE_SECONDS": "15",
    }.items():
        monkeypatch.setenv(dispatch.PREFIX + key, value)
    for role in ("WORKER_", "DRAIN_"):
        for key, value in {
            "ISSUER": "https://accounts.google.com", "AUDIENCE": "fixture-audience",
            "EMAIL": "fixture@fixture.iam.gserviceaccount.com", "SUBJECT": "fixture-subject",
        }.items():
            monkeypatch.setenv(dispatch.PREFIX + role + key, value)


def capture_payload(payload):
    snapshot = facility_master_service.get_snapshot()
    master = snapshot.master
    facility = next(item for item in master["facilities"] if item["facility_id"] == payload.facility_hint)
    assert len(facility["versions"]) == 1
    _, start, _ = parse_sheet_week_value(payload.week_hint)
    receipt = selection.capture_selection(payload.facility_hint, start.isoformat(), snapshot.revision,
                                          facility["versions"][0]["version_id"], payload.week_hint)
    received = datetime.fromisoformat(payload.received_at) if isinstance(payload.received_at, str) else payload.received_at
    return replace(payload, received_at=received, facility_selection=receipt)


def register_ingest(payload, **kwargs):
    payload = capture_payload(payload)
    with session_scope() as session:
        assert facility_service.ensure_facility_materialized(session, payload.facility_hint) is not None
    data = asdict(payload)
    data["received_at"] = payload.received_at.isoformat()
    receipt = jobs.create_ingest_receipt(data)
    delivery_id, generation = delivery.delivery_for_job(receipt["id"], receipt["generation"], receipt["input_digest"])
    claim = delivery.claim_delivery(delivery_id, generation)
    assert claim is not None
    job_id = "fixture-ocr-" + receipt["id"]
    with delivery.claim_context(claim), jobs.ingest_execution_context(claim["lease"]):
        jobs.prepare_ingest_ocr_job(claim["lease"], job_id, payload)
        return order_service.create_order_from_ingest(payload, registration_job_id=job_id, **kwargs)


def seed_saved_evidence(order_id, template_version_id, payload, *, producer_version, source):
    """Prepare existing evidence rows, not a simulated recognizer execution."""
    evidence_id = str(uuid4())
    with session_scope() as session:
        session.add(OrderOcrEvidenceRun(
            id=evidence_id, order_id=order_id, template_version_id=template_version_id,
            schema_version="v2", producer_version=producer_version, source=source,
            status="ready", payload_json=deepcopy(payload),
            artifact_digest=sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
        ))
    return {"id": evidence_id}


def sheet_ingest_payload(message_id, rows):
    root = Path(facility_master_service.get_snapshot().source).parent
    path = root / (str(uuid4()) + ".pdf")
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 40), "Synthetic FAC00001 / 2025-12-22..2025-12-28 / saved-sheet fixture")
        page.insert_textbox((40, 60, 550, 800), json.dumps(rows, ensure_ascii=True), fontsize=8)
        pdf.save(path)
    digest = sha256(path.read_bytes()).hexdigest()
    uploaded_id = str(uuid4())
    with session_scope() as session:
        session.add(UploadedPdf(id=uploaded_id, message_id=message_id, storage_uri=str(path),
                               original_filename=path.name, content_sha256=digest,
                               received_at=datetime(2025, 12, 23, 10), page_count=1))
    return IngestEmailPayload(message_id, str(path), datetime(2025, 12, 23, 10), "FAC00001",
                              "2025-12@2025-12-22~2025-12-28",
                              content_sha256=digest)


def seed_sheet_monthly_menu(rows, *, slot_start=0):
    """One serving is one unit in these synthetic quantity-retention cases."""
    with session_scope() as session:
        if session.get(MonthlyMenu, "2025-12") is None:
            session.add(MonthlyMenu(id="2025-12", month_start=date(2025, 12, 1)))
            session.flush()
        for row in rows:
            day = datetime.strptime("2025/" + row[0], "%Y/%m/%d").date()
            key = "sheet-input-" + sha256(json.dumps(row[:3]).encode()).hexdigest()[:20]
            if session.get(MenuMaster, key) is not None:
                continue
            session.add(MenuMaster(id=key, name=row[2], normalized_name=menu_service._normalize_menu_name(row[2]),
                                   unit_type="g", qty_per_serving=1, category="主菜", daypart=row[1]))
            session.flush()
            session.add(MonthlyMenuItem(id=key, monthly_menu_id="2025-12", menu_master_id=key,
                                       name=row[2], diet_type="regular", category="主菜", daypart=row[1],
                                       unit_type="g", qty_per_serving=1))
            session.add(MonthlyMenuEntry(id=key, monthly_menu_id="2025-12", menu_date=day,
                                        daypart=row[1], category="主菜", diet_type="regular", name=row[2],
                                        slot_index=slot_start + rows.index(row)))
