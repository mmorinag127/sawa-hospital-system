"""Freeze the exact validation inputs and accepted candidate at confirmation."""

from copy import deepcopy
from datetime import date, datetime, time

from sqlalchemy import inspect, select, func, or_, and_
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException

from . import models


def begin_confirmation_snapshot(session):
    """Start one database snapshot before any confirmation reads or writes."""
    if session.in_transaction() and session.info.get("confirmation_snapshot_transaction") is session.get_transaction():
        return
    try:
        connection = session.connection()
        dialect = connection.dialect.name
        if dialect == "postgresql":
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        elif dialect == "sqlite":
            # Python's legacy SQLite driver does not begin a transaction for SELECT.
            if not connection.connection.driver_connection.in_transaction:
                connection.exec_driver_sql("BEGIN")
        else:
            raise HTTPException(status_code=409, detail="このDBでは確定時の入力正本の時点整合を確認できません")
    except SQLAlchemyError as error:
        raise HTTPException(status_code=409, detail="確定用の一貫した入力読み取りを開始できません。再読み込みして確定操作をやり直してください") from error
    session.info["confirmation_snapshot_transaction"] = session.get_transaction()


def begin_solver_input_snapshot(session):
    """Freeze one solve's authoritative inputs before the first solve read."""
    key = "solver_input_snapshot_transaction"
    if session.in_transaction() and session.info.get(key) is session.get_transaction():
        return
    if session.new or session.dirty or session.deleted:
        raise HTTPException(
            status_code=409,
            detail="未保存の設定変更があるため、一貫した計算入力を開始できません。保存後に計算をやり直してください",
        )
    if session.in_transaction():
        # Dirty/new can be empty after flush; never discard caller-owned writes.
        raise HTTPException(status_code=409, detail="既存のトランザクションを終了してから計算を開始してください")
    try:
        connection = session.connection()
        dialect = connection.dialect.name
        if dialect == "postgresql":
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        elif dialect == "sqlite":
            # Python's legacy SQLite driver does not begin a transaction for SELECT.
            if not connection.connection.driver_connection.in_transaction:
                connection.exec_driver_sql("BEGIN")
        else:
            raise HTTPException(status_code=409, detail="このDBでは計算入力の時点整合を確認できません")
        # Reads must use this transaction's database values, never stale held entities.
        session.expire_all()
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=409,
            detail="一貫した計算入力の読み取りを開始できません。再読み込みして計算をやり直してください",
        ) from error
    session.info[key] = session.get_transaction()
    session.info.pop("solver_prior_source", None)


def _json_value(value):
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return deepcopy(value)


def _row_values(row, excluded=()):
    return {column.key: _json_value(getattr(row, column.key))
            for column in inspect(type(row)).columns if column.key not in excluded}


def candidate_state(session, candidate):
    return {
        "assignments": [_row_values(row, {"id", "candidate_id"}) for row in session.query(models.Assignment).filter_by(
            candidate_id=candidate.id,
        ).order_by(models.Assignment.id).populate_existing().all()],
        "statuses": [_row_values(row, {"id", "candidate_id"}) for row in session.query(models.CandidateStaffStatus).filter_by(
            candidate_id=candidate.id,
        ).order_by(models.CandidateStaffStatus.staff_id, models.CandidateStaffStatus.work_date).populate_existing().all()],
    }


def _capture_rule_source(session, start, end, *, populate_existing, solver=False):
    result = {"start_date": start.isoformat(), "end_date": end.isoformat(), "tables": {}}
    revision = session.query(models.EditorRevision.revision).filter_by(resource="position_assignment_rules").scalar()
    if type(revision) is not int or revision <= 0:
        raise HTTPException(409, detail={"code": "position_assignment_rules_revision_unresolved"})
    result["position_assignment_rules_revision"] = revision
    for model in (
        models.Staff, models.Skill, models.StaffSkill, models.Position, models.StaffPositionRule,
        models.StaffPositionPreference, models.ShiftTemplate, models.WorkdayRule, models.Demand,
        models.Availability, models.StaffPairRule, models.RuleConfig, models.PositionPriority,
        models.RotationPolicy, models.RotationQueueState, models.ScheduleException,
        models.PositionAssignmentRule,
    ):
        if model in (models.RotationPolicy, models.RotationQueueState):
            continue
        query = session.query(model)
        if model is models.PositionAssignmentRule:
            query = query.filter(model.effective_from < end, model.effective_to.is_(None) | (model.effective_to >= start))
        if hasattr(model, "work_date"):
            query = query.filter(model.work_date >= start, model.work_date < end)
        query = query.order_by(*inspect(model).primary_key)
        if populate_existing:
            query = query.populate_existing()
        result["tables"][model.__tablename__] = [_row_values(row) for row in query.all()]
    if not solver:
        rotations = capture_delivery_read_set(session, start=start, end=end, populate_existing=populate_existing)
        result['rotation_read_set_version'] = rotations['version']
        result['tables'].update(rotations['tables'])
    return result


