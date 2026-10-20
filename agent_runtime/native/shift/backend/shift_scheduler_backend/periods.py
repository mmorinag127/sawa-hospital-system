from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re

from .rule_config_utils import normalize_allowed_weekdays


TARGET_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
STANDARD_PERIOD_MODES = {"calendar_month", "fixed_start_day"}
PERIOD_MODES = STANDARD_PERIOD_MODES | {"custom"}
DEFAULT_STANDARD_PERIOD_MODE = "calendar_month"
DEFAULT_MAX_PERIOD_DAYS = 62
DEFAULT_STANDARD_PERIOD_START_DAY = None
DEFAULT_HOLIDAY_COUNT_UNIT = 2
DEFAULT_HOLIDAY_COUNTED_WORK_KINDS = {
    "off": 1.0,
    "half_day": 0.5,
    "regular": 0.0,
    "working": 0.0,
    "position": 0.0,
}


@dataclass(frozen=True)
class ResolvedPeriod:
    target_month: str
    start_date: date
    end_date_exclusive: date
    period_mode: str


def default_settings_config() -> dict:
    return {
        "strategy": "balanced",
        "standard_period_mode": DEFAULT_STANDARD_PERIOD_MODE,
        "standard_period_start_day": DEFAULT_STANDARD_PERIOD_START_DAY,
        "max_period_days": DEFAULT_MAX_PERIOD_DAYS,
        "holiday_count_unit": DEFAULT_HOLIDAY_COUNT_UNIT,
        "holiday_counted_work_kinds": dict(DEFAULT_HOLIDAY_COUNTED_WORK_KINDS),
        "monthly_holiday_exact_days": None,
        "monthly_holiday_min_days": None,
        "monthly_holiday_max_days": None,
        "auto_apply_japanese_holidays": True,
        "forbid_half_day_on_weekdays": False,
        "forbid_half_day_on_saturdays": False,
        "auto_half_day_allowed_weekdays": None,
        "max_daily_work_minutes": None,
        "max_weekly_work_minutes": None,
        "max_monthly_work_minutes": None,
        "min_monthly_work_minutes": None,
        "min_rest_minutes": 0,
        "half_day_work_minutes": 240,
        "full_day_work_minutes": 480,
        "weekly_min_off_days": None,
        "require_prior_period_continuity": False,
        "monthly_required_off_days": None,
        "monthly_min_off_days": None,
        "monthly_max_off_days": None,
        "max_consecutive_work_days": None,
        "max_consecutive_off_days": None,
        "require_monthly_consecutive_off": False,
        "monthly_consecutive_off_strength": "hard",
        "monthly_consecutive_off_weight": 1,
        "coverage_weight": None,
        "balanced_shift_types": [],
        "weekday_off_spread_weight": 0,
        "weekday_off_target_min": 0,
        "weekday_off_target_max": 3,
        "avoid_unplanned_off_weight": 1,
        "consecutive_work_weight": 0,
        "target_work_minutes_weight": 0,
        "weekend_fairness_weight": 0,
        "shift_fairness_weight": 0,
        "staff_half_day_shift_template_ids": {},
        "staff_display_order_ids": [],
        "regular_work_display_blank": False,
        "weekday_off_hard_max": None,
        "weekday_off_hard_min": None,
        "weekday_off_exempt_staff_ids": [],
        "weekend_min_staff_count": None,
        "weekend_max_staff_count": None,
        "weekend_preferred_staff_ids": [],
        "weekend_comparison_staff_ids": [],
        "weekend_preferred_min_advantage": 0,
        "staff_weekend_rules": [],
        "weekend_coverage_staff_ids": [],
        "weekend_coverage_min_working": None,
        "weekend_coverage_max_working": None,
        "weekend_coverage_target_working": None,
        "weekend_coverage_priority": "hard",
        "weekend_coverage_weight": 100,
        "weekend_coverage_target_weight": 100,
        "weekend_adjustment_policy": "ask",
        "additional_weekday_off_weight": 100,
        "day_category_staffing_ranges": [],
        "group_day_category_staffing_ranges": [],
        "staff_day_category_staffing_ranges": [],
        "position_day_category_prohibitions": [],
        "position_staffing_groups": [],
    }


