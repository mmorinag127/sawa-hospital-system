import os
import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Response
from fastapi.responses import FileResponse

from src.api.auth import require_role
from src.services import config_service, ocr_registry_service, ocr_training_dataset_service
from src.services.storage_service import load_bytes_from_uri, save_bytes_to_gcs
from src.services.template_builder import build_template_from_pdf


router = APIRouter()

def _sanitize_template_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", value.strip())


def _default_template_id(facility_id: str) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"TPL_{_sanitize_template_id(facility_id)}_{stamp}"


def _get_template_bucket() -> str:
    return (
        os.getenv("OCR_TEMPLATE_BUCKET")
        or os.getenv("OCR_PIPELINE_BUCKET")
        or os.getenv("RAW_BUCKET")
        or ""
    )


def _get_template_prefix() -> str:
    prefix = os.getenv("OCR_TEMPLATE_PREFIX", "templates/")
    if prefix and not prefix.endswith("/"):
        prefix = f"{prefix}/"
    return prefix


def _get_debug_prefix() -> str:
    prefix = os.getenv("OCR_TEMPLATE_DEBUG_PREFIX")
    if not prefix:
        prefix = f"{_get_template_prefix()}debug/"
    if prefix and not prefix.endswith("/"):
        prefix = f"{prefix}/"
    return prefix


def _resolve_fax_template_config(facility_id: str) -> tuple[dict, dict]:
    master = config_service.load_facility_master()
    base_template = master.get("fax_template_base", {})
    facility = next(
        (fac for fac in master.get("facilities", []) if fac.get("facility_id") == facility_id),
        None,
    )
    if not facility:
        raise HTTPException(status_code=404, detail="facility not found in master")
    fax_template = config_service._merge_template(  # type: ignore[attr-defined]
        base_template,
        facility.get("fax_template"),
    )
    fax_template = config_service._merge_template(  # type: ignore[attr-defined]
        fax_template,
        facility.get("fax_template_override"),
    )
    return facility, fax_template


@router.get("/ocr/templates", dependencies=[Depends(require_role("admin"))])
def list_templates(limit: int = 100):
    return {"templates": ocr_registry_service.list_templates(limit=limit)}


@router.get("/ocr/templates/{template_id}", dependencies=[Depends(require_role("admin"))])
def get_template(template_id: str):
    template = ocr_registry_service.get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"template": template}


@router.get("/ocr/templates/{template_id}/image", dependencies=[Depends(require_role("admin"))])
def get_template_image(template_id: str):
    template = ocr_registry_service.get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    data = template.get("data") or {}
    uri = data.get("template_image_gcs_uri") or data.get("template_image_uri")
    if not uri:
        raise HTTPException(status_code=404, detail="template image not found")
    image_bytes = load_bytes_from_uri(str(uri))
    return Response(content=image_bytes, media_type="image/png")


@router.get(
    "/ocr/templates/{template_id}/debug/{debug_name}",
    dependencies=[Depends(require_role("admin"))],
)
def get_template_debug_image(template_id: str, debug_name: str):
    template = ocr_registry_service.get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    data = template.get("data") or {}
    debug_images = data.get("debug_images") or {}
    uri = debug_images.get(debug_name)
    if not uri:
        raise HTTPException(status_code=404, detail="debug image not found")
    image_bytes = load_bytes_from_uri(str(uri))
    return Response(content=image_bytes, media_type="image/png")


