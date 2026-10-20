"""Hard operational rules shared by generation and candidate validation."""

from collections import defaultdict
from datetime import date, timedelta


def operational_message(code, staff_name, day, *, actual=None, bound=None):
    descriptions = {
        "prior_status_missing": "前期間から休暇・連勤を確認するための勤務実績が不足しています",
        "prior_time_history_missing": "前期間から勤務時間・休息を確認するための勤務枠と時間の実績が不足しています",
        "prior_time_history_invalid": "前期間の勤務時間の記録に矛盾または不正な値があります",
        "candidate_status_missing_or_duplicate": "出勤・休暇の記録が不足しているか、重複しています",
        "candidate_work_time_unresolved": "勤務時間を確認できる勤務枠が未設定、または出勤・担当の記録が一致していません",
        "current_time_history_missing": "勤務時間の確認に必要な日別勤務設定が不足しています",
        "current_status_missing": "休暇・連勤の確認に必要な日別勤務設定が不足しています",
        "daily_work_minutes_exceeded": "1日の勤務時間上限を超えています",
        "weekly_work_minutes_exceeded": "連続7日間の勤務時間上限を超えています",
        "period_work_minutes_exceeded": "対象期間の勤務時間上限を超えています",
        "period_work_minutes_shortage": "対象期間の勤務時間下限を満たしていません",
        "weekly_min_off_days": "連続7日間の最低休暇日数を満たしていません",
        "max_consecutive_work_days": "連続勤務日数の上限を超えています",
        "max_consecutive_off_days": "連続休暇日数の上限を超えています",
        "monthly_required_off_days": "対象期間の休暇日数が指定日数と一致していません",
        "monthly_min_off_days": "対象期間の最低休暇日数を満たしていません",
        "monthly_max_off_days": "対象期間の休暇日数上限を超えています",
        "shift_overlap_or_minimum_rest": "勤務枠が重複しているか、勤務間の最低休息時間を満たしていません",
        "prior_shift_overlap_or_minimum_rest": "前期間の勤務枠と重複しているか、前期間からの最低休息時間を満たしていません",
        "monthly_consecutive_off_missing": "対象期間に必要な2日連続の休暇がありません",
    }
    message = f"{staff_name}：{day.isoformat()}、{descriptions[code]}"
    if actual is not None and bound is not None:
        unit = "分" if "minutes" in code else "日"
        comparison = "下限" if code in {"period_work_minutes_shortage", "weekly_min_off_days", "monthly_min_off_days"} else "指定" if code == "monthly_required_off_days" else "上限"
        message += f"（実績 {actual:g}{unit}、{comparison} {bound:g}{unit}）"
    return message


def active_on(staff, day):
    return not ((staff.active_from and day < staff.active_from)
                or (staff.active_to and day > staff.active_to))


def hard_pair_constraint(kind, first, second):
    if kind in {"avoid_same_weekday_off", "avoid_same_day_off", "avoid_same_day_assignment"}:
        return first + second <= 1
    if kind in {"require_same_day_work", "require_same_day_assignment"}:
        return first == second
    raise ValueError("未対応の職員組み合わせ条件です")


