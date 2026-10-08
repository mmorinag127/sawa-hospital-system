"""Documentation-only monthly import envelopes; no runtime filtering."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ImportResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


class ImportSource(ImportResponse):
    file_uri: str
    filename: str
    content_sha256: str
    sheet_name: str
    scope_override: str | None
    diet_types: list[str] | None
    media_type: str


class ImportInputSnapshot(ImportResponse):
    revision: int
    digest: str


class ImportReview(ImportResponse):
    issues: list[dict[str, Any]]
    resolutions: list[dict[str, Any]]
    item_count: int
    master_diffs: list[dict[str, Any]]
    plans: dict[str, dict[str, Any]]
    items: list[dict[str, Any]]
    entries: list[dict[str, Any]]
    scope_override: str | None
    input_snapshot: ImportInputSnapshot


class SavedImportResponse(ImportResponse):
    id: str
    month_id: str
    state: Literal["review", "applied"]
    source: ImportSource
    review: ImportReview
    revision: str
    input_revision: str
    current_input_revision: str
    current_review: ImportReview


class ImportListResponse(ImportResponse):
    items: list[SavedImportResponse]


class MonthlyMasterChecks(ImportResponse):
    count: int
    issues: list[dict[str, Any]]


class MonthlySnapshotResponse(ImportResponse):
    menu: dict[str, Any] | None
    items: list[dict[str, Any]]
    entries: list[dict[str, Any]]
    master_checks: MonthlyMasterChecks
    source_month_id: str
    revision: str


class ImportApplyResponse(ImportResponse):
    created: bool
    receipt: SavedImportResponse
    snapshot: MonthlySnapshotResponse
    replaced: bool
    item_count: int


class MonthlyCommandResponse(ImportResponse):
    updated: bool
    result: str | dict[str, Any]
    snapshot: MonthlySnapshotResponse
