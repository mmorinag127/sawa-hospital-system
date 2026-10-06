from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException

from src.api.auth import require_role
from src.services import canonical_facility_store, facility_master_service
from src.services.config_validator import validate_facility_master

router = APIRouter()


@contextmanager
def canonical_errors():
    try:
        yield
    except canonical_facility_store.CanonicalStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/facility-master", dependencies=[Depends(require_role("operator"))])
def get_facility_master():
    with canonical_errors():
        snapshot = facility_master_service.get_snapshot()
    return {
        "facility_master": snapshot.master,
        "revision": snapshot.revision,
        "validation": validate_facility_master(snapshot.master),
        "path": snapshot.source,
        "source": "canonical_json",
    }


@router.put("/facility-master", dependencies=[Depends(require_role("operator"))])
def update_facility_master(body: dict):
    master = body.get("facility_master") if isinstance(body, dict) else None
    if not isinstance(master, dict):
        raise HTTPException(status_code=400, detail="facility_master must be an object")
    validation = validate_facility_master(master)
    if validation["errors"]:
        raise HTTPException(status_code=400, detail={"errors": validation["errors"]})
    with canonical_errors():
        return facility_master_service.save_master(master, expected_revision=body.get("expected_revision"))
