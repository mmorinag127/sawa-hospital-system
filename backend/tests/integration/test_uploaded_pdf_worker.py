import pathlib
import sys
import symtable
from datetime import date, datetime, timedelta

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.models.document import OrderDocument  # noqa: E402
from src.models.ingest_job import IngestJob  # noqa: E402
from src.models.ocr_job import OcrJob  # noqa: E402
from src.models.order import Order  # noqa: E402
from src.models.uploaded_pdf import UploadedPdf  # noqa: E402
from src.services.ocr_job_service import create_job, get_job, list_recoverable_jobs, update_job  # noqa: E402
from src.services import order_service  # noqa: E402
from src.services.uploaded_pdf_service import (  # noqa: E402
    backfill_uploaded_pdfs_from_ingest_jobs,
    build_ingest_payload,
    claim_uploaded_pdf,
    create_uploaded_pdf_from_upload,
    get_uploaded_pdf,
    list_ready_uploaded_pdf_ids,
)
from src.services.manual_upload_service import ManualUploadSavedFile  # noqa: E402
from src.workers.ingest_mail_adapter import parse_ingest_payload  # noqa: E402
from src.workers import ingest_worker  # noqa: E402
from tests.helpers.ingest_original import (  # noqa: E402
    configure_delivery, authored_sources, process_uploaded_original, authored_pipeline_provider,
    register_authored_original,
)
from src.services import ingest_job_service  # noqa: E402
from src.services import ingest_delivery_service  # noqa: E402
from src.models.ingest_delivery import IngestDelivery  # noqa: E402
from src.services.ocr_job_service import OwnPipelineLineageError  # noqa: E402

pytestmark = pytest.mark.usefixtures("isolated_formal_schema", "delivery_configuration")


@pytest.fixture
def delivery_configuration(monkeypatch):
    configure_delivery(monkeypatch)


def test_worker_service_aliases_resolve_real_modules():
    assert ingest_worker.order_service is order_service
    source = pathlib.Path(ingest_worker.__file__).read_text()
    symbols = symtable.symtable(source, ingest_worker.__file__, "exec")
    def check(table):
        for symbol in table.get_symbols():
            if symbol.is_global() and symbol.is_referenced() and symbol.get_name().endswith("_service"):
                assert symbol.get_name() in vars(ingest_worker), symbol.get_name()
        for child in table.get_children():
            check(child)
    check(symbols)


@pytest.mark.parametrize("mutation", ["bool", "int", "float", "null", "unknown", "key_order"])
def test_materialize_selected_raw_literals_are_strict(monkeypatch, tmp_path, mutation):
    import json
    from copy import deepcopy
    from sqlalchemy import func, select
    from src.models.facility_template_version import FacilityTemplateVersion
    from src.services import canonical_facility_lineage as lineage, facility_service
    from src.services.canonical_facility_store import CanonicalStoreError
    from src.services.workflow_attempt_guard_service import snapshots_equal

    authored_sources(monkeypatch, tmp_path, facility_id="FAC00001", menu_date=date(2026, 4, 6))
    path = tmp_path / "authored-facilities.json"
    master = json.loads(path.read_text())
    raw = {"count": 1, "confirmed": True, "ratio": 1.0, "optional": None, "blank": ""}
    master["facilities"][0]["versions"][0]["facility"]["raw_operator_values"] = deepcopy(raw)
    path.write_text(json.dumps(master))
    with session_scope() as session:
        facility_service.ensure_facility_materialized(session, "FAC00001")
    with session_scope() as session:
        selected, _ = lineage.resolve(session, "FAC00001", date(2026, 4, 6))
        original_proof = deepcopy(selected["canonical_source_proof"])
        if mutation == "key_order":
            selected["raw_operator_values"] = dict(reversed(list(raw.items())))
            result, row = lineage.materialize(session, selected)
            assert snapshots_equal(row.config_json["raw_operator_values"], raw)
            assert snapshots_equal(row.validation_json["creation_source_proof"], original_proof)
            assert snapshots_equal(result["canonical_source_proof"], original_proof)
            return
        if mutation == "unknown":
            selected["raw_operator_values"]["unknown"] = "raw"
        else:
            key, value = {"bool": ("count", True), "int": ("confirmed", 1),
                          "float": ("count", 1.0), "null": ("blank", None)}[mutation]
            selected["raw_operator_values"][key] = value
        before = session.scalar(select(func.count()).select_from(FacilityTemplateVersion))
        with pytest.raises(CanonicalStoreError, match="canonical_lineage_selection_changed"):
            lineage.materialize(session, selected)
        assert session.scalar(select(func.count()).select_from(FacilityTemplateVersion)) == before
        assert snapshots_equal(selected["canonical_source_proof"], original_proof)


def _saved_file(*, message_id: str, filename: str) -> ManualUploadSavedFile:
    return ManualUploadSavedFile(
        message_id=message_id,
        pdf_uri=f"gs://bucket/{filename}",
        content_sha256=f"sha-{message_id}",
        original_filename=filename,
        received_at=datetime(2026, 4, 6, 10, 0, 0),
    )


def test_build_ingest_payload_stops_missing_week_despite_filename():
    with pytest.raises(ValueError, match="^week_hint_required$"):
        build_ingest_payload(
            {
                "message_id": "upload:sha256:filename-derived-week",
                "storage_uri": "gs://bucket/17.fax000355472_0405_.pdf",
                "received_at": "2026-04-06T00:04:43.617807",
                "facility_hint": None,
                "week_hint": None,
                "facility_name": None,
                "skip_ocr": False,
                "source_kind": "manual_upload",
                "original_filename": "17.fax000355472_0405_.pdf",
                "content_sha256": "sha-filename-derived-week",
            }
        )


