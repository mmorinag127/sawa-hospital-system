from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
import hashlib
import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from sqlalchemy import func, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from .db import get_session
from .planning_api_schemas import (
    PlanningCollectionResponse, PlanningCommandResponse, PlanningDeleteResponse, PositionDetailResponse,
    ShiftTemplateResponse, PositionResponse, WorkdayResponse, DemandResponse,
    PositionPriorityResponse, StaffPositionRuleResponse, StaffPositionPreferenceResponse,
    StaffPairRuleResponse, RotationResponse, ScheduleExceptionResponse, PlanningOverviewResponse, PositionAssignmentRuleResponse,
    PeriodCopyResponse, PdfReferenceSourceResponse, PdfComparisonResponse,
)
from .models import (
    Assignment,
    Availability,
    CandidateStaffStatus,
    CandidateSchedule,
    Demand,
    Position,
    PositionAssignmentRule,
    PositionPriority,
    ReferenceAssignment,
    ReferenceStaffStatus,
    RuleConfig,
    RotationPolicy,
    RotationQueueState,
    ScheduleConfirmation,
    ScheduleException,
    ShiftTemplate,
    Skill,
    Staff,
    StaffPairRule,
    StaffPositionPreference,
    StaffPositionRule,
    StaffSkill,
    SolveRun,
    WorkdayRule,
    EditorRevision,
)
from .editor_revision_service import (
    begin as begin_editor_write, begin_multi_resource_write_snapshot, claim_collection, claim_item, command_payload, finalize_command,
    begin_read_snapshot, end_read_snapshot, read_collection, require_revision, technical_revision,
)
from .periods import build_active_settings, resolve_period_request
from .pdf_parity import compare_reference_cells
from .candidate_validation import validate_candidate
from .validation_snapshot import begin_confirmation_snapshot, historical_candidate_validation, validate_for_confirmation
from .schedule_selection import confirmed_candidate, prior_candidate_state_hash, prior_history_coverage_errors, require_direct_predecessor, unresolved_candidate_detail
from .rule_config_utils import is_position_prohibited
from .rotation_queue import resolve_rotation_queue, validate_rotation_snapshot


router = APIRouter(prefix="/planning", tags=["planning"])
RevisionToken = StrictInt | None


class PositionAssignmentRuleInput(BaseModel):
    position_id: int
    staff_id: int
    shift_template_id: int | None = None
    effective_from: date
    effective_to: date | None = None
    reason: str = Field(min_length=1, max_length=500)
    revision: RevisionToken = None
    collection_revision: RevisionToken = None


class SkillInput(BaseModel):
    name: str


class SkillUpdateInput(SkillInput):
    revision: RevisionToken = None


class StaffSkillsInput(BaseModel):
    skill_ids: list[int] = Field(default_factory=list)
    revision: RevisionToken = None


class SkillResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    id: int
    name: str
    revision: int


class StaffSkillsResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    staff_id: int
    skill_ids: list[int]
    revision: int


class ShiftTemplateInput(BaseModel):
    name: str
    display_name: str | None = Field(default=None, max_length=120)
    shift_type: str = "regular"
    start_time: time
    end_time: time
    break_minutes: int = 0
    revision: RevisionToken = None
    collection_revision: RevisionToken = None


class PositionInput(BaseModel):
    name: str
    display_name: str | None = Field(default=None, max_length=120)
    required_skill_id: int | None = None
    required_staff_ids: list[int] = Field(default_factory=list)
    difficulty_weight: int = Field(default=1, ge=1)
    revision: RevisionToken = None
    collection_revision: RevisionToken = None


class DemandInput(BaseModel):
    work_date: date
    shift_template_id: int
    position_id: int
    required_count: int = Field(ge=0)
    min_count: int | None = Field(default=None, ge=0)
    max_count: int | None = Field(default=None, ge=0)
    ideal_count: int | None = Field(default=None, ge=0)
    revision: RevisionToken = None
    collection_revision: RevisionToken = None


class WorkdayInput(BaseModel):
    work_date: date
    kind: str = "normal"
    shift_template_id: int | None = None
    full_day_shift_template_id: int | None = None
    full_day_staff_ids: list[int] = Field(default_factory=list)
    min_staff_count: int | None = Field(default=None, ge=0)
    max_staff_count: int | None = Field(default=None, ge=0)
    group_min_counts: dict[str, int] = Field(default_factory=dict)
    group_max_counts: dict[str, int] = Field(default_factory=dict)
    notes: str | None = Field(default=None, max_length=500)
    revision: RevisionToken = None
    collection_revision: RevisionToken = None


class PeriodCopyInput(BaseModel):
    target_month: str | None = None
    start_date: date | None = None
    end_date_exclusive: date | None = None
    dry_run: bool = True
    conflict_mode: str = "error"
    include_workdays: bool = True
    include_demands: bool = True
    preview_versions: "PeriodCopyPreviewVersions | None" = None


