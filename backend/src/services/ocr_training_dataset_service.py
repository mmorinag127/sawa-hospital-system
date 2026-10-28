from __future__ import annotations

import csv
import json
import os
import re
import zipfile
import tempfile
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import delete, func, select, update

from src.db import session_scope
from src.staging_data import local_directory
from src.models.ocr_job import OcrJob
from src.models.ocr_training_sample import OcrTrainingSample
from src.models.order import Order, OrderLine
from src.models.order_ocr_cache import OrderOcrCache
from src.services.storage_service import load_bytes_from_uri

_EXPORT_DIR = Path(os.getenv("OCR_TRAINING_EXPORT_DIR", "/tmp/ocr-training-exports"))


def _sanitize_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_json(item) for key, item in value.items()}
    return str(value)


def _serialize_summary(sample: OcrTrainingSample) -> dict:
    return {
        "id": sample.id,
        "order_id": sample.order_id,
        "message_id": sample.message_id,
        "facility_code": sample.facility_code,
        "week_code": sample.week_code,
        "document_uri": sample.document_uri,
        "ocr_job_id": sample.ocr_job_id,
        "ocr_provider": sample.ocr_provider,
        "line_count": int(sample.line_count or 0),
        "has_corrections": bool(sample.has_corrections),
        "source": sample.source,
        "note": sample.note,
        "created_at": sample.created_at.isoformat() if sample.created_at else None,
        "updated_at": sample.updated_at.isoformat() if sample.updated_at else None,
    }


def _serialize_detail(sample: OcrTrainingSample) -> dict:
    data = _serialize_summary(sample)
    data["ocr_output"] = sample.ocr_output if isinstance(sample.ocr_output, dict) else None
    data["labeled_lines"] = sample.labeled_lines if isinstance(sample.labeled_lines, list) else []
    return data


def _line_payload(line: OrderLine) -> dict:
    final_quantity = (
        line.quantity_corrected
        if line.quantity_corrected is not None
        else line.quantity_original
    )
    return {
        "line_id": line.line_id,
        "date": line.date.isoformat() if line.date else None,
        "daypart": line.daypart,
        "menu_name": line.menu_name,
        "diet_type": line.diet_type,
        "area_id": line.area_id,
        "bag_type": line.bag_type,
        "quantity_original": line.quantity_original,
        "quantity_corrected": line.quantity_corrected,
        "final_quantity": final_quantity,
        "change_note": line.change_note,
    }


def _has_line_correction(line: OrderLine) -> bool:
    if line.change_note and line.change_note.strip():
        return True
    if line.quantity_corrected is None:
        return False
    if line.quantity_original is None:
        return True
    return float(line.quantity_corrected) != float(line.quantity_original)


@contextmanager
def _ocr_output_references(order_id):
    # Snapshot small references to disk; do not hold a DB connection during GCS reads.
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as references:
        with session_scope() as session:
            rows = session.execute(select(OcrJob.id, OcrJob.output_reference).where(
                OcrJob.order_id == order_id,
            ).order_by(OcrJob.updated_at.desc(), OcrJob.created_at.desc(), OcrJob.id.desc())
                .execution_options(yield_per=200))
            try:
                for job_id, reference in rows:
                    if reference:
                        references.write(json.dumps([job_id, reference]) + "\n")
            finally:
                rows.close()
        references.seek(0)
        yield (json.loads(line) for line in references)


def _load_ocr_payload(order: Order) -> tuple[dict | None, str | None, str | None]:
    with session_scope() as session:
        cache = session.get(OrderOcrCache, order.id)
        if cache and isinstance(cache.payload, dict):
            payload = _sanitize_json(cache.payload)
            if isinstance(payload, dict):
                provider = payload.get("engine") or payload.get("provider")
                execution = payload.get("_ocr_execution")
                job_id = execution.get("job_id") if isinstance(execution, dict) else None
                recorded_job_id = session.scalar(select(OcrJob.id).where(
                    OcrJob.id == job_id, OcrJob.order_id == order.id,
                )) if isinstance(job_id, str) and job_id else None
                return payload, str(provider) if provider else None, recorded_job_id

    with _ocr_output_references(order.id) as references:
        for job_id, reference in references:
            try:
                raw = load_bytes_from_uri(reference)
                parsed = json.loads(raw.decode("utf-8"))
                payload = _sanitize_json(parsed)
                if isinstance(payload, dict):
                    provider = payload.get("engine") or payload.get("provider")
                    return payload, str(provider) if provider else None, job_id
            except Exception:
                continue
    return None, None, None


