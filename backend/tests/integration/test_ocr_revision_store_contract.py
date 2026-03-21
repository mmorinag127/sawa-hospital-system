import pathlib
import sys
from datetime import datetime

from sqlalchemy import delete

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import order_service  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402
from src.services.ocr_sheet_revision_service import normalize_sheet_revision_snapshot  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models.order_ocr_cache import OrderOcrCache  # noqa: E402


def _seed_order(*, message_id: str):
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 1, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-01",
    )
    return order_service.create_order_from_ingest(payload)


def _build_revision_payload(idx: int):
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
    header = ["日付", "区分", "メニュー", "常食2F", "備考"]
    rows = [[f"01/0{idx + 1}", "朝", f"Menu {idx}", "5", f"note-{idx}"]]
    row_ids = [f"row-{idx}-1"]
    return fields, header, rows, row_ids


def _build_digests(*, fields, header, rows, row_ids):
    before = order_service._sheet_digest(
        fields=fields,
        header=header,
        rows_payload=[["", "", "", "", ""]],
        row_ids=row_ids,
    )
    after = order_service._sheet_digest(
        fields=fields,
        header=header,
        rows_payload=rows,
        row_ids=row_ids,
    )
    return before, after


def _clear_ocr_cache(order_id: str) -> None:
    with session_scope() as session:
        session.execute(delete(OrderOcrCache).where(OrderOcrCache.order_id == order_id))


def test_append_edited_revision_is_reflected_in_edit_history():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-rev-1")
    _clear_ocr_cache(order["id"])

    fields, header, rows, row_ids = _build_revision_payload(1)
    before, after = _build_digests(
        fields=fields,
        header=header,
        rows=rows,
        row_ids=row_ids,
    )
    order_service._append_edited_ocr_revision(
        order_id=order["id"],
        ui_mode="sheet",
        fields=fields,
        header=header,
        rows_payload=rows,
        row_ids=row_ids,
        before_digest=before,
        after_digest=after,
        revision_meta={"review_state": "draft_ready"},
    )

    history, error = order_service.get_ocr_edit_history(order["id"])
    assert error is None
    assert history["latest"] is not None
    assert history["latest"]["row_count"] == 1
    assert history["latest"]["review_state"] == "draft_ready"
    assert len(history["revisions"]) == 1
    assert history["revisions"][0]["rows"][0][0] == "01/02"


def test_revision_history_keeps_latest_twenty_revisions_and_overwrites_edited_cache():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-rev-2")
    order_id = order["id"]
    _clear_ocr_cache(order_id)

    for idx in range(21):
        fields, header, rows, row_ids = _build_revision_payload(idx)
        before, after = _build_digests(
            fields=fields,
            header=header,
            rows=rows,
            row_ids=row_ids,
        )
        order_service._append_edited_ocr_revision(
            order_id=order_id,
            ui_mode="sheet",
            fields=fields,
            header=header,
            rows_payload=rows,
            row_ids=row_ids,
            before_digest=before,
            after_digest=after,
            revision_meta={"review_state": "draft_saved"},
        )

    history, error = order_service.get_ocr_edit_history(order_id)
    assert error is None
    revisions = history["revisions"] or []
    assert len(revisions) == 20
    assert revisions[0]["rows"][0][2] == "Menu 1"
    assert revisions[-1]["rows"][0][2] == "Menu 20"
    assert revisions[-1]["row_count"] == 1


def test_normalized_snapshot_is_written_for_revision_rows():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-rev-3")
    fields, header, rows, row_ids = _build_revision_payload(4)

    snapshot = normalize_sheet_revision_snapshot(
        fields=fields,
        header=header,
        rows_payload=rows,
        row_ids=row_ids,
        field_label=lambda field: field,
        field_value_to_str=str,
    )
    assert snapshot["fields"] == fields
    assert snapshot["header"] == header
    assert snapshot["rows"] == rows
    assert snapshot["row_ids"] == row_ids
