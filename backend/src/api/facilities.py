import os

from fastapi import APIRouter, HTTPException, Depends, File, Form, UploadFile, status
from pydantic import BaseModel

from src.services import (
    facility_master_service,
    facility_service,
    config_service,
    master_order_form_template_service,
    order_form_service,
)
from src.services.config_validator import validate_facility_config
from src.api.auth import require_role
from src.api.facility_master import canonical_errors
from src.services.storage_service import get_default_output_bucket
from src.services.canonical_facility_store import check_revision
from src.api.facility_version_contract import (
    DatedFacilityResponse,
    FacilityVersionBody,
    FacilityVersionSaveResponse,
    FacilityVersionsResponse,
)

router = APIRouter()


@router.get("/{facility_id}/resolved", dependencies=[Depends(require_role("operator"))], response_model=None, responses={200: {"model": DatedFacilityResponse}})
def resolve_dated_facility(facility_id: str, effective_date: str | None = None):
    with canonical_errors():
        return config_service.get_facility_config_for_date(facility_id, effective_date)


@router.get("/{facility_id}/versions", dependencies=[Depends(require_role("operator"))], response_model=None, responses={200: {"model": FacilityVersionsResponse}})
def get_facility_versions(facility_id: str):
    with canonical_errors():
        snapshot = facility_master_service.get_snapshot()
        return facility_service.versions_response(snapshot.master, facility_id, snapshot.revision)


@router.post("/{facility_id}/versions", dependencies=[Depends(require_role("operator"))], response_model=None, responses={200: {"model": FacilityVersionSaveResponse}})
def create_facility_version(facility_id: str, body: FacilityVersionBody):
    with canonical_errors():
        version = body.get("version")
        return facility_service.save_facility_version(facility_id, version.get("version_id") if isinstance(version, dict) else None,
                                                     version, expected_revision=body.get("expected_revision"), create=True)


@router.put("/{facility_id}/versions/{version_id}", dependencies=[Depends(require_role("operator"))], response_model=None, responses={200: {"model": FacilityVersionSaveResponse}})
def update_facility_version(facility_id: str, version_id: str, body: FacilityVersionBody):
    with canonical_errors():
        return facility_service.save_facility_version(facility_id, version_id, body.get("version"),
                                                     expected_revision=body.get("expected_revision"), create=False)


class FacilityFaxTemplateUpdateBody(BaseModel):
    fax_template_id: str
    fax_template_ids: list[str] | None = None


@router.get("", dependencies=[Depends(require_role("operator"))])
def list_facilities():
    with canonical_errors():
        snapshot = facility_master_service.get_snapshot()
        return {"facilities": facility_service.list_facilities(master=snapshot.master),
                "revision": snapshot.revision, "source": "canonical_json"}


@router.get("/fax-template-options", dependencies=[Depends(require_role("operator"))])
def list_fax_template_options():
    templates: list[dict] = []
    return {"templates": templates}


@router.get("/{facility_id}", dependencies=[Depends(require_role("operator"))])
def get_facility(facility_id: str):
    with canonical_errors():
        snapshot = facility_master_service.get_snapshot()
        response = facility_service.facility_response(snapshot.master, facility_id, snapshot.revision)
    response["validation"] = validate_facility_config(response["config"])
    # Undated metadata cannot identify a dated canonical template version.
    response["version_resolution"] = {"status": "unversioned_reference_only", "dated_endpoint": f"/facilities/{facility_id}/resolved"}
    response["template_version_reference"] = None
    response["template_version_reference_status"] = "unavailable"
    return response


