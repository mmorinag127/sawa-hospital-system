"""Documentation-only price quantity response; no response filtering."""
from datetime import date

from pydantic import BaseModel


class MenuPriceQuantityItemResponse(BaseModel):
    ingredient_id: int
    amount_per_person: str | None
    unit: str | None
    source: str
    issue: str


class MenuPriceQuantitiesResponse(BaseModel):
    menu_execution_id: int
    facility_id: int
    service_date: date
    quantity_items: list[MenuPriceQuantityItemResponse]
