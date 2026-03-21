import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from fastapi.testclient import TestClient
from src.services import order_service
from src.services import output_builder
from src.main import app  # noqa: E402


def test_status_flow_confirm():
    order_service.clear_all()
    order = order_service.create_order_from_ingest(
        payload=type(
            "obj",
            (),
            {
                "message_id": "m2",
                "pdf_uri": "file://dummy.pdf",
                "received_at": "2025-12-23T10:10:00",
                "facility_hint": "FAC001",
                "week_hint": "WEK2025W52",
            },
        )
    )
    confirmed = order_service.confirm_order(order["id"])
    assert confirmed["status"] == "確定"
    # Outputs should be buildable without error even if empty lines
    output_builder.build_outputs(order["id"])


def test_confirm_endpoint_triggers_outputs(tmp_path):
    order_service.clear_all()
    client = TestClient(app)
    pdf_path = tmp_path / "endpoint.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    with pdf_path.open("rb") as handle:
        res = client.post(
            "/ingest/upload",
            files={"pdf_file": ("endpoint.pdf", handle.read(), "application/pdf")},
            data={"facility_hint": "FAC001", "week_hint": "WEK2025W52"},
        )
    assert res.status_code == 202
    order = order_service.list_orders()[0]
    res2 = client.post(f"/orders/{order['id']}/confirm")
    assert res2.status_code == 202