def test_create_order_from_ingest_stops_missing_week_without_mutation():
    order_service.clear_all()
    payload = parse_ingest_payload(
        {
            "message_id": "upload:sha256:create-order-week-fallback",
            "pdf_uri": "gs://bucket/manual-uploads/2026/04/06/16.fax000355571_0405_.pdf",
            "received_at": "2026-04-06T00:04:43.617807",
            "facility_hint": None,
            "week_hint": None,
            "facility_name": None,
            "skip_ocr": False,
            "source_kind": "manual_upload",
            "original_filename": "16.fax000355571_0405_.pdf",
            "content_sha256": "sha-create-order-week-fallback",
        }
    )

    with session_scope() as session:
        before = (session.query(Order).count(), session.query(OrderDocument).count())
    with pytest.raises(ValueError, match="^week_hint_required$"):
        order_service.create_order_from_ingest(payload, lines=[])
    with session_scope() as session:
        assert (session.query(Order).count(), session.query(OrderDocument).count()) == before


def test_pre_registered_original_component_owned_delivery_completes(monkeypatch, tmp_path):
    order_service.clear_all()
    saved = _saved_file(message_id="upload:sha256:worker-success", filename="worker-success.pdf")
    uri, digest = authored_sources(monkeypatch, tmp_path, facility_id="FAC00001", menu_date=date(2026, 4, 6))
    saved = ManualUploadSavedFile(message_id=saved.message_id, pdf_uri=uri,
        content_sha256=digest, original_filename=saved.original_filename, received_at=saved.received_at)
    uploaded_pdf, _ = create_uploaded_pdf_from_upload(
        saved=saved,
        facility_hint="FAC00001",
        week_hint="2026-04@2026-04-05~2026-04-11",
        facility_name=None,
        skip_ocr=False,
        source_kind="manual_upload",
    )

    original = ingest_job_service.get_ingest_job_snapshot(saved.message_id)
    assert original["id"] == saved.message_id
    assert original["input_digest"] == uploaded_pdf["ingest_receipt"]["input_digest"]
    with session_scope() as session:
        intent = session.query(IngestDelivery).filter_by(job_id=saved.message_id).one()
        assert intent.original_payload == ingest_job_service.get_ingest_payload(saved.message_id)
    register_authored_original(uploaded_pdf["id"], effective_date=date(2026, 4, 6))
    authored_pipeline_provider(monkeypatch, table_rows=[["04/06", "昼", "Menu A", "2"]])
    process_uploaded_original(uploaded_pdf["id"])

    completed = get_uploaded_pdf(uploaded_pdf["id"])
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["current_stage"] == "completed"
    assert str(completed["current_document_id"]).startswith("DOC")
    assert str(completed["current_order_id"]).startswith("ORD")
    with session_scope() as session:
        order = session.get(Order, completed["current_order_id"])
        assert order is not None
        assert order.week_code == "2026-04@2026-04-05~2026-04-11"
        assert order.current_document_id == completed["current_document_id"]
        assert order.superseded_document_ids in (None, [])
        from src.models.order import OrderLine
        line = session.query(OrderLine).filter_by(order_id=order.id).one()
        assert line.quantity_original == 2
        assert line.date == date(2026, 4, 6)
        assert line.area_id == "2F"
        assert line.menu_name == "Menu A"


def test_owned_delivery_business_failure_is_terminal_after_stale_upload(monkeypatch, tmp_path):
    order_service.clear_all()
    saved = _saved_file(message_id="upload:sha256:worker-stale", filename="worker-stale.pdf")
    uri, digest = authored_sources(monkeypatch, tmp_path, facility_id="FAC00002", menu_date=date(2026, 4, 6))
    saved = ManualUploadSavedFile(message_id=saved.message_id, pdf_uri=uri,
        content_sha256=digest, original_filename=saved.original_filename, received_at=saved.received_at)
    uploaded_pdf, _ = create_uploaded_pdf_from_upload(
        saved=saved,
        facility_hint="FAC00002",
        week_hint="2026-04@2026-04-05~2026-04-11",
        facility_name=None,
        skip_ocr=False,
        source_kind="manual_upload",
    )
    claimed = claim_uploaded_pdf(uploaded_pdf["id"], worker_instance="stale-worker")
    assert claimed is not None

    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf["id"])
        assert row is not None
        row.lease_expires_at = datetime.utcnow() - timedelta(minutes=5)
        row.updated_at = datetime.utcnow() - timedelta(minutes=5)

    assert uploaded_pdf["id"] in list_ready_uploaded_pdf_ids(limit=10)

    register_authored_original(uploaded_pdf["id"], effective_date=date(2026, 4, 6))

    before = ingest_job_service.get_ingest_job_snapshot(saved.message_id)
    calls = []
    def provider_failure(**kwargs):
        calls.append(kwargs)
        raise OwnPipelineLineageError("ocr_timeout")
    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", provider_failure)
    result = process_uploaded_original(uploaded_pdf["id"])

    retried = get_uploaded_pdf(uploaded_pdf["id"])
    assert retried is not None
    assert calls, (result, retried["last_error_message"])
    assert retried["status"] == "failed"
    assert retried["current_stage"] == "failed"
    assert retried["last_error_code"] == "ingest_failed"
    assert retried["last_error_message"] == "first_pass_output_rejected"
    assert retried["attempt_count"] == 2
    assert retried["next_retry_at"] is None
    assert retried["alerted_at"] is not None
    assert uploaded_pdf["id"] not in list_ready_uploaded_pdf_ids(limit=10)
    after = ingest_job_service.get_ingest_job_snapshot(saved.message_id)
    assert after["generation"] == before["generation"]
    assert after["input_digest"] == before["input_digest"]
    assert after["generation_attempts"] == 1
    assert after["status"] == "error"
    assert process_uploaded_original(uploaded_pdf["id"])["acknowledged"] is True
    repeated = ingest_job_service.get_ingest_job_snapshot(saved.message_id)
    assert repeated == after
    assert len(calls) == 1
    with session_scope() as session:
        intent = session.query(IngestDelivery).filter_by(job_id=saved.message_id).one()
        assert intent.terminal_receipt["receipt"]["status"] == "failed"
        assert intent.state == "ACKNOWLEDGED"


