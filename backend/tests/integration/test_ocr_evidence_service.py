import pathlib
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.orders as orders_api  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models.facility_template_version import FacilityTemplateVersion  # noqa: E402
from src.models.order import Order  # noqa: E402
from src.models.order_ocr_cache import OrderOcrCache  # noqa: E402
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun  # noqa: E402
from src.services import draft_sheet_service, ocr_evidence_service, order_service, template_resolution_service  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402
from src.workers import ingest_worker  # noqa: E402
from tests.contract.test_orders_ocr_sheet_history_api import history_case  # noqa: E402,F401

app = FastAPI()
app.include_router(orders_api.router, prefix="/orders")

pytestmark = pytest.mark.usefixtures("isolated_formal_schema")


def _sample_payload(quantity: str = "3") -> dict:
    return {
        "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
        "pages": [
            {
                "page_index": 1,
                "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                "figure_uris": [],
            }
        ],
        "table_raw": f"|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|03/22|朝|Menu A|{quantity}|",
        "tables": [
            {
                "table_id": "p1_t1",
                "page_index": 1,
                "rows": [["日付", "区分", "メニュー", "常食2F"], ["03/22", "朝", "Menu A", quantity]],
            }
        ],
        "quantity_subgrid_passes": [],
        "template_resolution": {
            "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "blocked": False,
            "blocked_reasons": [],
        },
        "table_box": [0.1, 0.2, 0.9, 0.8],
        "grid_column_edges": [0.1, 0.5, 0.9],
        "grid_row_edges": [0.2, 0.4, 0.8],
    }


@pytest.fixture
def registration_only_order(monkeypatch, tmp_path):
    from datetime import date
    from src.services.canonical_facility_store import read_snapshot
    from src.services.ingest_facility_selection_service import capture_selection
    from src.services.manual_upload_service import ManualUploadSavedFile
    from src.services.uploaded_pdf_service import create_uploaded_pdf_from_upload
    from tests.helpers.ingest_original import (
        authored_sources, configure_delivery, reserve_uploaded_original, register_authored_original,
    )

    configure_delivery(monkeypatch)

    def forbidden_provider(*args, **kwargs):
        pytest.fail("Registration-only evidence fixture must not execute OCR")

    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", forbidden_provider)

    def register(message_id):
        template_id = "fax_layout_regular_soft_mixer_forbidden_v1"
        columns = [
            {"index": 0, "source_index": 0, "role": "date", "name": "date_mmdd"},
            {"index": 1, "source_index": 1, "role": "daypart", "name": "daypart"},
            {"index": 2, "source_index": 2, "role": "menu_name", "name": "menu"},
            {"index": 3, "source_index": 3, "role": "quantity", "name": "qty.regular_2f",
             "diet_type": "regular", "area_id": "2F"},
        ]
        # Authored registry-metadata component, not a live facility definition.
        facility = {"facility_id": "FAC00001", "facility_name": "Authored evidence component",
            "areas": [{"area_id": "2F", "name": "Second floor"}],
            "fax_template_id": template_id,
            "fax_template": {"template_id": template_id, "page": 1, "units": "normalized", "columns": columns,
                "table_box": [0.1, 0.2, 0.9, 0.8],
                "grid_column_edges": [0.1, 0.3, 0.5, 0.7, 0.9], "grid_row_edges": [0.2, 0.4, 0.8]}}
        uri, digest = authored_sources(monkeypatch, tmp_path, facility_id="FAC00001",
            menu_date=date(2026, 3, 22), daypart="breakfast", facility_config=facility)
        saved = ManualUploadSavedFile(message_id=message_id, pdf_uri=uri, content_sha256=digest,
            original_filename="original.pdf", received_at=datetime(2026, 3, 22, 9, 0, 0))
        selection = capture_selection("FAC00001", "2026-03-22", read_snapshot().revision,
            "authored-original", "2026-03@2026-03-22~2026-03-28")
        uploaded, _ = create_uploaded_pdf_from_upload(saved=saved, facility_hint="FAC00001",
            week_hint="2026-03@2026-03-22~2026-03-28", facility_name=None,
            skip_ocr=False, source_kind="manual_upload", facility_selection=selection)
        receipt = reserve_uploaded_original(uploaded["id"], effective_date=date(2026, 3, 22))
        assert receipt["id"] == saved.message_id
        order = register_authored_original(uploaded["id"], effective_date=date(2026, 3, 22))
        with session_scope() as session:
            persisted = session.get(Order, order["id"])
            assert persisted is not None and persisted.template_version_id is not None
            assert session.get(FacilityTemplateVersion, persisted.template_version_id) is not None
            assert session.query(OrderOcrEvidenceRun).filter_by(order_id=order["id"]).all() == []
        return order

    return register


