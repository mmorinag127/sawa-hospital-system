import pathlib
import sys
from copy import deepcopy

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


def _assert_unowned_cache_cannot_publish(order_id, payload):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot
    before = _snapshot(get_engine())
    raw = deepcopy(payload)
    assert order_service._save_order_ocr_cache(order_id, payload) is None
    assert _snapshot(get_engine()) == before
    assert payload == raw
    assert order_service.get_latest_ocr_evidence_run(order_id) is None
    assert ocr_evidence_service.list_evidence_runs(order_id) == []


def test_unowned_legacy_cache_cannot_publish_evidence():
    order_service.clear_all()

    _assert_unowned_cache_cannot_publish("ORD-EVIDENCE-1", _sample_payload())


def test_debug_changes_cannot_grant_unowned_cache_evidence_authority():
    order_service.clear_all()
    payload = _sample_payload()

    _assert_unowned_cache_cannot_publish("ORD-EVIDENCE-2", payload)
    _assert_unowned_cache_cannot_publish("ORD-EVIDENCE-2", {
            **payload,
            "_reparse_debug": {"error": "sheet_llm_audit_failed"},
        })


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


def test_changed_legacy_quantity_cannot_create_new_evidence():
    order_service.clear_all()

    for value in ("2", "3"):
        _assert_unowned_cache_cannot_publish("ORD-EVIDENCE-4", _sample_payload(f"|a|b|\n|-|-|\n|1|{value}|"))
