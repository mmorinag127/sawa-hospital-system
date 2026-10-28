from datetime import datetime
import json
from pathlib import Path
import re
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, Form, Query, Header
from fastapi.responses import Response
from loguru import logger
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.exc import StaleDataError

from src.services import menu_service
from src.services import menu_edit_revision_service as revisions
from src.api.auth import require_role
from src.api.menu_import_contract import SavedImportResponse, ImportListResponse, ImportApplyResponse, MonthlyCommandResponse
from src.services.menu_upload_archive_service import save_monthly_menu_upload

router = APIRouter()


def _monthly_call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except revisions.RevisionPreconditionRequired as exc:
        raise HTTPException(428, str(exc)) from exc
    except revisions.RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except revisions.RevisionSchemaNotMigrated as exc:
        raise HTTPException(503, str(exc)) from exc
    except StaleDataError as exc:
        raise HTTPException(409, "monthly import changed; working input retained") from exc
    except OperationalError as exc:
        original = exc.orig
        code = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
        sqlite_code = getattr(original, "sqlite_errorcode", None)
        if code in {"40001", "40P01"} or (sqlite_code is not None and (sqlite_code & 255) in {5, 6}):
            raise HTTPException(409, "monthly input conflict; saved source and working input retained") from exc
        raise
    except menu_service.MenuMasterResolutionRequired as exc:
        raise HTTPException(409, {"code": "menu_master_review_required", "issues": exc.issues}) from exc
    except FileNotFoundError as exc:
        raise HTTPException(409, "saved source archive is unavailable; no menu applied") from exc
    except ValueError as exc:
        status = 404 if str(exc) in {"monthly target not found", "saved menu import not found for this month"} else 400
        raise HTTPException(status, str(exc)) from exc


def _attachment_content_disposition(filename: str) -> str:
    normalized = str(filename or "").strip() or "monthly-menu.xlsx"
    path = Path(normalized)
    suffix = path.suffix or ".xlsx"
    ascii_stem = path.stem.encode("ascii", "ignore").decode("ascii").strip().replace('"', "")
    ascii_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_stem).strip("._-")
    ascii_fallback = f"{ascii_stem or 'monthly-menu'}{suffix}"
    encoded = quote(normalized, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


@router.get("/scope-options", dependencies=[Depends(require_role("operator"))])
def get_menu_scope_options():
    return menu_service.list_menu_scope_options()


@router.get("/latest", dependencies=[Depends(require_role("operator"))])
def get_latest_menu(identity_only: bool = False):
    menu = menu_service.get_latest_menu_identity() if identity_only else menu_service.get_latest_menu(allow_unresolved=True)
    if not menu:
        raise HTTPException(status_code=404, detail="not found")
    return menu


@router.get("", dependencies=[Depends(require_role("operator"))])
def list_recent_menus(limit: int = Query(default=12, ge=1, le=36)):
    return {"items": menu_service.list_recent_menus(limit=limit)}


@router.get("/summaries", dependencies=[Depends(require_role("operator"))])
def get_menu_summaries(month_ids: list[str] = Query(min_length=1, max_length=100)):
    if any(not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) for month in month_ids):
        raise HTTPException(422, "explicit YYYY-MM months are required")
    return {"items": menu_service.get_menu_summaries(month_ids)}


@router.get("/{month_id}", dependencies=[Depends(require_role("operator"))])
def get_menu(month_id: str, facility_id: str | None = Query(default=None), editor: bool = Query(default=False)):
    if editor and facility_id:
        raise HTTPException(422, "editor read requires the complete recorded monthly source; facility projection is read-only")
    if editor:
        menu = _monthly_call(menu_service.get_monthly_editor_snapshot, month_id)
    else:
        menu = menu_service.get_menu_for_facility(month_id, facility_id, allow_unresolved=True) if facility_id else menu_service.get_menu(month_id, allow_unresolved=True)
    if not menu:
        raise HTTPException(status_code=404, detail="not found")
    return menu


