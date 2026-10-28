from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import RotationPolicy, RotationQueueState, Staff


def _queue_inputs(session, policies, as_of):
    ids = [policy.id for policy in policies]
    if not ids:
        return {}, set()
    latest = select(
        RotationQueueState.rotation_policy_id.label('policy_id'),
        func.max(RotationQueueState.effective_from).label('effective_from'),
    ).where(
        RotationQueueState.rotation_policy_id.in_(ids),
        RotationQueueState.effective_from <= as_of,
    ).group_by(RotationQueueState.rotation_policy_id).subquery()
    by_policy = {policy.id: [] for policy in policies}
    for row in session.query(RotationQueueState).join(latest,
        (RotationQueueState.rotation_policy_id == latest.c.policy_id)
        & (RotationQueueState.effective_from == latest.c.effective_from),
    ).order_by(RotationQueueState.id.desc()).populate_existing():
        by_policy[row.rotation_policy_id].append(row)
    staff_ids = set()
    for policy in policies:
        rows = by_policy[policy.id]
        candidates = [row for row in rows if row.source_solve_run_id is None] or rows
        if len(candidates) > 1:
            continue
        queue = candidates[0].queue_staff_ids if candidates else policy.member_staff_ids
        if not isinstance(queue, (list, tuple)) or any(type(value) is not int for value in queue):
            continue
        staff_ids.update(queue)
    existing = set()
    ordered_ids = list(staff_ids)
    for offset in range(0, len(ordered_ids), 500):
        existing.update(row[0] for row in session.query(Staff.id).filter(
            Staff.id.in_(ordered_ids[offset:offset + 500]),
        ))
    return by_policy, existing


def _select_queue(policy, states, existing_ids):
    state = None
    if states:
        same_day = [row for row in states if row.effective_from == states[0].effective_from]
        operator_states = [row for row in same_day if row.source_solve_run_id is None]
        candidates = operator_states or same_day
        if len(candidates) != 1:
            raise ValueError(f"{policy.name}: {states[0].effective_from} の交代順が重複しています")
        state = candidates[0]
    recorded = state.queue_staff_ids if state else policy.member_staff_ids
    if not isinstance(recorded, (list, tuple)) or any(type(value) is not int for value in recorded):
        raise ValueError(f"{policy.name}: 交代順のスタッフIDを確認してください")
    queue = list(recorded)
    if not queue or len(queue) != len(set(queue)):
        raise ValueError(f"{policy.name}: 交代順の対象者を一人ずつ指定してください")
    if not set(queue).issubset(existing_ids):
        raise ValueError(f"{policy.name}: 交代順のスタッフが見つかりません")
    return state, queue


def resolve_rotation_queue(
    session: Session, policy: RotationPolicy, as_of: date, *, _inputs=None,
) -> tuple[RotationQueueState | None, list[int]]:
    """Resolve dated membership without changing the policy's historical baseline."""
    states, existing = _queue_inputs(session, [policy], as_of) if _inputs is None else _inputs
    return _select_queue(policy, states[policy.id], existing)


def resolve_rotation_queues(session: Session, policies: list[RotationPolicy], as_of: date) -> dict:
    states, existing = _queue_inputs(session, policies, as_of)
    return {policy.id: _select_queue(policy, states[policy.id], existing) for policy in policies}


def rotation_period_snapshot(
    session: Session, policies: list[RotationPolicy], start: date, end: date, *, _inputs=None,
) -> dict[str, dict]:
    snapshot = {}
    states, existing = _queue_inputs(session, policies, start) if _inputs is None else _inputs
    boundaries_by_policy = {policy.id: [] for policy in policies}
    if policies:
        for row in session.query(RotationQueueState).filter(
            RotationQueueState.rotation_policy_id.in_(boundaries_by_policy),
            RotationQueueState.effective_from > start,
            RotationQueueState.effective_from <= end,
        ).order_by(RotationQueueState.effective_from, RotationQueueState.id).populate_existing():
            boundaries_by_policy[row.rotation_policy_id].append(row)
    for policy in sorted(policies, key=lambda row: row.id):
        state, queue = _select_queue(policy, states[policy.id], existing)
        boundaries = boundaries_by_policy[policy.id]
        snapshot[str(policy.id)] = {
            "state_id": state.id if state else None,
            "queue_staff_ids": queue,
            "position_id": policy.position_id,
            "enabled": policy.enabled,
            "boundary_states": [
                {
                    "id": row.id,
                    "effective_from": row.effective_from.isoformat(),
                    "queue_staff_ids": list(row.queue_staff_ids),
                    "source_solve_run_id": row.source_solve_run_id,
                }
                for row in boundaries
            ],
        }
    return snapshot


def validate_rotation_period(
    session: Session, policies: list[RotationPolicy], start: date, end: date, *, _inputs=None,
) -> list[str]:
    errors = []
    states, existing = _queue_inputs(session, policies, start) if _inputs is None else _inputs
    boundary_dates = {policy.id: set() for policy in policies}
    if policies:
        for policy_id, effective_from in session.query(
            RotationQueueState.rotation_policy_id, RotationQueueState.effective_from,
        ).filter(RotationQueueState.rotation_policy_id.in_(boundary_dates),
                 RotationQueueState.effective_from > start, RotationQueueState.effective_from < end).distinct():
            boundary_dates[policy_id].add(effective_from)
    for policy in policies:
        try:
            _select_queue(policy, states[policy.id], existing)
        except ValueError as error:
            errors.append(str(error))
        for effective_from in sorted(boundary_dates[policy.id]):
            errors.append(
                f"{policy.name}: 期間内の {effective_from} に交代順の変更があります。"
                "適用日をまたぐ期間は作成・確定できません"
            )
    return errors


def validate_rotation_snapshot(
    session: Session, policies: list[RotationPolicy], start: date, end: date,
    snapshot: dict | None, *, _inputs=None,
) -> list[str]:
    inputs = _queue_inputs(session, policies, start) if _inputs is None else _inputs
    errors = validate_rotation_period(session, policies, start, end, _inputs=inputs)
    if errors:
        return errors
    if snapshot is None:
        return ["交代順の作成時記録がありません。作り直してから確定してください"] if policies else []
    try:
        current = rotation_period_snapshot(session, policies, start, end, _inputs=inputs)
    except ValueError as error:
        return [str(error)]
    if snapshot != current:
        errors.append("交代順が作成後に変更されています。作り直してから確定してください")
    return errors