def test_interrupted_transport_reuses_original_business_lease_and_budget(monkeypatch, tmp_path):
    uri, digest = authored_sources(monkeypatch, tmp_path, facility_id="FAC00002", menu_date=date(2026, 4, 6))
    saved = ManualUploadSavedFile(message_id="upload:sha256:transport-interruption",
        pdf_uri=uri, content_sha256=digest, original_filename="transport-interruption.pdf",
        received_at=datetime(2026, 4, 6, 10, 0, 0))
    uploaded, _ = create_uploaded_pdf_from_upload(saved=saved, facility_hint="FAC00002",
        week_hint="2026-04@2026-04-05~2026-04-11", facility_name=None,
        skip_ocr=False, source_kind="manual_upload")
    register_authored_original(uploaded["id"], effective_date=date(2026, 4, 6))
    original = ingest_job_service.get_ingest_job_snapshot(saved.message_id)
    delivery_id, generation = ingest_delivery_service.delivery_for_job(
        original["id"], original["generation"], original["input_digest"])
    abandoned = ingest_delivery_service.claim_delivery(delivery_id, generation)
    renewed = ingest_delivery_service.renew_missing(delivery_id, generation)
    resumed = ingest_delivery_service.claim_delivery(delivery_id, renewed["generation"])
    assert resumed["lease"] == abandoned["lease"]
    assert resumed["claim_token"] != abandoned["claim_token"]
    claimed = ingest_job_service.get_ingest_job_snapshot(saved.message_id)
    assert claimed["generation"] == original["generation"]
    assert claimed["input_digest"] == original["input_digest"]
    assert claimed["generation_attempts"] == 1
    assert get_uploaded_pdf(uploaded["id"])["attempt_count"] == 1
    calls = []
    def provider_failure(**kwargs):
        calls.append(kwargs)
        raise OwnPipelineLineageError("provider_required_stage_failed")
    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", provider_failure)
    assert process_uploaded_original(uploaded["id"])["result"] == "business_failed"
    assert len(calls) == 1
    terminal = ingest_job_service.get_ingest_job_snapshot(saved.message_id)
    assert terminal["generation_attempts"] == 1
    assert terminal["generation"] == original["generation"]
    assert uploaded["id"] not in list_ready_uploaded_pdf_ids(limit=10)


@pytest.mark.parametrize("fault", [None, "historical", "key_order", "bool_literal", "int_literal", "float_literal", "null_literal", "unknown_field", "missing_version", "duplicate_version", "tampered_version", "missing", "wrong_id", "facility", "digest", "version", "source_proof", "proof_revision", "legacy_active", "template", "projection_bool", "projection_float", "policy_bool"])
def test_original_projection_capture_validates_physical_source(monkeypatch, tmp_path, fault):
    from copy import deepcopy
    from src.models.facility_template_version import FacilityTemplateVersion
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    from src.services import canonical_facility_lineage as lineage, config_service
    from src.services.workflow_attempt_guard_service import _order_execution_input, snapshots_equal

    uri, digest = authored_sources(monkeypatch, tmp_path, facility_id="FAC00002", menu_date=date(2026, 4, 6))
    import json
    master_path = tmp_path / "authored-facilities.json"
    authored_master = json.loads(master_path.read_text())
    raw_values = {"confirmed": True, "ratio": 1.0, "count": 1, "optional": None, "blank": ""}
    authored_master["facilities"][0]["raw_operator_values"] = deepcopy(raw_values)
    authored_master["facilities"][0]["versions"][0]["facility"]["raw_operator_values"] = deepcopy(raw_values)
    master_path.write_text(json.dumps(authored_master))
    saved = ManualUploadSavedFile(message_id="upload:sha256:projection-capture", pdf_uri=uri,
        content_sha256=digest, original_filename="projection-capture.pdf", received_at=datetime(2026, 4, 6, 10))
    uploaded, _ = create_uploaded_pdf_from_upload(saved=saved, facility_hint="FAC00002",
        week_hint="2026-04@2026-04-05~2026-04-11", facility_name=None,
        skip_ocr=False, source_kind="manual_upload")
    registered = register_authored_original(uploaded["id"], effective_date=date(2026, 4, 6))
    master = json.loads(master_path.read_text())
    source_faults = ("missing_version", "duplicate_version", "tampered_version", "bool_literal", "int_literal", "float_literal", "null_literal", "unknown_field")
    if fault in (*source_faults, "historical", "key_order"):
        source = master["facilities"][0]
        if fault == "historical":
            source["facility_name"] = "Different current root"
            source["fax_template_id"] = "different-current"
            source["fax_template"]["template_id"] = "different-current"
        elif fault == "missing_version":
            source["versions"] = []
        elif fault == "duplicate_version":
            source["versions"].append(deepcopy(source["versions"][0]))
        elif fault == "tampered_version":
            source["versions"][0]["facility"]["facility_name"] = "Tampered selected version"
        elif fault == "key_order":
            source["versions"][0] = dict(reversed(list(source["versions"][0].items())))
        elif fault == "unknown_field":
            source["versions"][0]["facility"]["unknown"] = None
        else:
            key, value = {"bool_literal": ("count", True), "int_literal": ("confirmed", 1),
                "float_literal": ("count", 1.0), "null_literal": ("blank", None)}[fault]
            source["versions"][0]["facility"]["raw_operator_values"][key] = value
        master_path.write_text(json.dumps(master))
    with session_scope() as session:
        order = session.get(Order, registered["id"])
        projection = session.get(FacilityTemplateVersion, order.template_version_id)
        if fault in source_faults:
            with pytest.raises(ValueError, match="original_template_configured_source_mismatch"):
                _order_execution_input(session, order, lock=True)
            assert session.query(OrderOcrEvidenceRun).count() == 0
            return
        before = _order_execution_input(session, order, lock=True)
        assert projection.status == "archived"
        assert before["original_template_lineage"] == lineage.verify_projection(lineage.read_projection(session, projection.id, lock=True))
        assert before["original_template_source_proof"] == projection.validation_json["creation_source_proof"]
        if fault in (None, "historical", "key_order"):
            assert snapshots_equal(before["configured_input"]["facility"], projection.config_json)
            assert snapshots_equal(before["configured_input"]["facility"]["raw_operator_values"], raw_values)
            assert before["configured_input"]["authored_source_envelope"] == master["facilities"][0]
            assert snapshots_equal(before, _order_execution_input(session, order, lock=True))
            source = json.loads(master_path.read_text())
            source["facilities"][0]["facility_name"] = "Root changed after original capture"
            master_path.write_text(json.dumps(source))
            assert config_service.captured_processing_config_matches(order.facility_code, before["configured_input"])
            assert snapshots_equal(before, _order_execution_input(session, order, lock=True, original_input=before))
            return
        if fault == "missing":
            order.template_version_id = None
        elif fault == "wrong_id":
            order.template_version_id = "CFVwrong-explicit-original"
        elif fault == "facility":
            projection.facility_id = "WRONG"
        elif fault == "digest":
            projection.template_digest = "tampered"
        elif fault == "version":
            projection.version = "wrong-version"
        elif fault == "legacy_active":
            projection.status = "active"
        elif fault == "template":
            projection.template_id = "wrong-template"
        elif fault == "source_proof":
            changed = deepcopy(projection.validation_json)
            changed["creation_source_proof"]["source"] = "legacy"
            projection.validation_json = changed
        elif fault == "proof_revision":
            changed = deepcopy(projection.validation_json)
            changed["creation_source_proof"]["revision"] = "different-creation-source"
            projection.validation_json = changed
        elif fault in ("projection_bool", "projection_float"):
            from sqlalchemy import update
            changed = deepcopy(projection.config_json)
            changed["raw_operator_values"]["count"] = True if fault == "projection_bool" else 1.0
            session.execute(update(FacilityTemplateVersion).where(FacilityTemplateVersion.id == projection.id).values(config_json=changed))
        elif fault == "policy_bool":
            policy_path = tmp_path / "authored-policy.json"
            policy = json.loads(policy_path.read_text())
            policy["ocr_retry_limit"] = True
            policy_path.write_text(json.dumps(policy))
        session.flush()
        with pytest.raises((ValueError, lineage.CanonicalStoreError)):
            _order_execution_input(session, order, lock=True, original_input=before)
        assert before["order"]["template_version_id"] == before["original_template_projection"]["id"]
        assert session.query(OrderOcrEvidenceRun).count() == 0


