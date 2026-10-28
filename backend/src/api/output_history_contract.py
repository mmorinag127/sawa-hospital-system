"""Registered metadata-only output generation history contract."""
from typing import Annotated, Literal

from fastapi import Query
from pydantic import BaseModel

OrderIdQuery = Annotated[str, Query(min_length=1)]
WorkflowRevisionQuery = Annotated[int, Query(ge=1)]
PageQuery = Annotated[int, Query(ge=1)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]


class OutputHistoryEntry(BaseModel):
    output_bundle_id: str
    order_id: str
    source_saved_sheet_id: str
    source_bagging_result_id: str
    template_version_id: str | None
    created_at: str | None
    current: bool
    validation: Literal["validated", "blocked", "not_loaded"]
    blocked_reasons: list[str]


class OutputHistoryResponse(BaseModel):
    order_id: str
    workflow_revision: int
    current_output_bundle_id: str
    page: int
    limit: int
    total: int
    entries: list[OutputHistoryEntry]
    next_before_id: str | None = None
