from datetime import date

from sqlalchemy.orm import Session

from .models import RotationPolicy, RotationQueueState, Staff


def resolve_rotation_queue(
    session: Session, policy: RotationPolicy, as_of: date,
) -> tuple[RotationQueueState | None, list[int]]:
    """Resolve dated membership without changing the policy's historical baseline."""
    states = (
        session.query(RotationQueueState)
        .filter(
            RotationQueueState.rotation_policy_id == policy.id,
            RotationQueueState.effective_from <= as_of,
        )
        .order_by(RotationQueueState.effective_from.desc(), RotationQueueState.id.desc())
        .populate_existing()
        .all()
    )
    state = None
    if states:
        same_day = [row for row in states if row.effective_from == states[0].effective_from]
        operator_states = [row for row in same_day if row.source_solve_run_id is None]
        candidates = operator_states or same_day
        if len(candidates) != 1:
            raise ValueError(f"{policy.name}: {states[0].effective_from} の交代順が重複しています")
        state = candidates[0]
    queue = list(state.queue_staff_ids if state else policy.member_staff_ids)
    if not queue or len(queue) != len(set(queue)):
        raise ValueError(f"{policy.name}: 交代順の対象者を一人ずつ指定してください")
    existing_ids = {row[0] for row in session.query(Staff.id).filter(Staff.id.in_(queue)).all()}
    if existing_ids != set(queue):
        raise ValueError(f"{policy.name}: 交代順のスタッフが見つかりません")
    return state, queue


def rotation_period_snapshot(
    session: Session, policies: list[RotationPolicy], start: date, end: date,
) -> dict[str, dict]:
    snapshot = {}
    for policy in sorted(policies, key=lambda row: row.id):
        state, queue = resolve_rotation_queue(session, policy, start)
        boundaries = (
            session.query(RotationQueueState)
            .filter(
                RotationQueueState.rotation_policy_id == policy.id,
                RotationQueueState.effective_from > start,
                RotationQueueState.effective_from <= end,
            )
            .order_by(RotationQueueState.effective_from, RotationQueueState.id)
            .populate_existing()
            .all()
        )
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
    session: Session, policies: list[RotationPolicy], start: date, end: date,
) -> list[str]:
    errors = []
    for policy in policies:
        try:
            resolve_rotation_queue(session, policy, start)
        except ValueError as error:
            errors.append(str(error))
        boundaries = (
            session.query(RotationQueueState)
            .filter(
                RotationQueueState.rotation_policy_id == policy.id,
                RotationQueueState.effective_from > start,
                RotationQueueState.effective_from < end,
            )
            .order_by(RotationQueueState.effective_from)
            .all()
        )
        for effective_from in sorted({row.effective_from for row in boundaries}):
            errors.append(
                f"{policy.name}: 期間内の {effective_from} に交代順の変更があります。"
                "適用日をまたぐ期間は作成・確定できません"
            )
    return errors


def validate_rotation_snapshot(
    session: Session, policies: list[RotationPolicy], start: date, end: date,
    snapshot: dict | None,
) -> list[str]:
    errors = validate_rotation_period(session, policies, start, end)
    if errors:
        return errors
    if snapshot is None:
        return ["交代順の作成時記録がありません。作り直してから確定してください"] if policies else []
    try:
        current = rotation_period_snapshot(session, policies, start, end)
    except ValueError as error:
        return [str(error)]
    if snapshot != current:
        errors.append("交代順が作成後に変更されています。作り直してから確定してください")
    return errors
