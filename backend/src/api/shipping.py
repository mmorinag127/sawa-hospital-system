from datetime import date

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Query
from fastapi.responses import FileResponse

from src.api.auth import require_role
from src.services import shipping_service, shipping_status_store

router = APIRouter()


@router.post("/shipping/parse", dependencies=[Depends(require_role("operator"))])
async def parse_shipping_pdf(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        records = shipping_service.extract_shipping_records(content)
        if not records:
            raise HTTPException(status_code=400, detail="no records found")
        tracking_numbers = [record.tracking_number for record in records if str(record.tracking_number).strip()]
        if tracking_numbers:
            try:
                tracking_records = shipping_service.get_tracking_status_records(tracking_numbers)
                facility_by_tracking = {
                    str(record.tracking_number).strip(): str(record.facility_name).strip()
                    for record in records
                    if str(record.tracking_number).strip() and str(record.facility_name).strip()
                }
                ship_date_by_tracking = {
                    str(record.tracking_number).strip(): record.ship_date
                    for record in records
                    if str(record.tracking_number).strip() and record.ship_date
                }
                shipping_status_store.record_tracking_statuses(
                    tracking_records,
                    source="shipping_pdf_parse",
                    facility_by_tracking=facility_by_tracking,
                    ship_date_by_tracking=ship_date_by_tracking,
                )
            except Exception:
                # Parsing the shipping PDF should still succeed even if the
                # tracking lookup is temporarily unavailable.
                pass
        output_path = shipping_service.build_shipping_excel(records)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"parse failed: {exc}") from exc
    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="伝票番号管理表.xlsx",
    )


@router.post("/shipping/track-status", dependencies=[Depends(require_role("operator"))])
def track_shipping_status(body: dict):
    numbers = body.get("tracking_numbers") if isinstance(body, dict) else None
    if not isinstance(numbers, list):
        raise HTTPException(status_code=400, detail="tracking_numbers must be a list")
    normalized = [str(item).strip() for item in numbers if str(item).strip()]
    if not normalized:
        raise HTTPException(status_code=400, detail="tracking_numbers is empty")
    status_records = shipping_service.get_tracking_status_records(normalized)
    items = [status.serialize() for status in status_records]
    shipping_status_store.record_tracking_statuses(status_records, source="manual_track")
    delivered = sum(1 for item in items if item.get("delivered"))
    pending = max(len(items) - delivered, 0)
    return {
        "items": items,
        "summary": {
            "total": len(items),
            "delivered": delivered,
            "pending": pending,
            "all_delivered": len(items) > 0 and pending == 0,
        },
    }


@router.post("/shipping/status/manual", dependencies=[Depends(require_role("operator"))])
def mark_shipping_status(body: dict):
    tracking_number = body.get("tracking_number") if isinstance(body, dict) else None
    status = body.get("status") if isinstance(body, dict) else None
    try:
        item = shipping_status_store.mark_tracking_status(
            str(tracking_number or "").strip(),
            status=str(status or "").strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"item": item}


@router.post("/shipping/enrich-excel", dependencies=[Depends(require_role("operator"))])
async def enrich_shipping_excel(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        output_path, summary = shipping_service.enrich_tracking_excel(content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"enrich failed: {exc}") from exc
    status_items = summary.pop("_status_items", [])
    facility_by_tracking = summary.pop("_facility_by_tracking", {})
    ship_date_by_tracking = summary.pop("_ship_date_by_tracking", {})
    shipping_status_store.record_tracking_statuses(
        status_items,
        source="excel_enrich",
        facility_by_tracking=facility_by_tracking if isinstance(facility_by_tracking, dict) else None,
        ship_date_by_tracking=ship_date_by_tracking if isinstance(ship_date_by_tracking, dict) else None,
    )
    headers = {
        "X-Shipping-Total-Rows": str(summary.get("total_rows", 0)),
        "X-Shipping-Lookup-Count": str(summary.get("lookup_count", 0)),
        "X-Shipping-Delivered-Rows": str(summary.get("delivered_rows", 0)),
        "X-Shipping-Pending-Rows": str(summary.get("pending_rows", 0)),
        "X-Shipping-Updated-Arrival-Rows": str(summary.get("updated_arrival_rows", 0)),
        "X-Shipping-Error-Rows": str(summary.get("error_rows", 0)),
        "X-Shipping-All-Delivered": "1" if summary.get("all_delivered") else "0",
    }
    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="伝票番号管理表_到着更新.xlsx",
        headers=headers,
    )


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc


