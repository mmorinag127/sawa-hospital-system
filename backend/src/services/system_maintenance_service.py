from __future__ import annotations

import json
import os
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, delete, func, select

from src.db import Base, DB_URI, engine, session_scope
from src.models.ingest_job import IngestJob
from src.models.ocr_job import OcrJob
from src.models.ocr_training_sample import OcrTrainingSample
from src.models.order import Order, OrderLine, OrderMenuSnapshot
from src.models.order_ocr_cache import OrderOcrCache
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_ocr_revision import OrderOcrRevision
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.order_critical_decision import OrderCriticalDecision
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.output import Bag, DeliveryNote, LabelRow, ManufacturingAggregateRow
from src.models.shipping_tracking import ShippingTrackingLog
from src.models.user import AuditLog, Notification
from src.models.document import OrderDocument


Base.metadata.create_all(bind=engine)

_EXPORT_DIR = Path(os.getenv("SYSTEM_EXPORT_DIR", "/tmp/system-exports"))
_EXPORT_DIR.mkdir(parents=True, exist_ok=True)

_OPERATIONAL_MODELS: list[tuple[str, Any]] = [
    ("label_rows", LabelRow),
    ("bags", Bag),
    ("delivery_notes", DeliveryNote),
    ("manufacturing_aggregate_rows", ManufacturingAggregateRow),
    ("order_menu_snapshots", OrderMenuSnapshot),
    ("order_lines", OrderLine),
    ("order_documents", OrderDocument),
    ("order_ocr_cache", OrderOcrCache),
    ("order_ocr_evidence_runs", OrderOcrEvidenceRun),
    ("order_sheet_drafts", OrderSheetDraft),
    ("order_workflow_states", OrderWorkflowState),
    ("order_critical_decisions", OrderCriticalDecision),
    ("order_confirmed_snapshots", OrderConfirmedSnapshot),
    ("order_ocr_revisions", OrderOcrRevision),
    ("ocr_jobs", OcrJob),
    ("ingest_jobs", IngestJob),
    ("orders", Order),
    ("shipping_tracking_logs", ShippingTrackingLog),
    ("ocr_training_samples", OcrTrainingSample),
]

_ADMIN_LOG_MODELS: list[tuple[str, Any]] = [
    ("notifications", Notification),
    ("audit_logs", AuditLog),
]


def _read_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _read_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _resolve_sqlite_path() -> Path | None:
    if not DB_URI.startswith("sqlite"):
        return None
    if DB_URI.startswith("sqlite:///"):
        raw = DB_URI[len("sqlite:///") :]
        if raw.startswith("./"):
            return Path.cwd() / raw[2:]
        return Path(raw)
    return None


def get_sqlite_db_path() -> Path | None:
    path = _resolve_sqlite_path()
    if not path or not path.exists():
        return None
    return path


def _sum_operational_rows() -> int:
    total = 0
    with session_scope() as session:
        for _, model in _OPERATIONAL_MODELS:
            count_value = session.execute(select(func.count()).select_from(model)).scalar_one_or_none()
            total += int(count_value or 0)
    return total


def get_db_quota_status() -> dict:
    alert_ratio = max(_read_float_env("DB_QUOTA_ALERT_RATIO", 0.85), 0.0)
    critical_ratio = max(_read_float_env("DB_QUOTA_CRITICAL_RATIO", 0.95), alert_ratio)
    level = "unknown"
    resource = "database"
    unit = "rows"
    used = 0
    limit = _read_int_env("DB_QUOTA_LIMIT_ROWS", 500000)

    sqlite_path = get_sqlite_db_path()
    if sqlite_path:
        resource = "sqlite_db_file"
        unit = "bytes"
        used = int(sqlite_path.stat().st_size)
        limit = _read_int_env("DB_QUOTA_LIMIT_BYTES", 1024 * 1024 * 1024)
    else:
        used = _sum_operational_rows()

    ratio = None
    if limit > 0:
        ratio = used / limit
        if ratio >= critical_ratio:
            level = "critical"
        elif ratio >= alert_ratio:
            level = "warning"
        else:
            level = "ok"

    message = ""
    if level == "critical":
        message = "quotaが上限に非常に近い状態です。削除または容量拡張が必要です。"
    elif level == "warning":
        message = "quotaが上限に近づいています。データ整理または容量確認を推奨します。"
    elif level == "ok":
        message = "quotaは許容範囲です。"
    else:
        message = "quota上限が未設定のため、逼迫判定は未実施です。"

    return {
        "resource": resource,
        "unit": unit,
        "used": used,
        "limit": limit,
        "ratio": ratio,
        "alert_level": level,
        "message": message,
        "alert_ratio": alert_ratio,
        "critical_ratio": critical_ratio,
    }


def _sanitize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_value(item) for key, item in value.items()}
    return str(value)


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _sanitize_value(value) for key, value in row.items()}


def export_database_snapshot(*, max_rows_per_table: int | None = None) -> tuple[Path, str, str, dict]:
    default_limit = _read_int_env("DB_EXPORT_MAX_ROWS_PER_TABLE", 1000000)
    if max_rows_per_table is None:
        max_rows = max(1, default_limit)
    else:
        max_rows = max(1, min(int(max_rows_per_table), 5000000))

    metadata = MetaData()
    metadata.reflect(bind=engine)
    table_names = sorted(metadata.tables.keys())

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"db_snapshot_{stamp}.zip"
    output_path = _EXPORT_DIR / filename

    manifest_items: list[dict[str, Any]] = []
    with session_scope() as session:
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for table_name in table_names:
                table = metadata.tables[table_name]
                count_value = session.execute(select(func.count()).select_from(table)).scalar_one_or_none()
                total_rows = int(count_value or 0)
                query = select(table)
                if total_rows > max_rows:
                    query = query.limit(max_rows)
                rows = session.execute(query).mappings().all()
                serialized_rows = [_serialize_row(dict(row)) for row in rows]
                table_filename = f"tables/{table_name}.json"
                zf.writestr(
                    table_filename,
                    json.dumps(serialized_rows, ensure_ascii=False, indent=2),
                )
                manifest_items.append(
                    {
                        "table": table_name,
                        "rows_total": total_rows,
                        "rows_exported": len(serialized_rows),
                        "truncated": total_rows > len(serialized_rows),
                        "path": table_filename,
                    }
                )
            manifest_payload = {
                "exported_at": datetime.utcnow().isoformat(),
                "db_uri_type": "sqlite" if DB_URI.startswith("sqlite") else "sql",
                "tables": manifest_items,
                "max_rows_per_table": max_rows,
            }
            zf.writestr("manifest.json", json.dumps(manifest_payload, ensure_ascii=False, indent=2))

    summary = {
        "table_count": len(table_names),
        "max_rows_per_table": max_rows,
        "tables": manifest_items,
    }
    return output_path, filename, "application/zip", summary


def clear_operational_data(*, include_audit_logs: bool = True) -> dict:
    removed: dict[str, int] = {}
    with session_scope() as session:
        for table_name, model in _OPERATIONAL_MODELS:
            count_value = session.execute(select(func.count()).select_from(model)).scalar_one_or_none()
            removed[table_name] = int(count_value or 0)
            session.execute(delete(model))
        if include_audit_logs:
            for table_name, model in _ADMIN_LOG_MODELS:
                count_value = session.execute(select(func.count()).select_from(model)).scalar_one_or_none()
                removed[table_name] = int(count_value or 0)
                session.execute(delete(model))
    return {
        "removed": removed,
        "total_removed": int(sum(removed.values())),
    }
