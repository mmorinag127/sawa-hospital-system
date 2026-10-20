"""Documentation-only constraint responses; not runtime serialization models."""
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ConstraintResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    revision: int
    staff_id: int
    staff_name: str
    work_date: str
    status: str
    strength: str
    weight: int
    shift_template_id: int | None
    reason: str | None


class ConstraintCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    items: list[ConstraintResponse]
    collection_revision: int


class ConstraintCommandResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    item: ConstraintResponse
    collection_revision: int


class ConstraintDeletedItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    revision: int
    deleted: Literal[True]


class ConstraintDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    item: ConstraintDeletedItem
    collection_revision: int


class ConstraintBulkItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    selected_count: int
    changed_count: int
    cleared_count: int
    items: list[ConstraintResponse]


class ConstraintBulkResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    item: ConstraintBulkItem
    collection_revision: int
