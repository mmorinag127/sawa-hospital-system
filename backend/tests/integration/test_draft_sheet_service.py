import pathlib
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.models.order import OrderLine  # noqa: E402
from src.services import draft_sheet_service, ocr_evidence_service, order_service  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


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
        "template_resolution": {
            "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "blocked": False,
            "blocked_reasons": [],
        },
        "table_box": [0.1, 0.2, 0.9, 0.8],
        "grid_column_edges": [0.1, 0.5, 0.9],
        "grid_row_edges": [0.2, 0.4, 0.8],
    }


def test_build_initial_sheet_draft_prefers_latest_saved_draft() -> None:
    order_service.clear_all()
    order = _seed_order("msg-draft-saved")

    draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "manual_draft",
            "fields": ["col1"],
            "header": ["数量"],
            "rows": [["9"]],
            "row_ids": ["row-1"],
        },
        draft_state="draft_ready",
        edited_by="tester",
    )

    built = draft_sheet_service.build_initial_sheet_draft(order["id"])

    assert isinstance(built, dict)
    assert built["source"] == "manual_draft"
    assert built["rows"] == [["9"]]


def test_build_initial_sheet_draft_from_latest_evidence_run() -> None:
    order_service.clear_all()
    order = _seed_order("msg-draft-evidence")

    evidence = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("5"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )

    built = draft_sheet_service.build_initial_sheet_draft(order["id"])

    assert isinstance(evidence, dict)
    assert isinstance(built, dict)
    assert built["source"] == "ocr_evidence"
    assert built["header"] == ["日付", "区分", "メニュー", "常食2F"]
    assert built["rows"] == [["03/22", "朝", "Menu A", "5"]]
    assert built["base_evidence_run_id"] == evidence["id"]


def test_build_initial_sheet_draft_falls_back_to_legacy_cache_revision() -> None:
    order_service.clear_all()
    order = _seed_order("msg-draft-legacy")

    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|03/22|朝|Menu A|3|",
            "_edited_ocr": {
                "latest": {
                    "ui_mode": "sheet",
                    "fields": ["date_mmdd", "daypart", "menu", "qty"],
                    "header": ["日付", "区分", "メニュー", "常食2F"],
                    "rows": [["03/22", "朝", "Menu A", "8"]],
                    "row_ids": ["row-a"],
                }
            },
        },
    )

    built = draft_sheet_service.build_initial_sheet_draft(order["id"])

    assert isinstance(built, dict)
    assert built["source"] == "edited_sheet"
    assert built["rows"] == [["03/22", "朝", "Menu A", "8"]]


