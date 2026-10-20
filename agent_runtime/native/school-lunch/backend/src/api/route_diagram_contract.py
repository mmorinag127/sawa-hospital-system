"""Documentation only: serializer shapes, independent of request validation."""
from pydantic import BaseModel, ConfigDict


class RouteLayoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: int
    facility_id: int
    name: str
    version: int
    image: str
    locations: dict[str, tuple[float, float]]


class RouteResponse(BaseModel):
    # public_route preserves unknown historical keys. No runtime filtering.
    model_config = ConfigDict(extra="allow")
    id: int
    label: str
    color: str
    locations: list[str]
    styles: list[str | None]
    map_label: str | None = None
    points: list[tuple[float, float]] | None = None
    label_position: tuple[float, float] | None = None
    segment_states: list[dict] | None = None
    source_provenance: dict | None = None
    label_modes: dict | None


class RouteDiagramResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    facility_id: int
    service_date: str
    layout_id: int
    layout_version: int
    version: int
    source_versions: dict[str, int]
    routes: list[RouteResponse]
    blockers: list[str]
    source_modifications: dict[str, bool | None]


class RouteCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    points: list[tuple[float, float]]


class RouteSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    diagram_id: int
    version: int
    status: str
    filename: str
    source_sha256: str
    image_sha256: str
    page: int
    image: str
    settings: dict
    candidates: list[RouteCandidateResponse]
    confirmation: dict | None
    blockers: list[str]
