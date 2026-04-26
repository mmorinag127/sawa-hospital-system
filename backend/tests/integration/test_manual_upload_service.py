import pathlib
import sys
import time
from datetime import datetime

import fitz
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import manual_upload_service  # noqa: E402
from src.services.manual_upload_service import ManualUploadSavedFile  # noqa: E402


def _two_page_pdf_bytes() -> bytes:
    doc = fitz.open()
    try:
        first = doc.new_page()
        first.insert_text((72, 72), "FAX page one")
        second = doc.new_page()
        second.insert_text((72, 72), "FAX page two")
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()


def _placeholder_pdf_bytes(*page_texts: str) -> bytes:
    doc = fitz.open()
    try:
        for text in page_texts:
            page = doc.new_page()
            page.insert_text((72, 72), text)
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()


def _meaningful_single_page_pdf_bytes() -> bytes:
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), "2026/04/15 FAX order sheet")
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()


def test_save_uploaded_pdf_pages_preserves_page_order_when_parallel_saves_finish_out_of_order(monkeypatch):
    observed_page_numbers: list[int] = []

    def _slow_save_pdf_variant(**kwargs):
        page_number = int(kwargs["page_number"])
        if page_number == 1:
            time.sleep(0.05)
        else:
            time.sleep(0.01)
        observed_page_numbers.append(page_number)
        return ManualUploadSavedFile(
            message_id=f"msg-{page_number}",
            pdf_uri=f"file://page-{page_number}.pdf",
            content_sha256=f"sha-{page_number}",
            original_filename=str(kwargs["original_filename"]),
            received_at=kwargs["received_at"],
            page_number=page_number,
            total_pages=int(kwargs["total_pages"]),
            split_group_id=str(kwargs["split_group_id"]),
        )

    monkeypatch.setattr(manual_upload_service, "_save_pdf_variant", _slow_save_pdf_variant)
    monkeypatch.setenv("MANUAL_UPLOAD_SAVE_WORKERS", "4")

    saved = manual_upload_service.save_uploaded_pdf_pages(
        pdf_bytes=_two_page_pdf_bytes(),
        original_filename="multi.pdf",
        received_at=datetime(2026, 4, 15, 10, 0, 0),
    )

    assert observed_page_numbers == [2, 1]
    assert [item.page_number for item in saved] == [1, 2]
    assert all(item.total_pages == 2 for item in saved)


def test_validate_pdf_bytes_rejects_placeholder_page_marker_pdf():
    with pytest.raises(ValueError, match="placeholder/test PDF"):
        manual_upload_service._validate_pdf_bytes(_placeholder_pdf_bytes("Page 1"))


def test_validate_pdf_bytes_accepts_meaningful_single_page_pdf():
    manual_upload_service._validate_pdf_bytes(_meaningful_single_page_pdf_bytes())
