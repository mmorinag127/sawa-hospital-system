import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import order_service, ocr_evidence_service  # noqa: E402


def _sample_payload(table_raw: str = "|a|b|\n|-|-|\n|1|2|") -> dict:
    return {
        "status": "done",
        "engine": "yomitoku",
        "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
        "facility_id": "FAC00001",
        "input_reference": "gs://bucket/input/sample.pdf",
        "output_reference": "gs://bucket/output/sample.json",
        "pages": [
            {
                "page_index": 1,
                "ocr_overlay_uri": "gs://bucket/output/page1_ocr.png",
                "layout_overlay_uri": "gs://bucket/output/page1_layout.png",
            }
        ],
        "combined": {
            "ocr_pdf": "gs://bucket/output/ocr.pdf",
            "layout_pdf": "gs://bucket/output/layout.pdf",
        },
        "table_raw": table_raw,
        "quantity_subgrid_passes": [
            {
                "crop_uri": "gs://bucket/output/qty.png",
                "normalized_rows": [["4", "2"]],
            }
        ],
        "template_resolution": {
            "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "blocked": False,
            "blocked_reasons": [],
        },
        "table_box": [0.1, 0.1, 0.9, 0.9],
        "grid_column_edges": [0.1, 0.5, 0.9],
        "grid_row_edges": [0.1, 0.3, 0.9],
        "page_correction_artifacts": {
            "corrected_pdf_uri": "gs://bucket/output/corrected.pdf",
        },
    }


def test_save_order_ocr_cache_persists_evidence_run():
    order_service.clear_all()

    order_service._save_order_ocr_cache("ORD-EVIDENCE-1", _sample_payload())

    latest = order_service.get_latest_ocr_evidence_run("ORD-EVIDENCE-1")
    assert latest is not None
    assert latest["order_id"] == "ORD-EVIDENCE-1"
    assert latest["schema_version"] == "v1_legacy"
    assert latest["artifact_digest"]
    capabilities = latest.get("capabilities_json") or {}
    assert capabilities.get("step2_view_ready") is True
    assert capabilities.get("step2_edit_ready") is True
    assert capabilities.get("apply_ready") is True
    assert capabilities.get("confirm_ready") is True


def test_save_order_ocr_cache_dedupes_when_only_debug_changes():
    order_service.clear_all()
    payload = _sample_payload()

    order_service._save_order_ocr_cache("ORD-EVIDENCE-2", payload)
    order_service._save_order_ocr_cache(
        "ORD-EVIDENCE-2",
        {
            **payload,
            "_reparse_debug": {"error": "sheet_llm_audit_failed"},
        },
    )

    runs = ocr_evidence_service.list_evidence_runs("ORD-EVIDENCE-2")
    assert len(runs) == 1


def test_save_order_ocr_cache_ignores_draft_only_payload_without_evidence():
    order_service.clear_all()

    order_service._save_order_ocr_cache(
        "ORD-EVIDENCE-3",
        {
            "_edited_ocr": {
                "rows": [["03/22", "朝", "Menu A", "5"]],
            }
        },
    )

    latest = order_service.get_latest_ocr_evidence_run("ORD-EVIDENCE-3")
    assert latest is None
    assert ocr_evidence_service.list_evidence_runs("ORD-EVIDENCE-3") == []


def test_save_order_ocr_cache_creates_new_run_when_evidence_changes():
    order_service.clear_all()

    order_service._save_order_ocr_cache("ORD-EVIDENCE-4", _sample_payload("|a|b|\n|-|-|\n|1|2|"))
    first = order_service.get_latest_ocr_evidence_run("ORD-EVIDENCE-4")
    order_service._save_order_ocr_cache("ORD-EVIDENCE-4", _sample_payload("|a|b|\n|-|-|\n|1|3|"))
    latest = order_service.get_latest_ocr_evidence_run("ORD-EVIDENCE-4")
    runs = ocr_evidence_service.list_evidence_runs("ORD-EVIDENCE-4")

    assert first is not None
    assert latest is not None
    assert len(runs) == 2
    assert first["artifact_digest"] != latest["artifact_digest"]
