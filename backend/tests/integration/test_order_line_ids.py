import pathlib
import sys
from datetime import date, datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.models.order import Order, OrderLine  # noqa: E402
from src.services import order_service  # noqa: E402


def test_ensure_unique_line_ids_replaces_ids_that_collide_with_existing_rows(tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-line-id-collision.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    colliding_id = "OLNbf2abd"
    with session_scope() as session:
        session.add(
            Order(
                id="ORDcollision",
                facility_code="FAC00001",
                week_code="2026-02",
                document_uri=str(pdf_path),
                message_id="msg-line-id-existing",
                received_at=datetime(2026, 2, 16, 8, 0, 0),
            )
        )
        session.add(
            OrderLine(
                id=colliding_id,
                order_id="ORDcollision",
                date=date(2026, 2, 16),
                daypart="朝",
                menu_name="Collision Menu",
                diet_type="regular",
                area_id="2F",
                bag_type="standard",
                quantity_original=1,
            )
        )

    normalized = order_service._ensure_unique_line_ids(
        [
            {
                "id": colliding_id,
                "date": "2026-02-16",
                "daypart": "朝",
                "menu_name": "Collision Menu",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 2,
            }
        ]
    )

    assert normalized
    assert normalized[0]["id"] != colliding_id