class PeriodCopyPreviewVersions(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    source_workdays_revision: StrictStr
    target_workdays_revision: StrictStr
    source_demands_revision: StrictStr
    target_demands_revision: StrictStr
    settings_revision: StrictStr
    workdays_collection_revision: StrictInt
    demands_collection_revision: StrictInt


class PriorityInput(BaseModel):
    staff_id: int
    rank: int = Field(ge=1)
    mode: str = "prefer"


class PrioritiesInput(BaseModel):
    priorities: list[PriorityInput] = Field(default_factory=list)


class StaffPositionRuleInput(BaseModel):
    staff_id: int
    kind: str = "deny"
    reason: str | None = None


class StaffPositionPreferenceInput(BaseModel):
    staff_id: int
    penalty: int = Field(ge=0, le=1000)
    reason: str | None = Field(default=None, max_length=500)


class RotationInput(BaseModel):
    name: str
    position_id: int
    member_staff_ids: list[int] = Field(min_length=1)
    enabled: bool = True
    effective_from: date | None = None
    revision: RevisionToken = None
    collection_revision: RevisionToken = None


class RotationQueueInput(BaseModel):
    queue_staff_ids: list[int] = Field(min_length=1)
    effective_from: date | None = None
    revision: RevisionToken = None
    collection_revision: RevisionToken = None


class StaffPairRuleInput(BaseModel):
    first_staff_id: int
    second_staff_id: int
    kind: str
    strength: str = "hard"
    weight: int = Field(default=10, ge=1)
    reason: str | None = None
    revision: RevisionToken = None
    collection_revision: RevisionToken = None


class ScheduleExceptionInput(BaseModel):
    work_date: date
    kind: str
    staff_id: int | None = None
    position_id: int | None = None
    shift_template_id: int | None = None
    rotation_policy_id: int | None = None
    reason: str = Field(min_length=1, max_length=500)
    revision: RevisionToken = None
    collection_revision: RevisionToken = None


PRIORITY_MODES = {"strict", "prefer"}
EXCEPTION_KINDS = {"fixed_assignment", "rotation_force", "rotation_exclusion"}


def _shift_template_display_name(template: ShiftTemplate | None) -> str | None:
    if template is None:
        return None
    return template.display_name or template.name


def _position_display_name(position: Position | None) -> str | None:
    if position is None:
        return None
    return position.display_name or position.name


def _skill_payload(skill: Skill) -> dict:
    return {"id": skill.id, "name": skill.name, "revision": skill.revision}


def _staff_skills_payload(staff: Staff, rows: list[StaffSkill]) -> dict:
    return {
        "staff_id": staff.id,
        "skill_ids": [row.skill_id for row in rows],
        "revision": staff.skills_revision,
    }


def _require_revision(expected: object | None, *, code: str) -> int:
    return require_revision(expected, code)


def _revision_conflict(code: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": f"{code}_conflict", "message": "別の画面で更新されています。変更は保存していません。再読み込みしてからやり直してください。"},
    )


def _begin_skills_exclusive_transaction(session: Session) -> None:
    begin_editor_write(session)


def _commit_skill_name_change(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail="同じ名前のスキルが既にあります") from error


def _after_failed_cas(session: Session, model, row_id: int):
    session.rollback()
    session.expire_all()
    return session.get(model, row_id)


def _allow_sqlite_lock_conflict_or_reraise(session: Session, error: OperationalError) -> None:
    sqlite_lock_codes = {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
        getattr(sqlite3, "SQLITE_BUSY_SNAPSHOT", None),
    }
    if (
        session.get_bind().dialect.name == "sqlite"
        and getattr(error.orig, "sqlite_errorcode", None) in sqlite_lock_codes
    ):
        return
    session.rollback()
    raise error


def _lock_skill_for_reference(session: Session, skill_id: int) -> None:
    try:
        result = session.execute(
            update(Skill).where(Skill.id == skill_id).values(revision=Skill.revision)
        )
    except OperationalError as error:
        _allow_sqlite_lock_conflict_or_reraise(session, error)
        skill = _after_failed_cas(session, Skill, skill_id)
        if skill is None:
            raise HTTPException(status_code=422, detail="必要スキルが見つかりません")
        raise _revision_conflict("skill_reference")
    if result.rowcount != 1:
        raise HTTPException(status_code=422, detail="必要スキルが見つかりません")


def _lock_existing_skill(session: Session, skill_id: int) -> None:
    try:
        result = session.execute(
            update(Skill).where(Skill.id == skill_id).values(revision=Skill.revision)
        )
    except OperationalError as error:
        _allow_sqlite_lock_conflict_or_reraise(session, error)
        skill = _after_failed_cas(session, Skill, skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail="スキルが見つかりません")
        raise _revision_conflict("skill_revision")
    if result.rowcount != 1:
        raise HTTPException(status_code=404, detail="スキルが見つかりません")


def _lock_skills_for_assignment(session: Session, skill_ids: list[int], label: str) -> None:
    unique_ids = sorted(set(skill_ids))
    for skill_id in unique_ids:
        try:
            result = session.execute(
                update(Skill).where(Skill.id == skill_id).values(revision=Skill.revision)
            )
        except OperationalError as error:
            _allow_sqlite_lock_conflict_or_reraise(session, error)
            skill = _after_failed_cas(session, Skill, skill_id)
            if skill is None:
                raise HTTPException(status_code=422, detail=f"{label} が見つかりません: [{skill_id}]")
            raise _revision_conflict("staff_skills")
        if result.rowcount != 1:
            session.rollback()
            raise HTTPException(status_code=422, detail=f"{label} が見つかりません: [{skill_id}]")


def _claim_skill_revision(session: Session, skill_id: int, revision: object | None) -> None:
    _lock_existing_skill(session, skill_id)
    try:
        expected = _require_revision(revision, code="skill_revision")
    except HTTPException:
        session.rollback()
        raise
    try:
        result = session.execute(
            update(Skill)
            .where(Skill.id == skill_id, Skill.revision == expected)
            .values(revision=Skill.revision)
        )
    except OperationalError as error:
        _allow_sqlite_lock_conflict_or_reraise(session, error)
        skill = _after_failed_cas(session, Skill, skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail="スキルが見つかりません")
        raise _revision_conflict("skill_revision")
    if result.rowcount == 1:
        return
    skill = _after_failed_cas(session, Skill, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="スキルが見つかりません")
    raise _revision_conflict("skill_revision")


def _claim_staff_skills_revision(session: Session, staff_id: int, revision: object | None) -> None:
    try:
        present = session.execute(
            update(Staff).where(Staff.id == staff_id).values(skills_revision=Staff.skills_revision)
        )
    except OperationalError as error:
        _allow_sqlite_lock_conflict_or_reraise(session, error)
        staff = _after_failed_cas(session, Staff, staff_id)
        if staff is None:
            raise HTTPException(status_code=404, detail="スタッフが見つかりません")
        raise _revision_conflict("staff_skills_revision")
    if present.rowcount != 1:
        raise HTTPException(status_code=404, detail="スタッフが見つかりません")
    try:
        expected = _require_revision(revision, code="staff_skills_revision")
    except HTTPException:
        session.rollback()
        raise
    try:
        result = session.execute(
            update(Staff)
            .where(Staff.id == staff_id, Staff.skills_revision == expected)
            .values(skills_revision=Staff.skills_revision + 1)
        )
    except OperationalError as error:
        _allow_sqlite_lock_conflict_or_reraise(session, error)
        staff = _after_failed_cas(session, Staff, staff_id)
        if staff is None:
            raise HTTPException(status_code=404, detail="スタッフが見つかりません")
        raise _revision_conflict("staff_skills_revision")
    if result.rowcount == 1:
        return
    staff = _after_failed_cas(session, Staff, staff_id)
    if staff is None:
        raise HTTPException(status_code=404, detail="スタッフが見つかりません")
    raise _revision_conflict("staff_skills_revision")


def _shift_template_payload(template: ShiftTemplate) -> dict:
    return {
        "id": template.id,
        "name": template.name,
        "display_name": template.display_name,
        "shift_type": template.shift_type,
        "start_time": template.start_time.isoformat(timespec="minutes"),
        "end_time": template.end_time.isoformat(timespec="minutes"),
        "break_minutes": template.break_minutes,
        "revision": template.revision,
    }


def _staff_using_half_day_template(session: Session, template_id: int) -> bool:
    row = session.query(RuleConfig).filter(RuleConfig.name == "active").one_or_none()
    mapping = (row.config_json or {}).get("staff_half_day_shift_template_ids", {}) if row else {}
    return template_id in {int(value) for value in mapping.values()}


def _position_payload(position: Position) -> dict:
    return {
        "id": position.id,
        "name": position.name,
        "display_name": position.display_name,
        "required_skill_id": position.required_skill_id,
        "required_skill_name": position.required_skill.name if position.required_skill else None,
        "required_staff_ids": position.required_staff_ids or [],
        "difficulty_weight": position.difficulty_weight,
        "revision": position.revision,
    }


def _demand_payload(demand: Demand) -> dict:
    return {
        "id": demand.id,
        "work_date": demand.work_date.isoformat(),
        "shift_template_id": demand.shift_template_id,
        "shift_template_name": _shift_template_display_name(demand.shift_template),
        "position_id": demand.position_id,
        "position_name": _position_display_name(demand.position),
        "required_count": demand.required_count,
        "min_count": demand.min_count,
        "max_count": demand.max_count,
        "ideal_count": demand.ideal_count,
        "revision": demand.revision,
    }


def _workday_payload(rule: WorkdayRule) -> dict:
    return {
        "id": rule.id,
        "work_date": rule.work_date.isoformat(),
        "kind": rule.kind,
        "shift_template_id": rule.shift_template_id,
        "shift_template_name": _shift_template_display_name(rule.shift_template),
        "full_day_shift_template_id": rule.full_day_shift_template_id,
        "full_day_shift_template_name": (
            _shift_template_display_name(rule.full_day_shift_template)
        ),
        "full_day_staff_ids": rule.full_day_staff_ids or [],
        "min_staff_count": rule.min_staff_count,
        "max_staff_count": rule.max_staff_count,
        "group_min_counts": rule.group_min_counts or {},
        "group_max_counts": rule.group_max_counts or {},
        "notes": rule.notes,
        "revision": rule.revision,
    }


def _priority_payload(priority: PositionPriority) -> dict:
    return {
        "id": priority.id,
        "position_id": priority.position_id,
        "staff_id": priority.staff_id,
        "staff_name": priority.staff.name,
        "rank": priority.rank,
        "mode": priority.mode,
    }


def _staff_position_rule_payload(rule: StaffPositionRule) -> dict:
    return {
        "id": rule.id,
        "staff_id": rule.staff_id,
        "staff_name": rule.staff.name,
        "position_id": rule.position_id,
        "position_name": _position_display_name(rule.position),
        "kind": rule.kind,
        "reason": rule.reason,
    }


def _rotation_error(code: str, message: str, status_code: int = 409) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _rotation_payload(
    policy: RotationPolicy, session: Session, as_of: date | None = None,
    *, staff_by_id: dict[int, str] | None = None,
) -> dict:
    if staff_by_id is None:
        staff_by_id = {staff.id: staff.name for staff in session.query(Staff).all()}
    as_of = as_of or date.today()
    try:
        state, queue = resolve_rotation_queue(session, policy, as_of)
    except ValueError as error:
        raise _rotation_error("rotation_conflict", str(error)) from error
    return {
        "id": policy.id,
        "name": policy.name,
        "position_id": policy.position_id,
        "position_name": _position_display_name(policy.position),
        "member_staff_ids": policy.member_staff_ids,
        "member_names": [staff_by_id.get(staff_id, f"#{staff_id}") for staff_id in policy.member_staff_ids],
        "enabled": policy.enabled,
        "current_queue_staff_ids": queue,
        "current_queue_names": [
            staff_by_id.get(staff_id, f"#{staff_id}")
            for staff_id in queue
        ],
        "queue_effective_from": state.effective_from.isoformat() if state else None,
        "queue_as_of": as_of.isoformat(),
        "revision": policy.revision,
    }


def _exception_payload(exception: ScheduleException) -> dict:
    return {
        "id": exception.id,
        "work_date": exception.work_date.isoformat(),
        "kind": exception.kind,
        "staff_id": exception.staff_id,
        "staff_name": exception.staff.name if exception.staff else None,
        "position_id": exception.position_id,
        "position_name": _position_display_name(exception.position),
        "shift_template_id": exception.shift_template_id,
        "shift_template_name": _shift_template_display_name(exception.shift_template),
        "rotation_policy_id": exception.rotation_policy_id,
        "rotation_policy_name": exception.rotation_policy.name if exception.rotation_policy else None,
        "reason": exception.reason,
        "revision": exception.revision,
    }


def _staff_pair_rule_payload(rule: StaffPairRule) -> dict:
    return {
        "id": rule.id,
        "first_staff_id": rule.first_staff_id,
        "first_staff_name": rule.first_staff.name,
        "second_staff_id": rule.second_staff_id,
        "second_staff_name": rule.second_staff.name,
        "kind": rule.kind,
        "strength": rule.strength,
        "weight": rule.weight,
        "reason": rule.reason,
        "revision": rule.revision,
    }


def _ensure_ids_exist(session: Session, model, ids: list[int], label: str) -> None:
    existing = {row[0] for row in session.query(model.id).filter(model.id.in_(ids)).all()} if ids else set()
    missing = sorted(set(ids) - existing)
    if missing:
        raise HTTPException(status_code=422, detail=f"{label} が見つかりません: {missing}")


@router.get("/skills", response_model=list[SkillResponse])
def list_skills(session: Session = Depends(get_session)) -> list[dict]:
    return [_skill_payload(skill) for skill in session.query(Skill).order_by(Skill.name).all()]


@router.post("/skills", status_code=201, response_model=SkillResponse)
def create_skill(payload: SkillInput, session: Session = Depends(get_session)) -> dict:
    _begin_skills_exclusive_transaction(session)
    if session.query(Skill).filter(Skill.name == payload.name).first():
        raise HTTPException(status_code=409, detail="同じ名前のスキルが既にあります")
    skill = Skill(name=payload.name)
    session.add(skill)
    _commit_skill_name_change(session)
    session.refresh(skill)
    return _skill_payload(skill)


@router.put("/skills/{skill_id}", response_model=SkillResponse)
def update_skill(skill_id: int, payload: SkillUpdateInput, session: Session = Depends(get_session)) -> dict:
    _begin_skills_exclusive_transaction(session)
    _claim_skill_revision(session, skill_id, payload.revision)
    duplicate = session.query(Skill).filter(Skill.name == payload.name, Skill.id != skill_id).first()
    if duplicate:
        session.rollback()
        raise HTTPException(status_code=409, detail="同じ名前のスキルが既にあります")
    session.execute(
        update(Skill)
        .where(Skill.id == skill_id)
        .values(name=payload.name, revision=Skill.revision + 1)
    )
    _commit_skill_name_change(session)
    skill = session.get(Skill, skill_id)
    assert skill is not None
    session.refresh(skill)
    return _skill_payload(skill)


@router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: int, revision: int | None = None, session: Session = Depends(get_session)) -> None:
    _begin_skills_exclusive_transaction(session)
    _claim_skill_revision(session, skill_id, revision)
    if session.query(StaffSkill).filter(StaffSkill.skill_id == skill_id).first():
        session.rollback()
        raise HTTPException(status_code=409, detail="スタッフに設定されているスキルは削除できません")
    if session.query(Position).filter(Position.required_skill_id == skill_id).first():
        session.rollback()
        raise HTTPException(status_code=409, detail="持ち場に設定されているスキルは削除できません")
    skill = session.get(Skill, skill_id)
    assert skill is not None
    session.delete(skill)
    session.commit()


@router.get("/staff/{staff_id}/skills", response_model=StaffSkillsResponse)
def list_staff_skills(staff_id: int, session: Session = Depends(get_session)) -> dict:
    rows = (
        session.query(Staff.id, Staff.skills_revision, StaffSkill.skill_id)
        .outerjoin(StaffSkill, StaffSkill.staff_id == Staff.id)
        .filter(Staff.id == staff_id)
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="スタッフが見つかりません")
    return {
        "staff_id": rows[0].id,
        "skill_ids": [row.skill_id for row in rows if row.skill_id is not None],
        "revision": rows[0].skills_revision,
    }


@router.put("/staff/{staff_id}/skills", response_model=StaffSkillsResponse)
def save_staff_skills(staff_id: int, payload: StaffSkillsInput, session: Session = Depends(get_session)) -> dict:
    _begin_skills_exclusive_transaction(session)
    _claim_staff_skills_revision(session, staff_id, payload.revision)
    _lock_skills_for_assignment(session, payload.skill_ids, "スキル")
    session.query(StaffSkill).filter(StaffSkill.staff_id == staff_id).delete()
    session.add_all([StaffSkill(staff_id=staff_id, skill_id=skill_id) for skill_id in sorted(set(payload.skill_ids))])
    session.commit()
    staff = session.get(Staff, staff_id)
    assert staff is not None
    rows = session.query(StaffSkill).filter(StaffSkill.staff_id == staff_id).all()
    return _staff_skills_payload(staff, rows)


@router.get("/shift-templates", responses={200: {"model": PlanningCollectionResponse[ShiftTemplateResponse]}})
def list_shift_templates(session: Session = Depends(get_session)) -> dict:
    return read_collection(session, "shift_templates", lambda: [_shift_template_payload(row) for row in session.query(ShiftTemplate).order_by(ShiftTemplate.name).all()])


@router.post("/shift-templates", status_code=201, responses={201: {"model": PlanningCommandResponse[ShiftTemplateResponse]}})
def create_shift_template(payload: ShiftTemplateInput, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "shift_templates", payload.collection_revision)
    if session.query(ShiftTemplate).filter(ShiftTemplate.name == payload.name).first():
        raise HTTPException(status_code=409, detail="同じ名前の勤務時間が既にあります")
    template = ShiftTemplate(
        name=payload.name,
        display_name=payload.display_name,
        shift_type=payload.shift_type,
        start_time=payload.start_time,
        end_time=payload.end_time,
        break_minutes=payload.break_minutes,
    )
    session.add(template)
    return finalize_command(session, "shift_templates", lambda: _shift_template_payload(template))


