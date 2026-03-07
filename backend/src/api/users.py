from fastapi import APIRouter, Depends, HTTPException, status

from src.api.auth import invalidate_user_cache, require_role
from src.services import user_service

router = APIRouter()


@router.get("/users", dependencies=[Depends(require_role("admin"))])
def list_users():
    return {"items": user_service.list_users()}


@router.post("/users", dependencies=[Depends(require_role("admin"))])
def create_or_update_user(body: dict):
    user, created, error = user_service.upsert_user(
        body.get("account"),
        role=body.get("role"),
        status=body.get("status", "active"),
    )
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    invalidate_user_cache()
    return {
        "created": created,
        "user": user,
    }


@router.put("/users/{user_id}", dependencies=[Depends(require_role("admin"))])
def update_user(user_id: str, body: dict):
    user, error = user_service.update_user(
        user_id,
        role=body.get("role"),
        status=body.get("status"),
    )
    if error == "user_not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    invalidate_user_cache()
    return {"updated": True, "user": user}
