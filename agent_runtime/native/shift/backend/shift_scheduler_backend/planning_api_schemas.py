"""Planning payload documentation only; never used to filter runtime responses."""
from datetime import date
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict


class PlanningResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


class PlanningItemResponse(PlanningResponse):
    id: int
    revision: int


class PlanningSkillResponse(PlanningItemResponse):
    name: str


class ShiftTemplateResponse(PlanningItemResponse):
    name: str
    display_name: str | None
    shift_type: str
    start_time: str
    end_time: str
    break_minutes: int


class PositionResponse(PlanningItemResponse):
    name: str
    display_name: str | None
    required_skill_id: int | None
    required_skill_name: str | None
    required_staff_ids: list[int]
    difficulty_weight: int


class WorkdayResponse(PlanningItemResponse):
    work_date: date
    kind: Literal["normal", "half_day", "closed"]
    shift_template_id: int | None
    shift_template_name: str | None
    full_day_shift_template_id: int | None
    full_day_shift_template_name: str | None
    full_day_staff_ids: list[int]
    min_staff_count: int | None
    max_staff_count: int | None
    group_min_counts: dict[str, int]
    group_max_counts: dict[str, int]
    notes: str | None


class DemandResponse(PlanningItemResponse):
    work_date: date
    shift_template_id: int
    shift_template_name: str | None
    position_id: int
    position_name: str | None
    required_count: int
    min_count: int | None
    max_count: int | None
    ideal_count: int | None


class PositionPriorityResponse(PlanningResponse):
    id: int
    position_id: int
    staff_id: int
    staff_name: str
    rank: int
    mode: Literal["strict", "prefer"]


class StaffPositionRuleResponse(PlanningResponse):
    id: int
    staff_id: int
    staff_name: str
    position_id: int
    position_name: str | None
    kind: Literal["allow", "deny"]
    reason: str | None


class StaffPositionPreferenceResponse(PlanningResponse):
    staff_id: int
    penalty: int
    reason: str | None


class StaffPairRuleResponse(PlanningItemResponse):
    first_staff_id: int
    first_staff_name: str
    second_staff_id: int
    second_staff_name: str
    kind: Literal[
        "avoid_same_weekday_off", "avoid_same_day_off", "avoid_same_day_assignment",
        "require_same_day_work", "require_same_day_assignment",
    ]
    strength: Literal["hard", "soft"]
    weight: int
    reason: str | None


class RotationResponse(PlanningItemResponse):
    name: str
    position_id: int
    position_name: str | None
    member_staff_ids: list[int]
    member_names: list[str]
    enabled: bool
    current_queue_staff_ids: list[int]
    current_queue_names: list[str]
    queue_effective_from: date | None
    queue_as_of: date


class PositionAssignmentRuleResponse(PlanningItemResponse):
    position_id: int
    position_name: str
    staff_id: int
    staff_name: str
    shift_template_id: int | None
    shift_template_name: str | None
    effective_from: date
    effective_to: date | None
    reason: str


class ScheduleExceptionResponse(PlanningItemResponse):
    work_date: date
    kind: Literal["fixed_assignment", "rotation_force", "rotation_exclusion"]
    staff_id: int | None
    staff_name: str | None
    position_id: int | None
    position_name: str | None
    shift_template_id: int | None
    shift_template_name: str | None
    rotation_policy_id: int | None
    rotation_policy_name: str | None
    reason: str


Item = TypeVar("Item")


class PlanningCollectionResponse(PlanningResponse, Generic[Item]):
    items: list[Item]
    collection_revision: int


class PlanningCommandResponse(PlanningResponse, Generic[Item]):
    item: Item
    collection_revision: int


class PlanningDeletedItemResponse(PlanningItemResponse):
    deleted: Literal[True]


class PlanningDeleteResponse(PlanningCommandResponse[PlanningDeletedItemResponse]):
    pass


class PlanningOverviewResponse(PlanningResponse):
    skills: list[PlanningSkillResponse]
    shift_templates: list[ShiftTemplateResponse]
    positions: list[PositionResponse]
    workdays: list[WorkdayResponse]
    demands: list[DemandResponse]
    priorities: list[PositionPriorityResponse]
    position_rules: list[StaffPositionRuleResponse]
    pair_rules: list[StaffPairRuleResponse]
    rotations: list[RotationResponse]
    exceptions: list[ScheduleExceptionResponse]
    position_assignment_rules: list[PositionAssignmentRuleResponse]


class PeriodCopyDemandExclusionResponse(PlanningResponse):
    source_demand_id: int
    work_date: date
    position_id: int


Version = TypeVar("Version")


class PeriodCopyResponse(PlanningResponse, Generic[Version]):
    source_start_date: date
    source_end_date_exclusive: date
    target_start_date: date
    target_end_date_exclusive: date
    period_mode: Literal["calendar_month", "fixed_start_day", "custom"]
    mapping_mode: Literal["relative_day", "weekday_occurrence"]
    workday_source_count: int
    demand_source_count: int
    workday_copy_count: int
    demand_copy_count: int
    prohibited_demand_exclusions: list[PeriodCopyDemandExclusionResponse]
    conflict_count: int
    unmapped_count: int
    applied: bool
    preview_versions: Version
    result_versions: Version | None = None


class PdfReferenceSourceResponse(PlanningResponse):
    source_name: str
    start_date: date
    end_date: date
    end_date_exclusive: date
    reference_status_count: int
    reference_assignment_count: int


class PdfMismatchResponse(PlanningResponse):
    work_date: date
    staff: str
    pdf_label: str
    solver_label: str
    issue: str | None = None


class PdfComparisonResponse(PlanningResponse):
    source_name: str
    candidate_id: int
    start_date: date
    end_date: date
    cell_count: int
    checked_cell_count: int
    reference_status_count: int
    reference_assignment_count: int
    mismatch_count: int
    unreadable_or_unresolved_count: int
    match: bool
    mismatches: list[PdfMismatchResponse]
