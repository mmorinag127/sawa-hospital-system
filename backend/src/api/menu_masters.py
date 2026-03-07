from fastapi import APIRouter, Depends, HTTPException

from src.api.auth import require_role
from src.services import menu_service

router = APIRouter()


@router.get("/menu-masters", dependencies=[Depends(require_role("admin"))])
def list_menu_masters(q: str | None = None, limit: int = 1000):
    return {"items": menu_service.list_menu_masters(query=q, limit=limit)}


@router.post("/menu-masters", dependencies=[Depends(require_role("admin"))])
def create_menu_master(body: dict):
    try:
        item = menu_service.create_menu_master(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"item": item}


@router.put("/menu-masters/{item_id}", dependencies=[Depends(require_role("admin"))])
def update_menu_master(item_id: str, body: dict):
    try:
        updated = menu_service.update_menu_master(item_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="not found")
    return {"updated": True}
