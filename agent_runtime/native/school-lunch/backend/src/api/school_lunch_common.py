from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from io import BytesIO
from hashlib import sha256
from uuid import uuid4
from urllib.parse import quote
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, StrictInt, TypeAdapter, ValidationError
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse
from src.services.workbook_pdf_service import OutputFormat, workbook_response

from src.api.auth import require_role
from src.api.monthly_overview_contract import MonthlyOverviewResponse
from src.api.school_work_inbox import router as work_inbox_router
from src.schemas.intake_contract import (
    CookingJobResponse, CookingExtractDraftResponse, CookingExtractJobResponse,
    SourceReviewResponse, CookingDocumentSummaryResponse, CookingDocumentResponse,
    WorkScheduleSourceResponse,
)
from src.api.school_scope import get_school_lunch_session, require_school_scope, require_facility, month_bounds
from src.models.school_lunch import Facility
from src.models.school_lunch_document import SchoolLunchDocument
from sqlalchemy import select, or_, and_
from sqlalchemy.exc import IntegrityError
from src.db import session_scope
from src.services import (
    cooking_arrangement_service,
    implementation_price_ocr_dispatch_service,
    implementation_price_ocr_job_service,
    implementation_price_storage,
    school_lunch_common_service,
    school_lunch_staff_service,
    school_lunch_source_review,
    school_lunch_review_store,
    work_schedule_service,
    work_schedule_import,
)


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RouteColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]
AssigneeName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

router = APIRouter(
    prefix="/api/school-lunch",
    dependencies=[Depends(require_role("operator")), Depends(require_school_scope)],
)
router.include_router(work_inbox_router)


class FacilityResponse(BaseModel):
    id: int
    name: str


class FacilityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.get("/facilities", response_model=list[FacilityResponse])
def list_facilities(session: Session = Depends(get_school_lunch_session)):
    return session.scalars(select(Facility).order_by(Facility.name)).all()


@router.post("/facilities", response_model=FacilityResponse, status_code=201)
def create_facility(body: FacilityCreate, session: Session = Depends(get_school_lunch_session)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "学校名を入力してください。")
    row = Facility(name=name)
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        raise HTTPException(409, "同じ学校名が登録されています。")
    return row


