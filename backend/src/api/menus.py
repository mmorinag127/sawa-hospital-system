from datetime import datetime
import json

from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, Form, Query
from fastapi.responses import Response
from loguru import logger

from src.services import menu_service
from src.api.auth import require_role
from src.services.menu_upload_archive_service import save_monthly_menu_upload

router = APIRouter()


@router.get("/scope-options", dependencies=[Depends(require_role("operator"))])
def get_menu_scope_options():
    return menu_service.list_menu_scope_options()


@router.get("/latest", dependencies=[Depends(require_role("operator"))])
def get_latest_menu():
    menu = menu_service.get_latest_menu()
    if not menu:
        raise HTTPException(status_code=404, detail="not found")
    return menu


@router.get("/{month_id}", dependencies=[Depends(require_role("operator"))])
def get_menu(month_id: str, facility_id: str | None = Query(default=None)):
    menu = menu_service.get_menu_for_facility(month_id, facility_id) if facility_id else menu_service.get_menu(month_id)
    if not menu:
        raise HTTPException(status_code=404, detail="not found")
    return menu


@router.post("", dependencies=[Depends(require_role("operator"))])
async def upload_menu(
    month_id: str,
    file: UploadFile = File(...),
    sheet_name: str | None = None,
    scope_type: str | None = None,
    scope_value: str | None = None,
    review_resolutions: str | None = Form(None),
):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        scope_override = menu_service.resolve_menu_upload_scope(scope_type, scope_value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    parsed_review_resolutions: list[dict] | None = None
    if review_resolutions and review_resolutions.strip():
        try:
            raw = json.loads(review_resolutions)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"invalid review_resolutions: {exc.msg}")
        if not isinstance(raw, list):
            raise HTTPException(status_code=400, detail="review_resolutions must be a list")
        parsed_review_resolutions = raw
    upload_metadata: dict[str, object] = {}
    if file.filename:
        try:
            archived = save_monthly_menu_upload(
                month_id=month_id,
                file_bytes=content,
                original_filename=file.filename,
                uploaded_at=datetime.utcnow(),
            )
            upload_metadata = {
                "file_uri": archived.file_uri,
                "content_sha256": archived.content_sha256,
            }
            if scope_override:
                upload_metadata["scope_override"] = scope_override
        except Exception as exc:  # noqa: BLE001
            logger.warning("Monthly menu upload archive failed", month_id=month_id, error=str(exc))
            upload_metadata = {
                "archive_error": str(exc),
            }
            if scope_override:
                upload_metadata["scope_override"] = scope_override
    try:
        _, replaced, item_count = menu_service.create_menu(
            month_id=month_id,
            file_bytes=content,
            filename=file.filename,
            sheet_name=sheet_name,
            upload_metadata=upload_metadata,
            scope_override=scope_override,
            menu_master_resolutions=parsed_review_resolutions,
            require_menu_master_review=True,
        )
    except menu_service.MenuMasterResolutionRequired as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "menu_master_review_required",
                "issues": exc.issues,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "created": True,
        "replaced": replaced,
        "item_count": item_count,
        "scope_override": scope_override,
    }


@router.get("/{month_id}/uploads", dependencies=[Depends(require_role("operator"))])
def list_menu_uploads(month_id: str):
    return {"items": menu_service.list_menu_uploads(month_id)}


@router.get("/{month_id}/uploads/{upload_id}/download", dependencies=[Depends(require_role("operator"))])
def download_menu_upload(month_id: str, upload_id: str):
    payload = menu_service.get_menu_upload_download(month_id, upload_id)
    if not payload:
        raise HTTPException(status_code=404, detail="not found")
    if not payload.get("download_available"):
        raise HTTPException(status_code=404, detail="upload file is not available")
    filename = str(payload.get("filename") or "monthly-menu.xlsx")
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(
        content=payload["bytes"],
        media_type=str(payload.get("media_type") or "application/octet-stream"),
        headers=headers,
    )


@router.put("/{month_id}/items/{item_id}", dependencies=[Depends(require_role("operator"))])
def update_menu_item(month_id: str, item_id: str, body: dict):
    result = menu_service.update_item_status(month_id, item_id, body)
    if result == "updated":
        return {"updated": True}
    if result in {"not_found", "month_mismatch"}:
        raise HTTPException(status_code=404, detail="not found")
    if result == "conflict":
        raise HTTPException(status_code=409, detail="duplicate monthly menu item")
    if result == "invalid_name":
        raise HTTPException(status_code=400, detail="name is required")
    return {"updated": True}


@router.post("/{month_id}/master-checks/{item_id}/resolve", dependencies=[Depends(require_role("operator"))])
def resolve_menu_master_check(month_id: str, item_id: str, body: dict):
    try:
        payload = menu_service.resolve_menu_master_check(month_id, item_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not payload:
        raise HTTPException(status_code=404, detail="not found")
    return payload


@router.post("/condiments", dependencies=[Depends(require_role("operator"))])
async def upload_condiments(file: UploadFile = File(...), sheet_name: str | None = None):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        result = menu_service.import_condiments(
            file_bytes=content,
            filename=file.filename,
            sheet_name=sheet_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result