def test_order_service_build_initial_sheet_draft_prefers_semantic_sheet(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-draft-semantic-sheet")

    monkeypatch.setattr(
        order_service,
        "get_ocr_sheet",
        lambda _order_id, **_kwargs: (
            {
                "source": "weekly_menu+ocr_payload",
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
                "header": ["日付", "区分", "メニュー", "常食2F"],
                "rows": [["03/22", "朝", "Menu A", "6"]],
                "row_ids": ["semantic-1"],
                "warnings": ["sheet_ocr_review_required"],
            },
            None,
        ),
    )

    built = order_service.build_initial_sheet_draft(order["id"])

    assert isinstance(built, dict)
    assert built["source"] == "weekly_menu+ocr_payload"
    assert built["fields"] == ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    assert built["header"] == ["日付", "区分", "メニュー", "常食2F"]
    assert built["rows"] == [["03/22", "朝", "Menu A", "6"]]
    assert built["row_ids"] == ["semantic-1"]


def test_get_latest_sheet_draft_upgrades_generic_cols_from_semantic_sheet(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-generic-draft-upgrade")

    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "ocr_evidence",
            "fields": ["col1", "col2", "col3", "col4"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
        draft_state="draft_ready",
        edited_by="tester",
    )
    assert isinstance(saved, dict)

    monkeypatch.setattr(
        order_service,
        "get_ocr_sheet",
        lambda _order_id, **_kwargs: (
            {
                "source": "weekly_menu+ocr_payload",
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
                "header": ["日付", "区分", "メニュー", "常食2F"],
                "rows": [["03/22", "朝", "Menu A", "6"]],
                "row_ids": ["semantic-1"],
                "warnings": [],
            },
            None,
        ),
    )

    upgraded = order_service.get_latest_sheet_draft(
        order["id"],
        backfill_from_revision=True,
        upgrade_generic_from_sheet=True,
    )

    assert isinstance(upgraded, dict)
    draft_json = upgraded["draft_sheet_json"]
    assert draft_json["fields"] == ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    assert draft_json["rows"] == [["03/22", "朝", "Menu A", "6"]]


def test_build_initial_sheet_draft_uses_recoverable_semantic_payload(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-recoverable-semantic")

    monkeypatch.setattr(
        order_service,
        "get_ocr_sheet",
        lambda _order_id, **_kwargs: (None, "template_unresolved"),
    )
    monkeypatch.setattr(
        order_service,
        "build_recoverable_ocr_sheet_payload",
        lambda _order_id, _error: (
            {
                "source": "review_blocked",
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"],
                "header": ["日付", "区分", "メニュー", "常食"],
                "rows": [["03/22", "朝", "Menu A", "6"]],
                "row_ids": ["semantic-1"],
                "warnings": ["template_unresolved"],
            },
            None,
        ),
    )

    built = order_service.build_initial_sheet_draft(order["id"])

    assert isinstance(built, dict)
    assert built["fields"] == ["date_mmdd", "daypart", "menu", "qty.regular_x"]
    assert built["rows"] == [["03/22", "朝", "Menu A", "6"]]
    assert built["source"] == "review_blocked"


def test_get_latest_sheet_draft_upgrades_generic_cols_from_recoverable_semantic_sheet(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-generic-draft-recoverable-upgrade")

    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "ocr_evidence",
            "fields": ["col1", "col2", "col3", "col4"],
            "header": ["日付", "区分", "メニュー", "常食"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
        draft_state="draft_ready",
        edited_by="tester",
    )
    assert isinstance(saved, dict)

    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id, **_kwargs: (None, "template_unresolved"))
    monkeypatch.setattr(
        order_service,
        "build_recoverable_ocr_sheet_payload",
        lambda _order_id, _error: (
            {
                "source": "review_blocked",
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"],
                "header": ["日付", "区分", "メニュー", "常食"],
                "rows": [["03/22", "朝", "Menu A", "7"]],
                "row_ids": ["semantic-1"],
                "warnings": ["template_unresolved"],
            },
            None,
        ),
    )

    upgraded = order_service.get_latest_sheet_draft(
        order["id"],
        backfill_from_revision=True,
        upgrade_generic_from_sheet=True,
    )

    assert isinstance(upgraded, dict)
    draft_json = upgraded["draft_sheet_json"]
    assert draft_json["fields"] == ["date_mmdd", "daypart", "menu", "qty.regular_x"]
    assert draft_json["rows"] == [["03/22", "朝", "Menu A", "7"]]


def test_rerun_ocr_evidence_only_persists_new_evidence_without_overwriting_current_draft(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-rerun-evidence-only")
    first = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("3"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )
    assert isinstance(first, dict)
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "3"]],
            "row_ids": ["row-1"],
        },
        edited_by="tester",
    )
    assert isinstance(saved, dict)
    assert saved["base_evidence_run_id"] == first["id"]

    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda _uri: b"%PDF-rerun%")
    monkeypatch.setattr(
        order_service,
        "run_ocr_pipeline",
        lambda **_kwargs: {
            **_sample_payload("9"),
            "status": "done",
            "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "output_reference": "gs://bucket/output.json",
        },
    )

    rerun, error = order_service.rerun_ocr_evidence_only(order["id"])

    assert error is None
    assert isinstance(rerun, dict)
    assert rerun["id"] != first["id"]

    current_draft = order_service.get_latest_sheet_draft(order["id"], backfill_from_revision=True)
    assert isinstance(current_draft, dict)
    assert current_draft["id"] == saved["id"]
    assert current_draft["base_evidence_run_id"] == first["id"]

    latest_evidence = order_service.get_latest_ocr_evidence_run(order["id"], backfill_from_cache=True)
    assert isinstance(latest_evidence, dict)
    assert latest_evidence["id"] == rerun["id"]

    with session_scope() as session:
        lines = session.query(OrderLine).filter(OrderLine.order_id == order["id"]).all()
        assert len(lines) == 1
        assert lines[0].quantity_original == 3


