from fastapi import APIRouter, HTTPException, Depends, File, Form, UploadFile, status
from pydantic import BaseModel

from src.services import facility_service, config_service, order_form_service
from src.services.config_validator import validate_facility_config
from src.api.auth import require_role

router = APIRouter()


class FacilityFaxTemplateUpdateBody(BaseModel):
    fax_template_id: str
    fax_template_ids: list[str] | None = None


def _template_option_payload(template_id: str, template: dict) -> dict:
    columns = template.get("columns")
    columns = columns if isinstance(columns, list) else []
    quantity_headers = [
        str(column.get("header") or column.get("name") or "").strip()
        for column in columns
        if isinstance(column, dict) and str(column.get("role") or "").strip() == "quantity"
    ]
    return {
        "template_id": template_id,
        "label": str(template.get("description") or template.get("template_family") or template_id),
        "description": template.get("description"),
        "template_family": template.get("template_family"),
        "template_version": template.get("template_version"),
        "quantity_headers": [item for item in quantity_headers if item],
    }


def _normalize_template_ids(primary_template_id: str, template_ids: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for item in [primary_template_id, *(template_ids or [])]:
        token = str(item or "").strip()
        if token and token not in normalized:
            normalized.append(token)
    return normalized


@router.get("", dependencies=[Depends(require_role("operator"))])
def list_facilities():
    return {"facilities": facility_service.list_facilities()}


@router.get("/fax-template-options", dependencies=[Depends(require_role("operator"))])
def list_fax_template_options():
    registry = config_service.load_fax_template_registry()
    templates = [
        _template_option_payload(template_id, template)
        for template_id, template in sorted(registry.items())
        if isinstance(template_id, str) and isinstance(template, dict)
    ]
    return {"templates": templates}


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


@router.put("/{facility_id}/fax-template", dependencies=[Depends(require_role("operator"))])
def update_facility_fax_template(facility_id: str, body: FacilityFaxTemplateUpdateBody):
    facility = facility_service.get_facility(facility_id)
    if not facility:
        raise HTTPException(status_code=404, detail="not found")
    registry = config_service.load_fax_template_registry()
    primary_template_id = str(body.fax_template_id or "").strip()
    if not primary_template_id:
        raise HTTPException(status_code=400, detail="fax_template_id_required")
    template_ids = _normalize_template_ids(primary_template_id, body.fax_template_ids)
    missing_template_ids = [template_id for template_id in template_ids if template_id not in registry]
    if missing_template_ids:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "fax_template_not_found",
                "template_ids": missing_template_ids,
            },
        )

    current_config = facility_service.get_facility_config(facility_id) or {}
    next_config = dict(current_config)
    next_config["fax_template_id"] = primary_template_id
    next_config["fax_template_ids"] = template_ids
    next_config["facility_template_source"] = "operator_override"

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
        "validation": validation,
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
