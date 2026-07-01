from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse

from src.api.auth import require_role
from src.services import ocr_registry_service, ocr_training_dataset_service


router = APIRouter()


def _legacy_ocr_registry_disabled() -> None:
    raise HTTPException(status_code=410, detail="legacy_ocr_registry_disabled")


@router.get("/ocr/templates", dependencies=[Depends(require_role("admin"))])
def list_templates(limit: int = 100):
    _legacy_ocr_registry_disabled()


@router.get("/ocr/templates/{template_id}", dependencies=[Depends(require_role("admin"))])
def get_template(template_id: str):
    _legacy_ocr_registry_disabled()


@router.get("/ocr/templates/{template_id}/image", dependencies=[Depends(require_role("admin"))])
def get_template_image(template_id: str):
    _legacy_ocr_registry_disabled()


@router.get(
    "/ocr/templates/{template_id}/debug/{debug_name}",
    dependencies=[Depends(require_role("admin"))],
)
def get_template_debug_image(template_id: str, debug_name: str):
    _legacy_ocr_registry_disabled()


@router.post("/ocr/templates", dependencies=[Depends(require_role("admin"))])
def create_template(body: dict):
    _legacy_ocr_registry_disabled()


@router.post("/ocr/templates/auto", dependencies=[Depends(require_role("admin"))])
async def create_template_auto(
    facility_id: str = Form(...),
    file: UploadFile = File(...),
    template_id: str | None = Form(None),
    facility_prompt: str | None = Form(None),
    rows: int | None = Form(None),
    cols: int | None = Form(None),
    dpi: int | None = Form(None),
):
    _legacy_ocr_registry_disabled()


@router.put("/ocr/templates/{template_id}", dependencies=[Depends(require_role("admin"))])
def update_template(template_id: str, body: dict):
    _legacy_ocr_registry_disabled()


@router.delete("/ocr/templates/{template_id}", dependencies=[Depends(require_role("admin"))])
def delete_template(template_id: str):
    _legacy_ocr_registry_disabled()


@router.get("/ocr/unclassified", dependencies=[Depends(require_role("admin"))])
def list_unclassified(status: str | None = None, limit: int = 100):
    return {
        "items": ocr_registry_service.list_unclassified(status=status, limit=limit)
    }


@router.get("/ocr/unclassified/{job_id}", dependencies=[Depends(require_role("admin"))])
def get_unclassified(job_id: str):
    entry = ocr_registry_service.get_unclassified(job_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"item": entry}


@router.post("/ocr/unclassified/{job_id}/resolve", dependencies=[Depends(require_role("admin"))])
def resolve_unclassified(job_id: str, body: dict):
    _legacy_ocr_registry_disabled()


@router.get("/ocr/facilities", dependencies=[Depends(require_role("admin"))])
def list_facilities(limit: int = 100):
    _legacy_ocr_registry_disabled()


@router.get("/ocr/facilities/{facility_id}", dependencies=[Depends(require_role("admin"))])
def get_facility(facility_id: str):
    _legacy_ocr_registry_disabled()


@router.put("/ocr/facilities/{facility_id}", dependencies=[Depends(require_role("admin"))])
def update_facility(facility_id: str, body: dict):
    _legacy_ocr_registry_disabled()


@router.delete("/ocr/facilities/{facility_id}", dependencies=[Depends(require_role("admin"))])
def delete_facility(facility_id: str):
    _legacy_ocr_registry_disabled()


@router.post(
    "/ocr/training-samples/from-order/{order_id}",
    dependencies=[Depends(require_role("operator"))],
)
def register_training_sample_from_order(order_id: str, body: dict | None = None):
    source = "manual"
    note = None
    if isinstance(body, dict):
        raw_source = body.get("source")
        raw_note = body.get("note")
        if isinstance(raw_source, str) and raw_source.strip():
            source = raw_source.strip()
        if isinstance(raw_note, str) and raw_note.strip():
            note = raw_note.strip()
    sample, error = ocr_training_dataset_service.register_order_sample(
        order_id,
        source=source,
        note=note,
    )
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error in {"document_not_found", "lines_not_found"}:
        raise HTTPException(status_code=400, detail=error)
    if error:
        raise HTTPException(status_code=500, detail=error)
    return {"sample": sample}


@router.get("/ocr/training-samples", dependencies=[Depends(require_role("operator"))])
def list_training_samples(limit: int = 100):
    return {"items": ocr_training_dataset_service.list_samples(limit=limit)}


@router.delete("/ocr/training-samples", dependencies=[Depends(require_role("admin"))])
def clear_training_samples():
    removed = ocr_training_dataset_service.clear_samples()
    return {"removed": removed}


@router.get("/ocr/training-samples/export", dependencies=[Depends(require_role("admin"))])
def export_training_samples(file_format: str = "jsonl", limit: int = 1000000):
    try:
        output_path, filename, media_type = ocr_training_dataset_service.export_samples(
            file_format=file_format,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(
        str(output_path),
        media_type=media_type,
        filename=filename,
    )


@router.get("/ocr/training-samples/export-pdfs", dependencies=[Depends(require_role("admin"))])
def export_training_sample_pdfs(limit: int = 1000000, clear_after_export: bool = False):
    try:
        output_path, filename, media_type, summary = ocr_training_dataset_service.export_registered_pdfs(
            limit=max(1, min(limit, 1000000)),
            clear_after_export=bool(clear_after_export),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"pdf export failed: {exc}") from exc
    headers = {
        "X-OCR-Training-Total-Samples": str(summary.get("total_samples", 0)),
        "X-OCR-Training-Exported-PDFs": str(summary.get("exported_pdfs", 0)),
        "X-OCR-Training-Failed-PDFs": str(summary.get("failed_pdfs", 0)),
        "X-OCR-Training-Removed": str(summary.get("removed", 0)),
        "X-OCR-Training-Clear-Skipped": "1" if summary.get("clear_skipped") else "0",
    }
    return FileResponse(
        str(output_path),
        media_type=media_type,
        filename=filename,
        headers=headers,
    )


@router.get("/ocr/training-samples/{sample_id}", dependencies=[Depends(require_role("operator"))])
def get_training_sample(sample_id: str):
    sample = ocr_training_dataset_service.get_sample(sample_id)
    if not sample:
        raise HTTPException(status_code=404, detail="sample not found")
    return {"sample": sample}
