from fastapi import APIRouter, Depends, HTTPException, status

from src.api.auth import require_role
from src.services import facility_master_service
from src.services.config_validator import validate_facility_master

router = APIRouter()


@router.get("/facility-master", dependencies=[Depends(require_role("operator"))])
def get_facility_master():
    master = facility_master_service.get_master()
    validation = validate_facility_master(master)
    return {
        "facility_master": master,
        "validation": validation,
        "path": str(facility_master_service.get_master_path()),
    }


@router.put("/facility-master", dependencies=[Depends(require_role("operator"))])
def update_facility_master(body: dict):
    master = body.get("facility_master") if isinstance(body, dict) else None
    if master is None:
        master = body
    if not isinstance(master, dict):
        raise HTTPException(status_code=400, detail="facility_master must be an object")
    validation = validate_facility_master(master)
    if validation["errors"]:
        raise HTTPException(status_code=400, detail={"errors": validation["errors"]})
    facility_master_service.save_master(master)
    return {
        "facility_master": master,
        "validation": validation,
        "path": str(facility_master_service.get_master_path()),
        "updated": True,
    }
