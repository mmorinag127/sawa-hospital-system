import pathlib
import sys
import time

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
