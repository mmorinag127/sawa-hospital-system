import pathlib
import sys
from datetime import datetime, timedelta

from sqlalchemy import delete

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.models.ocr_job import OcrJob  # noqa: E402
from src.services.ocr_quality_service import summarize_reparse_quality  # noqa: E402


def _clear_ocr_jobs() -> None:
    with session_scope() as session:
        session.execute(delete(OcrJob))


def _seed_job(
    *,
    job_id: str,
    provider: str,
    status: str,
    metrics: dict | None = None,
    error_message: str | None = None,
    updated_at: datetime | None = None,
) -> None:
    now = updated_at or datetime.utcnow()
    payload = {"provider": provider, "changed": False}
    if isinstance(metrics, dict):
        payload.update(metrics)
    with session_scope() as session:
        session.add(
            OcrJob(
                id=job_id,
                status=status,
                input_reference="file://dummy.pdf",
                template_id=None,
                output_reference=None,
                metrics=payload,
                error_message=error_message,
                created_at=now,
                updated_at=now,
            )
        )


def test_summarize_reparse_quality_detects_provider_gate_fail(monkeypatch):
    _clear_ocr_jobs()
    now = datetime.utcnow()
    for idx in range(5):
        _seed_job(
            job_id=f"OCR-ORD-QA-OPENAI-{idx}",
            provider="openai",
            status="done",
            metrics={"llm_assist": True, "requested_provider": "openai"},
            updated_at=now - timedelta(minutes=idx),
        )
    for idx in range(2):
        _seed_job(
            job_id=f"OCR-ORD-QA-GEMINI-DONE-{idx}",
            provider="gemini",
            status="done",
            metrics={"llm_assist": True, "requested_provider": "gemini"},
            updated_at=now - timedelta(minutes=10 + idx),
        )
    for idx in range(3):
        _seed_job(
            job_id=f"OCR-ORD-QA-GEMINI-FAIL-{idx}",
            provider="gemini",
            status="failed",
            metrics={
                "llm_assist": True,
                "requested_provider": "gemini",
                "error": "sheet_canonical_mismatch",
            },
            error_message="sheet_canonical_mismatch",
            updated_at=now - timedelta(minutes=20 + idx),
        )

    monkeypatch.setenv("OCR_REPARSE_QUALITY_MIN_SAMPLES", "3")
    monkeypatch.setenv("OCR_REPARSE_QUALITY_MIN_SUCCESS_RATE", "0.80")
    monkeypatch.setenv("OCR_REPARSE_QUALITY_MAX_TRUNCATED_RATE", "0.50")
    monkeypatch.setenv("OCR_REPARSE_QUALITY_MAX_EMPTY_RATE", "0.50")
    monkeypatch.setenv("OCR_REPARSE_QUALITY_MAX_VALIDATION_FAILURE_RATE", "0.50")

    payload = summarize_reparse_quality(now=now)
    assert payload.get("gate", {}).get("status") == "fail"
    assert "gemini" in (payload.get("gate", {}).get("fail_providers") or [])
    providers = {row.get("provider"): row for row in payload.get("providers") or []}
    assert providers["openai"]["gate_status"] == "pass"
    assert providers["gemini"]["gate_status"] == "fail"
    assert providers["gemini"]["validation_failed"] == 3
    assert providers["gemini"]["success_rate"] == 0.4


def test_summarize_reparse_quality_marks_insufficient_data(monkeypatch):
    _clear_ocr_jobs()
    now = datetime.utcnow()
    _seed_job(
        job_id="OCR-ORD-QA-OPENAI-ONLY-1",
        provider="openai",
        status="done",
        metrics={"llm_assist": True, "requested_provider": "openai"},
        updated_at=now - timedelta(minutes=1),
    )
    monkeypatch.setenv("OCR_REPARSE_QUALITY_MIN_SAMPLES", "2")
    payload = summarize_reparse_quality(now=now)

    assert payload.get("gate", {}).get("status") == "pass"
    providers = payload.get("providers") or []
    assert len(providers) == 1
    assert providers[0].get("gate_status") == "warming_up"
    assert providers[0].get("provider") == "openai"
    warming_up = payload.get("gate", {}).get("warming_up_providers") or []
    assert "openai" in warming_up


def test_summarize_reparse_quality_reports_insufficient_data_when_no_samples():
    _clear_ocr_jobs()
    payload = summarize_reparse_quality()
    assert payload.get("gate", {}).get("status") == "insufficient_data"
    assert payload.get("providers") == []