@router.put("/shift-templates/{template_id}", responses={200: {"model": PlanningCommandResponse[ShiftTemplateResponse]}})
def update_shift_template(template_id: int, payload: ShiftTemplateInput, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "shift_templates", payload.collection_revision)
    claim_item(session, ShiftTemplate, template_id, payload.revision, "shift_template_revision")
    template = session.get(ShiftTemplate, template_id)
    assert template is not None
    if session.query(Demand).filter(Demand.shift_template_id == template_id).first():
        raise HTTPException(status_code=409, detail="必要人数に使われている勤務時間は変更できません")
    if session.query(WorkdayRule).filter(WorkdayRule.shift_template_id == template_id).first():
        raise HTTPException(status_code=409, detail="日ごとの基本勤務に使われている勤務時間は変更できません")
    if session.query(CandidateStaffStatus).filter(CandidateStaffStatus.shift_template_id == template_id).first():
        raise HTTPException(status_code=409, detail="作成済みシフトの勤務状態に使われている勤務時間は変更できません")
    if payload.shift_type != "half_day" and _staff_using_half_day_template(session, template_id):
        raise HTTPException(status_code=409, detail="人員の半日勤務設定に使われている勤務時間は通常勤務へ変更できません")
    duplicate = (
        session.query(ShiftTemplate)
        .filter(ShiftTemplate.name == payload.name, ShiftTemplate.id != template_id)
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="同じ名前の勤務時間が既にあります")
    for field, value in payload.model_dump(exclude={"revision", "collection_revision"}).items():
        setattr(template, field, value)
    return finalize_command(session, "shift_templates", lambda: _shift_template_payload(template))


@router.delete("/shift-templates/{template_id}", responses={200: {"model": PlanningDeleteResponse}})
def delete_shift_template(template_id: int, revision: int | None = None, collection_revision: int | None = None, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "shift_templates", collection_revision)
    claim_item(session, ShiftTemplate, template_id, revision, "shift_template_revision")
    template = session.get(ShiftTemplate, template_id)
    assert template is not None
    if _staff_using_half_day_template(session, template_id):
        raise HTTPException(status_code=409, detail="人員の半日勤務設定に使われている勤務時間は削除できません")
    references = [
        (session.query(Demand).filter(Demand.shift_template_id == template_id), "必要人数"),
        (session.query(Assignment).filter(Assignment.shift_template_id == template_id), "作成済みシフト"),
        (
            session.query(ReferenceAssignment).filter(ReferenceAssignment.shift_template_id == template_id),
            "確認用の過去シフト",
        ),
        (session.query(ScheduleException).filter(ScheduleException.shift_template_id == template_id), "特別な予定"),
        (session.query(PositionAssignmentRule).filter(PositionAssignmentRule.shift_template_id == template_id), "期間付き担当設定"),
        (session.query(WorkdayRule).filter(WorkdayRule.shift_template_id == template_id), "日ごとの基本勤務"),
        (
            session.query(WorkdayRule).filter(WorkdayRule.full_day_shift_template_id == template_id),
            "日ごとの全日勤務",
        ),
        (
            session.query(Availability).filter(Availability.shift_template_id == template_id),
            "休み・勤務希望",
        ),
        (
            session.query(CandidateStaffStatus).filter(CandidateStaffStatus.shift_template_id == template_id),
            "作成済みシフトの勤務状態",
        ),
    ]
    for query, label in references:
        if query.first():
            raise HTTPException(status_code=409, detail=f"{label}に使われている勤務時間は削除できません")
    session.delete(template)
    return finalize_command(session, "shift_templates", lambda: {"id": template_id, "revision": revision + 1, "deleted": True})


@router.get("/positions", responses={200: {"model": PlanningCollectionResponse[PositionResponse]}})
def list_positions(session: Session = Depends(get_session)) -> dict:
    return read_collection(session, "positions", lambda: [_position_payload(row) for row in session.query(Position).order_by(Position.name).all()])


@router.post("/positions", status_code=201, responses={201: {"model": PlanningCommandResponse[PositionResponse]}})
def create_position(payload: PositionInput, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "positions", payload.collection_revision)
    if session.query(Position).filter(Position.name == payload.name).first():
        raise HTTPException(status_code=409, detail="同じ名前の持ち場が既にあります")
    if payload.required_skill_id is not None:
        _lock_skill_for_reference(session, payload.required_skill_id)
    if len(set(payload.required_staff_ids)) != len(payload.required_staff_ids):
        raise HTTPException(status_code=422, detail="必須担当スタッフを重複して指定できません")
    _ensure_ids_exist(session, Staff, payload.required_staff_ids, "必須担当スタッフ")
    position = Position(**payload.model_dump(exclude={"revision", "collection_revision"}))
    session.add(position)
    return finalize_command(session, "positions", lambda: _position_payload(position))


@router.put("/positions/{position_id}", responses={200: {"model": PlanningCommandResponse[PositionResponse]}})
def update_position(position_id: int, payload: PositionInput, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "positions", payload.collection_revision)
    claim_item(session, Position, position_id, payload.revision, "position_revision")
    position = session.get(Position, position_id)
    assert position is not None
    if payload.required_skill_id is not None:
        _lock_skill_for_reference(session, payload.required_skill_id)
    if len(set(payload.required_staff_ids)) != len(payload.required_staff_ids):
        raise HTTPException(status_code=422, detail="必須担当スタッフを重複して指定できません")
    _ensure_ids_exist(session, Staff, payload.required_staff_ids, "必須担当スタッフ")
    duplicate = session.query(Position).filter(Position.name == payload.name, Position.id != position_id).first()
    if duplicate:
        raise HTTPException(status_code=409, detail="同じ名前の持ち場が既にあります")
    for field, value in payload.model_dump(exclude={"revision", "collection_revision"}).items():
        setattr(position, field, value)
    return finalize_command(session, "positions", lambda: _position_payload(position))


@router.delete("/positions/{position_id}", responses={200: {"model": PlanningDeleteResponse}})
def delete_position(position_id: int, revision: int | None = None, collection_revision: int | None = None, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "positions", collection_revision)
    claim_item(session, Position, position_id, revision, "position_revision")
    position = session.get(Position, position_id)
    assert position is not None
    active = session.query(RuleConfig).filter(RuleConfig.name == "active").one_or_none()
    config = active.config_json if active else {}
    if any(row["position_id"] == position_id for key in ("position_staffing_groups", "position_day_category_prohibitions") for row in (config or {}).get(key, [])):
        raise HTTPException(status_code=409, detail="作り方に設定されている持ち場は、設定を外してから削除してください")
    references = [
        (session.query(Demand).filter(Demand.position_id == position_id), "必要人数"),
        (session.query(Assignment).filter(Assignment.position_id == position_id), "作成済みシフト"),
        (
            session.query(ReferenceAssignment).filter(ReferenceAssignment.position_id == position_id),
            "確認用の過去シフト",
        ),
        (session.query(PositionPriority).filter(PositionPriority.position_id == position_id), "持ち場の優先順"),
        (
            session.query(StaffPositionPreference).filter(StaffPositionPreference.position_id == position_id),
            "個別の担当希望",
        ),
        (
            session.query(StaffPositionRule).filter(StaffPositionRule.position_id == position_id),
            "持ち場の許可・禁止条件",
        ),
        (session.query(RotationPolicy).filter(RotationPolicy.position_id == position_id), "交代順"),
        (session.query(ScheduleException).filter(ScheduleException.position_id == position_id), "特別な予定"),
        (session.query(PositionAssignmentRule).filter(PositionAssignmentRule.position_id == position_id), "期間付き担当設定"),
    ]
    for query, label in references:
        if query.first():
            raise HTTPException(status_code=409, detail=f"{label}に使われている持ち場は削除できません")
    session.delete(position)
    return finalize_command(session, "positions", lambda: {"id": position_id, "revision": revision + 1, "deleted": True})


WORKDAY_KINDS = {"normal", "half_day", "closed"}


@router.get("/workdays", responses={200: {"model": PlanningCollectionResponse[WorkdayResponse]}})
def list_workdays(
    start: date | None = None,
    end: date | None = None,
    session: Session = Depends(get_session),
) -> dict:
    query = session.query(WorkdayRule)
    if start:
        query = query.filter(WorkdayRule.work_date >= start)
    if end:
        query = query.filter(WorkdayRule.work_date < end)
    return read_collection(session, "workdays", lambda: [_workday_payload(row) for row in query.order_by(WorkdayRule.work_date, WorkdayRule.id).all()])


@router.put("/workdays", responses={200: {"model": PlanningCommandResponse[WorkdayResponse]}})
def save_workday(payload: WorkdayInput, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "workdays", payload.collection_revision)
    if payload.kind not in WORKDAY_KINDS:
        raise HTTPException(status_code=422, detail="日ごとの基本勤務の種類が正しくありません")
    if payload.kind == "closed":
        shift_template_id = None
        full_day_shift_template_id = None
    else:
        if payload.shift_template_id is None:
            raise HTTPException(status_code=422, detail="通常勤務または半日勤務には勤務時間が必要です")
        if session.get(ShiftTemplate, payload.shift_template_id) is None:
            raise HTTPException(status_code=422, detail="勤務時間が見つかりません")
        shift_template_id = payload.shift_template_id
        if payload.full_day_shift_template_id is not None:
            if session.get(ShiftTemplate, payload.full_day_shift_template_id) is None:
                raise HTTPException(status_code=422, detail="全日勤務の勤務時間が見つかりません")
            full_day_shift_template_id = payload.full_day_shift_template_id
        else:
            full_day_shift_template_id = None
    if (
        payload.min_staff_count is not None
        and payload.max_staff_count is not None
        and payload.min_staff_count > payload.max_staff_count
    ):
        raise HTTPException(status_code=422, detail="必要人数の最小値は最大値以下にしてください")
    _ensure_ids_exist(session, Staff, payload.full_day_staff_ids, "スタッフ")
    row = session.query(WorkdayRule).filter(WorkdayRule.work_date == payload.work_date).one_or_none()
    if row is None:
        row = WorkdayRule(work_date=payload.work_date)
        session.add(row)
    else:
        claim_item(session, WorkdayRule, row.id, payload.revision, "workday_revision")
    row.kind = payload.kind
    row.shift_template_id = shift_template_id
    row.full_day_shift_template_id = full_day_shift_template_id
    row.full_day_staff_ids = payload.full_day_staff_ids
    row.min_staff_count = payload.min_staff_count
    row.max_staff_count = payload.max_staff_count
    row.group_min_counts = payload.group_min_counts
    row.group_max_counts = payload.group_max_counts
    row.notes = payload.notes
    return finalize_command(session, "workdays", lambda: _workday_payload(row))


@router.delete("/workdays/{workday_id}", responses={200: {"model": PlanningDeleteResponse}})
def delete_workday(workday_id: int, revision: int | None = None, collection_revision: int | None = None, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "workdays", collection_revision)
    claim_item(session, WorkdayRule, workday_id, revision, "workday_revision")
    row = session.get(WorkdayRule, workday_id)
    assert row is not None
    session.delete(row)
    return finalize_command(session, "workdays", lambda: {"id": workday_id, "revision": revision + 1, "deleted": True})


@router.get("/demands", responses={200: {"model": PlanningCollectionResponse[DemandResponse]}})
def list_demands(start: date | None = None, end: date | None = None, session: Session = Depends(get_session)) -> dict:
    query = session.query(Demand)
    if start:
        query = query.filter(Demand.work_date >= start)
    if end:
        query = query.filter(Demand.work_date < end)
    return read_collection(session, "demands", lambda: [_demand_payload(row) for row in query.order_by(Demand.work_date, Demand.id).all()])


