"""Read/command snapshot contract for operator-selected candidate contents."""

from contextlib import contextmanager
from datetime import date
from hashlib import sha256
import json
import re

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, ValidationError
from sqlalchemy import update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from . import models
from .validation_snapshot import candidate_state, capture_rule_source, _row_values


class CandidatePrecondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: StrictInt | None = None
    candidate_revision: StrictStr | None = None
    input_revision: StrictStr | None = None


def require_precondition(value):
    if value is None:
        raise HTTPException(428, detail={"code": "candidate_precondition_required"})
    try:
        result = CandidatePrecondition.model_validate(value)
    except ValidationError as error:
        raise HTTPException(422, detail={"code": "candidate_precondition_invalid"}) from error
    if any(field is None for field in (result.candidate_id, result.candidate_revision, result.input_revision)):
        raise HTTPException(428, detail={"code": "candidate_precondition_required"})
    if result.candidate_id <= 0 or any(
        re.fullmatch(r"[0-9a-f]{64}", token) is None
        for token in (result.candidate_revision, result.input_revision)
    ):
        raise HTTPException(422, detail={"code": "candidate_precondition_invalid"})
    return result


@contextmanager
def candidate_snapshot(session, *, writing=False):
    if session.new or session.dirty or session.deleted:
        raise HTTPException(409, detail={"code": "candidate_pending_changes"})
    # Empty dirty/new sets do not prove ownership: the caller may have flushed.
    if session.in_transaction():
        raise HTTPException(409, detail={"code": "candidate_transaction_owned_by_caller"})
    bind = session.get_bind()
    if isinstance(bind, Connection) and bind.in_transaction():
        raise HTTPException(409, detail={"code": "candidate_transaction_owned_by_caller"})
    session.begin()
    dialect = session.get_bind().dialect.name
    try:
        connection = session.connection()
        if dialect == "sqlite":
            driver = connection.connection.driver_connection
            if driver.in_transaction:
                raise HTTPException(409, detail={"code": "candidate_transaction_owned_by_caller"})
            connection.exec_driver_sql("BEGIN IMMEDIATE" if writing else "BEGIN")
        elif dialect == "postgresql":
            try:
                connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            except SQLAlchemyError as error:
                raise HTTPException(409, detail={"code": "candidate_snapshot_unavailable"}) from error
        else:
            raise HTTPException(409, detail={"code": "candidate_snapshot_unsupported"})
        session.expire_all()
        session.info["confirmation_snapshot_transaction"] = session.get_transaction()
        yield
        session.commit()
    except OperationalError as error:
        session.rollback()
        if (dialect == "sqlite" and (
            (getattr(error.orig, "sqlite_errorcode", 0) or 0) & 255
        ) in (5, 6)) or (dialect == "postgresql" and (getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)) in ("40001", "40P01", "55P03")):
            raise HTTPException(409, detail={"code": "candidate_snapshot_conflict"}) from error
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.info.pop("confirmation_snapshot_transaction", None)


