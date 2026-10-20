from __future__ import annotations

from datetime import date, timedelta


from .holiday_calendar import is_weekend_or_japanese_holiday
from .periods import scale_decimal_units
from .models import Demand, Position, Staff
from .rule_config_utils import (
    auto_half_day_allowed_weekdays,
    day_category,
    day_category_rule,
    is_position_prohibited,
    fixed_weekday_off_value,
    is_allowed_weekday_off,
    is_auto_half_day_allowed,
    staff_in_setting_scope,
)


WEEKDAY_LABELS = ["月", "火", "水", "木", "金", "土", "日"]


def _is_special_day(work_date: date, settings: dict) -> bool:
    return work_date.weekday() >= 5 or (
        settings.get("auto_apply_japanese_holidays", True)
        and is_weekend_or_japanese_holiday(work_date)
    )


def _has_auto_half_day_template(settings: dict, staff_id: int) -> bool:
    return str(staff_id) in (settings.get("staff_half_day_shift_template_ids") or {})


def _holiday_rule_feasible(*, dates: list[date], settings: dict, staffs: list[Staff]) -> bool:
    from ortools.sat.python import cp_model

    exact_holidays = settings.get("monthly_holiday_exact_days")
    minimum_holidays = settings.get("monthly_holiday_min_days")
    maximum_holidays = settings.get("monthly_holiday_max_days")
    holiday_count_unit = int(settings.get("holiday_count_unit", 2) or 2)
    off_units = scale_decimal_units(
        (settings.get("holiday_counted_work_kinds") or {}).get("off", 1.0),
        holiday_count_unit,
        "holiday_counted_work_kinds.off",
    ) or 0
    half_day_units = scale_decimal_units(
        (settings.get("holiday_counted_work_kinds") or {}).get("half_day", 0.5),
        holiday_count_unit,
        "holiday_counted_work_kinds.half_day",
    ) or 0
    exact_units = scale_decimal_units(exact_holidays, holiday_count_unit, "monthly_holiday_exact_days")
    minimum_units = scale_decimal_units(minimum_holidays, holiday_count_unit, "monthly_holiday_min_days")
    maximum_units = scale_decimal_units(maximum_holidays, holiday_count_unit, "monthly_holiday_max_days")
    paid_leave_units = scale_decimal_units(0.5, holiday_count_unit, "paid_leave_half_day") or 0
    model = cp_model.CpModel()
    status_vars: dict[tuple[int, date, str], cp_model.IntVar] = {}
    row_by_staff_id = {
        int(row["staff_id"]): row for row in settings.get("staff_weekend_rules") or []
    }
    coverage_ids = {
        int(staff_id) for staff_id in settings.get("weekend_coverage_staff_ids") or []
    }
    coverage_minimum = settings.get("weekend_coverage_min_working")
    coverage_maximum = settings.get("weekend_coverage_max_working")
    weekday_off_hard_min = settings.get("weekday_off_hard_min")
    weekday_off_hard_max = settings.get("weekday_off_hard_max")
    weekday_off_exempt_ids = {
        int(staff_id) for staff_id in settings.get("weekday_off_exempt_staff_ids") or []
    }

    for staff in staffs:
        for work_date in dates:
            if (
                (staff.active_from and work_date < staff.active_from)
                or (staff.active_to and work_date > staff.active_to)
            ):
                continue
            off = model.NewBoolVar(f"preflight_off_s{staff.id}_{work_date}")
            half = model.NewBoolVar(f"preflight_half_s{staff.id}_{work_date}")
            work = model.NewBoolVar(f"preflight_work_s{staff.id}_{work_date}")
            model.Add(off + half + work == 1)
            status_vars[(staff.id, work_date, "off")] = off
            status_vars[(staff.id, work_date, "half")] = half
            status_vars[(staff.id, work_date, "work")] = work

            row = row_by_staff_id.get(staff.id)
            fixed_weekday = fixed_weekday_off_value(row)
            if fixed_weekday is not None and work_date.weekday() == fixed_weekday:
                if row.get("fixed_weekday_off_priority", "hard") == "hard":
                    model.Add(off == 1)
                model.Add(half == 0)
                continue
            if not _is_special_day(work_date, settings) and not is_allowed_weekday_off(row, work_date):
                model.Add(off == 0)
            if not _has_auto_half_day_template(settings, staff.id) or not is_auto_half_day_allowed(work_date, settings):
                model.Add(half == 0)

    for work_date in dates:
        is_special_day = _is_special_day(work_date, settings)
        active_staff_ids = [
            staff.id for staff in staffs if (staff.id, work_date, "work") in status_vars
        ]
        weekday_off_staff_ids = [
            staff_id for staff_id in active_staff_ids
            if staff_in_setting_scope(staff_id, settings, "weekday_off_count")
        ]
        if not is_special_day and weekday_off_hard_min is not None:
            model.Add(sum(
                status_vars[(staff_id, work_date, "off")]
                for staff_id in weekday_off_staff_ids if staff_id not in weekday_off_exempt_ids
            ) >= int(weekday_off_hard_min))
        if not is_special_day and weekday_off_hard_max is not None:
            model.Add(
                sum(
                    status_vars[(staff_id, work_date, "off")]
                    for staff_id in weekday_off_staff_ids
                    if staff_id not in weekday_off_exempt_ids
                )
                <= int(weekday_off_hard_max)
            )
        category_staffing = day_category_rule(settings.get("day_category_staffing_ranges"), work_date, settings)
        category_staff_ids = [
            staff_id for staff_id in active_staff_ids
            if staff_in_setting_scope(staff_id, settings, "day_category_staffing_ranges")
        ]
        staffing_rows = [(category_staffing, category_staff_ids)] if category_staffing else []
        staffing_rows.extend(
            (row, [staff.id for staff in staffs if staff.id in active_staff_ids and staff.staff_group == row.get("staff_group")])
            for row in settings.get("group_day_category_staffing_ranges") or []
            if row.get("day_category") == day_category(work_date, settings)
        )
        if is_special_day:
            staffing_rows.append(({
                "min_working": settings.get("weekend_min_staff_count"),
                "max_working": settings.get("weekend_max_staff_count"),
            }, active_staff_ids))
        for staffing, member_ids in staffing_rows:
            working = sum(status_vars[(staff_id, work_date, "work")] + status_vars[(staff_id, work_date, "half")] for staff_id in member_ids)
            if staffing.get("min_working") is not None:
                model.Add(working >= int(staffing["min_working"]))
            if staffing.get("max_working") is not None:
                model.Add(working <= int(staffing["max_working"]))
        if not is_special_day:
            continue
        active_group_staff_ids = [
            staff.id
            for staff in staffs
            if staff.id in coverage_ids
            and (staff.id, work_date, "work") in status_vars
        ]
        if coverage_minimum is not None and settings.get("weekend_coverage_priority", "hard") == "hard":
            model.Add(
                sum(
                    status_vars[(staff_id, work_date, "work")]
                    + status_vars[(staff_id, work_date, "half")]
                    for staff_id in active_group_staff_ids
                )
                >= int(coverage_minimum)
            )
        if coverage_minimum is not None and coverage_maximum is not None:
            model.Add(
                sum(
                    status_vars[(staff_id, work_date, "work")]
                    + status_vars[(staff_id, work_date, "half")]
                    for staff_id in active_group_staff_ids
                )
                <= int(coverage_maximum)
            )
        preferred_ids = {
            int(staff_id) for staff_id in settings.get("weekend_preferred_staff_ids") or []
        }
        comparison_ids = {
            int(staff_id) for staff_id in settings.get("weekend_comparison_staff_ids") or []
        }
        advantage = int(settings.get("weekend_preferred_min_advantage", 0) or 0)
        if advantage:
            model.Add(
                sum(
                    status_vars[(staff_id, work_date, "work")]
                    + status_vars[(staff_id, work_date, "half")]
                    for staff_id in active_staff_ids
                    if staff_id in preferred_ids
                )
                >= sum(
                    status_vars[(staff_id, work_date, "work")]
                    + status_vars[(staff_id, work_date, "half")]
                    for staff_id in active_staff_ids
                    if staff_id in comparison_ids
                )
                + advantage
            )

    for staff in staffs:
        active_dates = [
            work_date
            for work_date in dates
            if (staff.id, work_date, "off") in status_vars
        ]
        if not active_dates:
            continue
        if staff_in_setting_scope(staff.id, settings, "monthly_holiday"):
            total = sum(
                status_vars[(staff.id, work_date, "off")] * off_units
                + status_vars[(staff.id, work_date, "half")] * half_day_units
                for work_date in active_dates
            )
            if exact_units is not None:
                if staff.paid_leave_eligible:
                    paid_leave_used = model.NewBoolVar(f"preflight_paid_leave_s{staff.id}")
                    model.Add(total == exact_units + paid_leave_units * paid_leave_used)
                else:
                    model.Add(total == exact_units)
            if minimum_units is not None:
                model.Add(total >= minimum_units)
            if maximum_units is not None:
                model.Add(total <= maximum_units)

        weekly_minimum = int(settings.get("weekly_min_off_days", 0) or 0)
        if weekly_minimum:
            for index in range(max(0, len(active_dates) - 6)):
                window = active_dates[index : index + 7]
                if len(window) == 7 and (window[-1] - window[0]).days == 6:
                    model.Add(
                        sum(status_vars[(staff.id, day, "off")] for day in window)
                        >= weekly_minimum
                    )
        max_consecutive = int(settings.get("max_consecutive_work_days", 0) or 0)
        if max_consecutive and staff_in_setting_scope(staff.id, settings, "max_consecutive_work_days"):
            window_size = max_consecutive + 1
            for index in range(max(0, len(active_dates) - max_consecutive)):
                window = active_dates[index : index + window_size]
                if len(window) == window_size and (window[-1] - window[0]).days == max_consecutive:
                    model.Add(
                        sum(
                            status_vars[(staff.id, day, "work")]
                            + status_vars[(staff.id, day, "half")]
                            for day in window
                        )
                        <= max_consecutive
                    )
        max_consecutive_off = int(settings.get("max_consecutive_off_days", 0) or 0)
        if max_consecutive_off:
            for index in range(max(0, len(active_dates) - max_consecutive_off)):
                window = active_dates[index : index + max_consecutive_off + 1]
                if (window[-1] - window[0]).days == max_consecutive_off:
                    model.Add(sum(status_vars[(staff.id, day, "off")] for day in window) <= max_consecutive_off)
        if (settings.get("require_monthly_consecutive_off")
                and settings.get("monthly_consecutive_off_strength", "hard") == "hard"
                and len(dates) >= 14
                and staff_in_setting_scope(staff.id, settings, "require_monthly_consecutive_off")):
            pairs = []
            for first, second in zip(active_dates, active_dates[1:], strict=False):
                if (second - first).days != 1:
                    continue
                pair = model.NewBoolVar(f"preflight_consecutive_off_s{staff.id}_{first}")
                model.Add(pair <= status_vars[(staff.id, first, "off")])
                model.Add(pair <= status_vars[(staff.id, second, "off")])
                model.Add(
                    pair
                    >= status_vars[(staff.id, first, "off")]
                    + status_vars[(staff.id, second, "off")]
                    - 1
                )
                pairs.append(pair)
            model.Add(sum(pairs) >= 1)

        row = row_by_staff_id.get(staff.id)
        required_weekend_pairs = int((row or {}).get("consecutive_weekend_off_count", 0) or 0)
        minimum_weekend_off_days = int((row or {}).get("minimum_weekend_off_days", 0) or 0)
        if required_weekend_pairs or minimum_weekend_off_days:
            weekend_pairs = []
            active_date_set = set(active_dates)
            for saturday in active_dates:
                sunday = saturday + timedelta(days=1)
                if saturday.weekday() != 5 or sunday not in active_date_set:
                    continue
                if minimum_weekend_off_days:
                    model.Add(status_vars[(staff.id, saturday, "off")] + status_vars[(staff.id, sunday, "off")] >= minimum_weekend_off_days)
                if not required_weekend_pairs:
                    continue
                pair = model.NewBoolVar(f"preflight_weekend_pair_s{staff.id}_{saturday}")
                model.Add(pair <= status_vars[(staff.id, saturday, "off")])
                model.Add(pair <= status_vars[(staff.id, sunday, "off")])
                model.Add(
                    pair
                    >= status_vars[(staff.id, saturday, "off")]
                    + status_vars[(staff.id, sunday, "off")]
                    - 1
                )
                weekend_pairs.append(pair)
            if required_weekend_pairs and (row or {}).get("consecutive_weekend_off_priority", "hard") == "hard":
                model.Add(sum(weekend_pairs) == required_weekend_pairs)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5
    return solver.Solve(model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}


