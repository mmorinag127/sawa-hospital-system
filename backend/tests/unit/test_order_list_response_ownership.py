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
        return rows

    monkeypatch.setattr(order_service, "_fetch_orders", fetch)
    yield SimpleNamespace(clock=clock, rows=rows, calls=calls)
    order_service.invalidate_orders_cache()


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
    monkeypatch.setattr(orders_api, "get_ocr_jobs", lambda ids: {})
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
    assert len(mounted_cache.calls) == 1


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
    assert len(mounted_cache.calls) == 1


def test_actual_handler_propagates_fetch_exception(cache_world, monkeypatch):
    error = TimeoutError("owned endpoint fetch failure")

    def unavailable(*args, **kwargs):
        raise error

    monkeypatch.setattr(order_service, "_fetch_orders", unavailable)
    with pytest.raises(TimeoutError) as caught:
        orders_api.list_orders(include_runtime=False, limit=None)
    assert caught.value is error
