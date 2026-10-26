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
