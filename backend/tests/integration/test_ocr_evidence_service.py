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

app = FastAPI()
app.include_router(orders_api.router, prefix="/orders")


def _seed_order(message_id: str) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 3, 22, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-03",
    )
    return order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-03-22",
                "daypart": "朝",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 3,
            }
        ],
    )


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


def _attach_active_template_version(order_id: str, facility_id: str = "FAC00001") -> str:
    template_version_id = f"FTV{order_id[-8:]}"
    with session_scope() as session:
        session.add(
            FacilityTemplateVersion(
                id=template_version_id,
                facility_id=facility_id,
                version="test-active",
                status="active",
                template_id="fax_layout_regular_soft_mixer_forbidden_v1",
                source="test",
                columns_json=[
                    {"index": 0, "role": "date", "header": "日付", "source_index": 0},
                    {"index": 1, "role": "daypart", "header": "区分", "source_index": 1},
                    {"index": 2, "role": "menu_name", "header": "メニュー", "source_index": 2},
                    {
                        "index": 3,
                        "role": "quantity",
                        "header": "常食2F",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "source_index": 3,
                    },
                ],
                cells_json=[],
                template_digest=f"digest-{template_version_id}",
                validation_json={"errors": [], "warnings": []},
                created_at=datetime.utcnow(),
                activated_at=datetime.utcnow(),
            )
        )
        order = session.get(Order, order_id)
        assert order is not None
        order.template_version_id = template_version_id
    return template_version_id


def test_persist_evidence_run_dedupes_identical_payload_digest():
    order_service.clear_all()
    order = _seed_order("msg-evidence-dedupe")
    _attach_active_template_version(order["id"])

    first = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("3"),
        schema_version="v1_legacy",
        producer_version="test",
    )
    second = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("3"),
        schema_version="v1_legacy",
        producer_version="test",
    )
    third = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("9"),
        schema_version="v1_legacy",
        producer_version="test",
    )

    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert isinstance(third, dict)
    assert first["id"] == second["id"]
    assert first["artifact_digest"] == second["artifact_digest"]
    assert third["id"] != first["id"]
    assert third["artifact_digest"] != first["artifact_digest"]


def test_persist_evidence_run_does_not_dedupe_different_record_identity_for_same_digest():
    order_service.clear_all()
    order = _seed_order("msg-evidence-dedupe-identity")
    _attach_active_template_version(order["id"])

    legacy = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("3"),
        schema_version="v1_legacy",
        producer_version="legacy-cache-mirror/v1",
        source="legacy-cache-mirror",
    )
    hakodate = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("3"),
        schema_version="v2_hakodate_evidence_rerun",
        producer_version="hakodate_best_method_pipeline",
        source="ocr-rerun-hakodate",
    )

    assert isinstance(legacy, dict)
    assert isinstance(hakodate, dict)
    assert legacy["artifact_digest"] == hakodate["artifact_digest"]
    assert legacy["id"] != hakodate["id"]
    assert hakodate["created"] is True


def test_persist_evidence_run_blocks_without_template_version():
    order_service.clear_all()
    order = _seed_order("msg-evidence-no-template-version")

    evidence = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("3"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test",
    )

    assert evidence is None
    assert ocr_evidence_service.get_latest_evidence_run(order["id"]) is None


def test_save_order_ocr_cache_does_not_create_legacy_mirror_for_hakodate_payload():
    order_service.clear_all()
    order = _seed_order("msg-hakodate-cache-no-legacy-mirror")
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


def test_classify_evidence_payload_accepts_hakodate_live_artifacts():
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

    assert result["persistable"] is True


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


def test_get_evidence_endpoint_does_not_backfill_from_cache_when_run_missing():
    order_service.clear_all()
    client = TestClient(app)
    order = _seed_order("msg-evidence-endpoint-no-backfill")
    _attach_active_template_version(order["id"])
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


def test_get_latest_ocr_evidence_run_default_does_not_backfill_from_cache():
    order_service.clear_all()
    order = _seed_order("msg-evidence-service-no-backfill")
    _attach_active_template_version(order["id"])
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


def test_legacy_cache_backfill_option_is_disabled():
    order_service.clear_all()
    order = _seed_order("msg-evidence-service-backfill-disabled")
    _attach_active_template_version(order["id"])
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


def test_legacy_ocr_output_fallback_options_are_disabled():
    order_service.clear_all()
    order = _seed_order("msg-ocr-output-fallback-disabled")
    _attach_active_template_version(order["id"])
    with session_scope() as session:
        session.add(OrderOcrCache(order_id=order["id"], payload=_sample_payload("6")))

    with pytest.raises(order_service.LegacyOcrEvidenceFallbackDisabledError):
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


def test_get_ocr_output_endpoint_does_not_promote_cache_only_payload_to_current():
    order_service.clear_all()
    client = TestClient(app)
    order = _seed_order("msg-ocr-output-cache-only-not-current")
    _attach_active_template_version(order["id"])
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


