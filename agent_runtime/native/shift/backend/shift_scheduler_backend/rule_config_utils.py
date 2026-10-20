from __future__ import annotations

from datetime import date, timedelta

from .holiday_calendar import is_japanese_national_holiday


PAID_LEAVE_HALF_DAY_WORK_KIND = "paid_leave_half_day"


def staff_in_setting_scope(staff_id: int, settings: dict, key: str) -> bool:
    """Keep ordinary RuleConfig global while scoping profile-owned settings."""
    if key not in set(settings.get("profile_scoped_setting_keys") or []):
        return True
    return int(staff_id) in {int(value) for value in settings.get("member_staff_ids") or []}
DAY_CATEGORIES = {"weekday", "saturday", "sunday", "holiday"}


def day_category(work_date: date, settings: dict) -> str:
    if settings.get("auto_apply_japanese_holidays", True) and is_japanese_national_holiday(work_date):
        return "holiday"
    if work_date.weekday() == 5:
        return "saturday"
    if work_date.weekday() == 6:
        return "sunday"
    return "weekday"


def day_category_rule(rows: list[dict] | None, work_date: date, settings: dict) -> dict | None:
    category = day_category(work_date, settings)
    return next((row for row in rows or [] if row.get("day_category") == category), None)


def is_position_prohibited(position_id: int, work_date: date, settings: dict) -> bool:
    category = day_category(work_date, settings)
    return any(
        int(row["position_id"]) == position_id and category in (row.get("day_categories") or [])
        for row in settings.get("position_day_category_prohibitions") or []
    )


def normalize_allowed_weekdays(
    raw_values: list[int] | tuple[int, ...] | None,
    *,
    maximum: int,
) -> list[int] | None:
    if raw_values is None:
        return None
    normalized = []
    for raw_value in raw_values:
        value = int(raw_value)
        if 0 <= value <= maximum and value not in normalized:
            normalized.append(value)
    return sorted(normalized)


def fixed_weekday_off_value(rule_row: dict | None) -> int | None:
    if not rule_row or rule_row.get("fixed_weekday_off") is None:
        return None
    value = int(rule_row["fixed_weekday_off"])
    return value if 0 <= value <= 4 else None


def allowed_weekday_offs_value(rule_row: dict | None) -> list[int] | None:
    if not rule_row:
        return None
    return normalize_allowed_weekdays(
        rule_row.get("allowed_weekday_offs"),
        maximum=4,
    )


def is_allowed_weekday_off(rule_row: dict | None, work_date: date) -> bool:
    if work_date.weekday() >= 5:
        return True
    allowed_weekdays = allowed_weekday_offs_value(rule_row)
    if allowed_weekdays is None:
        return True
    fixed_weekday = fixed_weekday_off_value(rule_row)
    if fixed_weekday is not None and work_date.weekday() == fixed_weekday:
        return True
    return work_date.weekday() in allowed_weekdays


def auto_half_day_allowed_weekdays(settings: dict) -> list[int] | None:
    explicit = normalize_allowed_weekdays(
        settings.get("auto_half_day_allowed_weekdays"),
        maximum=6,
    )
    if explicit is not None:
        return explicit
    forbid_weekdays = bool(settings.get("forbid_half_day_on_weekdays"))
    forbid_saturdays = bool(settings.get("forbid_half_day_on_saturdays"))
    if not forbid_weekdays and not forbid_saturdays:
        return None
    return [
        weekday
        for weekday in range(7)
        if not ((weekday < 5 and forbid_weekdays) or (weekday == 5 and forbid_saturdays))
    ]


def is_auto_half_day_allowed(work_date: date, settings: dict) -> bool:
    allowed_weekdays = auto_half_day_allowed_weekdays(settings)
    if allowed_weekdays is None:
        return True
    return work_date.weekday() in allowed_weekdays


def is_special_staffing_date(work_date: date, settings: dict) -> bool:
    return day_category(work_date, settings) != "weekday"


def saturday_sunday_pairs(
    start_date: date,
    end_date_exclusive: date,
    *,
    prior_dates: set[date] | None = None,
) -> list[tuple[date, date]]:
    pairs = []
    if (
        start_date < end_date_exclusive
        and start_date.weekday() == 6
        and prior_dates is not None
        and start_date - timedelta(days=1) in prior_dates
    ):
        pairs.append((start_date - timedelta(days=1), start_date))

    saturday = start_date + timedelta(days=(5 - start_date.weekday()) % 7)
    while saturday < end_date_exclusive:
        sunday = saturday + timedelta(days=1)
        if sunday >= end_date_exclusive:
            break
        pairs.append((saturday, sunday))
        saturday += timedelta(days=7)
    return pairs
