from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import os
from typing import Any

from sqlalchemy import select

from src.db import session_scope
from src.models.ocr_job import OcrJob


def _read_int_env(name: str, default: int, *, min_value: int = 1, max_value: int = 100000) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(min_value, min(max_value, value))


def _read_float_env(name: str, default: float, *, min_value: float = 0.0, max_value: float = 1.0) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(min_value, min(max_value, value))


def _to_rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return float(numerator) / float(denominator)


def _normalize_provider(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _looks_like_reparse_quality_job(
    metrics: dict[str, Any],
    *,
    status: str | None = None,
    error_message: str | None = None,
) -> tuple[bool, str]:
    if not isinstance(metrics, dict):
        return False, "metrics_missing"
    quality_track = _normalize_provider(metrics.get("quality_track"))
    if quality_track:
        if quality_track == "llm_reparse":
            return True, "quality_track"
        return False, f"quality_track_{quality_track}"
    provider = _normalize_provider(metrics.get("provider") or metrics.get("requested_provider"))
    if provider not in {"openai", "gemini"}:
        return False, "provider_not_llm"
    status_norm = str(status or "").strip().lower()
    if status_norm in {"done", "empty"}:
        return True, f"status_{status_norm}"
    if status_norm == "failed" and (
        _normalize_provider(metrics.get("error"))
        or str(metrics.get("error") or "").strip()
    ):
        return True, "failed_with_error"
    if isinstance(metrics.get("llm_assist"), bool) and metrics.get("llm_assist") is True:
        return True, "llm_assist"
    if _normalize_provider(metrics.get("requested_provider")) in {"openai", "gemini"}:
        return True, "requested_provider"
    if any(
        key in metrics
        for key in (
            "llm_audit",
            "llm_pre_inference_audit",
            "llm_second_pass_audit",
            "repair_pass_applied",
            "before_count",
            "after_count",
        )
    ):
        return True, "reparse_metrics"
    return False, "non_reparse_llm_job"


def _new_provider_bucket(provider: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "total": 0,
        "done": 0,
        "failed": 0,
        "empty": 0,
        "changed": 0,
        "truncated_output": 0,
        "rows_replaced_with_pipeline": 0,
        "validation_failed": 0,
        "last_updated_at": None,
    }


def _job_has_explicit_quality_track(metrics: dict[str, Any]) -> bool:
    return _normalize_provider(metrics.get("quality_track")) in {"llm_reparse", "non_llm_reparse"}


def summarize_reparse_quality(*, now: datetime | None = None) -> dict[str, Any]:
    lookback_hours = _read_int_env("OCR_REPARSE_QUALITY_LOOKBACK_HOURS", 168, min_value=1, max_value=24 * 90)
    sample_limit = _read_int_env("OCR_REPARSE_QUALITY_SAMPLE_LIMIT", 500, min_value=1, max_value=5000)
    min_samples = _read_int_env("OCR_REPARSE_QUALITY_MIN_SAMPLES", 5, min_value=1, max_value=1000)
    min_success_rate = _read_float_env("OCR_REPARSE_QUALITY_MIN_SUCCESS_RATE", 0.90)
    max_truncated_rate = _read_float_env("OCR_REPARSE_QUALITY_MAX_TRUNCATED_RATE", 0.25)
    max_empty_rate = _read_float_env("OCR_REPARSE_QUALITY_MAX_EMPTY_RATE", 0.10)
    max_validation_failure_rate = _read_float_env(
        "OCR_REPARSE_QUALITY_MAX_VALIDATION_FAILURE_RATE",
        0.10,
    )

    now_dt = now or datetime.utcnow()
    since = now_dt - timedelta(hours=lookback_hours)

    buckets: dict[str, dict[str, Any]] = defaultdict(dict)
    evaluated_jobs = 0
    included_jobs = 0
    skipped_non_reparse_jobs = 0
    skipped_reasons: dict[str, int] = defaultdict(int)

    with session_scope() as session:
        rows = (
            session.execute(
                select(
                    OcrJob.status,
                    OcrJob.metrics,
                    OcrJob.error_message,
                    OcrJob.updated_at,
                )
                .where(OcrJob.updated_at >= since)
                .order_by(OcrJob.updated_at.desc())
                .limit(max(sample_limit * 6, sample_limit))
            )
            .all()
        )

    explicit_quality_track_jobs = 0
    for _status_value, metrics_value, _error_message_value, _updated_at_value in rows:
        metrics = metrics_value if isinstance(metrics_value, dict) else {}
        if _job_has_explicit_quality_track(metrics):
            explicit_quality_track_jobs += 1

    use_explicit_quality_track_only = explicit_quality_track_jobs > 0

    for status_value, metrics_value, error_message_value, updated_at_value in rows:
        if evaluated_jobs >= sample_limit:
            break
        metrics = metrics_value if isinstance(metrics_value, dict) else {}
        if use_explicit_quality_track_only and not _job_has_explicit_quality_track(metrics):
            skipped_non_reparse_jobs += 1
            skipped_reasons["legacy_untagged_job"] += 1
            continue
        status_value = str(status_value or "").strip().lower()
        error_message_text = (
            str(error_message_value or "").strip()
            if not isinstance(error_message_value, str)
            else error_message_value.strip()
        )
        include_job, skip_reason = _looks_like_reparse_quality_job(
            metrics,
            status=status_value,
            error_message=error_message_text,
        )
        if not include_job:
            skipped_non_reparse_jobs += 1
            skipped_reasons[skip_reason] += 1
            continue
        provider = _normalize_provider(metrics.get("provider") or metrics.get("requested_provider"))
        if not provider:
            skipped_non_reparse_jobs += 1
            skipped_reasons["provider_missing"] += 1
            continue
        if provider not in buckets:
            buckets[provider] = _new_provider_bucket(provider)
        bucket = buckets[provider]
        evaluated_jobs += 1
        included_jobs += 1
        bucket["total"] += 1

        status = str(status_value or "").strip().lower()
        if status == "done":
            bucket["done"] += 1
        if status in {"failed", "empty"}:
            bucket["failed"] += 1
        if status == "empty":
            bucket["empty"] += 1

        if bool(metrics.get("changed")):
            bucket["changed"] += 1
        if bool(metrics.get("truncated_output")):
            bucket["truncated_output"] += 1
        if bool(metrics.get("rows_replaced_with_pipeline")):
            bucket["rows_replaced_with_pipeline"] += 1

        error_text = str(metrics.get("error") or error_message_value or "").strip().lower()
        if error_text.startswith("sheet_"):
            bucket["validation_failed"] += 1
        if error_text.startswith("lines_empty"):
            bucket["empty"] += 1

        if updated_at_value:
            previous = bucket["last_updated_at"]
            if previous is None or updated_at_value > previous:
                bucket["last_updated_at"] = updated_at_value

    provider_rows: list[dict[str, Any]] = []
    fail_providers: list[str] = []
    warming_up_providers: list[str] = []
    for provider in sorted(buckets.keys()):
        bucket = buckets[provider]
        total = int(bucket["total"])
        success_rate = _to_rate(int(bucket["done"]), total)
        truncated_rate = _to_rate(int(bucket["truncated_output"]), total)
        empty_rate = _to_rate(int(bucket["empty"]), total)
        validation_failure_rate = _to_rate(int(bucket["validation_failed"]), total)
        changed_rate = _to_rate(int(bucket["changed"]), total)
        fallback_rate = _to_rate(int(bucket["rows_replaced_with_pipeline"]), total)

        violations: list[str] = []
        gate_status = "pass"
        if total < min_samples:
            gate_status = "warming_up"
            warming_up_providers.append(provider)
        else:
            if success_rate is not None and success_rate < min_success_rate:
                violations.append(
                    f"success_rate<{min_success_rate:.2f} ({success_rate:.3f})"
                )
            if truncated_rate is not None and truncated_rate > max_truncated_rate:
                violations.append(
                    f"truncated_rate>{max_truncated_rate:.2f} ({truncated_rate:.3f})"
                )
            if empty_rate is not None and empty_rate > max_empty_rate:
                violations.append(
                    f"empty_rate>{max_empty_rate:.2f} ({empty_rate:.3f})"
                )
            if (
                validation_failure_rate is not None
                and validation_failure_rate > max_validation_failure_rate
            ):
                violations.append(
                    f"validation_failure_rate>{max_validation_failure_rate:.2f} ({validation_failure_rate:.3f})"
                )
            if violations:
                gate_status = "fail"
                fail_providers.append(provider)

        provider_rows.append(
            {
                "provider": provider,
                "total": total,
                "done": int(bucket["done"]),
                "failed": int(bucket["failed"]),
                "empty": int(bucket["empty"]),
                "changed": int(bucket["changed"]),
                "truncated_output": int(bucket["truncated_output"]),
                "rows_replaced_with_pipeline": int(bucket["rows_replaced_with_pipeline"]),
                "validation_failed": int(bucket["validation_failed"]),
                "success_rate": success_rate,
                "truncated_rate": truncated_rate,
                "empty_rate": empty_rate,
                "validation_failure_rate": validation_failure_rate,
                "changed_rate": changed_rate,
                "pipeline_fallback_rate": fallback_rate,
                "gate_status": gate_status,
                "violations": violations,
                "last_updated_at": (
                    bucket["last_updated_at"].isoformat() if bucket["last_updated_at"] else None
                ),
            }
        )

    checked_provider_count = sum(1 for row in provider_rows if row["gate_status"] != "warming_up")
    if fail_providers:
        gate_status = "fail"
    elif not provider_rows:
        gate_status = "insufficient_data"
    else:
        gate_status = "pass"

    return {
        "sample": {
            "lookback_hours": lookback_hours,
            "sample_limit": sample_limit,
            "min_samples": min_samples,
            "evaluated_jobs": evaluated_jobs,
            "included_jobs": included_jobs,
            "skipped_non_reparse_jobs": skipped_non_reparse_jobs,
            "explicit_quality_track_jobs": explicit_quality_track_jobs,
            "scope_mode": "explicit_only" if use_explicit_quality_track_only else "legacy_heuristic",
            "since": since.isoformat(),
            "generated_at": now_dt.isoformat(),
        },
        "scope": {
            "job_type": "llm_reparse_only",
            "mode": "explicit_only" if use_explicit_quality_track_only else "legacy_heuristic",
            "included_jobs": included_jobs,
            "skipped_non_reparse_jobs": skipped_non_reparse_jobs,
            "explicit_quality_track_jobs": explicit_quality_track_jobs,
            "skipped_reasons": dict(sorted(skipped_reasons.items())),
        },
        "thresholds": {
            "min_success_rate": min_success_rate,
            "max_truncated_rate": max_truncated_rate,
            "max_empty_rate": max_empty_rate,
            "max_validation_failure_rate": max_validation_failure_rate,
        },
        "providers": provider_rows,
        "gate": {
            "status": gate_status,
            "fail_providers": fail_providers,
            "warming_up_providers": warming_up_providers,
            "checked_provider_count": checked_provider_count,
            "provider_count": len(provider_rows),
        },
    }
