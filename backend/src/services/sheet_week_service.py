from __future__ import annotations

from datetime import date, timedelta
import re


def to_sheet_month_id(value: object) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return text
    match = re.fullmatch(r"(\d{4}-\d{2})@\d{4}-\d{2}-\d{2}~\d{4}-\d{2}-\d{2}", text)
    if match:
        return match.group(1)
    return None


def parse_sheet_week_value(value: object) -> tuple[str | None, date | None, date | None]:
    if not value:
        return None, None, None
    text = str(value).strip()
    if not text:
        return None, None, None
    month_id = to_sheet_month_id(text)
    if not month_id:
        return None, None, None
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return month_id, None, None
    try:
        range_part = text.split("@", 1)[1]
        start_token, end_token = [item.strip() for item in range_part.split("~", 1)]
        start_date = date.fromisoformat(start_token)
        end_date = date.fromisoformat(end_token)
    except Exception:
        return None, None, None
    if end_date < start_date:
        return None, None, None
    if start_date.strftime("%Y-%m") != month_id:
        return None, None, None
    return month_id, start_date, end_date


def format_sheet_week_value(
    month_id: str | None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> str | None:
    month = to_sheet_month_id(month_id)
    if not month:
        return None
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        return month
    if end_date < start_date:
        return month
    anchor_month = start_date.strftime("%Y-%m")
    if month != anchor_month:
        month = anchor_month
    return f"{month}@{start_date.isoformat()}~{end_date.isoformat()}"


def normalize_sheet_week_value(value: object) -> str | None:
    month_id, start_date, end_date = parse_sheet_week_value(value)
    if not month_id:
        return None
    return format_sheet_week_value(month_id, start_date, end_date)


def format_sheet_week_label(value: object) -> str:
    month_id, start_date, end_date = parse_sheet_week_value(value)
    if not month_id:
        return ""
    if isinstance(start_date, date) and isinstance(end_date, date):
        return f"{month_id} ({start_date.strftime('%m/%d')}-{end_date.strftime('%m/%d')})"
    return month_id


def shift_sheet_month_id(month_id: str, delta: int) -> str | None:
    base = to_sheet_month_id(month_id)
    if not base:
        return None
    year = int(base[:4])
    month = int(base[5:7])
    index = year * 12 + (month - 1) + delta
    shifted_year = index // 12
    shifted_month = (index % 12) + 1
    return f"{shifted_year:04d}-{shifted_month:02d}"


def sheet_month_distance(from_month_id: str | None, to_month_id: str | None) -> int | None:
    from_month = to_sheet_month_id(from_month_id)
    to_month = to_sheet_month_id(to_month_id)
    if not from_month or not to_month:
        return None
    fy = int(from_month[:4])
    fm = int(from_month[5:7])
    ty = int(to_month[:4])
    tm = int(to_month[5:7])
    return abs((fy * 12 + fm) - (ty * 12 + tm))


def sheet_week_month_ids(value: object) -> list[str]:
    month_id, start_date, end_date = parse_sheet_week_value(value)
    if not month_id:
        return []
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        return [month_id]
    month_ids: list[str] = []
    cursor = date(start_date.year, start_date.month, 1)
    end_cursor = date(end_date.year, end_date.month, 1)
    while cursor <= end_cursor:
        month_ids.append(cursor.strftime("%Y-%m"))
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return month_ids


def build_cross_month_week_value(dates: list[date] | set[date] | tuple[date, ...] | None) -> str | None:
    normalized = sorted({item for item in (dates or []) if isinstance(item, date)})
    if len(normalized) < 2:
        return None
    start_date = normalized[0]
    end_date = normalized[-1]
    if start_date.strftime("%Y-%m") == end_date.strftime("%Y-%m"):
        return None
    if (end_date - start_date).days > 10:
        return None
    return format_sheet_week_value(start_date.strftime("%Y-%m"), start_date, end_date)


def select_dominant_date_cluster(
    dates: list[date] | set[date] | tuple[date, ...] | None,
    *,
    max_gap_days: int = 2,
    min_cluster_size: int = 2,
    min_cluster_share: float = 0.5,
) -> set[date]:
    normalized = sorted({item for item in (dates or []) if isinstance(item, date)})
    if len(normalized) <= 1:
        return set(normalized)

    clusters: list[list[date]] = []
    current: list[date] = []
    for item in normalized:
        if not current:
            current = [item]
            continue
        prev = current[-1]
        if (item - prev).days <= max(1, int(max_gap_days)):
            current.append(item)
        else:
            clusters.append(current)
            current = [item]
    if current:
        clusters.append(current)
    if len(clusters) <= 1:
        return set(normalized)

    best = max(clusters, key=lambda cluster: (len(cluster), cluster[-1]))
    if (
        len(best) >= max(1, int(min_cluster_size))
        and (len(best) / len(normalized)) >= float(min_cluster_share)
    ):
        return set(best)
    return set(normalized)


def build_cross_month_week_value_from_clustered_dates(
    dates: list[date] | set[date] | tuple[date, ...] | None,
    *,
    max_gap_days: int = 2,
    min_cluster_size: int = 2,
    min_cluster_share: float = 0.5,
) -> str | None:
    dominant_dates = select_dominant_date_cluster(
        dates,
        max_gap_days=max_gap_days,
        min_cluster_size=min_cluster_size,
        min_cluster_share=min_cluster_share,
    )
    return build_cross_month_week_value(dominant_dates)


def build_calendar_week_value(anchor_date: date | None) -> str | None:
    if not isinstance(anchor_date, date):
        return None
    weekday = anchor_date.weekday()
    days_from_sunday = (weekday + 1) % 7
    start_date = anchor_date - timedelta(days=days_from_sunday)
    end_date = start_date + timedelta(days=6)
    return format_sheet_week_value(start_date.strftime("%Y-%m"), start_date, end_date)


def is_fixed_calendar_week_value(value: object) -> bool:
    month_id, start_date, end_date = parse_sheet_week_value(value)
    if not month_id or not isinstance(start_date, date) or not isinstance(end_date, date):
        return False
    if start_date.weekday() != 6:
        return False
    return end_date == start_date + timedelta(days=6)


def coerce_to_calendar_week_value(value: object) -> str | None:
    month_id, start_date, end_date = parse_sheet_week_value(value)
    if not month_id or not isinstance(start_date, date) or not isinstance(end_date, date):
        return normalize_sheet_week_value(value)
    if is_fixed_calendar_week_value(value):
        return format_sheet_week_value(month_id, start_date, end_date)
    return build_calendar_week_value(start_date)


def is_strict_expansion_of_week_range(candidate_week_value: str | None, base_week_value: str | None) -> bool:
    candidate_month_id, candidate_start, candidate_end = parse_sheet_week_value(candidate_week_value)
    base_month_id, base_start, base_end = parse_sheet_week_value(base_week_value)
    if (
        not candidate_month_id
        or not base_month_id
        or not isinstance(candidate_start, date)
        or not isinstance(candidate_end, date)
        or not isinstance(base_start, date)
        or not isinstance(base_end, date)
    ):
        return False
    if candidate_start > base_start or candidate_end < base_end:
        return False
    return candidate_start < base_start or candidate_end > base_end
