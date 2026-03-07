from fastapi import APIRouter, HTTPException, Depends, status

from src.services import facility_service, config_service
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
