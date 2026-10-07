from fastapi import APIRouter, Depends, Header, HTTPException

from src.api.auth import require_role
from src.services import menu_edit_revision_service, menu_rule_service

router = APIRouter()


@router.get("", dependencies=[Depends(require_role("operator"))])
def list_menu_rules(rule_type: str | None = None):
    return menu_rule_service.list_rules(rule_type)


@router.post("", dependencies=[Depends(require_role("operator"))])
def create_menu_rule(body: dict, if_match: str | None = Header(default=None, alias="If-Match")):
    try:
        rule = menu_rule_service.create_rule(body, if_match)
    except menu_edit_revision_service.RevisionPreconditionRequired as exc:
        raise HTTPException(status_code=428, detail=str(exc)) from exc
    except menu_edit_revision_service.RevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return rule


@router.put("/{rule_id}", dependencies=[Depends(require_role("operator"))])
def update_menu_rule(rule_id: str, body: dict, if_match: str | None = Header(default=None, alias="If-Match")):
    try:
        updated = menu_rule_service.update_rule(rule_id, body, if_match)
    except menu_edit_revision_service.RevisionPreconditionRequired as exc:
        raise HTTPException(status_code=428, detail=str(exc)) from exc
    except menu_edit_revision_service.RevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="not found")
    return {"updated": True, **updated}


@router.delete("/{rule_id}", dependencies=[Depends(require_role("operator"))])
def delete_menu_rule(rule_id: str, if_match: str | None = Header(default=None, alias="If-Match")):
    try:
        deleted = menu_rule_service.delete_rule(rule_id, if_match)
    except menu_edit_revision_service.RevisionPreconditionRequired as exc:
        raise HTTPException(status_code=428, detail=str(exc)) from exc
    except menu_edit_revision_service.RevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="not found")
    return deleted
