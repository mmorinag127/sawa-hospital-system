from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm.exc import StaleDataError

from src.api.auth import require_role
from src.api.menu_master_schemas import (
    MenuMasterFields,
    MenuMasterItemResponse,
    MenuMasterListResponse,
    MenuMasterUpdate,
    MenuMasterUpdateResponse,
)
from src.services import menu_service


def _require_menu_schema() -> None:
    try:
        menu_service.ensure_menu_schema()
    except menu_service.MenuSchemaNotMigrated as exc:
        raise HTTPException(status_code=503, detail={
            "code": "menu_schema_not_migrated", "message": str(exc),
        }) from exc


router = APIRouter(dependencies=[Depends(require_role("operator")), Depends(_require_menu_schema)])


@router.get(
    "/menu-masters",
    response_model=None,
    responses={200: {"model": MenuMasterListResponse}},
)
def list_menu_masters(
    q: str | None = None, limit: int = 1000, offset: int = Query(default=0, ge=0),
    sort: str = "name", order: str = "asc",
):
    try:
        return menu_service.list_menu_masters_page(query=q, limit=limit, offset=offset, sort=sort, order=order)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/menu-masters/{item_id}",
    response_model=None,
    responses={200: {"model": MenuMasterItemResponse}},
)
def get_menu_master(item_id: str):
    item = menu_service.get_menu_master(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    return {"item": item}


@router.post(
    "/menu-masters",
    response_model=None,
    responses={200: {"model": MenuMasterItemResponse}},
)
def create_menu_master(body: MenuMasterFields):
    try:
        item = menu_service.create_menu_master(body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"item": item}


@router.put(
    "/menu-masters/{item_id}",
    response_model=None,
    responses={200: {"model": MenuMasterUpdateResponse}},
)
def update_menu_master(item_id: str, body: MenuMasterUpdate):
    try:
        item = menu_service.save_menu_master(
            item_id, body.model_dump(exclude_unset=True, exclude={"revision"}),
            expected_revision=body.revision,
        )
    except (menu_service.MenuMasterRevisionConflict, StaleDataError) as exc:
        raise HTTPException(status_code=409, detail="menu master revision conflict; reload before saving") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    return {"updated": True, "item": item}
