from pydantic import BaseModel


class RegisteredProcess(BaseModel):
    revision_id: int
    version: int
    status: str


class RegisteredRouteArtifact(BaseModel):
    id: int
    process_revision_id: int
    generation_status: str


class RegisteredMenu(BaseModel):
    id: int
    name: str
    version: int
    status: str
    source_document_id: str | None
    process: RegisteredProcess | None
    route_artifacts: list[RegisteredRouteArtifact]


class RegisteredPrice(BaseModel):
    id: str
    label: str
    source_menu_execution_ids: list[int]
    issues: list[str]


class RegisteredSchedule(BaseModel):
    id: int
    version: int
    status: str
    stale: bool


class RegisteredRoute(BaseModel):
    id: int
    version: int
    layout_id: int
    layout_version: int
    blockers: list[str]


class RegisteredWork(BaseModel):
    id: str
    facility_id: int | None
    facility_name: str | None
    service_date: str | None
    menus: list[RegisteredMenu]
    prices: list[RegisteredPrice]
    schedules: list[RegisteredSchedule]
    routes: list[RegisteredRoute]
    next_actions: list[str]
    review_reasons: list[str]


class RegisteredWorkResponse(BaseModel):
    items: list[RegisteredWork]
    next_after_id: str | None = None


class RegisteredWorkIndex(BaseModel):
    first_dates: dict[str, str]
    months: list[str]
    unassigned_count: int