def test_owned_native_runs_with_identical_values_keep_independent_execution_identity(history_case):
    case = history_case()
    records = []
    for quantity in ("3", "3", "9"):
        case.produce(cell_values=quantity)
        record = ocr_evidence_service.get_evidence_run(case.evidence)
        assert record is not None
        execution = record["payload_json"]["_ocr_execution"]
        assert execution["job_id"] == case.job
        assert execution["evidence_run_id"] == record["id"]
        recognized = record["payload_json"]["hakodate_ocr_evidence"]["records"]
        assert recognized and {item["raw_text"] for item in recognized} == {quantity}
        records.append(record)
    assert len({record["id"] for record in records}) == 3
    assert len({record["payload_json"]["_ocr_execution"]["job_id"] for record in records}) == 3
    for record in records:
        assert ocr_evidence_service.get_evidence_run(record["id"])["payload_json"] == record["payload_json"]


@pytest.mark.parametrize("source,schema", [("legacy-cache-mirror", "v1_legacy"),
                                           ("ocr-rerun-hakodate", "v2_hakodate_evidence_rerun")])
def test_payload_labels_cannot_replace_owned_execution(registration_only_order, source, schema):
    order_service.clear_all()
    order = registration_only_order("msg-evidence-dedupe-identity")

    from copy import deepcopy
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    payload = _sample_payload("3")
    original = deepcopy(payload)
    before = _snapshot(get_engine())
    with pytest.raises(ValueError, match="^configured_execution_input_changed$"):
        ocr_evidence_service.persist_evidence_run(order_id=order["id"], payload=payload,
            schema_version=schema, producer_version="unowned-component", source=source)
    assert payload == original
    assert _snapshot(get_engine()) == before


def test_persist_evidence_run_blocks_changed_captured_template_version(history_case):
    case = history_case()
    case.prepare_provider(cell_values="3")
    # Explicit corrupted projection: absence must not be repaired from the cache.
    with session_scope() as session:
        session.get(Order, case.id).template_version_id = None
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    before = _snapshot(get_engine())
    with pytest.raises(ValueError, match="^configured_execution_input_changed$"):
        with case.execution():
            ocr_evidence_service.persist_evidence_run(order_id=case.id, payload=_sample_payload("3"),
                schema_version="v2_hakodate_evidence_rerun", producer_version="test", source="test",
                template_version_id=case.queued["ocr_job"]["template_version_id"],
                workflow_job_id=case.job,
                workflow_attempt_id=case.queued["ocr_job"]["workflow_attempt_id"],
                expected_workflow_revision=case.queued["workflow"]["revision"])
    assert ocr_evidence_service.get_latest_evidence_run(case.id) is None
    assert _snapshot(get_engine()) == before


def test_save_order_ocr_cache_does_not_create_legacy_mirror_for_hakodate_payload(registration_only_order):
    order_service.clear_all()
    order = registration_only_order("msg-hakodate-cache-no-legacy-mirror")
    payload = {
        "status": "done",
        "stage": "done",
        "engine": "hakodate_best_method_pipeline",
        "hakodate_preprocessing": {
            "target_cell_map": [
                {
                    "target_cell_id": "D11",
                    "sheet_cell": "D11",
                    "worksheet_row": 11,
                    "worksheet_col": 4,
                    "semantic_field": "qty.regular_2f",
                    "bbox": [90.0, 190.0, 110.0, 210.0],
                    "center": [100.0, 200.0],
                    "source": "hakodate_best_method_pipeline",
                }
            ]
        },
        "hakodate_ocr_evidence_records": [
            {
                "evidence_id": "hakodate-cell-1",
                "text": "9",
                "normalized_value": "9",
                "center": [100.0, 200.0],
                "engine": "yomitoku_contact_sheet_batch",
                "source_scope": "hakodate_cell_crop_batch",
            }
        ],
    }

    order_service._save_order_ocr_cache(  # noqa: SLF001
        order["id"],
        payload,
        augment_hakodate_artifacts=False,
        persist_evidence=True,
        refresh_workflow=False,
    )

    assert ocr_evidence_service.get_latest_evidence_run(order["id"]) is None


