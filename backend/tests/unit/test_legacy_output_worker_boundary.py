"""Source-authored boundary cases; no broker, database or output execution required."""
from importlib import import_module
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.fixture
def boundary(monkeypatch):
    effects = {}
    for name, attribute in (("src.services.output_builder", "build_outputs"),
                            ("src.services.order_service", "set_status")):
        module = ModuleType(name)
        effects[attribute] = Mock(side_effect=AssertionError("Unowned domain operation"))
        setattr(module, attribute, effects[attribute])
        monkeypatch.setitem(sys.modules, name, module)
    worker = import_module("src.workers.output_worker")
    effects["send_task"] = Mock(side_effect=ConnectionError("Authored broker failure"))
    monkeypatch.setattr(worker.celery_app, "send_task", effects["send_task"])
    effects["retry"] = Mock(side_effect=AssertionError("Business retry forbidden"))
    monkeypatch.setattr(worker.generate_outputs, "retry", effects["retry"])
    yield worker, effects
    for effect in effects.values():
        effect.assert_not_called()


@pytest.mark.parametrize("mode", [None, "false", "true"])
@pytest.mark.parametrize("order_id", ["original-order", "superseded-order", ""])
def test_enqueue_rejects_before_acceptance_without_broker_or_inline(boundary, monkeypatch, mode, order_id):
    worker, _effects = boundary
    if mode is None:
        monkeypatch.delenv("USE_CELERY", raising=False)
    else:
        monkeypatch.setenv("USE_CELERY", mode)
    with pytest.raises(worker.OriginalOutputExecutionRequired, match="recorded original execution version is required"):
        worker.enqueue_outputs(order_id)
    assert not hasattr(worker, "_run_outputs_inline")


@pytest.mark.parametrize("retries", [0, 1, 3, 99])
def test_actual_registered_legacy_delivery_fails_without_retry_or_domain_writes(boundary, retries):
    worker, _effects = boundary
    task = worker.generate_outputs
    assert task.name == "backend.src.workers.output_worker.generate_outputs"
    assert worker.celery_app.tasks[task.name] is task._get_current_object()
    assert task.max_retries == 0
    assert task.autoretry_for == ()
    assert worker.celery_app.conf.task_routes["backend.src.workers.output_worker.*"] == {"queue": "exports"}
    task.push_request(retries=retries)
    try:
        with pytest.raises(worker.OutputBuildError, match="No output was generated") as failure:
            task.run("old-delivered-order")
        assert type(failure.value) is worker.OriginalOutputExecutionRequired
    finally:
        task.pop_request()
