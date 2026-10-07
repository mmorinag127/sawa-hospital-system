"""Documentation only for existing base-menu serialization; no runtime validation."""
from typing import Any

from pydantic import BaseModel, ConfigDict


class BaseMenuItemResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    revision: int
    cycle_day: int
    daypart: str | None
    category: str | None
    name: str
    diet_type: str | None
    slot_index: int | None
    unit_type: str | None
    qty_per_serving: float | None
    temp_type: str | None
    bag_max_qty: float | None
    bag_max_unit: str | None
    condiments: list[Any]


class BaseMenuListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[BaseMenuItemResponse]
    revision: str


class BaseMenuReplaceResponse(BaseMenuListResponse):
    created: int


class BaseMenuUpdateResponse(BaseMenuListResponse):
    updated: bool
    item: BaseMenuItemResponse
