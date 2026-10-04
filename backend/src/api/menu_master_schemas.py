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