def test_classify_evidence_payload_marks_failed_partial_output_unpersistable():
    result = ocr_evidence_service.classify_evidence_payload(
        {
            "status": "failed",
            "stage": "error",
            "error": "template resolution failed",
            "input_reference": "gs://bucket/input.pdf",
            "output_reference": "gs://bucket/output.json",
        }
    )

    assert result["persistable"] is False
    assert result["error"] == "ocr_pipeline_failed"
    assert result["stage"] == "error"


def test_classify_evidence_payload_marks_empty_done_output_unpersistable():
    result = ocr_evidence_service.classify_evidence_payload(
        {
            "status": "done",
            "stage": "done",
            "input_reference": "gs://bucket/input.pdf",
            "output_reference": "gs://bucket/output.json",
        }
    )

    assert result["persistable"] is False
    assert result["error"] == "evidence_unusable"


def test_classify_evidence_payload_rejects_unreceipted_hakodate_looking_artifacts():
    result = ocr_evidence_service.classify_evidence_payload(
        {
            "status": "done",
            "stage": "done",
            "engine": "hakodate_best_method_pipeline",
            "hakodate_preprocessing": {
                "target_cell_map": [
                    {
                        "target_cell_id": "D11",
                        "sheet_cell": "D11",
                        "worksheet_row": 11,
                        "worksheet_col": 4,
                        "semantic_field": "qty.regular_2f",
                        "bbox": [90.0, 190.0, 110.0, 210.0],
                        "center": [100.0, 200.0],
                        "source": "hakodate_best_method_pipeline",
                    }
                ]
            },
            "hakodate_ocr_evidence_records": [
                {
                    "evidence_id": "hakodate-cell-1",
                    "text": "9",
                    "normalized_value": "9",
                    "center": [100.0, 200.0],
                    "engine": "yomitoku_contact_sheet_batch",
                    "source_scope": "hakodate_cell_crop_batch",
                }
            ],
        }
    )

    assert result["persistable"] is False


def test_classify_evidence_payload_accepts_actual_owned_native_artifacts(history_case):
    from copy import deepcopy

    case = history_case()
    case.produce(cell_values="9")
    evidence = ocr_evidence_service.get_evidence_run(case.evidence)
    payload = evidence["payload_json"]
    expected = evidence["artifact_manifest_json"]["original_hakodate_source"]
    original = deepcopy(payload)
    result = ocr_evidence_service.classify_evidence_payload(payload, expected_hakodate_source=expected)
    assert result["persistable"] is True
    assert payload == original


def test_classify_evidence_payload_rejects_hakodate_target_map_without_ocr_evidence():
    result = ocr_evidence_service.classify_evidence_payload(
        {
            "status": "done",
            "stage": "done",
            "engine": "hakodate_best_method_pipeline",
            "hakodate_preprocessing": {
                "target_cell_map": [
                    {
                        "target_cell_id": "D11",
                        "sheet_cell": "D11",
                        "worksheet_row": 11,
                        "worksheet_col": 4,
                        "semantic_field": "qty.regular_2f",
                        "bbox": [90.0, 190.0, 110.0, 210.0],
                        "center": [100.0, 200.0],
                        "source": "hakodate_best_method_pipeline",
                    }
                ]
            },
            "hakodate_ocr_evidence_records": [],
        }
    )

    assert result["persistable"] is False
    assert result["error"] == "evidence_unusable"


