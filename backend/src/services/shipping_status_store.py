from __future__ import annotations

import csv
import json
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, inspect, select, text

from src.db import Base, DB_URI, engine, session_scope
from src.models.shipping_tracking import (
    ShippingTrackingCurrent,
    ShippingTrackingEvent,
    ShippingTrackingLog,
)
from src.services.sagawa_tracking_service import normalize_tracking_key


Base.metadata.create_all(bind=engine)

_DEFAULT_TZ = "Asia/Tokyo"
_EXPORT_DIR = Path(os.getenv("SHIPPING_EXPORT_DIR", "/tmp/shipping-exports"))
_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
_EVENT_TEXT_RE = re.compile(r"(\d{2})/(\d{2})\s*(\d{2}):(\d{2})")
STATUS_SHIPPED = "発送済み"
STATUS_NOT_SHIPPED = "発送しなかった"


def _ensure_shipping_tracking_schema() -> None:
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    with engine.begin() as conn:
        if "shipping_tracking_logs" in table_names:
            log_columns = {column["name"] for column in inspector.get_columns("shipping_tracking_logs")}
            if "ship_date" not in log_columns:
                conn.execute(text("ALTER TABLE shipping_tracking_logs ADD COLUMN ship_date DATE"))
        ShippingTrackingCurrent.__table__.create(bind=conn, checkfirst=True)
        ShippingTrackingEvent.__table__.create(bind=conn, checkfirst=True)
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_shipping_tracking_logs_ship_date "
                "ON shipping_tracking_logs (ship_date)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_shipping_tracking_events_tracking_key_order "
                "ON shipping_tracking_events (tracking_key, event_order)"
            )
        )


_ensure_shipping_tracking_schema()


def _status_to_dict(item: object) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "serialize"):
        try:
            serialized = item.serialize()
        except Exception:
            return {}
        if isinstance(serialized, dict):
            return dict(serialized)
    return {}


def _normalize_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _coerce_timezone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or _DEFAULT_TZ)
    except Exception:
        return ZoneInfo(_DEFAULT_TZ)


def _coerce_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        return date.fromisoformat(text_value[:10])
    except Exception:
        return None


def _coerce_utc_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _coerce_local_event_datetime(value: Any, *, event_at_text: str | None = None) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(_coerce_timezone(_DEFAULT_TZ)).replace(tzinfo=None)
        return value
    text_value = str(value or "").strip()
    if text_value:
        try:
            parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(_coerce_timezone(_DEFAULT_TZ)).replace(tzinfo=None)
            return parsed
        except Exception:
            pass
    candidate = str(event_at_text or "").strip()
    match = _EVENT_TEXT_RE.match(candidate)
    if not match:
        return None
    month, day, hour, minute = (int(part) for part in match.groups())
    reference = datetime.now(_coerce_timezone(_DEFAULT_TZ))
    try:
        parsed = datetime(reference.year, month, day, hour, minute)
    except ValueError:
        return None
    if parsed > reference.replace(tzinfo=None) + timedelta(days=2):
        try:
            parsed = datetime(reference.year - 1, month, day, hour, minute)
        except ValueError:
            return None
    return parsed


def _serialize_utc_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return aware.isoformat()


def _serialize_local_datetime(value: datetime | None, *, timezone_name: str = _DEFAULT_TZ) -> str | None:
    if value is None:
        return None
    zone = _coerce_timezone(timezone_name)
    aware = value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
    return aware.isoformat()


def _serialize_event_row(row: ShippingTrackingEvent, *, timezone_name: str = _DEFAULT_TZ) -> dict[str, Any]:
    occurred_at = _serialize_local_datetime(row.event_at, timezone_name=timezone_name) or row.event_at_text
    return {
        "id": row.id,
        "tracking_key": row.tracking_key,
        "tracking_number": row.tracking_number,
        "event_order": row.event_order,
        "status": row.event_status,
        "event_status": row.event_status,
        "occurred_at": occurred_at,
        "event_at": _serialize_local_datetime(row.event_at, timezone_name=timezone_name),
        "time_text": row.event_at_text,
        "event_at_text": row.event_at_text,
        "facility_name": row.office_name,
        "office_name": row.office_name,
    }


def _serialize_log_row(row: ShippingTrackingLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "tracking_key": row.tracking_key,
        "tracking_number": row.tracking_number,
        "ship_date": row.ship_date.isoformat() if row.ship_date else None,
        "facility_name": row.facility_name,
        "status": row.status,
        "delivered": bool(row.delivered),
        "arrival_text": row.arrival_text,
        "error": row.error,
        "source": row.source,
        "looked_up_at": _serialize_utc_datetime(row.looked_up_at),
        "events": [],
    }


