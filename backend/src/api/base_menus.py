from fastapi import APIRouter, HTTPException, Depends, Header

from src.api.auth import require_role
from src.api.base_menu_schemas import BaseMenuListResponse, BaseMenuReplaceResponse, BaseMenuUpdateResponse
from src.services import base_menu_service, menu_edit_revision_service

router = APIRouter()


@router.get("/base-menus", dependencies=[Depends(require_role("operator"))], response_model=None, responses={200: {"model": BaseMenuListResponse}})
def list_base_menus(cycle_day: int | None = None):
    return base_menu_service.list_items(cycle_day)


@router.post("/base-menus", dependencies=[Depends(require_role("operator"))], response_model=None, responses={200: {"model": BaseMenuReplaceResponse}})
def replace_base_menus(body: dict, if_match: str | None = Header(default=None, alias="If-Match")):
    items = body.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items must be a list")
    try:
        return base_menu_service.replace_items(items, if_match)
    except menu_edit_revision_service.RevisionPreconditionRequired as exc:
        raise HTTPException(status_code=428, detail=str(exc)) from exc
    except menu_edit_revision_service.RevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/base-menus/{item_id}", dependencies=[Depends(require_role("operator"))], response_model=None, responses={200: {"model": BaseMenuUpdateResponse}})
def update_base_menu(item_id: str, body: dict, if_match: str | None = Header(default=None, alias="If-Match")):
    try:
        updated = base_menu_service.update_item(item_id, body, if_match)
    except menu_edit_revision_service.RevisionPreconditionRequired as exc:
        raise HTTPException(status_code=428, detail=str(exc)) from exc
    except menu_edit_revision_service.RevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="not found")
    return {"updated": True, **updated}