def test_get_document_does_not_use_cache_only_ocr_artifact_as_current_source():
    order_service.clear_all()
    client = TestClient(app)
    order = _seed_order("msg-document-cache-only-not-current")
    _attach_active_template_version(order["id"])
    payload = dict(_sample_payload("8"))
    payload["input_reference"] = "file://archived-source.pdf"
    with session_scope() as session:
        session.add(OrderOcrCache(order_id=order["id"], payload=payload))

    res = client.get(f"/orders/{order['id']}/document")

    assert res.status_code == 404
    assert order_service.get_latest_ocr_evidence_run(order["id"]) is None
    with session_scope() as session:
        rows = (
            session.query(OrderOcrEvidenceRun)
            .filter(OrderOcrEvidenceRun.order_id == order["id"])
            .all()
        )
    assert rows == []


def test_legacy_draft_sheet_endpoint_is_disabled_after_evidence_persistence():
    order_service.clear_all()
    client = TestClient(app)
    order = _seed_order("msg-draft-endpoint-build")
    _attach_active_template_version(order["id"])
    ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("5"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test",
    )

    res = client.get(f"/orders/{order['id']}/draft-sheet")

    assert res.status_code == 410
    assert res.json()["detail"]["error"] == "legacy_order_workflow_disabled"


def test_get_draft_sheet_endpoint_prefers_semantic_shell_over_raw_evidence_when_recovery_warning_remains(monkeypatch):
    order_service.clear_all()
    client = TestClient(app)
    order = _seed_order("msg-draft-endpoint-semantic-shell")

    monkeypatch.setattr(
        order_service,
        "get_latest_sheet_draft",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        order_service,
        "get_ocr_sheet",
        lambda _order_id, **_kwargs: (
            {
                "source": "weekly_menu",
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"],
                "header": ["日付", "区分", "メニュー", "常食"],
                "rows": [["03/22", "朝", "Menu A", ""]],
                "row_ids": ["semantic-1"],
                "warnings": ["ocr_evidence_recovery_required", "sheet_ocr_review_required"],
            },
            None,
        ),
    )
    monkeypatch.setattr(
        draft_sheet_service,
        "build_sheet_draft_from_evidence",
        lambda _order_id: {
            "order_id": _order_id,
            "source": "ocr_evidence",
            "fields": ["col1", "col2", "col3", "col4"],
            "header": ["日付", "区分", "メニュー", "数量"],
            "rows": [["03/22", "朝", "Menu A", "9"]],
            "row_ids": ["raw-1"],
        },
    )

    res = client.get(f"/orders/{order['id']}/draft-sheet")

    assert res.status_code == 200
    payload = res.json()
    assert payload["source"] == "weekly_menu"
    assert payload["fields"] == ["date_mmdd", "daypart", "menu", "qty.regular_x"]
    assert payload["rows"] == [["03/22", "朝", "Menu A", ""]]
    assert payload["draft_sheet_json"]["source"] == "weekly_menu"


def test_process_ingest_inline_persists_evidence_run_from_pipeline_output(monkeypatch):
    order_service.clear_all()
    original_create_order = ingest_worker.create_order_from_ingest

    def create_order_with_template(*args, **kwargs):
        order = original_create_order(*args, **kwargs)
        if isinstance(order, dict):
            _attach_active_template_version(order["id"], facility_id=order.get("facility_code") or "FAC00001")
        return order

    monkeypatch.setattr(ingest_worker.config_service, "load_ingest_policy", lambda: {"ocr_retry_limit": 1, "quantity_rules": {}})
    monkeypatch.setattr(
        ingest_worker.config_service,
        "load_facility_master",
        lambda: {"fax_template_base": {}, "facilities": [{"facility_id": "FAC00001", "facility_name": "施設A"}]},
    )
    monkeypatch.setattr(ingest_worker, "should_skip_ocr", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(ingest_worker, "load_bytes_from_uri", lambda _uri: b"%PDF-1.4 test")
    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", lambda **_kwargs: _sample_payload("7"))
    monkeypatch.setattr(
        ingest_worker,
        "extract_fax_data",
        lambda *_args, **_kwargs: SimpleNamespace(
            facility_name=None,
            date_strings=[],
            tokens=[],
            table_rows=[],
        ),
    )
    monkeypatch.setattr(ingest_worker.config_service, "get_facility_config", lambda _facility_id: None)
    monkeypatch.setattr(ingest_worker, "_enqueue_auto_llm_reparse", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ingest_worker, "create_order_from_ingest", create_order_with_template)

    ingest_worker._process_ingest_inline(
        message_id="msg-evidence-inline-ingest",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 3, 22, 10, 0, 0).isoformat(),
        facility_hint="FAC00001",
        week_hint="2026-03",
    )

    order = order_service.find_order_by_message_id("msg-evidence-inline-ingest")
    assert isinstance(order, dict)
    evidence = order_service.get_latest_ocr_evidence_run(order["id"])
    assert isinstance(evidence, dict)
    assert evidence["producer_version"] == "ocr_pipeline_ingest"
    assert evidence["capabilities_json"]["step2_view_ready"] is True


def test_persist_evidence_run_uses_resolved_template_registry_grid_metadata_when_classifier_differs():
    order_service.clear_all()
    order = _seed_order("msg-evidence-registry-grid")
    _attach_active_template_version(order["id"])
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

    evidence = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=payload,
        schema_version="v2_evidence_rerun",
        producer_version="test",
        source="test",
    )

    assert isinstance(evidence, dict)
    assert evidence["artifact_manifest_json"]["artifacts"]["grid_metadata"] is True
    assert evidence["capabilities_json"]["semantic_shell_only"] is False
    assert evidence["capabilities_json"]["apply_ready"] is True