class IngredientInput(BaseModel):
    id: int | None = None
    name: NonBlank
    category: NonBlank | None = None
    stock_unit: NonBlank | None = None
    order_unit: NonBlank | None = None
    conversion_rate: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)

    @field_validator("category", "stock_unit", "order_unit", "conversion_rate", mode="before")
    @classmethod
    def empty_is_unconfigured(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


class IngredientResponse(IngredientInput):
    id: int


class ProcessStepInput(BaseModel):
    id: int | None = None
    sequence: int = Field(ge=1)
    location: NonBlank
    operation: NonBlank
    scheduled_time: time | None = None
    work_zone: Literal["contaminated", "contaminated_meat_fish", "non_contaminated"] | None = None
    route_line_style: Literal["solid", "dashed"] | None = None


class ProcessStepResponse(ProcessStepInput):
    id: int


class MenuIngredientInput(BaseModel):
    id: int | None = None
    ingredient: IngredientInput
    required_quantity: Decimal = Field(ge=0)
    unit: NonBlank
    process_text: str = ""
    route_color: RouteColor = "#2563eb"
    process_steps: list[ProcessStepInput] = Field(default_factory=list)


class MenuIngredientResponse(MenuIngredientInput):
    id: int
    ingredient: IngredientResponse
    process_steps: list[ProcessStepResponse]


class CookingInstructionSource(BaseModel):
    text: str
    source: str


class MenuExecutionWrite(BaseModel):
    cooking_instructions: str = ""
    cooking_instruction_sources: list[CookingInstructionSource] = Field(default_factory=list)
    service_date: date
    name: NonBlank
    servings: int = Field(gt=0)
    status: NonBlank
    version: int = Field(ge=1)
    assignees: list[AssigneeName] = Field(default_factory=list)
    ingredients: list[MenuIngredientInput] = Field(min_length=1)


class MenuExecutionCreate(MenuExecutionWrite):
    facility_name: NonBlank


class MenuExecutionUpdate(MenuExecutionWrite):
    pass


class MenuExecutionResponse(BaseModel):
    cooking_instructions: str
    cooking_instruction_sources: list[CookingInstructionSource]
    id: int
    facility: FacilityResponse
    service_date: date
    name: str
    servings: int
    status: str
    version: int
    assignees: list[str]
    ingredients: list[MenuIngredientResponse]


# Documentation only: the successful serializer emits every key, including nulls.
class MenuCoreIngredientResponse(BaseModel):
    id: int
    name: str
    category: str | None
    stock_unit: str | None
    order_unit: str | None
    conversion_rate: str | None


class MenuCoreStepResponse(BaseModel):
    id: int
    sequence: int
    location: str
    operation: str
    scheduled_time: str | None
    work_zone: Literal["contaminated", "contaminated_meat_fish", "non_contaminated"] | None
    route_line_style: Literal["solid", "dashed"] | None


class MenuCoreRowResponse(BaseModel):
    id: int
    ingredient: MenuCoreIngredientResponse
    required_quantity: str
    unit: str
    process_text: str
    route_color: str
    process_steps: list[MenuCoreStepResponse]


class MenuCoreResponse(MenuExecutionResponse):
    ingredients: list[MenuCoreRowResponse]


class CookingArrangementConfirmRequest(BaseModel):
    menus: list[MenuExecutionCreate] = Field(min_length=1)
    source_document_id: str = Field(min_length=32, max_length=32)


class WorkScheduleTaskInput(BaseModel):
    id: int | None = None
    sequence: int = Field(ge=1)
    start_time: time | None = None
    end_time: time | None = None
    duration_minutes: int | None = Field(default=None, gt=0, strict=True)
    offset_before_plating_minutes: int | None = Field(default=None, strict=True)
    operation: NonBlank
    work_zone: Literal["contaminated", "contaminated_meat_fish", "non_contaminated"] | None = None
    assignees: list[AssigneeName] | None = None
    ingredient_name: str = ""
    phase: Literal["prep", "wash_open", "cook", "heat", "plate", "other"] | None = None
    kettle: Literal["A", "B", "C", "D", "E", "F"] | None = None
    kettle_use: int | None = Field(default=None, ge=1)
    uses_raw_meat: bool | None = None


class WorkScheduleTaskResponse(BaseModel):
    assignees: list[str] | None = None
    id: int | None = None
    sequence: int
    start_time: time | None
    end_time: time | None
    duration_minutes: int | None = None
    offset_before_plating_minutes: int | None = None
    operation: str
    work_zone: Literal["contaminated", "contaminated_meat_fish", "non_contaminated"] | None
    ingredient_name: str
    phase: str | None = None
    kettle: str | None = None
    kettle_use: int | None = None
    uses_raw_meat: bool | None = None


class WorkScheduleRowInput(BaseModel):
    id: int | None = None
    position: int = Field(ge=1)
    menu_execution_id: int | None = None
    menu_version: int | None = Field(default=None, ge=1)
    menu_name: NonBlank
    assignees: list[AssigneeName] = Field(default_factory=list)
    tasks: list[WorkScheduleTaskInput] = Field(min_length=1)


class WorkScheduleRowResponse(BaseModel):
    id: int | None = None
    position: int
    menu_execution_id: int | None
    menu_version: int | None = None
    menu_name: str
    assignees: list[str]
    tasks: list[WorkScheduleTaskResponse]


class WorkScheduleSourceNote(BaseModel):
    text: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    source_text: str = Field(min_length=1)

    @field_validator("text", "source", "source_text")
    @classmethod
    def preserve_original_text(cls, value):
        if not value.strip():
            raise ValueError("注記と出典は空白だけにできません")
        return value


class WorkScheduleWrite(BaseModel):
    source_process_revisions: dict[str, int] | None = None
    source_notes: list[WorkScheduleSourceNote] = Field(default_factory=list)
    time_profile: Literal["normal", "early"] | None = None
    source_menu_versions: dict[str, int] | None = None
    facility_name: NonBlank
    service_date: date
    plating_start: time
    plating_end: time
    timing_profile_id: int | None = Field(default=None, gt=0)
    timing_profile_version: int | None = Field(default=None, ge=1)
    timeline_ticks: list[str] | None = None
    status: NonBlank
    version: int = Field(ge=1)
    rows: list[WorkScheduleRowInput] = Field(min_length=1)
    delivery_assignees: list[AssigneeName] = Field(default_factory=list)


class WorkScheduleResponse(BaseModel):
    source_process_revisions: dict[str, int] | None = None
    pattern_missing: list[str] = Field(default_factory=list)
    source_notes: list[WorkScheduleSourceNote] = Field(default_factory=list)
    time_profile: Literal["normal", "early"] | None = None
    source_menu_versions: dict[str, int] | None = None
    current_menu_versions: dict[str, int] = Field(default_factory=dict)
    stale: bool = False
    id: int | None
    facility: FacilityResponse
    service_date: date
    plating_start: time | None
    plating_end: time | None
    timing_profile_id: int | None = None
    timing_profile_version: int | None = None
    timeline_ticks: list[str] | None = None
    status: str
    version: int
    rows: list[WorkScheduleRowResponse]
    delivery_assignees: list[str] = Field(default_factory=list)
    hygiene: dict | None = None


class WorkScheduleReuse(BaseModel):
    source_schedule_id: int = Field(gt=0)
    source_version: int = Field(ge=1)
    facility_name: NonBlank
    service_date: date
    profile_id: int = Field(gt=0)
    profile_version: int = Field(ge=1)
    include_manual_rows: bool = Field(default=False, strict=True)


def _not_found(exc: school_lunch_common_service.SchoolLunchEntityNotFound) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


class StaffCreate(BaseModel):
    name: AssigneeName


class StaffUpdate(StaffCreate):
    active: bool
    version: int = Field(ge=1)


class StaffResponse(StaffUpdate):
    id: int


@router.get("/staff", response_model=list[StaffResponse])
def list_staff(session: Session = Depends(get_school_lunch_session)):
    return school_lunch_staff_service.list_staff(session)


@router.post("/staff", response_model=StaffResponse, status_code=201)
def create_staff(body: StaffCreate, session: Session = Depends(get_school_lunch_session)):
    try:
        return school_lunch_staff_service.create_staff(session, body.name)
    except school_lunch_common_service.SchoolLunchConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/staff/{member_id}", response_model=StaffResponse)
def update_staff(member_id: int, body: StaffUpdate, session: Session = Depends(get_school_lunch_session)):
    try:
        return school_lunch_staff_service.update_staff(session, member_id, body.model_dump())
    except school_lunch_common_service.SchoolLunchEntityNotFound as exc:
        raise _not_found(exc) from exc
    except school_lunch_common_service.SchoolLunchConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/menu-executions", response_model=MenuExecutionResponse, status_code=201, responses={201: {"model": MenuCoreResponse}})
def create_menu_execution(body: MenuExecutionCreate, session: Session = Depends(get_school_lunch_session)):
    try:
        return school_lunch_common_service.create_menu_execution(session, body.model_dump())
    except school_lunch_common_service.SchoolLunchConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/menu-executions", response_model=list[MenuExecutionResponse], responses={200: {"model": list[MenuCoreResponse]}})
def list_menu_executions(facility_id: int = Query(gt=0), month: str = Query(), session: Session = Depends(get_school_lunch_session)):
    start, end = month_bounds(month)
    return school_lunch_common_service.list_menu_executions(session, facility_id, start, end)


@router.get("/monthly-overview", responses={200: {"model": MonthlyOverviewResponse}})
def get_monthly_overview(
    month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    session: Session = Depends(get_school_lunch_session),
    after_id: int | None = None,
    limit: int = Query(default=25, ge=1, le=100),
):
    try:
        return school_lunch_common_service.get_monthly_overview(session, month, after_id=after_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/menu-executions/{menu_execution_id}", response_model=MenuExecutionResponse, responses={200: {"model": MenuCoreResponse}})
def get_menu_execution(menu_execution_id: int, session: Session = Depends(get_school_lunch_session)):
    try:
        return school_lunch_common_service.get_menu_execution(session, menu_execution_id)
    except school_lunch_common_service.SchoolLunchEntityNotFound as exc:
        raise _not_found(exc) from exc


@router.put("/menu-executions/{menu_execution_id}", response_model=MenuExecutionResponse, responses={200: {"model": MenuCoreResponse}})
def update_menu_execution(
    menu_execution_id: int,
    body: MenuExecutionUpdate,
    session: Session = Depends(get_school_lunch_session),
):
    try:
        return school_lunch_common_service.update_menu_execution(
            session,
            menu_execution_id,
            body.model_dump(exclude={field for field in ("cooking_instructions", "cooking_instruction_sources") if field not in body.model_fields_set}),
        )
    except school_lunch_common_service.SchoolLunchEntityNotFound as exc:
        raise _not_found(exc) from exc
    except school_lunch_common_service.SchoolLunchConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _require_cooking_job(job_id: str, facility_id: int, session=None, *, for_adoption=False):
    if session is None:
        job = implementation_price_ocr_job_service.get_ocr_job(job_id, facility_id=facility_id)
    else:
        row = implementation_price_ocr_job_service._job_or_raise(session, job_id)
        if row.facility_id != facility_id:
            raise implementation_price_ocr_job_service.OcrJobNotFoundError(job_id)
        job = implementation_price_ocr_job_service.serialize_job(row)
        if for_adoption:
            job["result"] = implementation_price_ocr_job_service.validated_successful_result(session,
                job_id, facility_id=facility_id, document_type="cooking_arrangement")
    if job["document_type"] != "cooking_arrangement":
        raise implementation_price_ocr_job_service.OcrJobNotFoundError(job_id)
    if job.get("status") == "succeeded" and job.get("result") is not None:
        job = {**job, "result": school_lunch_source_review.review_ocr_result(job["result"])}
    return job


def _dispatch_cooking_job(job_id: str, facility_id: int, generation: int):
    implementation_price_ocr_dispatch_service.dispatch_ocr_job(job_id, generation)
    return _require_cooking_job(job_id, facility_id)


@router.post("/cooking-arrangements/extract", status_code=202, responses={202: {"model": CookingExtractDraftResponse | CookingExtractJobResponse}})
async def extract_cooking_arrangement(request: Request, file: UploadFile = File(...), month: str = Query(), session: Session = Depends(get_school_lunch_session)):
    filename = str(file.filename or "").strip()
    content = await file.read(cooking_arrangement_service.MAX_UPLOAD_BYTES + 1)
    try:
        suffix = cooking_arrangement_service.validate_upload(filename=filename, content=content)
        month_bounds(month)
        document = SchoolLunchDocument(id=uuid4().hex, facility_id=request.state.school_id,
            target_month=month, filename=filename, content=content, sha256=sha256(content).hexdigest())
        if suffix in cooking_arrangement_service.EXCEL_EXTENSIONS:
            result = await run_in_threadpool(
                cooking_arrangement_service.extract_excel_candidates,
                filename=filename,
                workbook_bytes=content,
            )
            document.extracted_json = result
            session.add(document)
            session.flush()
            return {"mode": "draft", "result": result, "source_document_id": document.id}
        stored_filename, pdf_bytes = await run_in_threadpool(
            cooking_arrangement_service.normalize_ocr_upload,
            filename=filename,
            content=content,
        )
        job = await run_in_threadpool(
            implementation_price_ocr_job_service.create_ocr_job,
            document_type="cooking_arrangement",
            facility_id=request.state.school_id,
            filename=stored_filename,
            pdf_bytes=pdf_bytes,
            source_document=document,
        )
        try:
            dispatched_job = await run_in_threadpool(_dispatch_cooking_job, job["id"], request.state.school_id, job["delivery_generation"])
        except (
            implementation_price_ocr_dispatch_service.OcrDispatchConfigurationError,
            implementation_price_ocr_dispatch_service.OcrDispatchError,
        ):
            dispatched_job = await run_in_threadpool(_require_cooking_job, job["id"], request.state.school_id)
        return {"mode": "job", "job": dispatched_job, "source_document_id": job["source_document_id"]}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except implementation_price_storage.StorageConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except implementation_price_ocr_dispatch_service.OcrDispatchConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/cooking-arrangements/jobs/{job_id}", responses={200: {"model": CookingJobResponse}})
async def get_cooking_arrangement_job(job_id: str, request: Request, session: Session = Depends(get_school_lunch_session)):
    try:
        job = _require_cooking_job(job_id, request.state.school_id, session)
        document = session.scalar(select(SchoolLunchDocument).where(SchoolLunchDocument.ocr_job_id == job_id))
        return {**job, "source_document_id": document.id if document else None}
    except implementation_price_ocr_job_service.OcrJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="cooking arrangement job not found") from exc


@router.get("/cooking-arrangements/jobs/{job_id}/source", responses={200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}})
async def get_cooking_source(job_id: str, request: Request):
    try:
        job = await run_in_threadpool(_require_cooking_job, job_id, request.state.school_id)
        data = await run_in_threadpool(implementation_price_storage.load_job_source, job)
    except implementation_price_ocr_job_service.OcrJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="cooking arrangement job not found") from exc
    except implementation_price_storage.StoredPdfNotFoundError as exc:
        raise HTTPException(status_code=404, detail="原本PDFが見つかりません。再アップロードしてください。") from exc
    except implementation_price_storage.StorageConfigurationError as exc:
        raise HTTPException(status_code=503, detail="原本PDFを取得できません。時間をおいて再試行してください。") from exc
    return StreamingResponse(BytesIO(data), media_type="application/pdf", headers={"Cache-Control": "private, no-store"})


