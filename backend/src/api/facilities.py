import json

from fastapi import APIRouter, HTTPException, Depends, File, Form, UploadFile, status
from pydantic import BaseModel

from src.db import session_scope
from src.services import (
    config_service,
    facility_service,
    facility_template_version_service,
    master_order_form_template_service,
    order_form_service,
)
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


_TEMPLATE_DEFINITION_KEYS = ("fax_template_id", "fax_template_ids", "fax_template_override")


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _template_definition_change_error(facility_id: str, next_config: dict) -> dict | None:
    current_config = facility_service.get_facility_config(facility_id) or {}
    for key in _TEMPLATE_DEFINITION_KEYS:
        if key not in next_config:
            continue
        if _stable_json(next_config.get(key)) != _stable_json(current_config.get(key)):
            return {
                "error": "facility_template_definition_update_requires_versioned_template_endpoint",
                "field": key,
                "message": "Use /facilities/{facility_id}/fax-template or workflow-v2 facility template column editing.",
            }
    return None


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
    with session_scope() as session:
        active_version = facility_template_version_service.get_active_template_version(session, facility_id)
        if active_version is not None and isinstance(resolved, dict):
            resolved = dict(resolved)
            fax_template = dict(resolved.get("fax_template") or {})
            fax_template["facility_template_version_id"] = active_version.id
            fax_template["facility_template_version_digest"] = active_version.template_digest
            resolved["fax_template"] = fax_template
            resolved["facility_template_version_id"] = active_version.id
            resolved["facility_template_version"] = facility_template_version_service.serialize_template_version(active_version)
    return {
        "facility": facility,
        "config": config,
        "resolved_config": resolved,
    }


@router.get("/{facility_id}/generated-fax-template-diagnostics", dependencies=[Depends(require_role("operator"))])
def get_generated_fax_template_diagnostics(facility_id: str, week_value: str | None = None):
    facility = facility_service.get_facility(facility_id)
    if not facility:
        raise HTTPException(status_code=404, detail="not found")
    resolved = config_service.get_facility_config(facility_id)
    if not isinstance(resolved, dict):
        raise HTTPException(status_code=400, detail="facility_template_unresolved")
    try:
        return master_order_form_template_service.build_facility_template_diagnostics(
            facility_config=resolved,
            week_value=week_value,
        )
    except master_order_form_template_service.FacilityTemplateBuildError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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

    with session_scope() as session:
        result, error = facility_template_version_service.save_template_registration_for_facility(
            session,
            facility_id=facility_id,
            fax_template_id=primary_template_id,
            fax_template_ids=template_ids,
            actor="facility-fax-template-registration",
        )
    if error == "fax_template_not_found":
        raise HTTPException(status_code=400, detail=result or {"error": error})
    if error == "facility_not_found":
        raise HTTPException(status_code=404, detail="not found")
    if error == "validation_error":
        raise HTTPException(status_code=400, detail=result or {"error": error})
    if error:
        raise HTTPException(status_code=400, detail=error)
    return result


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role("admin"))])
def create_facility(body: dict):
    if "name" not in body:
        raise HTTPException(status_code=400, detail="name required")
    fac = facility_service.create_facility(body["name"], body.get("areas", []))
    return fac


@router.put("/{facility_id}", dependencies=[Depends(require_role("operator"))])
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
    template_change_error = _template_definition_change_error(facility_id, config)
    if template_change_error:
        raise HTTPException(status_code=400, detail=template_change_error)
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