def build_active_settings(config: dict | None) -> dict:
    defaults = default_settings_config()
    merged = {**defaults, **(config or {})}
    merged["holiday_counted_work_kinds"] = {
        **DEFAULT_HOLIDAY_COUNTED_WORK_KINDS,
        **((config or {}).get("holiday_counted_work_kinds") or {}),
    }
    explicit_auto_half_day_allowed_weekdays = normalize_allowed_weekdays(
        merged.get("auto_half_day_allowed_weekdays"),
        maximum=6,
    )
    merged["auto_half_day_allowed_weekdays"] = explicit_auto_half_day_allowed_weekdays
    configured = config or {}
    if "forbid_half_day_on_weekdays" not in configured and explicit_auto_half_day_allowed_weekdays is not None:
        merged["forbid_half_day_on_weekdays"] = not any(
            weekday in explicit_auto_half_day_allowed_weekdays
            for weekday in range(5)
        )
    if "forbid_half_day_on_saturdays" not in configured and explicit_auto_half_day_allowed_weekdays is not None:
        merged["forbid_half_day_on_saturdays"] = 5 not in explicit_auto_half_day_allowed_weekdays
    return merged


def resolve_period_request(
    *,
    target_month: str | None,
    start_date: date | None,
    end_date_exclusive: date | None,
    settings_config: dict | None,
) -> ResolvedPeriod:
    settings = build_active_settings(settings_config)
    has_target_month = bool(target_month)
    has_custom_start = start_date is not None
    has_custom_end = end_date_exclusive is not None
    has_custom_period = has_custom_start or has_custom_end
    if has_target_month and has_custom_period:
        raise ValueError("target_month と start_date / end_date_exclusive は同時に指定できません")
    if not has_target_month and not has_custom_period:
        raise ValueError("target_month または start_date / end_date_exclusive を指定してください")
    if has_custom_start != has_custom_end:
        raise ValueError("start_date と end_date_exclusive は両方指定してください")

    if has_target_month:
        if not target_month or not TARGET_MONTH_PATTERN.match(target_month):
            raise ValueError("target_month は YYYY-MM 形式で指定してください")
        period_mode = str(settings.get("standard_period_mode") or DEFAULT_STANDARD_PERIOD_MODE)
        if period_mode not in STANDARD_PERIOD_MODES:
            raise ValueError("standard_period_mode は calendar_month または fixed_start_day を指定してください")
        start, end = resolve_standard_period(target_month, period_mode, settings.get("standard_period_start_day"))
        validate_period_length(start, end, int(settings.get("max_period_days") or DEFAULT_MAX_PERIOD_DAYS))
        return ResolvedPeriod(
            target_month=target_month,
            start_date=start,
            end_date_exclusive=end,
            period_mode=period_mode,
        )

    assert start_date is not None
    assert end_date_exclusive is not None
    validate_period_length(
        start_date,
        end_date_exclusive,
        int(settings.get("max_period_days") or DEFAULT_MAX_PERIOD_DAYS),
    )
    return ResolvedPeriod(
        target_month=f"{start_date.year:04d}-{start_date.month:02d}",
        start_date=start_date,
        end_date_exclusive=end_date_exclusive,
        period_mode="custom",
    )


def resolve_standard_period(
    target_month: str,
    standard_period_mode: str,
    standard_period_start_day: int | None,
) -> tuple[date, date]:
    if not TARGET_MONTH_PATTERN.match(target_month):
        raise ValueError("target_month は YYYY-MM 形式で指定してください")
    year, month = (int(value) for value in target_month.split("-"))
    if standard_period_mode == "calendar_month":
        start = date(year, month, 1)
        return start, _next_month(start)
    if standard_period_mode != "fixed_start_day":
        raise ValueError("standard_period_mode は calendar_month または fixed_start_day を指定してください")
    if standard_period_start_day is None:
        raise ValueError("fixed_start_day では standard_period_start_day を指定してください")
    if not 1 <= int(standard_period_start_day) <= 28:
        raise ValueError("standard_period_start_day は 1 から 28 の間で指定してください")
    start = date(year, month, int(standard_period_start_day))
    next_start = _next_month(start)
    return start, date(next_start.year, next_start.month, int(standard_period_start_day))


def validate_period_length(start_date: date, end_date_exclusive: date, max_period_days: int) -> None:
    if end_date_exclusive <= start_date:
        raise ValueError("end_date_exclusive は start_date より後の日付にしてください")
    days = (end_date_exclusive - start_date).days
    if days > max_period_days:
        raise ValueError(f"期間は最大 {max_period_days} 日までです")


def scale_decimal_units(value: float | int | str | None, unit: int, field_name: str) -> int | None:
    if value is None:
        return None
    try:
        scaled = Decimal(str(value)) * Decimal(unit)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field_name} の値が不正です") from error
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{field_name} は 1/{unit} 単位で指定してください")
    return int(scaled)


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)