class SourceReviewRow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: str = Field(min_length=1, max_length=400)
    source_page: int = Field(ge=0)
    raw_day: str = Field(max_length=100)
    facility_name: str = Field(max_length=200)
    service_date: str = Field(max_length=10)
    menu_name: str = Field(max_length=2000)
    target_food: str = Field(max_length=2000)
    instruction: str = Field(max_length=10000)
    container: str = Field(max_length=2000)
    confirmed: bool = False


class SourceReviewWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=0, le=2147483646)
    source_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    rows: list[SourceReviewRow] = Field(max_length=10000)


@router.get("/cooking-arrangements/jobs/{job_id}/review", responses={200: {"model": SourceReviewResponse}})
def get_cooking_source_review(job_id: str, request: Request, session: Session = Depends(get_school_lunch_session)):
    try:
        return school_lunch_review_store.get_review(session, job_id, _require_cooking_job(job_id, request.state.school_id, session))
    except implementation_price_ocr_job_service.OcrJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="cooking arrangement job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/cooking-arrangements/jobs/{job_id}/review", responses={200: {"model": SourceReviewResponse}})
def put_cooking_source_review(job_id: str, body: SourceReviewWrite, request: Request, session: Session = Depends(get_school_lunch_session)):
    try:
        return school_lunch_review_store.save_review(session, job_id, _require_cooking_job(job_id, request.state.school_id, session, for_adoption=True), body.model_dump())
    except implementation_price_ocr_job_service.OcrJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="cooking arrangement job not found") from exc
    except school_lunch_review_store.ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/cooking-arrangements/jobs/{job_id}/retry", responses={200: {"model": CookingJobResponse}})
