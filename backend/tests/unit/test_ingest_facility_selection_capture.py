"""Isolated authored JSON/SQL tests, not live PDF, OCR or provider proof."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import select

from src.db import session_scope
from src.models.facility import Facility
from src.models.ingest_job import IngestJob
from src.models.order import Order
from src.services import canonical_facility_store as canonical
from src.services import facility_master_service
from src.services import ingest_facility_selection_service as selection
from src.services import ingest_job_service as jobs
from src.workers.ingest_mail_adapter import parse_ingest_payload, to_job_kwargs

WEEK = "2026-01@2026-01-01~2026-01-03"


@pytest.fixture
def captured(tmp_path, monkeypatch, isolated_formal_schema):
    record = {"version_id": "authored-A", "valid_from": "2026-01-01", "valid_to": "2026-01-31",
        "facility": {"facility_id": "F", "facility_name": "Authored component", "areas": None,
            "unknown": {"zero": 0, "null": None}, "quantity_setting": 0,
            "fax_template": {"template_id": "authored-template", "units": "normalized",
                "table_box": [0, 0, 1, 1], "header_rows": 0,
                "columns": [{"index": 0, "source_index": 0, "role": "quantity",
                    "diet_type": "regular", "area_id": "X"}]}}}
    path = tmp_path / "component-canonical.json"
    path.write_text(json.dumps({"facilities": [{"facility_id": "F", "versions": [record]}]}))
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(path))
    from src.services import ingest_delivery_dispatch_service as dispatch
    for key, value in {"PROJECT_ID": "fixture-project", "LOCATION": "fixture-location",
            "QUEUE": "fixture-queue", "TARGET_URL": "https://fixture.invalid",
            "SERVICE_ACCOUNT_EMAIL": "fixture@fixture.iam.gserviceaccount.com",
            "AUDIENCE": "fixture-audience", "DISPATCH_DEADLINE_SECONDS": "15"}.items():
        monkeypatch.setenv(dispatch.PREFIX + key, value)
    for role in ("WORKER_", "DRAIN_"):
        for key, value in {"ISSUER": "https://accounts.google.com", "AUDIENCE": "fixture-audience",
                "EMAIL": "fixture@fixture.iam.gserviceaccount.com", "SUBJECT": "fixture-subject"}.items():
            monkeypatch.setenv(dispatch.PREFIX + role + key, value)
    revision = facility_master_service.get_snapshot().revision
    receipt = selection.capture_selection("F", "2026-01-02", revision, "authored-A", WEEK)
    payload = {"message_id": "component-message", "pdf_uri": "gs://fixture/input.bin",
        "received_at": "2026-01-02T12:00:00", "facility_hint": "F", "week_hint": WEEK,
        "facility_selection": receipt, "skip_ocr": False, "content_sha256": "a" * 64}
    return path, payload


def test_initial_receipt_is_canonical_and_retry_does_not_select_latest(captured, monkeypatch):
    _, payload = captured
    receipt = jobs.create_ingest_receipt(payload)
    with session_scope() as session:
        stored = session.get(IngestJob, receipt["id"])
        assert stored.payload["facility_selection"] == payload["facility_selection"]
        assert stored.input_digest == jobs.payload_digest(stored.payload)
    monkeypatch.setattr(facility_master_service, "get_snapshot",
        lambda: pytest.fail("replay must not select current canonical JSON"))
    replay = jobs.create_ingest_receipt(deepcopy(payload))
    assert replay == receipt
    forced = jobs.create_ingest_receipt(deepcopy(payload), force=True)
    assert forced["generation"] != receipt["generation"]
    with session_scope() as session:
        assert session.get(IngestJob, receipt["id"]).payload["facility_selection"] == payload["facility_selection"]


@pytest.mark.parametrize("change", ["cas", "forged", "missing_source"])
def test_initial_shared_receipt_refuses_source_changes_before_insert(captured, change):
    path, payload = captured
    if change == "missing_source":
        path.unlink()
    else:
        master = json.loads(path.read_text())
        master["facilities"][0]["versions"][0]["facility"]["quantity_setting"] = 1
        path.write_text(json.dumps(master))
        if change == "forged":
            # An internally consistent old receipt cannot authenticate its own authored JSON.
            payload["facility_selection"]["canonical_source_proof"]["revision"] = facility_master_service.get_snapshot().revision
    with pytest.raises(ValueError, match="initial_source"):
        jobs.create_ingest_receipt(payload)
    with session_scope() as session:
        assert session.execute(select(IngestJob)).scalars().all() == []


@pytest.mark.parametrize("field,value", [("facility_hint", "foreign"), ("facility_selection", None),
    ("week_hint", "2026-01@2026-01-04~2026-01-10")])
def test_owner_missing_and_outside_period_are_refused(captured, field, value):
    _, payload = captured
    payload[field] = value
    with pytest.raises(ValueError):
        jobs.create_ingest_receipt(payload)
    with session_scope() as session:
        assert session.execute(select(IngestJob)).scalars().all() == []


def test_explicit_version_and_revision_required(captured):
    _, payload = captured
    for revision, version in [("", "authored-A"), ("old-revision", "authored-A"),
            (payload["facility_selection"]["canonical_source_proof"]["revision"], "foreign-version")]:
        with pytest.raises(canonical.CanonicalStoreError):
            selection.capture_selection("F", "2026-01-02", revision, version, WEEK)


def test_materialized_order_projection_preserves_zero_null_and_owner(captured):
    _, payload = captured
    selected = payload["facility_selection"]
    with session_scope() as session:
        session.add(Facility(id="F", name="Authored component"))
    with session_scope() as session:
        projection_id = selection.materialize_selection(session, selected)
        session.add(Order(id="component-order", document_uri=payload["pdf_uri"], message_id=payload["message_id"],
            facility_code="F", week_code=WEEK, template_version_id=projection_id))
    with session_scope() as session:
        order = session.get(Order, "component-order")
        selection.require_order_selection(session, order, selected)
        assert order.template_version_id == selected["facility_template_version_id"]
        assert selected["unknown"] == {"zero": 0, "null": None}
        for field, value in [("template_version_id", None), ("facility_code", "foreign"),
                ("week_code", "2026-01@2026-01-04~2026-01-10")]:
            prior = getattr(order, field)
            setattr(order, field, value)
            with pytest.raises(ValueError):
                selection.require_order_selection(session, order, selected)
            setattr(order, field, prior)


def test_adapter_and_first_pass_failure_keep_original_capture(captured, monkeypatch):
    _, data = captured
    payload = parse_ingest_payload(data)
    assert to_job_kwargs(payload)["facility_selection"] == data["facility_selection"]
    from src.workers import ingest_worker as worker
    boundary = {"job_id": "job", "order_id": "order", "ingest_lease": {"payload": deepcopy(data)},
        "orders": {"order": {"order": {"facility_code": "F", "week_code": WEEK},
            "document": {"source_email_id": data["message_id"], "storage_uri": data["pdf_uri"],
                "received_at": data["received_at"], "id": "document"}}}}
    monkeypatch.setattr(worker, "current_nonworkflow_execution", lambda: boundary)
    monkeypatch.setattr(worker, "get_job", lambda _: {"metrics": {"request_mode": "ingest_first_pass"}})
    observed = []
    def persist(failed, **kwargs):
        selection.validate_selection(failed.facility_selection, failed.facility_hint, failed.week_hint)
        observed.append((failed, kwargs))
        return {"non_ocr_terminal_receipt": {"terminal": "failed"}}
    monkeypatch.setattr(worker, "create_order_from_ingest", persist)
    failures = []
    monkeypatch.setattr(jobs, "fail_ingest_attempt", lambda lease, error: failures.append((lease, error)))
    assert worker._mark_ocr_job_final_failed("job", error_message="recognizer_failure") == {"terminal": "failed"}
    assert observed[0][0].facility_selection == data["facility_selection"]
    assert observed[0][1]["error_message"] == "recognizer_failure"
    assert observed[0][1]["execution_input"] is boundary
    assert failures == [(boundary["ingest_lease"], "recognizer_failure")]


def test_real_registration_persists_captured_projection_and_rejects_unowned_source(captured):
    from src.services import ingest_delivery_service as delivery
    from src.services import order_service
    _, data = captured
    with session_scope() as session:
        session.add(Facility(id="F", name="Authored component"))
    receipt = jobs.create_ingest_receipt(data)
    delivery_id, generation = delivery.delivery_for_job(receipt["id"], receipt["generation"], receipt["input_digest"])
    claim = delivery.claim_delivery(delivery_id, generation)
    parsed = parse_ingest_payload(data)
    with delivery.claim_context(claim), jobs.ingest_execution_context(claim["lease"]):
        jobs.prepare_ingest_ocr_job(claim["lease"], "component-ocr", parsed)
        registered = order_service.create_order_from_ingest(parsed, lines=None, document_status="processing",
            error_message="ocr_pending", registration_job_id="component-ocr")
        duplicate = order_service.create_order_from_ingest(parsed, lines=None, document_status="processing",
            error_message="ocr_pending", registration_job_id="component-ocr")
        assert duplicate["id"] == registered["id"]
    with session_scope() as session:
        order = session.get(Order, registered["id"])
        assert order.template_version_id == data["facility_selection"]["facility_template_version_id"]
        assert order.facility_code == "F" and order.week_code == WEEK
        selection.require_order_selection(session, order, data["facility_selection"])
        assert session.query(Order).count() == 1
    with pytest.raises(ValueError, match="owner_changed"):
        order_service.create_order_from_ingest(parsed, lines=None, registration_job_id="component-ocr")
    with session_scope() as session:
        assert session.query(Order).count() == 1