@router.get("/shipping/status/today", dependencies=[Depends(require_role("operator"))])
def get_shipping_status_today(limit: int = 20):
    normalized_limit = max(1, min(limit, 200))
    return shipping_status_store.get_today_statuses(limit=normalized_limit)


@router.get("/shipping/status/history", dependencies=[Depends(require_role("operator"))])
def get_shipping_status_history(
    limit: int = 200,
    date_from: str | None = None,
    date_to: str | None = None,
):
    normalized_limit = max(1, min(limit, 1000))
    parsed_from = _parse_iso_date(date_from) if date_from else None
    parsed_to = _parse_iso_date(date_to) if date_to else None
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="date_from must be <= date_to")
    return shipping_status_store.get_status_history(
        limit=normalized_limit,
        date_from=parsed_from,
        date_to=parsed_to,
    )


@router.get("/shipping/status/latest", dependencies=[Depends(require_role("operator"))])
def get_shipping_status_latest(
    view: str = "active",
    limit: int = 200,
    base_date: str | None = None,
    window_days: int = 3,
    facility_name: list[str] | None = Query(default=None),
    source: str | None = None,
    attention_stale_hours: int = 24,
    include_quota: bool = True,
):
    normalized_limit = max(1, min(limit, 1000))
    normalized_window_days = max(0, min(window_days, 90))
    normalized_stale_hours = max(1, min(attention_stale_hours, 24 * 14))
    parsed_base_date = _parse_iso_date(base_date) if base_date else None
    try:
        return shipping_status_store.get_latest_status_view(
            view=view,
            limit=normalized_limit,
            base_date=parsed_base_date,
            window_days=normalized_window_days,
            facility_names=facility_name,
            source=source,
            attention_stale_hours=normalized_stale_hours,
            include_quota=include_quota,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/shipping/status/refresh-pending", dependencies=[Depends(require_role("operator"))])
def refresh_pending_shipping_statuses(limit: int = 100, max_age_days: int = 14):
    normalized_limit = max(1, min(limit, 500))
    normalized_age = max(1, min(max_age_days, 90))
    tracking_numbers = shipping_status_store.get_latest_pending_tracking_numbers(
        limit=normalized_limit,
        max_age_days=normalized_age,
    )
    if not tracking_numbers:
        return {
            "accepted": True,
            "tracking_count": 0,
            "updated": 0,
            "delivered": 0,
            "pending": 0,
        }
    status_records = shipping_service.get_tracking_status_records(tracking_numbers)
    inserted = shipping_status_store.record_tracking_statuses(
        status_records,
        source="scheduled_refresh",
    )
    serialized = [status.serialize() for status in status_records]
    delivered = sum(1 for item in serialized if item.get("delivered"))
    pending = max(len(serialized) - delivered, 0)
    return {
        "accepted": True,
        "tracking_count": len(tracking_numbers),
        "updated": inserted,
        "delivered": delivered,
        "pending": pending,
    }


@router.delete("/shipping/status/history", dependencies=[Depends(require_role("admin"))])
def clear_shipping_status_history():
    removed = shipping_status_store.clear_status_history()
    return {
        "removed": removed,
        "quota": shipping_status_store.get_quota_status(),
    }


@router.get("/shipping/status/export", dependencies=[Depends(require_role("admin"))])
def export_shipping_status_history(
    format: str = "csv",
    limit: int = 1000000,
    date_from: str | None = None,
    date_to: str | None = None,
):
    parsed_from = _parse_iso_date(date_from) if date_from else None
    parsed_to = _parse_iso_date(date_to) if date_to else None
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="date_from must be <= date_to")
    try:
        output_path, filename, media_type = shipping_status_store.export_status_history(
            file_format=format,
            limit=max(1, min(limit, 1000000)),
            date_from=parsed_from,
            date_to=parsed_to,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"export failed: {exc}") from exc
    return FileResponse(
        output_path,
        media_type=media_type,
        filename=filename,
    )