def test_get_evidence_endpoint_does_not_backfill_from_cache_when_run_missing(registration_only_order):
    order_service.clear_all()
    client = TestClient(app)
    order = registration_only_order("msg-evidence-endpoint-no-backfill")
    with session_scope() as session:
        session.add(OrderOcrCache(order_id=order["id"], payload=_sample_payload("5")))

    res = client.get(f"/orders/{order['id']}/evidence")

    assert res.status_code == 404
    assert order_service.get_latest_ocr_evidence_run(order["id"]) is None
    with session_scope() as session:
        rows = (
            session.query(OrderOcrEvidenceRun)
            .filter(OrderOcrEvidenceRun.order_id == order["id"])
            .all()
        )
    assert rows == []


def test_get_latest_ocr_evidence_run_default_does_not_backfill_from_cache(registration_only_order):
    order_service.clear_all()
    order = registration_only_order("msg-evidence-service-no-backfill")
    with session_scope() as session:
        session.add(OrderOcrCache(order_id=order["id"], payload=_sample_payload("4")))

    latest = order_service.get_latest_ocr_evidence_run(order["id"])

    assert latest is None
    with session_scope() as session:
        rows = (
            session.query(OrderOcrEvidenceRun)
            .filter(OrderOcrEvidenceRun.order_id == order["id"])
            .all()
        )
    assert rows == []


def test_legacy_cache_backfill_option_is_disabled(registration_only_order):
    order_service.clear_all()
    order = registration_only_order("msg-evidence-service-backfill-disabled")
    with session_scope() as session:
        session.add(OrderOcrCache(order_id=order["id"], payload=_sample_payload("4")))

    with pytest.raises(order_service.LegacyOcrEvidenceFallbackDisabledError):
        order_service.get_latest_ocr_evidence_run(order["id"], backfill_from_cache=True)

    with session_scope() as session:
        rows = (
            session.query(OrderOcrEvidenceRun)
            .filter(OrderOcrEvidenceRun.order_id == order["id"])
            .all()
        )
    assert rows == []


def test_legacy_ocr_output_fallback_options_are_disabled(registration_only_order):
    import inspect

    parameters = inspect.signature(order_service.get_ocr_output).parameters
    assert "allow_legacy_fallback" not in parameters
    assert all(parameter.kind != inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())

    order_service.clear_all()
    order = registration_only_order("msg-ocr-output-fallback-disabled")
    with session_scope() as session:
        session.add(OrderOcrCache(order_id=order["id"], payload=_sample_payload("6")))

    with pytest.raises(TypeError, match=r"^get_ocr_output\(\) got an unexpected keyword argument 'allow_legacy_fallback'$"):
        order_service.get_ocr_output(order["id"], allow_legacy_fallback=True)
    with pytest.raises(order_service.LegacyOcrEvidenceFallbackDisabledError):
        order_service.get_ocr_output(order["id"], allow_job_reconcile=True)

    with session_scope() as session:
        rows = (
            session.query(OrderOcrEvidenceRun)
            .filter(OrderOcrEvidenceRun.order_id == order["id"])
            .all()
        )
    assert rows == []


def test_get_ocr_output_endpoint_does_not_promote_cache_only_payload_to_current(registration_only_order):
    order_service.clear_all()
    client = TestClient(app)
    order = registration_only_order("msg-ocr-output-cache-only-not-current")
    with session_scope() as session:
        session.add(OrderOcrCache(order_id=order["id"], payload=_sample_payload("6")))

    res = client.get(f"/orders/{order['id']}/ocr-output")

    assert res.status_code == 404
    assert order_service.get_latest_ocr_evidence_run(order["id"]) is None
    with session_scope() as session:
        cache = session.get(OrderOcrCache, order["id"])
        cache_payload = cache.payload if cache is not None else None
        rows = (
            session.query(OrderOcrEvidenceRun)
            .filter(OrderOcrEvidenceRun.order_id == order["id"])
            .all()
        )
    assert isinstance(cache_payload, dict)
    assert rows == []