def daily_staffing_rules(staffs, dates, working, workdays, settings):
    from .rule_config_utils import is_special_staffing_date
    for day in dates:
        active = [staff for staff in staffs if active_on(staff, day)]
        total = sum(working[(staff.id, day)] for staff in active)
        rule = workdays.get(day)
        if rule is not None:
            for bound, relation in ((rule.min_staff_count, "ge"), (rule.max_staff_count, "le")):
                if bound is not None:
                    yield day, "日別の出勤人数", total, relation, int(bound)
            for ranges, relation in ((rule.group_min_counts, "ge"), (rule.group_max_counts, "le")):
                for group, bound in (ranges or {}).items():
                    count = sum(working[(staff.id, day)] for staff in active if staff.staff_group == group)
                    yield day, f"日別の{group}の出勤人数", count, relation, int(bound)
        if is_special_staffing_date(day, settings):
            for key, relation in (("weekend_min_staff_count", "ge"), ("weekend_max_staff_count", "le")):
                if settings.get(key) is not None:
                    yield day, "土日祝の出勤人数", total, relation, int(settings[key])
            if settings.get("weekend_preferred_min_advantage"):
                first = sum(working[(staff.id, day)] for staff in active if staff.id in (settings.get("weekend_preferred_staff_ids") or []))
                second = sum(working[(staff.id, day)] for staff in active if staff.id in (settings.get("weekend_comparison_staff_ids") or []))
                yield day, "土日祝の指定グループ間の人数差", first - second, "ge", int(settings["weekend_preferred_min_advantage"])
            if settings.get("weekend_coverage_max_working") is not None:
                count = sum(working[(staff.id, day)] for staff in active if staff.id in (settings.get("weekend_coverage_staff_ids") or []))
                yield day, "土日祝の指定メンバー出勤人数", count, "le", int(settings["weekend_coverage_max_working"])


def upper_limit(settings, staff, key):
    if key in {"max_weekly_work_minutes", "max_monthly_work_minutes"}:
        individual = getattr(staff, key, None)
        return individual if individual is not None else settings.get(key)
    values = [value for value in (settings.get(key), getattr(staff, key, None)) if value is not None]
    return min(values) if values else None


def input_calendar_dates(session, start, end):
    from .models import Demand, WorkdayRule
    from .solver import _load_availability
    unavailable, request_off, half_days, request_work, working = _load_availability(session, start, end)
    dates = {row.work_date for model in (Demand, WorkdayRule)
             for row in session.query(model).filter(model.work_date >= start, model.work_date < end).all()}
    for keys in (unavailable["days"], unavailable["shifts"], request_off, half_days,
                 request_work, working["days"], working["shifts"]):
        dates.update(key[1] for key in keys)
    return sorted(dates)


def input_calendar_errors(staffs, dates, start, end, settings):
    """Require unknown dates only when a hard rule must include those dates."""
    known = set(dates)
    period = {start + timedelta(days=offset) for offset in range((end - start).days)}
    errors = []
    from .rule_config_utils import staff_in_setting_scope
    for staff in staffs:
        needed = set()
        period_keys = ("monthly_holiday_exact_days", "monthly_holiday_min_days", "monthly_holiday_max_days")
        period_rule = (
            staff_in_setting_scope(staff.id, settings, "monthly_holiday")
            and any(settings.get(key) is not None for key in period_keys)
        )
        period_rule |= upper_limit(settings, staff, "max_monthly_work_minutes") is not None
        period_rule |= (staff.min_monthly_work_minutes if staff.min_monthly_work_minutes is not None
                        else settings.get("min_monthly_work_minutes")) is not None
        if len(period) >= 28:
            period_rule |= any(settings.get(key) is not None for key in
                               ("monthly_required_off_days", "monthly_min_off_days", "monthly_max_off_days"))
        if (len(period) >= 14 and settings.get("require_monthly_consecutive_off")
                and settings.get("monthly_consecutive_off_strength", "hard") == "hard"
                and staff_in_setting_scope(staff.id, settings, "require_monthly_consecutive_off")):
            period_rule = True
        if period_rule:
            needed.update(period)
        sizes = []
        if settings.get("weekly_min_off_days") or upper_limit(settings, staff, "max_weekly_work_minutes") is not None:
            sizes.append((7, bool(settings.get("require_prior_period_continuity"))))
        for key in ("max_consecutive_work_days", "max_consecutive_off_days"):
            if settings.get(key) and staff_in_setting_scope(staff.id, settings, key):
                sizes.append((int(settings[key]) + 1, key == "max_consecutive_off_days" or bool(settings.get("require_prior_period_continuity"))))
        for size, include_boundary in sizes:
            for last in dates:
                first = last - timedelta(days=size - 1)
                if first < start and not include_boundary:
                    continue
                needed.update(last - timedelta(days=offset) for offset in range(size))
        for day in sorted((needed & period) - known):
            if active_on(staff, day):
                errors.append({"severity": "error", "code": "required_input_date_missing", "staff_id": staff.id,
                               "work_date": day.isoformat(),
                               "message": f"{staff.name}：{day} の勤務入力がなく、勤務時間・休暇・連勤の条件を確認できません"})
    return errors


