from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .holiday_calendar import is_weekend_or_japanese_holiday
from .models import (
    Assignment,
    CandidateSchedule,
    CandidateStaffStatus,
    Demand,
    RotationPolicy,
    ScheduleConfirmation,
    RuleConfig,
    ShiftTemplate,
    Staff,
    StaffPairRule,
    WorkdayRule,
)
from .periods import build_active_settings
from .rotation_queue import resolve_rotation_queue, validate_rotation_snapshot
from .rule_config_utils import (
    PAID_LEAVE_HALF_DAY_WORK_KIND,
    fixed_weekday_off_value,
    is_allowed_weekday_off,
    is_special_staffing_date,
    day_category,
    day_category_rule,
    is_position_prohibited,
    staff_in_setting_scope,
    saturday_sunday_pairs,
)


def _status_uses_half_day_template(status: CandidateStaffStatus) -> bool:
    return status.shift_template is not None and status.shift_template.shift_type == "half_day"


def _template_minutes(template) -> int:
    start = template.start_time.hour * 60 + template.start_time.minute
    end = template.end_time.hour * 60 + template.end_time.minute
    return (end - start if end > start else end - start + 1440) - template.break_minutes


def _load_prior_statuses(config_json: dict) -> dict[tuple[int, date], str]:
    prior_by_key: dict[tuple[int, date], str] = {}
    for row in config_json.get("prior_statuses") or []:
        if isinstance(row, dict):
            staff_id = row.get("staff_id")
            work_date = row.get("work_date")
            status = row.get("status")
        elif isinstance(row, (list, tuple)) and len(row) == 3:
            staff_id, work_date, status = row
        else:
            continue
        if status not in {"working", "off"}:
            continue
        try:
            parsed_date = work_date if isinstance(work_date, date) else date.fromisoformat(str(work_date))
            prior_by_key[(int(staff_id), parsed_date)] = status
        except (TypeError, ValueError):
            continue
    return prior_by_key


