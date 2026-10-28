"""Internal durable solve ownership; no transport or public API cutover."""

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from . import models
from .jobs import SolveJobRequest, _execution_json, execution_json_equal, recorded_input_version, record_execution_terminal
from .periods import resolve_period_request
from .validation_snapshot import begin_solver_input_snapshot, capture_delivery_read_set


class DeliveryFenced(ValueError):
    pass


class DeliveryBusy(DeliveryFenced):
    pass


@dataclass(frozen=True)
class DeliveryReviewDisposition:
    code: str
    capture_trusted: bool
    stopped_for_review: bool
    active_lease: bool


def _lock_row(session, model, identity):
    """Exact own row only, delivery before run; PostgreSQL never waits on it."""
    try:
        return session.execute(select(model).where(model.id == identity).with_for_update(
            nowait=True
        ).execution_options(populate_existing=True)).scalar_one_or_none()
    except DBAPIError as error:
        code = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
        if code == "55P03":
            raise DeliveryBusy("shift_delivery_busy") from error
        raise


@dataclass(frozen=True)
class SolveClaim:
    delivery_id: int
    generation: int
    owner_attempt: str


def enqueue_solve(session, request: SolveJobRequest, *, transport_contract: dict):
    return _enqueue_solve(session, request, transport_contract=transport_contract)


def enqueue_cloud_task_solve(session, request: SolveJobRequest, *, config):
    from .solve_delivery_dispatch_service import CloudTaskConfig, capture_contract
    if not isinstance(config, CloudTaskConfig):
        raise ValueError("shift_delivery_explicit_transport_required")
    config.validate()
    return _enqueue_solve(session, request, transport_factory=lambda run_id: capture_contract(config, run_id, 1))


def enqueue_configured_solve(session, request: SolveJobRequest, *, config):
    from . import agent_trial_transport as local
    if isinstance(config, dict) and config.get("mode") == "request_local":
        return local.enqueue(session, request, config)
    return enqueue_cloud_task_solve(session, request, config=config)


def _enqueue_solve(session, request, *, transport_contract=None, transport_factory=None):
    """Capture and persist together; caller supplies the actual transport contract."""
    from .solver import prepare_period_solve
    if not isinstance(request, SolveJobRequest) or request.effective_settings is None:
        raise ValueError("shift_delivery_explicit_request_required")
    if transport_factory is None and (not isinstance(transport_contract, dict) or not transport_contract):
        raise ValueError("shift_delivery_explicit_transport_required")
    transport = _execution_json(transport_contract) if transport_factory is None else None
    try:
        begin_solver_input_snapshot(session)
        period = resolve_period_request(
            target_month=request.target_month, start_date=request.start_date,
            end_date_exclusive=request.end_date_exclusive, settings_config=request.effective_settings,
        )
        prepared = prepare_period_solve(
            session, period.target_month, period.start_date, period.end_date_exclusive,
            strategy=request.strategy, locked_assignment_ids=list(request.locked_assignment_ids),
            candidate_count=request.candidate_count, period_mode=period.period_mode,
            prior_statuses=list(request.prior_statuses), prior_time_history=list(request.prior_time_history),
            rule_config_override=request.effective_settings,
            execution_request={"producer": "execute_solve_job", "job_request": asdict(request)},
            commit=False,
        )
        if isinstance(prepared, models.SolveRun):
            session.commit()
            return prepared
        run = prepared.solve_run
        if transport_factory is not None:
            transport = _execution_json(transport_factory(run.id))
        version = recorded_input_version(run.config_json)
        if version is None:
            raise ValueError("shift_delivery_input_unavailable")
        run.status = "queued"
        delivery = models.SolveDelivery(
            solve_run_id=run.id, generation=1, state="pending", owner_attempt=None,
            lease_until=None, transport_contract=transport,
            captured_config=deepcopy(run.config_json), input_version=version,
            read_set=capture_delivery_read_set(session, start=period.start_date, end=period.end_date_exclusive), receipt=None,
        )
        session.add(delivery)
        session.commit()
        return run
    except BaseException:
        session.rollback()
        raise


def claim_solve(session, delivery_id: int, *, now: datetime, lease_until: datetime):
    """A pending-only CAS; duplicates never wait on a calculation-held row lock."""
    if session.in_transaction() or lease_until <= now or now.tzinfo is not None or lease_until.tzinfo is not None:
        raise ValueError("shift_delivery_clean_session_and_explicit_lease_required")
    owner = str(uuid4())
    try:
        delivery = _lock_row(session, models.SolveDelivery, delivery_id)
        if delivery is None or delivery.state != "pending":
            session.rollback()
            return None
        result = session.execute(update(models.SolveDelivery).where(
            models.SolveDelivery.id == delivery_id, models.SolveDelivery.state == "pending",
            models.SolveDelivery.owner_attempt.is_(None), models.SolveDelivery.receipt.is_(None),
        ).values(state="claimed", owner_attempt=owner, lease_until=lease_until))
        if result.rowcount != 1:
            session.rollback()
            return None
        generation = session.execute(select(models.SolveDelivery.generation).where(
            models.SolveDelivery.id == delivery_id
        )).scalar_one()
        session.commit()
        return SolveClaim(delivery_id, generation, owner)
    except BaseException:
        session.rollback()
        raise