def template_slot(template):
    if template is None:
        return None
    start = template.start_time.hour * 60 + template.start_time.minute
    end = template.end_time.hour * 60 + template.end_time.minute
    if end <= start:
        end += 1440
    return {"start_minute": start, "end_minute": end,
            "work_minutes": max(0, end - start - template.break_minutes),
            "shift_template_id": template.id}


def candidate_time_rows(session, candidate_id):
    from .models import Assignment, CandidateStaffStatus

    assignments = defaultdict(list)
    for row in session.query(Assignment).filter(Assignment.candidate_id == candidate_id).all():
        assignments[(row.staff_id, row.work_date)].append(row)
    statuses = defaultdict(list)
    for row in session.query(CandidateStaffStatus).filter(CandidateStaffStatus.candidate_id == candidate_id).all():
        statuses[(row.staff_id, row.work_date)].append(row)
    result = []
    for staff_id, day in sorted(assignments.keys() | statuses.keys()):
        key = (staff_id, day)
        rows = statuses[key]
        status = rows[0] if len(rows) == 1 else None
        slots = None
        if status is not None and status.status == "off" and not assignments[key]:
            slots = []
        elif status is not None and status.status == "working":
            templates = [row.shift_template for row in assignments[key]] if assignments[key] else [status.shift_template]
            if all(template is not None for template in templates):
                slots = [template_slot(template) for template in templates]
        result.append({"staff_id": staff_id, "work_date": day.isoformat(),
                       "status": status.status if status is not None else None,
                       "slots": slots, "source_candidate_id": candidate_id})
    return result