@router.put("/demands", responses={200: {"model": PlanningCommandResponse[DemandResponse]}})
def save_demand(payload: DemandInput, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "demands", payload.collection_revision)
    if session.get(Position, payload.position_id) is None or session.get(ShiftTemplate, payload.shift_template_id) is None:
        raise HTTPException(status_code=422, detail="持ち場または勤務時間が見つかりません")
    minimum = payload.min_count if payload.min_count is not None else payload.required_count
    maximum = payload.max_count if payload.max_count is not None else payload.required_count
    ideal = payload.ideal_count if payload.ideal_count is not None else payload.required_count
    if minimum > ideal or ideal > maximum:
        raise HTTPException(status_code=422, detail="必要人数の最小値・目安・最大値の順序が正しくありません")
    config = session.query(RuleConfig).filter(RuleConfig.name == "active").one_or_none()
    settings = build_active_settings(config.config_json if config else {})
    if minimum > 0 and is_position_prohibited(payload.position_id, payload.work_date, settings):
        raise HTTPException(status_code=422, detail="この日は、保存した作り方で配置しない持ち場に指定されています。必要人数または作り方を見直してください")
    row = (
        session.query(Demand)
        .filter(Demand.work_date == payload.work_date)
        .filter(Demand.position_id == payload.position_id)
        .filter(Demand.shift_template_id == payload.shift_template_id)
        .one_or_none()
    )
    if row is None:
        row = Demand(**payload.model_dump(exclude={"revision", "collection_revision"}))
        session.add(row)
    else:
        claim_item(session, Demand, row.id, payload.revision, "demand_revision")
        for field, value in payload.model_dump(exclude={"revision", "collection_revision"}).items():
            setattr(row, field, value)
    return finalize_command(session, "demands", lambda: _demand_payload(row))


@router.delete("/demands/{demand_id}", responses={200: {"model": PlanningDeleteResponse}})
def delete_demand(demand_id: int, revision: int | None = None, collection_revision: int | None = None, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "demands", collection_revision)
    claim_item(session, Demand, demand_id, revision, "demand_revision")
    row = session.get(Demand, demand_id)
    assert row is not None
    session.delete(row)
    return finalize_command(session, "demands", lambda: {"id": demand_id, "revision": revision + 1, "deleted": True})


def _position_detail_payload(session: Session, position_id: int | None, items: list[dict]) -> dict:
    position = session.get(Position, position_id) if position_id is not None else None
    if position_id is not None and position is None:
        raise HTTPException(404, detail="持ち場が見つかりません")
    return {"items": items, "position": _position_payload(position) if position else None,
            "collection_revision": command_payload(session, "positions", {})["collection_revision"]}


def _read_position_detail(session: Session, position_id: int | None, items) -> dict:
    _, started = begin_read_snapshot(session, ("positions",))
    try:
        result = _position_detail_payload(session, position_id, items())
    except Exception:
        end_read_snapshot(session, started, failed=True)
        raise
    end_read_snapshot(session, started)
    return result


def _claim_position_detail(session: Session, position_id: int, revision: int | None, collection_revision: int | None) -> None:
    require_revision(revision, "position_revision")
    require_revision(collection_revision, "positions_collection_revision")
    begin_editor_write(session)
    try:
        claim_collection(session, "positions", collection_revision)
        claim_item(session, Position, position_id, revision, "position_revision")
    except Exception:
        session.rollback()
        raise


def _finish_position_detail(session: Session, position_id: int, items) -> dict:
    session.flush()
    result = _position_detail_payload(session, position_id, items())
    session.commit()
    return result


@router.get("/position-priorities", responses={200: {"model": PositionDetailResponse[PositionPriorityResponse]}})
def list_position_priorities(position_id: int | None = None, session: Session = Depends(get_session)) -> dict:
    query = session.query(PositionPriority)
    if position_id is not None:
        query = query.filter(PositionPriority.position_id == position_id)
    return _read_position_detail(session, position_id, lambda: [_priority_payload(row) for row in query.order_by(PositionPriority.position_id, PositionPriority.rank).all()])


@router.put("/positions/{position_id}/priorities", responses={200: {"model": PositionDetailResponse[PositionPriorityResponse]}})
def save_position_priorities(position_id: int, payload: PrioritiesInput, session: Session = Depends(get_session), revision: int | None = None, collection_revision: int | None = None) -> dict:
    _claim_position_detail(session, position_id, revision, collection_revision)
    try:
        if session.get(Position, position_id) is None:
            raise HTTPException(status_code=404, detail="持ち場が見つかりません")
        if any(row.mode not in PRIORITY_MODES for row in payload.priorities):
            raise HTTPException(status_code=422, detail="持ち場の優先順の扱いが正しくありません")
        ranks = sorted(row.rank for row in payload.priorities)
        if ranks != list(range(1, len(ranks) + 1)):
            raise HTTPException(status_code=422, detail="持ち場の優先順は1番から連続して指定してください")
        _ensure_ids_exist(session, Staff, [row.staff_id for row in payload.priorities], "スタッフ")
        if len({row.staff_id for row in payload.priorities}) != len(payload.priorities):
            raise HTTPException(status_code=422, detail="同じスタッフを複数回指定できません")
        session.query(PositionPriority).filter(PositionPriority.position_id == position_id).delete()
        rows = [
            PositionPriority(position_id=position_id, staff_id=item.staff_id, rank=item.rank, mode=item.mode)
            for item in payload.priorities
        ]
        session.add_all(rows)
        return _finish_position_detail(session, position_id, lambda: [_priority_payload(row) for row in sorted(rows, key=lambda row: row.rank)])
    except Exception:
        session.rollback()
        raise


@router.get("/position-rules", responses={200: {"model": PositionDetailResponse[StaffPositionRuleResponse]}})
def list_staff_position_rules(
    position_id: int | None = None,
    staff_id: int | None = None,
    session: Session = Depends(get_session),
) -> dict:
    query = session.query(StaffPositionRule)
    if position_id is not None:
        query = query.filter(StaffPositionRule.position_id == position_id)
    if staff_id is not None:
        query = query.filter(StaffPositionRule.staff_id == staff_id)
    return _read_position_detail(session, position_id, lambda: [
        _staff_position_rule_payload(row)
        for row in query.order_by(StaffPositionRule.position_id, StaffPositionRule.staff_id).all()
    ])


@router.get("/positions/{position_id}/preferences", responses={200: {"model": PositionDetailResponse[StaffPositionPreferenceResponse]}})
def list_position_preferences(position_id: int, session: Session = Depends(get_session)) -> dict:
    return _read_position_detail(session, position_id, lambda: [
        {"staff_id": row.staff_id, "penalty": row.penalty, "reason": row.reason}
        for row in session.query(StaffPositionPreference).filter(
            StaffPositionPreference.position_id == position_id
        ).order_by(StaffPositionPreference.staff_id).all()
    ])


@router.put("/positions/{position_id}/preferences", responses={200: {"model": PositionDetailResponse[StaffPositionPreferenceResponse]}})
def save_position_preferences(
    position_id: int, payload: list[StaffPositionPreferenceInput], session: Session = Depends(get_session), revision: int | None = None, collection_revision: int | None = None
) -> dict:
    _claim_position_detail(session, position_id, revision, collection_revision)
    try:
        if session.get(Position, position_id) is None:
            raise HTTPException(status_code=404, detail="持ち場が見つかりません")
        _ensure_ids_exist(session, Staff, [row.staff_id for row in payload], "スタッフ")
        if len({row.staff_id for row in payload}) != len(payload):
            raise HTTPException(status_code=422, detail="同じスタッフを複数回指定できません")
        session.query(StaffPositionPreference).filter(StaffPositionPreference.position_id == position_id).delete()
        session.add_all([StaffPositionPreference(position_id=position_id, **row.model_dump()) for row in payload])
        return _finish_position_detail(session, position_id, lambda: [
            {"staff_id": row.staff_id, "penalty": row.penalty, "reason": row.reason}
            for row in session.query(StaffPositionPreference).filter(StaffPositionPreference.position_id == position_id).order_by(StaffPositionPreference.staff_id).all()])
    except Exception:
        session.rollback()
        raise


@router.put("/positions/{position_id}/staff-rules", responses={200: {"model": PositionDetailResponse[StaffPositionRuleResponse]}})
def save_staff_position_rules(
    position_id: int,
    payload: list[StaffPositionRuleInput],
    session: Session = Depends(get_session),
    revision: int | None = None, collection_revision: int | None = None,
) -> dict:
    _claim_position_detail(session, position_id, revision, collection_revision)
    try:
        if session.get(Position, position_id) is None:
            raise HTTPException(status_code=404, detail="持ち場が見つかりません")
        if any(row.kind not in {"allow", "deny"} for row in payload):
            raise HTTPException(status_code=422, detail="持ち場の許可・禁止条件が正しくありません")
        _ensure_ids_exist(session, Staff, [row.staff_id for row in payload], "スタッフ")
        if len({row.staff_id for row in payload}) != len(payload):
            raise HTTPException(status_code=422, detail="同じスタッフを複数回指定できません")
        session.query(StaffPositionRule).filter(
            StaffPositionRule.position_id == position_id
        ).delete()
        rows = [
            StaffPositionRule(
                position_id=position_id,
                staff_id=item.staff_id,
                kind=item.kind,
                reason=item.reason,
            )
            for item in payload
        ]
        session.add_all(rows)
        return _finish_position_detail(session, position_id, lambda: [_staff_position_rule_payload(row) for row in rows])
    except Exception:
        session.rollback()
        raise


PAIR_RULE_KINDS = {
    "avoid_same_weekday_off",
    "avoid_same_day_off",
    "avoid_same_day_assignment",
    "require_same_day_work",
    "require_same_day_assignment",
}
PAIR_RULE_STRENGTHS = {"hard", "soft"}


def _normalized_pair_rule_values(payload: StaffPairRuleInput, session: Session) -> dict:
    if payload.first_staff_id == payload.second_staff_id:
        raise HTTPException(status_code=422, detail="異なる2人を選んでください")
    _ensure_ids_exist(session, Staff, [payload.first_staff_id, payload.second_staff_id], "スタッフ")
    if payload.kind not in PAIR_RULE_KINDS:
        raise HTTPException(status_code=422, detail="組み合わせ条件の内容が正しくありません")
    if payload.strength not in PAIR_RULE_STRENGTHS:
        raise HTTPException(status_code=422, detail="組み合わせ条件の扱いが正しくありません")
    first_staff_id, second_staff_id = sorted([payload.first_staff_id, payload.second_staff_id])
    return {
        "first_staff_id": first_staff_id,
        "second_staff_id": second_staff_id,
        "kind": payload.kind,
        "strength": payload.strength,
        "weight": payload.weight,
        "reason": payload.reason,
    }


@router.get("/pair-rules", responses={200: {"model": PlanningCollectionResponse[StaffPairRuleResponse]}})
def list_staff_pair_rules(session: Session = Depends(get_session)) -> dict:
    return read_collection(session, "pair_rules", lambda: [_staff_pair_rule_payload(row) for row in session.query(StaffPairRule).order_by(StaffPairRule.id).all()])


