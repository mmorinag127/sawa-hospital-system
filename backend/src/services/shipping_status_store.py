from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select

from src.db import Base, DB_URI, engine, session_scope
from src.models.shipping_tracking import ShippingTrackingLog
from src.services.sagawa_tracking_service import normalize_tracking_key


Base.metadata.create_all(bind=engine)

_DEFAULT_TZ = "Asia/Tokyo"
_EXPORT_DIR = Path(os.getenv("SHIPPING_EXPORT_DIR", "/tmp/shipping-exports"))
_EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def _serialize_row(row: ShippingTrackingLog) -> dict:
    return {
        "id": row.id,
        "tracking_key": row.tracking_key,
        "tracking_number": row.tracking_number,
        "facility_name": row.facility_name,
        "status": row.status,
        "delivered": bool(row.delivered),
        "arrival_text": row.arrival_text,
        "error": row.error,
        "source": row.source,
        "looked_up_at": row.looked_up_at.isoformat() if row.looked_up_at else None,
    }


def _summary(items: list[dict]) -> dict:
    delivered = 0
    errors = 0
    for item in items:
        if item.get("error"):
            errors += 1
        if item.get("delivered"):
            delivered += 1
    total = len(items)
    pending = max(total - delivered, 0)
    return {
        "total": total,
        "delivered": delivered,
        "pending": pending,
        "errors": errors,
        "all_delivered": total > 0 and pending == 0,
    }


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


def get_quota_status() -> dict:
    alert_ratio = max(_read_float_env("SHIPPING_QUOTA_ALERT_RATIO", 0.85), 0.0)
    critical_ratio = max(_read_float_env("SHIPPING_QUOTA_CRITICAL_RATIO", 0.95), alert_ratio)
    level = "unknown"
    resource = "shipping_tracking_logs"
    unit = "rows"
    used = 0
    limit = _read_int_env("SHIPPING_LOG_QUOTA_LIMIT_ROWS", 200000)
    with session_scope() as session:
        count_value = session.execute(select(func.count(ShippingTrackingLog.id))).scalar_one_or_none()
        used = int(count_value or 0)

    sqlite_path = _resolve_sqlite_path()
    sqlite_size = None
    if sqlite_path and sqlite_path.exists():
        try:
            sqlite_size = int(sqlite_path.stat().st_size)
        except Exception:
            sqlite_size = None
    bytes_limit = _read_int_env("SHIPPING_DB_QUOTA_LIMIT_BYTES", 0)
    if sqlite_size is not None and bytes_limit > 0:
        resource = "sqlite_db_file"
        unit = "bytes"
        used = sqlite_size
        limit = bytes_limit

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
        message = "quotaが上限に非常に近い状態です。早急に削除または容量拡張が必要です。"
    elif level == "warning":
        message = "quotaが上限に近づいています。履歴削除または容量確認を推奨します。"
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


def _status_to_dict(item: object) -> dict:
    if isinstance(item, dict):
        return item
    if hasattr(item, "serialize"):
        try:
            serialized = item.serialize()
            if isinstance(serialized, dict):
                return serialized
        except Exception:
            return {}
    return {}


def _coerce_timezone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or _DEFAULT_TZ)
    except Exception:
        return ZoneInfo(_DEFAULT_TZ)


