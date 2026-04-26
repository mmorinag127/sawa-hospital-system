import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import order_service  # noqa: E402


def test_run_reparse_with_heartbeat_emits_progress(monkeypatch):
    calls: list[dict] = []

    def _record_progress(job_id: str, **kwargs):
        calls.append({"job_id": job_id, **kwargs})
        return None

    monkeypatch.setattr(order_service, "_update_reparse_job_progress", _record_progress)
    monkeypatch.setattr(order_service, "_read_reparse_float_env", lambda *args, **kwargs: 0.01)
    monkeypatch.setattr(order_service, "_read_reparse_stage_timeout_seconds", lambda _stage: 60.0)

    result = order_service._run_reparse_with_heartbeat(
        "OCR-test-heartbeat",
        processing_stage="inference",
        result_state="processing",
        metrics_patch={"quality_error": None},
        func=lambda: (time.sleep(0.04), "ok")[1],
    )

    assert result == "ok"
    assert calls
    assert all(call["job_id"] == "OCR-test-heartbeat" for call in calls)
    assert any(call.get("processing_stage") == "inference" for call in calls)


def test_run_reparse_with_heartbeat_times_out_stage_and_records_failure(monkeypatch):
    calls: list[dict] = []

    def _record_progress(job_id: str, **kwargs):
        calls.append({"job_id": job_id, **kwargs})
        return None

    monkeypatch.setattr(order_service, "_update_reparse_job_progress", _record_progress)
    monkeypatch.setattr(order_service, "_read_reparse_float_env", lambda *args, **kwargs: 0.01)
    monkeypatch.setattr(order_service, "_read_reparse_stage_timeout_seconds", lambda _stage: 0.03)

    with pytest.raises(TimeoutError, match="reparse_stage_timeout:inference>0s"):
        order_service._run_reparse_with_heartbeat(
            "OCR-test-timeout",
            processing_stage="inference",
            result_state="processing",
            metrics_patch={"quality_error": None},
            func=lambda: (time.sleep(0.08), "late")[1],
        )

    assert any(call.get("status") == "failed" for call in calls)
    assert any(call.get("processing_stage") == "inference_timeout" for call in calls)


def test_run_reparse_with_heartbeat_uses_timeout_override(monkeypatch):
    calls: list[dict] = []

    def _record_progress(job_id: str, **kwargs):
        calls.append({"job_id": job_id, **kwargs})
        return None

    monkeypatch.setattr(order_service, "_update_reparse_job_progress", _record_progress)
    monkeypatch.setattr(order_service, "_read_reparse_float_env", lambda *args, **kwargs: 0.01)
    monkeypatch.setattr(order_service, "_read_reparse_stage_timeout_seconds", lambda _stage: 60.0)

    with pytest.raises(TimeoutError, match="reparse_stage_timeout:validation>0s"):
        order_service._run_reparse_with_heartbeat(
            "OCR-test-timeout-override",
            processing_stage="validation",
            result_state="processing",
            metrics_patch={"quality_error": None},
            timeout_seconds_override=0.03,
            func=lambda: (time.sleep(0.08), "late")[1],
        )

    assert any(call.get("status") == "failed" for call in calls)
    assert any(call.get("processing_stage") == "validation_timeout" for call in calls)


def test_optional_llm_reparse_audit_uses_bounded_timeout_and_returns_unknown(monkeypatch):
    monkeypatch.setattr(order_service, "_update_reparse_job_progress", lambda *args, **kwargs: None)

    def _read_env(name: str, default: float, **kwargs):
        if name == "OCR_REPARSE_HEARTBEAT_SECONDS":
            return 0.01
        if name == "OCR_REPARSE_LLM_AUDIT_TIMEOUT_SECONDS":
            return 0.03
        return default

    monkeypatch.setattr(order_service, "_read_reparse_float_env", _read_env)
    monkeypatch.setattr(order_service, "_run_llm_reparse_audit", lambda **kwargs: (time.sleep(0.08), None)[1])

    result = order_service._run_optional_llm_reparse_audit(
        job_id="OCR-test-optional-audit-timeout",
        processing_stage="validation",
        pdf_bytes=b"%PDF-1.4",
        provider="gemini",
        template={},
        facility_id="FAC00003",
        preferred_template_id=None,
        candidate_rows=[["04/26", "朝", "メニュー", "1"]],
        reference_rows=[["04/26", "朝", "メニュー", "1"]],
        baseline_rows=[["04/26", "朝", "メニュー", "1"]],
        expected_row_count=1,
        metrics_patch={"quality_track": "llm_reparse"},
    )

    assert isinstance(result, dict)
    assert result["status"] == "unknown"
    assert "reparse_stage_timeout:validation>0s" in str(result.get("error") or "")