@router.post("/pair-rules", status_code=201, responses={201: {"model": PlanningCommandResponse[StaffPairRuleResponse]}})
def create_staff_pair_rule(payload: StaffPairRuleInput, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "pair_rules", payload.collection_revision)
    values = _normalized_pair_rule_values(payload, session)
    duplicate = (
        session.query(StaffPairRule)
        .filter(StaffPairRule.first_staff_id == values["first_staff_id"])
        .filter(StaffPairRule.second_staff_id == values["second_staff_id"])
        .filter(StaffPairRule.kind == values["kind"])
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="同じ組み合わせ条件が既にあります")
    row = StaffPairRule(**values)
    session.add(row)
    return finalize_command(session, "pair_rules", lambda: _staff_pair_rule_payload(row))


@router.put("/pair-rules/{pair_rule_id}", responses={200: {"model": PlanningCommandResponse[StaffPairRuleResponse]}})
def update_staff_pair_rule(pair_rule_id: int, payload: StaffPairRuleInput, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "pair_rules", payload.collection_revision)
    claim_item(session, StaffPairRule, pair_rule_id, payload.revision, "pair_rule_revision")
    row = session.get(StaffPairRule, pair_rule_id)
    assert row is not None
    values = _normalized_pair_rule_values(payload, session)
    duplicate = (
        session.query(StaffPairRule)
        .filter(StaffPairRule.first_staff_id == values["first_staff_id"])
        .filter(StaffPairRule.second_staff_id == values["second_staff_id"])
        .filter(StaffPairRule.kind == values["kind"])
        .filter(StaffPairRule.id != pair_rule_id)
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="同じ組み合わせ条件が既にあります")
    for field, value in values.items():
        setattr(row, field, value)
    return finalize_command(session, "pair_rules", lambda: _staff_pair_rule_payload(row))


@router.delete("/pair-rules/{pair_rule_id}", responses={200: {"model": PlanningDeleteResponse}})
def delete_staff_pair_rule(pair_rule_id: int, revision: int | None = None, collection_revision: int | None = None, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "pair_rules", collection_revision)
    claim_item(session, StaffPairRule, pair_rule_id, revision, "pair_rule_revision")
    row = session.get(StaffPairRule, pair_rule_id)
    assert row is not None
    session.delete(row)
    return finalize_command(session, "pair_rules", lambda: {"id": pair_rule_id, "revision": revision + 1, "deleted": True})


@router.get("/rotations", responses={200: {"model": PlanningCollectionResponse[RotationResponse]}})
def list_rotations(session: Session = Depends(get_session), as_of: date | None = None) -> dict:
    def items() -> list[dict]:
        rows = session.query(RotationPolicy).order_by(RotationPolicy.name).all()
        staff_by_id = {staff.id: staff.name for staff in session.query(Staff).all()} if rows else {}
        return [_rotation_payload(row, session, as_of, staff_by_id=staff_by_id) for row in rows]

    return read_collection(session, "rotations", items)


def _confirmed_rotation_end(session: Session, policy: RotationPolicy) -> date | None:
    ends = [
        row.effective_from
        for row in session.query(RotationQueueState).filter(
            RotationQueueState.rotation_policy_id == policy.id,
            RotationQueueState.source_solve_run_id.is_not(None),
        ).all()
    ]
    for run in session.query(SolveRun).join(ScheduleConfirmation).all():
        config = run.config_json or {}
        referenced = (
            str(policy.id) in config.get("rotation_state_snapshot", {})
            or str(policy.id) in config.get("rotation_queue_snapshot", {})
            or policy.id in config.get("input_snapshot", {}).get("rotation_policy_ids", [])
            or session.query(Assignment.id).join(CandidateSchedule).filter(
                CandidateSchedule.solve_run_id == run.id,
                Assignment.position_id == policy.position_id,
            ).first() is not None
        )
        if referenced:
            end = config.get("end_date") or config.get("end_date_exclusive")
            try:
                ends.append(date.fromisoformat(end))
            except (TypeError, ValueError) as error:
                raise _rotation_error("rotation_history_locked", "確定済み期間を確認できないため交代順を変更できません") from error
    return max(ends) if ends else None


def _save_operator_rotation(
    session: Session, policy: RotationPolicy, queue: list[int], effective_from: date | None,
    *, membership_change: bool,
) -> None:
    if not queue or len(queue) != len(set(queue)):
        raise _rotation_error("rotation_invalid_queue", "同じスタッフを複数回指定できません", 422)
    ids = {row[0] for row in session.query(Staff.id).filter(Staff.id.in_(queue)).all()}
    if ids != set(queue):
        raise _rotation_error("rotation_invalid_queue", "交代順のスタッフが見つかりません", 422)
    confirmed_end = _confirmed_rotation_end(session, policy)
    has_history = (
        confirmed_end is not None
        or session.query(RotationQueueState.id).filter(RotationQueueState.rotation_policy_id == policy.id).first() is not None
        or session.query(ScheduleException.id).filter(ScheduleException.rotation_policy_id == policy.id).first() is not None
    )
    if effective_from is None and has_history:
        raise _rotation_error("rotation_history_date_required", "履歴がある交代順の変更には適用日が必要です", 422)
    if effective_from is not None and confirmed_end is not None and effective_from < confirmed_end:
        raise _rotation_error("rotation_history_locked", "確定済み期間の交代順は変更できません")
    try:
        _, current_queue = resolve_rotation_queue(session, policy, effective_from or date.today())
    except ValueError as error:
        raise _rotation_error("rotation_conflict", str(error)) from error
    if not membership_change and set(current_queue) != set(queue):
        raise _rotation_error("rotation_invalid_queue", "適用日の交代順の全員を一度ずつ指定してください", 422)
    if effective_from is None:
        policy.member_staff_ids = list(queue)
        return
    operator_states = session.query(RotationQueueState).filter(
        RotationQueueState.rotation_policy_id == policy.id,
        RotationQueueState.effective_from == effective_from,
        RotationQueueState.source_solve_run_id.is_(None),
    ).all()
    if len(operator_states) > 1:
        raise _rotation_error("rotation_duplicate_date", "同じ適用日の交代順変更が複数あります")
    next_state = session.query(RotationQueueState).filter(
        RotationQueueState.rotation_policy_id == policy.id,
        RotationQueueState.effective_from > effective_from,
    ).order_by(RotationQueueState.effective_from).first()
    exceptions = session.query(ScheduleException).filter(
        ScheduleException.rotation_policy_id == policy.id,
        ScheduleException.work_date >= effective_from,
    )
    if next_state:
        exceptions = exceptions.filter(ScheduleException.work_date < next_state.effective_from)
    if any(row.staff_id not in queue for row in exceptions.all()):
        raise _rotation_error("rotation_conflict", "適用日以降の特別な予定に対象外のスタッフがいます")
    if operator_states:
        operator_states[0].queue_staff_ids = list(queue)
    else:
        session.add(RotationQueueState(
            rotation_policy_id=policy.id,
            effective_from=effective_from,
            queue_staff_ids=list(queue),
            source_solve_run_id=None,
        ))


@router.post("/rotations", status_code=201, responses={201: {"model": PlanningCommandResponse[RotationResponse]}})
def create_rotation(payload: RotationInput, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "rotations", payload.collection_revision)
    if session.query(RotationPolicy).filter(RotationPolicy.name == payload.name).first():
        raise HTTPException(status_code=409, detail="同じ名前の交代順が既にあります")
    if session.query(RotationPolicy).filter(RotationPolicy.position_id == payload.position_id).first():
        raise HTTPException(status_code=409, detail="この持ち場には既に交代順があります")
    if session.get(Position, payload.position_id) is None:
        raise HTTPException(status_code=422, detail="持ち場が見つかりません")
    if len(set(payload.member_staff_ids)) != len(payload.member_staff_ids):
        raise HTTPException(status_code=422, detail="同じスタッフを複数回指定できません")
    _ensure_ids_exist(session, Staff, payload.member_staff_ids, "スタッフ")
    policy = RotationPolicy(**payload.model_dump(exclude={"effective_from", "revision", "collection_revision"}))
    session.add(policy)
    if payload.effective_from is not None:
        session.flush()
        _save_operator_rotation(session, policy, payload.member_staff_ids, payload.effective_from, membership_change=True)
    return finalize_command(session, "rotations", lambda: _rotation_payload(policy, session, payload.effective_from))


@router.put("/rotations/{rotation_id}", responses={200: {"model": PlanningCommandResponse[RotationResponse]}})
def update_rotation(rotation_id: int, payload: RotationInput, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "rotations", payload.collection_revision)
    claim_item(session, RotationPolicy, rotation_id, payload.revision, "rotation_revision")
    policy = session.query(RotationPolicy).filter(RotationPolicy.id == rotation_id).populate_existing().with_for_update().one_or_none()
    if policy is None:
        raise HTTPException(status_code=404, detail="交代順が見つかりません")
    if policy.position_id != payload.position_id:
        raise HTTPException(status_code=422, detail="交代順の持ち場は変更できません")
    duplicate = session.query(RotationPolicy).filter(RotationPolicy.name == payload.name, RotationPolicy.id != rotation_id).first()
    if duplicate:
        raise HTTPException(status_code=409, detail="同じ名前の交代順が既にあります")
    try:
        _, current_queue = resolve_rotation_queue(session, policy, payload.effective_from or date.today())
    except ValueError as error:
        raise _rotation_error("rotation_conflict", str(error)) from error
    if payload.member_staff_ids != current_queue:
        _save_operator_rotation(session, policy, payload.member_staff_ids, payload.effective_from, membership_change=True)
    for field, value in payload.model_dump(exclude={"member_staff_ids", "effective_from", "revision", "collection_revision"}).items():
        setattr(policy, field, value)
    return finalize_command(session, "rotations", lambda: _rotation_payload(policy, session, payload.effective_from))


@router.delete("/rotations/{rotation_id}", responses={200: {"model": PlanningDeleteResponse}})
def delete_rotation(rotation_id: int, revision: int | None = None, collection_revision: int | None = None, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "rotations", collection_revision)
    claim_item(session, RotationPolicy, rotation_id, revision, "rotation_revision")
    policy = session.get(RotationPolicy, rotation_id)
    assert policy is not None
    if session.query(RotationQueueState).filter(RotationQueueState.rotation_policy_id == rotation_id).first():
        raise HTTPException(status_code=409, detail="確定済みの交代順は削除できません")
    if session.query(ScheduleException).filter(ScheduleException.rotation_policy_id == rotation_id).first():
        raise HTTPException(status_code=409, detail="特別な予定に使われている交代順は削除できません")
    session.delete(policy)
    return finalize_command(session, "rotations", lambda: {"id": rotation_id, "revision": revision + 1, "deleted": True})


@router.put("/rotations/{rotation_id}/queue", responses={200: {"model": PlanningCommandResponse[RotationResponse]}})
def save_rotation_queue(rotation_id: int, payload: RotationQueueInput, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "rotations", payload.collection_revision)
    claim_item(session, RotationPolicy, rotation_id, payload.revision, "rotation_revision")
    policy = session.query(RotationPolicy).filter(RotationPolicy.id == rotation_id).populate_existing().with_for_update().one_or_none()
    if policy is None:
        raise HTTPException(status_code=404, detail="交代順が見つかりません")
    _save_operator_rotation(session, policy, payload.queue_staff_ids, payload.effective_from, membership_change=False)
    return finalize_command(session, "rotations", lambda: _rotation_payload(policy, session, payload.effective_from))


@router.get("/exceptions", responses={200: {"model": PlanningCollectionResponse[ScheduleExceptionResponse]}})
def list_exceptions(start: date | None = None, end: date | None = None, session: Session = Depends(get_session)) -> dict:
    query = session.query(ScheduleException)
    if start:
        query = query.filter(ScheduleException.work_date >= start)
    if end:
        query = query.filter(ScheduleException.work_date < end)
    return read_collection(session, "exceptions", lambda: [_exception_payload(row) for row in query.order_by(ScheduleException.work_date, ScheduleException.id).all()])


