import sys
import pathlib
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import order_service  # noqa: E402
from src.services.fax_extractor import FaxExtractedData  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def test_ingest_to_reparse_flow(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\\n%EOF\\n")
    payload = IngestEmailPayload(
        message_id="msg-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 1, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="WEK2026W02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    def _fake_extract(pdf_bytes, template):
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026/01/08"],
            table_rows=[["01/08", "Menu A", "1"]],
            tokens=[],
            grid=None,
            ocr_provider="mock",
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):
        return [
            {
                "date": "2026-01-08",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 2,
            }
        ]

    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)

    updated, error = order_service.reparse_order(order["id"])
    assert error is None
    assert updated is not None
    assert updated["lines"]