def _owned(delivery, claim, now):
    if (delivery is None or delivery.state != "claimed" or delivery.generation != claim.generation
            or delivery.owner_attempt != claim.owner_attempt or delivery.receipt is not None
            or delivery.lease_until is None or delivery.lease_until <= now):
        raise DeliveryFenced("shift_delivery_owner_fenced")


def _finish(session, delivery, run, claim, *, now, failure):
    """Only lifecycle columns are updated by CAS; immutable captures are untouched."""
    receipt = {
        "run_id": run.id, "generation": claim.generation, "owner_attempt": claim.owner_attempt,
        "input_version": delivery.input_version, "business_state": run.status,
        "failure": failure,
    }
    session.flush()
    changed = session.execute(update(models.SolveDelivery).where(
        models.SolveDelivery.id == claim.delivery_id,
        models.SolveDelivery.generation == claim.generation,
        models.SolveDelivery.state == "claimed",
        models.SolveDelivery.owner_attempt == claim.owner_attempt,
        models.SolveDelivery.input_version == delivery.input_version,
        models.SolveDelivery.lease_until > now,
        models.SolveDelivery.receipt.is_(None),
    ).values(state="terminal", receipt=receipt), execution_options={"synchronize_session": False})
    if changed.rowcount != 1:
        raise DeliveryFenced("shift_delivery_owner_fenced")
    session.commit()
    return run


def _record_failure(session, run, failure):
    run.status = "failed"
    run.solver_status = None
    run.finished_at = datetime.now(UTC)
    record_execution_terminal(run)
    session.add(models.Violation(solve_run_id=run.id, severity="error",
        rule_name=failure["code"], message=failure["reason"], penalty=0))


def _validate_delivery_capture(delivery, run):
    """One source fence for execution and expiry; equal corruption is not evidence."""
    try:
        captured = delivery.captured_config
        if run is None or run.status != "queued" or type(captured) is not dict:
            raise ValueError("capture")
        contract = captured.get("execution_contract")
        version = recorded_input_version(captured)
        if (type(contract) is not dict or type(version) is not str or not version
                or type(delivery.input_version) is not str or not delivery.input_version
                or version != delivery.input_version
                or contract.get("inputVersion") != version
                or contract.get("inputAvailability") != "recorded"
                or not execution_json_equal(run.config_json, captured)):
            raise ValueError("capture")
        attempt = contract.get("attemptIdentity")
        if type(attempt) is not str or not attempt:
            raise ValueError("attempt")
        identity = UUID(attempt)
        if identity.version != 4 or str(identity) != attempt:
            raise ValueError("attempt")
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise DeliveryFenced("shift_delivery_capture_corrupt") from error
    return captured


def classify_delivery_review(session, delivery, run, *, now=None):
    """Classify a durable review without changing its claimed business work."""
    review = session.query(models.SolveDeliveryReview).filter_by(delivery_id=delivery.id).one_or_none()
    if review is None:
        return None
    capture_trusted = True
    try:
        _validate_delivery_capture(delivery, run)
    except DeliveryFenced:
        capture_trusted = False
    transport_stoppable = (
        delivery.state == "claimed" and run is not None and run.status == "queued"
        and delivery.lease_until is not None and delivery.receipt is None
    )
    active_lease = transport_stoppable and now is not None and delivery.lease_until > now
    lease_expired = now is None or (transport_stoppable and delivery.lease_until <= now)
    return DeliveryReviewDisposition(
        code=review.code,
        capture_trusted=capture_trusted,
        stopped_for_review=(review.code == "shift_recovery_requires_review"
            and transport_stoppable and lease_expired),
        active_lease=active_lease,
    )