async def retry_cooking_arrangement_job(job_id: str, request: Request):
    try:
        await run_in_threadpool(_require_cooking_job, job_id, request.state.school_id)
        job = await run_in_threadpool(implementation_price_ocr_job_service.retry_failed_ocr_job, job_id, facility_id=request.state.school_id)
        return await run_in_threadpool(_dispatch_cooking_job, job_id, request.state.school_id, job["delivery_generation"])
    except implementation_price_ocr_job_service.OcrJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="cooking arrangement job not found") from exc
    except implementation_price_ocr_job_service.OcrJobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (
        implementation_price_ocr_dispatch_service.OcrDispatchConfigurationError,
        implementation_price_ocr_dispatch_service.OcrDispatchError,
    ) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/cooking-arrangements/confirm", response_model=list[MenuExecutionResponse], status_code=201)
def confirm_cooking_arrangement(
    request: Request,
    body: CookingArrangementConfirmRequest,
    session: Session = Depends(get_school_lunch_session),
):
    try:
        document = session.get(SchoolLunchDocument, body.source_document_id)
        if document is None or document.facility_id != request.state.school_id:
            raise HTTPException(404, "選択学校の取込原本が見つかりません。")
        result = document.extracted_json
        if document.ocr_job_id:
            try:
                result = implementation_price_ocr_job_service.validated_successful_result(session,
                    document.ocr_job_id, facility_id=request.state.school_id,
                    document_type="cooking_arrangement", document_id=document.id)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
        source_menus = (result or {}).get("menus", [])
        if not source_menus or any(not isinstance(menu.get("facility_name"), str) or not menu["facility_name"].strip() for menu in source_menus):
            raise HTTPException(409, "原本の学校名を確認できません。学校名が確認できる原本を取り込んでください。")
        names = {menu["facility_name"] for menu in source_menus}
        if names != {request.state.school_name}:
            raise HTTPException(409, "原本の学校と選択学校が一致しません。原本を確認してください。")
        return school_lunch_common_service.create_menu_execution_batch(
            session,
            [{**menu.model_dump(), "source_document_id": document.id} for menu in body.menus],
        )
    except school_lunch_common_service.SchoolLunchConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/cooking-arrangements/documents", responses={200: {"model": list[CookingDocumentSummaryResponse]}})