def _hash(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()


def _rotation_command_source(session, start, end):
    from .planning_api import _confirmed_rotation_end
    policies = session.query(models.RotationPolicy).filter_by(enabled=True).order_by(models.RotationPolicy.id).all()
    states = []
    for policy in policies:
        rows = session.query(models.RotationQueueState).filter(
            models.RotationQueueState.rotation_policy_id == policy.id,
            models.RotationQueueState.effective_from <= end,
        ).order_by(models.RotationQueueState.effective_from, models.RotationQueueState.id).all()
        prior_days = [row.effective_from for row in rows if row.effective_from <= start]
        boundary = max(prior_days) if prior_days else None
        states.extend(row for row in rows if row.effective_from > start or row.effective_from == boundary)
    member_ids = {member for policy in policies for member in policy.member_staff_ids}
    member_ids.update(member for row in states for member in row.queue_staff_ids)
    return {
        "policies": [_row_values(row) for row in policies],
        "states": [_row_values(row) for row in states],
        "existing_member_ids": [row[0] for row in session.query(models.Staff.id).filter(
            models.Staff.id.in_(member_ids)
        ).order_by(models.Staff.id).all()],
        # Bind the existing confirmation history decision, not every history
        # row or an unrelated global epoch.
        "confirmed_ends": {str(policy.id): _confirmed_rotation_end(session, policy) for policy in policies},
    }


def candidate_precondition(session, candidate, *, prior_for_start_date=None):
    run = candidate.solve_run
    config = {key: value for key, value in (run.config_json or {}).items() if key != "confirmed_candidate_id"}
    state = candidate_state(session, candidate)
    assignments = session.query(models.Assignment).filter_by(candidate_id=candidate.id).order_by(models.Assignment.id).all()
    score = candidate.score_breakdown_json or {}
    content = {"id": candidate.id, "run_status": run.status, "state": state,
               "strategy": candidate.strategy, "total_score": candidate.total_score,
               "assignment_ids": [row.id for row in assignments], "score": score,
               "explanations": [_row_values(row) for row in session.query(models.AssignmentExplanation).filter(
                   models.AssignmentExplanation.assignment_id.in_([row.id for row in assignments])
               ).order_by(models.AssignmentExplanation.id).all()]}
    status_reasons = session.query(models.CandidateStaffStatusExplanation).join(
        models.CandidateStaffStatus, models.CandidateStaffStatus.id == models.CandidateStaffStatusExplanation.status_id,
    ).filter(models.CandidateStaffStatus.candidate_id == candidate.id).order_by(models.CandidateStaffStatusExplanation.id).all()
    if status_reasons:
        content["status_explanations"] = [_row_values(row) for row in status_reasons]
    confirmed = session.query(models.ScheduleConfirmation).filter_by(solve_run_id=run.id).first()
    generation = score.get("generation_time_snapshot")
    if confirmed is None and prior_for_start_date is not None and generation is not None and not isinstance(generation, dict):
        raise HTTPException(409, detail={"code": "candidate_rule_source_unresolved"})
    if confirmed is not None:
        from .schedule_selection import confirmed_candidate
        accepted_candidate = confirmed_candidate(session, run)
        accepted = (accepted_candidate.score_breakdown_json or {}).get("confirmed_validation_snapshot") if accepted_candidate else None
        source = accepted.get("rule_source") if isinstance(accepted, dict) else None
    elif prior_for_start_date is not None and (
        (generation or {}).get("candidate_state") == state
        or generation is None
    ):
        # Existing historical validation reads the generation source, not all
        # current global history or today's staffing settings.
        source = (generation or {}).get("rule_source")
    else:
        try:
            # Use the existing validator's saved-period interpretation, without
            # imposing request-time period limits on an already saved candidate.
            end_value = config.get("end_date") or config.get("end_date_exclusive")
            if config.get("start_date") and end_value:
                start, end = date.fromisoformat(config["start_date"]), date.fromisoformat(end_value)
            else:
                year, month = (int(value) for value in run.target_month.split("-"))
                start = date(year, month, 1)
                end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(409, detail={"code": "candidate_rule_source_unresolved"}) from error
        source = capture_rule_source(session, start, end)
        source["tables"][models.RuleProfile.__tablename__] = [
            _row_values(row) for row in session.query(models.RuleProfile).filter_by(enabled=True).order_by(models.RuleProfile.id).all()
        ]
    if not isinstance(source, dict) or not isinstance(source.get("tables"), dict):
        raise HTTPException(409, detail={"code": "candidate_rule_source_unresolved"})
    try:
        start, end = date.fromisoformat(source["start_date"]), date.fromisoformat(source["end_date"])
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(409, detail={"code": "candidate_rule_source_unresolved"}) from error
    if confirmed is None:
        # Historical staffing provenance stays frozen, but confirmation still
        # uses the existing dated rotation checks. Bind only those live inputs.
        rotation_source = _rotation_command_source(session, start, end)
        if prior_for_start_date is not None:
            source = {"historical_or_revalidated_source": source, "rotation_source": rotation_source}
        else:
            source["tables"][models.RotationPolicy.__tablename__] = rotation_source["policies"]
            source["tables"][models.RotationQueueState.__tablename__] = rotation_source["states"]
            source["rotation_confirmation_source"] = {
                "existing_member_ids": rotation_source["existing_member_ids"],
                "confirmed_ends": rotation_source["confirmed_ends"],
            }
    return {"candidate_id": candidate.id, "candidate_revision": _hash(content),
            "input_revision": _hash({"config": config, "source": source,
                                     "prior_for_start_date": prior_for_start_date})}


def execute_candidate_command(session, value, candidate_id, command, *, prior_for_start_date=None, solve_run_id=None):
    expected = require_precondition(value)
    if candidate_id is not None and expected.candidate_id != candidate_id:
        raise HTTPException(409, detail={"code": "candidate_precondition_conflict"})
    with candidate_snapshot(session, writing=True):
        run_id = session.query(models.CandidateSchedule.solve_run_id).filter_by(id=expected.candidate_id).scalar()
        if run_id is None or (solve_run_id is not None and run_id != solve_run_id):
            raise HTTPException(409, detail={"code": "candidate_precondition_conflict"})
        session.query(models.SolveRun).filter_by(id=run_id).populate_existing().with_for_update().one()
        # A physical row version makes concurrent commands conflict even when
        # their first snapshot precedes another command's assignment-only edit.
        session.execute(update(models.SolveRun).where(models.SolveRun.id == run_id).values(status=models.SolveRun.status))
        candidate = session.query(models.CandidateSchedule).filter_by(id=expected.candidate_id).populate_existing().with_for_update().one_or_none()
        if candidate is None:
            raise HTTPException(409, detail={"code": "candidate_precondition_conflict"})
        if prior_for_start_date is not None:
            from .schedule_selection import require_direct_predecessor
            require_direct_predecessor(session, candidate.solve_run, prior_for_start_date)
            snapshot = (candidate.score_breakdown_json or {}).get("generation_time_snapshot")
            confirmed = session.query(models.ScheduleConfirmation).filter_by(solve_run_id=run_id).first()
            if confirmed is None and (not isinstance(snapshot, dict) or not isinstance(snapshot.get("validation"), dict)):
                raise HTTPException(409, detail={"code": "legacy_prior_requires_selection"})
        current = candidate_precondition(session, candidate, prior_for_start_date=prior_for_start_date)
        if current != expected.model_dump():
            raise HTTPException(409, detail={"code": "candidate_precondition_conflict"})
        result = command(candidate)
        session.flush()
        result["command_precondition"] = candidate_precondition(session, candidate, prior_for_start_date=prior_for_start_date)
        return result