def analyze_rule_feasibility(
    *,
    start_date: date,
    end_date_exclusive: date,
    settings: dict,
    staffs: list[Staff],
    demands: list[Demand] | None = None,
    positions: list[Position] | None = None,
) -> dict:
    dates = [
        start_date + timedelta(days=offset)
        for offset in range((end_date_exclusive - start_date).days)
    ]
    weekend_dates = [
        work_date
        for work_date in dates
        if _is_special_day(work_date, settings)
    ]
    full_weekends = [
        work_date
        for work_date in weekend_dates
        if work_date.weekday() == 5 and work_date + timedelta(days=1) < end_date_exclusive
    ]
    staff_by_id = {staff.id: staff for staff in staffs}
    issues: list[dict] = []
    summaries: list[dict] = []
    position_names = {row.id: row.display_name or row.name for row in positions or []}
    for demand in demands or []:
        minimum = demand.min_count if demand.min_count is not None else demand.required_count
        if start_date <= demand.work_date < end_date_exclusive and minimum > 0 and is_position_prohibited(demand.position_id, demand.work_date, settings):
            name = position_names.get(demand.position_id, f"持ち場ID {demand.position_id}")
            issues.append({
                "severity": "action_required",
                "code": f"prohibited_position_demand_{demand.id}",
                "message": f"{demand.work_date:%m/%d}の{name}は配置しない設定ですが、今月の予定には{minimum}名必要と登録されています。",
                "suggestions": [{"action": "review_planning", "label": "今月の予定で該当する持ち場を修正する"}],
            })

    for row in settings.get("staff_weekend_rules") or []:
        staff_id = int(row["staff_id"])
        staff = staff_by_id.get(staff_id)
        if staff is None:
            continue
        fixed_weekday = fixed_weekday_off_value(row)
        allowed_weekday_offs = (
            None
            if row.get("allowed_weekday_offs") is None
            else sorted(
                {
                    int(value)
                    for value in (row.get("allowed_weekday_offs") or [])
                    if 0 <= int(value) <= 4
                }
            )
        )
        fixed_dates = (
            [work_date for work_date in dates if work_date.weekday() == int(fixed_weekday)]
            if fixed_weekday is not None
            else []
        )
        required_pairs = int(row.get("consecutive_weekend_off_count", 0) or 0)
        summaries.append(
            {
                "staff_id": staff_id,
                "staff_name": staff.name,
                "fixed_weekday_off": fixed_weekday,
                "fixed_weekday_label": WEEKDAY_LABELS[int(fixed_weekday)]
                if fixed_weekday is not None
                else None,
                "fixed_weekday_count": len(fixed_dates),
                "allowed_weekday_offs": allowed_weekday_offs,
                "consecutive_weekend_off_count": required_pairs,
            }
        )
        if required_pairs > len(full_weekends) and row.get("consecutive_weekend_off_priority", "hard") == "hard":
            issues.append(
                {
                    "severity": "error",
                    "code": "not_enough_full_weekends",
                    "message": f"{staff.name} の土日連休は最大 {len(full_weekends)}回ですが、{required_pairs}回が設定されています。",
                    "suggestions": [
                        {
                            "action": "change_consecutive_weekend_count",
                            "label": f"土日連休を {len(full_weekends)}回以下に変更する",
                            "staff_id": staff_id,
                            "value": len(full_weekends),
                        }
                    ],
                }
            )

    coverage_ids = {int(staff_id) for staff_id in settings.get("weekend_coverage_staff_ids") or []}
    coverage_minimum = settings.get("weekend_coverage_min_working")
    exact_holidays = settings.get("monthly_holiday_exact_days")
    if coverage_ids and coverage_minimum is not None and settings.get("weekend_coverage_priority", "hard") == "hard":
        rules = settings.get("staff_weekend_rules") or []
        conflicting_weekends = []
        for saturday in full_weekends:
            sunday = saturday + timedelta(days=1)
            members = [staff_by_id[staff_id] for staff_id in coverage_ids if staff_id in staff_by_id
                       and (not staff_by_id[staff_id].active_from or staff_by_id[staff_id].active_from <= saturday)
                       and (not staff_by_id[staff_id].active_to or staff_by_id[staff_id].active_to >= sunday)]
            member_ids = {staff.id for staff in members}
            required = sum(int(row.get("minimum_weekend_off_days", 0) or 0) for row in rules if int(row["staff_id"]) in member_ids)
            capacity = sum(max(0, sum(
                staff.id in coverage_ids
                and (not staff.active_from or staff.active_from <= day)
                and (not staff.active_to or staff.active_to >= day)
                for staff in staffs
            ) - int(coverage_minimum)) for day in (saturday, sunday))
            if required > capacity:
                conflicting_weekends.append((saturday, required, capacity))
        if conflicting_weekends:
            saturday, required, capacity = conflicting_weekends[0]
            revised_rules = [{**row, "minimum_weekend_off_days": 0}
                             if int(row["staff_id"]) in coverage_ids else dict(row) for row in rules]
            issues.append({
                "severity": "action_required",
                "code": "weekend_rest_coverage_conflict",
                "message": f"{saturday:%m/%d}の週末は、各人の土日休み条件で合計{required}人日以上の休みが必要ですが、毎日{coverage_minimum}名出勤では合計{capacity}人日しか休めません。",
                "suggestions": [{
                    "action": "prioritize_weekend_coverage",
                    "label": "出勤人数を優先し、毎週の土日休み指定を解除する（月の土日連休は維持）",
                    "settings_patch": {"staff_weekend_rules": revised_rules},
                }, {"action": "review_rules", "label": "人員・作り方で出勤人数を見直す"}],
            })
    if coverage_ids and coverage_minimum is not None and exact_holidays is not None and settings.get("weekend_coverage_priority", "hard") == "hard":
        group_size = len(coverage_ids)
        weekend_off_capacity = max(0, group_size - int(coverage_minimum)) * len(weekend_dates)
        required_weekend_offs = 0
        group_names = []
        for staff_id in coverage_ids:
            staff = staff_by_id.get(staff_id)
            if staff is None:
                continue
            group_names.append(staff.name)
            row = next(
                (
                    item
                    for item in settings.get("staff_weekend_rules") or []
                    if int(item["staff_id"]) == staff_id
                ),
                None,
            )
            fixed_weekday = fixed_weekday_off_value(row) if row else None
            fixed_count = (
                sum(work_date.weekday() == int(fixed_weekday) for work_date in dates)
                if fixed_weekday is not None
                else 0
            )
            required_weekend_offs += max(0, int(float(exact_holidays) - fixed_count))
        extra_weekday_offs = max(0, required_weekend_offs - weekend_off_capacity)
        if extra_weekday_offs:
            accepted = settings.get("weekend_adjustment_policy") == "allow_minimum_weekday_offs"
            issues.append(
                {
                    "severity": "warning" if accepted else "action_required",
                    "code": "additional_weekday_offs_required",
                    "message": (
                        f"{', '.join(group_names)}には週末休みが合計 {required_weekend_offs}日必要ですが、"
                        f"週末に{coverage_minimum}人以上出勤する条件では {weekend_off_capacity}日までです。"
                        f"指定曜日以外の平日休みを最低 {extra_weekday_offs}日追加します。"
                    ),
                    "required_weekend_offs": required_weekend_offs,
                    "weekend_off_capacity": weekend_off_capacity,
                    "minimum_additional_weekday_offs": extra_weekday_offs,
                    "suggestions": [
                        {
                            "action": "allow_minimum_weekday_offs",
                            "label": f"平日休みを最低 {extra_weekday_offs}日だけ追加する（推奨）",
                            "settings_patch": {
                                "weekend_adjustment_policy": "allow_minimum_weekday_offs"
                            },
                        },
                        {
                            "action": "review_rules",
                            "label": "休日数または週末の最低出勤人数を見直す",
                        },
                    ],
                }
            )

    if not _holiday_rule_feasible(dates=dates, settings=settings, staffs=staffs):
        relaxed_saturday_and_sunday = {
            **settings,
            "forbid_half_day_on_weekdays": True,
            "forbid_half_day_on_saturdays": False,
            "auto_half_day_allowed_weekdays": [5, 6],
        }
        relaxed_all_days = {
            **settings,
            "forbid_half_day_on_weekdays": False,
            "forbid_half_day_on_saturdays": False,
            "auto_half_day_allowed_weekdays": None,
        }
        suggestions = []
        current_allowed_weekdays = auto_half_day_allowed_weekdays(settings)
        if current_allowed_weekdays != [5, 6] and _holiday_rule_feasible(
            dates=dates,
            settings=relaxed_saturday_and_sunday,
            staffs=staffs,
        ):
            suggestions.append(
                {
                    "action": "allow_auto_half_day_on_saturday_and_sunday",
                    "label": "土曜の自動半日勤務を許可する",
                    "settings_patch": {
                        "forbid_half_day_on_weekdays": True,
                        "forbid_half_day_on_saturdays": False,
                    },
                }
            )
        if current_allowed_weekdays is not None and _holiday_rule_feasible(
            dates=dates,
            settings=relaxed_all_days,
            staffs=staffs,
        ):
            suggestions.append(
                {
                    "action": "allow_auto_half_day_on_all_days",
                    "label": "月〜土の自動半日勤務を許可する",
                    "settings_patch": {
                        "forbid_half_day_on_weekdays": False,
                        "forbid_half_day_on_saturdays": False,
                    },
                }
            )
        has_holiday_bound = any(
            settings.get(key) is not None
            for key in (
                "monthly_holiday_exact_days",
                "monthly_holiday_min_days",
                "monthly_holiday_max_days",
            )
        )
        issues.append(
            {
                "severity": "action_required",
                "code": "holiday_rule_infeasible" if has_holiday_bound else "combined_day_off_rule_infeasible",
                "message": (
                    "現在の休日換算・週休・連続勤務・曜日別休み・週末出勤条件を"
                    "同時に満たす組み合わせを作れません。"
                ),
                "suggestions": suggestions or [
                    {
                        "action": "review_rules",
                        "label": "有休0.5の対象者、休日数、または曜日制約を見直す",
                    }
                ],
            }
        )

    return {
        "blocking": any(row["severity"] in {"error", "action_required"} for row in issues),
        "period": {
            "start_date": start_date.isoformat(),
            "end_date_exclusive": end_date_exclusive.isoformat(),
            "weekend_days": len(weekend_dates),
            "full_weekends": len(full_weekends),
        },
        "staff_rule_summaries": summaries,
        "issues": issues,
    }