def list_cooking_documents(facility_id: int = Query(gt=0), month: str = Query(), session: Session = Depends(get_school_lunch_session),
                           before_id: str | None = None, limit: int = Query(default=25, ge=1, le=100)):
    month_bounds(month)
    statement = select(SchoolLunchDocument.id, SchoolLunchDocument.filename,
        SchoolLunchDocument.sha256, SchoolLunchDocument.created_at, SchoolLunchDocument.ocr_job_id,
        SchoolLunchDocument.target_month).where(SchoolLunchDocument.facility_id == facility_id,
        SchoolLunchDocument.target_month == month)
    if before_id is not None:
        cursor = session.execute(select(SchoolLunchDocument.id, SchoolLunchDocument.created_at).where(
            SchoolLunchDocument.id == before_id, SchoolLunchDocument.facility_id == facility_id,
            SchoolLunchDocument.target_month == month,
        )).first()
        if cursor is None:
            raise HTTPException(422, "取込履歴のページ指定が不正です")
        statement = statement.where(or_(SchoolLunchDocument.created_at < cursor.created_at,
            and_(SchoolLunchDocument.created_at == cursor.created_at, SchoolLunchDocument.id < cursor.id)))
    rows = session.execute(statement.order_by(SchoolLunchDocument.created_at.desc(), SchoolLunchDocument.id.desc()).limit(limit)).all()
    return [{"id": row.id, "filename": row.filename, "sha256": row.sha256, "created_at": row.created_at.isoformat() + "Z",
        "ocr_job_id": row.ocr_job_id, "target_month": row.target_month} for row in rows]