def test_rerun_ocr_evidence_only_maps_failed_partial_output_to_pipeline_failure(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-rerun-evidence-failed-partial")

    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda _uri: b"%PDF-rerun%")
    monkeypatch.setattr(
        order_service,
        "run_ocr_pipeline",
        lambda **_kwargs: {
            "status": "failed",
            "stage": "error",
            "error": "template resolution failed",
            "input_reference": "gs://bucket/input.pdf",
            "output_reference": "gs://bucket/output.json",
        },
    )

    rerun, error = order_service.rerun_ocr_evidence_only(order["id"])

    assert rerun is None
    assert error == "ocr_pipeline_failed"
    job = order_service.get_ocr_job(f"OCR-{order['id']}")
    assert isinstance(job, dict)
    assert job["status"] == "failed"
    assert str(job.get("error_message") or "").startswith("ocr_pipeline_failed:")
    latest_evidence = order_service.get_latest_ocr_evidence_run(order["id"], backfill_from_cache=False)
    assert latest_evidence is None


def test_rerun_ocr_evidence_only_maps_empty_done_output_to_evidence_unusable(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-rerun-evidence-empty-output")

    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda _uri: b"%PDF-rerun%")
    monkeypatch.setattr(
        order_service,
        "run_ocr_pipeline",
        lambda **_kwargs: {
            "status": "done",
            "stage": "done",
            "input_reference": "gs://bucket/input.pdf",
            "output_reference": "gs://bucket/output.json",
        },
    )

    rerun, error = order_service.rerun_ocr_evidence_only(order["id"])

    assert rerun is None
    assert error == "evidence_unusable"
    job = order_service.get_ocr_job(f"OCR-{order['id']}")
    assert isinstance(job, dict)
    assert job["status"] == "failed"
    assert str(job.get("error_message") or "").startswith("evidence_unusable")


def test_rerun_ocr_evidence_only_rejects_partial_pipeline_output(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-rerun-evidence-partial")
    first = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("3"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )
    assert isinstance(first, dict)
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "3"]],
            "row_ids": ["row-1"],
        },
        edited_by="tester",
    )
    assert isinstance(saved, dict)

    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda _uri: b"%PDF-rerun%")
    monkeypatch.setattr(
        order_service,
        "run_ocr_pipeline",
        lambda **_kwargs: {
            "status": "running",
            "stage": "ocr",
            "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "output_reference": "gs://bucket/output.json",
        },
    )

    rerun, error = order_service.rerun_ocr_evidence_only(order["id"])

    assert rerun is None
    assert error == "evidence_unusable"
    current_draft = order_service.get_latest_sheet_draft(order["id"], backfill_from_revision=True)
    assert isinstance(current_draft, dict)
    assert current_draft["id"] == saved["id"]
    latest_evidence = order_service.get_latest_ocr_evidence_run(order["id"], backfill_from_cache=True)
    assert isinstance(latest_evidence, dict)
    assert latest_evidence["id"] == first["id"]


def test_switch_draft_to_latest_evidence_explicitly_adopts_new_candidate(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-switch-evidence-adopt")
    first = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("3"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )
    assert isinstance(first, dict)
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "3"]],
            "row_ids": ["row-1"],
        },
        edited_by="tester",
    )
    assert isinstance(saved, dict)
    second = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("8"),
        schema_version="v2_evidence_rerun",
        producer_version="rerun",
        source="ocr-rerun",
    )
    assert isinstance(second, dict)

    monkeypatch.setattr(
        order_service,
        "get_ocr_sheet",
        lambda _order_id, use_saved_draft=True, evidence_run_override=None: (
            {
                "source": "weekly_menu+ocr_payload",
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
                "header": ["日付", "区分", "メニュー", "常食2F"],
                "rows": [["03/22", "朝", "Menu A", "8"]],
                "row_ids": ["row-1"],
                "base_evidence_run_id": second["id"],
            },
            None,
        ),
    )

    switched, error = order_service.switch_draft_to_latest_evidence(order["id"], edited_by="switch-test")

    assert error is None
    assert isinstance(switched, dict)
    assert switched["base_evidence_run_id"] == second["id"]
    assert switched["draft_sheet_json"]["rows"] == [["03/22", "朝", "Menu A", "8"]]
