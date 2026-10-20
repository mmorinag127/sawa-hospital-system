"""Documentation only: no runtime response filtering or coercion."""
from pydantic import BaseModel, ConfigDict


class TimingProfileResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: int
    facility_name: str
    name: str
    plating_start: str
    plating_end: str
    timeline_ticks: list[str]
    version: int


class CalculatedTaskResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    key: str
    start_time: str
    end_time: str


class CalculationResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    profile: TimingProfileResponse
    tasks: list[CalculatedTaskResponse]
