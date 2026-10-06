from typing import Annotated, Any

from pydantic import BaseModel, Field


class MenuMasterFields(BaseModel):
    # Existing service coercion is the input contract; do not tighten its enums/types here.
    name: Any = None
    unit_type: Any = None
    qty_per_serving: Any = None
    bag_max_qty: Any = None
    bag_max_unit: Any = None
    temp_type: Any = None
    daypart: Any = None
    category: Any = None
    condiments: Any = None


class MenuMasterUpdate(MenuMasterFields):
    revision: Annotated[int, Field(strict=True, gt=0)]


class MenuMasterResponse(BaseModel):
    """Exact public shape of ``menu_service.serialize_menu_master``."""

    id: str
    revision: int
    name: str
    normalized_name: str | None
    unit_type: str | None
    qty_per_serving: float | None
    bag_max_qty: float | None
    bag_max_unit: str | None
    temp_type: str | None
    daypart: str | None
    category: str | None
    # Input remains intentionally permissive, so preserve legacy JSON list values
    # rather than coercing them while documenting the response.
    condiments: list[Any]


class MenuMasterListResponse(BaseModel):
    items: list[MenuMasterResponse]
    total: int
    offset: int
    limit: int


class MenuMasterItemResponse(BaseModel):
    item: MenuMasterResponse


class MenuMasterUpdateResponse(MenuMasterItemResponse):
    updated: bool