@pytest.mark.parametrize("result_kind", ["empty", "nonpersistable"])
def test_owned_nonpersistable_first_pass_is_terminal_without_lines(monkeypatch, tmp_path, result_kind):
    from src.models.order import OrderLine
    from src.services.workflow_attempt_guard_service import processing_start_read_input, snapshots_equal
    from copy import deepcopy

    uri, digest = authored_sources(monkeypatch, tmp_path, facility_id="FAC00001", menu_date=date(2026, 4, 6))
    saved = ManualUploadSavedFile(message_id="upload:sha256:empty-first-pass", pdf_uri=uri,
        content_sha256=digest, original_filename="empty-first-pass.pdf", received_at=datetime(2026, 4, 6, 10))
    uploaded, _ = create_uploaded_pdf_from_upload(saved=saved, facility_hint="FAC00001",
        week_hint="2026-04@2026-04-05~2026-04-11", facility_name=None,
        skip_ocr=False, source_kind="manual_upload")
    registered = register_authored_original(uploaded["id"], effective_date=date(2026, 4, 6))
    original = ingest_job_service.get_ingest_job_snapshot(saved.message_id)
    authored_pipeline_provider(monkeypatch, table_rows=[])
    provider = ingest_worker.run_ocr_pipeline
    captured = []
    def output(**kwargs):
        captured.append(deepcopy(processing_start_read_input()))
        result = provider(**kwargs)
        if result_kind == "nonpersistable":
            result["status"] = "error"
            result["error"] = "authored_external_failure"
        return result
    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", output)
    assert process_uploaded_original(uploaded["id"])["result"] == "business_failed"
    terminal = get_uploaded_pdf(uploaded["id"])
    assert terminal["status"] == "failed"
    assert terminal["attempt_count"] == 1
    assert terminal["alerted_at"] is None
    assert terminal["last_error_code"] == "ingest_failed"
    assert terminal["last_error_message"]
    assert terminal["next_retry_at"] is None
    assert uploaded["id"] not in list_ready_uploaded_pdf_ids(limit=10)
    after = ingest_job_service.get_ingest_job_snapshot(saved.message_id)
    assert after["generation"] == original["generation"]
    assert after["input_digest"] == original["input_digest"]
    assert after["generation_attempts"] == 1
    with session_scope() as session:
        assert session.query(OrderLine).filter_by(order_id=registered["id"]).count() == 0
    saved_input = get_job(f"OCR-{registered['id']}")["metrics"]["nonworkflow_execution"]["start_orders"][registered["id"]]["processing_start_input"]
    assert len(captured) == 1
    assert snapshots_equal(captured[0], saved_input)