def _serialize_current_row(
    row: ShippingTrackingCurrent,
    *,
    events: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "tracking_key": row.tracking_key,
        "tracking_number": row.tracking_number,
        "ship_date": row.ship_date.isoformat() if row.ship_date else None,
        "facility_name": row.facility_name,
        "facility_name_source": "recorded" if row.facility_name else None,
        "status": row.status,
        "delivered": bool(row.delivered),
        "arrival_text": row.arrival_text,
        "error": row.error,
        "source": row.source,
        "looked_up_at": _serialize_utc_datetime(row.looked_up_at),
        "events": list(events or []),
    }


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    delivered = 0
    not_shipped = 0
    errors = 0
    facility_missing = 0
    attention = 0
    for item in items:
        if item.get("error"):
            errors += 1
        if item.get("delivered"):
            delivered += 1
        if _is_not_shipped(item):
            not_shipped += 1
        if not item.get("facility_name"):
            facility_missing += 1
        if item.get("attention_reasons"):
            attention += 1
    total = len(items)
    pending = max(total - delivered - not_shipped, 0)
    return {
        "total": total,
        "delivered": delivered,
        "not_shipped": not_shipped,
        "pending": pending,
        "errors": errors,
        "all_delivered": total > 0 and pending == 0,
        "facility_missing": facility_missing,
        "attention": attention,
    }


