"""Read-work reductions preserve exact JSON and never become write receipts."""
from types import SimpleNamespace

import pytest

from src.services import snapshot_serialization as snapshots
from src.services import system_process_log_service as logs
from src.services.ingest_delivery_service import _receipt


def test_native_snapshot_avoids_round_trip_without_changing_types(monkeypatch):
    def forbidden(_value):
        raise AssertionError("native JSON was normalized again")
    monkeypatch.setattr(snapshots, "_json_snapshot", forbidden)
    assert snapshots.canonical_snapshot_json({"b": (False, 1, 1.0), "a": None}) == '{"a": null, "b": [false, 1, 1.0]}'
    assert not snapshots.snapshots_equal({"x": True}, {"x": 1})
    assert not snapshots.snapshots_equal({"x": 1.0}, {"x": 1})


def test_ocr_log_summary_never_loads_deferred_metrics():
    class Row(SimpleNamespace):
        @property
        def metrics(self):
            raise AssertionError("summary loaded full metrics")
    row = Row(id="job", status="done", input_reference="source", updated_at=None, created_at=None,
              error_message=None, output_reference=None, template_id="template")
    result = logs._ocr_job_row(row, include_details=False)
    assert result["id"] == "ocr:job" and result["summary"] == "template"
    assert result["details"] is None
    with pytest.raises(AssertionError, match="full metrics"):
        logs._ocr_job_row(row)


def test_terminal_lineage_projection_cannot_validate_a_pipeline_write():
    with pytest.raises(ValueError, match="terminal_read_evidence_is_not_pipeline_write_proof"):
        _receipt(None, None, None, _read_evidence={})


def test_workflow_read_creates_one_pure_analysis_per_request(monkeypatch):
    from src.services import order_workflow_v2_service as workflow
    from src.services.canonical_ocr_artifact_service import _EvidenceAnalysis

    seen = []
    order, state = SimpleNamespace(id="read-order"), SimpleNamespace(order_id="read-order")

    def serialize(session, **options):
        assert options["_read_context"] == (order, state)
        seen.append(options["_analysis"])
        return {"context_suggestion": {"present": True}, "blockers": []}

    def resolution(session, actual_order, actual_state, **options):
        assert actual_order is order and actual_state is state
        seen.append(options["_analysis"])
        return {"status": "resolved"}

    monkeypatch.setattr(workflow, "_serialize_workflow_checked", serialize)
    monkeypatch.setattr(workflow, "_workflow_menu_amount_resolution", resolution)
    for _ in range(2):
        result = workflow._workflow_read_projection(object(), order, state)
        assert result["menu_amount_resolution"] == {"status": "resolved"}
    assert all(isinstance(analysis, _EvidenceAnalysis) for analysis in seen)
    assert seen[0] is seen[1] and seen[2] is seen[3]
    assert seen[0] is not seen[2]


def test_workflow_metadata_borrow_is_explicit_and_editors_keep_owned_copies():
    from src.services import order_workflow_v2_service as workflow

    stored = {"ocr_job_id": "job", "nested": {"source": ["original"]}}
    state = SimpleNamespace(secondary_actions_json={workflow.WORKFLOW_V2_META_KEY: stored})
    borrowed = workflow._workflow_meta(state, copy_meta=False)
    owned = workflow._workflow_meta(state)
    assert borrowed is stored and owned == stored and owned is not stored
    owned["nested"]["source"].append("editor change")
    assert stored["nested"]["source"] == ["original"]


def test_terminal_read_borrows_internal_payload_but_persister_owns_copy(monkeypatch):
    from src.services import ingest_job_service

    captured = {"job_id": "job", "input_reference": "original.pdf", "execution_id": "execution",
                "ingest_lease": {"id": "ingest"}, "orders": {"order": {"original": ["value"]}}}
    terminal = {"job_id": "job", "execution_id": "execution", "status": "done"}
    job = SimpleNamespace(id="job", input_reference="original.pdf", status="done",
                          metrics={"nonworkflow_execution": captured})
    row = SimpleNamespace(owner_lease={"id": "ingest"})
    monkeypatch.setattr(ingest_job_service, "ingest_terminal_receipt", lambda *args, **kwargs: terminal)
    kind, read = _receipt(None, row, job, validate_pipeline=False, _read_evidence={})
    assert kind == "terminal" and read["capture"] is captured and read["receipt"] is terminal
    kind, write = _receipt(None, row, job)
    assert kind == "terminal" and write == read
    write["capture"]["orders"]["order"]["original"].append("persister change")
    write["receipt"]["status"] = "changed"
    assert captured["orders"]["order"]["original"] == ["value"]
    assert terminal["status"] == "done"
