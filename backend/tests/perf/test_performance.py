import os
import pathlib
import sys
import time

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope
from src.models.menu import MonthlyMenu, MonthlyMenuItem
from src.services import menu_service, order_service
from src.services.output_builder import build_outputs
from src.workers.ingest_mail_adapter import IngestEmailPayload
from tests.helpers.newnarrowbaggingfixture import selected_bagging_order, accepted_bagging_order, authored_tabular_invoice


def _perf_enabled() -> bool:
    return os.getenv("RUN_PERF_TESTS") == "1"


def _p95(durations: list[float]) -> float:
    if not durations:
        return 0.0
    durations_sorted = sorted(durations)
    index = max(int(len(durations_sorted) * 0.95) - 1, 0)
    return durations_sorted[index]


@pytest.mark.skipif(not _perf_enabled(), reason="set RUN_PERF_TESTS=1 to run perf tests")
def test_output_performance(monkeypatch, tmp_path, isolated_formal_schema):
    """Authored load component, not a recognition or customer-PDF benchmark."""
    order_count = int(os.getenv("PERF_ORDER_COUNT", "100"))
    line_count = int(os.getenv("PERF_LINES_PER_ORDER", "10"))
    assert order_count > 0 and line_count > 0
    max_p95 = float(os.getenv("PERF_OUTPUT_P95_SECONDS", "120"))
    max_total = float(os.getenv("PERF_OUTPUT_TOTAL_SECONDS", "1800"))
    # Each quantity has a distinct declared physical cell. Never collapse the old
    # repeated (diet, area) identities into the last of their 1/2/3 quantities.
    lines = [{
        "line_id": f"perf-line-{i}", "date": "2025-01-06", "daypart": "朝",
        "menu_name": "Menu A", "diet_type": "regular", "area_id": f"ARE{i + 1:04d}",
        "bag_type": "standard", "quantity_original": 1 + (i % 3),
    } for i in range(line_count)]
    areas = {line["area_id"]: line["area_id"] for line in lines}
    invoice = authored_tabular_invoice(tmp_path, quantity_columns=[
        {"name": line["area_id"], "source": "quantity", "diet_type": line["diet_type"],
         "area_id": line["area_id"]} for line in lines])
    inputs = dict(menu_qty_per_serving=100,
        menu_unit_type="g", menu_temp_type="hot", area_names=areas, invoice_template=invoice)
    facilities = [f"perf-component-{index:04d}" for index in range(order_count)]
    originals = {}
    for facility in facilities:
        originals[facility] = selected_bagging_order(monkeypatch, tmp_path, lines, **inputs,
            facility_id=facility, prepare_only=True)
    assert len(originals) == order_count
    durations = []
    created = set()
    start_total = time.monotonic()
    for idx, facility in enumerate(facilities):
        order = accepted_bagging_order(monkeypatch, tmp_path, lines, **inputs,
            facility_id=facility, prepared_sources=True, prepared_original=originals[facility],
            message_id=f"perf-{idx}", confirm=True)
        assert order["id"] not in created
        created.add(order["id"])
        actual = order_service.get_order_by_id(order["id"])["lines"]
        assert len(actual) == line_count
        assert sorted((line["area_id"], line["quantity_original"]) for line in actual) == sorted(
            (line["area_id"], line["quantity_original"]) for line in lines)
        start = time.monotonic()
        build_outputs(order["id"])
        durations.append(time.monotonic() - start)
    total = time.monotonic() - start_total
    assert len(created) == order_count
    assert _p95(durations) <= max_p95
    assert total <= max_total
