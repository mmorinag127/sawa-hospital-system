from datetime import date, datetime, timedelta
import re
from typing import Iterable, Optional

from src.services.config_service import load_ingest_policy

DATE_FORMAT_MAP = {
    "MM/DD": "%m/%d",
    "YYYY/MM/DD": "%Y/%m/%d",
    "YYYY-MM-DD": "%Y-%m-%d",
}


def _normalize_format(fmt: str) -> str:
    return DATE_FORMAT_MAP.get(fmt, "%m/%d")


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


def _extract_date_candidate(value: str, include_year: bool) -> str:
    if include_year:
        match = re.search(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", value)
        if match:
            return match.group(0)
    match = re.search(r"\d{1,2}[/-]\d{1,2}", value)
    if match:
        return match.group(0)
    return value


def _parse_date(date_str: str, fmt: str, default_year: int) -> Optional[date]:
    if not date_str:
        return None
    if not isinstance(date_str, str):
        date_str = str(date_str)
    cleaned = date_str.strip()
    if not cleaned:
        return None
    normalized_input = _normalize_date_string(cleaned)
    normalized = _normalize_format(fmt)
    try:
        include_year = "%Y" in normalized
        normalized_input = _extract_date_candidate(normalized_input, include_year)
        if "%Y" in normalized:
            return datetime.strptime(normalized_input, normalized).date()
        with_year = f"{default_year}/{normalized_input}"
        return datetime.strptime(with_year, f"%Y/{normalized}").date()
    except ValueError:
        return None


def _adjust_year(parsed: date, received_at: datetime, fmt: str) -> date:
    normalized = _normalize_format(fmt)
    if "%Y" in normalized:
        return parsed
    candidates = [parsed]
    for delta in (-1, 1):
        try:
            candidates.append(parsed.replace(year=parsed.year + delta))
        except ValueError:
            continue
    received_date = received_at.date()
    return min(candidates, key=lambda candidate: abs((candidate - received_date).days))


def parse_date_string(date_str: str, received_at: datetime) -> Optional[date]:
    policy = load_ingest_policy()
    week_policy = policy.get("week_id_policy", {})
    date_format = week_policy.get("date_format", "MM/DD")
    parsed = _parse_date(date_str, date_format, received_at.year)
    if parsed:
        return _adjust_year(parsed, received_at, date_format)
    return None


def week_id_from_dates(
    date_strings: Iterable[object],
    received_at: datetime,
    policy: Optional[dict] = None,
) -> Optional[str]:
    policy = policy or load_ingest_policy()
    week_policy = policy.get("week_id_policy", {})
    date_format = week_policy.get("date_format", "MM/DD")
    week_format = week_policy.get("week_format", "WEK%GW%V")

    dates: list[date] = []
    for raw in date_strings:
        if isinstance(raw, date):
            dates.append(raw)
            continue
        if raw is None:
            continue
        parsed = _parse_date(raw, date_format, received_at.year)
        if parsed:
            parsed = _adjust_year(parsed, received_at, date_format)
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
) -> Optional[str]:
    policy = policy or load_ingest_policy()
    month_policy = policy.get("month_id_policy", {})
    week_policy = policy.get("week_id_policy", {})
    date_format = month_policy.get("date_format") or week_policy.get("date_format", "MM/DD")
    month_format = month_policy.get("month_format", "%Y-%m")

    dates: list[date] = []
    for raw in date_strings:
        if isinstance(raw, date):
            dates.append(raw)
            continue
        if raw is None:
            continue
        parsed = _parse_date(raw, date_format, received_at.year)
        if parsed:
            parsed = _adjust_year(parsed, received_at, date_format)
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