def validate_candidate(
    candidate_id: int,
    session: Session,
    *,
    settings_override: dict | None = None,
) -> dict:
    """Return the current, post-edit validation used by UI and confirmation."""
    candidate = session.get(CandidateSchedule, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    from .validation_snapshot import confirmed_validation
    historical = confirmed_validation(session, candidate)
    if historical is not None:
        return historical
    run = candidate.solve_run
    config_json = run.config_json or {}
    end_date_value = config_json.get("end_date") or config_json.get("end_date_exclusive")
    if config_json.get("start_date") and end_date_value:
        start = date.fromisoformat(config_json["start_date"])
        end = date.fromisoformat(end_date_value)
    else:
        year, month = (int(value) for value in run.target_month.split("-"))
        start = date(year, month, 1)
        end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    warnings: list[dict] = []

    covered: dict[tuple[date, int, int], int] = {}
    for assignment in (
        session.query(Assignment).filter(Assignment.candidate_id == candidate_id).all()
    ):
        key = (assignment.work_date, assignment.position_id, assignment.shift_template_id)
        covered[key] = covered.get(key, 0) + 1
    demands = session.query(Demand).filter(Demand.work_date >= start, Demand.work_date < end).all()
    for demand in demands:
        actual = covered.get((demand.work_date, demand.position_id, demand.shift_template_id), 0)
        minimum = demand.min_count if demand.min_count is not None else demand.required_count
        maximum = demand.max_count if demand.max_count is not None else demand.required_count
        position_name = demand.position.display_name or demand.position.name
        if actual < minimum:
            warnings.append(
                {
                    "severity": "error",
                    "code": "coverage_shortage",
                    "work_date": demand.work_date.isoformat(),
                    "message": f"{demand.work_date.isoformat()} {position_name} が {minimum - actual} 人不足しています",
                }
            )
        elif actual > maximum:
            warnings.append(
                {
                    "severity": "error",
                    "code": "coverage_excess",
                    "work_date": demand.work_date.isoformat(),
                    "message": f"{demand.work_date.isoformat()} {position_name} が {actual - maximum} 人多くなっています",
                }
            )

    config = session.query(RuleConfig).filter(RuleConfig.name == "active").one_or_none()
    if settings_override is not None:
        settings = build_active_settings(settings_override)
    else:
        from .models import RuleProfile
        if session.query(RuleProfile.id).filter(RuleProfile.enabled.is_(True)).first():
            from .rule_profiles import effective_rule_config
            settings = effective_rule_config(session)
        else:
            settings = build_active_settings(config.config_json if config else {})
    from .operational_constraints import input_calendar_dates, input_calendar_errors
    period_dates = input_calendar_dates(session, start, end)
    from .assignment_validation import validate_assignments
    warnings.extend(validate_assignments(session, candidate, start, end, settings))
    assignments = session.query(Assignment).filter(Assignment.candidate_id == candidate_id).all()
    for demand in demands:
        for group in settings.get("position_staffing_groups") or []:
            if group["position_id"] != demand.position_id:
                continue
            selected = {
                row.staff_id for row in assignments
                if row.work_date == demand.work_date
                and row.position_id == demand.position_id
                and row.shift_template_id == demand.shift_template_id
            }
            if selected and len(selected.intersection(group["staff_ids"])) < group["min_count"]:
                warnings.append({
                    "severity": "error", "code": "position_staffing_group_shortage",
                    "position_id": demand.position_id, "work_date": demand.work_date.isoformat(),
                    "message": f"{demand.work_date} の担当グループの最低人数を満たしていません",
                })
    for assignment in session.query(Assignment).filter(Assignment.candidate_id == candidate_id).all():
        if is_position_prohibited(assignment.position_id, assignment.work_date, settings):
            warnings.append({
                "severity": "error", "code": "position_day_category_prohibited",
                "work_date": assignment.work_date.isoformat(), "position_id": assignment.position_id,
                "message": f"{assignment.work_date.isoformat()} はこの担当を配置できません",
            })
    prior_status_by_key = _load_prior_statuses(config_json)
    counted = settings["holiday_counted_work_kinds"]
    holiday_by_staff: dict[int, float] = {}
    paid_leave_adjustment_by_staff: dict[int, float] = {}
    candidate_statuses = (
        session.query(CandidateStaffStatus)
        .filter(CandidateStaffStatus.candidate_id == candidate_id)
        .all()
    )
    workday_rule_by_date = {
        row.work_date: row
        for row in (
            session.query(WorkdayRule)
            .filter(WorkdayRule.work_date >= start, WorkdayRule.work_date < end)
            .all()
        )
    }
    for status in candidate_statuses:
        counted_work_kind = status.work_kind
        if (
            status.work_kind in {"position", PAID_LEAVE_HALF_DAY_WORK_KIND}
            and _status_uses_half_day_template(status)
        ):
            counted_work_kind = "half_day"
        elif status.work_kind == PAID_LEAVE_HALF_DAY_WORK_KIND:
            counted_work_kind = "half_day"
        holiday_by_staff[status.staff_id] = holiday_by_staff.get(status.staff_id, 0.0) + float(
            counted.get(counted_work_kind, 0.0)
        )
        if status.work_kind == PAID_LEAVE_HALF_DAY_WORK_KIND:
            paid_leave_adjustment_by_staff[status.staff_id] = (
                paid_leave_adjustment_by_staff.get(status.staff_id, 0.0) + 0.5
            )
    active_staff = (
        session.query(Staff)
        .filter(
            (Staff.active_from.is_(None) | (Staff.active_from < end)),
            (Staff.active_to.is_(None) | (Staff.active_to >= start)),
        )
        .all()
    )
    exact = settings.get("monthly_holiday_exact_days")
    calendar_errors = input_calendar_errors(active_staff, period_dates, start, end, settings)
    if calendar_errors:
        return {"blocking": True, "warnings": [*warnings, *calendar_errors],
                "holiday_counts": [], "additional_weekday_offs": [], "rotation_balances": []}
    minimum_holidays = settings.get("monthly_holiday_min_days")
    maximum_holidays = settings.get("monthly_holiday_max_days")
    holiday_counts = []
    for staff in active_staff:
        if not staff_in_setting_scope(staff.id, settings, "monthly_holiday"):
            continue
        actual = holiday_by_staff.get(staff.id, 0.0)
        paid_leave_adjustment = paid_leave_adjustment_by_staff.get(staff.id, 0.0)
        holiday_counts.append(
            {
                "staff_id": staff.id,
                "staff_name": staff.name,
                "equivalent_days": actual,
                "paid_leave_adjustment": paid_leave_adjustment,
            }
        )
        exact_target = float(exact) if exact is not None else None
        exact_with_paid_leave = (
            exact_target is not None
            and staff.paid_leave_eligible
            and paid_leave_adjustment == 0.5
            and actual == exact_target + 0.5
        )
        out_of_range = False
        if exact is not None and actual != exact_target and not exact_with_paid_leave:
            out_of_range = True
        if minimum_holidays is not None and actual < float(minimum_holidays):
            out_of_range = True
        if maximum_holidays is not None and actual > float(maximum_holidays):
            out_of_range = True
        if out_of_range:
            target = (
                f"{exact:g}日"
                if exact is not None
                else f"{minimum_holidays or 0:g}〜{maximum_holidays or '-'}日"
            )
            warnings.append(
                {
                    "severity": "error",
                    "code": "holiday_equivalent_mismatch",
                    "staff_id": staff.id,
                    "message": f"{staff.name} の休み換算は {actual:g}日です（基準 {target}）",
                }
            )
        if paid_leave_adjustment and not staff.paid_leave_eligible:
            warnings.append(
                {
                    "severity": "error",
                    "code": "paid_leave_not_allowed",
                    "staff_id": staff.id,
                    "message": f"{staff.name} は有休0.5を使えません",
                }
            )
        if paid_leave_adjustment not in {0.0, 0.5}:
            warnings.append(
                {
                    "severity": "error",
                    "code": "invalid_paid_leave_adjustment",
                    "staff_id": staff.id,
                    "message": f"{staff.name} の有休調整は 0.5 日だけ指定できます",
                }
            )
        if paid_leave_adjustment == 0.5 and staff.paid_leave_eligible:
            warnings.append(
                {
                    "severity": "info",
                    "code": "paid_leave_adjustment_applied",
                    "staff_id": staff.id,
                    "message": f"{staff.name} は有休0.5で調整しています",
                }
            )

    status_by_staff_date = {
        (status.staff_id, status.work_date): status for status in candidate_statuses
    }
    staff_by_id = {staff.id: staff for staff in active_staff}
    from .operational_constraints import validate_operational
    warnings.extend(validate_operational(
        session, candidate, active_staff, period_dates, settings,
        prior_status_by_key, workday_rule_by_date,
    ))

    for staff_id in {int(value) for value in settings.get("weekday_no_off_staff_ids") or []}:
        for work_date in period_dates:
            status = status_by_staff_date.get((staff_id, work_date))
            if day_category(work_date, settings) == "weekday" and status is not None and status.status == "off":
                warnings.append({"severity": "error", "code": "profile_weekday_off_forbidden",
                                 "staff_id": staff_id, "work_date": work_date.isoformat(),
                                 "message": f"{work_date.isoformat()} はこの人員を休みにできません"})

    for first_id, second_id in settings.get("off_overlap_pairs") or []:
        for work_date in period_dates:
            first = status_by_staff_date.get((int(first_id), work_date))
            second = status_by_staff_date.get((int(second_id), work_date))
            if first is not None and second is not None and first.status == second.status == "off":
                warnings.append({"severity": "error", "code": "profile_off_overlap",
                                 "work_date": work_date.isoformat(),
                                 "message": f"{work_date.isoformat()} にprofile指定の2名が同時に休みです"})

    for status in candidate_statuses:
        if status.work_date.weekday() != 6 or status.status == "off":
            continue
        if "sunday_half_day" not in set(settings.get("profile_scoped_setting_keys") or []):
            continue
        if not staff_in_setting_scope(status.staff_id, settings, "sunday_half_day"):
            continue
        if status.work_kind != "half_day" and not (
            status.work_kind == "position" and _status_uses_half_day_template(status)
        ):
            warnings.append({"severity": "error", "code": "profile_sunday_half_day_required",
                             "staff_id": status.staff_id, "work_date": status.work_date.isoformat(),
                             "message": f"{status.work_date.isoformat()} の日曜出勤は半日勤務にしてください"})

    assignment_keys = {(row.staff_id, row.work_date, row.position_id) for row in assignments}
    for row in settings.get("weekday_position_assignments") or []:
        for work_date in period_dates:
            if (day_category(work_date, settings) == "weekday"
                    and (int(row["staff_id"]), work_date, int(row["position_id"])) not in assignment_keys):
                warnings.append({"severity": "error", "code": "profile_weekday_position_missing",
                                 "staff_id": int(row["staff_id"]), "work_date": work_date.isoformat(),
                                 "position_id": int(row["position_id"]),
                                 "message": f"{work_date.isoformat()} のprofile指定担当がありません"})

    no_auto_ids = {int(value) for value in settings.get("auto_assignment_prohibited_position_ids") or []}
    for assignment in assignments:
        if assignment.position_id in no_auto_ids and assignment.source == "solver":
            warnings.append({"severity": "error", "code": "profile_auto_assignment_prohibited",
                             "staff_id": assignment.staff_id, "work_date": assignment.work_date.isoformat(),
                             "position_id": assignment.position_id,
                             "message": f"{assignment.work_date.isoformat()} の担当は自動配置できません"})

    def is_active_on_date(staff: Staff, work_date: date) -> bool:
        return not (
            (staff.active_from and work_date < staff.active_from)
            or (staff.active_to and work_date > staff.active_to)
        )

    for pair_rule in session.query(StaffPairRule).filter(StaffPairRule.strength == "hard").all():
        if pair_rule.kind not in {"avoid_same_weekday_off", "avoid_same_day_off"}:
            continue
        for work_date in period_dates:
            if pair_rule.kind == "avoid_same_weekday_off" and work_date.weekday() >= 5:
                continue
            first_status = status_by_staff_date.get((pair_rule.first_staff_id, work_date))
            second_status = status_by_staff_date.get((pair_rule.second_staff_id, work_date))
            if first_status is None or second_status is None:
                continue
            if first_status.status == "off" and second_status.status == "off":
                warnings.append({
                    "severity": "error",
                    "code": "staff_pair_same_day_off",
                    "work_date": work_date.isoformat(),
                    "first_staff_id": pair_rule.first_staff_id,
                    "second_staff_id": pair_rule.second_staff_id,
                    "message": f"{work_date.isoformat()} に同時休み禁止の2名が休みです",
                })

    from .solver import _daily_work_limit_minutes, _load_availability
    _, _, half_day_overrides, _, _ = _load_availability(session, start, end)
    templates = {row.id: row for row in session.query(ShiftTemplate).all()}
    for staff in active_staff:
        for work_date in period_dates:
            workday_rule = workday_rule_by_date.get(work_date)
            if (
                workday_rule is None
                or workday_rule.kind != "half_day"
                or staff.id in (workday_rule.full_day_staff_ids or [])
            ):
                continue
            status = status_by_staff_date.get((staff.id, work_date))
            if status is None or status.status != "working":
                continue
            valid_kind = (
                status.work_kind == "half_day"
                or (
                    status.work_kind == "position"
                    and _status_uses_half_day_template(status)
                )
                or status.work_kind == PAID_LEAVE_HALF_DAY_WORK_KIND
            )
            half_day_limit = _daily_work_limit_minutes(
                staff, workday_rule, templates, settings,
                override_shift_template_id=half_day_overrides.get((staff.id, work_date)),
            )
            valid_duration = (
                status.shift_template is None
                or _template_minutes(status.shift_template) <= half_day_limit
            )
            if not valid_kind or not valid_duration:
                warnings.append(
                    {
                        "severity": "error",
                        "code": "global_half_day_mismatch",
                        "staff_id": staff.id,
                        "work_date": work_date.isoformat(),
                        "message": f"{staff.name} は {work_date.isoformat()} の全体半日勤務として保存されていません",
                    }
                )

    weekday_off_hard_min = settings.get("weekday_off_hard_min")
    weekday_off_hard_max = settings.get("weekday_off_hard_max")
    weekday_off_exempt_staff_ids = {
        int(staff_id) for staff_id in settings.get("weekday_off_exempt_staff_ids") or []
    }
    spread_weight = int(settings.get("weekday_off_spread_weight", 0) or 0)
    target_min = int(settings.get("weekday_off_target_min", 0) or 0)
    target_max = int(settings.get("weekday_off_target_max", 3) or 3)
    for work_date in period_dates:
        if is_special_staffing_date(work_date, settings):
            continue
        counted_staff = [
            staff
            for staff in active_staff
            if is_active_on_date(staff, work_date)
            and staff_in_setting_scope(staff.id, settings, "weekday_off_count")
            and staff.id not in weekday_off_exempt_staff_ids
        ]
        weekday_off_count = sum(
            status_by_staff_date.get((staff.id, work_date)) is not None
            and status_by_staff_date[(staff.id, work_date)].status == "off"
            for staff in counted_staff
        )
        if weekday_off_hard_min is not None and weekday_off_count < int(weekday_off_hard_min):
            warnings.append({
                "severity": "error", "code": "weekday_off_hard_min_not_met",
                "work_date": work_date.isoformat(),
                "message": f"{work_date.isoformat()} の平日休みは {weekday_off_count} 人です（下限 {int(weekday_off_hard_min)} 人）",
            })
        if weekday_off_hard_max is not None and weekday_off_count > int(weekday_off_hard_max):
            warnings.append(
                {
                    "severity": "error",
                    "code": "weekday_off_hard_max_exceeded",
                    "work_date": work_date.isoformat(),
                    "message": (
                        f"{work_date.isoformat()} の平日休みは {weekday_off_count} 人です"
                        f"（上限 {int(weekday_off_hard_max)} 人）"
                    ),
                }
            )
        if spread_weight and (weekday_off_count > target_max or (target_min and weekday_off_count < target_min)):
            warnings.append({"severity": "warning", "code": "weekday_off_target_gap", "work_date": work_date.isoformat(), "message": f"{work_date.isoformat()} の平日休みは {weekday_off_count} 人です（目安 {target_min}〜{target_max} 人）"})

    for work_date in period_dates:
        active_on_date = [staff for staff in active_staff if is_active_on_date(staff, work_date)]
        rules = []
        overall = day_category_rule(settings.get("day_category_staffing_ranges"), work_date, settings)
        if overall:
            rules.append(("day_category_staffing", overall, [
                staff for staff in active_on_date
                if staff_in_setting_scope(staff.id, settings, "day_category_staffing_ranges")
            ]))
        rules.extend(
            ("group_day_category_staffing", row, [staff for staff in active_on_date if staff.staff_group == row.get("staff_group")])
            for row in settings.get("group_day_category_staffing_ranges") or []
            if row.get("day_category") == day_category(work_date, settings)
        )
        rules.extend(
            ("staff_day_category_staffing", row, [staff for staff in active_on_date if staff.id in row["staff_ids"]])
            for row in settings.get("staff_day_category_staffing_ranges") or []
            if row["day_category"] == day_category(work_date, settings)
        )
        for code, row, scoped_staff in rules:
            count = sum(
                (status := status_by_staff_date.get((staff.id, work_date))) is not None and status.status != "off"
                for staff in scoped_staff
            )
            if row.get("min_working") is not None and count < int(row["min_working"]):
                warnings.append({"severity": "error", "code": f"{code}_shortage", "work_date": work_date.isoformat(), "message": f"{work_date.isoformat()} の出勤人数が下限を満たしていません"})
            if row.get("max_working") is not None and count > int(row["max_working"]):
                warnings.append({"severity": "error", "code": f"{code}_excess", "work_date": work_date.isoformat(), "message": f"{work_date.isoformat()} の出勤人数が上限を超えています"})

    max_consecutive_off_days = int(settings.get("max_consecutive_off_days", 0) or 0)
    if max_consecutive_off_days:
        for staff in active_staff:
            consecutive = 0
            prior_dates = [start - timedelta(days=offset) for offset in range(max_consecutive_off_days, 0, -1)]
            for work_date in prior_dates:
                if not is_active_on_date(staff, work_date):
                    consecutive = 0
                    continue
                prior_status = prior_status_by_key.get((staff.id, work_date))
                if prior_status is None:
                    warnings.append({"severity": "error", "code": "consecutive_off_prior_status_missing", "staff_id": staff.id, "work_date": work_date.isoformat(), "message": f"{staff.name} の連続休暇を確認するため、{work_date.isoformat()} の勤務実績が必要です"})
                consecutive = consecutive + 1 if prior_status == "off" else 0
            for work_date in period_dates:
                if not is_active_on_date(staff, work_date):
                    consecutive = 0
                    continue
                status = status_by_staff_date.get((staff.id, work_date))
                consecutive = consecutive + 1 if status is not None and status.status == "off" else 0
                if consecutive > max_consecutive_off_days:
                    warnings.append({"severity": "error", "code": "max_consecutive_off_days_exceeded", "staff_id": staff.id, "work_date": work_date.isoformat(), "message": f"{staff.name} の連続休暇が上限を超えています"})
                    break
    for row in settings.get("staff_weekend_rules") or []:
        staff_id = int(row["staff_id"])
        staff = staff_by_id.get(staff_id)
        if staff is None:
            continue
        fixed_weekday_off = fixed_weekday_off_value(row)
        if fixed_weekday_off is not None:
            for work_date in period_dates:
                if work_date.weekday() != fixed_weekday_off:
                    continue
                status = status_by_staff_date.get((staff_id, work_date))
                if status is not None and status.status != "off":
                    warnings.append({
                        "severity": "error" if row.get("fixed_weekday_off_priority", "hard") == "hard" else "warning",
                        "code": "fixed_weekday_off_mismatch",
                        "staff_id": staff_id,
                        "work_date": work_date.isoformat(),
                        "message": f"{staff.name} の固定曜日休みが守られていません（{work_date.isoformat()}）",
                    })
        for work_date in period_dates:
            if is_special_staffing_date(work_date, settings):
                continue
            status = status_by_staff_date.get((staff_id, work_date))
            if status is None or status.status != "off":
                continue
            if fixed_weekday_off is not None and work_date.weekday() == fixed_weekday_off:
                continue
            if not is_allowed_weekday_off(row, work_date):
                warnings.append({
                    "severity": "error",
                    "code": "weekday_off_not_allowed",
                    "staff_id": staff_id,
                    "work_date": work_date.isoformat(),
                    "message": f"{staff.name} は {work_date.isoformat()} に平日休みを入れられません",
                })
        required_pairs = int(row.get("consecutive_weekend_off_count", 0) or 0)
        minimum_off_days = int(row.get("minimum_weekend_off_days", 0) or 0)
        if required_pairs == 0 and minimum_off_days == 0:
            continue
        prior_saturday = start - timedelta(days=1)
        if (
            minimum_off_days and start.weekday() == 6
            and is_active_on_date(staff, prior_saturday) and is_active_on_date(staff, start)
            and (staff_id, prior_saturday) not in prior_status_by_key
        ):
            warnings.append({
                "severity": "error", "code": "weekend_prior_status_missing",
                "staff_id": staff_id, "work_date": start.isoformat(),
                "message": f"{staff.name} の週末休みを確認するため、{prior_saturday} の勤務実績が必要です",
            })
        actual_pairs = 0
        for saturday, sunday in saturday_sunday_pairs(
            start,
            end,
            prior_dates={work_date for _, work_date in prior_status_by_key},
        ):
            if not is_active_on_date(staff, saturday) or not is_active_on_date(staff, sunday):
                continue
            sunday_status = status_by_staff_date.get((staff_id, sunday))
            if sunday_status is None:
                continue
            if saturday < start:
                prior_key = (staff_id, saturday)
                if prior_key not in prior_status_by_key:
                    continue
                saturday_off = prior_status_by_key[prior_key] == "off"
            else:
                saturday_status = status_by_staff_date.get((staff_id, saturday))
                saturday_off = saturday_status is not None and saturday_status.status == "off"
            sunday_off = sunday_status.status == "off"
            off_days = int(saturday_off) + int(sunday_off)
            if minimum_off_days and off_days < minimum_off_days:
                warnings.append(
                    {
                        "severity": "error",
                        "code": "minimum_weekend_off_days_mismatch",
                        "staff_id": staff_id,
                        "work_date": sunday.isoformat(),
                        "message": (
                            f"{staff.name} の {saturday.isoformat()} / {sunday.isoformat()} は "
                            f"休みが {off_days} 日です（基準 {minimum_off_days} 日）"
                        ),
                    }
                )
            if saturday_off and sunday_off:
                actual_pairs += 1
        if required_pairs and actual_pairs != required_pairs:
            warnings.append({
                "severity": "error" if row.get("consecutive_weekend_off_priority", "hard") == "hard" else "warning",
                "code": "consecutive_weekend_off_mismatch",
                "staff_id": staff_id,
                "message": f"{staff.name} の土日連休は {actual_pairs}回です（基準 {required_pairs}回）",
            })

    coverage_staff_ids = {int(value) for value in settings.get("weekend_coverage_staff_ids") or []}
    coverage_minimum = settings.get("weekend_coverage_min_working")
    coverage_maximum = settings.get("weekend_coverage_max_working")
    coverage_target = settings.get("weekend_coverage_target_working")
    if coverage_minimum is not None or coverage_maximum is not None or coverage_target is not None:
        for work_date_offset in range((end - start).days):
            work_date = start + timedelta(days=work_date_offset)
            if not (
                work_date.weekday() >= 5
                or (
                    settings.get("auto_apply_japanese_holidays", True)
                    and is_weekend_or_japanese_holiday(work_date)
                )
            ):
                continue
            working_count = sum(
                status_by_staff_date.get((staff_id, work_date)) is not None
                and status_by_staff_date[(staff_id, work_date)].status != "off"
                for staff_id in coverage_staff_ids
            )
            if coverage_minimum is not None and working_count < int(coverage_minimum):
                warnings.append({
                    "severity": "error" if settings.get("weekend_coverage_priority", "hard") == "hard" else "warning",
                    "code": "weekend_group_coverage_shortage",
                    "work_date": work_date.isoformat(),
                    "message": f"{work_date.isoformat()} の指定グループ出勤者が {int(coverage_minimum) - working_count}人不足しています",
                })
            if coverage_maximum is not None and working_count > int(coverage_maximum):
                warnings.append({
                    "severity": "error",
                    "code": "weekend_group_coverage_excess",
                    "work_date": work_date.isoformat(),
                    "message": f"{work_date.isoformat()} の指定グループ出勤者が {working_count - int(coverage_maximum)}人多すぎます",
                })
            if coverage_target is not None and working_count != int(coverage_target):
                warnings.append({
                    "severity": "warning",
                    "code": "weekend_group_coverage_target_gap",
                    "work_date": work_date.isoformat(),
                    "message": f"{work_date.isoformat()} の指定グループ出勤者は {working_count}人です（目安 {int(coverage_target)}人）",
                })
    additional_weekday_offs = []
    if settings.get("weekend_adjustment_policy") == "allow_minimum_weekday_offs":
        fixed_weekday_by_staff = {
            int(row["staff_id"]): fixed_weekday_off_value(row)
            for row in settings.get("staff_weekend_rules") or []
        }
        for staff_id in coverage_staff_ids:
            staff = staff_by_id.get(staff_id)
            if staff is None:
                continue
            fixed_weekday = fixed_weekday_by_staff.get(staff_id)
            for work_date in period_dates:
                if is_special_staffing_date(work_date, settings) or work_date.weekday() == fixed_weekday:
                    continue
                status = status_by_staff_date.get((staff_id, work_date))
                if status is not None and status.status == "off":
                    additional_weekday_offs.append({
                        "staff_id": staff_id,
                        "staff_name": staff.name,
                        "work_date": work_date.isoformat(),
                    })
        if additional_weekday_offs:
            warnings.append({
                "severity": "info",
                "code": "minimum_weekday_adjustment_applied",
                "message": f"週末の最低出勤人数を守るため、指定曜日以外の平日休みを {len(additional_weekday_offs)}日配置しました",
                "dates": additional_weekday_offs,
            })
    rotation_balances = []
    policies = session.query(RotationPolicy).filter(RotationPolicy.enabled.is_(True)).all()
    confirmed = session.query(ScheduleConfirmation).filter_by(solve_run_id=run.id).first() is not None
    rotation_snapshot = config_json.get("rotation_queue_snapshot")
    if not confirmed:
        for message in validate_rotation_snapshot(session, policies, start, end, rotation_snapshot):
            warnings.append({"severity": "error", "code": "rotation_queue_changed", "message": message})
    assignments = (
        session.query(Assignment)
        .filter(Assignment.candidate_id == candidate_id)
        .all()
    )
    for policy in policies:
        recorded = (rotation_snapshot or {}).get(str(policy.id))
        if confirmed and recorded is not None:
            member_ids = recorded["queue_staff_ids"]
        else:
            try:
                _, member_ids = resolve_rotation_queue(session, policy, start)
            except ValueError as error:
                warnings.append({"severity": "error", "code": "rotation_queue_invalid", "message": str(error)})
                continue
        counts = {
            staff_id: sum(
                assignment.staff_id == staff_id and assignment.position_id == policy.position_id
                for assignment in assignments
            )
            for staff_id in member_ids
        }
        if not counts:
            continue
        maximum = max(counts.values())
        minimum = min(counts.values())
        rotation_balances.append(
            {
                "rotation_id": policy.id,
                "rotation_name": policy.name,
                "position_name": policy.position.display_name or policy.position.name,
                "counts": counts,
                "maximum": maximum,
                "minimum": minimum,
            }
        )
        if maximum - minimum > 1:
            warnings.append(
                {
                    "severity": "warning",
                    "code": "rotation_balance_gap",
                    "message": f"{policy.name} の担当回数差が {maximum - minimum} 回あります",
                }
            )
    return {
        "blocking": any(row["severity"] == "error" for row in warnings),
        "warnings": warnings,
        "holiday_counts": holiday_counts,
        "additional_weekday_offs": additional_weekday_offs,
        "rotation_balances": rotation_balances,
    }
