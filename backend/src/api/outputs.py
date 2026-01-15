import csv

import pandas as pd
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse

from src.services.output_builder import build_outputs
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


@router.get("/labels", dependencies=[Depends(require_role("operator"))])
def download_labels(order_id: str):
    outputs = build_outputs(order_id)
    path = outputs["labels"]
    return FileResponse(path, media_type="text/csv", filename=f"{order_id}_labels.csv")


@router.get("/delivery-notes", dependencies=[Depends(require_role("operator"))])
def download_delivery(order_id: str):
    outputs = build_outputs(order_id)
    path = outputs["delivery_note"]
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{order_id}_delivery.xlsx",
    )


@router.get("/manufacturing-aggregate", dependencies=[Depends(require_role("operator"))])
def download_aggregate(order_id: str):
    outputs = build_outputs(order_id)
    path = outputs["aggregate"]
    return FileResponse(path, media_type="text/csv", filename=f"{order_id}_aggregate.csv")


@router.get("/preview", dependencies=[Depends(require_role("operator"))])
def preview_output(order_id: str, type: str, limit: int = _PREVIEW_LIMIT_DEFAULT):
    outputs = build_outputs(order_id)
    limit = max(1, min(limit, _PREVIEW_LIMIT_DEFAULT))
    if type == "labels":
        payload = _preview_csv(outputs["labels"], "cp932", limit)
    elif type == "delivery":
        payload = _preview_excel(outputs["delivery_note"], limit)
    elif type == "aggregate":
        payload = _preview_csv(outputs["aggregate"], "cp932", limit)
    else:
        raise HTTPException(status_code=400, detail="invalid output type")
    return {"type": type, **payload}