def capture_delivery_read_set(session, *, start=None, end=None, version=2, populate_existing=False):
    if version == 1:
        return {model.__tablename__: [_row_values(row) for row in
            session.query(model).order_by(*inspect(model).primary_key).all()]
            for model in (models.RotationPolicy, models.RotationQueueState)}
    if version != 2 or not isinstance(start, date) or not isinstance(end, date) or start >= end:
        raise ValueError("shift_delivery_read_set_period_required")
    policy_query = session.query(models.RotationPolicy).order_by(models.RotationPolicy.id)
    policies = (policy_query.populate_existing() if populate_existing else policy_query).all()
    enabled_ids = [row.id for row in policies if row.enabled]
    queue = models.RotationQueueState
    latest = select(queue.rotation_policy_id.label('policy_id'), func.max(queue.effective_from).label('day')).where(
        queue.rotation_policy_id.in_(enabled_ids), queue.effective_from <= start,
    ).group_by(queue.rotation_policy_id).subquery()
    state_query = session.query(queue).outerjoin(latest, latest.c.policy_id == queue.rotation_policy_id).filter(
        queue.rotation_policy_id.in_(enabled_ids),
        or_(queue.effective_from == latest.c.day, and_(queue.effective_from > start, queue.effective_from <= end)),
    ).order_by(queue.id)
    states = (state_query.populate_existing() if populate_existing else state_query).all() if enabled_ids else []
    return {"version": 2, "start_date": start.isoformat(), "end_date": end.isoformat(),
            "tables": {models.RotationPolicy.__tablename__: [_row_values(row) for row in policies],
                       queue.__tablename__: [_row_values(row) for row in states]}}


def capture_rule_source(session, start, end):
    return _capture_rule_source(session, start, end, populate_existing=True)


def capture_solver_input_source(session, start, end, *, effective_settings,
                                locked_rows, reference_source, planning_rules,
                                prior_statuses, prior_time_history):
    """Capture solve provenance from the already-started solver transaction."""
    if not session.in_transaction() or session.info.get("solver_input_snapshot_transaction") is not session.get_transaction():
        raise HTTPException(status_code=409, detail="計算入力スナップショットが確立されていません")
    result = _capture_rule_source(session, start, end, populate_existing=False, solver=True)
    result["version"] = 2
    result["effective_settings"] = deepcopy(effective_settings)
    result["locked_assignments"] = [_row_values(row) for row in locked_rows]
    result["prior_source"] = deepcopy(session.info.get("solver_prior_source"))
    result["prior_statuses"] = [
        {"staff_id": staff_id, "work_date": _json_value(day), "status": status}
        for staff_id, day, status in (prior_statuses or [])
    ]
    result["prior_time_history"] = deepcopy(prior_time_history or [])
    result["reference_source"] = reference_source
    for model in (models.ReferenceAssignment, models.ReferenceStaffStatus):
        rows = []
        if reference_source:
            rows = session.query(model).filter(
                model.source_name == reference_source,
                model.confidence.in_(["high", "medium"]),
                model.work_date >= start, model.work_date < end,
            ).order_by(*inspect(model).primary_key).all()
        result["tables"][model.__tablename__] = [_row_values(row) for row in rows]
    result["tables"][models.RotationPolicy.__tablename__] = [
        _row_values(row) for row in planning_rules["rotation_policies"]
    ]
    result["rotation_queue_snapshot"] = deepcopy(planning_rules["rotation_queue_snapshot"])
    state_ids = {value for value in planning_rules["rotation_state_snapshot"].values() if value is not None}
    state_ids.update(row["id"] for snapshot in planning_rules["rotation_queue_snapshot"].values()
                     for row in snapshot["boundary_states"])
    result["tables"][models.RotationQueueState.__tablename__] = [
        _row_values(row) for row in session.query(models.RotationQueueState).filter(
            models.RotationQueueState.id.in_(state_ids),
        ).order_by(*inspect(models.RotationQueueState).primary_key).all()
    ] if state_ids else []
    return result


