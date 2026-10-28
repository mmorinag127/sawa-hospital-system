"""Validate assignments with the same eligibility and deterministic rules as generation."""

from collections import defaultdict
from datetime import date
from types import SimpleNamespace

from .models import (
    Assignment, AssignmentExplanation, CandidateStaffStatus, Demand, Position, PositionAssignmentRule,
    PositionPriority, RotationPolicy, ScheduleException, ShiftTemplate, Staff, StaffPairRule, WorkdayRule,
)


class AssignmentChecks:
    """Evaluate the equality constraints emitted by the shared lock builder."""

    def __init__(self):
        self.valid = True

    def Add(self, condition):
        self.valid = self.valid and bool(condition)


def validate_assignments(session, candidate, start, end, settings, *, assignments=None, candidate_statuses=None,
                         workdays=None, period_dates=None, staffs=None, templates=None, demands=None,
                         hard_pair_rules=None, policies=None, availability=None):
    from .operational_constraints import active_on
    from .solver import (
        _assignment_eligibility_error, _daily_work_limit_minutes, _is_hard_unavailable,
        _load_availability, _load_locked_assignments, _load_staff_position_rules,
        _load_staff_skill_ids, _prepare_assignment_locks, _shift_minutes,
    )

    errors = []
    if staffs is None:
        staffs = {row.id: row for row in session.query(Staff).all()}
    positions = {row.id: row for row in session.query(Position).all()}
    if templates is None:
        templates = {row.id: row for row in session.query(ShiftTemplate).all()}
    if demands is None:
        demands = session.query(Demand).filter(Demand.work_date >= start, Demand.work_date < end).order_by(
            Demand.work_date, Demand.shift_template_id, Demand.position_id,
        ).all()
    if workdays is None:
        workdays = {row.work_date: row for row in session.query(WorkdayRule).filter(
            WorkdayRule.work_date >= start, WorkdayRule.work_date < end,
        ).all()}
    if assignments is None:
        assignments = session.query(Assignment).filter_by(candidate_id=candidate.id).all()
    if candidate_statuses is None:
        candidate_statuses = session.query(CandidateStaffStatus).filter_by(candidate_id=candidate.id).all()
    statuses = {(row.staff_id, row.work_date): row for row in candidate_statuses}
    hard_unavailable, _, half_day_overrides, _, hard_working = (
        _load_availability(session, start, end) if availability is None else availability)
    staff_skills = _load_staff_skill_ids(session)
    position_rules = _load_staff_position_rules(session)

    def report(code, message, staff_id=None, day=None):
        name = staffs[staff_id].name if staff_id in staffs else "勤務表"
        errors.append({"severity": "error", "code": code, "staff_id": staff_id,
                       "work_date": day.isoformat() if day else start.isoformat(),
                       "message": f"{name}：{(day or start).isoformat()}、{message}"})

    by_slot = defaultdict(list)
    by_day = defaultdict(list)
    for row in assignments:
        by_slot[(row.work_date, row.position_id, row.shift_template_id)].append(row)
        by_day[(row.staff_id, row.work_date)].append(row)
    demand_slots = {(row.work_date, row.position_id, row.shift_template_id) for row in demands}
    for row in assignments:
        if (row.work_date, row.position_id, row.shift_template_id) not in demand_slots:
            report("assignment_demand_unresolved", "担当の対象枠がありません。必要人数の条件を設定して再確認してください", row.staff_id, row.work_date)

    values = {}
    for demand in demands:
        selected = by_slot[(demand.work_date, demand.position_id, demand.shift_template_id)]
        selected_ids = {row.staff_id for row in selected}
        for staff in staffs.values():
            reason = _assignment_eligibility_error(
                staff, demand, positions[demand.position_id], hard_unavailable, staff_skills,
                position_rules, workdays, templates, settings, half_day_overrides,
            )
            if reason is None:
                values[(staff.id, demand.id)] = int(staff.id in selected_ids)
            elif staff.id in selected_ids:
                report("assignment_not_eligible", reason, staff.id, demand.work_date)
        if len(selected) != len(selected_ids):
            report("duplicate_assignment", "同じ担当枠に同じ職員が重複しています", day=demand.work_date)
        for staff_id in positions[demand.position_id].required_staff_ids or []:
            if staff_id not in selected_ids:
                report("required_staff_missing", f"{positions[demand.position_id].display_name or positions[demand.position_id].name} の必須担当者が配置されていません", staff_id, demand.work_date)

    for (staff_id, day), status in statuses.items():
        if not start <= day < end:
            report("status_outside_period", "対象期間外の勤務状態が保存されています", staff_id, day)
            continue
        if status.status != "working":
            continue
        if staff_id not in staffs or not active_on(staffs[staff_id], day):
            report("working_outside_employment", "在籍期間外に出勤が設定されています", staff_id, day)
            continue
        if (staff_id, day) in hard_unavailable["days"]:
            report("hard_unavailable_working", "勤務不可または必須の希望休に出勤が設定されています", staff_id, day)
        workday = workdays.get(day)
        if workday is not None and workday.kind == "closed":
            report("closed_day_working", "休業日に出勤が設定されています", staff_id, day)
        rows = by_day[(staff_id, day)]
        template_ids = [row.shift_template_id for row in rows] if rows else [status.shift_template_id]
        if any(_is_hard_unavailable(staff_id, day, template_id, hard_unavailable) for template_id in template_ids):
            report("hard_unavailable_shift", "勤務不可に指定された勤務枠が設定されています", staff_id, day)
        if (staff_id, day) in half_day_overrides:
            template_id = half_day_overrides[(staff_id, day)]
            if template_id not in templates:
                report("half_day_override_unresolved", "個人半日勤務の勤務枠を特定できません。条件の勤務時間を設定してください", staff_id, day)
            elif staff_id in staffs:
                actual = sum(_shift_minutes(templates[value]) for value in template_ids if value in templates)
                limit = _daily_work_limit_minutes(staffs[staff_id], workday, templates, settings, template_id)
                if actual > limit or (not rows and status.shift_template_id != template_id):
                    report("half_day_override_mismatch", "個人半日勤務の指定枠または勤務時間上限を満たしていません", staff_id, day)
    for staff_id, day in hard_working["days"] | set(half_day_overrides):
        if staff_id in staffs and not active_on(staffs[staff_id], day):
            continue
        status = statuses.get((staff_id, day))
        if status is None or status.status != "working":
            report("hard_work_request_missing", "必須の勤務希望または個人半日勤務が反映されていません", staff_id, day)
    for staff_id, day, template_id in hard_working["shifts"]:
        if staff_id in staffs and not active_on(staffs[staff_id], day):
            continue
        # Generation requires a position assignment for a shift-specific request.
        if not any(row.shift_template_id == template_id for row in by_day[(staff_id, day)]):
            report("hard_shift_request_missing", "必須の勤務枠指定に対応する担当が配置されていません", staff_id, day)

    from .operational_constraints import daily_staffing_rules, hard_pair_constraint, input_calendar_dates
    dates = input_calendar_dates(session, start, end) if period_dates is None else period_dates
    working = {(staff_id, day): int((status := statuses.get((staff_id, day))) is not None and status.status == "working")
               for staff_id in staffs for day in dates}
    for day, label, value, relation, bound in daily_staffing_rules(list(staffs.values()), dates, working, workdays, settings):
        if not (value >= bound if relation == "ge" else value <= bound):
            report("daily_staffing_mismatch", f"{label}が基準を満たしていません（実績 {value}人、{'下限' if relation == 'ge' else '上限'} {bound}人）", day=day)
    if hard_pair_rules is None:
        hard_pair_rules = session.query(StaffPairRule).filter_by(strength="hard").all()
    for rule in hard_pair_rules:
        if rule.first_staff_id not in staffs or rule.second_staff_id not in staffs:
            continue
        if rule.kind not in {"avoid_same_day_assignment", "require_same_day_assignment", "require_same_day_work"}:
            continue
        for day in dates:
            present = []
            for staff_id in (rule.first_staff_id, rule.second_staff_id):
                status = statuses.get((staff_id, day))
                present.append(int(status is not None and status.status == "working") if rule.kind == "require_same_day_work"
                               else int(bool(by_day[(staff_id, day)])))
            if not hard_pair_constraint(rule.kind, *present):
                label = {"avoid_same_day_assignment": "同日の担当配置禁止", "require_same_day_assignment": "同日の担当配置必須", "require_same_day_work": "同日の出勤必須"}[rule.kind]
                report("hard_staff_pair_mismatch", f"{staffs[rule.second_staff_id].name}との「{label}」を満たしていません", rule.first_staff_id, day)

    config = candidate.solve_run.config_json or {}
    input_snapshot = config.get("input_snapshot") or {}
    try:
        if "locked_assignments" in input_snapshot:
            locked_rows = [SimpleNamespace(**{**row, "work_date": date.fromisoformat(row["work_date"])})
                           for row in input_snapshot["locked_assignments"]]
        else:
            locked_rows = _load_locked_assignments(session, input_snapshot.get("locked_assignment_ids") or [])
    except (ValueError, TypeError, KeyError):
        report("locked_assignment_unresolved", "固定担当の作成時記録を特定できません。固定条件を確認して再作成してください")
        return errors
    if policies is None:
        policies = session.query(RotationPolicy).filter_by(enabled=True).order_by(RotationPolicy.id).all()
    queues = {}
    for policy in policies:
        if not any(demand.position_id == policy.position_id for demand in demands):
            continue
        snapshot = (config.get("rotation_queue_snapshot") or {}).get(str(policy.id))
        if not isinstance(snapshot, dict) or not snapshot.get("queue_staff_ids"):
            report("rotation_assignment_unverified", f"{policy.name} の交代順の正本記録がありません。条件を確認して再作成してください")
            return errors
        queues[policy.id] = snapshot["queue_staff_ids"]
    planning = {
        "rotation_policies": [policy for policy in policies if policy.id in queues],
        "starting_queues": queues,
        "position_priorities": session.query(PositionPriority).order_by(PositionPriority.position_id, PositionPriority.rank).all(),
        "exceptions": session.query(ScheduleException).filter(ScheduleException.work_date >= start, ScheduleException.work_date < end).all(),
        "position_assignment_rules": session.query(PositionAssignmentRule).filter(
            PositionAssignmentRule.effective_from < end,
            PositionAssignmentRule.effective_to.is_(None) | (PositionAssignmentRule.effective_to >= start),
        ).order_by(PositionAssignmentRule.id).all(),
    }
    checks = AssignmentChecks()
    failures, next_queues, reasons = _prepare_assignment_locks(checks, demands, values, planning, locked_rows)
    for message in failures:
        report("deterministic_assignment_unverified", f"{message}。条件を変更して再作成してください")
    for (staff_id, demand_id), (category, _) in reasons.items():
        if not values.get((staff_id, demand_id)):
            demand = next(row for row in demands if row.id == demand_id)
            label = {"fixed": "固定担当", "locked": "固定した担当", "priority": "必須の担当優先順", "rotation": "交代順", "position_rule": "期間付き担当"}[category]
            report("deterministic_assignment_mismatch", f"{label}で必要な配置と一致しません。手編集ではなく条件を変更して再作成してください", staff_id, demand.work_date)
    if not checks.valid and not failures and not any(row["code"] == "deterministic_assignment_mismatch" for row in errors):
        report("deterministic_assignment_unverified", "固定・優先順・交代順の配置を検証できません。条件を変更して再作成してください")
    score = candidate.score_breakdown_json or {}
    recorded = score.get("deterministic_assignment_snapshot")
    expected = [{"staff_id": staff_id, "demand_id": demand_id, "category": reason[0]}
                for (staff_id, demand_id), reason in sorted(reasons.items())]
    if recorded is not None:
        if recorded != expected:
            report("deterministic_assignment_snapshot_changed", "固定・優先順・交代順の条件が作成時から変わっています。条件を確認して再作成してください")
    elif reasons:
        explanations = {(row.assignment_id, row.category) for row in session.query(AssignmentExplanation).filter(
            AssignmentExplanation.assignment_id.in_([row.id for row in assignments]),
        ).all()}
        for (staff_id, demand_id), (category, _) in reasons.items():
            demand = next(row for row in demands if row.id == demand_id)
            rows = by_slot[(demand.work_date, demand.position_id, demand.shift_template_id)]
            if not any(row.staff_id == staff_id and row.source == category and (row.id, category) in explanations for row in rows):
                report("deterministic_assignment_evidence_missing", "固定・優先順・交代順の元生成配置を証明する記録がありません。条件を確認して再作成してください", staff_id, demand.work_date)
    for policy_id, queue in next_queues.items():
        if (score.get("next_rotation_queues") or {}).get(policy_id) != queue:
            report("rotation_result_queue_mismatch", "実配置と保存済みの次回交代順が一致しません。条件を確認して再作成してください")
    return errors
