import csv
from datetime import date as dt_date

import pandas as pd
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from loguru import logger

from src.services.output_builder import (
    build_outputs,
    build_output_preview,
    build_delivery_preview,
    build_daily_output_bundle,
)
from src.api.auth import require_role

router = APIRouter()

_PREVIEW_LIMIT_DEFAULT = 10


def _preview_csv(path: str, encoding: str, limit: int) -> dict:
    with open(path, newline="", encoding=encoding, errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        rows: list[list[str]] = []
        for idx, row in enumerate(reader):
            if idx >= limit:
                break
            rows.append([str(cell) for cell in row])
    return {"headers": header, "rows": rows}


def _normalize_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _preview_excel(path: str, limit: int) -> dict:
    df = pd.read_excel(path)
    if limit > 0:
        df = df.head(limit)
    headers = [str(col) for col in df.columns]
    rows: list[list[str]] = []
    for _, row in df.iterrows():
        rows.append([_normalize_cell(row.get(col)) for col in df.columns])
    return {"headers": headers, "rows": rows}


def _parse_iso_date(value: str) -> dt_date:
    try:
        return dt_date.fromisoformat(value)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc


@router.get("/labels", dependencies=[Depends(require_role("operator"))])
def download_labels(order_id: str):
    outputs = build_outputs(order_id)
    path = outputs["labels"]
    logger.info("Output download", order_id=order_id, output="labels", path=path)
    return FileResponse(path, media_type="text/csv", filename=f"{order_id}_labels.csv")


@router.get("/delivery-notes", dependencies=[Depends(require_role("operator"))])
def download_delivery(order_id: str):
    outputs = build_outputs(order_id)
    path = outputs["delivery_note"]
    logger.info("Output download", order_id=order_id, output="delivery", path=path)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{order_id}_delivery.xlsx",
    )


@router.get("/manufacturing-aggregate", dependencies=[Depends(require_role("operator"))])
def download_aggregate(order_id: str):
    outputs = build_outputs(order_id)
    path = outputs["aggregate"]
    logger.info("Output download", order_id=order_id, output="aggregate", path=path)
    return FileResponse(path, media_type="text/csv", filename=f"{order_id}_aggregate.csv")


@router.get("/preview", dependencies=[Depends(require_role("operator"))])
def preview_output(order_id: str, type: str, limit: int = _PREVIEW_LIMIT_DEFAULT):
    limit = max(1, min(limit, _PREVIEW_LIMIT_DEFAULT))
    if type == "labels":
        outputs = build_output_preview(order_id, "labels")
        payload = _preview_csv(outputs["labels"], "cp932", limit)
    elif type == "delivery":
        preview = build_delivery_preview(order_id)
        headers = preview.get("headers", [])
        rows = preview.get("rows", [])
        payload = {
            "headers": headers,
            "rows": rows[:limit],
            "ocr_entry_count": preview.get("ocr_entry_count"),
        }
    elif type == "aggregate":
        outputs = build_output_preview(order_id, "aggregate")
        payload = _preview_csv(outputs["aggregate"], "cp932", limit)
    else:
        raise HTTPException(status_code=400, detail="invalid output type")
    return {"type": type, **payload}


@router.get("/daily-bundle", dependencies=[Depends(require_role("operator"))])
def download_daily_bundle(
    date: str,
    bundle_type: str = "both",
    status: str | None = "確定",
):
    target_date = _parse_iso_date(date)
    try:
        bundle_path, summary = build_daily_output_bundle(
            target_date,
            bundle_type=bundle_type,
            status=status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"bundle build failed: {exc}") from exc
    headers = {
        "X-Daily-Bundle-Total-Orders": str(summary.get("total_orders", 0)),
        "X-Daily-Bundle-Success-Orders": str(summary.get("success_orders", 0)),
        "X-Daily-Bundle-Error-Orders": str(summary.get("error_orders", 0)),
        "X-Daily-Bundle-Type": str(summary.get("bundle_type", bundle_type)),
    }
    filename = f"daily_outputs_{target_date.isoformat()}_{summary.get('bundle_type', bundle_type)}.zip"
    return FileResponse(
        str(bundle_path),
        media_type="application/zip",
        filename=filename,
        headers=headers,
    )
