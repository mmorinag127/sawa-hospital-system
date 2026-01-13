from fastapi import APIRouter, HTTPException, UploadFile, File, Depends

from src.services import menu_service
from src.api.auth import require_role

router = APIRouter()


@router.get("/{week_id}", dependencies=[Depends(require_role("admin"))])
def get_menu(week_id: str):
    menu = menu_service.get_menu(week_id)
    if not menu:
        raise HTTPException(status_code=404, detail="not found")
    return menu


@router.post("", dependencies=[Depends(require_role("admin"))])
async def upload_menu(week_id: str, file: UploadFile = File(...), sheet_name: str | None = None):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        _, replaced, item_count = menu_service.create_menu(
            week_id=week_id,
            file_bytes=content,
            filename=file.filename,
            sheet_name=sheet_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"created": True, "replaced": replaced, "item_count": item_count}


@router.put("/{week_id}/items/{item_id}", dependencies=[Depends(require_role("admin"))])
def update_menu_item(week_id: str, item_id: str, body: dict):
    updated = menu_service.update_item(week_id, item_id, body)
    if not updated:
        raise HTTPException(status_code=404, detail="not found")
    return {"updated": True}
