from datetime import date

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
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
    shipping_status_store.record_tracking_statuses(
        status_items,
        source="excel_enrich",
        facility_by_tracking=facility_by_tracking if isinstance(facility_by_tracking, dict) else None,
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
