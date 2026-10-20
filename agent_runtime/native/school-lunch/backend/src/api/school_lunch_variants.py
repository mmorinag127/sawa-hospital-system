from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.auth import require_role
from src.api.school_lunch_common import MenuExecutionCreate, MenuExecutionResponse, NonBlank, get_school_lunch_session
from src.models.school_lunch_variant import MenuVariant
from src.models.school_lunch_source_review import SchoolLunchSourceReview
from src.services import school_lunch_common_service as menus
from src.services.menu_route_reuse import clone_unchanged_routes
from src.api.school_lunch_common import _require_cooking_job
from src.services import school_lunch_review_store, implementation_price_ocr_job_service

from src.api.school_scope import require_school_scope

router = APIRouter(prefix="/api/school-lunch", dependencies=[Depends(require_role("operator")), Depends(require_school_scope)])


class SourceReviewReference(BaseModel):
    job_id: NonBlank
    version: int = Field(ge=1)
    source: NonBlank


class VariantCreate(BaseModel):
    source_menu_id: int = Field(gt=0)
    source_version: int = Field(ge=1)
    kind: Literal["reuse", "support", "allergy"]
    instruction: NonBlank = Field(max_length=2000)
    menu: MenuExecutionCreate
    source_review: SourceReviewReference | None = None


@router.post("/menu-variants", response_model=MenuExecutionResponse, status_code=201)
def create_variant(body: VariantCreate, request: Request, session: Session = Depends(get_school_lunch_session)):
    try:
        source = menus.get_menu_execution(session, body.source_menu_id)
        if source["version"] != body.source_version:
            raise menus.SchoolLunchConflict("元の献立が変更されています。再読み込みしてください。")
        payload = body.menu.model_dump()
        source_snapshot = MenuExecutionResponse.model_validate(source).model_dump(mode="json")
        if body.source_review is not None:
            if body.kind != "allergy":
                raise ValueError("アレルギー原本確認はアレルギー変更にのみ使用できます。")
            reference = body.source_review
            # PostgreSQL holds this lock until the variant transaction commits;
            # a concurrent review correction cannot replace the checked version.
            locked = session.execute(select(SchoolLunchSourceReview).where(
                SchoolLunchSourceReview.job_id == reference.job_id,
            ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
            if locked is None:
                raise menus.SchoolLunchConflict("保存済みの原本確認を指定してください。")
            review = school_lunch_review_store.get_review(session, reference.job_id, _require_cooking_job(reference.job_id, request.state.school_id, session, for_adoption=True))
            if review["stale"] or review["version"] != reference.version:
                raise menus.SchoolLunchConflict("原本確認結果が変更されています。再確認してください。")
            row = next((row for row in review["rows"] if row["source"] == reference.source), None)
            if row is None or not row["confirmed"]:
                raise menus.SchoolLunchConflict("確認済みの原本行を指定してください。")
            if (source["facility"]["name"] != row["facility_name"]
                    or str(source["service_date"]) != row["service_date"]
                    or payload["facility_name"] != row["facility_name"]
                    or str(payload["service_date"]) != row["service_date"]):
                raise menus.SchoolLunchConflict("原本の学校・日付と献立が一致しません。")
            source_snapshot["source_review"] = {
                **reference.model_dump(), "source_fingerprint": review["source_fingerprint"],
                "row": dict(row),
            }
        # Variant rows are new rows, never references that could edit source children.
        for row in payload["ingredients"]:
            row.pop("id", None)
            for step in row["process_steps"]:
                step.pop("id", None)
        target = menus.create_menu_execution(session, payload)
        clone_unchanged_routes(session, source, target)
        session.add(MenuVariant(
            source_menu_id=source["id"], target_menu_id=target["id"], source_version=source["version"],
            kind=body.kind, instruction=body.instruction, source_snapshot=source_snapshot,
        ))
        session.flush()
        return target
    except menus.SchoolLunchEntityNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except menus.SchoolLunchConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except implementation_price_ocr_job_service.OcrJobNotFoundError as exc:
        raise HTTPException(404, "cooking arrangement job not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/menu-executions/{menu_id}/variant")
def get_variant(menu_id: int, session: Session = Depends(get_school_lunch_session)):
    try:
        target = menus.get_menu_execution(session, menu_id)
    except menus.SchoolLunchEntityNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    variant = session.execute(select(MenuVariant).where(MenuVariant.target_menu_id == menu_id)).scalar_one_or_none()
    if variant is None:
        return None
    original = {row["ingredient"]["name"] for row in variant.source_snapshot["ingredients"]}
    current = {row["ingredient"]["name"] for row in target["ingredients"]}
    return {
        "source_menu_id": variant.source_menu_id, "source_version": variant.source_version,
        "kind": variant.kind, "instruction": variant.instruction,
        "removed_ingredients": sorted(original - current), "added_ingredients": sorted(current - original),
        "source_snapshot": variant.source_snapshot,
    }
