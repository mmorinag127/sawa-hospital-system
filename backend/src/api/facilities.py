from fastapi import APIRouter, HTTPException, Depends, File, Form, UploadFile, status

from src.services import facility_service, config_service, order_form_service
from src.services.config_validator import validate_facility_config
from src.api.auth import require_role

router = APIRouter()


@router.get("", dependencies=[Depends(require_role("operator"))])
def list_facilities():
    return {"facilities": facility_service.list_facilities()}


@router.get("/{facility_id}", dependencies=[Depends(require_role("operator"))])
def get_facility(facility_id: str):
    facility = facility_service.get_facility(facility_id)
    if not facility:
        raise HTTPException(status_code=404, detail="not found")
    config = facility_service.get_facility_config(facility_id) or {}
    resolved = config_service.get_facility_config(facility_id)
    return {
        "facility": facility,
        "config": config,
        "resolved_config": resolved,
    }


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role("admin"))])
def create_facility(body: dict):
    if "name" not in body:
        raise HTTPException(status_code=400, detail="name required")
    fac = facility_service.create_facility(body["name"], body.get("areas", []))
    return fac


@router.put("/{facility_id}", dependencies=[Depends(require_role("admin"))])
def update_facility(facility_id: str, body: dict):
    updated = facility_service.update_facility(facility_id, body.get("name"), body.get("areas"))
    if not updated:
        raise HTTPException(status_code=404, detail="not found")
    return updated


@router.put("/{facility_id}/config", dependencies=[Depends(require_role("admin"))])
def update_config(facility_id: str, body: dict):
    config = body.get("config") if isinstance(body, dict) and "config" in body else body
    if not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="config must be an object")
    validation = validate_facility_config(config)
    if validation["errors"]:
        raise HTTPException(status_code=400, detail={"errors": validation["errors"]})
    updated = facility_service.update_config(facility_id, config)
    if not updated:
        raise HTTPException(status_code=404, detail="not found")
    resolved = config_service.get_facility_config(facility_id)
    return {"updated": True, "validation": validation, "resolved_config": resolved}


@router.post("/{facility_id}/order-form-source-workbook", dependencies=[Depends(require_role("admin"))])
async def upload_order_form_source_workbook(
    facility_id: str,
    file: UploadFile = File(...),
    month_id: str | None = Form(default=None),
):
    facility = facility_service.get_facility(facility_id)
    if not facility:
        raise HTTPException(status_code=404, detail="not found")
    filename = file.filename or "source_workbook.xlsx"
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="source workbook must be .xlsx or .xlsm")
    try:
        uploaded = order_form_service.save_facility_source_workbook_upload(
            facility_id=facility_id,
            filename=filename,
            data=await file.read(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"source workbook upload failed: {exc}") from exc

    current_config = facility_service.get_facility_config(facility_id) or {}
    next_config = dict(current_config)
    normalized_month = str(month_id or "").strip()
    if normalized_month:
        month_sources = dict(next_config.get("order_form_month_source_uris") or {})
        month_sources[normalized_month] = uploaded["uri"]
        next_config["order_form_month_source_uris"] = month_sources
    else:
        next_config["order_form_source_workbook_uri"] = uploaded["uri"]
    next_config["order_form_source_workbook_sha256"] = uploaded["sha256"]
    next_config["order_form_source_workbook_filename"] = filename

    validation = validate_facility_config(next_config)
    if validation["errors"]:
        raise HTTPException(status_code=400, detail={"errors": validation["errors"]})
    updated = facility_service.update_config(facility_id, next_config)
    if not updated:
        raise HTTPException(status_code=404, detail="not found")
    resolved = config_service.get_facility_config(facility_id)
    return {
        "updated": True,
        "config": next_config,
        "source_workbook": uploaded,
        "validation": validation,
        "resolved_config": resolved,
    }
