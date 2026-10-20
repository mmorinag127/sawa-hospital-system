"""Documentation-only intake envelopes; nested vendor payloads stay raw."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel
from src.schemas.job_status import JobStatusRecord


class CookingJobResponse(BaseModel):
    job_status: JobStatusRecord
    id: str
    facility_id: int
    document_type: str
    filename: str
    status: str | None
    provider: str
    review_required: bool
    stored_pdf_ref: str | None
    result: dict[str, Any] | None
    error: str | None
    dispatch_error: str | None
    attempt_count: int
    delivery_generation: int | None
    source_contract: dict[str, Any] | None
    input_digest: str | None
    business_attempt_id: str | None
    execution_token: str | None
    source_contract_error: str | None
    result_provenance: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    queued_at: datetime
    started_at: datetime | None
    lease_expires_at: datetime | None
    finished_at: datetime | None
    source_document_id: str | None = None


class CookingExtractDraftResponse(BaseModel):
    mode: Literal["draft"]
    result: dict[str, Any]
    source_document_id: str


class CookingExtractJobResponse(BaseModel):
    mode: Literal["job"]
    job: CookingJobResponse
    source_document_id: str


class SourceInstructionResponse(BaseModel):
    source: str
    source_page: int
    raw_day: str
    facility_name: str
    service_date: str
    menu_name: str
    target_food: str
    instruction: str
    container: str


class SourceReviewRowResponse(SourceInstructionResponse):
    confirmed: bool


class SourceReviewResponse(BaseModel):
    version: int
    source_fingerprint: str
    stale: bool
    rows: list[SourceReviewRowResponse]
    originals: list[SourceInstructionResponse]
    updated_at: datetime | None


class CookingDocumentSummaryResponse(BaseModel):
    id: str
    filename: str
    sha256: str
    created_at: datetime
    ocr_job_id: str | None
    target_month: str


class CookingDocumentResponse(BaseModel):
    id: str
    filename: str
    result: dict[str, Any] | None
    ocr_job_id: str | None


class WorkScheduleSourceResponse(BaseModel):
    document_type: Literal["work_schedule"]
    filename: str
    source_sha256: str
    status: Literal["needs_review"]
    review_required: Literal[True]
    sheets: list[dict[str, Any]]
    auxiliary_sheets: list[dict[str, Any]]
    issues: list[dict[str, Any]]