def history_days(settings, staffs):
    days = int(settings.get("max_consecutive_off_days") or 0)
    if not settings.get("require_prior_period_continuity"):
        return days
    days = max(days, 1, int(settings.get("max_consecutive_work_days") or 0),
               6 if settings.get("weekly_min_off_days") else 0)
    if any(upper_limit(settings, staff, "max_weekly_work_minutes") is not None for staff in staffs):
        days = max(days, 6)
    rest = int(settings.get("min_rest_minutes") or 0)
    if rest:
        days = max(days, (rest + 1439) // 1440 + 1)
    return days


def boundary_time_days(settings, staff):
    if not settings.get("require_prior_period_continuity"):
        return 0
    from .rule_config_utils import staff_in_setting_scope
    has_weekly_time_rule = upper_limit(settings, staff, "max_weekly_work_minutes") is not None
    scoped_keys = set(settings.get("profile_scoped_setting_keys") or [])
    has_binary_continuity_rule = (
        bool(settings.get("weekly_min_off_days"))
        or "max_consecutive_work_days" not in scoped_keys
        or (
            bool(settings.get("max_consecutive_work_days"))
            and staff_in_setting_scope(staff.id, settings, "max_consecutive_work_days")
        )
    )
    days = 6 if has_weekly_time_rule else int(has_binary_continuity_rule)
    rest = int(settings.get("min_rest_minutes") or 0)
    return max(days, (rest + 1439) // 1440 + 1) if rest else days


def load_time_history(rows, *, staffs=None, start=None, settings=None):
    result = {}
    staff_by_id = {staff.id: staff for staff in staffs} if staffs is not None else None
    for row in rows or []:
        key = (int(row["staff_id"]), date.fromisoformat(row["work_date"]))
        if staff_by_id is not None:
            staff = staff_by_id.get(key[0])
            if (staff is None or not active_on(staff, start) or not active_on(staff, key[1])
                    or not 1 <= (start - key[1]).days <= boundary_time_days(settings, staff)):
                continue
        if key in result:
            raise ValueError("duplicate prior time history")
        slots = row.get("slots")
        if slots is not None:
            for slot in slots:
                slot_start, slot_end, minutes = (slot.get(name) for name in ("start_minute", "end_minute", "work_minutes"))
                if (not all(isinstance(value, int) and not isinstance(value, bool) for value in (slot_start, slot_end, minutes))
                        or not 0 <= slot_start < 1440 or not slot_start < slot_end <= slot_start + 1440
                        or not 0 <= minutes <= slot_end - slot_start):
                    raise ValueError("invalid prior time history")
            if row.get("status") == "off" and slots or row.get("status") == "working" and not slots:
                raise ValueError("prior status/time mismatch")
        result[key] = row
    return result


def required_history_errors(staffs, start, settings, prior_status, prior_time):
    errors = []
    continuity = bool(settings.get("require_prior_period_continuity"))
    from .rule_config_utils import staff_in_setting_scope
    for staff in staffs:
        if not active_on(staff, start):
            continue
        binary_days = int(settings.get("max_consecutive_off_days") or 0)
        if continuity:
            if staff_in_setting_scope(staff.id, settings, "max_consecutive_work_days"):
                binary_days = max(binary_days, int(settings.get("max_consecutive_work_days") or 0))
            binary_days = max(binary_days, 6 if settings.get("weekly_min_off_days") else 0)
        time_days = boundary_time_days(settings, staff)
        for offset in range(1, max(binary_days, time_days) + 1):
            day = start - timedelta(days=offset)
            if not active_on(staff, day):
                continue
            key = (staff.id, day)
            if offset <= binary_days and prior_status.get(key) not in {"off", "working"}:
                errors.append(("prior_status_missing", staff.id, day))
            if offset <= time_days:
                row = prior_time.get(key)
                if row is None or row.get("slots") is None or row.get("status") != prior_status.get(key):
                    errors.append(("prior_time_history_missing", staff.id, day))
    return errors


def minute_rules(staffs, dates, settings, minutes, prior_time):
    """Yield (code, staff, date, expression, relation, bound) for both consumers."""
    if not dates:
        return
    start = dates[0]
    for staff in staffs:
        active_dates = [day for day in dates if active_on(staff, day)]
        if not active_dates:
            continue
        daily = upper_limit(settings, staff, "max_daily_work_minutes")
        for day in active_dates:
            if daily is not None:
                yield "daily_work_minutes_exceeded", staff.id, day, minutes[(staff.id, day)], "le", daily
        weekly = upper_limit(settings, staff, "max_weekly_work_minutes")
        if weekly is not None:
            for last in dates:
                window = [last - timedelta(days=offset) for offset in range(6, -1, -1)]
                if window[0] < start and not settings.get("require_prior_period_continuity"):
                    continue
                values = []
                for day in window:
                    if not active_on(staff, day):
                        continue
                    if day >= start:
                        if (staff.id, day) not in minutes:
                            raise ValueError(operational_message("current_time_history_missing", staff.name, day))
                        values.append(minutes[(staff.id, day)])
                    else:
                        row = prior_time.get((staff.id, day))
                        if row is None or row.get("slots") is None:
                            break
                        values.append(sum(slot["work_minutes"] for slot in row["slots"]))
                else:
                    yield "weekly_work_minutes_exceeded", staff.id, last, sum(values), "le", weekly
        total = sum(minutes[(staff.id, day)] for day in active_dates)
        maximum = upper_limit(settings, staff, "max_monthly_work_minutes")
        if maximum is not None:
            yield "period_work_minutes_exceeded", staff.id, start, total, "le", maximum
        minimum = (staff.min_monthly_work_minutes if staff.min_monthly_work_minutes is not None
                   else settings.get("min_monthly_work_minutes"))
        if minimum is not None:
            yield "period_work_minutes_shortage", staff.id, start, total, "ge", minimum


def binary_rules(staffs, dates, settings, off, prior_status):
    if not dates:
        return
    start = dates[0]
    from .rule_config_utils import staff_in_setting_scope
    for staff in staffs:
        active_dates = [day for day in dates if active_on(staff, day)]
        windows = []
        if settings.get("weekly_min_off_days"):
            windows.append((7, "weekly_min_off_days", "ge", int(settings["weekly_min_off_days"])))
        if (settings.get("max_consecutive_work_days")
                and staff_in_setting_scope(staff.id, settings, "max_consecutive_work_days")):
            windows.append((int(settings["max_consecutive_work_days"]) + 1, "max_consecutive_work_days", "ge", 1))
        if settings.get("max_consecutive_off_days"):
            windows.append((int(settings["max_consecutive_off_days"]) + 1, "max_consecutive_off_days", "le", int(settings["max_consecutive_off_days"])))
        for size, code, relation, bound in windows:
            for last in dates:
                window = [last - timedelta(days=offset) for offset in range(size - 1, -1, -1)]
                if (window[0] < start and code != "max_consecutive_off_days"
                        and not settings.get("require_prior_period_continuity")):
                    continue
                if not all(active_on(staff, day) for day in window):
                    continue
                values = []
                for day in window:
                    if day >= start:
                        if (staff.id, day) not in off:
                            raise ValueError(operational_message("current_status_missing", staff.name, day))
                        values.append(off[(staff.id, day)])
                    elif (staff.id, day) in prior_status:
                        values.append(int(prior_status[(staff.id, day)] == "off"))
                    else:
                        break
                else:
                    yield code, staff.id, last, sum(values), relation, bound
        # Preserve the existing monthly applicability and exact-over-range policy.
        if len(dates) >= 28:
            exact = settings.get("monthly_required_off_days")
            total = sum(off[(staff.id, day)] for day in active_dates)
            if exact is not None:
                if len(active_dates) >= 28:
                    yield "monthly_required_off_days", staff.id, start, total, "eq", int(exact)
            elif active_dates:
                for key, relation in (("monthly_min_off_days", "ge"), ("monthly_max_off_days", "le")):
                    if settings.get(key) is not None:
                        yield key, staff.id, start, total, relation, int(settings[key])


def slots_conflict(first_day, first, second_day, second, rest):
    first_start = first_day.toordinal() * 1440 + first["start_minute"]
    first_end = first_day.toordinal() * 1440 + first["end_minute"]
    second_start = second_day.toordinal() * 1440 + second["start_minute"]
    second_end = second_day.toordinal() * 1440 + second["end_minute"]
    if first_start > second_start:
        first_start, second_start = second_start, first_start
        first_end, second_end = second_end, first_end
    return second_start - first_end < rest


def validate_operational(session, candidate, staffs, dates, settings, prior_status, workday_rules):
    from itertools import combinations
    from .models import ShiftTemplate
    from .solver import _daily_work_limit_minutes, _load_availability

    errors = []

    staff_names = {staff.id: staff.name for staff in staffs}

    def report(code, staff_id, day, severity="error", *, actual=None, bound=None):
        errors.append({"severity": severity, "code": code, "staff_id": staff_id,
                       "work_date": day.isoformat(),
                       "message": operational_message(code, staff_names.get(staff_id, "勤務表"), day, actual=actual, bound=bound)})

    if not dates:
        return errors
    _, _, half_day_overrides, _, _ = _load_availability(session, dates[0], dates[-1] + timedelta(days=1))
    try:
        prior_time = load_time_history(
            (candidate.solve_run.config_json or {}).get("prior_time_history"),
            staffs=staffs, start=dates[0], settings=settings,
        ) if settings.get("require_prior_period_continuity") else {}
    except (ValueError, TypeError, KeyError):
        report("prior_time_history_invalid", None, dates[0])
        prior_time = {}
    templates = {row.id: row for row in session.query(ShiftTemplate).all()}
    for code, staff_id, day in required_history_errors(staffs, dates[0], settings, prior_status, prior_time):
        report(code, staff_id, day)
    rows = {(row["staff_id"], date.fromisoformat(row["work_date"])): row
            for row in candidate_time_rows(session, candidate.id)}
    minutes, off = {}, {}
    time_valid_staff, status_valid_staff = [], []
    for staff in staffs:
        valid_time = valid_status = True
        slots = []
        for day in dates:
            key = (staff.id, day)
            if not active_on(staff, day):
                minutes[key] = off[key] = 0
                continue
            row = rows.get(key)
            if row is None or row.get("status") not in {"working", "off"}:
                report("candidate_status_missing_or_duplicate", staff.id, day)
                valid_status = valid_time = False
                continue
            off[key] = int(row["status"] == "off")
            if row["slots"] is None:
                report("candidate_work_time_unresolved", staff.id, day)
                valid_time = False
                continue
            minutes[key] = sum(slot["work_minutes"] for slot in row["slots"])
            daily_limit = _daily_work_limit_minutes(
                staff, workday_rules.get(day), templates, settings,
                override_shift_template_id=half_day_overrides.get(key),
            )
            if minutes[key] > daily_limit:
                report("daily_work_minutes_exceeded", staff.id, day, actual=minutes[key], bound=daily_limit)
            slots.extend((day, slot) for slot in row["slots"])
        for first, second in combinations(slots, 2):
            if slots_conflict(first[0], first[1], second[0], second[1], int(settings.get("min_rest_minutes") or 0)):
                report("shift_overlap_or_minimum_rest", staff.id, second[0])
        for (staff_id, prior_day), row in prior_time.items():
            if staff_id != staff.id or prior_day >= dates[0]:
                continue
            for prior_slot in row.get("slots") or []:
                for day, slot in slots:
                    if slots_conflict(prior_day, prior_slot, day, slot, int(settings.get("min_rest_minutes") or 0)):
                        report("prior_shift_overlap_or_minimum_rest", staff.id, day)
        if valid_time:
            time_valid_staff.append(staff)
        if valid_status:
            status_valid_staff.append(staff)
    for rules in (minute_rules(time_valid_staff, dates, settings, minutes, prior_time),
                  binary_rules(status_valid_staff, dates, settings, off, prior_status)):
        for code, staff_id, day, value, relation, bound in rules:
            valid = value <= bound if relation == "le" else value >= bound if relation == "ge" else value == bound
            if not valid:
                if code == "max_consecutive_work_days":
                    report(code, staff_id, day, actual=int(settings[code]) + 1, bound=int(settings[code]))
                else:
                    report(code, staff_id, day, actual=value, bound=bound)
    if settings.get("require_monthly_consecutive_off") and len(dates) >= 14:
        severity = "error" if str(settings.get("monthly_consecutive_off_strength", "hard")).lower() == "hard" else "warning"
        exempt_ids = {int(value) for value in settings.get("consecutive_off_exempt_staff_ids") or []}
        for staff in status_valid_staff:
            from .rule_config_utils import staff_in_setting_scope
            if (staff.id in exempt_ids
                    or not staff_in_setting_scope(staff.id, settings, "require_monthly_consecutive_off")):
                continue
            active_dates = [day for day in dates if active_on(staff, day)]
            if not active_dates:
                continue
            if not any((second - first).days == 1 and off[(staff.id, first)] and off[(staff.id, second)]
                       for first, second in zip(active_dates, active_dates[1:])):
                report("monthly_consecutive_off_missing", staff.id, dates[0], severity)
    return errors
