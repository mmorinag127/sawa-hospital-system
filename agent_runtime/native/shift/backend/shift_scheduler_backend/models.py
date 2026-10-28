from datetime import date, datetime, time

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, JSON, String, Time, UniqueConstraint, event, inspect, select, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Staff(Base):
    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    staff_group: Mapped[str] = mapped_column(String(80), default="general")
    max_daily_work_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_weekly_work_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_monthly_work_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_monthly_work_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_monthly_work_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paid_leave_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    active_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    active_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    skills_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    skills: Mapped[list["StaffSkill"]] = relationship(back_populates="staff")


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class StaffSkill(Base):
    __tablename__ = "staff_skills"
    __table_args__ = (UniqueConstraint("staff_id", "skill_id", name="uq_staff_skill"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), index=True)
    level: Mapped[int] = mapped_column(Integer, default=1)

    staff: Mapped[Staff] = relationship(back_populates="skills")
    skill: Mapped[Skill] = relationship()


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    required_skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id"), nullable=True)
    difficulty_weight: Mapped[int] = mapped_column(Integer, default=1)
    required_staff_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    required_skill: Mapped[Skill | None] = relationship()


class StaffPositionPreference(Base):
    __tablename__ = "staff_position_preferences"
    __table_args__ = (
        UniqueConstraint("staff_id", "position_id", name="uq_staff_position_preference"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    penalty: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    staff: Mapped[Staff] = relationship()
    position: Mapped[Position] = relationship()


class StaffPositionRule(Base):
    """Hard staff-to-position eligibility or prohibition rules."""

    __tablename__ = "staff_position_rules"
    __table_args__ = (
        UniqueConstraint("staff_id", "position_id", name="uq_staff_position_rule"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="deny")
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    staff: Mapped[Staff] = relationship()
    position: Mapped[Position] = relationship()


class PositionAssignmentRule(Base):
    __tablename__ = "position_assignment_rules"
    __table_args__ = (CheckConstraint(
        "effective_to IS NULL OR effective_to >= effective_from",
        name="ck_position_assignment_rule_dates",
    ),)

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    shift_template_id: Mapped[int | None] = mapped_column(ForeignKey("shift_templates.id"), nullable=True, index=True)
    effective_from: Mapped[date] = mapped_column(Date, index=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    reason: Mapped[str] = mapped_column(String(500))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    position: Mapped[Position] = relationship()
    staff: Mapped[Staff] = relationship()
    shift_template: Mapped["ShiftTemplate | None"] = relationship()


class CandidateStaffStatusExplanation(Base):
    __tablename__ = "candidate_staff_status_explanations"
    __table_args__ = (UniqueConstraint("status_id", name="uq_candidate_staff_status_explanation"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    status_id: Mapped[int] = mapped_column(ForeignKey("candidate_staff_statuses.id"), index=True)
    category: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(String(500))

    status: Mapped["CandidateStaffStatus"] = relationship()


class RuleConfig(Base):
    __tablename__ = "rule_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)


class EditorRevision(Base):
    """One CAS token for each editable master collection."""

    __tablename__ = "editor_revisions"

    resource: Mapped[str] = mapped_column(String(80), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)


class RuleProfile(Base):
    """A reusable, ID-based set of scheduling rules applied during every solve."""

    __tablename__ = "rule_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    profile_json: Mapped[dict] = mapped_column(JSON, default=dict)


class ShiftTemplate(Base):
    __tablename__ = "shift_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    shift_type: Mapped[str] = mapped_column(String(40), default="regular")
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    break_minutes: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class WorkdayRule(Base):
    """The ordinary working mode for a calendar date.

    Position demands remain separate. This table answers whether an active
    staff member is normally working an 8-hour day, a half day, or a closed
    day when no position name is printed in the schedule.
    """

    __tablename__ = "workday_rules"
    __table_args__ = (UniqueConstraint("work_date", name="uq_workday_rule_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(20), default="normal")
    shift_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("shift_templates.id"), nullable=True
    )
    full_day_shift_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("shift_templates.id"), nullable=True
    )
    full_day_staff_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    min_staff_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_staff_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    group_min_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    group_max_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    shift_template: Mapped[ShiftTemplate | None] = relationship(
        foreign_keys=[shift_template_id]
    )
    full_day_shift_template: Mapped[ShiftTemplate | None] = relationship(
        foreign_keys=[full_day_shift_template_id]
    )


class Demand(Base):
    __tablename__ = "demands"
    __table_args__ = (
        UniqueConstraint("work_date", "shift_template_id", "position_id", name="uq_demand_slot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    shift_template_id: Mapped[int] = mapped_column(ForeignKey("shift_templates.id"))
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    required_count: Mapped[int] = mapped_column(Integer)
    min_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ideal_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    shift_template: Mapped[ShiftTemplate] = relationship()
    position: Mapped[Position] = relationship()


class Availability(Base):
    __tablename__ = "availabilities"

    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(40))
    strength: Mapped[str] = mapped_column(String(20), default="soft")
    weight: Mapped[int] = mapped_column(Integer, default=10)
    shift_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("shift_templates.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    staff: Mapped[Staff] = relationship()
    shift_template: Mapped["ShiftTemplate | None"] = relationship()


class StaffPairRule(Base):
    """A reusable relationship rule between two staff members."""

    __tablename__ = "staff_pair_rules"
    __table_args__ = (
        UniqueConstraint("first_staff_id", "second_staff_id", "kind", name="uq_staff_pair_rule"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    first_staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    second_staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    kind: Mapped[str] = mapped_column(String(60), index=True)
    strength: Mapped[str] = mapped_column(String(20), default="hard")
    weight: Mapped[int] = mapped_column(Integer, default=10)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    first_staff: Mapped[Staff] = relationship(foreign_keys=[first_staff_id])
    second_staff: Mapped[Staff] = relationship(foreign_keys=[second_staff_id])


class SolveRun(Base):
    __tablename__ = "solve_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_month: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    solver_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, active_history=True)
    period_summary_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    period_end_key: Mapped[str | None] = mapped_column(String(10), nullable=True)
    display_period_end_key: Mapped[str | None] = mapped_column(String(10), nullable=True)
    period_known: Mapped[bool] = mapped_column(Boolean, nullable=False)
    period_display_error: Mapped[bool] = mapped_column(Boolean, nullable=False)
    prior_candidate_start_key: Mapped[str | None] = mapped_column(String(10), nullable=True)
    rotation_end_key: Mapped[str | None] = mapped_column(String(10), nullable=True)
    rotation_reference_error: Mapped[bool] = mapped_column(Boolean, nullable=False)
    __table_args__ = (
        Index('ix_solve_runs_period_end_status_id', 'period_end_key', 'status', 'id'),
        Index('ix_solve_runs_period_integrity', 'period_display_error', 'period_known', 'status', 'id'),
        Index('ix_solve_runs_confirmed_period', 'status', 'period_known', 'display_period_end_key', 'id'),
        Index('ix_solve_runs_rotation_integrity', 'rotation_reference_error', 'id'),
    )


class SolveRunRotationReference(Base):
    __tablename__ = 'solve_run_rotation_references'

    policy_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    solve_run_id: Mapped[int] = mapped_column(ForeignKey('solve_runs.id', ondelete='CASCADE'), primary_key=True, index=True)


@event.listens_for(SolveRun, 'before_insert')
@event.listens_for(SolveRun, 'before_update')
def _period_metadata(_mapper, _connection, run):
    from .period_metadata_v1 import period_metadata, rotation_metadata
    values = {**period_metadata(run.target_month, run.config_json), **rotation_metadata(run.config_json)[0]}
    for key, value in values.items():
        setattr(run, key, value)


def _write_rotation_references(connection, run):
    from .period_metadata_v1 import rotation_metadata
    table = SolveRunRotationReference.__table__
    connection.execute(table.delete().where(table.c.solve_run_id == run.id))
    references = rotation_metadata(run.config_json)[1]
    if references:
        connection.execute(table.insert(), [{'solve_run_id': run.id, 'policy_key': key} for key in references])


@event.listens_for(SolveRun, 'after_insert')
def _insert_rotation_references(_mapper, connection, run):
    _write_rotation_references(connection, run)


@event.listens_for(SolveRun, 'after_update')
def _update_rotation_references(_mapper, connection, run):
    if inspect(run).attrs.config_json.history.has_changes():
        _write_rotation_references(connection, run)


@event.listens_for(SolveRun, "before_update")
def _preserve_solve_execution_contract(mapper, connection, run):
    # Read via the flush's writer connection, never the mutated identity map or
    # JSON history (flag_modified need not retain a deleted/original value).
    previous = connection.execute(select(SolveRun.__table__.c.config_json).where(
        SolveRun.__table__.c.id == inspect(run).identity[0]
    ).with_for_update()).scalar_one()
    from .jobs import canonical_execution_json, execution_json_equal, recorded_input_version
    canonical_execution_json(previous)
    canonical_execution_json(run.config_json)
    original = (previous or {}).get("execution_contract")
    current = (run.config_json or {}).get("execution_contract")
    if original is None:
        if current is not None:
            raise ValueError("shift_execution_legacy_original_unavailable")
        return
    identity_keys = ("version", "attemptIdentity", "request")
    if current is None or not execution_json_equal(
        {key: current[key] for key in identity_keys if key in current},
        {key: original[key] for key in identity_keys if key in original},
    ):
        raise ValueError("shift_execution_identity_immutable")
    if not execution_json_equal(original["inputAvailability"], "not_captured") and not execution_json_equal(current, original):
        raise ValueError("shift_execution_input_immutable")
    if not execution_json_equal(current["inputAvailability"], "not_captured") and not execution_json_equal(
        current["inputVersion"], recorded_input_version(run.config_json)
    ):
        raise ValueError("shift_execution_recorded_input_mismatch")
    if not execution_json_equal(original["inputAvailability"], "not_captured") and not execution_json_equal(
        {key: previous[key] for key in ("generation_rule_source",) if key in previous},
        {key: run.config_json[key] for key in ("generation_rule_source",) if key in run.config_json},
    ):
        raise ValueError("shift_execution_source_immutable")
    terminal = (previous or {}).get("execution_terminal")
    if terminal is not None and not execution_json_equal(terminal, (run.config_json or {}).get("execution_terminal")):
        raise ValueError("shift_execution_terminal_immutable")


class SolveDelivery(Base):
    __tablename__ = "solve_deliveries"
    __table_args__ = (
        CheckConstraint("generation >= 1", name="ck_solve_delivery_generation"),
        CheckConstraint("state IN ('pending', 'claimed', 'stopping', 'terminal')", name="ck_solve_delivery_state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    solve_run_id: Mapped[int] = mapped_column(ForeignKey("solve_runs.id"), unique=True, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    owner_attempt: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    transport_contract: Mapped[dict] = mapped_column(JSON, nullable=False)
    captured_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    input_version: Mapped[str] = mapped_column(String(64), nullable=False)
    read_set: Mapped[dict] = mapped_column(JSON, nullable=False)
    receipt: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)


@event.listens_for(SolveDelivery, "before_update")
def _preserve_delivery_capture(mapper, connection, delivery):
    from .jobs import execution_json_equal
    previous = connection.execute(select(SolveDelivery.__table__).where(
        SolveDelivery.id == inspect(delivery).identity[0]
    )).mappings().one()
    for key in ("solve_run_id", "generation", "transport_contract", "captured_config", "input_version", "read_set"):
        if not execution_json_equal(previous[key], getattr(delivery, key)):
            raise ValueError("shift_delivery_capture_immutable")
    transitions = {"pending": {"pending", "claimed"}, "claimed": {"claimed", "stopping", "terminal"},
        "stopping": {"stopping", "terminal"}, "terminal": {"terminal"}}
    if delivery.state not in transitions[previous["state"]]:
        raise ValueError("shift_delivery_terminal_immutable" if previous["state"] == "terminal"
            else "shift_delivery_state_transition_invalid")
    if previous["owner_attempt"] is not None and delivery.owner_attempt != previous["owner_attempt"]:
        raise ValueError("shift_delivery_owner_immutable")
    if previous["receipt"] is not None or previous["state"] == "terminal":
        for key in ("state", "owner_attempt", "lease_until", "receipt"):
            if key == "lease_until":
                equal = previous[key] == getattr(delivery, key)
            else:
                equal = execution_json_equal(previous[key], getattr(delivery, key))
            if not equal:
                raise ValueError("shift_delivery_terminal_immutable")


class SolveDeliveryReview(Base):
    __tablename__ = "solve_delivery_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("solve_deliveries.id"), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class CandidateSchedule(Base):
    __tablename__ = "candidate_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    solve_run_id: Mapped[int] = mapped_column(ForeignKey("solve_runs.id"), index=True)
    candidate_no: Mapped[int] = mapped_column(Integer)
    strategy: Mapped[str] = mapped_column(String(80))
    total_score: Mapped[int] = mapped_column(BigInteger, default=0)
    score_breakdown_json: Mapped[dict] = mapped_column(JSON, default=dict)

    solve_run: Mapped[SolveRun] = relationship()


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate_schedules.id"), index=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    shift_template_id: Mapped[int] = mapped_column(ForeignKey("shift_templates.id"))
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(40), default="solver")

    candidate: Mapped[CandidateSchedule] = relationship()
    staff: Mapped[Staff] = relationship()
    shift_template: Mapped[ShiftTemplate] = relationship()
    position: Mapped[Position] = relationship()


class CandidateStaffStatus(Base):
    """Explicit daily state so an empty position cell is not confused with off."""

    __tablename__ = "candidate_staff_statuses"
    __table_args__ = (
        UniqueConstraint("candidate_id", "staff_id", "work_date", name="uq_candidate_staff_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate_schedules.id"), index=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20))
    work_kind: Mapped[str] = mapped_column(String(20))
    shift_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("shift_templates.id"), nullable=True
    )

    candidate: Mapped[CandidateSchedule] = relationship()
    staff: Mapped[Staff] = relationship()
    shift_template: Mapped[ShiftTemplate | None] = relationship(foreign_keys=[shift_template_id])


class Violation(Base):
    __tablename__ = "violations"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidate_schedules.id"), nullable=True, index=True
    )
    solve_run_id: Mapped[int | None] = mapped_column(ForeignKey("solve_runs.id"), nullable=True)
    severity: Mapped[str] = mapped_column(String(40))
    rule_name: Mapped[str] = mapped_column(String(120))
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), nullable=True)
    work_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    message: Mapped[str] = mapped_column(String(1000))
    penalty: Mapped[int] = mapped_column(Integer, default=0)

    candidate: Mapped[CandidateSchedule | None] = relationship()
    staff: Mapped[Staff | None] = relationship()


class ReferenceAssignment(Base):
    __tablename__ = "reference_assignments"
    # Stores normalized assignments extracted from a source schedule such as the PDF table.
    # This is used to compare solver output with the source schedule cell by cell.
    __table_args__ = (
        UniqueConstraint(
            "source_name",
            "staff_id",
            "work_date",
            "shift_template_id",
            "position_id",
            name="uq_reference_assignment",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_name: Mapped[str] = mapped_column(String(120), index=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    shift_template_id: Mapped[int] = mapped_column(ForeignKey("shift_templates.id"))
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    raw_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    confidence: Mapped[str] = mapped_column(String(40), default="medium")
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_row_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_col_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    staff: Mapped[Staff] = relationship()
    shift_template: Mapped[ShiftTemplate] = relationship()
    position: Mapped[Position] = relationship()


class ReferenceStaffStatus(Base):
    """A manually transcribed non-assignment cell from a reference schedule."""

    __tablename__ = "reference_staff_statuses"
    __table_args__ = (
        UniqueConstraint("source_name", "staff_id", "work_date", name="uq_reference_staff_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_name: Mapped[str] = mapped_column(String(120), index=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20))
    work_kind: Mapped[str] = mapped_column(String(20))
    raw_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    confidence: Mapped[str] = mapped_column(String(40), default="medium")
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_row_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_col_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    staff: Mapped[Staff] = relationship()


class PositionPriority(Base):
    """A reusable staff ordering rule for one position."""

    __tablename__ = "position_priorities"
    __table_args__ = (
        UniqueConstraint("position_id", "staff_id", name="uq_position_priority"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(20), default="prefer")

    position: Mapped[Position] = relationship()
    staff: Mapped[Staff] = relationship()


class RotationPolicy(Base):
    """An ordered, reusable assignment rotation for a position."""

    __tablename__ = "rotation_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), unique=True)
    member_staff_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    position: Mapped[Position] = relationship()


class RotationQueueState(Base):
    """The confirmed queue that becomes the starting point for the next period."""

    __tablename__ = "rotation_queue_states"
    __table_args__ = (Index("ix_rotation_queue_policy_date_id", "rotation_policy_id", "effective_from", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rotation_policy_id: Mapped[int] = mapped_column(ForeignKey("rotation_policies.id"), index=True)
    effective_from: Mapped[date] = mapped_column(Date, index=True)
    queue_staff_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    source_solve_run_id: Mapped[int | None] = mapped_column(ForeignKey("solve_runs.id"), nullable=True)

    rotation_policy: Mapped[RotationPolicy] = relationship()
    source_solve_run: Mapped[SolveRun | None] = relationship()


class ScheduleException(Base):
    """A date-specific instruction which is applied before optimization."""

    __tablename__ = "schedule_exceptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), nullable=True, index=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True)
    shift_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("shift_templates.id"), nullable=True
    )
    rotation_policy_id: Mapped[int | None] = mapped_column(
        ForeignKey("rotation_policies.id"), nullable=True, index=True
    )
    reason: Mapped[str] = mapped_column(String(500))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    staff: Mapped[Staff | None] = relationship()
    position: Mapped[Position | None] = relationship()
    shift_template: Mapped[ShiftTemplate | None] = relationship()
    rotation_policy: Mapped[RotationPolicy | None] = relationship()


class AssignmentExplanation(Base):
    """Human-readable evidence for an individual calculated assignment."""

    __tablename__ = "assignment_explanations"
    __table_args__ = (UniqueConstraint("assignment_id", name="uq_assignment_explanation"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), index=True)
    category: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(String(500))

    assignment: Mapped[Assignment] = relationship()


class ScheduleConfirmation(Base):
    """Marks a schedule as accepted and makes its rotation queues authoritative."""

    __tablename__ = "schedule_confirmations"
    __table_args__ = (UniqueConstraint("solve_run_id", name="uq_schedule_confirmation"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    solve_run_id: Mapped[int] = mapped_column(ForeignKey("solve_runs.id"), index=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime)

    solve_run: Mapped[SolveRun] = relationship()

    def stored_timestamp(self, session) -> str:
        """Serialize the persisted representation without inventing timezone data."""
        state = inspect(self)
        if not state.persistent or state.session is not session or not state.identity:
            raise RuntimeError("confirmation_identity_unresolved")
        if state.attrs.confirmed_at.history.has_changes() or state.attrs.id.history.has_changes():
            raise RuntimeError("confirmation_timestamp_pending_edit")
        with session.no_autoflush:
            stored = session.execute(
                select(ScheduleConfirmation.confirmed_at).where(
                    ScheduleConfirmation.id == state.identity[0]
                )
            ).scalar_one_or_none()
        if stored is None:
            raise RuntimeError("confirmation_timestamp_unresolved")
        return stored.isoformat()