@pytest.mark.parametrize("fault", [None, "current_root", "bool", "int", "float"])
def test_actual_owned_advance_preserves_immutable_original_template(monkeypatch, tmp_path, fault):
    import json
    from copy import deepcopy
    from sqlalchemy import update
    from src.models.facility_template_version import FacilityTemplateVersion
    from src.models.order import OrderLine
    from src.services import canonical_facility_lineage as lineage
    from src.services.workflow_attempt_guard_service import (
        advance_nonworkflow_order_input, current_nonworkflow_execution, snapshots_equal,
    )

    uri, digest = authored_sources(monkeypatch, tmp_path, facility_id="FAC00001", menu_date=date(2026, 4, 6))
    master_path = tmp_path / "authored-facilities.json"
    master = json.loads(master_path.read_text())
    raw = {"confirmed": True, "ratio": 1.0, "count": 1, "optional": None, "blank": ""}
    master["facilities"][0]["raw_operator_values"] = deepcopy(raw)
    master["facilities"][0]["versions"][0]["facility"]["raw_operator_values"] = deepcopy(raw)
    master_path.write_text(json.dumps(master))
    saved = ManualUploadSavedFile(message_id="upload:sha256:original-advance", pdf_uri=uri,
        content_sha256=digest, original_filename="original-advance.pdf", received_at=datetime(2026, 4, 6, 10))
    uploaded, _ = create_uploaded_pdf_from_upload(saved=saved, facility_hint="FAC00001",
        week_hint="2026-04@2026-04-05~2026-04-11", facility_name=None,
        skip_ocr=False, source_kind="manual_upload")
    registered = register_authored_original(uploaded["id"], effective_date=date(2026, 4, 6))
    authored_pipeline_provider(monkeypatch, table_rows=[["04/06", "昼", "Menu A", "2"]])
    provider = ingest_worker.run_ocr_pipeline
    original_captures = []

    def output(**kwargs):
        execution = current_nonworkflow_execution()
        start = deepcopy(execution["start_orders"])
        template_capture = deepcopy(execution["orders"][registered["id"]])
        original_captures.append(start)
        if fault == "current_root":
            changed = json.loads(master_path.read_text())
            changed["facilities"][0]["facility_name"] = "Unrelated current root"
            master_path.write_text(json.dumps(changed))
        with session_scope() as session:
            order = session.get(Order, registered["id"])
            if fault in ("bool", "int", "float"):
                physical = lineage.read_projection(session, order.template_version_id, lock=True)
                changed = deepcopy(physical.config_json)
                key, value = {"bool": ("count", True), "int": ("confirmed", 1), "float": ("count", 1.0)}[fault]
                changed["raw_operator_values"][key] = value
                session.execute(update(FacilityTemplateVersion).where(FacilityTemplateVersion.id == physical.id).values(config_json=changed))
                assert not snapshots_equal(lineage.read_projection(session, physical.id).config_json, physical.config_json)
                with pytest.raises(lineage.CanonicalStoreError):
                    advance_nonworkflow_order_input(session, execution, order)
                session.rollback()
            else:
                advance_nonworkflow_order_input(session, execution, order)
        assert snapshots_equal(execution["start_orders"], start)
        for key in ("original_template_projection", "original_template_lineage", "original_template_source_proof", "configured_input"):
            assert snapshots_equal(execution["orders"][registered["id"]][key], template_capture[key])
        return provider(**kwargs)

    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", output)
    assert process_uploaded_original(uploaded["id"])["result"] == "business_done"
    assert len(original_captures) == 1
    job = get_job(f"OCR-{registered['id']}")
    assert snapshots_equal(job["metrics"]["nonworkflow_execution"]["start_orders"], original_captures[0])
    with session_scope() as session:
        line = session.query(OrderLine).filter_by(order_id=registered["id"]).one()
        assert line.quantity_original == 2
        projection = lineage.read_projection(session, session.get(Order, registered["id"]).template_version_id)
        assert snapshots_equal(projection.config_json["raw_operator_values"], raw)


def test_fresh_upload_owned_runner_stops_missing_explicit_original_version(monkeypatch, tmp_path):
    from src.models.facility_template_version import FacilityTemplateVersion
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    from src.models.order import OrderLine

    uri, digest = authored_sources(monkeypatch, tmp_path, facility_id="FAC00001", menu_date=date(2026, 4, 6))
    saved = ManualUploadSavedFile(message_id="upload:sha256:fresh-missing-version", pdf_uri=uri,
        content_sha256=digest, original_filename="fresh-missing-version.pdf", received_at=datetime(2026, 4, 6, 10))
    uploaded, _ = create_uploaded_pdf_from_upload(saved=saved, facility_hint="FAC00001",
        week_hint="2026-04@2026-04-05~2026-04-11", facility_name=None,
        skip_ocr=False, source_kind="manual_upload")
    original = ingest_job_service.get_ingest_job_snapshot(saved.message_id)
    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", lambda **kwargs: pytest.fail("missing explicit version must stop before provider"))
    result = process_uploaded_original(uploaded["id"])
    assert result["result"] == "business_failed"
    terminal = get_uploaded_pdf(uploaded["id"])
    assert terminal["status"] == "failed"
    assert "original_template_projection_missing" in terminal["last_error_message"]
    assert terminal["next_retry_at"] is None
    assert uploaded["id"] not in list_ready_uploaded_pdf_ids(limit=10)
    with session_scope() as session:
        order = session.query(Order).filter_by(message_id=saved.message_id).one()
        assert order.template_version_id is None
        assert session.query(FacilityTemplateVersion).count() == 0
        assert session.query(OrderOcrEvidenceRun).count() == 0
        assert session.query(OrderLine).count() == 0
    after = ingest_job_service.get_ingest_job_snapshot(saved.message_id)
    assert after["input_digest"] == original["input_digest"]
    assert after["generation"] == original["generation"]
    assert after["generation_attempts"] == 1


