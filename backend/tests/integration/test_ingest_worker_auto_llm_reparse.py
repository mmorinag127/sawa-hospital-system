import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.workers import ingest_worker  # noqa: E402


class _FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[object, tuple, dict]] = []

    def submit(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        return None


def test_enqueue_auto_llm_reparse_submits_when_first_pass_payload_is_ready(monkeypatch):
    fake_executor = _FakeExecutor()
    monkeypatch.setenv("OCR_AUTO_LLM_REPARSE_ON_INGEST", "1")
    monkeypatch.setenv("OCR_AUTO_LLM_REPARSE_PROVIDER", "gemini")
    monkeypatch.setattr(ingest_worker, "_AUTO_REPARSE_EXECUTOR", fake_executor)
    monkeypatch.setattr(ingest_worker, "get_job", lambda job_id: None)

    ingest_worker._enqueue_auto_llm_reparse(
        {
            "id": "ORD-auto-001",
            "facility": "FAC00001",
            "document": "gs://bucket/test.pdf",
        },
        ocr_status="success",
        pipeline_output={"table_raw": "|日付|区分|献立|常食|\n|---|---|---|---|\n|03/20|朝|Menu A|7|"},
    )

    assert len(fake_executor.calls) == 1
    fn, args, kwargs = fake_executor.calls[0]
    assert fn == ingest_worker._run_auto_llm_reparse
    assert args == ("ORD-auto-001",)
    assert kwargs == {"provider": "gemini"}


def test_enqueue_auto_llm_reparse_skips_when_reparse_job_is_already_active(monkeypatch):
    fake_executor = _FakeExecutor()
    monkeypatch.setenv("OCR_AUTO_LLM_REPARSE_ON_INGEST", "1")
    monkeypatch.setattr(ingest_worker, "_AUTO_REPARSE_EXECUTOR", fake_executor)
    monkeypatch.setattr(
        ingest_worker,
        "get_job",
        lambda job_id: {"id": job_id, "status": "running", "metrics": {}, "updated_at": None},
    )
    monkeypatch.setattr(
        ingest_worker,
        "describe_job_state",
        lambda job: {"status": "running", "job_id": job.get("id")},
    )

    ingest_worker._enqueue_auto_llm_reparse(
        {
            "id": "ORD-auto-002",
            "facility": "FAC00001",
            "document": "gs://bucket/test.pdf",
        },
        ocr_status="success",
        pipeline_output={"pages": [{"page_index": 1}]},
    )

    assert fake_executor.calls == []
