"""Calculation job boundary.

The local implementation executes immediately, while the request/result shape
can be handed to Cloud Tasks, Pub/Sub, or a Cloud Run worker later without
putting queue concerns into the solver.
"""

from dataclasses import asdict, dataclass
from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
import json
from typing import Literal, TypedDict
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import Boolean, Date, DateTime, Integer, JSON, String, Time

from .periods import default_settings_config, resolve_period_request
from .validation_snapshot import begin_solver_input_snapshot
from . import models


class JobStatusProjection(TypedDict):
    domain: str
    jobId: int
    inputVersion: str | None
    inputReference: None
    attemptIdentity: str | None
    state: str
    sourceState: str
    failure: dict | None
    artifactReference: int
    executionContract: dict | None
    provenanceAvailability: str
    transportState: Literal["pending", "claimed", "stopping", "terminal"] | None
    businessState: str


def _execution_json(value):
    def encode(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError("shift_execution_request_not_json")
    return json.loads(json.dumps(value, default=encode, allow_nan=False))


def canonical_execution_json(value):
    def require_json(item):
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise TypeError("shift_execution_json_key_invalid")
            for child in item.values():
                require_json(child)
        elif type(item) is list:
            for child in item:
                require_json(child)
        elif item is not None and type(item) not in (str, bool, int, float):
            raise TypeError("shift_execution_json_value_invalid")
    require_json(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def execution_json_equal(left, right):
    return canonical_execution_json(left) == canonical_execution_json(right)


def create_execution_contract(request):
    return {"version": 1, "attemptIdentity": str(uuid4()),
        "request": _execution_json(request), "inputVersion": None,
        "inputAvailability": "not_captured"}


def bind_execution_input(run):
    contract = deepcopy(run.config_json["execution_contract"])
    if contract["inputAvailability"] != "not_captured":
        raise ValueError("shift_execution_input_already_bound")
    contract["inputVersion"] = recorded_input_version(run.config_json)
    contract["inputAvailability"] = "recorded" if contract["inputVersion"] is not None else "unavailable"
    run.config_json = {**run.config_json, "execution_contract": contract}


def record_execution_terminal(run):
    contract = run.config_json.get("execution_contract")
    if contract is None:
        return
    if contract["inputAvailability"] == "not_captured":
        bind_execution_input(run)
        contract = run.config_json["execution_contract"]
    run.config_json = {**run.config_json, "execution_terminal": {
        "executionContract": deepcopy(contract), "businessState": run.status,
        "solverStatus": run.solver_status,
        "finishedAt": run.finished_at.isoformat() if run.finished_at is not None else None}}


def _complete_prior_time_history(rows):
    seen = set()
    for row in rows:
        if not {"staff_id", "work_date", "status", "slots"}.issubset(row):
            return False
        staff_id = row["staff_id"]
        if not isinstance(staff_id, int) or isinstance(staff_id, bool) or staff_id <= 0:
            return False
        day = date.fromisoformat(row["work_date"])
        key = (staff_id, day)
        if key in seen:
            return False
        seen.add(key)
        if "source_candidate_id" in row:
            candidate_id = row["source_candidate_id"]
            if not isinstance(candidate_id, int) or isinstance(candidate_id, bool) or candidate_id <= 0:
                return False
        status, slots = row["status"], row["slots"]
        # Legacy confirmed history uses None for unknown time, not an empty off day.
        if status not in ("working", "off") or not isinstance(slots, list):
            return False
        if (status == "off" and slots) or (status == "working" and not slots):
            return False
        for slot in slots:
            if not isinstance(slot, dict):
                return False
            start, end, minutes = (slot.get(name) for name in ("start_minute", "end_minute", "work_minutes"))
            if (not all(isinstance(value, int) and not isinstance(value, bool) for value in (start, end, minutes))
                    or not 0 <= start < 1440 or not start < end <= start + 1440
                    or not 0 <= minutes <= end - start):
                return False
            if "shift_template_id" in slot:
                template_id = slot["shift_template_id"]
                if not isinstance(template_id, int) or isinstance(template_id, bool) or template_id <= 0:
                    return False
    return True


def _recorded_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _recorded_rows(rows, model):
    columns = model.__table__.columns
    if not isinstance(rows, list):
        return False
    identities = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(columns.keys()):
            return False
        for column in columns:
            value = row[column.key]
            if value is None:
                if not column.nullable:
                    return False
                continue
            if column.primary_key or column.foreign_keys:
                if not _recorded_id(value):
                    return False
            elif isinstance(column.type, Boolean):
                if not isinstance(value, bool):
                    return False
            elif isinstance(column.type, Integer):
                if not isinstance(value, int) or isinstance(value, bool):
                    return False
            elif isinstance(column.type, (Date, DateTime, Time)):
                if not isinstance(value, str):
                    return False
                parser = datetime if isinstance(column.type, DateTime) else time if isinstance(column.type, Time) else date
                parser.fromisoformat(value)
            elif isinstance(column.type, String):
                if not isinstance(value, str):
                    return False
            elif isinstance(column.type, JSON):
                # JSON containers follow the model's declared list/dict shape, not truthiness.
                annotation = str(model.__annotations__[column.key])
                shape = list if "list[" in annotation else dict
                if not isinstance(value, shape):
                    return False
                if shape is list and any(not _recorded_id(item) for item in value):
                    return False
                if model is models.WorkdayRule and column.key in ("group_min_counts", "group_max_counts"):
                    if any(not isinstance(key, str) or not isinstance(count, int) or isinstance(count, bool)
                           for key, count in value.items()):
                        return False
                if model is models.RuleConfig:
                    from .main import OptimizationSettingsInput
                    OptimizationSettingsInput.model_validate(value, strict=True)
        identity = tuple(row[column.key] for column in columns if column.primary_key)
        if identity in identities:
            return False
        identities.add(identity)
    return True


def _recorded_settings(settings):
    # Reuse the actual request schema without importing main during module initialization.
    from .main import OptimizationSettingsInput

    if not isinstance(settings, dict) or not set(default_settings_config()).issubset(settings):
        return False
    OptimizationSettingsInput.model_validate(settings, strict=True)
    if not set(default_settings_config()["holiday_counted_work_kinds"]).issubset(settings["holiday_counted_work_kinds"]):
        return False
    half_days = settings["staff_half_day_shift_template_ids"]
    if not isinstance(half_days, dict) or any(
            not (_recorded_id(key) or isinstance(key, str) and key.isdecimal() and int(key) > 0) or not _recorded_id(value)
            for key, value in half_days.items()):
        return False
    if not isinstance(settings["staff_display_order_ids"], list) or any(
            not _recorded_id(value) for value in settings["staff_display_order_ids"]):
        return False
    for key in ("member_staff_ids", "weekday_no_off_staff_ids", "consecutive_off_exempt_staff_ids",
                "auto_assignment_prohibited_position_ids"):
        if settings.get(key) is not None and (not isinstance(settings[key], list) or any(not _recorded_id(value) for value in settings[key])):
            return False
    for key in ("off_overlap_pairs", "weekend_balance_groups"):
        if settings.get(key) is not None:
            if not isinstance(settings[key], list) or any(not isinstance(group, list) or any(
                    not _recorded_id(value) for value in group) for group in settings[key]):
                return False
            if key == "off_overlap_pairs" and any(len(pair) != 2 or pair[0] == pair[1] for pair in settings[key]):
                return False
    if settings.get("weekday_position_assignments") is not None:
        if not isinstance(settings["weekday_position_assignments"], list) or any(
                not isinstance(row, dict) or not _recorded_id(row.get("staff_id")) or not _recorded_id(row.get("position_id"))
                for row in settings["weekday_position_assignments"]):
            return False
    if settings.get("profile_scoped_setting_keys") is not None and (not isinstance(settings["profile_scoped_setting_keys"], list)
            or any(not isinstance(key, str) for key in settings["profile_scoped_setting_keys"])):
        return False
    if "active_rule_profile" in settings:
        profile = settings["active_rule_profile"]
        if (not isinstance(profile, dict) or set(profile) != {"id", "name", "version"}
                or not _recorded_id(profile["id"]) or not isinstance(profile["name"], str)
                or type(profile["version"]) is not int or profile["version"] != 1):
            return False
        groups = settings.get("weekend_balance_groups")
        if not isinstance(groups, list) or len(groups) != 2 or any(not group or len(group) != len(set(group)) for group in groups):
            return False
        if set(groups[0]).intersection(groups[1]):
            return False
    return True


def recorded_input_version(config):
    source = config.get("generation_rule_source") if isinstance(config, dict) else None
    required = {"start_date", "end_date", "tables", "effective_settings", "locked_assignments",
        "prior_source", "prior_statuses", "prior_time_history", "reference_source", "rotation_queue_snapshot"}
    if not isinstance(source, dict) or not required.issubset(source):
        return None
    version = source.get("version", 1)
    if type(version) is not int or version not in (1, 2):
        return None
    try:
        if date.fromisoformat(source["start_date"]) >= date.fromisoformat(source["end_date"]):
            return None
        if not isinstance(source["tables"], dict) or not _recorded_settings(source["effective_settings"]):
            return None
        if version == 1 and models.PositionAssignmentRule.__tablename__ in source["tables"]:
            return None
        if version == 2 and not _recorded_id(source.get("position_assignment_rules_revision")):
            return None
        if version == 2 and not _recorded_rows(source["tables"].get(models.PositionAssignmentRule.__tablename__), models.PositionAssignmentRule):
            return None
        if not isinstance(source["rotation_queue_snapshot"], dict):
            return None
        if source["prior_source"] is not None and not isinstance(source["prior_source"], dict):
            return None
        if source["prior_source"] is not None and set(source["prior_source"]) != {
                "solve_run_id", "candidate_id", "history_start", "history_end_exclusive"}:
            return None
        if source["prior_source"] is not None:
            prior = source["prior_source"]
            if not _recorded_id(prior["solve_run_id"]) or not _recorded_id(prior["candidate_id"]):
                return None
            if not date.fromisoformat(prior["history_start"]) < date.fromisoformat(prior["history_end_exclusive"]):
                return None
            if prior["history_end_exclusive"] != source["start_date"]:
                return None
        if source["reference_source"] is not None and not isinstance(source["reference_source"], str):
            return None
        for key in ("locked_assignments", "prior_statuses", "prior_time_history"):
            if not isinstance(source[key], list) or any(not isinstance(row, dict) for row in source[key]):
                return None
        if not _recorded_rows(source["locked_assignments"], models.Assignment):
            return None
        if any(set(row) != {"staff_id", "work_date", "status"} for row in source["prior_statuses"]):
            return None
        prior_days = set()
        for row in source["prior_statuses"]:
            if not _recorded_id(row["staff_id"]) or row["status"] not in ("working", "off"):
                return None
            key = (row["staff_id"], date.fromisoformat(row["work_date"]))
            if key in prior_days:
                return None
            prior_days.add(key)
        if not _complete_prior_time_history(source["prior_time_history"]):
            return None
        for policy_id, snapshot in source["rotation_queue_snapshot"].items():
            if not isinstance(policy_id, str) or not policy_id.isdecimal() or int(policy_id) <= 0:
                return None
            if not isinstance(snapshot, dict) or set(snapshot) != {
                    "state_id", "queue_staff_ids", "position_id", "enabled", "boundary_states"}:
                return None
            if not isinstance(snapshot["queue_staff_ids"], list) or not isinstance(snapshot["boundary_states"], list):
                return None
            if (snapshot["state_id"] is not None and not _recorded_id(snapshot["state_id"])) or not _recorded_id(snapshot["position_id"]) or not isinstance(snapshot["enabled"], bool):
                return None
            queue = snapshot["queue_staff_ids"]
            if not queue or any(not _recorded_id(value) for value in queue) or len(queue) != len(set(queue)):
                return None
            if any(not isinstance(row, dict) or set(row) != {
                    "id", "effective_from", "queue_staff_ids", "source_solve_run_id"}
                    for row in snapshot["boundary_states"]):
                return None
            for row in snapshot["boundary_states"]:
                if not _recorded_id(row["id"]) or (row["source_solve_run_id"] is not None and not _recorded_id(row["source_solve_run_id"])):
                    return None
                date.fromisoformat(row["effective_from"])
                queue = row["queue_staff_ids"]
                if not isinstance(queue, list) or any(not _recorded_id(value) for value in queue) or len(queue) != len(set(queue)):
                    return None
        for model in (models.Staff, models.Skill, models.StaffSkill, models.Position,
                models.StaffPositionRule, models.StaffPositionPreference, models.ShiftTemplate,
                models.WorkdayRule, models.Demand, models.Availability, models.StaffPairRule,
                models.RuleConfig, models.PositionPriority, models.RotationPolicy,
                models.RotationQueueState, models.ScheduleException,
                models.ReferenceAssignment, models.ReferenceStaffStatus):
            rows = source["tables"].get(model.__tablename__)
            if not _recorded_rows(rows, model):
                return None
            if model in (models.ReferenceAssignment, models.ReferenceStaffStatus) and any(
                    not source["reference_source"] or row["source_name"] != source["reference_source"]
                    or row["confidence"] not in ("high", "medium") for row in rows):
                return None
        encoded = json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return None
    return sha256(encoded.encode("utf-8")).hexdigest()


def _delivery_terminal_time_matches(recorded, persisted):
    # Producers record aware UTC; DateTime(timezone=False) reloads that UTC as naive.
    if type(recorded) is not str or type(persisted) is not datetime:
        return False
    try:
        observed = datetime.fromisoformat(recorded)
    except ValueError:
        return False
    if observed.tzinfo is None or observed.utcoffset() != timedelta(0):
        return False
    actual = persisted.replace(tzinfo=UTC) if persisted.tzinfo is None else persisted.astimezone(UTC)
    return observed.astimezone(UTC) == actual


def serialize_job_status(session, run) -> JobStatusProjection:
    delivery = session.query(models.SolveDelivery).filter_by(solve_run_id=run.id).one_or_none()
    if delivery is not None:
        review = session.query(models.SolveDeliveryReview).filter_by(delivery_id=delivery.id).one_or_none()
        if review is not None:
            if review.code != "shift_recovery_requires_review":
                raise ValueError("shift_delivery_projection_review_invalid")
            trusted = False
            contract = run.config_json.get("execution_contract") if type(run.config_json) is dict else None
            input_version = recorded_input_version(run.config_json)
            from .solve_delivery_service import classify_delivery_review
            disposition = classify_delivery_review(session, delivery, run)
            trusted = disposition.capture_trusted
            return {"domain": "shift", "jobId": run.id, "inputVersion": input_version if trusted else None,
                "inputReference": None, "attemptIdentity": contract["attemptIdentity"] if trusted else None,
                "executionContract": deepcopy(contract) if trusted else None,
                "provenanceAvailability": "recorded" if trusted else "unavailable",
                "transportState": delivery.state if delivery.state in {"pending", "claimed", "stopping", "terminal"} else None,
                "businessState": run.status, "state": "blocked", "sourceState": run.status,
                "failure": {"code": review.code, "message": "Delivery requires operational review"},
                "artifactReference": run.id}
    contract = (run.config_json or {}).get("execution_contract")
    input_version = recorded_input_version(run.config_json)
    if delivery is not None and (type(contract) is not dict
            or not {"inputVersion", "attemptIdentity", "inputAvailability"}.issubset(contract)):
        raise ValueError("shift_delivery_projection_capture_mismatch")
    if contract is not None and contract["inputVersion"] != input_version:
        raise ValueError("shift_execution_recorded_input_mismatch")
    failure = None
    if run.status in {"failed", "error"}:
        reasons = session.query(models.Violation).filter_by(solve_run_id=run.id).order_by(models.Violation.id).all()
        messages = [row.message for row in reasons if row.severity in {"hard", "error"} and row.message]
        failure = {"code": run.solver_status, "message": "\n".join(messages) or None}
    transport_state = None
    if delivery is not None:
        captured = delivery.captured_config
        if (contract is None or input_version is None or type(captured) is not dict
                or not execution_json_equal(captured.get("execution_contract"), contract)
                or recorded_input_version(captured) != input_version
                or delivery.input_version != input_version
                or contract.get("inputAvailability") != "recorded"
                or type(delivery.generation) is not int or delivery.generation < 1
                or delivery.state not in {"pending", "claimed", "stopping", "terminal"}):
            raise ValueError("shift_delivery_projection_capture_mismatch")
        transport_state = delivery.state
        if delivery.state == "pending":
            if delivery.owner_attempt is not None or delivery.lease_until is not None or delivery.receipt is not None or run.status != "queued":
                raise ValueError("shift_delivery_projection_pending_mismatch")
        elif (type(delivery.owner_attempt) is not str or not delivery.owner_attempt
                or delivery.lease_until is None):
            raise ValueError("shift_delivery_projection_owner_missing")
        if delivery.state in {"claimed", "stopping"} and (delivery.receipt is not None or run.status != "queued"):
            raise ValueError("shift_delivery_projection_owned_mismatch")
        if delivery.state == "terminal":
            receipt = delivery.receipt
            terminal = run.config_json.get("execution_terminal")
            expected = {"run_id": run.id, "generation": delivery.generation,
                "owner_attempt": delivery.owner_attempt, "input_version": input_version,
                "business_state": run.status}
            if (type(receipt) is not dict or set(receipt) != set(expected) | {"failure"}
                    or any(not execution_json_equal(receipt[key], value) for key, value in expected.items())
                    or run.status not in {"succeeded", "failed", "error"}
                    or run.finished_at is None
                    or type(terminal) is not dict
                    or not execution_json_equal(terminal.get("executionContract"), contract)
                    or terminal.get("businessState") != run.status
                    or terminal.get("solverStatus") != run.solver_status
                    or not _delivery_terminal_time_matches(terminal.get("finishedAt"), run.finished_at)):
                raise ValueError("shift_delivery_projection_terminal_mismatch")
            recorded_failure = receipt["failure"]
            if recorded_failure is not None:
                if (run.status not in {"failed", "error"} or type(recorded_failure) is not dict
                        or set(recorded_failure) != {"code", "reason"}
                        or any(type(value) is not str or not value for value in recorded_failure.values())):
                    raise ValueError("shift_delivery_projection_failure_invalid")
                failure = {"code": recorded_failure["code"], "message": recorded_failure["reason"]}
    if delivery is not None and delivery.state == "pending":
        warning = session.query(models.Violation).filter_by(solve_run_id=run.id,
            rule_name="shift_task_dispatch_pending").order_by(models.Violation.id).first()
        if warning is not None:
            failure = {"code": warning.rule_name, "message": warning.message}
    return {"domain": "shift", "jobId": run.id, "inputVersion": input_version,
        "inputReference": None, "attemptIdentity": contract["attemptIdentity"] if contract is not None else None,
        "executionContract": deepcopy(contract),
        "provenanceAvailability": "recorded" if contract is not None else "legacy_unavailable",
        "transportState": transport_state, "businessState": run.status,
        "state": "failed" if run.status == "error" else run.status, "sourceState": run.status,
        "failure": failure, "artifactReference": run.id}


@dataclass(frozen=True)
class SolveJobRequest:
    target_month: str | None
    start_date: date | None
    end_date_exclusive: date | None
    strategy: str
    locked_assignment_ids: tuple[int, ...]
    candidate_count: int
    prior_statuses: tuple[tuple[int, date, str], ...] = ()
    prior_time_history: tuple[dict, ...] = ()
    effective_settings: dict | None = None
    original_request: dict | None = None


@dataclass(frozen=True)
class SolveJobResult:
    solve_run_id: int
    status: str
    solver_status: str | None


def enqueue_solve_job(session: Session, request: SolveJobRequest, *, transport_contract: dict):
    """Explicit internal opt-in; existing API execution remains synchronous."""
    from .solve_delivery_service import enqueue_solve
    return enqueue_solve(session, request, transport_contract=transport_contract)


def execute_solve_job(session: Session, request: SolveJobRequest) -> SolveJobResult:
    """Execute one calculation request synchronously for the local backend."""
    from .solver import solve_period

    if request.effective_settings is None:
        raise HTTPException(
            status_code=409,
            detail="計算用の有効設定が明示されていません。設定を再読み込みして計算をやり直してください",
        )
    begin_solver_input_snapshot(session)
    effective_settings = request.effective_settings
    settings_row = resolve_period_request(
        target_month=request.target_month,
        start_date=request.start_date,
        end_date_exclusive=request.end_date_exclusive,
        settings_config=effective_settings,
    )
    solve_run = solve_period(
        session=session,
        label=settings_row.target_month,
        start_date=settings_row.start_date,
        end_date=settings_row.end_date_exclusive,
        strategy=request.strategy,
        locked_assignment_ids=list(request.locked_assignment_ids),
        candidate_count=request.candidate_count,
        period_mode=settings_row.period_mode,
        prior_statuses=list(request.prior_statuses),
        prior_time_history=list(request.prior_time_history),
        rule_config_override=effective_settings,
        execution_request={"producer": "execute_solve_job", "job_request": asdict(request)},
    )
    return SolveJobResult(
        solve_run_id=solve_run.id,
        status=solve_run.status,
        solver_status=solve_run.solver_status,
    )