def execute_claimed_solve(session, claim: SolveClaim, *, clock):
    """Compare and execute within one snapshot; clock is an explicit control input."""
    from .solver import PreparedInputStopped, PreparedPeriodSolve, prepare_period_solve, run_prepared_period_solve
    try:
        begin_solver_input_snapshot(session)
        delivery = _lock_row(session, models.SolveDelivery, claim.delivery_id)
        _owned(delivery, claim, clock())
        run = _lock_row(session, models.SolveRun, delivery.solve_run_id)
        if run is None:
            raise DeliveryFenced("shift_delivery_run_missing")
        captured = _validate_delivery_capture(delivery, run)
        if (session.query(models.CandidateSchedule.id).filter_by(solve_run_id=run.id).first()
                or session.query(models.ScheduleConfirmation.id).filter_by(solve_run_id=run.id).first()):
            raise DeliveryFenced("shift_delivery_outputs_already_exist")
        source = captured["generation_rule_source"]
        session.info["solver_prior_source"] = deepcopy(source["prior_source"])
        request = captured["execution_contract"]["request"]["solve_period"]
        failure = None
        try:
            recorded_read_set = delivery.read_set
            legacy_keys = {models.RotationPolicy.__tablename__, models.RotationQueueState.__tablename__}
            if type(recorded_read_set) is dict and set(recorded_read_set) == legacy_keys:
                current_read_set = capture_delivery_read_set(session, version=1)
            elif type(recorded_read_set) is dict and recorded_read_set.get("version") == 2:
                current_read_set = capture_delivery_read_set(session, start=date.fromisoformat(request["start_date"]),
                    end=date.fromisoformat(request["end_date_exclusive"]))
            else:
                raise PreparedInputStopped("shift_delivery_input_changed", "Rotation read set version is unresolved")
            if not execution_json_equal(recorded_read_set, current_read_set):
                raise PreparedInputStopped("shift_delivery_input_changed", "Rotation read set differs")
            prepared = prepare_period_solve(
                session, request["label"], request["start_date"], request["end_date_exclusive"],
                strategy=request["strategy"], reference_source=request["reference_source"],
                locked_assignment_ids=request["locked_assignment_ids"], candidate_count=request["candidate_count"],
                period_mode=request["period_mode"], prior_statuses=[
                    (staff_id, date.fromisoformat(day), status)
                    for staff_id, day, status in (request["prior_statuses"] or [])
                ] if request["prior_statuses"] is not None else None,
                prior_time_history=request["prior_time_history"], rule_config_override=request["rule_config_override"],
                execution_request=captured["execution_contract"]["request"]["caller_request"],
                existing_run=run, commit=False,
            )
        except PreparedInputStopped as error:
            failure = {"code": error.code, "reason": error.reason}
            _record_failure(session, run, failure)
        else:
            if not isinstance(prepared, PreparedPeriodSolve):
                raise DeliveryFenced("shift_delivery_preparation_unavailable")
            try:
                with session.begin_nested():
                    run.status = "running"
                    run_prepared_period_solve(prepared, commit=False)
            except Exception as error:
                failure = {"code": "shift_delivery_execution_failed", "reason": type(error).__name__}
                _record_failure(session, run, failure)
        return _finish(session, delivery, run, claim, now=clock(), failure=failure)
    except BaseException:
        # Includes savepoints, candidate rows and early infeasible violations.
        session.rollback()
        raise


def stop_expired_solve(session, claim: SolveClaim, *, now: datetime):
    """Explicit recovery stops an abandoned attempt, never re-runs business work."""
    if session.in_transaction():
        raise ValueError("shift_delivery_clean_session_required")
    try:
        locked = _lock_row(session, models.SolveDelivery, claim.delivery_id)
        if locked is None:
            raise DeliveryFenced("shift_delivery_owner_fenced")
        run = _lock_row(session, models.SolveRun, locked.solve_run_id)
        _validate_delivery_capture(locked, run)
        changed = session.execute(update(models.SolveDelivery).where(
            models.SolveDelivery.id == claim.delivery_id,
            models.SolveDelivery.generation == claim.generation,
            models.SolveDelivery.owner_attempt == claim.owner_attempt,
            models.SolveDelivery.state == "claimed", models.SolveDelivery.lease_until <= now,
            models.SolveDelivery.receipt.is_(None),
        ).values(state="stopping"))
        if changed.rowcount != 1:
            raise DeliveryFenced("shift_delivery_owner_fenced")
        delivery = session.get(models.SolveDelivery, claim.delivery_id)
        run = session.get(models.SolveRun, delivery.solve_run_id)
        if (session.query(models.CandidateSchedule.id).filter_by(solve_run_id=run.id).first()
                or session.query(models.ScheduleConfirmation.id).filter_by(solve_run_id=run.id).first()):
            raise DeliveryFenced("shift_delivery_recovery_requires_review")
        failure = {"code": "shift_delivery_attempt_expired", "reason": "Owned execution lease expired"}
        _record_failure(session, run, failure)
        delivery.state = "terminal"
        delivery.receipt = {"run_id": run.id, "generation": claim.generation,
            "owner_attempt": claim.owner_attempt, "input_version": delivery.input_version,
            "business_state": run.status, "failure": failure}
        session.commit()
        return run
    except BaseException:
        session.rollback()
        raise