@router.post("/ocr/templates", dependencies=[Depends(require_role("admin"))])
def create_template(body: dict):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be an object")
    template_id = body.get("template_id") or body.get("id")
    data = body.get("template") if isinstance(body.get("template"), dict) else body.get("data")
    if data is None and isinstance(body.get("template"), dict):
        data = body.get("template")
    if data is None and isinstance(body.get("data"), dict):
        data = body.get("data")
    if data is None:
        data = {k: v for k, v in body.items() if k not in {"template_id", "id", "data", "template"}}
    if not template_id:
        raise HTTPException(status_code=400, detail="template_id is required")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="template data must be an object")
    template = ocr_registry_service.save_template(str(template_id), data)
    return {"template": template}


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
    if not facility_id.strip():
        raise HTTPException(status_code=400, detail="facility_id is required")
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="PDF file is required")
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="PDF file is empty")

    template_id = _sanitize_template_id(template_id or _default_template_id(facility_id))
    bucket = _get_template_bucket()
    if not bucket:
        raise HTTPException(status_code=500, detail="OCR template bucket is not configured")

    prefix = _get_template_prefix()
    debug_prefix = _get_debug_prefix()

    try:
        result = build_template_from_pdf(
            pdf_bytes=pdf_bytes,
            facility_id=facility_id,
            template_id=template_id,
            rows=rows,
            cols=cols,
            dpi=dpi or 350,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to build template: {exc}") from exc

    object_name = f"{prefix}{template_id}.png"
    template_uri = save_bytes_to_gcs(
        bucket, object_name, result.template_image, content_type="image/png"
    )

    debug_uris: dict[str, str] = {}
    for name, data in result.debug_images.items():
        debug_name = f"{template_id}_{name}"
        debug_uris[name] = save_bytes_to_gcs(
            bucket, f"{debug_prefix}{debug_name}", data, content_type="image/png"
        )

    template_payload = dict(result.template)
    template_payload["template_image_gcs_uri"] = template_uri
    if debug_uris:
        template_payload["debug_images"] = debug_uris

    facility, _ = _resolve_fax_template_config(facility_id)
    template = ocr_registry_service.save_template(template_id, template_payload)

    facility_payload: dict[str, object] = {
        "facility_id": facility_id,
        "template_id": template_id,
    }
    facility_name = facility.get("facility_name")
    if isinstance(facility_name, str) and facility_name.strip():
        facility_payload["facility_name"] = facility_name.strip()
    if isinstance(facility_prompt, str) and facility_prompt.strip():
        facility_payload["main_ocr_facility_prompt"] = facility_prompt.strip()
    facility_doc = ocr_registry_service.save_facility(facility_id, facility_payload)

    return {"template": template, "facility": facility_doc, "debug_images": debug_uris}


@router.put("/ocr/templates/{template_id}", dependencies=[Depends(require_role("admin"))])
def update_template(template_id: str, body: dict):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be an object")
    data = body.get("template") if isinstance(body.get("template"), dict) else body.get("data")
    if data is None:
        data = body
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="template data must be an object")
    template = ocr_registry_service.save_template(template_id, data)
    return {"template": template}


@router.delete("/ocr/templates/{template_id}", dependencies=[Depends(require_role("admin"))])
def delete_template(template_id: str):
    ocr_registry_service.delete_template(template_id)
    return {"deleted": True}


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
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be an object")
    template_id = body.get("template_id")
    if not template_id:
        raise HTTPException(status_code=400, detail="template_id is required")
    note = body.get("note") if isinstance(body.get("note"), str) else None
    resolved = ocr_registry_service.resolve_unclassified(job_id, str(template_id), note)
    return {"resolved": resolved}


@router.get("/ocr/facilities", dependencies=[Depends(require_role("admin"))])
def list_facilities(limit: int = 100):
    return {"facilities": ocr_registry_service.list_facilities(limit=limit)}


@router.get("/ocr/facilities/{facility_id}", dependencies=[Depends(require_role("admin"))])
def get_facility(facility_id: str):
    facility = ocr_registry_service.get_facility(facility_id)
    if not facility:
        raise HTTPException(status_code=404, detail="Facility not found")
    return {"facility": facility}


@router.put("/ocr/facilities/{facility_id}", dependencies=[Depends(require_role("admin"))])
def update_facility(facility_id: str, body: dict):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be an object")
    data = body.get("facility") if isinstance(body.get("facility"), dict) else body.get("data")
    if data is None:
        data = body
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="facility data must be an object")
    facility = ocr_registry_service.save_facility(facility_id, data)
    return {"facility": facility}


@router.delete("/ocr/facilities/{facility_id}", dependencies=[Depends(require_role("admin"))])
def delete_facility(facility_id: str):
    ocr_registry_service.delete_facility(facility_id)
    return {"deleted": True}


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