def register_order_sample(
    order_id: str,
    *,
    source: str = "manual",
    note: str | None = None,
) -> tuple[dict | None, str | None]:
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        if not order.document_uri:
            return None, "document_not_found"
        lines = (
            session.execute(
                select(OrderLine).where(OrderLine.order_id == order_id)
            )
            .scalars()
            .all()
        )
        if not lines:
            return None, "lines_not_found"
        labeled_lines = [_line_payload(line) for line in lines]
        has_corrections = any(_has_line_correction(line) for line in lines)
        order = SimpleNamespace(**{name: getattr(order, name) for name in (
            "id", "current_document_id", "document_uri", "lines_updated_at", "facility_code", "week_code", "message_id")})
    ocr_output, ocr_provider, ocr_job_id = _load_ocr_payload(order)
    now = datetime.utcnow()

    with session_scope() as session:
        if session.get_bind().dialect.name == "sqlite":
            session.execute(update(Order).where(Order.id == order_id).values(status=Order.status))
        current_order = session.execute(select(Order).where(Order.id == order_id).with_for_update()).scalar_one_or_none()
        if current_order is None:
            return None, "order_not_found"
        if (current_order.current_document_id, current_order.document_uri, current_order.lines_updated_at,
            current_order.facility_code, current_order.week_code) != (order.current_document_id, order.document_uri,
            order.lines_updated_at, order.facility_code, order.week_code):
            return None, "order_training_input_changed"
        sample = (
            session.execute(
                select(OcrTrainingSample).where(OcrTrainingSample.order_id == order_id)
            )
            .scalars()
            .first()
        )
        if not sample:
            sample = OcrTrainingSample(
                id=f"OTS{uuid4().hex[:10]}",
                order_id=order_id,
            )
            session.add(sample)
            sample.created_at = now

        sample.message_id = order.message_id
        sample.facility_code = order.facility_code
        sample.week_code = order.week_code
        sample.document_uri = order.document_uri
        sample.ocr_job_id = ocr_job_id
        sample.ocr_provider = ocr_provider
        sample.ocr_output = _sanitize_json(ocr_output) if isinstance(ocr_output, dict) else None
        sample.labeled_lines = _sanitize_json(labeled_lines)
        sample.line_count = len(labeled_lines)
        sample.has_corrections = has_corrections
        sample.source = source or "manual"
        sample.note = note
        sample.updated_at = now
        session.flush()
        session.refresh(sample)
        return _serialize_detail(sample), None


def list_samples(limit: int = 100) -> list[dict]:
    normalized_limit = max(1, min(limit, 1000))
    with session_scope() as session:
        samples = (
            session.execute(
                select(OcrTrainingSample)
                .order_by(OcrTrainingSample.updated_at.desc())
                .limit(normalized_limit)
            )
            .scalars()
            .all()
    )
    return [_serialize_summary(sample) for sample in samples]


def get_sample(sample_id: str) -> dict | None:
    with session_scope() as session:
        sample = session.get(OcrTrainingSample, sample_id)
        if not sample:
            return None
        return _serialize_detail(sample)


def _query_samples(limit: int = 1000000) -> list[dict]:
    normalized_limit = max(1, min(limit, 1000000))
    with session_scope() as session:
        samples = (
            session.execute(
                select(OcrTrainingSample)
                .order_by(OcrTrainingSample.updated_at.desc())
                .limit(normalized_limit)
            )
            .scalars()
            .all()
        )
    return [_serialize_detail(sample) for sample in samples]


def _query_sample_models(limit: int = 1000000) -> list[OcrTrainingSample]:
    normalized_limit = max(1, min(limit, 1000000))
    with session_scope() as session:
        samples = (
            session.execute(
                select(OcrTrainingSample)
                .order_by(OcrTrainingSample.updated_at.desc())
                .limit(normalized_limit)
            )
            .scalars()
            .all()
        )
    return samples


def _safe_segment(value: str | None, default: str) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    normalized = normalized.strip("._")
    return normalized or default


def _build_pdf_archive_path(sample: OcrTrainingSample, index: int) -> str:
    facility_seg = _safe_segment(sample.facility_code, "unknown_facility")
    order_seg = _safe_segment(sample.order_id, f"order_{index:05d}")
    sample_seg = _safe_segment(sample.id, f"sample_{index:05d}")
    return f"pdf/{facility_seg}/{order_seg}_{sample_seg}.pdf"


def clear_samples(sample_ids: list[str] | None = None) -> int:
    with session_scope() as session:
        if sample_ids:
            normalized_ids = [str(sample_id).strip() for sample_id in sample_ids if str(sample_id).strip()]
            if not normalized_ids:
                return 0
            count_value = session.execute(
                select(func.count(OcrTrainingSample.id)).where(
                    OcrTrainingSample.id.in_(normalized_ids)
                )
            ).scalar_one_or_none()
            session.execute(delete(OcrTrainingSample).where(OcrTrainingSample.id.in_(normalized_ids)))
            return int(count_value or 0)
        count_value = session.execute(select(func.count(OcrTrainingSample.id))).scalar_one_or_none()
        session.execute(delete(OcrTrainingSample))
        return int(count_value or 0)


