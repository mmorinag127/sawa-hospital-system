"""Documentation-only models for the existing monthly overview serializer."""
from datetime import date
from pydantic import BaseModel, ConfigDict


class OverviewDay(BaseModel):
    model_config = ConfigDict(extra='forbid')
    date: date
    menu_count: int
    menu_statuses: list[str]
    work_schedule_id: int | None
    work_schedule_status: str | None
    work_schedule_stale: bool
    route_diagram_id: int | None
    route_diagram_version: int | None
    route_layout_id: int | None
    route_layout_version: int | None
    route_diagram_blockers: list[str]


class OverviewFacility(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: int
    name: str
    days: list[OverviewDay]


class MonthlyOverviewResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    month: str
    start_date: date
    end_date: date
    facilities: list[OverviewFacility]
    next_after_id: int | None = None
