import os
import pathlib
import sys
import time

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope
from src.models.menu import MenuItem, WeeklyMenu
from src.services import menu_service, order_service
from src.services.output_builder import build_outputs
from src.workers.ingest_mail_adapter import IngestEmailPayload


def _perf_enabled() -> bool:
    return os.getenv("RUN_PERF_TESTS") == "1"


def _p95(durations: list[float]) -> float:
    if not durations:
        return 0.0
    durations_sorted = sorted(durations)
    index = max(int(len(durations_sorted) * 0.95) - 1, 0)
    return durations_sorted[index]


@pytest.mark.skipif(not _perf_enabled(), reason="set RUN_PERF_TESTS=1 to run perf tests")
def test_output_performance():
    order_service.clear_all()
    with session_scope() as session:
        session.query(MenuItem).delete()
        session.query(WeeklyMenu).delete()

    menu = menu_service.create_item_stub("WEK2025W01", "Menu A")
    menu_service.update_item(
        "WEK2025W01",
        menu["id"],
        {"unit_type": "g", "qty_per_serving": 100, "temp_type": "hot", "daypart": "AM"},
    )

    order_count = int(os.getenv("PERF_ORDER_COUNT", "100"))
    line_count = int(os.getenv("PERF_LINES_PER_ORDER", "10"))
    max_p95 = float(os.getenv("PERF_OUTPUT_P95_SECONDS", "120"))
    max_total = float(os.getenv("PERF_OUTPUT_TOTAL_SECONDS", "1800"))

    durations: list[float] = []
    start_total = time.monotonic()
    for idx in range(order_count):
        week_id = f"WEK2025W{idx + 1:02d}"
        payload = IngestEmailPayload(
            message_id=f"perf-{idx}",
            pdf_uri="file:///tmp/perf.pdf",
            received_at=pd.Timestamp("2025-01-05T12:00:00"),
            facility_hint="FAC00001",
            week_hint=week_id,
        )
        lines = [
            {
                "line_id": f"{idx}-{line_idx}",
                "date": "2025-01-06",
                "daypart": "AM",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "ARE0001",
                "bag_type": "standard",
                "quantity_original": 1 + (line_idx % 3),
            }
            for line_idx in range(line_count)
        ]
        order = order_service.create_order_from_ingest(payload, lines=lines)
        start = time.monotonic()
        build_outputs(order["id"])
        durations.append(time.monotonic() - start)

    total = time.monotonic() - start_total
    assert _p95(durations) <= max_p95
    assert total <= max_total
