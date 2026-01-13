from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse

from src.services.output_builder import build_outputs
from src.api.auth import require_role

router = APIRouter()


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
