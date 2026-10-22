"""Documentation only: serializer envelopes, with stored JSON left open."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, JsonValue, Field


class Envelope(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')


class ProcessMenu(Envelope):
    id: int
    name: str
    facility_id: int
    service_date: str
    version: int


class ProcessHistory(Envelope):
    id: int
    number: int
    status: Literal['draft', 'confirmed']
    scope: Literal['menu', 'day']
    created_at: str
    origin: str
    document: dict[str, JsonValue]
    can_restore: bool
    change_note: str
    changes: list[dict[str, JsonValue]]


class ProcessRecipe(Envelope):
    id: int
    name: str
    revision: int


class ProcessProposal(Envelope):
    proposal_id: int
    document: dict[str, JsonValue]
    base_version: int
    example_revision_ids: list[int]
    model: str
    issues: list[str]
    fixed_step_ids: list[str]


class ProcessDefaults(Envelope):
    base_version: int
    document: dict[str, JsonValue]


class ProcessEquipment(Envelope):
    type: str | None
    id: str | None


class SharedMenuDefaults(Envelope):
    revision_id: int
    template_id: str | None
    equipment: dict[str, ProcessEquipment]


class ProcessCorrectionChange(Envelope):
    path: str
    before: JsonValue | None = None
    after: JsonValue | None = None


class ReuseCondition(Envelope):
    id: str
    status: Literal['compatible', 'unknown', 'mismatch']
    message: str


class ReuseCompatibility(Envelope):
    compatible: bool
    conditions: list[ReuseCondition]


class ProcessCorrectionProposal(Envelope):
    base_version: int
    document: dict[str, JsonValue]
    changes: list[ProcessCorrectionChange]
    question: str | None
    choices: list[str]
    issues: list[str]
    instruction: str


class ProcessContext(Envelope):
    menu: ProcessMenu
    version: int
    recipe_id: int | None
    recipe_revision: int
    revision: int | None
    status: Literal['draft', 'confirmed']
    scope: Literal['menu', 'day']
    document: dict[str, JsonValue]
    suggested_document: dict[str, JsonValue] | None = None
    shared_menu_defaults: SharedMenuDefaults | None
    proposal: ProcessProposal | None
    history: list[ProcessHistory]
    recipes: list[ProcessRecipe]


class SharedMenuAdoptionContext(ProcessContext):
    shared_menu_source_revision_id: int


class SupplyPoint(Envelope):
    id: str
    name: str
    role: Literal['inspection', 'storage', 'on_site']
    unavailable_reason: str = None
    unavailable_from: dict[str, str] = Field(default_factory=dict)


class RoutePort(Envelope):
    id: str
    name: str
    side: str


class RouteEquipment(Envelope):
    id: str
    name: str
    type: str | None
    ports: list[RoutePort]
    bounds: list[float] | None = None


class KitchenDiagram(Envelope):
    svg: str
    view_box: list[int | float]


class RouteTemplate(Envelope):
    template_id: str
    template_version: int
    material_sources: list[SupplyPoint]
    equipment: list[RouteEquipment]
    diagram: KitchenDiagram | None = None


class RouteOptions(Envelope):
    templates: list[RouteTemplate]
    colors: list[str]
    day_colors: list[str]


class DrawingEquipment(Envelope):
    id: str
    name: str
    bounds: list[float]


class ProcessQuestionDrawing(Envelope):
    template_id: str
    template_version: int
    sha256: str
    view_box: list[float]
    title: str
    svg_base64: str
    equipment: list[DrawingEquipment]


class ProfileRevision(Envelope):
    id: int
    number: int
    status: Literal['draft', 'confirmed']
    template_id: str
    template_version: int
    document: dict[str, JsonValue]
    change_note: str
    created_at: str


class RouteProfile(Envelope):
    id: int
    facility_id: int
    name: str
    version: int
    head_revision_id: int | None
    confirmed_revision_id: int | None
    revision: ProfileRevision


class RouteArtifact(Envelope):
    id: int
    menu_id: int
    process_revision_id: int
    profile_revision_id: int
    renderer_version: str
    input_hash: str
    svg: str
    svg_hash: str
    topology: list[JsonValue]
    input_snapshot: dict[str, JsonValue]
    generation_status: Literal['draft', 'confirmed']
    warnings: list[str]
    created_at: str


class RouteInput(Envelope):
    menu_id: int
    revision_id: int
    document: dict[str, JsonValue]
    unassigned_equipment_steps: list[str]
    process_confirmed: Literal[True]
    ready_for_geometry: Literal[False]
    geometry_note: str
