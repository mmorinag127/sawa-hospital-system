from pathlib import Path
from decimal import Decimal
from urllib.parse import quote
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.types import Receive, Scope, Send
from pydantic import BaseModel, Field, StrictInt

from src.api.auth import require_role
from src.schemas.job_status import JobStatusRecord
from src.schemas.implementation_price_contract import MenuPriceQuantitiesResponse
from src.services import (
    implementation_price_ocr_job_service,
    implementation_price_ocr_dispatch_service,
    implementation_price_storage,
    implementation_price_table_service,
)

router = APIRouter()


class _GeneratedPriceTableFileResponse(FileResponse):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)


class PriceTableItemInput(BaseModel):
    name: str = Field(..., min_length=1)
    amount_per_person: Decimal | None = None
    unit: str = Field(..., min_length=1)
    unit_price_tax_included: Decimal | None = None
    unit_price_tax_excluded: Decimal | None = None
    price_unit: str = Field(..., min_length=1)
    origin_or_maker: str = Field(..., min_length=1)
    supplier: str = Field(..., min_length=1)
    source: str | None = None
    purchased_quantity: Decimal | None = None
    purchased_unit: str | None = None
    yield_rate: Decimal | None = None
    total_quantity_precision: int | None = Field(default=None, ge=0, le=6)
    quantity_basis: str = "per_person"
    actual_quantity: Decimal | None = Field(default=None, ge=0)
    amount_per_person_precision: int | None = Field(default=None, ge=0, le=6)
    amount_per_person_rounding: str | None = None
    delivered_quantity: Decimal | None = Field(default=None, ge=0)
    delivered_unit: str | None = None
    invoice_amount: Decimal | None = Field(default=None, ge=0)
    invoice_tax_included: bool | None = None
    invoice_amount_rounding: Literal["down", "half_up"] | None = None


class PriceTableSchoolInput(BaseModel):
    name: str = Field(..., min_length=1)
    recipient: str = Field(..., min_length=1)
    headcount: int = Field(..., gt=0)
    menu_name: str = Field(..., min_length=1)
    allocation_headcount: int | None = Field(default=None, gt=0)
    reserve_meals: int | None = Field(default=None, ge=0)
    items: list[PriceTableItemInput] = Field(..., min_length=1)


class PriceTableQuantityItemInput(BaseModel):
    name: str = Field(..., min_length=1)
    amount_per_person: Decimal | None = None
    unit: str = Field(..., min_length=1)
    cost_key: str | None = None
    source: str | None = None
    purchased_quantity: Decimal | None = None
    purchased_unit: str | None = None
    yield_rate: Decimal | None = None
    total_quantity_precision: int | None = Field(default=None, ge=0, le=6)
    quantity_basis: str = "per_person"
    actual_quantity: Decimal | None = Field(default=None, ge=0)
    amount_per_person_precision: int | None = Field(default=None, ge=0, le=6)
    amount_per_person_rounding: str | None = None


class PriceTableManualSchoolInput(BaseModel):
    name: str = Field(..., min_length=1)
    recipient: str = Field(..., min_length=1)
    headcount: int = Field(..., gt=0)
    headcount_confirmed: bool | None = None
    menu_name: str = Field(..., min_length=1)
    allocation_headcount: int | None = Field(default=None, gt=0)
    reserve_meals: int | None = Field(default=None, ge=0)
    quantity_items: list[PriceTableQuantityItemInput] = Field(..., min_length=1)


class PriceTableCostItemInput(BaseModel):
    price_basis: Literal["direct", "invoice"] = "direct"
    invoice_unit_price: Decimal | None = Field(default=None, ge=0)
    invoice_unit: str | None = None
    package_quantity: Decimal | None = Field(default=None, gt=0)
    package_unit: str | None = None
    unit_price_precision: int | None = Field(default=None, ge=0, le=6)
    unit_price_rounding: Literal["half_up", "down"] | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    source_ocr_job_id: str | None = None
    source_page_index: int | None = Field(default=None, gt=0)
    source_table_id: str | None = None
    source_row_index: int | None = Field(default=None, ge=0)
    invoice_evidence: dict | None = None
    invoice_school_confirmed: bool | None = None
    price_tax_basis: Literal["included", "excluded"] | None = None
    key: str | None = None
    name: str = Field(..., min_length=1)
    status: str = Field(..., min_length=1)
    unit_price_tax_included: Decimal | None = None
    unit_price_tax_excluded: Decimal | None = None
    price_unit: str = ""
    origin_or_maker: str = ""
    supplier: str = ""
    source: str | None = None
    delivered_quantity: Decimal | None = Field(default=None, ge=0)
    delivered_unit: str | None = None
    invoice_amount: Decimal | None = Field(default=None, ge=0)
    invoice_tax_included: bool | None = None
    invoice_amount_rounding: Literal["down", "half_up"] | None = None


