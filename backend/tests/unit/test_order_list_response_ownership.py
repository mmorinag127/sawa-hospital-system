"""Owned cache and mounted GET checks; no provider or live latency proof."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import datetime
from threading import Event
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from src.api import orders as orders_api
from src.api.auth import UserContext, get_current_operator
from src.main import app
from src.services import order_service
from src.models.order import Order
from test_ingest_delivery_contract import store


@pytest.fixture
def cache_world(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(order_service, "time", SimpleNamespace(time=lambda: clock.now))
    order_service.invalidate_orders_cache()
    rows = [{"id": "owned-list", "received_at": datetime(2026, 10, 19),
             "ocr_job_id": "OCR-owned-list", "superseded_document_ids": ["original"],
             "metadata": {"zero": 0, "unknown": None, "values": [False, ""]}}]
    calls = []

    def fetch(*args, **kwargs):
        calls.append((args, kwargs))
        return deepcopy(rows)

    monkeypatch.setattr(order_service, "_fetch_orders", fetch)
    yield SimpleNamespace(clock=clock, rows=rows, calls=calls)
    order_service.invalidate_orders_cache()


def test_explicit_stored_facts_http_does_not_load_ocr_ownership(store, monkeypatch):
    with store() as session:
        session.add(Order(id="facts-only", document_uri="fixture://facts-only", message_id="facts-only",
                          facility_code=None, week_code="2026-10", status="要確認"))
        session.commit()
    monkeypatch.setitem(app.dependency_overrides, get_current_operator,
                        lambda: UserContext("operator", "facts@example.invalid", ("hospital",)))
    def forbidden(*args, **kwargs):
        raise AssertionError("stored facts loaded OCR execution")
    monkeypatch.setattr(order_service, "get_recorded_order_job_bindings", forbidden)
    with closing(TestClient(app)) as client:
        response = client.get("/orders", params={"response_view": "stored_facts", "include_runtime": False, "limit": 25})
        assert response.status_code == 200
        row = response.json()["orders"][0]
        assert row["id"] == "facts-only" and row["status"] == "要確認" and row["week"] == "2026-10"
        assert "ocr_job_id" not in row
        assert client.get("/orders", params={"response_view": "stored_facts", "include_runtime": True}).status_code == 422
    with pytest.raises(AssertionError, match="OCR execution"):
        order_service.list_orders_page(limit=25, job_bindings_out={})


def test_unassigned_filter_precedes_cursor_pagination_and_preserves_month_rules(store):
    from datetime import timedelta
    with store() as session:
        for index, (facility, week) in enumerate([
            ("known", "2026-10"), (None, "2026-10"), ("known", "not-a-sheet-month"),
            ("known", "2026-10@2026-10-25~2026-10-31"), ("known", None),
        ]):
            session.add(Order(id=f"facts-{index}", document_uri=f"fixture://facts-{index}",
                message_id=f"facts-{index}", facility_code=facility, week_code=week,
                received_at=datetime(2026, 10, 27) + timedelta(minutes=index)))
        session.commit()
    ids, cursor = [], None
    for _ in range(5):
        page = order_service.list_orders_page(limit=1, only_unassigned=True, include_job_bindings=False,
            before_received_at=cursor["received_at"] if cursor else None, before_id=cursor["id"] if cursor else None)
        ids.extend(row["id"] for row in page["orders"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert ids == ["facts-4", "facts-2", "facts-1"]


def test_candidate_batch_keeps_original_service_errors_and_does_not_return_ocr_body(monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, get_current_operator,
                        lambda: UserContext("operator", "facts@example.invalid", ("hospital",)))
    calls = []
    def output(order_id, *, persist_cache):
        calls.append((order_id, persist_cache))
        if order_id == "pending":
            return None, "ocr_output_pending"
        if order_id == "invalid":
            raise ValueError("authored_fax_template_unresolved")
        return {"facility_candidates": [{"facility_id": "F1", "score": 0.9}], "table_raw": "not-a-candidate-field"}, None
    monkeypatch.setattr(order_service, "get_ocr_output", output)
    with closing(TestClient(app)) as client:
        response = client.get("/orders/facility-candidates", params=[("order_ids", value) for value in ("ready", "pending", "invalid", "ready")])
        assert response.status_code == 200
        results = response.json()["results"]
        assert results["ready"] == {"candidates": [{"facility_id": "F1", "score": 0.9}]}
        assert results["pending"]["error"] == "ocr_output_pending"
        assert results["invalid"]["error"] == "authored_fax_template_unresolved"
        assert "table_raw" not in response.text
        assert client.get("/orders/facility-candidates", params=[("order_ids", str(index)) for index in range(51)]).status_code == 422
    assert calls == [("ready", False), ("pending", False), ("invalid", False)]


def test_miss_store_and_each_hit_have_independent_nested_ownership(cache_world):
    original = deepcopy(cache_world.rows)
    first = order_service.list_orders()
    first[0]["metadata"]["values"].append("first response")
    first[0]["superseded_document_ids"].clear()
    second = order_service.list_orders()
    assert second == original
    second[0]["metadata"]["zero"] = 99
    second.append({"id": "response-only"})
    assert order_service.list_orders() == original
    assert len(cache_world.calls) == 1


def test_store_does_not_borrow_fetch_result(cache_world):
    original = deepcopy(cache_world.rows)
    order_service.list_orders()
    cache_world.rows[0]["metadata"]["values"].append("fetch owner")
    cache_world.rows.clear()
    assert order_service.list_orders() == original


def test_refresh_detaches_store_and_preserves_count_key_and_invalidation(cache_world):
    original = deepcopy(cache_world.rows)
    assert order_service.refresh_orders_cache("confirmed", include_archived=False, limit=25) == 1
    cache_world.rows[0]["metadata"]["values"].append("fetch owner")
    response = order_service.list_orders("confirmed", include_archived=False, limit=25)
    assert response == original
    response[0]["metadata"]["zero"] = 99
    assert order_service.list_orders("confirmed", include_archived=False, limit=25) == original
    assert cache_world.calls == [(("confirmed",), {"include_archived": False, "limit": 25})]
    order_service.invalidate_orders_cache()
    assert order_service.list_orders("confirmed", include_archived=False, limit=25) == cache_world.rows
    assert len(cache_world.calls) == 2


@pytest.mark.parametrize("age,expected_calls", [(29.999, 1), (30.0, 2), (30.001, 2)])
def test_exact_existing_ttl_and_fetch_arguments(cache_world, age, expected_calls):
    original = deepcopy(cache_world.rows)
    assert order_service.list_orders("confirmed", include_archived=False, limit=25) == original
    cache_world.clock.now += age
    assert order_service.list_orders("confirmed", include_archived=False, limit=25) == original
    assert cache_world.calls == [(("confirmed",), {"include_archived": False, "limit": 25})] * expected_calls


@pytest.mark.parametrize("error_type", [TimeoutError, RuntimeError])
@pytest.mark.parametrize("state", ["fresh", "expired", "absent"])
def test_unavailable_fetch_never_returns_stale_or_empty_success(cache_world, monkeypatch, error_type, state):
    if state != "absent":
        order_service.list_orders()
    if state == "expired":
        cache_world.clock.now += 30
    before = deepcopy(order_service._orders_cache)
    error = error_type("owned fetch failure")
    attempts = []

    def unavailable(*args, **kwargs):
        attempts.append((args, kwargs))
        raise error

    monkeypatch.setattr(order_service, "_fetch_orders", unavailable)
    if state == "fresh":
        assert order_service.list_orders() == cache_world.rows
        assert attempts == []
    else:
        with pytest.raises(error_type) as caught:
            order_service.list_orders()
        assert caught.value is error
        assert len(attempts) == 1
    assert order_service._orders_cache == before


@pytest.mark.parametrize("state", ["fresh", "expired", "absent"])
def test_explicit_refresh_propagates_failure_without_replacing_cache(cache_world, monkeypatch, state):
    if state != "absent":
        order_service.list_orders()
    if state == "expired":
        cache_world.clock.now += 30
    before = deepcopy(order_service._orders_cache)
    error = TimeoutError("owned refresh failure")

    def unavailable(*args, **kwargs):
        raise error

    monkeypatch.setattr(order_service, "_fetch_orders", unavailable)
    with pytest.raises(TimeoutError) as caught:
        order_service.refresh_orders_cache()
    assert caught.value is error
    assert order_service._orders_cache == before


@pytest.fixture
def mounted_cache(cache_world, monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, get_current_operator,
                        lambda: UserContext("operator", "owned-list@example.invalid", ("hospital",)))
    monkeypatch.setattr(orders_api, "get_recorded_order_job_bindings", lambda ids: {})
    monkeypatch.setattr(order_service, "_load_order_ocr_cache_map", lambda ids: {})
    monkeypatch.setattr(order_service, "get_cached_ocr_payload", lambda order_id: None)
    monkeypatch.setattr(orders_api.workflow_state_service, "list_workflow_states", lambda ids: {})
    monkeypatch.setattr(orders_api, "_attach_order_workflow_context", lambda *args, **kwargs: None)

    def summary(order, **kwargs):
        order["request_owned"] = {"values": ["review"]}

    monkeypatch.setattr(orders_api, "_attach_order_review_summary", summary)
    return cache_world


def test_real_sequential_gets_do_not_retain_runtime_hydration(mounted_cache):
    with closing(TestClient(app)) as client:
        enriched = client.get("/orders?include_ocr=true")
        plain = client.get("/orders?include_runtime=false")
    assert enriched.status_code == plain.status_code == 200
    assert enriched.json()["orders"][0]["request_owned"] == {"values": ["review"]}
    assert "request_owned" not in plain.json()["orders"][0]
    assert "request_owned" not in order_service.list_orders()[0]
    assert len(mounted_cache.calls) == 3


def test_real_parallel_get_cannot_observe_other_requests_mutation(mounted_cache, monkeypatch):
    order_service.list_orders()
    entered, release = Event(), Event()

    def summary(order, **kwargs):
        order["request_owned"] = {"values": ["in flight"]}
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(orders_api, "_attach_order_review_summary", summary)

    def enriched_get():
        with closing(TestClient(app)) as client:
            return client.get("/orders?include_ocr=true")

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(enriched_get)
        try:
            assert entered.wait(10)
            with closing(TestClient(app)) as client:
                plain = client.get("/orders?include_runtime=false")
        finally:
            release.set()
        enriched = pending.result(timeout=10)
    assert enriched.status_code == plain.status_code == 200
    assert "request_owned" not in plain.json()["orders"][0]
    assert enriched.json()["orders"][0]["request_owned"] == {"values": ["in flight"]}
    assert len(mounted_cache.calls) == 3


def test_actual_handler_propagates_fetch_exception(cache_world, monkeypatch):
    error = TimeoutError("owned endpoint fetch failure")

    def unavailable(*args, **kwargs):
        raise error

    monkeypatch.setattr(order_service, "_fetch_orders", unavailable)
    with pytest.raises(TimeoutError) as caught:
        orders_api.list_orders(include_runtime=False, limit=None)
    assert caught.value is error