@router.post("", dependencies=[Depends(require_role("operator"))], response_model=None, responses={200: {"model": SavedImportResponse}})
def upload_menu(
    month_id: str,
    file: UploadFile = File(...),
    sheet_name: str | None = None,
    scope_type: str | None = None,
    scope_value: str | None = None,
    review_resolutions: str | None = Form(None),
    diet_types: str | None = None,
):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month_id):
        raise HTTPException(422, "explicit YYYY-MM month is required")
    content = file.file.read()
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
    if parsed_review_resolutions is not None:
        raise HTTPException(400, "save and review the import receipt before applying")
    selected_diets = None
    if diet_types is not None:
        try:
            selected_diets = json.loads(diet_types)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "diet_types must be an explicit JSON list") from exc
        if not isinstance(selected_diets, list) or not selected_diets or any(
            not isinstance(value, str) or menu_service.normalize_diet_type(value) != value for value in selected_diets
        ):
            raise HTTPException(422, "explicit normalized diet_types list is required")
    if not file.filename:
        raise HTTPException(400, "original filename is required")
    try:
        archived = save_monthly_menu_upload(
            month_id=month_id,
            file_bytes=content,
            original_filename=file.filename,
            uploaded_at=datetime.utcnow(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.warning("Monthly menu original archive failed", month_id=month_id)
        raise HTTPException(503, "original upload archive failed; no menu applied") from exc
    return _monthly_call(menu_service.save_menu_import, month_id, content, archived,
        sheet_name=sheet_name, scope_override=scope_override, diet_types=selected_diets)


@router.get("/{month_id}/imports", dependencies=[Depends(require_role("operator"))], response_model=None, responses={200: {"model": ImportListResponse}})
def list_menu_imports(month_id: str, before_id: str | None = None):
    return _monthly_call(menu_service.list_menu_imports, month_id, before_id=before_id)


@router.get("/{month_id}/imports/{import_id}", dependencies=[Depends(require_role("operator"))], response_model=None, responses={200: {"model": SavedImportResponse}})
def read_menu_import(month_id: str, import_id: str):
    return _monthly_call(menu_service.read_menu_import, month_id, import_id)


@router.post("/{month_id}/imports/{import_id}/review", dependencies=[Depends(require_role("operator"))], response_model=None, responses={200: {"model": SavedImportResponse}})
def review_menu_import(month_id: str, import_id: str, body: dict, if_match: str | None = Header(None)):
    if not isinstance(body.get("resolutions"), list):
        raise HTTPException(422, "explicit resolutions list is required")
    return _monthly_call(menu_service.review_menu_import, month_id, import_id, if_match, body["resolutions"], input_expected=body.get("input_revision"))


@router.get("/{month_id}/imports/{import_id}/download", dependencies=[Depends(require_role("operator"))], response_model=None, responses={200: {"content": {"*/*": {"schema": {"type": "string", "format": "binary"}}}}})
def download_menu_import(month_id: str, import_id: str):
    source = _monthly_call(menu_service.get_menu_import_download, month_id, import_id)
    return Response(content=source["bytes"], media_type=source["media_type"],
        headers={"Content-Disposition": _attachment_content_disposition(source["filename"])})


@router.post("/{month_id}/imports/{import_id}/apply", dependencies=[Depends(require_role("operator"))], response_model=None, responses={200: {"model": ImportApplyResponse}})
def apply_menu_import(month_id: str, import_id: str, if_match: str | None = Header(None)):
    return _monthly_call(menu_service.review_menu_import, month_id, import_id, if_match, None, apply=True)


@router.get("/{month_id}/uploads", dependencies=[Depends(require_role("operator"))])
def list_menu_uploads(month_id: str, before_id: str | None = None):
    try:
        return menu_service.list_menu_uploads(month_id, before_id=before_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/{month_id}/uploads/{upload_id}/download", dependencies=[Depends(require_role("operator"))])
def download_menu_upload(month_id: str, upload_id: str):
    payload = menu_service.get_menu_upload_download(month_id, upload_id)
    if not payload:
        raise HTTPException(status_code=404, detail="not found")
    if not payload.get("download_available"):
        raise HTTPException(status_code=404, detail="upload file is not available")
    filename = str(payload.get("filename") or "monthly-menu.xlsx")
    headers = {"Content-Disposition": _attachment_content_disposition(filename)}
    return Response(
        content=payload["bytes"],
        media_type=str(payload.get("media_type") or "application/octet-stream"),
        headers=headers,
    )


@router.put("/{month_id}/items/{item_id}", dependencies=[Depends(require_role("operator"))])
def update_menu_item(month_id: str, item_id: str, body: dict, if_match: str | None = Header(None)):
    return _monthly_call(menu_service.monthly_editor_command, month_id, item_id, body, if_match, action="item")


@router.post("/{month_id}/entries/{entry_id}/exceptions", dependencies=[Depends(require_role("operator"))], response_model=None, responses={200: {"model": MonthlyCommandResponse}})
def upsert_menu_entry_exceptions(month_id: str, entry_id: str, body: dict, if_match: str | None = Header(None)):
    return _monthly_call(menu_service.monthly_editor_command, month_id, entry_id, body, if_match, action="exceptions")


@router.post("/{month_id}/master-checks/{item_id}/resolve", dependencies=[Depends(require_role("operator"))])
def resolve_menu_master_check(month_id: str, item_id: str, body: dict, if_match: str | None = Header(None)):
    return _monthly_call(menu_service.monthly_editor_command, month_id, item_id, body, if_match, action="master")


@router.post("/condiments", dependencies=[Depends(require_role("operator"))])
def upload_condiments(file: UploadFile = File(...), sheet_name: str | None = None):
    content = file.file.read()
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
