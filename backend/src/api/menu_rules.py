from fastapi import APIRouter, Depends, HTTPException

from src.api.auth import require_role
from src.services import menu_rule_service

router = APIRouter()


@router.get("", dependencies=[Depends(require_role("operator"))])
def list_menu_rules(rule_type: str | None = None):
    return {"rules": menu_rule_service.list_rules(rule_type)}


@router.post("", dependencies=[Depends(require_role("operator"))])
def create_menu_rule(body: dict):
    try:
        rule = menu_rule_service.create_rule(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"rule": rule}


@router.put("/{rule_id}", dependencies=[Depends(require_role("operator"))])
def update_menu_rule(rule_id: str, body: dict):
    updated = menu_rule_service.update_rule(rule_id, body)
    if not updated:
        raise HTTPException(status_code=404, detail="not found")
    return {"updated": True}


@router.delete("/{rule_id}", dependencies=[Depends(require_role("operator"))])
def delete_menu_rule(rule_id: str):
    deleted = menu_rule_service.delete_rule(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="not found")
    return {"deleted": True}
