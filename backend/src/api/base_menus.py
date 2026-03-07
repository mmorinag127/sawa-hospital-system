from fastapi import APIRouter, HTTPException, Depends

from src.api.auth import require_role
from src.services import base_menu_service

router = APIRouter()


@router.get("/base-menus", dependencies=[Depends(require_role("admin"))])
def list_base_menus(cycle_day: int | None = None):
    items = base_menu_service.list_items(cycle_day)
    return {"items": items}


@router.post("/base-menus", dependencies=[Depends(require_role("admin"))])
def replace_base_menus(body: dict):
    items = body.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items must be a list")
    result = base_menu_service.replace_items(items)
    return result


@router.put("/base-menus/{item_id}", dependencies=[Depends(require_role("admin"))])
def update_base_menu(item_id: str, body: dict):
    updated = base_menu_service.update_item(item_id, body)
    if not updated:
        raise HTTPException(status_code=404, detail="not found")
    return {"updated": True}