def _day_range_utc(target_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    zone = _coerce_timezone(timezone_name)
    start_local = datetime.combine(target_date, time.min, tzinfo=zone)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def record_tracking_statuses(
    statuses: Sequence[object],
    *,
    source: str,
    facility_by_tracking: Mapping[str, str] | None = None,
) -> int:
    if not statuses:
        return 0
    inserted = 0
    now = datetime.utcnow()
    facility_map: dict[str, str] = {}
    if facility_by_tracking:
        for key, value in facility_by_tracking.items():
            normalized_key = normalize_tracking_key(str(key))
            if not normalized_key:
                continue
            facility_name = str(value or "").strip()
            if facility_name:
                facility_map[normalized_key] = facility_name

    with session_scope() as session:
        for raw in statuses:
            payload = _status_to_dict(raw)
            tracking_key = normalize_tracking_key(
                str(payload.get("tracking_key") or payload.get("tracking_number") or "")
            )
            if not tracking_key:
                continue
            tracking_number = str(payload.get("tracking_number") or tracking_key).strip()
            status = str(payload.get("status") or "不明").strip() or "不明"
            arrival_text = str(payload.get("arrival_text") or "").strip() or None
            error = str(payload.get("error") or "").strip() or None
            facility_name = (
                str(payload.get("facility_name") or "").strip()
                or facility_map.get(tracking_key)
                or None
            )
            session.add(
                ShippingTrackingLog(
                    id=f"STL{uuid4().hex[:10]}",
                    tracking_key=tracking_key,
                    tracking_number=tracking_number,
                    facility_name=facility_name,
                    status=status,
                    delivered=bool(payload.get("delivered")),
                    arrival_text=arrival_text,
                    error=error,
                    source=source,
                    looked_up_at=now,
                )
            )
            inserted += 1
    return inserted


def _query_history_rows(
    *,
    limit: int,
    date_from: date | None = None,
    date_to: date | None = None,
    timezone_name: str = _DEFAULT_TZ,
) -> list[ShippingTrackingLog]:
    query_limit = max(1, min(limit, 1_000_000))
    with session_scope() as session:
        query = select(ShippingTrackingLog)
        if date_from:
            start_utc, _ = _day_range_utc(date_from, timezone_name)
            query = query.where(ShippingTrackingLog.looked_up_at >= start_utc)
        if date_to:
            _, end_utc = _day_range_utc(date_to, timezone_name)
            query = query.where(ShippingTrackingLog.looked_up_at < end_utc)
        rows = (
            session.execute(
                query.order_by(ShippingTrackingLog.looked_up_at.desc()).limit(query_limit)
            )
            .scalars()
            .all()
        )
    return rows


def get_today_statuses(*, limit: int = 20, timezone_name: str = _DEFAULT_TZ) -> dict:
    zone = _coerce_timezone(timezone_name)
    today = datetime.now(zone).date()
    start_utc, end_utc = _day_range_utc(today, timezone_name)
    fetch_limit = max(limit * 10, limit, 50)
    with session_scope() as session:
        rows = (
            session.execute(
                select(ShippingTrackingLog)
                .where(ShippingTrackingLog.looked_up_at >= start_utc)
                .where(ShippingTrackingLog.looked_up_at < end_utc)
                .order_by(ShippingTrackingLog.looked_up_at.desc())
                .limit(fetch_limit)
            )
            .scalars()
            .all()
        )

    latest_by_tracking: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if row.tracking_key in seen:
            continue
        seen.add(row.tracking_key)
        latest_by_tracking.append(_serialize_row(row))
        if len(latest_by_tracking) >= limit:
            break
    return {
        "date": today.isoformat(),
        "timezone": str(zone),
        "summary": _summary(latest_by_tracking),
        "items": latest_by_tracking,
        "quota": get_quota_status(),
    }


def get_status_history(
    *,
    limit: int = 200,
    date_from: date | None = None,
    date_to: date | None = None,
    timezone_name: str = _DEFAULT_TZ,
) -> dict:
    zone = _coerce_timezone(timezone_name)
    rows = _query_history_rows(
        limit=max(1, min(limit, 1000)),
        date_from=date_from,
        date_to=date_to,
        timezone_name=timezone_name,
    )
    items = [_serialize_row(row) for row in rows]
    return {
        "timezone": str(zone),
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "summary": _summary(items),
        "items": items,
        "quota": get_quota_status(),
    }


def clear_status_history() -> int:
    with session_scope() as session:
        total = session.execute(select(func.count(ShippingTrackingLog.id))).scalar_one_or_none()
        session.execute(delete(ShippingTrackingLog))
    return int(total or 0)


def export_status_history(
    *,
    file_format: str,
    limit: int = 1_000_000,
    date_from: date | None = None,
    date_to: date | None = None,
    timezone_name: str = _DEFAULT_TZ,
) -> tuple[Path, str, str]:
    normalized_format = file_format.strip().lower()
    if normalized_format not in {"csv", "json"}:
        raise ValueError("format must be csv or json")
    rows = _query_history_rows(
        limit=limit,
        date_from=date_from,
        date_to=date_to,
        timezone_name=timezone_name,
    )
    items = [_serialize_row(row) for row in rows]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if normalized_format == "csv":
        filename = f"shipping_tracking_history_{stamp}.csv"
        output_path = _EXPORT_DIR / filename
        fieldnames = [
            "id",
            "looked_up_at",
            "tracking_key",
            "tracking_number",
            "facility_name",
            "status",
            "delivered",
            "arrival_text",
            "error",
            "source",
        ]
        with output_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for item in items:
                row_payload = {key: item.get(key) for key in fieldnames}
                writer.writerow(row_payload)
        return output_path, filename, "text/csv"

    filename = f"shipping_tracking_history_{stamp}.json"
    output_path = _EXPORT_DIR / filename
    payload = {
        "exported_at": datetime.utcnow().isoformat(),
        "timezone": timezone_name,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "summary": _summary(items),
        "quota": get_quota_status(),
        "items": items,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path, filename, "application/json"