def _validate_exception_references(payload: ScheduleExceptionInput, session: Session) -> None:
    if payload.kind not in EXCEPTION_KINDS:
        raise HTTPException(status_code=422, detail="特別な予定の種類が正しくありません")
    if payload.staff_id is not None and session.get(Staff, payload.staff_id) is None:
        raise HTTPException(status_code=422, detail="スタッフが見つかりません")
    if payload.position_id is not None and session.get(Position, payload.position_id) is None:
        raise HTTPException(status_code=422, detail="持ち場が見つかりません")
    if payload.shift_template_id is not None and session.get(ShiftTemplate, payload.shift_template_id) is None:
        raise HTTPException(status_code=422, detail="勤務時間が見つかりません")
    if payload.rotation_policy_id is not None and session.get(RotationPolicy, payload.rotation_policy_id) is None:
        raise HTTPException(status_code=422, detail="交代順が見つかりません")
    if payload.kind == "fixed_assignment" and (payload.staff_id is None or payload.position_id is None):
        raise HTTPException(status_code=422, detail="固定担当にはスタッフと持ち場が必要です")
    if payload.kind in {"rotation_force", "rotation_exclusion"} and (
        payload.staff_id is None or payload.rotation_policy_id is None
    ):
        raise HTTPException(status_code=422, detail="交代順の予定にはスタッフと交代順が必要です")
    if payload.kind in {"rotation_force", "rotation_exclusion"}:
        policy = session.query(RotationPolicy).filter(
            RotationPolicy.id == payload.rotation_policy_id,
        ).populate_existing().with_for_update().one()
        try:
            _, queue = resolve_rotation_queue(session, policy, payload.work_date)
        except ValueError as error:
            raise _rotation_error("rotation_conflict", str(error)) from error
        if payload.staff_id not in queue:
            raise _rotation_error("rotation_invalid_queue", "この日の交代順に対象のスタッフがいません", 422)


@router.post("/exceptions", status_code=201, responses={201: {"model": PlanningCommandResponse[ScheduleExceptionResponse]}})
def create_exception(payload: ScheduleExceptionInput, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "exceptions", payload.collection_revision)
    _validate_exception_references(payload, session)
    row = ScheduleException(**payload.model_dump(exclude={"revision", "collection_revision"}))
    session.add(row)
    return finalize_command(session, "exceptions", lambda: _exception_payload(row))


@router.put("/exceptions/{exception_id}", responses={200: {"model": PlanningCommandResponse[ScheduleExceptionResponse]}})
def update_exception(exception_id: int, payload: ScheduleExceptionInput, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "exceptions", payload.collection_revision)
    claim_item(session, ScheduleException, exception_id, payload.revision, "exception_revision")
    row = session.get(ScheduleException, exception_id)
    assert row is not None
    _validate_exception_references(payload, session)
    for field, value in payload.model_dump(exclude={"revision", "collection_revision"}).items():
        setattr(row, field, value)
    return finalize_command(session, "exceptions", lambda: _exception_payload(row))


@router.delete("/exceptions/{exception_id}", responses={200: {"model": PlanningDeleteResponse}})
def delete_exception(exception_id: int, revision: int | None = None, collection_revision: int | None = None, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "exceptions", collection_revision)
    claim_item(session, ScheduleException, exception_id, revision, "exception_revision")
    row = session.get(ScheduleException, exception_id)
    assert row is not None
    session.delete(row)
    return finalize_command(session, "exceptions", lambda: {"id": exception_id, "revision": revision + 1, "deleted": True})


def _position_assignment_rule_payload(row: PositionAssignmentRule) -> dict:
    return {"id": row.id, "revision": row.revision, "position_id": row.position_id,
            "position_name": _position_display_name(row.position), "staff_id": row.staff_id,
            "staff_name": row.staff.name, "shift_template_id": row.shift_template_id,
            "shift_template_name": _shift_template_display_name(row.shift_template),
            "effective_from": row.effective_from.isoformat(),
            "effective_to": row.effective_to.isoformat() if row.effective_to else None,
            "reason": row.reason}


def _validate_position_assignment_rule(payload, session, exclude_id=None):
    _ensure_ids_exist(session, Position, [payload.position_id], "持ち場")
    _ensure_ids_exist(session, Staff, [payload.staff_id], "スタッフ")
    if payload.shift_template_id is not None:
        _ensure_ids_exist(session, ShiftTemplate, [payload.shift_template_id], "勤務時間")
    if payload.effective_to is not None and payload.effective_to < payload.effective_from:
        raise HTTPException(422, detail="適用終了日は適用開始日以降にしてください")
    rows = session.query(PositionAssignmentRule).filter_by(position_id=payload.position_id).all()
    for row in rows:
        if row.id == exclude_id:
            continue
        same_shift = row.shift_template_id is None or payload.shift_template_id is None or row.shift_template_id == payload.shift_template_id
        if same_shift and row.effective_from <= (payload.effective_to or date.max) and payload.effective_from <= (row.effective_to or date.max):
            raise HTTPException(409, detail="同じ持ち場・勤務時間の適用期間が重なっています。前の設定に終了日を設定してください")


@router.get("/position-assignment-rules", responses={200: {"model": PlanningCollectionResponse[PositionAssignmentRuleResponse]}})
def list_position_assignment_rules(start: date | None = None, end: date | None = None, session: Session = Depends(get_session)) -> dict:
    query = session.query(PositionAssignmentRule)
    if start:
        query = query.filter(PositionAssignmentRule.effective_to.is_(None) | (PositionAssignmentRule.effective_to >= start))
    if end:
        query = query.filter(PositionAssignmentRule.effective_from < end)
    return read_collection(session, "position_assignment_rules", lambda: [_position_assignment_rule_payload(row) for row in query.order_by(PositionAssignmentRule.position_id, PositionAssignmentRule.effective_from, PositionAssignmentRule.id).all()])


@router.post("/position-assignment-rules", status_code=201, responses={201: {"model": PlanningCommandResponse[PositionAssignmentRuleResponse]}})
def create_position_assignment_rule(payload: PositionAssignmentRuleInput, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "position_assignment_rules", payload.collection_revision)
    _validate_position_assignment_rule(payload, session)
    row = PositionAssignmentRule(**payload.model_dump(exclude={"revision", "collection_revision"}))
    session.add(row)
    return finalize_command(session, "position_assignment_rules", lambda: _position_assignment_rule_payload(row))


@router.put("/position-assignment-rules/{rule_id}", responses={200: {"model": PlanningCommandResponse[PositionAssignmentRuleResponse]}})
def update_position_assignment_rule(rule_id: int, payload: PositionAssignmentRuleInput, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "position_assignment_rules", payload.collection_revision)
    claim_item(session, PositionAssignmentRule, rule_id, payload.revision, "position_assignment_rule_revision")
    row = session.get(PositionAssignmentRule, rule_id)
    assert row is not None
    _validate_position_assignment_rule(payload, session, rule_id)
    for key, value in payload.model_dump(exclude={"revision", "collection_revision"}).items():
        setattr(row, key, value)
    return finalize_command(session, "position_assignment_rules", lambda: _position_assignment_rule_payload(row))


@router.delete("/position-assignment-rules/{rule_id}", responses={200: {"model": PlanningDeleteResponse}})
def delete_position_assignment_rule(rule_id: int, revision: int | None = None, collection_revision: int | None = None, session: Session = Depends(get_session)) -> dict:
    begin_editor_write(session)
    claim_collection(session, "position_assignment_rules", collection_revision)
    claim_item(session, PositionAssignmentRule, rule_id, revision, "position_assignment_rule_revision")
    row = session.get(PositionAssignmentRule, rule_id)
    assert row is not None
    session.delete(row)
    return finalize_command(session, "position_assignment_rules", lambda: {"id": rule_id, "revision": revision + 1, "deleted": True})


class PlanningPeriodResponse(BaseModel):
    target_month: str
    start_date: date
    end_date_exclusive: date
    period_mode: str


@router.get("/period", response_model=PlanningPeriodResponse)
def read_planning_period(month: str, session: Session = Depends(get_session)):
    """Use the same configured period resolver as solve; no calendar inference."""
    active = session.query(RuleConfig).filter(RuleConfig.name == "active").one_or_none()
    try:
        resolved = resolve_period_request(target_month=month, start_date=None, end_date_exclusive=None,
            settings_config=build_active_settings(active.config_json if active else {}))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"target_month":resolved.target_month,"start_date":resolved.start_date,
            "end_date_exclusive":resolved.end_date_exclusive,"period_mode":resolved.period_mode}