def confirmed_validation(session, candidate):
    confirmation = session.query(models.ScheduleConfirmation).filter_by(solve_run_id=candidate.solve_run_id).first()
    if confirmation is None:
        return None
    from .schedule_selection import confirmed_candidate
    accepted = confirmed_candidate(session, candidate.solve_run)
    if accepted is None or accepted.id != candidate.id:
        return {"blocking": True, "warnings": [{"severity": "error", "code": "confirmed_candidate_unresolved",
                "message": "確定済みの候補を特定できません。確定記録を確認してください"}],
                "holiday_counts": [], "additional_weekday_offs": [], "rotation_balances": []}
    snapshot = (candidate.score_breakdown_json or {}).get("confirmed_validation_snapshot")
    if snapshot is None:
        balances = []
        assignments = session.query(models.Assignment).filter_by(candidate_id=candidate.id).all()
        for state in session.query(models.RotationQueueState).filter_by(source_solve_run_id=candidate.solve_run_id).all():
            policy = session.get(models.RotationPolicy, state.rotation_policy_id)
            if policy is None or not state.queue_staff_ids:
                continue
            counts = {staff_id: sum(row.staff_id == staff_id and row.position_id == policy.position_id
                                    for row in assignments) for staff_id in state.queue_staff_ids}
            balances.append({"rotation_id": policy.id, "rotation_name": policy.name,
                             "position_name": policy.position.display_name or policy.position.name,
                             "counts": counts, "maximum": max(counts.values()), "minimum": min(counts.values())})
        return {"blocking": False, "warnings": [{"severity": "info", "code": "legacy_confirmed_validation_unavailable",
                "message": "この確定済み表は過去の確定結果を保持しています。作成時の全条件記録がないため、現在の設定で再判定していません"}],
                "holiday_counts": [], "additional_weekday_offs": [], "rotation_balances": balances,
                "historical_validation": "unavailable"}
    if snapshot.get("candidate_state") != candidate_state(session, candidate):
        return {"blocking": True, "warnings": [{"severity": "error", "code": "confirmed_candidate_state_changed",
                "message": "保存された実配置が確定時の正本と一致しません。確定済み表を変更せず記録を確認してください"}],
                "holiday_counts": [], "additional_weekday_offs": [], "rotation_balances": []}
    return deepcopy(snapshot["validation"])


def historical_candidate_validation(session, candidate):
    """Use creation-time validation for an unchanged historical candidate."""
    confirmed = confirmed_validation(session, candidate)
    if confirmed is not None:
        return confirmed
    snapshot = (candidate.score_breakdown_json or {}).get("generation_time_snapshot")
    if snapshot is not None and snapshot.get("candidate_state") != candidate_state(session, candidate):
        from .candidate_validation import validate_candidate
        return validate_candidate(candidate.id, session)
    if snapshot is not None and isinstance(snapshot.get("validation"), dict):
        return deepcopy(snapshot["validation"])
    return {
        "blocking": False,
        "warnings": [{
            "severity": "info",
            "code": "legacy_candidate_validation_unavailable",
            "message": "この過去候補には作成時の検証結果がありません。現在の設定では再判定せず、次期間の勤務履歴として選択できます",
        }],
        "holiday_counts": [],
        "additional_weekday_offs": [],
        "rotation_balances": [],
        "historical_validation": "unavailable",
    }


def validate_for_confirmation(session, candidate, start, end, *, prior_for_start_date=None):
    from .candidate_validation import validate_candidate
    from .operational_constraints import candidate_time_rows

    begin_confirmation_snapshot(session)
    session.flush()
    state = candidate_state(session, candidate)
    if prior_for_start_date is not None:
        from .schedule_selection import require_direct_predecessor
        require_direct_predecessor(session, candidate.solve_run, prior_for_start_date)
    generation_snapshot = (candidate.score_breakdown_json or {}).get("generation_time_snapshot")
    unchanged_since_generation = bool(
        prior_for_start_date is not None
        and (
            generation_snapshot is None
            or generation_snapshot.get("candidate_state") == state
        )
    )
    if prior_for_start_date is not None and unchanged_since_generation:
        validation = historical_candidate_validation(session, candidate)
        source = deepcopy((generation_snapshot or {}).get("rule_source"))
    else:
        source = capture_rule_source(session, start, end)
        validation = validate_candidate(candidate.id, session)
    if validation["blocking"]:
        return validation
    source_changed = (
        not unchanged_since_generation
        and source != capture_rule_source(session, start, end)
    )
    if source_changed or state != candidate_state(session, candidate):
        return {**validation, "blocking": True, "warnings": [*validation["warnings"], {
            "severity": "error", "code": "confirmation_inputs_changed",
            "message": "確認中に勤務条件または実配置が変更されました。最新の条件で再確認してください",
        }]}
    candidate.score_breakdown_json = {**(candidate.score_breakdown_json or {}), "confirmed_validation_snapshot": {
        "version": 1, "rule_source": source, "candidate_state": state,
        "validation": deepcopy(validation), "time_rows": candidate_time_rows(session, candidate.id),
    }}
    return validation


def historical_time_rows(session, candidate):
    from .operational_constraints import candidate_time_rows

    confirmed = session.query(models.ScheduleConfirmation).filter_by(solve_run_id=candidate.solve_run_id).first()
    if confirmed is None:
        return candidate_time_rows(session, candidate.id)
    score = candidate.score_breakdown_json or {}
    state = candidate_state(session, candidate)
    for key in ("confirmed_validation_snapshot", "generation_time_snapshot"):
        snapshot = score.get(key)
        if snapshot is not None and snapshot.get("candidate_state") == state:
            return deepcopy(snapshot["time_rows"])
    # Legacy confirmation proves the saved day state, not mutable template times.
    assigned_days = {(row["staff_id"], row["work_date"]) for row in state["assignments"]}
    return [{"staff_id": row["staff_id"], "work_date": row["work_date"], "status": row["status"],
             "source_candidate_id": candidate.id,
             "slots": [] if row["status"] == "off" and (row["staff_id"], row["work_date"]) not in assigned_days else None}
            for row in state["statuses"]]
