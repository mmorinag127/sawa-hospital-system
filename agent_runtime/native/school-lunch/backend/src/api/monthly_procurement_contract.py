"""Documentation-only contracts from compute; no response filtering."""
from pydantic import BaseModel, ConfigDict


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class FacilityResponse(Contract):
    id: int
    name: str


class IncomingResponse(Contract):
    date: str
    quantity: str


class MaterialResponse(Contract):
    ingredient_id: int
    name: str
    stock_unit: str | None
    order_unit: str | None
    conversion_rate: str | None
    opening_stock: str | None
    unit_price: str | None
    price_unit: str | None
    supplier: str
    order_increment: str | None
    incoming: list[IncomingResponse]


class CostRowResponse(Contract):
    date: str
    menu_id: int
    menu: str
    servings: int
    ingredient_id: int
    ingredient: str
    quantity: str
    unit: str
    unit_price: str | None
    price_unit: str | None
    cost: str | None
    per_person_cost: str | None


class DailyCostResponse(Contract):
    date: str
    cost: str | None


class OrderResponse(Contract):
    date: str
    ingredient_id: int
    ingredient: str
    supplier: str
    quantity: str
    unit: str
    stock_quantity: str


class StockRowResponse(Contract):
    date: str
    ingredient_id: int
    ingredient: str
    unit: str
    opening: str
    incoming: str
    required: str
    shortage: str
    planned_order_stock: str
    projected_closing: str


class MonthlyProcurementResponse(Contract):
    facility: FacilityResponse
    month: str
    version: int
    menu_versions: dict[str, str]
    stale: bool
    menu_count: int
    ingredients: list[MaterialResponse]
    cost_rows: list[CostRowResponse]
    daily_costs: list[DailyCostResponse]
    month_cost: str | None
    stock_rows: list[StockRowResponse]
    orders: list[OrderResponse]
    issues: list[str]
    inventory_ready: bool
    inventory_issues: list[str]


EXPORT_RESPONSE = {
    200: {
        "description": "Completed native workbook or converted PDF bytes",
        "content": {media: {"schema": {"type": "string", "format": "binary"}} for media in (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/pdf")},
        "headers": {name: {"schema": {"type": kind}} for name, kind in (
            ("Content-Disposition", "string"), ("Content-Length", "integer"), ("Cache-Control", "string"))},
    }
}