class PriceTableGenerateRequest(BaseModel):
    facility_id: int = Field(..., gt=0)
    source_ocr_job_ids: list[str] = Field(default_factory=list)
    implementation_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    corporation_name: str = Field(..., min_length=1)
    representative_name: str = Field(..., min_length=1)
    tax_rate: Decimal | None = None
    schools: list[PriceTableSchoolInput] = Field(..., min_length=1, max_length=1)


class PriceTableGenerateFromSourcesRequest(BaseModel):
    facility_id: int = Field(..., gt=0)
    source_ocr_job_ids: list[str] = Field(default_factory=list)
    source_menu_execution_id: int | None = Field(default=None, gt=0, strict=True)
    source_menu_execution_ids: list[StrictInt] = Field(default_factory=list)
    implementation_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    corporation_name: str = Field(..., min_length=1)
    representative_name: str = Field(..., min_length=1)
    tax_rate: Decimal | None = None
    schools: list[PriceTableManualSchoolInput] = Field(..., min_length=1, max_length=1)
    cost_items: list[PriceTableCostItemInput] = Field(..., min_length=1)


class PriceTableDraftSaveResponse(BaseModel):
    draft_id: str


class PriceTableDraftSummary(BaseModel):
    draft_id: str
    facility_id: int | None
    scope_status: str
    label: str
    implementation_date: str
    updated_at: datetime


class PriceTablePreviewResponse(BaseModel):
    implementation_date: date
    schools: list[dict]


class PriceTableOcrResponse(BaseModel):
    document_type: str
    filename: str
    status: str
    provider: str
    review_required: bool
    cost_items: list[dict]
    invoice_tables: list[dict] = Field(default_factory=list)
    invoice_line_items: list[dict] = Field(default_factory=list)
    quantity_items: list[dict]
    pages: list[dict]


class PriceTableOcrJobResponse(BaseModel):
    job_status: JobStatusRecord
    id: str
    facility_id: int
    document_type: str
    filename: str
    status: str | None
    provider: str
    review_required: bool
    stored_pdf_ref: str | None
    result: PriceTableOcrResponse | None = None
    error: str | None = None
    dispatch_error: str | None = None
    attempt_count: int | None
    delivery_generation: int | None = None
    source_contract_error: str | None = None
    result_provenance: dict | None = None
    created_at: datetime
    updated_at: datetime
    queued_at: datetime
    started_at: datetime | None = None
    lease_expires_at: datetime | None = None
    finished_at: datetime | None = None


def _dispatch_or_raise(job_id: str, facility_id: int, generation: int) -> dict:
    try:
        implementation_price_ocr_dispatch_service.dispatch_ocr_job(job_id, generation)
    except (
        implementation_price_ocr_dispatch_service.OcrDispatchConfigurationError,
        implementation_price_ocr_dispatch_service.OcrDispatchError,
    ) as exc:
        job = implementation_price_ocr_job_service.get_ocr_job(job_id, facility_id=facility_id)
        raise HTTPException(
            status_code=503,
            detail={"message": str(exc), "job_id": job_id, "status": job["status"]},
        ) from exc
    return implementation_price_ocr_job_service.get_ocr_job(job_id, facility_id=facility_id)


def _validate_pdf_upload(filename: str, content_type: str, pdf_bytes: bytes) -> None:
    if content_type and "pdf" not in content_type and not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="file must be a PDF")
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="file must not be empty")