def test_operator_restart_preserves_original_payload_and_runs_owned_delivery(monkeypatch, tmp_path):
    from src.services.uploaded_pdf_service import requeue_uploaded_pdf
    order_service.clear_all()
    saved = _saved_file(message_id="upload:sha256:worker-force-restart", filename="worker-force-restart.pdf")
    uri, digest = authored_sources(monkeypatch, tmp_path, facility_id="FAC00003", menu_date=date(2026, 4, 6))
    saved = ManualUploadSavedFile(message_id=saved.message_id, pdf_uri=uri, content_sha256=digest,
        original_filename=saved.original_filename, received_at=saved.received_at)
    uploaded, _ = create_uploaded_pdf_from_upload(saved=saved, facility_hint="FAC00003",
        week_hint="2026-04@2026-04-05~2026-04-11", facility_name=None,
        skip_ocr=False, source_kind="manual_upload")
    original = ingest_job_service.get_ingest_job_snapshot(saved.message_id)
    payload = ingest_job_service.get_ingest_payload(saved.message_id)
    register_authored_original(uploaded["id"], effective_date=date(2026, 4, 6))
    requeue_uploaded_pdf(uploaded["id"])
    restarted = ingest_job_service.get_ingest_job_snapshot(saved.message_id)
    assert restarted["status"] == "pending"
    assert restarted["started_at"] is None
    assert restarted["last_error"] is None
    assert restarted["generation"] != original["generation"]
    assert restarted["input_digest"] == original["input_digest"]
    assert ingest_job_service.get_ingest_payload(saved.message_id) == payload
    authored_pipeline_provider(monkeypatch, table_rows=[["04/06", "昼", "Menu A", "2"]])
    assert process_uploaded_original(uploaded["id"])["result"] == "business_done"
    completed = get_uploaded_pdf(uploaded["id"])
    assert completed["status"] == "completed"
    assert str(completed["current_order_id"]).startswith("ORD")
    assert str(completed["current_document_id"]).startswith("DOC")
    assert ingest_job_service.get_ingest_job_snapshot(saved.message_id)["generation_attempts"] == 1


def test_upload_missing_week_does_not_rewrite_existing_message_order():
    order_service.clear_all()
    saved = _saved_file(
        message_id="upload:sha256:worker-existing-message-order",
        filename="17.fax000355472_0405_.pdf",
    )
    with session_scope() as session:
        document = OrderDocument(
            id="DOC-UPL-NULL-WEEK",
            facility_code=None,
            week_code=None,
            storage_uri=saved.pdf_uri,
            source_email_id=saved.message_id,
            received_at=saved.received_at,
            ocr_attempts=1,
            status="processed",
        )
        order = Order(
            id="ORD-UPL-NULL-WEEK",
            facility_code=None,
            week_code=None,
            status="要確認",
            current_document_id=document.id,
            superseded_document_ids=[],
            document_uri=saved.pdf_uri,
            message_id=saved.message_id,
            received_at=saved.received_at,
        )
        session.add(document)
        session.add(order)

    with pytest.raises(ValueError, match="^week_hint_required$"):
        create_uploaded_pdf_from_upload(saved=saved, facility_hint=None,
            week_hint=None, facility_name=None, skip_ocr=False, source_kind="manual_upload")

    with session_scope() as session:
        order = session.get(Order, "ORD-UPL-NULL-WEEK")
        assert order is not None
        assert order.week_code is None
        assert order.current_document_id == "DOC-UPL-NULL-WEEK"
        assert order.superseded_document_ids == []
        assert session.get(OrderDocument, "DOC-UPL-NULL-WEEK").status == "processed"
        assert session.query(UploadedPdf).count() == 0
        assert session.get(IngestJob, saved.message_id) is None


def test_uploaded_pdf_linking_ignores_archived_orders_with_same_message() -> None:
    order_service.clear_all()
    saved = _saved_file(
        message_id="upload:sha256:archived-order-link",
        filename="17.fax000355472_0405_.pdf",
    )
    uploaded_pdf, _ = create_uploaded_pdf_from_upload(
        saved=saved,
        facility_hint=None,
        week_hint="2026-04@2026-04-05~2026-04-11",
        facility_name=None,
        skip_ocr=False,
        source_kind="manual_upload",
    )

    with session_scope() as session:
        document = OrderDocument(
            id="DOC-ARCHIVED-UPLOAD-LINK",
            facility_code="FAC00001",
            week_code="2026-04@2026-04-05~2026-04-11",
            storage_uri=saved.pdf_uri,
            source_email_id=saved.message_id,
            received_at=saved.received_at,
            ocr_attempts=1,
            status="processed",
        )
        order = Order(
            id="ORD-ARCHIVED-UPLOAD-LINK",
            facility_code="FAC00001",
            week_code="2026-04@2026-04-05~2026-04-11",
            status="アーカイブ",
            current_document_id=document.id,
            superseded_document_ids=[],
            document_uri=saved.pdf_uri,
            message_id=saved.message_id,
            received_at=saved.received_at,
            archived_at=datetime.utcnow(),
            archived_by="test",
        )
        session.add(document)
        session.add(order)

    linked = get_uploaded_pdf(uploaded_pdf["id"])

    assert linked is not None
    assert linked["current_document_id"] is None
    assert linked["current_order_id"] is None
    with session_scope() as session:
        assert session.get(Order, "ORD-ARCHIVED-UPLOAD-LINK").current_document_id == "DOC-ARCHIVED-UPLOAD-LINK"
        assert session.get(OrderDocument, "DOC-ARCHIVED-UPLOAD-LINK").status == "processed"