def _latest_items_by_tracking(rows: Sequence[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    latest: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in rows:
        tracking_key = normalize_tracking_key(
            str(item.get("tracking_key") or item.get("tracking_number") or "")
        )
        if not tracking_key or tracking_key in seen:
            continue
        seen.add(tracking_key)
        if not item.get("tracking_key"):
            item["tracking_key"] = tracking_key
        latest.append(item)
        if len(latest) >= limit:
            break
    return latest


def _is_not_shipped(item: Mapping[str, Any] | None) -> bool:
    if not item:
        return False
    status = str(item.get("status") or "").strip()
    return status == STATUS_NOT_SHIPPED


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


def get_quota_status() -> dict[str, Any]:
    alert_ratio = max(_read_float_env("SHIPPING_QUOTA_ALERT_RATIO", 0.85), 0.0)
    critical_ratio = max(_read_float_env("SHIPPING_QUOTA_CRITICAL_RATIO", 0.95), alert_ratio)
    resource = "shipping_tracking_logs"
    unit = "rows"
    limit = _read_int_env("SHIPPING_LOG_QUOTA_LIMIT_ROWS", 200000)
    with session_scope() as session:
        used_count = session.execute(select(func.count(ShippingTrackingLog.id))).scalar_one_or_none()
        used = int(used_count or 0)

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
    level = "unknown"
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


def _day_range_utc(target_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    zone = _coerce_timezone(timezone_name)
    start_local = datetime.combine(target_date, time.min, tzinfo=zone)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def _local_date_from_utc(value: datetime | None, *, timezone_name: str = _DEFAULT_TZ) -> date | None:
    if value is None:
        return None
    zone = _coerce_timezone(timezone_name)
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return aware.astimezone(zone).date()


def _utc_epoch_seconds(value: datetime | None) -> float:
    if value is None:
        return float("-inf")
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return aware.timestamp()


def _reference_date_from_item(
    item: Mapping[str, Any],
    *,
    timezone_name: str = _DEFAULT_TZ,
) -> tuple[date | None, str | None]:
    ship_date = _coerce_date(item.get("ship_date"))
    if ship_date:
        return ship_date, "ship_date"
    looked_up_at = _coerce_utc_datetime(item.get("looked_up_at"))
    if looked_up_at:
        return _local_date_from_utc(looked_up_at, timezone_name=timezone_name), "looked_up_at"
    return None, None


def _normalize_event_payloads(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    events: list[dict[str, Any]] = []
    for index, raw_item in enumerate(value):
        raw = _status_to_dict(raw_item) if not isinstance(raw_item, Mapping) else dict(raw_item)
        status = _normalize_text(
            raw.get("status")
            or raw.get("event_status")
            or raw.get("state")
            or raw.get("label")
            or raw.get("event")
        )
        if not status:
            continue
        event_at_text = _normalize_text(
            raw.get("event_at_text")
            or raw.get("time_text")
            or raw.get("occurred_at")
            or raw.get("datetime")
            or raw.get("timestamp")
            or raw.get("time")
            or raw.get("date")
        )
        office_name = _normalize_text(
            raw.get("office_name")
            or raw.get("facility_name")
            or raw.get("facility")
            or raw.get("office")
            or raw.get("station")
        )
        order_value = raw.get("event_order")
        try:
            event_order = int(order_value)
        except Exception:
            event_order = index
        event_at = _coerce_local_event_datetime(raw.get("event_at"), event_at_text=event_at_text)
        events.append(
            {
                "event_order": event_order,
                "status": status,
                "event_status": status,
                "event_at_text": event_at_text,
                "time_text": event_at_text,
                "occurred_at": _serialize_local_datetime(event_at) or event_at_text,
                "event_at": event_at,
                "facility_name": office_name,
                "office_name": office_name,
            }
        )
    events.sort(
        key=lambda item: (
            item.get("event_order", 0),
            item.get("event_at") or datetime.max,
            item.get("status") or "",
        )
    )
    return events


def _event_signature(events: Sequence[dict[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            int(item.get("event_order") or 0),
            str(item.get("status") or ""),
            _serialize_local_datetime(item.get("event_at")),
            str(item.get("event_at_text") or ""),
            str(item.get("facility_name") or ""),
        )
        for item in events
    )


def _event_row_signature(rows: Sequence[ShippingTrackingEvent]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            int(row.event_order or 0),
            str(row.event_status or ""),
            _serialize_local_datetime(row.event_at),
            str(row.event_at_text or ""),
            str(row.office_name or ""),
        )
        for row in rows
    )


def _current_signature_from_values(
    *,
    tracking_number: str,
    ship_date: date | None,
    facility_name: str | None,
    status: str,
    delivered: bool,
    arrival_text: str | None,
    error: str | None,
) -> tuple[Any, ...]:
    return (
        tracking_number,
        ship_date.isoformat() if ship_date else None,
        facility_name,
        status,
        bool(delivered),
        arrival_text,
        error,
    )


def _current_signature_from_row(row: ShippingTrackingCurrent | ShippingTrackingLog | None) -> tuple[Any, ...] | None:
    if row is None:
        return None
    return _current_signature_from_values(
        tracking_number=str(row.tracking_number or ""),
        ship_date=row.ship_date,
        facility_name=_normalize_text(row.facility_name),
        status=str(row.status or ""),
        delivered=bool(row.delivered),
        arrival_text=_normalize_text(row.arrival_text),
        error=_normalize_text(row.error),
    )


def record_tracking_statuses(
    statuses: Sequence[object],
    *,
    source: str,
    facility_by_tracking: Mapping[str, str] | None = None,
    ship_date_by_tracking: Mapping[str, date] | None = None,
) -> int:
    if not statuses:
        return 0

    now = datetime.utcnow()
    normalized_statuses: list[tuple[str, dict[str, Any]]] = []
    for raw in statuses:
        payload = _status_to_dict(raw)
        tracking_key = normalize_tracking_key(
            str(payload.get("tracking_key") or payload.get("tracking_number") or "")
        )
        if not tracking_key:
            continue
        normalized_statuses.append((tracking_key, payload))
    if not normalized_statuses:
        return 0

    facility_map: dict[str, str] = {}
    if facility_by_tracking:
        for key, value in facility_by_tracking.items():
            normalized_key = normalize_tracking_key(str(key))
            facility_name = _normalize_text(value)
            if normalized_key and facility_name:
                facility_map[normalized_key] = facility_name

    ship_date_map: dict[str, date] = {}
    if ship_date_by_tracking:
        for key, value in ship_date_by_tracking.items():
            normalized_key = normalize_tracking_key(str(key))
            parsed_ship_date = _coerce_date(value)
            if normalized_key and parsed_ship_date:
                ship_date_map[normalized_key] = parsed_ship_date

    tracking_keys = [tracking_key for tracking_key, _ in normalized_statuses]
    processed = 0

    with session_scope() as session:
        current_rows = {
            row.tracking_key: row
            for row in session.execute(
                select(ShippingTrackingCurrent).where(ShippingTrackingCurrent.tracking_key.in_(tracking_keys))
            )
            .scalars()
            .all()
        }
        existing_event_rows = (
            session.execute(
                select(ShippingTrackingEvent)
                .where(ShippingTrackingEvent.tracking_key.in_(tracking_keys))
                .order_by(ShippingTrackingEvent.tracking_key.asc(), ShippingTrackingEvent.event_order.asc())
            )
            .scalars()
            .all()
        )
        event_rows_by_tracking: dict[str, list[ShippingTrackingEvent]] = {}
        for row in existing_event_rows:
            event_rows_by_tracking.setdefault(row.tracking_key, []).append(row)

        latest_logs: dict[str, ShippingTrackingLog] = {}
        for row in (
            session.execute(
                select(ShippingTrackingLog)
                .where(ShippingTrackingLog.tracking_key.in_(tracking_keys))
                .order_by(ShippingTrackingLog.looked_up_at.desc(), ShippingTrackingLog.created_at.desc())
            )
            .scalars()
            .all()
        ):
            latest_logs.setdefault(row.tracking_key, row)

        for tracking_key, payload in normalized_statuses:
            current_row = current_rows.get(tracking_key)
            fallback_row = latest_logs.get(tracking_key)

            tracking_number = _normalize_text(payload.get("tracking_number")) or (
                current_row.tracking_number
                if current_row
                else fallback_row.tracking_number if fallback_row else tracking_key
            )
            ship_date = (
                _coerce_date(payload.get("ship_date"))
                or ship_date_map.get(tracking_key)
                or (current_row.ship_date if current_row else None)
                or (fallback_row.ship_date if fallback_row else None)
            )
            facility_name = (
                _normalize_text(payload.get("facility_name"))
                or facility_map.get(tracking_key)
                or (current_row.facility_name if current_row else None)
                or (fallback_row.facility_name if fallback_row else None)
            )
            status = _normalize_text(payload.get("status")) or "不明"
            delivered = bool(payload.get("delivered"))
            arrival_text = _normalize_text(payload.get("arrival_text"))
            error = _normalize_text(payload.get("error"))
            looked_up_at = _coerce_utc_datetime(payload.get("looked_up_at")) or now

            parsed_events = _normalize_event_payloads(payload.get("events"))
            prior_event_rows = event_rows_by_tracking.get(tracking_key, [])
            previous_event_payloads = [
                _serialize_event_row(row, timezone_name=_DEFAULT_TZ) for row in prior_event_rows
            ]
            next_events = parsed_events
            if error and not parsed_events and previous_event_payloads:
                next_events = previous_event_payloads

            previous_signature = _current_signature_from_row(current_row or fallback_row)
            next_signature = _current_signature_from_values(
                tracking_number=tracking_number,
                ship_date=ship_date,
                facility_name=facility_name,
                status=status,
                delivered=delivered,
                arrival_text=arrival_text,
                error=error,
            )
            events_changed = _event_signature(next_events) != _event_row_signature(prior_event_rows)
            should_log = previous_signature != next_signature or events_changed or bool(error)

            if current_row is None:
                current_row = ShippingTrackingCurrent(
                    tracking_key=tracking_key,
                    tracking_number=tracking_number,
                    ship_date=ship_date,
                    facility_name=facility_name,
                    status=status,
                    delivered=delivered,
                    arrival_text=arrival_text,
                    error=error,
                    source=source,
                    looked_up_at=looked_up_at,
                    created_at=now,
                    updated_at=now,
                )
                session.add(current_row)
                current_rows[tracking_key] = current_row
            else:
                current_row.tracking_number = tracking_number
                current_row.ship_date = ship_date
                current_row.facility_name = facility_name
                current_row.status = status
                current_row.delivered = delivered
                current_row.arrival_text = arrival_text
                current_row.error = error
                current_row.source = source
                current_row.looked_up_at = looked_up_at
                current_row.updated_at = now

            if events_changed:
                session.execute(
                    delete(ShippingTrackingEvent).where(ShippingTrackingEvent.tracking_key == tracking_key)
                )
                new_event_rows: list[ShippingTrackingEvent] = []
                for event_index, event_payload in enumerate(next_events):
                    event_row = ShippingTrackingEvent(
                        id=f"STE{uuid4().hex[:10]}",
                        tracking_key=tracking_key,
                        tracking_number=tracking_number,
                        event_order=int(event_payload.get("event_order") or event_index),
                        event_status=str(event_payload.get("status") or event_payload.get("event_status") or ""),
                        event_at_text=_normalize_text(
                            event_payload.get("event_at_text") or event_payload.get("time_text")
                        ),
                        event_at=_coerce_local_event_datetime(
                            event_payload.get("event_at"),
                            event_at_text=_normalize_text(
                                event_payload.get("event_at_text") or event_payload.get("time_text")
                            ),
                        ),
                        office_name=_normalize_text(
                            event_payload.get("facility_name") or event_payload.get("office_name")
                        ),
                        looked_up_at=looked_up_at,
                        created_at=now,
                    )
                    session.add(event_row)
                    new_event_rows.append(event_row)
                event_rows_by_tracking[tracking_key] = new_event_rows

            if should_log:
                session.add(
                    ShippingTrackingLog(
                        id=f"STL{uuid4().hex[:10]}",
                        tracking_key=tracking_key,
                        tracking_number=tracking_number,
                        ship_date=ship_date,
                        facility_name=facility_name,
                        status=status,
                        delivered=delivered,
                        arrival_text=arrival_text,
                        error=error,
                        source=source,
                        looked_up_at=looked_up_at,
                        created_at=now,
                    )
                )

            processed += 1

    return processed


def mark_tracking_status(
    tracking_number: str,
    *,
    status: str,
    source: str = "manual_status",
) -> dict[str, Any]:
    tracking_key = normalize_tracking_key(str(tracking_number or ""))
    if not tracking_key:
        raise ValueError("tracking_number is required")
    normalized_status = _normalize_text(status)
    if normalized_status not in {STATUS_SHIPPED, STATUS_NOT_SHIPPED}:
        raise ValueError("status must be 発送済み or 発送しなかった")

    now = datetime.utcnow()
    delivered = normalized_status == STATUS_SHIPPED
    with session_scope() as session:
        current_row = session.get(ShippingTrackingCurrent, tracking_key)
        fallback_row = (
            session.execute(
                select(ShippingTrackingLog)
                .where(ShippingTrackingLog.tracking_key == tracking_key)
                .order_by(ShippingTrackingLog.looked_up_at.desc(), ShippingTrackingLog.created_at.desc())
            )
            .scalars()
            .first()
        )
        tracking_number_value = (
            (current_row.tracking_number if current_row else None)
            or (fallback_row.tracking_number if fallback_row else None)
            or str(tracking_number).strip()
        )
        ship_date = (current_row.ship_date if current_row else None) or (
            fallback_row.ship_date if fallback_row else None
        )
        facility_name = (current_row.facility_name if current_row else None) or (
            fallback_row.facility_name if fallback_row else None
        )
        arrival_text = (
            _normalize_text(current_row.arrival_text) if current_row else None
        ) or (_normalize_text(fallback_row.arrival_text) if fallback_row else None)

        if current_row is None:
            current_row = ShippingTrackingCurrent(
                tracking_key=tracking_key,
                tracking_number=tracking_number_value,
                ship_date=ship_date,
                facility_name=facility_name,
                status=normalized_status,
                delivered=delivered,
                arrival_text=arrival_text,
                error=None,
                source=source,
                looked_up_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(current_row)
        else:
            current_row.tracking_number = tracking_number_value
            current_row.ship_date = ship_date
            current_row.facility_name = facility_name
            current_row.status = normalized_status
            current_row.delivered = delivered
            current_row.arrival_text = arrival_text
            current_row.error = None
            current_row.source = source
            current_row.looked_up_at = now
            current_row.updated_at = now

        log_row = ShippingTrackingLog(
            id=f"STL{uuid4().hex[:10]}",
            tracking_key=tracking_key,
            tracking_number=tracking_number_value,
            ship_date=ship_date,
            facility_name=facility_name,
            status=normalized_status,
            delivered=delivered,
            arrival_text=arrival_text,
            error=None,
            source=source,
            looked_up_at=now,
            created_at=now,
        )
        session.add(log_row)
        session.flush()
        return _serialize_current_row(current_row)


def _query_history_rows(
    *,
    limit: int,
    date_from: date | None = None,
    date_to: date | None = None,
    timezone_name: str = _DEFAULT_TZ,
    facility_names: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    query_limit = max(1, min(limit, 1_000_000))
    normalized_names = [str(name).strip() for name in (facility_names or []) if str(name).strip()]
    with session_scope() as session:
        query = select(ShippingTrackingLog)
        if normalized_names:
            query = query.where(ShippingTrackingLog.facility_name.in_(normalized_names))
        if date_from:
            start_utc, _ = _day_range_utc(date_from, timezone_name)
            query = query.where(ShippingTrackingLog.looked_up_at >= start_utc)
        if date_to:
            _, end_utc = _day_range_utc(date_to, timezone_name)
            query = query.where(ShippingTrackingLog.looked_up_at < end_utc)
        rows = (
            session.execute(
                query.order_by(ShippingTrackingLog.looked_up_at.desc(), ShippingTrackingLog.created_at.desc()).limit(
                    query_limit
                )
            )
            .scalars()
            .all()
        )
        return [_serialize_log_row(row) for row in rows]


def _query_latest_rows(
    *,
    limit: int,
    timezone_name: str = _DEFAULT_TZ,
    facility_names: Sequence[str] | None = None,
    source: str | None = None,
) -> list[dict[str, Any]]:
    query_limit = max(1, min(limit, 10_000))
    normalized_names = [str(name).strip() for name in (facility_names or []) if str(name).strip()]
    source_name = _normalize_text(source)

    with session_scope() as session:
        query = select(ShippingTrackingCurrent)
        if normalized_names:
            query = query.where(ShippingTrackingCurrent.facility_name.in_(normalized_names))
        if source_name:
            query = query.where(ShippingTrackingCurrent.source == source_name)
        current_rows = (
            session.execute(
                query.order_by(ShippingTrackingCurrent.looked_up_at.desc(), ShippingTrackingCurrent.updated_at.desc()).limit(
                    query_limit
                )
            )
            .scalars()
            .all()
        )
        tracking_keys = [row.tracking_key for row in current_rows]
        event_rows = []
        if tracking_keys:
            event_rows = (
                session.execute(
                    select(ShippingTrackingEvent)
                    .where(ShippingTrackingEvent.tracking_key.in_(tracking_keys))
                    .order_by(ShippingTrackingEvent.tracking_key.asc(), ShippingTrackingEvent.event_order.asc())
                )
                .scalars()
                .all()
            )
        events_by_tracking: dict[str, list[dict[str, Any]]] = {}
        for row in event_rows:
            events_by_tracking.setdefault(row.tracking_key, []).append(
                _serialize_event_row(row, timezone_name=timezone_name)
            )

        items = [
            _serialize_current_row(row, events=events_by_tracking.get(row.tracking_key, []))
            for row in current_rows
        ]

    if len(items) >= query_limit:
        return items

    fallback_rows = _query_history_rows(
        limit=max(query_limit * 20, 1000),
        timezone_name=timezone_name,
        facility_names=normalized_names,
    )
    current_keys = {item.get("tracking_key") for item in items}
    if source_name:
        fallback_rows = [
            item for item in fallback_rows if _normalize_text(item.get("source")) == source_name
        ]
    for item in _latest_items_by_tracking(fallback_rows, limit=query_limit):
        tracking_key = item.get("tracking_key")
        if tracking_key in current_keys:
            continue
        items.append(item)
        current_keys.add(tracking_key)
        if len(items) >= query_limit:
            break
    return items


def _sort_latest_items(items: list[dict[str, Any]], *, timezone_name: str = _DEFAULT_TZ) -> list[dict[str, Any]]:
    def _sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        reference_date, _ = _reference_date_from_item(item, timezone_name=timezone_name)
        looked_up_at = _coerce_utc_datetime(item.get("looked_up_at"))
        looked_up_sort = looked_up_at or datetime.min
        return (
            reference_date or date.max,
            str(item.get("facility_name") or "未設定"),
            -_utc_epoch_seconds(looked_up_sort) if looked_up_at else float("inf"),
            str(item.get("tracking_number") or item.get("tracking_key") or ""),
        )

    return sorted(items, key=_sort_key)


def _describe_attention_reasons(
    item: dict[str, Any],
    *,
    attention_stale_hours: int,
    now_utc: datetime,
) -> list[str]:
    reasons: list[str] = []
    if item.get("error"):
        reasons.append("error")
    status = str(item.get("status") or "")
    if "該当なし" in status or str(item.get("message") or "") == "no_match":
        reasons.append("no_match")
    if not item.get("facility_name"):
        reasons.append("facility_missing")
    if not item.get("delivered"):
        looked_up_at = _coerce_utc_datetime(item.get("looked_up_at"))
        if looked_up_at:
            age_hours = (now_utc - looked_up_at).total_seconds() / 3600
            if age_hours >= max(attention_stale_hours, 1):
                reasons.append("stale")
    deduped: list[str] = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)
    return deduped


def _build_groups(items: list[dict[str, Any]], *, timezone_name: str = _DEFAULT_TZ) -> list[dict[str, Any]]:
    grouped: dict[tuple[str | None, str | None], list[dict[str, Any]]] = {}
    metadata: dict[tuple[str | None, str | None], tuple[str | None, str | None]] = {}
    for item in items:
        reference_date, group_date_source = _reference_date_from_item(item, timezone_name=timezone_name)
        group_date = reference_date.isoformat() if reference_date else None
        facility_name = _normalize_text(item.get("facility_name"))
        key = (group_date, facility_name)
        grouped.setdefault(key, []).append(item)
        metadata[key] = (group_date_source, group_date)

    groups: list[dict[str, Any]] = []
    for (group_date, facility_name), group_items in grouped.items():
        delivered_count = sum(1 for item in group_items if item.get("delivered"))
        pending_count = max(len(group_items) - delivered_count, 0)
        latest_looked_up_at = max(
            (_coerce_utc_datetime(item.get("looked_up_at")) for item in group_items),
            default=None,
        )
        group_date_source, reference_date = metadata[(group_date, facility_name)]
        groups.append(
            {
                "ship_date": group_date if group_date_source == "ship_date" else None,
                "group_date": group_date,
                "group_date_source": group_date_source,
                "reference_date": reference_date,
                "facility_name": facility_name,
                "facility_name_source": "recorded" if facility_name else None,
                "item_count": len(group_items),
                "pending_count": pending_count,
                "delivered_count": delivered_count,
                "latest_looked_up_at": _serialize_utc_datetime(latest_looked_up_at),
                "items": _sort_latest_items(group_items, timezone_name=timezone_name),
            }
        )

    def _group_sort_key(group: dict[str, Any]) -> tuple[Any, ...]:
        raw_group_date = _coerce_date(group.get("group_date"))
        latest_looked_up_at = _coerce_utc_datetime(group.get("latest_looked_up_at"))
        return (
            raw_group_date or date.max,
            str(group.get("facility_name") or "未設定"),
            -_utc_epoch_seconds(latest_looked_up_at) if latest_looked_up_at else float("inf"),
        )

    return sorted(groups, key=_group_sort_key)


def _get_last_scheduled_refresh_at(*, timezone_name: str = _DEFAULT_TZ) -> str | None:
    with session_scope() as session:
        latest_log = session.execute(
            select(func.max(ShippingTrackingLog.looked_up_at)).where(ShippingTrackingLog.source == "scheduled_refresh")
        ).scalar_one_or_none()
        latest_current = session.execute(
            select(func.max(ShippingTrackingCurrent.looked_up_at)).where(
                ShippingTrackingCurrent.source == "scheduled_refresh"
            )
        ).scalar_one_or_none()
    candidates = [value for value in (latest_log, latest_current) if isinstance(value, datetime)]
    if not candidates:
        return None
    return _serialize_utc_datetime(max(candidates))


def _schedule_refresh_window(*, timezone_name: str = _DEFAULT_TZ) -> tuple[str | None, str | None]:
    zone = _coerce_timezone(timezone_name)
    minute = max(0, min(_read_int_env("SHIPPING_REFRESH_SCHEDULE_MINUTE", 15), 59))
    now_local = datetime.now(zone)
    next_local = now_local.replace(minute=minute, second=0, microsecond=0)
    if next_local <= now_local:
        next_local = next_local + timedelta(hours=1)
    return _get_last_scheduled_refresh_at(timezone_name=timezone_name), next_local.isoformat()


def get_today_statuses(*, limit: int = 20, timezone_name: str = _DEFAULT_TZ) -> dict[str, Any]:
    zone = _coerce_timezone(timezone_name)
    today = datetime.now(zone).date()
    items = _query_latest_rows(limit=max(limit * 20, 200), timezone_name=timezone_name)
    filtered = [
        item
        for item in items
        if _local_date_from_utc(_coerce_utc_datetime(item.get("looked_up_at")), timezone_name=timezone_name)
        == today
    ]
    limited = filtered[: max(1, limit)]
    return {
        "date": today.isoformat(),
        "timezone": str(zone),
        "summary": _summary(limited),
        "items": limited,
        "quota": get_quota_status(),
    }


def get_status_history(
    *,
    limit: int = 200,
    date_from: date | None = None,
    date_to: date | None = None,
    timezone_name: str = _DEFAULT_TZ,
    facility_names: Sequence[str] | None = None,
) -> dict[str, Any]:
    zone = _coerce_timezone(timezone_name)
    items = _query_history_rows(
        limit=max(1, min(limit, 1000)),
        date_from=date_from,
        date_to=date_to,
        timezone_name=timezone_name,
        facility_names=facility_names,
    )
    return {
        "timezone": str(zone),
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "summary": _summary(items),
        "items": items,
        "quota": get_quota_status(),
    }


def get_latest_status_view(
    *,
    view: str = "active",
    limit: int = 200,
    base_date: date | None = None,
    window_days: int = 3,
    facility_names: Sequence[str] | None = None,
    source: str | None = None,
    attention_stale_hours: int = 24,
    include_quota: bool = True,
    timezone_name: str = _DEFAULT_TZ,
) -> dict[str, Any]:
    normalized_view = str(view or "").strip().lower()
    if normalized_view not in {"active", "all", "attention", "recent"}:
        raise ValueError("view must be active, all, attention, or recent")

    zone = _coerce_timezone(timezone_name)
    effective_base_date = base_date or datetime.now(zone).date()
    fetch_limit = max(min(limit, 1000) * 20, 500)
    items = _query_latest_rows(
        limit=fetch_limit,
        timezone_name=timezone_name,
        facility_names=facility_names,
        source=source,
    )
    now_utc = datetime.utcnow()

    filtered: list[dict[str, Any]] = []
    for item in items:
        item = dict(item)
        attention_reasons = _describe_attention_reasons(
            item,
            attention_stale_hours=attention_stale_hours,
            now_utc=now_utc,
        )
        if attention_reasons:
            item["attention_reasons"] = attention_reasons

        reference_date, _ = _reference_date_from_item(item, timezone_name=timezone_name)
        if normalized_view == "active" and (item.get("delivered") or _is_not_shipped(item)):
            continue
        if normalized_view == "attention" and not attention_reasons:
            continue
        if normalized_view == "recent":
            if reference_date is None:
                continue
            if abs((reference_date - effective_base_date).days) > max(window_days, 0):
                continue
        filtered.append(item)
        if len(filtered) >= max(1, min(limit, 1000)):
            break

    filtered = _sort_latest_items(filtered, timezone_name=timezone_name)
    last_scheduled_refresh_at, next_scheduled_refresh_at = _schedule_refresh_window(
        timezone_name=timezone_name
    )
    return {
        "generated_at": datetime.now(zone).isoformat(),
        "timezone": str(zone),
        "view": normalized_view,
        "base_date": effective_base_date.isoformat(),
        "window_days": max(window_days, 0),
        "facility_names": [str(name).strip() for name in (facility_names or []) if str(name).strip()],
        "source": _normalize_text(source),
        "summary": _summary(filtered),
        "items": filtered,
        "groups": _build_groups(filtered, timezone_name=timezone_name),
        "quota": get_quota_status() if include_quota else None,
        "last_scheduled_refresh_at": last_scheduled_refresh_at,
        "next_scheduled_refresh_at": next_scheduled_refresh_at,
    }


def get_latest_statuses_for_facility(
    facility_names: Sequence[str],
    *,
    limit: int = 10,
    max_age_days: int = 30,
    timezone_name: str = _DEFAULT_TZ,
) -> dict[str, Any]:
    normalized_names = [str(name).strip() for name in facility_names if str(name).strip()]
    zone = _coerce_timezone(timezone_name)
    if not normalized_names:
        return {
            "timezone": str(zone),
            "facility_names": [],
            "summary": _summary([]),
            "items": [],
            "quota": get_quota_status(),
        }

    since = datetime.now(zone).date() - timedelta(days=max(max_age_days, 1))
    items = _query_latest_rows(
        limit=max(limit * 20, 200),
        timezone_name=timezone_name,
        facility_names=normalized_names,
    )
    filtered: list[dict[str, Any]] = []
    for item in _sort_latest_items(items, timezone_name=timezone_name):
        reference_date, _ = _reference_date_from_item(item, timezone_name=timezone_name)
        if reference_date and reference_date < since:
            continue
        filtered.append(item)
        if len(filtered) >= max(1, min(limit, 100)):
            break

    return {
        "timezone": str(zone),
        "facility_names": normalized_names,
        "summary": _summary(filtered),
        "items": filtered,
        "quota": get_quota_status(),
    }


def get_latest_pending_tracking_numbers(
    *,
    limit: int = 100,
    max_age_days: int = 14,
    timezone_name: str = _DEFAULT_TZ,
) -> list[str]:
    zone = _coerce_timezone(timezone_name)
    since = datetime.now(zone).date() - timedelta(days=max(max_age_days, 1))
    items = _query_latest_rows(limit=max(limit * 20, 500), timezone_name=timezone_name)
    pending: list[str] = []
    for item in _sort_latest_items(items, timezone_name=timezone_name):
        reference_date, _ = _reference_date_from_item(item, timezone_name=timezone_name)
        if reference_date and reference_date < since:
            continue
        if item.get("delivered") or _is_not_shipped(item):
            continue
        if item.get("error"):
            continue
        tracking_number = _normalize_text(item.get("tracking_number") or item.get("tracking_key"))
        if not tracking_number:
            continue
        pending.append(tracking_number)
        if len(pending) >= limit:
            break
    return pending


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

    items = _query_history_rows(
        limit=limit,
        date_from=date_from,
        date_to=date_to,
        timezone_name=timezone_name,
    )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if normalized_format == "csv":
        filename = f"shipping_tracking_history_{stamp}.csv"
        output_path = _EXPORT_DIR / filename
        fieldnames = [
            "id",
            "ship_date",
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
        with output_path.open("w", newline="", encoding="utf-8-sig") as file_obj:
            writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
            writer.writeheader()
            for item in items:
                writer.writerow({key: item.get(key) for key in fieldnames})
        return output_path, filename, "text/csv"

    filename = f"shipping_tracking_history_{stamp}.json"
    output_path = _EXPORT_DIR / filename
    payload = {
        "exported_at": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
        "timezone": timezone_name,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "summary": _summary(items),
        "quota": get_quota_status(),
        "items": items,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path, filename, "application/json"