def test_get_document_does_not_use_cache_only_ocr_artifact_as_current_source(registration_only_order):
    order_service.clear_all()
    client = TestClient(app)
    order = registration_only_order("msg-document-cache-only-not-current")
    payload = dict(_sample_payload("8"))
    payload["input_reference"] = "file://archived-source.pdf"
    with session_scope() as session:
        session.add(OrderOcrCache(order_id=order["id"], payload=payload))

    pathlib.Path(order["document"].removeprefix("file://")).unlink()
    res = client.get(f"/orders/{order['id']}/document")

    assert res.status_code == 409
    assert res.json()["detail"] == "selected_pdf_verification_failed"
    assert order_service.get_latest_ocr_evidence_run(order["id"]) is None
    with session_scope() as session:
        rows = (
            session.query(OrderOcrEvidenceRun)
            .filter(OrderOcrEvidenceRun.order_id == order["id"])
            .all()
        )
    assert rows == []


def test_legacy_draft_sheet_endpoint_is_disabled_after_evidence_persistence(monkeypatch, tmp_path):
    from datetime import date
    from src.services.canonical_facility_store import read_snapshot
    from src.services.ingest_facility_selection_service import capture_selection
    from src.models.order import OrderLine
    from src.services.manual_upload_service import ManualUploadSavedFile
    from src.services.uploaded_pdf_service import create_uploaded_pdf_from_upload
    from tests.helpers.ingest_original import (
        authored_sources, configure_delivery, register_authored_original,
        reserve_uploaded_original, process_uploaded_original, authored_pipeline_provider,
    )

    order_service.clear_all()
    client = TestClient(app)
    configure_delivery(monkeypatch)
    uri, digest = authored_sources(
        monkeypatch, tmp_path, facility_id="FAC00001",
        menu_date=date(2026, 3, 22), daypart="breakfast",
    )
    saved = ManualUploadSavedFile(
        message_id="msg-draft-endpoint-build", pdf_uri=uri, content_sha256=digest,
        original_filename="original.pdf", received_at=datetime(2026, 3, 22, 9, 0, 0),
    )
    selection = capture_selection("FAC00001", "2026-03-22", read_snapshot().revision,
        "authored-original", "2026-03@2026-03-22~2026-03-28")
    uploaded, _ = create_uploaded_pdf_from_upload(
        saved=saved, facility_hint="FAC00001",
        week_hint="2026-03@2026-03-22~2026-03-28", facility_name=None,
        skip_ocr=False, source_kind="manual_upload", facility_selection=selection,
    )
    receipt = reserve_uploaded_original(uploaded["id"], effective_date=date(2026, 3, 22))
    assert receipt["id"] == saved.message_id
    registered = register_authored_original(uploaded["id"], effective_date=date(2026, 3, 22))
    authored_pipeline_provider(monkeypatch, table_rows=[["03/22", "朝", "Menu A", "5"]])
    process_uploaded_original(uploaded["id"])

    order = order_service.find_order_by_message_id("msg-draft-endpoint-build")
    assert isinstance(order, dict)
    assert order["id"] == registered["id"]
    evidence = order_service.get_latest_ocr_evidence_run(order["id"])
    assert isinstance(evidence, dict)
    assert evidence["producer_version"] == "ocr_pipeline_ingest"
    with session_scope() as session:
        persisted = session.get(OrderOcrEvidenceRun, evidence["id"])
        assert persisted is not None and persisted.order_id == order["id"]
        line = session.query(OrderLine).filter_by(order_id=order["id"]).one()
        assert line.quantity_original == 5

    res = client.get(f"/orders/{order['id']}/draft-sheet")

    assert res.status_code == 410
    assert res.json()["detail"]["error"] == "legacy_order_workflow_disabled"


def test_retired_draft_sheet_refuses_semantic_shell_selection_without_side_effect(history_case):
    from tests.contract.test_orders_draft_review_api import _refuse
    f = history_case()
    f.produce()
    _refuse(f, "GET", "draft-sheet", None)