def _content_disposition(filename: str) -> str:
    normalized = str(filename or "").strip() or "implementation_price_table.xlsx"
    path = Path(normalized)
    suffix = path.suffix or ".xlsx"
    ascii_stem = path.stem.encode("ascii", "ignore").decode("ascii").strip().replace('"', "")
    ascii_fallback = f"{ascii_stem or 'implementation_price_table'}{suffix}"
    encoded = quote(normalized, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


@router.post("/implementation-price-tables/generate", dependencies=[Depends(require_role("operator"))], responses={200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {"schema": {"type": "string", "format": "binary"}}}}})
def generate_implementation_price_table(body: PriceTableGenerateRequest):
    try:
        output_path = implementation_price_table_service.build_scoped_price_table_workbook_file(
            body.model_dump(), body.model_dump(exclude_none=True)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filename = "実施価格表.xlsx"
    return _GeneratedPriceTableFileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.get("/implementation-price-tables/menu-quantities/{menu_id}", dependencies=[Depends(require_role("operator"))], responses={200: {"model": MenuPriceQuantitiesResponse}})
def get_menu_price_quantities(menu_id: int, facility_id: int = Query(..., gt=0)):
    from src.db import session_scope
    from src.services.implementation_price_menu_quantity import menu_price_quantities
    try:
        with session_scope() as session:
            return menu_price_quantities(session, menu_id=menu_id, facility_id=facility_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/implementation-price-tables/generate-from-sources", dependencies=[Depends(require_role("operator"))], responses={200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {"schema": {"type": "string", "format": "binary"}}}}})
def generate_implementation_price_table_from_sources(body: PriceTableGenerateFromSourcesRequest):
    try:
        output_path = implementation_price_table_service.build_scoped_price_table_workbook_file(
            body.model_dump(), body.model_dump(exclude_none=True), from_sources=True
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filename = "実施価格表.xlsx"
    return _GeneratedPriceTableFileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.post(
    "/implementation-price-tables/preview-from-sources",
    dependencies=[Depends(require_role("operator"))],
    response_model=PriceTablePreviewResponse,
)
def preview_implementation_price_table_from_sources(body: PriceTableGenerateFromSourcesRequest):
    try:
        return implementation_price_table_service.build_scoped_price_table_preview(
            body.model_dump(), body.model_dump(exclude_none=True)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/implementation-price-tables/source-drafts",
    dependencies=[Depends(require_role("operator"))],
    response_model=PriceTableDraftSaveResponse,
)
def save_implementation_price_table_source_draft(body: PriceTableGenerateFromSourcesRequest):
    try:
        result = implementation_price_table_service.save_manual_source_draft(body.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"draft_id": result["draft_id"]}


@router.get(
    "/implementation-price-tables/source-drafts",
    dependencies=[Depends(require_role("operator"))],
    response_model=list[PriceTableDraftSummary],
)
def list_implementation_price_table_source_drafts(facility_id: int = Query(..., gt=0), month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), implementation_date: date | None = None):
    try:
        return implementation_price_table_service.list_manual_source_drafts(facility_id=facility_id, month=month, implementation_date=implementation_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/implementation-price-tables/source-drafts/{draft_id}", dependencies=[Depends(require_role("operator"))], responses={200: {"content": {"application/json": {"schema": {"type": "object", "additionalProperties": True}}}}})
def get_implementation_price_table_source_draft(draft_id: str, facility_id: int = Query(..., gt=0)):
    try:
        return implementation_price_table_service.load_manual_source_draft(draft_id, facility_id=facility_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="draft not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/implementation-price-tables/ocr/jobs",
    dependencies=[Depends(require_role("operator"))],
    response_model=PriceTableOcrJobResponse,
    status_code=202,
)
async def create_implementation_price_table_ocr_job(
    facility_id: int = Form(..., gt=0),
    document_type: str = Form(...),
    file: UploadFile = File(...),
):
    content_type = str(file.content_type or "").lower()
    filename = str(file.filename or "").strip()
    pdf_bytes = await file.read()
    _validate_pdf_upload(filename, content_type, pdf_bytes)
    try:
        job = await run_in_threadpool(
            implementation_price_ocr_job_service.create_ocr_job,
            facility_id=facility_id,
            document_type=document_type,
            filename=filename,
            pdf_bytes=pdf_bytes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except implementation_price_storage.StorageConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except implementation_price_ocr_dispatch_service.OcrDispatchConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return await run_in_threadpool(_dispatch_or_raise, job["id"], facility_id, job["delivery_generation"])


@router.get("/implementation-price-tables/ocr/jobs/{job_id}/source", dependencies=[Depends(require_role("operator"))], responses={200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}})
async def get_price_ocr_source(job_id: str, facility_id: int = Query(..., gt=0)):
    try:
        job = await run_in_threadpool(implementation_price_ocr_job_service.get_ocr_job, job_id, facility_id=facility_id)
        content = await run_in_threadpool(implementation_price_storage.load_job_source, job)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="原本PDFがありません。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except implementation_price_storage.StorageConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content, media_type="application/pdf", headers={"Content-Disposition": _content_disposition(job["filename"]), "Cache-Control": "private, no-store"})


@router.get(
    "/implementation-price-tables/ocr/jobs/{job_id}",
    dependencies=[Depends(require_role("operator"))],
    response_model=PriceTableOcrJobResponse,
)
async def get_implementation_price_table_ocr_job(job_id: str, facility_id: int = Query(..., gt=0)):
    try:
        return await run_in_threadpool(implementation_price_ocr_job_service.get_ocr_job, job_id, facility_id=facility_id)
    except implementation_price_ocr_job_service.OcrJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="ocr job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/implementation-price-tables/ocr/jobs/{job_id}/retry",
    dependencies=[Depends(require_role("operator"))],
    response_model=PriceTableOcrJobResponse,
)
async def retry_implementation_price_table_ocr_job(job_id: str, facility_id: int = Query(..., gt=0)):
    try:
        job = await run_in_threadpool(implementation_price_ocr_job_service.retry_failed_ocr_job, job_id, facility_id=facility_id)
    except implementation_price_ocr_job_service.OcrJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="ocr job not found") from exc
    except implementation_price_ocr_job_service.OcrJobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except implementation_price_ocr_dispatch_service.OcrDispatchConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return await run_in_threadpool(_dispatch_or_raise, job_id, facility_id, job["delivery_generation"])


def _saved_price_result(draft_id: str, facility_id: int):
    """Calculate from the saved, school-bound source using the existing engine."""
    import hashlib
    import json
    payload = implementation_price_table_service.load_manual_source_draft(draft_id, facility_id=facility_id)
    validated = PriceTableGenerateFromSourcesRequest.model_validate(payload)
    preview = implementation_price_table_service.build_scoped_price_table_preview(
        validated.model_dump(), validated.model_dump(exclude_none=True))
    binding = {"draft_id": draft_id, "payload": payload, "preview": preview}
    encoded = json.dumps(binding, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return {**binding, "fingerprint": hashlib.sha256(encoded.encode()).hexdigest()}


@router.get("/implementation-price-tables/source-drafts/{draft_id}/preview", dependencies=[Depends(require_role("operator"))])
def preview_saved_implementation_price(draft_id: str, facility_id: int = Query(..., gt=0)):
    try:
        return _saved_price_result(draft_id, facility_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="draft not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/implementation-price-tables/source-drafts/{draft_id}/export", dependencies=[Depends(require_role("operator"))])
def export_saved_implementation_price(draft_id: str, facility_id: int = Query(..., gt=0), expected_fingerprint: str = Query(..., pattern=r"^[0-9a-f]{64}$")):
    try:
        result = _saved_price_result(draft_id, facility_id)
        if result["fingerprint"] != expected_fingerprint:
            raise HTTPException(status_code=409, detail="確認後に実施価格の入力・計算結果が変更されています。再確認してください。")
        body = PriceTableGenerateFromSourcesRequest.model_validate(result["payload"])
        output = implementation_price_table_service.build_scoped_price_table_workbook_file(
            body.model_dump(), body.model_dump(exclude_none=True), from_sources=True)
        # Recalculate after materialization as well; a concurrent edit must not
        # return a workbook under an older approval fingerprint.
        if _saved_price_result(draft_id, facility_id)["fingerprint"] != expected_fingerprint:
            output.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail="出力中に実施価格が変更されました。再確認してください。")
        return FileResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": _content_disposition("実施価格表.xlsx")})
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="draft not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