@router.get("/cooking-arrangements/documents/{document_id}", responses={200: {"model": CookingDocumentResponse}})
def read_cooking_document(document_id: str, request: Request, session: Session = Depends(get_school_lunch_session)):
    row = session.get(SchoolLunchDocument, document_id)
    if row is None or row.facility_id != request.state.school_id:
        raise HTTPException(404, "選択学校の原本が見つかりません。")
    result = row.extracted_json
    if row.ocr_job_id is not None:
        try:
            result = implementation_price_ocr_job_service.validated_successful_result(session,
                row.ocr_job_id, facility_id=request.state.school_id,
                document_type="cooking_arrangement", document_id=row.id, for_adoption=False)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    return {"id": row.id, "filename": row.filename, "result": result, "ocr_job_id": row.ocr_job_id}


@router.get("/cooking-arrangements/documents/{document_id}/source", responses={200: {"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
def download_cooking_document(document_id: str, request: Request, session: Session = Depends(get_school_lunch_session)):
    row = session.get(SchoolLunchDocument, document_id)
    if row is None or row.facility_id != request.state.school_id:
        raise HTTPException(404, "選択学校の原本が見つかりません。")
    try:
        implementation_price_ocr_job_service.validate_original_document(session, row, facility_id=request.state.school_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return StreamingResponse(BytesIO(row.content), media_type="application/octet-stream", headers={
        "Cache-Control": "private, no-store", "X-Content-SHA256": row.sha256, "Content-Length": str(len(row.content)),
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.filename, safe='')}"})


@router.get("/work-schedules", response_model=list[WorkScheduleResponse])
def list_work_schedules(facility_id: int = Query(gt=0), month: str = Query(), session: Session = Depends(get_school_lunch_session)):
    start, end = month_bounds(month)
    return work_schedule_service.list_work_schedules(session, facility_id, start, end)


@router.post("/work-schedules/extract", status_code=202, responses={202: {"model": WorkScheduleSourceResponse}})
async def extract_work_schedule(file: UploadFile = File(...)):
    content = await file.read(cooking_arrangement_service.MAX_UPLOAD_BYTES + 1)
    try:
        return await run_in_threadpool(work_schedule_import.extract_work_schedule_draft,
                                      filename=str(file.filename or ""), workbook_bytes=content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/work-schedules/draft", response_model=WorkScheduleResponse)
def build_work_schedule_draft(
    facility_name: NonBlank = Query(),
    service_date: date = Query(),
    rebuild: bool = False,
    refresh_sources: bool = Query(default=False),
    session: Session = Depends(get_school_lunch_session),
):
    try:
        return work_schedule_service.build_work_schedule_draft(session, facility_name, service_date, refresh_sources, rebuild=rebuild)
    except work_schedule_service.WorkScheduleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/work-schedules/source-import-context", response_model=WorkScheduleResponse)
def work_schedule_source_import_context(
    facility_name: NonBlank = Query(),
    service_date: date = Query(),
    session: Session = Depends(get_school_lunch_session),
):
    """Read the current save context, including an explicitly empty menu snapshot."""
    try:
        return work_schedule_service.build_work_schedule_draft(
            session, facility_name, service_date, source_import=True,
        )
    except work_schedule_service.WorkScheduleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/work-schedules", response_model=WorkScheduleResponse, status_code=201)
def create_work_schedule(body: WorkScheduleWrite, session: Session = Depends(get_school_lunch_session)):
    try:
        return work_schedule_service.create_work_schedule(session, body.model_dump())
    except work_schedule_service.WorkScheduleConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class ScheduleExportSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictInt = Field(gt=0)
    version: StrictInt = Field(gt=0)
    facility_id: StrictInt = Field(gt=0)
    service_date: date

    @field_validator("service_date", mode="before")
    @classmethod
    def explicit_date(cls, value):
        if not isinstance(value, str):
            raise ValueError("提供日はYYYY-MM-DDの文字列で指定してください")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("提供日が不正です") from exc
        if parsed.isoformat() != value:
            raise ValueError("提供日はYYYY-MM-DDで指定してください")
        return parsed


def schedule_export_sources(raw):
    if raw is None:
        return None  # Explicit compatibility for original external callers only.
    try:
        return [source.model_dump() for source in TypeAdapter(list[ScheduleExportSource]).validate_json(raw)]
    except ValidationError as exc:
        raise HTTPException(422, "工程表のID・版・学校・提供日を明示してください") from exc


@router.get("/work-schedules/monthly/export")
def export_monthly_work_schedules(
    facility_name: NonBlank = Query(),
    month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    format: Literal["standard", "original"] = Query(default="standard"),
    output: OutputFormat = "xlsx",
    session: Session = Depends(get_school_lunch_session),
    expected_sources: str | None = None,
):
    try:
        workbook = work_schedule_service.generate_monthly_work_schedule_workbook(session, facility_name, month, original=format == "original", expected_sources=schedule_export_sources(expected_sources))
    except work_schedule_service.WorkScheduleNotFound as exc:
        raise HTTPException(status_code=409 if expected_sources is not None else 404, detail=str(exc)) from exc
    except (work_schedule_service.WorkScheduleConflict, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return workbook_response(workbook, f"調理作業工程表_{facility_name}_{month}", output)


@router.post("/work-schedules/reuse-draft", response_model=WorkScheduleResponse)
def reuse_work_schedule(body: WorkScheduleReuse, session: Session = Depends(get_school_lunch_session)):
    try:
        return work_schedule_service.reuse_work_schedule_draft(session, body.model_dump())
    except work_schedule_service.WorkScheduleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except work_schedule_service.WorkScheduleConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/work-schedules/{schedule_id}", response_model=WorkScheduleResponse)
def get_work_schedule(schedule_id: int, session: Session = Depends(get_school_lunch_session)):
    try:
        return work_schedule_service.get_work_schedule(session, schedule_id)
    except work_schedule_service.WorkScheduleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/work-schedules/{schedule_id}", response_model=WorkScheduleResponse)
def update_work_schedule(
    schedule_id: int,
    body: WorkScheduleWrite,
    session: Session = Depends(get_school_lunch_session),
):
    try:
        payload = body.model_dump(exclude={"source_notes"} if "source_notes" not in body.model_fields_set else set())
        for row, data in zip(body.rows, payload["rows"]):
            for task, task_data in zip(row.tasks, data["tasks"]):
                if "assignees" not in task.model_fields_set:
                    task_data.pop("assignees", None)
        return work_schedule_service.update_work_schedule(session, schedule_id, payload)
    except work_schedule_service.WorkScheduleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except work_schedule_service.WorkScheduleConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/work-schedules/{schedule_id}/export")
def export_work_schedule(schedule_id: int, request: Request, format: Literal["standard", "original"] = "standard", output: OutputFormat = "xlsx", session: Session = Depends(get_school_lunch_session), expected_sources: str | None = None):
    try:
        from copy import deepcopy
        work_schedule_service.begin_schedule_export(session)
        schedule = work_schedule_service.get_work_schedule(session, schedule_id)
        if schedule["facility"]["id"] != request.state.school_id:
            raise HTTPException(409, "選択学校と工程表が一致しません")
        schedule = deepcopy(schedule)
        work_schedule_service.check_schedule_export_sources([schedule], schedule_export_sources(expected_sources))
        work_schedule_service.validate_work_schedule_export(session, schedule)
        workbook = work_schedule_service.generate_work_schedule_workbook(schedule, original=format == "original")
    except work_schedule_service.WorkScheduleNotFound as exc:
        raise HTTPException(status_code=409 if expected_sources is not None else 404, detail=str(exc)) from exc
    except work_schedule_service.WorkScheduleConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return workbook_response(workbook, f"調理作業工程表_{schedule['service_date']:%Y%m%d}", output)