def test_upload_missing_week_cannot_create_filename_derived_placeholder():
    order_service.clear_all()
    saved = _saved_file(
        message_id="upload:sha256:worker-placeholder-order",
        filename="11.fax000353442_0405_.pdf",
    )
    with pytest.raises(ValueError, match="^week_hint_required$"):
        create_uploaded_pdf_from_upload(saved=saved, facility_hint=None,
            week_hint=None, facility_name=None, skip_ocr=False, source_kind="manual_upload")
    with session_scope() as session:
        assert session.query(Order).count() == 0
        assert session.query(OrderDocument).count() == 0
        assert session.query(UploadedPdf).count() == 0
        assert session.get(IngestJob, saved.message_id) is None


def test_backfill_uploaded_pdfs_from_ingest_jobs_creates_recoverable_rows():
    order_service.clear_all()
    with session_scope() as session:
        session.add(
            IngestJob(
                id="upload:sha256:legacy-processing",
                status="processing",
                payload={
                    "message_id": "upload:sha256:legacy-processing",
                    "pdf_uri": "gs://bucket/manual-uploads/2026/04/06/legacy-processing.pdf",
                    "received_at": "2026-04-06T00:04:43.617807",
                    "facility_hint": "FAC00002",
                    "week_hint": "2026-04@2026-04-05~2026-04-11",
                    "source_kind": "manual_upload",
                    "original_filename": "legacy-processing.pdf",
                    "content_sha256": "sha-legacy-processing",
                },
                attempts=1,
                started_at=datetime.utcnow() - timedelta(hours=2),
                updated_at=datetime.utcnow() - timedelta(hours=2),
            )
        )

    with session_scope() as session:
        original_count = session.query(UploadedPdf).count()
        job = session.get(IngestJob, "upload:sha256:legacy-processing")
        original_payload = dict(job.payload)
        original_started_at = job.started_at
        original_updated_at = job.updated_at

    with pytest.raises(ValueError, match="^original_uploaded_pdf_receipt_required$"):
        backfill_uploaded_pdfs_from_ingest_jobs(limit=20)

    with session_scope() as session:
        assert session.query(UploadedPdf).count() == original_count
        job = session.get(IngestJob, "upload:sha256:legacy-processing")
        assert job.payload == original_payload
        assert job.status == "processing"
        assert job.attempts == 1
        assert job.started_at == original_started_at
        assert job.updated_at == original_updated_at


def test_run_uploaded_pdf_recovery_once_processes_ready_rows(monkeypatch):
    seen_limits: list[int] = []
    submitted_ids: list[str] = []
    stale_reset_limits: list[int] = []

    monkeypatch.setattr(
        ingest_worker.ingest_job_service,
        "reset_stale_processing",
        lambda limit: stale_reset_limits.append(limit) or [],
    )
    monkeypatch.setattr(ingest_worker, "backfill_uploaded_pdfs_from_ingest_jobs", lambda: pytest.fail("retired automatic legacy backfill"))
    monkeypatch.setattr(ingest_worker, "run_ocr_job_recovery_once", lambda limit: 0)
    monkeypatch.setattr(
        ingest_worker,
        "list_ready_uploaded_pdf_ids",
        lambda limit: seen_limits.append(limit) or ["UPL-a", "UPL-b"],
    )
    monkeypatch.setattr(
        ingest_worker,
        "_submit_uploaded_pdf_job",
        lambda uploaded_pdf_id: submitted_ids.append(uploaded_pdf_id) or True,
    )

    processed = ingest_worker.run_uploaded_pdf_recovery_once(limit=7)

    assert processed == 2
    assert stale_reset_limits == [7]
    assert seen_limits == [7]
    assert submitted_ids == ["UPL-a", "UPL-b"]


def test_wait_for_pipeline_output_on_ingest_defaults_false_without_http_trigger(monkeypatch):
    monkeypatch.delenv("OCR_PIPELINE_WAIT_FOR_OUTPUT_ON_INGEST", raising=False)
    monkeypatch.delenv("OCR_PIPELINE_URL", raising=False)

    assert ingest_worker._wait_for_pipeline_output_on_ingest() is False


def test_wait_for_pipeline_output_on_ingest_defaults_false_with_http_trigger(monkeypatch):
    monkeypatch.delenv("OCR_PIPELINE_WAIT_FOR_OUTPUT_ON_INGEST", raising=False)
    monkeypatch.setenv("OCR_PIPELINE_URL", "https://ocr-pipeline.example.test")

    assert ingest_worker._wait_for_pipeline_output_on_ingest() is False


def test_wait_for_pipeline_output_on_ingest_can_be_enabled_explicitly(monkeypatch):
    monkeypatch.setenv("OCR_PIPELINE_WAIT_FOR_OUTPUT_ON_INGEST", "true")
    monkeypatch.delenv("OCR_PIPELINE_URL", raising=False)

    assert ingest_worker._wait_for_pipeline_output_on_ingest() is True


def test_unowned_inline_async_first_pass_stops_before_provider_or_write(monkeypatch):
    order_service.clear_all()
    before = []
    with session_scope() as session:
        before = [session.query(model).count() for model in (Order, OrderDocument, OcrJob)]
    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", lambda **kwargs: pytest.fail("unowned provider execution"))
    with pytest.raises(ValueError, match="^ingest_execution_lease_required$"):
        ingest_worker._process_ingest_inline(
            message_id="upload:sha256:async-first-pass", pdf_uri="gs://bucket/manual-uploads/async-first-pass.pdf",
            received_at=datetime.utcnow().isoformat(), facility_hint="FAC00001",
            week_hint="2026-06@2026-06-07~2026-06-13", facility_name=None, date_hints=[],
            skip_ocr=False, source_kind="manual_upload", original_filename="async-first-pass.pdf",
            content_sha256="sha-async-first-pass", ocr_job_id="OCR-ORDasyncfirstpass", order_id="ORDasyncfirstpass")
    with session_scope() as session:
        assert [session.query(model).count() for model in (Order, OrderDocument, OcrJob)] == before