def test_summarize_reparse_quality_excludes_non_reparse_llm_jobs(monkeypatch):
    _clear_ocr_jobs()
    now = datetime.utcnow()
    _seed_job(
        job_id="OCR-ORD-NON-REPARSE-1",
        provider="gemini",
        status="failed",
        metrics={"changed": False},
        error_message="sheet_canonical_mismatch",
        updated_at=now - timedelta(minutes=3),
    )
    _seed_job(
        job_id="OCR-ORD-REPARSE-1",
        provider="gemini",
        status="done",
        metrics={"llm_assist": True, "requested_provider": "gemini", "changed": True},
        updated_at=now - timedelta(minutes=1),
    )
    monkeypatch.setenv("OCR_REPARSE_QUALITY_MIN_SAMPLES", "1")

    payload = summarize_reparse_quality(now=now)

    assert payload.get("gate", {}).get("status") == "pass"
    scope = payload.get("scope") or {}
    assert scope.get("job_type") == "llm_reparse_only"
    assert scope.get("included_jobs") == 1
    assert scope.get("skipped_non_reparse_jobs") == 1
    assert (scope.get("skipped_reasons") or {}).get("non_reparse_llm_job") == 1
    providers = {row.get("provider"): row for row in payload.get("providers") or []}
    assert providers["gemini"]["total"] == 1


def test_summarize_reparse_quality_prefers_explicit_quality_track_over_llm_provider_heuristics(monkeypatch):
    _clear_ocr_jobs()
    now = datetime.utcnow()
    _seed_job(
        job_id="OCR-ORD-NON-LLM-TRACKED",
        provider="gemini",
        status="failed",
        metrics={
            "quality_track": "non_llm_reparse",
            "requested_provider": "gemini",
            "llm_assist": True,
            "error": "sheet_canonical_mismatch",
        },
        error_message="sheet_canonical_mismatch",
        updated_at=now - timedelta(minutes=2),
    )
    _seed_job(
        job_id="OCR-ORD-LLM-TRACKED",
        provider="gemini",
        status="done",
        metrics={
            "quality_track": "llm_reparse",
            "reparse_origin": "llm_assist",
            "feedback_retry_depth": 1,
            "requested_provider": "gemini",
        },
        updated_at=now - timedelta(minutes=1),
    )
    monkeypatch.setenv("OCR_REPARSE_QUALITY_MIN_SAMPLES", "1")

    payload = summarize_reparse_quality(now=now)

    assert payload.get("gate", {}).get("status") == "pass"
    scope = payload.get("scope") or {}
    assert scope.get("mode") == "explicit_only"
    assert scope.get("explicit_quality_track_jobs") == 2
    assert scope.get("included_jobs") == 1
    assert scope.get("skipped_non_reparse_jobs") == 1
    assert (scope.get("skipped_reasons") or {}).get("quality_track_non_llm_reparse") == 1
    providers = {row.get("provider"): row for row in payload.get("providers") or []}
    assert providers["gemini"]["total"] == 1


def test_summarize_reparse_quality_ignores_legacy_untagged_jobs_when_explicit_track_exists(monkeypatch):
    _clear_ocr_jobs()
    now = datetime.utcnow()
    _seed_job(
        job_id="OCR-ORD-LEGACY-UNTAGGED-FAIL",
        provider="gemini",
        status="failed",
        metrics={
            "requested_provider": "gemini",
            "llm_assist": True,
            "error": "sheet_blank_anchor_drift",
        },
        error_message="sheet_blank_anchor_drift",
        updated_at=now - timedelta(minutes=3),
    )
    _seed_job(
        job_id="OCR-ORD-TAGGED-DONE",
        provider="gemini",
        status="done",
        metrics={
            "quality_track": "llm_reparse",
            "requested_provider": "gemini",
            "reparse_origin": "llm_assist",
            "feedback_retry_depth": 0,
        },
        updated_at=now - timedelta(minutes=1),
    )
    monkeypatch.setenv("OCR_REPARSE_QUALITY_MIN_SAMPLES", "1")

    payload = summarize_reparse_quality(now=now)

    assert payload.get("gate", {}).get("status") == "pass"
    scope = payload.get("scope") or {}
    assert scope.get("mode") == "explicit_only"
    assert scope.get("included_jobs") == 1
    assert (scope.get("skipped_reasons") or {}).get("legacy_untagged_job") == 1
    providers = {row.get("provider"): row for row in payload.get("providers") or []}
    assert providers["gemini"]["failed"] == 0
