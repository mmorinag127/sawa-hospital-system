"""Authored delivery component fixtures, not operator/OCR flow proof."""

import json
from copy import deepcopy
from hashlib import sha256

import fitz

from src.db import session_scope
from src.models.menu import MonthlyMenu, MonthlyMenuEntry
from src.services import config_service

from src.services import ingest_delivery_dispatch_service as dispatch
from src.services import ingest_delivery_service as delivery
from src.services import ingest_job_service as ingest
from src.services import uploaded_pdf_service as uploads


def configure_delivery(monkeypatch):
    values = {
        "PROJECT_ID": "fixture-project", "LOCATION": "fixture-location",
        "QUEUE": "fixture-queue", "TARGET_URL": "https://fixture.invalid",
        "SERVICE_ACCOUNT_EMAIL": "fixture@fixture.iam.gserviceaccount.com",
        "AUDIENCE": "fixture-audience", "DISPATCH_DEADLINE_SECONDS": "15",
    }
    for key, value in values.items():
        monkeypatch.setenv(dispatch.PREFIX + key, value)
    for role in ("WORKER_", "DRAIN_"):
        for key, value in {
            "ISSUER": "https://accounts.google.com", "AUDIENCE": values["AUDIENCE"],
            "EMAIL": values["SERVICE_ACCOUNT_EMAIL"], "SUBJECT": "fixture-subject",
        }.items():
            monkeypatch.setenv(dispatch.PREFIX + role + key, value)


def _authored_selection(uploaded, effective_date):
    from src.services.canonical_facility_store import read_snapshot
    from src.services.ingest_facility_selection_service import capture_selection
    snapshot = read_snapshot()
    return capture_selection(uploaded["facility_hint"], effective_date.isoformat(),
                             snapshot.revision, "authored-original", uploaded["week_hint"])


def reserve_uploaded_original(uploaded_pdf_id, *, effective_date):
    """Copy the physical authored upload through the production payload producer."""
    uploaded = uploads.get_uploaded_pdf(uploaded_pdf_id)
    assert uploaded is not None
    selection = _authored_selection(uploaded, effective_date)
    return ingest.create_ingest_receipt(uploads.build_ingest_payload(uploaded, facility_selection=selection))


def process_uploaded_original(uploaded_pdf_id):
    uploaded = uploads.get_uploaded_pdf(uploaded_pdf_id)
    assert uploaded is not None
    original = ingest.get_ingest_job_snapshot(uploaded["message_id"])
    assert original is not None
    delivery_id, generation = delivery.delivery_for_job(
        original["id"], original["generation"], original["input_digest"],
    )
    return delivery.process(delivery_id, generation)


def authored_sources(monkeypatch, tmp_path, *, facility_id, menu_date, daypart="lunch"):
    """Explicit component JSON/menu/PDF inputs, never operator scenario proof."""
    from src.models.facility import Facility

    facility = {
        "facility_id": facility_id, "facility_name": "Authored component facility",
        "areas": [], "fax_template_id": "authored-component",
        "fax_template": {"template_id": "authored-component", "page": 1, "columns": [
            {"index": 0, "source_index": 0, "role": "date", "name": "date_mmdd"},
            {"index": 1, "source_index": 1, "role": "daypart", "name": "daypart"},
            {"index": 2, "source_index": 2, "role": "menu_name", "name": "menu"},
            {"index": 3, "source_index": 3, "role": "quantity", "name": "qty.regular_2f", "diet_type": "regular", "area_id": "2F"},
        ]},
    }
    master = tmp_path / "authored-facilities.json"
    master.write_text(json.dumps({"facilities": [{**facility, "versions": [{
        "version_id": "authored-original", "valid_from": menu_date.isoformat(),
        "valid_to": None, "facility": deepcopy(facility),
    }]}]}))
    policy = tmp_path / "authored-policy.json"
    policy.write_text(json.dumps({"ocr_retry_limit": 1, "quantity_rules": {}, "backlog_policy": {"skip_ocr_for_stale": False}}))
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(master))
    monkeypatch.setattr(config_service, "INGEST_POLICY_PATH", policy)
    with session_scope() as session:
        parent = session.get(Facility, facility["facility_id"])
        if parent is None:
            session.add(Facility(id=facility["facility_id"], name=facility["facility_name"]))
            session.flush()
        else:
            assert parent.name == facility["facility_name"]
        month = menu_date.strftime("%Y-%m")
        session.add(MonthlyMenu(id=month, month_start=menu_date.replace(day=1)))
        session.add(MonthlyMenuEntry(id="authored-physical-row", monthly_menu_id=month,
            menu_date=menu_date, daypart=daypart, name="Menu A", slot_index=0,
            facility_override=facility_id, diet_type="regular"))
    with fitz.open() as pdf:
        pdf.new_page()
        data = pdf.tobytes()
    path = tmp_path / "original.pdf"
    path.write_bytes(data)
    return str(path), sha256(data).hexdigest()