@router.get("/{facility_id}/generated-fax-template-diagnostics", dependencies=[Depends(require_role("operator"))])
def get_generated_fax_template_diagnostics(facility_id: str, week_value: str | None = None):
    with canonical_errors():
        snapshot = facility_master_service.get_snapshot()
        resolved = facility_service.facility_response(snapshot.master, facility_id, snapshot.revision)["resolved_config"]
    if not isinstance(resolved, dict):
        raise HTTPException(status_code=400, detail="facility_template_unresolved")
    try:
        diagnostics = master_order_form_template_service.build_facility_template_diagnostics(
            facility_config=resolved,
            week_value=week_value,
        )
        return {**diagnostics, "canonical_revision": snapshot.revision, "source": "canonical_json",
                "version_resolution": {"status": "date_version_integration_required"}}
    except master_order_form_template_service.FacilityTemplateBuildError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{facility_id}/fax-template", dependencies=[Depends(require_role("operator"))])
def update_facility_fax_template(facility_id: str, body: FacilityFaxTemplateUpdateBody):
    with canonical_errors():
        facility = facility_service.get_facility(facility_id)
    if not facility:
        raise HTTPException(status_code=404, detail="not found")
    raise HTTPException(
        status_code=410,
        detail={
            "error": "legacy_fax_template_registry_disabled",
            "message": "施設テンプレートはfacility_master.template.jsonを正本とし、旧YAML registryによる登録は使用できません。",
        },
    )


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role("admin"))])
def create_facility(body: dict):
    if "name" not in body:
        raise HTTPException(status_code=400, detail="name required")
    with canonical_errors():
        return facility_service.create_facility(body["name"], body.get("areas", []), expected_revision=body.get("expected_revision"))


@router.put("/{facility_id}", dependencies=[Depends(require_role("operator"))])
def update_facility(facility_id: str, body: dict):
    with canonical_errors():
        return facility_service.update_facility(facility_id, body.get("name"), **({"areas": body["areas"]} if "areas" in body else {}), expected_revision=body.get("expected_revision"))


@router.put("/{facility_id}/config", dependencies=[Depends(require_role("admin"))])
def update_config(facility_id: str, body: dict):
    config = body.get("config")
    if not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="config must be an object")
    validation = validate_facility_config(config)
    if validation["errors"]:
        raise HTTPException(status_code=400, detail={"errors": validation["errors"]})
    with canonical_errors():
        return facility_service.update_config(facility_id, config, expected_revision=body.get("expected_revision"))


@router.post("/{facility_id}/order-form-source-workbook", dependencies=[Depends(require_role("admin"))])
def upload_order_form_source_workbook(
    facility_id: str,
    file: UploadFile = File(...),
    month_id: str | None = Form(default=None),
    expected_revision: str = Form(...),
):
    with canonical_errors():
        snapshot = facility_master_service.get_snapshot()
        check_revision(snapshot, expected_revision)
        current = facility_service.facility_response(snapshot.master, facility_id, snapshot.revision)
    if os.getenv("K_SERVICE") and not get_default_output_bucket():
        raise HTTPException(status_code=503, detail="source_workbook_gcs_storage_required")
    filename = file.filename or "source_workbook.xlsx"
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="source workbook must be .xlsx or .xlsm")
    try:
        uploaded = order_form_service.save_facility_source_workbook_upload(
            facility_id=facility_id,
            filename=filename,
            data=file.file.read(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "source_workbook_upload_invalid", "message": str(exc),
                                                    "config_saved": False, "upload_may_exist_unreferenced": True}) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail={"error": "source_workbook_upload_failed",
                                                    "config_saved": False, "upload_may_exist_unreferenced": True}) from exc

    if os.getenv("K_SERVICE") and (uploaded.get("storage") != "gcs" or not str(uploaded.get("uri", "")).startswith("gs://")):
        raise HTTPException(status_code=503, detail="source_workbook_gcs_storage_required")

    next_config = dict(current["config"])
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
    try:
        with canonical_errors():
            saved = facility_service.update_config(facility_id, next_config, expected_revision=expected_revision)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": exc.detail}
        outcome = True if detail.get("saved") else (False if exc.status_code < 500 else "unknown")
        raise HTTPException(status_code=exc.status_code, detail={**detail, "source_workbook": uploaded,
                                                               "config_saved": outcome,
                                                               "unreferenced_upload": outcome is False}) from exc
    return {**saved, "source_workbook": uploaded, "validation": validation}
