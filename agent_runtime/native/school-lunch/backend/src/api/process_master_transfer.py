"""Administrator-controlled, file-mediated master transfer between isolated DBs."""

import os

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from src.api.auth import require_role
from src.api.school_lunch_common import get_school_lunch_session
from src.models.menu_process import ProcessRecipe, ProcessRevision
from src.models.menu_process_route import (
    ProcessRouteProfile,
    ProcessRouteProfileRevision,
)
from src.models.school_lunch import Facility
from src.services import process_master_transfer_service as transfer


router = APIRouter(
    prefix="/api/school-lunch/admin/master-transfer",
    dependencies=[Depends(require_role("admin"))],
)


def require_environment(expected: str):
    configured = os.getenv("SAWA_ENVIRONMENT", "").lower()
    service = os.getenv("K_SERVICE", "")
    observed = configured or (
        service.removeprefix("price-api-")
        if service in {"price-api-stg", "price-api-prod"}
        else ""
    )
    if observed != expected or (
        configured
        and service in {"price-api-stg", "price-api-prod"}
        and service != f"price-api-{configured}"
    ):
        raise HTTPException(403, f"この操作は {expected} 環境でのみ許可されています")


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    facility_id: int = Field(ge=1)
    recipe_revision_ids: list[int] = Field(default_factory=list)
    profile_revision_ids: list[int] = Field(default_factory=list)


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_key: str
    action: str
    target_id: int | None = None


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bundle: dict
    target_facility_id: int = Field(ge=1)
    choices: list[Choice] = Field(default_factory=list)


class ApplyRequest(PreviewRequest):
    preview_hash: str


@router.get("/facilities")
def facilities(session=Depends(get_school_lunch_session)):
    return [
        {"id": row.id, "name": row.name}
        for row in session.scalars(select(Facility).order_by(Facility.name))
    ]


@router.get("/sources")
def sources(facility_id: int = Query(ge=1), session=Depends(get_school_lunch_session)):
    require_environment("stg")
    recipes = session.scalars(
        select(ProcessRecipe).where(ProcessRecipe.facility_id == facility_id)
    ).all()
    profiles = session.scalars(
        select(ProcessRouteProfile).where(
            ProcessRouteProfile.facility_id == facility_id
        )
    ).all()
    recipe_rows = []
    for recipe in recipes:
        rev = (
            session.get(ProcessRevision, recipe.confirmed_revision_id)
            if recipe.confirmed_revision_id
            else None
        )
        if rev and rev.status == "confirmed" and rev.scope == "menu":
            recipe_rows.append(
                {
                    "id": recipe.id,
                    "name": recipe.name,
                    "revision_id": rev.id,
                    "revision": rev.number,
                }
            )
    profile_rows = []
    for profile in profiles:
        rev = (
            session.get(ProcessRouteProfileRevision, profile.head_revision_id)
            if profile.head_revision_id
            else None
        )
        if rev:
            profile_rows.append(
                {
                    "id": profile.id,
                    "name": profile.name,
                    "revision_id": rev.id,
                    "revision": rev.number,
                    "status": rev.status,
                }
            )
    return {"recipes": recipe_rows, "profiles": profile_rows}


@router.post("/export")
def export(body: ExportRequest, session=Depends(get_school_lunch_session)):
    require_environment("stg")
    return transfer.export_bundle(
        session, body.facility_id, body.recipe_revision_ids, body.profile_revision_ids
    )


@router.post("/preview")
def preview(body: PreviewRequest, session=Depends(get_school_lunch_session)):
    require_environment("prod")
    return transfer.preview(
        session,
        body.bundle,
        body.target_facility_id,
        [choice.model_dump() for choice in body.choices],
    )


@router.post("/apply")
def apply(body: ApplyRequest, session=Depends(get_school_lunch_session)):
    require_environment("prod")
    return transfer.apply(
        session,
        body.bundle,
        body.target_facility_id,
        [choice.model_dump() for choice in body.choices],
        body.preview_hash,
    )