def register_authored_original(uploaded_pdf_id, *, effective_date):
    """New authored fixture registration and canonical selection before execution."""
    from src.workers.ingest_mail_adapter import parse_ingest_payload
    from src.services.workflow_attempt_guard_service import snapshots_equal
    from src.models.ingest_job import IngestJob
    uploaded = uploads.get_uploaded_pdf(uploaded_pdf_id)
    assert uploaded is not None
    selection = _authored_selection(uploaded, effective_date)
    original = ingest.get_ingest_job_snapshot(uploaded["message_id"])
    if original is None:
        ingest.create_ingest_receipt(uploads.build_ingest_payload(uploaded, facility_selection=selection))
        original = ingest.get_ingest_job_snapshot(uploaded["message_id"])
    assert original is not None
    with session_scope() as session:
        receipt = session.get(IngestJob, uploaded["message_id"])
        assert receipt is not None
        assert snapshots_equal(receipt.payload["facility_selection"], selection)
    payload = parse_ingest_payload(uploads.build_ingest_payload(uploaded, facility_selection=selection))
    return register_authored_payload(payload, effective_date=effective_date)


def register_authored_payload(payload, *, effective_date, registration_job_id=None):
    """Register explicit component inputs through actual registration/lineage APIs."""
    from src.services import order_service
    from src.services.workflow_attempt_guard_service import snapshots_equal
    from src.models.order import Order

    selection = payload.facility_selection
    assert isinstance(selection, dict)
    assert selection["effective_date"] == effective_date.isoformat()

    def register(lease):
        assert lease["id"] == payload.message_id
        assert not lease.get("claim_refusal")
        assert snapshots_equal(lease["payload"]["facility_selection"], selection)
        registered = order_service.create_order_from_ingest(payload, lines=None,
            ocr_attempts=0, document_status="processing", error_message="ocr_pending",
            registration_job_id=registration_job_id)
        with session_scope() as session:
            order = session.get(Order, registered["id"])
            assert order is not None
            assert order.template_version_id == selection["facility_template_version_id"]
        return registered

    lease = ingest.current_ingest_lease()
    if lease is not None:
        return register(lease)

    original = ingest.get_ingest_job_snapshot(payload.message_id)
    assert original is not None
    delivery_id, generation = delivery.delivery_for_job(
        original["id"], original["generation"], original["input_digest"],
    )
    claim = delivery.claim_delivery(delivery_id, generation)
    assert claim["duplicate"] is False
    assert claim["execute"] is True
    lease = claim["lease"]
    assert lease is not None
    with delivery.claim_context(claim), ingest.ingest_execution_context(lease):
        return register(lease)


def authored_pipeline_provider(monkeypatch, *, table_rows):
    """External OCR component double; every ownership receipt is real."""
    from copy import deepcopy
    from src.services import ocr_job_service as jobs
    from src.services.workflow_attempt_guard_service import current_nonworkflow_execution
    from src.workers import ingest_worker

    def output(**kwargs):
        execution = current_nonworkflow_execution()
        assert execution is not None
        job_id = kwargs["job_id"]
        reference = f"gs://authored-component/input/{job_id}.pdf"
        output_reference = f"gs://authored-component/output/{job_id}.json"
        assert jobs.record_own_pipeline_delivery(job_id,
            selected_input_reference=kwargs["input_reference"], input_reference=reference,
            output_reference=output_reference, generation="1",
            input_sha256=sha256(kwargs["pdf_bytes"]).hexdigest(), wait_timeout_seconds=15)
        receipt = jobs.get_job(job_id)["metrics"]["pipeline_delivery_receipt"]
        return {
            "status": "done", "template_id": kwargs["preferred_template_id"],
            "input_reference": receipt["input_reference"],
            "output_reference": receipt["output_reference"], "input_generation": receipt["generation"],
            "job_id": receipt["pipeline_job_id"], "input_sha256": receipt["input_sha256"],
            "configured_input_sha256": receipt["configured_input_sha256"],
            "execution_owner": {"job_id": receipt["job_id"], "order_id": receipt["order_id"],
                "workflow_attempt_id": receipt["workflow_attempt_id"], "workflow_revision": receipt["workflow_revision"],
                "nonworkflow_execution_id": receipt["execution_id"]},
            "canonical_adoption_allowed": True,
            "canonical_stages": {"classification": "passed", "roi": "passed", "correction": "disabled"},
            "table_rows": deepcopy(table_rows),
        }

    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", output)
