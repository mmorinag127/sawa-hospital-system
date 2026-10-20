"""Documentation-only staff response contracts; not runtime response filters."""
from typing import Literal

from pydantic import BaseModel, ConfigDict


class StaffResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    revision: int
    name: str
    staff_group: str
    max_daily_work_minutes: int | None
    max_weekly_work_minutes: int | None
    max_monthly_work_minutes: int | None
    min_monthly_work_minutes: int | None
    target_monthly_work_minutes: int | None
    paid_leave_eligible: bool
    half_day_shift_template_id: int | None
    half_day_shift_template_name: str
    active_from: str | None
    active_to: str | None
    notes: str | None


class StaffCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    items: list[StaffResponse]
    collection_revision: int


class StaffCommandResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    item: StaffResponse
    collection_revision: int


class StaffDisplayOrderItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    items: list[StaffResponse]


class StaffDisplayOrderResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    item: StaffDisplayOrderItem
    collection_revision: int


class StaffDeletedItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    revision: int
    deleted: Literal[True]


class StaffDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    item: StaffDeletedItem
    collection_revision: int