def test_run_uploaded_pdf_recovery_once_resets_stale_ingest_jobs_before_listing(monkeypatch):
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        ingest_worker.ingest_job_service,
        "reset_stale_processing",
        lambda limit: calls.append(("reset", limit)) or ["job-stale-1"],
    )
    monkeypatch.setattr(
        ingest_worker,
        "backfill_uploaded_pdfs_from_ingest_jobs",
        lambda: pytest.fail("retired automatic legacy backfill"),
    )
    monkeypatch.setattr(
        ingest_worker,
        "run_ocr_job_recovery_once",
        lambda limit: calls.append(("owned_ocr_recovery", limit)) or 0,
    )
    monkeypatch.setattr(
        ingest_worker,
        "list_ready_uploaded_pdf_ids",
        lambda limit: calls.append(("list", limit)) or ["UPL-a"],
    )
    monkeypatch.setattr(
        ingest_worker,
        "_submit_uploaded_pdf_job",
        lambda uploaded_pdf_id: calls.append(("submit", uploaded_pdf_id)) or True,
    )

    processed = ingest_worker.run_uploaded_pdf_recovery_once(limit=3)

    assert processed == 1
    assert calls == [
        ("reset", 3),
        ("owned_ocr_recovery", 3),
        ("list", 3),
        ("submit", "UPL-a"),
    ]


@pytest.mark.parametrize("job_id,input_uri,output_uri,status", [
    ("OCR-upload:sha256:ocr-recover-done", "gs://bucket/manual-uploads/2026/04/06/ocr-recover-done.pdf", "gs://bucket/output/ocr-recover-done.json", "awaiting_output"),
    ("OCR-upload:sha256:ocr-recover-retry", "gs://bucket/original.pdf", "gs://bucket/output/missing.json", "awaiting_output"),
    ("OCR-upload:sha256:ocr-recover-stale-running", "gs://bucket/original-stale.pdf", "gs://bucket/output/stale-running.json", "running"),
])
def test_legacy_recovery_without_saved_original_stops_unchanged(monkeypatch, job_id, input_uri, output_uri, status):
    order_service.clear_all()
    create_job(job_id, input_reference=input_uri)
    update_job(job_id, status=status, output_reference=output_uri, metrics={
        "request_mode": "ingest_first_pass", "processing_stage": "ocr_pipeline",
        "facility_id": "FAC00002", "preferred_template_id": "fax_layout_regular_forbidden_v1",
        "preferred_template_ids": ["fax_layout_regular_forbidden_v1"], "auto_recovery_count": 0,
        "stage_updated_at": (datetime.utcnow() - timedelta(minutes=30)).isoformat(),
        "next_recovery_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat(),
    }, error_message="ocr_output_pending" if status == "awaiting_output" else None)
    before = get_job(job_id)
    monkeypatch.setattr(ingest_worker, "load_bytes_from_uri", lambda uri: pytest.fail("unowned original read"))
    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", lambda **kwargs: pytest.fail("unowned recovery resubmit"))
    assert ingest_worker.run_ocr_job_recovery_once(limit=5) == 0
    assert get_job(job_id) == before
    with session_scope() as session:
        assert session.query(Order).count() == 0
        assert session.query(OrderDocument).count() == 0





def test_ocr_job_recovery_ignores_terminal_failed_jobs_before_limit(monkeypatch):
    order_service.clear_all()
    with session_scope() as session:
        session.query(OcrJob).delete()

    for index in range(3):
        job_id = f"OCR-terminal-{index}"
        create_job(job_id, input_reference=f"gs://bucket/terminal-{index}.pdf")
        update_job(
            job_id,
            status="failed",
            output_reference=f"gs://bucket/output/terminal-{index}.json",
            metrics={
                "request_mode": "ingest_first_pass",
                "processing_stage": "ocr_pipeline",
                "result_state": "hard_failed",
                "auto_recovery_count": 3,
                "stage_updated_at": (datetime.utcnow() - timedelta(days=60, minutes=index)).isoformat(),
                "next_recovery_at": None,
            },
            error_message="ocr_recovery_exhausted:OCR pipeline HTTP 429: Rate exceeded.",
        )

    create_job("OCR-active-awaiting", input_reference="gs://bucket/active.pdf")
    update_job(
        "OCR-active-awaiting",
        status="awaiting_output",
        output_reference="gs://bucket/output/active-missing.json",
        metrics={
            "request_mode": "ingest_first_pass",
            "facility_id": "FAC00002",
            "preferred_template_id": "fax_layout_regular_forbidden_v1",
            "preferred_template_ids": ["fax_layout_regular_forbidden_v1"],
            "auto_recovery_count": 0,
            "stage_updated_at": (datetime.utcnow() - timedelta(minutes=10)).isoformat(),
            "next_recovery_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat(),
        },
        error_message="ocr_output_pending",
    )

    monkeypatch.setattr(
        ingest_worker,
        "load_bytes_from_uri",
        lambda uri: b"%PDF-1.4" if uri == "gs://bucket/active.pdf" else (_ for _ in ()).throw(FileNotFoundError(uri)),
    )
    monkeypatch.setattr(
        ingest_worker,
        "run_ocr_pipeline",
        lambda **_kwargs: {
            "status": "running",
            "input_reference": "gs://bucket/active.pdf",
            "output_reference": "gs://bucket/output/active-recovered.json",
        },
    )

    recoverable = list_recoverable_jobs(limit=1)

    assert [job["id"] for job in recoverable] == ["OCR-active-awaiting"]
    before = get_job("OCR-active-awaiting")
    processed = ingest_worker.run_ocr_job_recovery_once(limit=1)
    assert processed == 0
    job = get_job("OCR-active-awaiting")
    assert job is not None
    assert job["output_reference"] == "gs://bucket/output/active-missing.json"
    assert job == before
    for index in range(3):
        assert get_job(f"OCR-terminal-{index}")["status"] == "failed"
