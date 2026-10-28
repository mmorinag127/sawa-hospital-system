"""Documentation-only contracts for the existing solve serializers.

These models are additional OpenAPI responses, not runtime response_model
serializers. Optional warning keys remain absent rather than receiving defaults.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from typing_extensions import NotRequired, TypedDict


class ContractModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class JobFailure(ContractModel):
    code: str | None
    message: str | None


class SolveJobStatus(ContractModel):
    domain: str
    jobId: int
    inputVersion: str | None
    inputReference: None
    attemptIdentity: str | None
    executionContract: dict[str, Any] | None
    provenanceAvailability: str
    transportState: Literal["pending", "claimed", "stopping", "terminal"] | None
    businessState: str
    state: str
    sourceState: str
    failure: JobFailure | None
    artifactReference: int


class SolvePeriod(ContractModel):
    target_month: str
    start_date: str | None
    end_date_exclusive: str | None
    period_mode: str


class SolveCandidate(ContractModel):
    id: int
    strategy: str
    total_score: float
    score_breakdown: dict[str, Any]


class CandidateCommandPrecondition(ContractModel):
    candidate_id: int
    candidate_revision: str
    input_revision: str


class CandidatePreconditionError(ContractModel):
    code: str


class AdditionalWeekdayOff(ContractModel):
    staff_id: int
    staff_name: str
    work_date: str


class CandidateWarning(TypedDict):
    __pydantic_config__ = ConfigDict(strict=True, extra="forbid")
    severity: str
    code: str
    message: str
    work_date: NotRequired[str]
    staff_id: NotRequired[int | None]
    position_id: NotRequired[int]
    dates: NotRequired[list[AdditionalWeekdayOff]]


class HolidayCount(TypedDict):
    __pydantic_config__ = ConfigDict(strict=True, extra="forbid")
    staff_id: int
    staff_name: str
    equivalent_days: float
    paid_leave_adjustment: NotRequired[float]


class RotationBalance(ContractModel):
    rotation_id: int
    rotation_name: str
    position_name: str
    counts: dict[str, int]
    maximum: int
    minimum: int


class CandidateValidation(TypedDict):
    __pydantic_config__ = ConfigDict(strict=True, extra="forbid")
    blocking: bool
    warnings: list[CandidateWarning]
    holiday_counts: list[HolidayCount]
    additional_weekday_offs: list[AdditionalWeekdayOff]
    rotation_balances: list[RotationBalance]
    historical_validation: NotRequired[str]


class SolveResponse(SolvePeriod):
    job_status: SolveJobStatus
    solve_run_id: int
    status: str
    solver_status: str | None
    candidates: list[SolveCandidate]
    assignments_count: int
    violations_count: int


class SolveRunSummary(SolvePeriod):
    job_status: SolveJobStatus | None
    id: int
    status: str
    solver_status: str | None
    started_at: str | None
    finished_at: str | None
    assignments_count: int
    confirmed: bool


class SolveAssignment(ContractModel):
    id: int
    staff_id: int
    position_id: int
    shift_template_id: int
    staff_name: str
    work_date: str
    shift_template: str
    position: str
    source: str
    is_locked: bool
    reason: str


class SolveStaffStatus(ContractModel):
    id: int
    staff_id: int
    shift_template_id: int | None
    source: str | None
    reason: str | None
    staff_name: str
    work_date: str
    status: str
    work_kind: str
    shift_template: str | None


class SolveViolation(ContractModel):
    severity: str
    rule_name: str
    message: str


class SolveRunDetail(SolvePeriod):
    job_status: SolveJobStatus
    command_precondition: CandidateCommandPrecondition | None
    command_precondition_error: CandidatePreconditionError | None
    id: int
    status: str
    solver_status: str | None
    selected_candidate_id: int | None
    candidates: list[SolveCandidate]
    assignments: list[SolveAssignment]
    statuses: list[SolveStaffStatus]
    violations: list[SolveViolation]
    confirmed: bool
    confirmed_at: str | None
    candidate_validation: CandidateValidation | None


class ScheduleCellResult(ContractModel):
    candidate_id: int
    staff_id: int
    work_date: str
    work_kind: str
    assignment_id: int | None


class ScheduleCellResponse(ScheduleCellResult):
    validation: CandidateValidation
    command_precondition: CandidateCommandPrecondition


class ScheduleBulkResponse(ContractModel):
    updated_count: int
    cells: list[ScheduleCellResult]
    validation: CandidateValidation
    command_precondition: CandidateCommandPrecondition


class AssignmentLockResponse(ContractModel):
    assignment_id: int
    locked: bool
    validation: CandidateValidation
    command_precondition: CandidateCommandPrecondition
