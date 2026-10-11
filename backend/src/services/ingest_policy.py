from datetime import date, datetime, timedelta
import re
from typing import Iterable, Optional

from src.services.config_service import load_ingest_policy

_FULLWIDTH_TRANS = str.maketrans(
    {
        "\uff10": "0",
        "\uff11": "1",
        "\uff12": "2",
        "\uff13": "3",
        "\uff14": "4",
        "\uff15": "5",
        "\uff16": "6",
        "\uff17": "7",
        "\uff18": "8",
        "\uff19": "9",
        "\uff0f": "/",
        "\uff0d": "-",
        "\u2212": "-",
    }
)


def _normalize_date_string(value: str) -> str:
    cleaned = value.translate(_FULLWIDTH_TRANS)
    cleaned = cleaned.replace("\u5e74", "/").replace("\u6708", "/").replace("\u65e5", "")
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned


def require_explicit_ingest_week(value: object) -> str:
    from src.services.sheet_week_service import parse_sheet_week_value

    if not isinstance(value, str) or not value.strip():
        raise ValueError("week_hint_required")
    month, start, end = parse_sheet_week_value(value)
    if month is None:
        raise ValueError("week_hint_invalid")
    if start is None or end is None:
        raise ValueError("week_choice_required")
    return value.strip()


def parse_date_string(date_str: str, received_at: datetime, *, policy: Optional[dict] = None, explicit_week: str | None = None) -> Optional[date]:
    require_explicit_ingest_week(explicit_week)
    if explicit_week is not None:
        from src.services.sheet_week_service import parse_sheet_week_value

        _, start, end = parse_sheet_week_value(require_explicit_ingest_week(explicit_week))
        if date_str is None or not str(date_str).strip():
            return None
        text = _normalize_date_string(str(date_str).strip())
        full_dates = list(re.finditer(r"(?<![\d/-])(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?![\d/-])", text))
        short_text = text
        for match in reversed(full_dates):
            short_text = short_text[:match.start()] + " " * (match.end() - match.start()) + short_text[match.end():]
        short_dates = list(re.finditer(r"(?<![\d/-])(\d{1,2})[/-](\d{1,2})(?![\d/-])", short_text))
        if len(full_dates) + len(short_dates) != 1:
            raise ValueError("ingest_date_unresolved_in_selected_period")
        if full_dates:
            try:
                parsed = date(*(int(value) for value in full_dates[0].groups()))
            except ValueError as exc:
                raise ValueError("ingest_date_outside_selected_period") from exc
            if not start <= parsed <= end:
                raise ValueError("ingest_date_outside_selected_period")
            return parsed
        month, day = (int(value) for value in short_dates[0].groups())
        matches = []
        for year in range(start.year, end.year + 1):
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if start <= candidate <= end:
                matches.append(candidate)
        if len(matches) != 1:
            raise ValueError("ingest_date_unresolved_in_selected_period")
        return matches[0]


def week_id_from_dates(
    date_strings: Iterable[object],
    received_at: datetime,
    policy: Optional[dict] = None,
    *, explicit_week: str | None = None,
) -> Optional[str]:
    require_explicit_ingest_week(explicit_week)
    policy = load_ingest_policy() if policy is None else policy
    week_policy = policy.get("week_id_policy", {})
    week_format = week_policy.get("week_format", "WEK%GW%V")

    dates: list[date] = []
    for raw in date_strings:
        parsed = parse_date_string(raw.isoformat() if isinstance(raw, date) else raw,
            received_at, policy=policy, explicit_week=explicit_week)
        if parsed:
            dates.append(parsed)
    if not dates:
        return None
    earliest = min(dates)
    iso_year, iso_week, _ = earliest.isocalendar()
    return week_format.replace("%G", f"{iso_year:04d}").replace("%V", f"{iso_week:02d}")


def month_id_from_dates(
    date_strings: Iterable[object],
    received_at: datetime,
    policy: Optional[dict] = None,
    *, explicit_week: str | None = None,
) -> Optional[str]:
    require_explicit_ingest_week(explicit_week)
    policy = load_ingest_policy() if policy is None else policy
    month_policy = policy.get("month_id_policy", {})
    month_format = month_policy.get("month_format", "%Y-%m")

    dates: list[date] = []
    for raw in date_strings:
        parsed = parse_date_string(raw.isoformat() if isinstance(raw, date) else raw,
            received_at, policy=policy, explicit_week=explicit_week)
        if parsed:
            dates.append(parsed)
    if not dates:
        return None
    earliest = min(dates)
    return earliest.strftime(month_format)


def should_skip_ocr(received_at: datetime, policy: Optional[dict] = None) -> bool:
    policy = policy or load_ingest_policy()
    backlog_cfg = policy.get("backlog_policy", {})
    if not backlog_cfg.get("skip_ocr_for_stale", False):
        return False
    max_age_days = int(backlog_cfg.get("max_age_days", 0) or 0)
    if max_age_days <= 0:
        return False
    return datetime.utcnow() - received_at > timedelta(days=max_age_days)


def ingest_chunk_delay_seconds(index: int, policy: Optional[dict] = None) -> int:
    policy = policy or load_ingest_policy()
    backlog_cfg = policy.get("backlog_policy", {})
    base = int(backlog_cfg.get("chunk_delay_seconds", 0) or 0)
    if base <= 0:
        return 0
    return max(0, base * max(0, index))


def retry_backoff_seconds(attempt: int, policy: Optional[dict] = None) -> int:
    policy = policy or load_ingest_policy()
    retry_cfg = policy.get("retry_policy", {})
    base = int(retry_cfg.get("base_delay_seconds", 10) or 10)
    max_delay = int(retry_cfg.get("max_delay_seconds", 60) or 60)
    delay = base * max(1, attempt)
    return min(delay, max_delay)
