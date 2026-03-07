from fastapi import APIRouter, HTTPException, UploadFile, File, Depends

from src.services import menu_service
from src.api.auth import require_role

router = APIRouter()


@router.get("/{month_id}", dependencies=[Depends(require_role("admin"))])
def get_menu(month_id: str):
    menu = menu_service.get_menu(month_id)
    if not menu:
        raise HTTPException(status_code=404, detail="not found")
    return menu


@router.post("", dependencies=[Depends(require_role("admin"))])
async def upload_menu(month_id: str, file: UploadFile = File(...), sheet_name: str | None = None):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        _, replaced, item_count = menu_service.create_menu(
            month_id=month_id,
            file_bytes=content,
            filename=file.filename,
            sheet_name=sheet_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"created": True, "replaced": replaced, "item_count": item_count}


@router.put("/{month_id}/items/{item_id}", dependencies=[Depends(require_role("admin"))])
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


@router.post("/condiments", dependencies=[Depends(require_role("admin"))])
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
