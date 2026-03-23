import pathlib
import sys
from datetime import datetime

from sqlalchemy import delete

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.models.order_sheet_patch_candidate import OrderSheetPatchCandidate  # noqa: E402
from src.models.order import Order  # noqa: E402
from src.services import ocr_patch_candidate_service, order_service  # noqa: E402


def _clear_candidates(*, order_id: str | None = None) -> None:
    with session_scope() as session:
        if order_id:
            session.execute(delete(OrderSheetPatchCandidate).where(OrderSheetPatchCandidate.order_id == order_id))
            return
        session.execute(delete(OrderSheetPatchCandidate))


def _seed_order(order_id: str) -> None:
    order = Order(
        id=order_id,
        facility_code="FAC00001",
        week_code="2026-01",
        document_uri=f"file:///{order_id}.pdf",
        message_id=f"msg-{order_id}",
        received_at=datetime(2026, 1, 8, 9, 0, 0),
    )
    with session_scope() as session:
        if session.get(Order, order_id) is None:
            session.add(order)


def _draft_sheet(quantity: str) -> dict:
    return {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
        "rows": [["01/08", "昼", "Menu A", quantity, "draft"]],
        "row_ids": ["row-1"],
        "ui_mode": "sheet",
        "source": "edited_sheet",
    }


def test_persist_and_get_patch_candidate() -> None:
    order_service.clear_all()
    _seed_order("ORD_PATCH_1")

    stored = ocr_patch_candidate_service.persist_patch_candidate(
        order_id="ORD_PATCH_1",
        base_draft_id="BAS-001",
        base_evidence_run_id="EVD-001",
        provider="gemini",
        model="gemini-2.5-pro",
        prompt_preset="numeric_verification",
        baseline_source="yomitoku",
        baseline_revision_id="BASE-001",
        candidate_state="proposed",
        summary_json={"status": "proposed", "notes": "initial estimate"},
        issues_json=[{"issue_code": "missing_qty", "row_id": "row-1"}],
        patches_json={"applied_overwrites": [{"row_id": "row-1"}], "rejected_overwrites": []},
        proposed_draft_sheet_json=_draft_sheet("10"),
    )

    assert stored is not None
    assert stored["order_id"] == "ORD_PATCH_1"
    assert stored["base_draft_id"] == "BAS-001"
    assert stored["base_evidence_run_id"] == "EVD-001"
    assert stored["candidate_state"] == "proposed"
    assert stored["provider"] == "gemini"
    assert stored["model"] == "gemini-2.5-pro"
    assert stored["summary_json"]["status"] == "proposed"
    assert stored["issues_json"] == [{"issue_code": "missing_qty", "row_id": "row-1"}]
    assert stored["patches_json"]["applied_overwrites"] == [{"row_id": "row-1"}]
    assert stored["proposed_draft_sheet_json"]["rows"][0][3] == "10"

    fetched = ocr_patch_candidate_service.get_patch_candidate("ORD_PATCH_1", stored["id"])
    assert fetched is not None
    assert fetched["id"] == stored["id"]
    assert fetched["candidate_state"] == "proposed"


def test_list_patch_candidates() -> None:
    order_service.clear_all()
    _seed_order("ORD_PATCH_2")
    _clear_candidates(order_id="ORD_PATCH_2")

    first = ocr_patch_candidate_service.persist_patch_candidate(
        order_id="ORD_PATCH_2",
        proposed_draft_sheet_json=_draft_sheet("5"),
    )
    second = ocr_patch_candidate_service.persist_patch_candidate(
        order_id="ORD_PATCH_2",
        candidate_state="review_required",
        proposed_draft_sheet_json=_draft_sheet("6"),
    )
    third = ocr_patch_candidate_service.persist_patch_candidate(
        order_id="ORD_PATCH_2",
        candidate_state="proposed",
        proposed_draft_sheet_json=_draft_sheet("7"),
    )

    all_candidates = ocr_patch_candidate_service.list_patch_candidates("ORD_PATCH_2")
    assert len(all_candidates) == 3
    assert {item["id"] for item in all_candidates} == {first["id"], second["id"], third["id"]}
    states = {item["id"]: item["candidate_state"] for item in all_candidates}
    assert states[first["id"]] == "ready"
    assert states[second["id"]] == "review_required"
    assert states[third["id"]] == "proposed"


def test_mark_patch_candidate_as_applied_and_apply_to_draft() -> None:
    order_service.clear_all()
    _seed_order("ORD_PATCH_3")
    base_draft = order_service.persist_sheet_draft(
        order_id="ORD_PATCH_3",
        draft_sheet_json=_draft_sheet("5"),
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
    )
    assert base_draft is not None

    draft = ocr_patch_candidate_service.persist_patch_candidate(
        order_id="ORD_PATCH_3",
        base_draft_id=base_draft["id"],
        candidate_state="proposed",
        proposed_draft_sheet_json=_draft_sheet("8"),
        summary_json={"status": "verified"},
        issues_json=[],
        patches_json={"applied_overwrites": [], "rejected_overwrites": []},
    )
    assert draft is not None

    applied_result, apply_error = ocr_patch_candidate_service.apply_patch_candidate_to_draft(
        "ORD_PATCH_3",
        patch_candidate_id=draft["id"],
        edited_by="operator",
    )
    assert apply_error is None
    assert applied_result is not None
    assert applied_result["draft"]["draft_sheet_json"]["rows"][0][3] == "8"
    assert applied_result["candidate"]["candidate_state"] == "applied"
    assert applied_result["candidate"]["applied_by"] == "operator"
    assert isinstance(applied_result["candidate"]["applied_at"], str)