def export_registered_pdfs(
    *,
    limit: int = 1000000,
    clear_after_export: bool = False,
) -> tuple[Path, str, str, dict]:
    samples = _query_sample_models(limit=limit)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"ocr_training_pdfs_{stamp}.zip"
    output = local_directory(_EXPORT_DIR) / filename

    manifest: list[dict] = []
    successful_sample_ids: list[str] = []
    failed_count = 0

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for index, sample in enumerate(samples, start=1):
            archive_path = _build_pdf_archive_path(sample, index)
            entry = {
                "sample_id": sample.id,
                "order_id": sample.order_id,
                "facility_code": sample.facility_code,
                "document_uri": sample.document_uri,
                "archive_path": archive_path,
                "status": "ok",
                "error": None,
            }
            try:
                pdf_bytes = load_bytes_from_uri(sample.document_uri)
                zf.writestr(archive_path, pdf_bytes)
                successful_sample_ids.append(sample.id)
            except Exception as exc:  # noqa: BLE001
                entry["status"] = "error"
                entry["error"] = str(exc)
                failed_count += 1
            manifest.append(entry)

        manifest_payload = {
            "exported_at": datetime.utcnow().isoformat(),
            "total_samples": len(samples),
            "exported_pdfs": len(successful_sample_ids),
            "failed_pdfs": failed_count,
            "clear_after_export_requested": bool(clear_after_export),
            "items": manifest,
        }
        zf.writestr("manifest.json", json.dumps(manifest_payload, ensure_ascii=False, indent=2))

    removed = 0
    clear_skipped = False
    if clear_after_export:
        if failed_count == 0:
            removed = clear_samples([sample.id for sample in samples])
        else:
            clear_skipped = True

    summary = {
        "total_samples": len(samples),
        "exported_pdfs": len(successful_sample_ids),
        "failed_pdfs": failed_count,
        "removed": removed,
        "clear_requested": bool(clear_after_export),
        "clear_skipped": clear_skipped,
    }
    return output, filename, "application/zip", summary


def export_samples(
    *,
    file_format: str = "jsonl",
    limit: int = 1000000,
) -> tuple[Path, str, str]:
    normalized_format = file_format.strip().lower()
    if normalized_format not in {"jsonl", "csv"}:
        raise ValueError("format must be jsonl or csv")
    items = _query_samples(limit=limit)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if normalized_format == "jsonl":
        filename = f"ocr_training_samples_{stamp}.jsonl"
        output = local_directory(_EXPORT_DIR) / filename
        with output.open("w", encoding="utf-8") as f:
            for item in items:
                payload = {
                    "sample_id": item.get("id"),
                    "order_id": item.get("order_id"),
                    "facility_code": item.get("facility_code"),
                    "week_code": item.get("week_code"),
                    "document_uri": item.get("document_uri"),
                    "ocr_provider": item.get("ocr_provider"),
                    "ocr_output": item.get("ocr_output"),
                    "labels": {
                        "line_count": item.get("line_count"),
                        "has_corrections": item.get("has_corrections"),
                        "lines": item.get("labeled_lines") or [],
                    },
                    "registered_at": item.get("updated_at"),
                    "source": item.get("source"),
                    "note": item.get("note"),
                }
                f.write(json.dumps(payload, ensure_ascii=False))
                f.write("\n")
        return output, filename, "application/x-ndjson"

    filename = f"ocr_training_samples_{stamp}.csv"
    output = local_directory(_EXPORT_DIR) / filename
    fieldnames = [
        "sample_id",
        "order_id",
        "facility_code",
        "week_code",
        "document_uri",
        "ocr_provider",
        "line_count",
        "has_corrections",
        "registered_at",
        "source",
        "note",
        "ocr_output_json",
        "labeled_lines_json",
    ]
    with output.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "sample_id": item.get("id"),
                    "order_id": item.get("order_id"),
                    "facility_code": item.get("facility_code"),
                    "week_code": item.get("week_code"),
                    "document_uri": item.get("document_uri"),
                    "ocr_provider": item.get("ocr_provider"),
                    "line_count": item.get("line_count"),
                    "has_corrections": item.get("has_corrections"),
                    "registered_at": item.get("updated_at"),
                    "source": item.get("source"),
                    "note": item.get("note"),
                    "ocr_output_json": json.dumps(item.get("ocr_output") or {}, ensure_ascii=False),
                    "labeled_lines_json": json.dumps(item.get("labeled_lines") or [], ensure_ascii=False),
                }
            )
    return output, filename, "text/csv"