@router.get("/overview", responses={200: {"model": PlanningOverviewResponse}})
def planning_overview(
    month: str | None = None,
    start_date: date | None = None,
    end_date_exclusive: date | None = None,
    session: Session = Depends(get_session),
) -> dict:
    active_config = (
        session.query(RuleConfig).filter(RuleConfig.name == "active").one_or_none()
    )
    if month or start_date or end_date_exclusive:
        try:
            resolved = resolve_period_request(
                target_month=month,
                start_date=start_date,
                end_date_exclusive=end_date_exclusive,
                settings_config=build_active_settings(active_config.config_json if active_config else {}),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        start = resolved.start_date
        end = resolved.end_date_exclusive
    else:
        start = None
        end = None
    return {
        "skills": [_skill_payload(row) for row in session.query(Skill).order_by(Skill.name).all()],
        "shift_templates": [_shift_template_payload(row) for row in session.query(ShiftTemplate).order_by(ShiftTemplate.name).all()],
        "positions": [_position_payload(row) for row in session.query(Position).order_by(Position.name).all()],
        "workdays": list_workdays(start, end, session)["items"],
        "demands": list_demands(start, end, session)["items"],
        "priorities": list_position_priorities(session=session)["items"],
        "position_rules": list_staff_position_rules(session=session)["items"],
        "pair_rules": list_staff_pair_rules(session)["items"],
        "rotations": list_rotations(session, as_of=start)["items"],
        "exceptions": list_exceptions(start, end, session)["items"],
        "position_assignment_rules": list_position_assignment_rules(start, end, session)["items"],
    }


def _previous_month_text(target_month: str) -> str:
    year, month = (int(value) for value in target_month.split("-"))
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def _period_copy_version(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _period_copy_workday_values(rows: list[WorkdayRule]) -> list[dict]:
    return [{
        "id": row.id, "revision": row.revision, "work_date": row.work_date.isoformat(),
        "kind": row.kind, "shift_template_id": row.shift_template_id,
        "full_day_shift_template_id": row.full_day_shift_template_id,
        "full_day_staff_ids": row.full_day_staff_ids,
        "min_staff_count": row.min_staff_count, "max_staff_count": row.max_staff_count,
        "group_min_counts": row.group_min_counts, "group_max_counts": row.group_max_counts,
        "notes": row.notes,
    } for row in sorted(rows, key=lambda row: row.id)]


def _period_copy_demand_values(rows: list[Demand]) -> list[dict]:
    return [{
        "id": row.id, "revision": row.revision, "work_date": row.work_date.isoformat(),
        "shift_template_id": row.shift_template_id, "position_id": row.position_id,
        "required_count": row.required_count, "min_count": row.min_count,
        "max_count": row.max_count, "ideal_count": row.ideal_count,
    } for row in sorted(rows, key=lambda row: row.id)]


def _period_copy_versions(
    session: Session,
    config: RuleConfig,
    source_workdays: list[WorkdayRule],
    target_workdays: list[WorkdayRule],
    source_demands: list[Demand],
    target_demands: list[Demand],
    collection_revisions: dict[str, int] | None = None,
) -> dict:
    if collection_revisions is None:
        workdays_token = session.get(EditorRevision, "workdays")
        demands_token = session.get(EditorRevision, "demands")
        if workdays_token is None or demands_token is None:
            raise HTTPException(status_code=422, detail={"code": "period_copy_revision_source_missing", "message": "期間コピーの正本版がありません。"})
        collection_revisions = {
            "workdays": technical_revision(workdays_token.revision, "workdays_collection_revision"),
            "demands": technical_revision(demands_token.revision, "demands_collection_revision"),
        }
    return {
        "source_workdays_revision": _period_copy_version(_period_copy_workday_values(source_workdays)),
        "target_workdays_revision": _period_copy_version(_period_copy_workday_values(target_workdays)),
        "source_demands_revision": _period_copy_version(_period_copy_demand_values(source_demands)),
        "target_demands_revision": _period_copy_version(_period_copy_demand_values(target_demands)),
        "settings_revision": _period_copy_version(config.config_json),
        "workdays_collection_revision": collection_revisions["workdays"],
        "demands_collection_revision": collection_revisions["demands"],
    }


@router.post("/period-copy", responses={200: {"model": PeriodCopyResponse[PeriodCopyPreviewVersions]}})
def copy_previous_period(
    payload: PeriodCopyInput,
    session: Session = Depends(get_session),
) -> dict:
    """Preview/apply recurring rules without shifting their weekday meaning."""
    if payload.conflict_mode not in {"error", "skip", "replace"}:
        raise HTTPException(status_code=422, detail="競合時の扱いは error、skip、replace のいずれかを指定してください")
    applying = not payload.dry_run
    if applying and payload.preview_versions is None:
        raise HTTPException(status_code=428, detail={"code": "period_copy_preview_versions_required", "message": "プレビューを再取得してから適用してください。"})
    preview_snapshot_started_here = False
    preview_collection_revisions: dict[str, int] | None = None
    supplied_collection_revisions: dict[str, int] | None = None
    if applying:
        supplied_collection_revisions = {
            "workdays": payload.preview_versions.workdays_collection_revision,
            "demands": payload.preview_versions.demands_collection_revision,
        }
        preview_collection_revisions = begin_multi_resource_write_snapshot(
            session,
            supplied_collection_revisions,
            {
                resource
                for resource, changed in (
                    ("workdays", payload.include_workdays),
                    ("demands", payload.include_demands),
                )
                if changed
            },
        )
    else:
        preview_collection_revisions, preview_snapshot_started_here = begin_read_snapshot(session, ("workdays", "demands"))
    config_query = session.query(RuleConfig).filter(RuleConfig.name == "active")
    if applying:
        config_query = config_query.with_for_update()
    else:
        config_query = config_query.with_for_update(read=True)
    config = config_query.one_or_none()
    if config is None:
        if applying:
            session.rollback()
        else:
            end_read_snapshot(session, preview_snapshot_started_here, failed=True)
        raise HTTPException(status_code=422, detail={"code": "period_copy_settings_missing", "message": "期間解決に必要な設定がありません。"})
    settings = build_active_settings(config.config_json)
    try:
        target = resolve_period_request(
            target_month=payload.target_month,
            start_date=payload.start_date,
            end_date_exclusive=payload.end_date_exclusive,
            settings_config=settings,
        )
        if target.period_mode == "custom":
            duration = target.end_date_exclusive - target.start_date
            source_start = target.start_date - duration
            source_end = target.start_date
        else:
            source_month = _previous_month_text(target.target_month)
            source = resolve_period_request(
                target_month=source_month,
                start_date=None,
                end_date_exclusive=None,
                settings_config=settings,
            )
            source_start, source_end = source.start_date, source.end_date_exclusive
    except ValueError as error:
        if applying:
            session.rollback()
        else:
            end_read_snapshot(session, preview_snapshot_started_here, failed=True)
        raise HTTPException(status_code=422, detail=str(error)) from error

    target_days = (target.end_date_exclusive - target.start_date).days
    source_workdays = session.query(WorkdayRule).filter(
        WorkdayRule.work_date >= source_start, WorkdayRule.work_date < source_end
    ).all() if payload.include_workdays else []
    source_demands = session.query(Demand).filter(
        Demand.work_date >= source_start, Demand.work_date < source_end
    ).all() if payload.include_demands else []

    target_dates = [target.start_date + timedelta(days=offset) for offset in range(target_days)]
    if target.period_mode == "custom":
        source_date_by_target = {
            target_date: source_start + timedelta(days=offset)
            for offset, target_date in enumerate(target_dates)
        }
        mapping_mode = "relative_day"
    else:
        source_dates_by_weekday: dict[int, list[date]] = defaultdict(list)
        for offset in range((source_end - source_start).days):
            source_date = source_start + timedelta(days=offset)
            source_dates_by_weekday[source_date.weekday()].append(source_date)
        weekday_occurrences: dict[int, int] = defaultdict(int)
        source_date_by_target = {}
        for target_date in target_dates:
            weekday = target_date.weekday()
            source_dates = source_dates_by_weekday[weekday]
            occurrence = weekday_occurrences[weekday]
            source_date_by_target[target_date] = source_dates[occurrence % len(source_dates)]
            weekday_occurrences[weekday] += 1
        mapping_mode = "weekday_occurrence"

    target_dates_by_source: dict[date, list[date]] = defaultdict(list)
    for target_date, source_date in source_date_by_target.items():
        target_dates_by_source[source_date].append(target_date)

    def mapped_dates(source_date: date) -> list[date]:
        return target_dates_by_source.get(source_date, [])

    target_workday_rows = session.query(WorkdayRule).filter(
            WorkdayRule.work_date >= target.start_date,
            WorkdayRule.work_date < target.end_date_exclusive,
        ).all()
    target_demand_rows = session.query(Demand).filter(
            Demand.work_date >= target.start_date,
            Demand.work_date < target.end_date_exclusive,
        ).all()
    existing_workdays = {row.work_date for row in target_workday_rows}
    existing_demands = {(row.work_date, row.position_id, row.shift_template_id) for row in target_demand_rows}
    workday_plan = [(row, day) for row in source_workdays for day in mapped_dates(row.work_date)]
    mapped_demands = [(row, day) for row in source_demands for day in mapped_dates(row.work_date)]
    excluded_demands = [(row, day) for row, day in mapped_demands if is_position_prohibited(row.position_id, day, settings)]
    demand_plan = [(row, day) for row, day in mapped_demands if not is_position_prohibited(row.position_id, day, settings)]
    workday_conflicts = [day for _, day in workday_plan if day is not None and day in existing_workdays]
    demand_conflicts = [
        day for row, day in demand_plan
        if day is not None and (day, row.position_id, row.shift_template_id) in existing_demands
    ]
    conflict_count = len(workday_conflicts) + len(demand_conflicts)
    preview_versions = _period_copy_versions(
        session,
        config,
        source_workdays,
        target_workday_rows,
        source_demands,
        target_demand_rows,
        supplied_collection_revisions or preview_collection_revisions,
    )
    preview = {
        "source_start_date": source_start.isoformat(),
        "source_end_date_exclusive": source_end.isoformat(),
        "target_start_date": target.start_date.isoformat(),
        "target_end_date_exclusive": target.end_date_exclusive.isoformat(),
        "period_mode": target.period_mode,
        "mapping_mode": mapping_mode,
        "workday_source_count": len(source_workdays),
        "demand_source_count": len(source_demands),
        "prohibited_demand_exclusions": [
            {"source_demand_id": row.id, "work_date": day.isoformat(), "position_id": row.position_id}
            for row, day in excluded_demands
        ],
        "workday_copy_count": sum(
            payload.conflict_mode == "replace" or day not in existing_workdays
            for _, day in workday_plan
        ),
        "demand_copy_count": sum(
            payload.conflict_mode == "replace"
            or (day, row.position_id, row.shift_template_id) not in existing_demands
            for row, day in demand_plan
        ),
        "conflict_count": conflict_count,
        "unmapped_count": 0,
        "applied": False,
        "preview_versions": preview_versions,
    }
    if payload.dry_run:
        end_read_snapshot(session, preview_snapshot_started_here)
        return preview
    try:
        if payload.preview_versions.model_dump() != preview_versions:
            raise HTTPException(status_code=409, detail={"code": "period_copy_preview_versions_conflict", "message": "プレビュー後にコピー元、コピー先、または設定が更新されています。再確認してください。"})
        if conflict_count and payload.conflict_mode == "error":
            raise HTTPException(status_code=409, detail=f"コピー先に {conflict_count} 件の既存設定があります。上書きは行いません")

        if payload.conflict_mode == "replace":
            if payload.include_workdays:
                session.query(WorkdayRule).filter(WorkdayRule.work_date >= target.start_date, WorkdayRule.work_date < target.end_date_exclusive).delete(synchronize_session=False)
                existing_workdays.clear()
            if payload.include_demands:
                session.query(Demand).filter(Demand.work_date >= target.start_date, Demand.work_date < target.end_date_exclusive).delete(synchronize_session=False)
                existing_demands.clear()

        for source_row, day in workday_plan:
            if day in existing_workdays:
                continue
            session.add(WorkdayRule(work_date=day, kind=source_row.kind, shift_template_id=source_row.shift_template_id, full_day_shift_template_id=source_row.full_day_shift_template_id, full_day_staff_ids=list(source_row.full_day_staff_ids or []), min_staff_count=source_row.min_staff_count, max_staff_count=source_row.max_staff_count, group_min_counts=dict(source_row.group_min_counts or {}), group_max_counts=dict(source_row.group_max_counts or {}), notes=(f"前期間コピー: {source_row.work_date.isoformat()} / {mapping_mode}" + (f" / {source_row.notes}" if source_row.notes else ""))[:500]))
        for source_row, day in demand_plan:
            key = (day, source_row.position_id, source_row.shift_template_id) if day else None
            if key in existing_demands:
                continue
            session.add(Demand(work_date=day, position_id=source_row.position_id, shift_template_id=source_row.shift_template_id, required_count=source_row.required_count, min_count=source_row.min_count, max_count=source_row.max_count, ideal_count=source_row.ideal_count))
        session.flush()
        result_versions = _period_copy_versions(
            session,
            config,
            session.query(WorkdayRule).filter(WorkdayRule.work_date >= source_start, WorkdayRule.work_date < source_end).all() if payload.include_workdays else [],
            session.query(WorkdayRule).filter(WorkdayRule.work_date >= target.start_date, WorkdayRule.work_date < target.end_date_exclusive).all() if payload.include_workdays else [],
            session.query(Demand).filter(Demand.work_date >= source_start, Demand.work_date < source_end).all() if payload.include_demands else [],
            session.query(Demand).filter(Demand.work_date >= target.start_date, Demand.work_date < target.end_date_exclusive).all() if payload.include_demands else [],
        )
        session.commit()
        return {**preview, "applied": True, "result_versions": result_versions}
    except Exception:
        session.rollback()
        raise


@router.get("/pdf-references", responses={200: {"model": list[PdfReferenceSourceResponse]}})
def list_pdf_reference_sources(session: Session = Depends(get_session)) -> list[dict]:
    """List manually verified reference schedules available for comparison."""
    source_names = {
        row[0]
        for row in session.query(ReferenceStaffStatus.source_name).distinct().all()
    }
    source_names.update(
        row[0]
        for row in session.query(ReferenceAssignment.source_name).distinct().all()
    )
    sources: list[dict] = []
    for source_name in sorted(source_names):
        status_range = (
            session.query(
                func.min(ReferenceStaffStatus.work_date),
                func.max(ReferenceStaffStatus.work_date),
            )
            .filter(ReferenceStaffStatus.source_name == source_name)
            .one()
        )
        assignment_range = (
            session.query(
                func.min(ReferenceAssignment.work_date),
                func.max(ReferenceAssignment.work_date),
            )
            .filter(ReferenceAssignment.source_name == source_name)
            .one()
        )
        dates = [value for value in (*status_range, *assignment_range) if value is not None]
        if not dates:
            continue
        start_date = min(dates)
        end_date = max(dates)
        sources.append(
            {
                "source_name": source_name,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "end_date_exclusive": (end_date + timedelta(days=1)).isoformat(),
                "reference_status_count": (
                    session.query(ReferenceStaffStatus)
                    .filter(ReferenceStaffStatus.source_name == source_name)
                    .count()
                ),
                "reference_assignment_count": (
                    session.query(ReferenceAssignment)
                    .filter(ReferenceAssignment.source_name == source_name)
                    .count()
                ),
            }
        )
    return sources


@router.get("/pdf-comparisons", responses={200: {"model": PdfComparisonResponse}})
def compare_pdf_result(
    source_name: str,
    candidate_id: int,
    start_date: date,
    end_date: date,
    session: Session = Depends(get_session),
) -> dict:
    """Compare a candidate with persisted, manually checked PDF cells."""
    if end_date <= start_date:
        raise HTTPException(status_code=422, detail="比較期間の終了日は開始日より後にしてください")
    candidate = session.get(CandidateSchedule, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="比較対象の候補が見つかりません")
    if candidate.solve_run.status not in {"succeeded", "failed"}:
        raise HTTPException(status_code=409, detail="計算が完了していない候補は比較できません")
    staff_names = [row.name for row in session.query(Staff).order_by(Staff.id).all()]
    mismatches = compare_reference_cells(
        session=session,
        candidate_id=candidate_id,
        source_name=source_name,
        staff_names=staff_names,
        start_date=start_date,
        end_date=end_date,
    )
    cell_count = len(staff_names) * (end_date - start_date).days
    issue_count = sum(1 for row in mismatches if row.get("issue"))
    return {
        "source_name": source_name,
        "candidate_id": candidate_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "cell_count": cell_count,
        "checked_cell_count": max(0, cell_count - issue_count),
        "reference_status_count": (
            session.query(ReferenceStaffStatus)
            .filter(ReferenceStaffStatus.source_name == source_name)
            .filter(ReferenceStaffStatus.work_date >= start_date)
            .filter(ReferenceStaffStatus.work_date < end_date)
            .count()
        ),
        "reference_assignment_count": (
            session.query(ReferenceAssignment)
            .filter(ReferenceAssignment.source_name == source_name)
            .filter(ReferenceAssignment.work_date >= start_date)
            .filter(ReferenceAssignment.work_date < end_date)
            .count()
        ),
        "mismatch_count": len(mismatches),
        "unreadable_or_unresolved_count": issue_count,
        "match": not mismatches and cell_count > 0,
        "mismatches": mismatches,
    }


@router.post("/solve-runs/{solve_run_id}/confirm")
def confirm_solve_run(
    solve_run_id: int,
    session: Session = Depends(get_session),
    candidate_id: int | None = None,
    prior_for_start_date: date | None = None,
    precondition: dict | None = None,
) -> dict:
    from .candidate_command import execute_candidate_command
    return execute_candidate_command(
        session, precondition, candidate_id,
        lambda candidate: _confirm_solve_run(solve_run_id, session, candidate_id, prior_for_start_date, candidate.id),
        prior_for_start_date=prior_for_start_date,
        solve_run_id=solve_run_id,
    )


def _confirm_solve_run(solve_run_id, session, candidate_id, prior_for_start_date, selected_candidate_id):
    begin_confirmation_snapshot(session)
    solve_run = session.query(SolveRun).filter(SolveRun.id == solve_run_id).populate_existing().with_for_update().one_or_none()
    if solve_run is None:
        raise HTTPException(status_code=404, detail="シフト案が見つかりません")
    if solve_run.status != "succeeded":
        raise HTTPException(status_code=409, detail="作成に成功したシフト案だけ確定できます")
    if prior_for_start_date is not None:
        require_direct_predecessor(session, solve_run, prior_for_start_date)
    existing = session.query(ScheduleConfirmation).filter(ScheduleConfirmation.solve_run_id == solve_run_id).one_or_none()
    if existing:
        accepted = confirmed_candidate(session, solve_run)
        if accepted.id != selected_candidate_id or (candidate_id is not None and accepted.id != candidate_id):
            raise HTTPException(status_code=409, detail={"code": "confirmed_candidate_mismatch", "message": "別の候補が確定済みです。確定済みの候補を確認してください。"})
        from .validation_snapshot import confirmed_validation
        if confirmed_validation(session, accepted)["blocking"]:
            raise HTTPException(409, detail={"code": "confirmed_candidate_state_changed"})
        return {"solve_run_id": solve_run_id, "confirmed": True, "confirmed_at": existing.stored_timestamp(session)}
    candidates = (
        session.query(CandidateSchedule)
        .filter(CandidateSchedule.solve_run_id == solve_run_id)
        .order_by(CandidateSchedule.candidate_no)
        .all()
    )
    if candidate_id is None and len(candidates) > 1:
        raise HTTPException(status_code=409, detail=unresolved_candidate_detail(
            solve_run, candidates, "複数の候補から確定対象を選択してください。",
        ))
    candidate = next((row for row in candidates if row.id == selected_candidate_id), None)
    if candidate is None:
        raise HTTPException(status_code=409, detail="確定対象のシフト案がありません")
    generation_snapshot = (candidate.score_breakdown_json or {}).get("generation_time_snapshot")
    if (prior_for_start_date is not None
            and (not isinstance(generation_snapshot, dict)
                 or not isinstance(generation_snapshot.get("validation"), dict))):
        raise HTTPException(status_code=409, detail={
            "code": "legacy_prior_requires_selection",
            "message": "この過去候補は作成時の検証記録がないため確定できません。次期間の参照案として選択してください。",
        })
    policies = session.query(RotationPolicy).order_by(RotationPolicy.id).populate_existing().with_for_update().all()
    policies = [policy for policy in policies if policy.enabled]
    config = solve_run.config_json or {}
    try:
        start_date = date.fromisoformat(config["start_date"])
        end_date = date.fromisoformat(config["end_date"])
    except (KeyError, TypeError, ValueError) as error:
        raise _rotation_error("rotation_snapshot_stale", "作成対象期間を確認できません。作り直してから確定してください") from error
    errors = validate_rotation_snapshot(
        session, policies, start_date, end_date, config.get("rotation_queue_snapshot"),
    )
    if errors:
        raise _rotation_error("rotation_snapshot_stale", "\n".join(errors))
    next_queues = candidate.score_breakdown_json.get("next_rotation_queues", {})
    if not isinstance(next_queues, dict) or set(next_queues) != {str(policy.id) for policy in policies}:
        raise _rotation_error("rotation_snapshot_stale", "繰越対象の交代順が一致しません。作り直してから確定してください")
    pending_states = []
    for policy in policies:
        _, current_queue = resolve_rotation_queue(session, policy, start_date)
        queue = next_queues[str(policy.id)]
        if (
            not isinstance(queue, list)
            or any(type(staff_id) is not int for staff_id in queue)
            or len(queue) != len(current_queue)
            or set(queue) != set(current_queue)
        ):
            raise _rotation_error("rotation_invalid_queue", "繰越順の対象者が作成時の交代順と一致しません")
        confirmed_end = _confirmed_rotation_end(session, policy)
        if confirmed_end is not None and start_date < confirmed_end:
            raise _rotation_error("rotation_history_locked", "既に確定済みの期間と重なる交代順は確定できません")
        boundary = session.query(RotationQueueState).filter(
            RotationQueueState.rotation_policy_id == policy.id,
            RotationQueueState.effective_from == end_date,
        ).all()
        if boundary:
            try:
                state, _ = resolve_rotation_queue(session, policy, end_date)
            except ValueError as error:
                raise _rotation_error("rotation_conflict", str(error)) from error
            if state.source_solve_run_id is None:
                continue
            raise _rotation_error("rotation_conflict", "確定先の日付には既に繰越順があります")
        pending_states.append(RotationQueueState(
            rotation_policy_id=policy.id,
            effective_from=end_date,
            queue_staff_ids=list(queue),
            source_solve_run_id=solve_run_id,
        ))
    validation = validate_for_confirmation(
        session,
        candidate,
        start_date,
        end_date,
        prior_for_start_date=prior_for_start_date,
    )
    if validation["blocking"]:
        raise HTTPException(
            status_code=409,
            detail="勤務条件に未解決のエラーがあるため確定できません。\n" + "\n".join(
                warning["message"] for warning in validation["warnings"] if warning["severity"] == "error"
            ),
        )
    session.add_all(pending_states)
    solve_run.config_json = {**solve_run.config_json, "confirmed_candidate_id": candidate.id}
    confirmation = ScheduleConfirmation(solve_run_id=solve_run_id, confirmed_at=datetime.now(UTC))
    session.add(confirmation)
    session.flush()
    return {"solve_run_id": solve_run_id, "confirmed": True, "confirmed_at": confirmation.stored_timestamp(session)}


@router.post("/solve-runs/{solve_run_id}/select-prior")
def select_prior_candidate(
    solve_run_id: int,
    candidate_id: int,
    next_start_date: date,
    session: Session = Depends(get_session),
) -> dict:
    begin_confirmation_snapshot(session)
    run = session.query(SolveRun).filter_by(id=solve_run_id).populate_existing().with_for_update().one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="前期間のシフトが見つかりません")
    require_direct_predecessor(session, run, next_start_date)
    if run.status != "succeeded":
        raise HTTPException(status_code=409, detail="作成に成功した案だけ前期間として使用できます")
    from .schedule_selection import predecessor_runs
    candidate = session.query(CandidateSchedule).filter_by(id=candidate_id, solve_run_id=run.id).one_or_none()
    if candidate is None:
        raise HTTPException(status_code=409, detail="指定した候補が前期間にありません")
    accepted = confirmed_candidate(session, run)
    if accepted is not None and accepted.id != candidate.id:
        raise HTTPException(status_code=409, detail="別の候補が確定済みです。確定済みの候補を使用してください")
    validation = historical_candidate_validation(session, candidate)
    errors = prior_history_coverage_errors(session, candidate, next_start_date)
    if validation["blocking"] or errors:
        raise HTTPException(status_code=409, detail="前期間として使えません。" + "\n".join(
            [row["message"] for row in validation["warnings"] if row["severity"] == "error"] + errors
        ))
    for other in predecessor_runs(session, next_start_date):
        if other.id == run.id:
            continue
        other_config = dict(other.config_json or {})
        for key in ("prior_candidate_id", "prior_candidate_for_start_date", "prior_candidate_state_hash"):
            other_config.pop(key, None)
        other.config_json = other_config
    run.config_json = {**(run.config_json or {}), "prior_candidate_id": candidate.id,
                       "prior_candidate_for_start_date": next_start_date.isoformat(),
                       "prior_candidate_state_hash": prior_candidate_state_hash(session, candidate)}
    session.commit()
    return {"solve_run_id": run.id, "prior_candidate_id": candidate.id,
            "next_start_date": next_start_date.isoformat()}


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)