def test_process_ingest_inline_persists_evidence_run_from_pipeline_output(monkeypatch, tmp_path):
    from datetime import date
    from src.services.canonical_facility_store import read_snapshot
    from src.services.ingest_facility_selection_service import capture_selection
    from src.models.order import OrderLine
    from src.services.manual_upload_service import ManualUploadSavedFile
    from src.services.uploaded_pdf_service import create_uploaded_pdf_from_upload
    from tests.helpers.ingest_original import (
        authored_sources, configure_delivery, register_authored_original,
        reserve_uploaded_original, process_uploaded_original, authored_pipeline_provider,
    )

    order_service.clear_all()
    configure_delivery(monkeypatch)
    # Synthetic component sources and external OCR double, not verified FAX/operator OCR.
    uri, digest = authored_sources(
        monkeypatch, tmp_path, facility_id="FAC00001",
        menu_date=date(2026, 3, 22), daypart="breakfast",
    )
    saved = ManualUploadSavedFile(
        message_id="msg-evidence-inline-ingest", pdf_uri=uri, content_sha256=digest,
        original_filename="original.pdf", received_at=datetime(2026, 3, 22, 10, 0, 0),
    )
    selection = capture_selection("FAC00001", "2026-03-22", read_snapshot().revision,
        "authored-original", "2026-03@2026-03-22~2026-03-28")
    uploaded, _ = create_uploaded_pdf_from_upload(
        saved=saved, facility_hint="FAC00001",
        week_hint="2026-03@2026-03-22~2026-03-28", facility_name=None,
        skip_ocr=False, source_kind="manual_upload", facility_selection=selection,
    )
    receipt = reserve_uploaded_original(uploaded["id"], effective_date=date(2026, 3, 22))
    assert receipt["id"] == saved.message_id
    register_authored_original(uploaded["id"], effective_date=date(2026, 3, 22))
    authored_pipeline_provider(monkeypatch, table_rows=[["03/22", "朝", "Menu A", "7"]])
    process_uploaded_original(uploaded["id"])

    order = order_service.find_order_by_message_id("msg-evidence-inline-ingest")
    assert isinstance(order, dict)
    evidence = order_service.get_latest_ocr_evidence_run(order["id"])
    assert isinstance(evidence, dict)
    assert evidence["producer_version"] == "ocr_pipeline_ingest"
    assert evidence["capabilities_json"]["step2_view_ready"] is True
    with session_scope() as session:
        line = session.query(OrderLine).filter_by(order_id=order["id"]).one()
        assert line.quantity_original == 7


def test_evidence_record_does_not_invent_grid_from_template_or_classifier_labels():
    payload = _sample_payload("6")
    payload["template_resolution"] = template_resolution_service.build_template_resolution(
        requested_template_id="fax_layout_regular_soft_mixer_forbidden_v1",
        requested_template_ids=[
            "fax_layout_regular_soft_mixer_forbidden_v1",
            "fax_layout_floor_2f3f_v1",
        ],
        resolved_template_id="fax_layout_regular_soft_mixer_forbidden_v1",
        classification={
            "matched_template_id": "fax_layout_floor_2f3f_v1",
            "confidence": 0.94,
            "candidates": [
                {"id": "fax_layout_floor_2f3f_v1", "score": 0.94},
                {"id": "fax_layout_regular_soft_mixer_forbidden_v1", "score": 0.91},
            ],
        },
        page_correction_summary={"pages": [{"mode": "template_warp", "template_id": "fax_layout_regular_soft_mixer_forbidden_v1"}]},
    )
    payload["table_box"] = None
    payload["grid_column_edges"] = []
    payload["grid_row_edges"] = []

    from copy import deepcopy
    before = deepcopy(payload)
    evidence = ocr_evidence_service.build_evidence_run_record(
        order_id="unpersisted-component-record",
        payload=payload,
        schema_version="v2_evidence_rerun",
        producer_version="test",
        source="test",
    )

    assert isinstance(evidence, dict)
    assert evidence["artifact_manifest_json"]["artifacts"]["grid_metadata"] is False
    assert evidence["payload_json"]["table_box"] is None
    assert evidence["payload_json"]["grid_column_edges"] == []
    assert evidence["payload_json"]["grid_row_edges"] == []
    assert evidence["payload_json"]["template_resolution"] == template_resolution_service.normalize_template_resolution_state(payload["template_resolution"])
    assert payload == before
