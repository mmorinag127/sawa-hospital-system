from __future__ import annotations

from datetime import date

import jpholiday


def japanese_holiday_name(work_date: date) -> str | None:
    name = jpholiday.is_holiday_name(work_date)
    return str(name) if name else None


def is_japanese_national_holiday(work_date: date) -> bool:
    return japanese_holiday_name(work_date) is not None


def is_weekend_or_japanese_holiday(work_date: date) -> bool:
    return work_date.weekday() >= 5 or is_japanese_national_holiday(work_date)
