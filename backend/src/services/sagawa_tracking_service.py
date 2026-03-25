from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import unescape
import http.cookiejar
import os
import re
import urllib.parse
import urllib.request


FORM_URL = os.getenv(
    "SAGAWA_TRACKING_FORM_URL",
    "https://k2k.sagawa-exp.co.jp/p/sagawa/web/okurijoinput.jsp",
)
USER_AGENT = os.getenv(
    "SAGAWA_TRACKING_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)
TIMEOUT_SECONDS = float(os.getenv("SAGAWA_TRACKING_TIMEOUT_SECONDS", "20"))

_HIDDEN_INPUT_RE = re.compile(
    r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"',
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_EVENT_TABLE_RE = re.compile(
    r'<table[^>]+class="[^"]*table_okurijo_detail2[^"]*"[^>]*>(.*?)</table>',
    re.IGNORECASE | re.DOTALL,
)
_TABLE_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_TABLE_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)


def _strip_tracking(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) < 10:
        return ""
    return digits[:12]


def normalize_tracking_key(value: str) -> str:
    return _strip_tracking(value)


def _tracking_for_query(value: str) -> str:
    normalized = _strip_tracking(value)
    if len(normalized) == 12:
        return f"{normalized[:4]}-{normalized[4:8]}-{normalized[8:12]}"
    return normalized


def _new_opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _extract_hidden_fields(html_text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name, value in _HIDDEN_INPUT_RE.findall(html_text):
        fields[name] = value
    return fields


def _decode_body(data: bytes) -> str:
    for encoding in ("cp932", "shift_jis", "utf-8"):
        try:
            return data.decode(encoding)
        except Exception:
            continue
    return data.decode("utf-8", errors="ignore")


def _build_payload(hidden_fields: dict[str, str], tracking_number: str) -> dict[str, str]:
    payload: dict[str, str] = {
        "jsf_tree_64": hidden_fields.get("jsf_tree_64", ""),
        "jsf_state_64": hidden_fields.get("jsf_state_64", ""),
        "jsf_viewid": hidden_fields.get("jsf_viewid", "/web/okurijoinput.jsp"),
        "main:no1": tracking_number,
        "main:correlation": hidden_fields.get("main:correlation", "1"),
        "main:toiStart": "お問い合わせ開始",
        "main_SUBMIT": "1",
        "main:_link_hidden_": "",
    }
    for idx in range(2, 11):
        payload[f"main:no{idx}"] = ""
    return payload


def _extract_status(page: str) -> str | None:
    match = re.search(r'<span class="state">([^<]+)</span>', page)
    if not match:
        return None
    status = match.group(1).strip()
    return status or None


def _extract_arrival_text(page: str) -> str | None:
    label_match = re.search(
        r'<span id="haitaKnryoLabel">[^<]*</span>\s*</dt>\s*<dd>\s*([^<]+)\s*</dd>',
        page,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if label_match:
        value = re.sub(r"\s+", " ", label_match.group(1)).strip()
        if value:
            return value
    slash_matches = re.findall(r"(\d{2}/\d{2}\s*\d{2}:\d{2})", page)
    if slash_matches:
        candidate = slash_matches[-1].strip()
        slash_match = re.match(r"(\d{2})/(\d{2})\s*(\d{2}):(\d{2})", candidate)
        if slash_match:
            month, day, hour, minute = slash_match.groups()
            return f"{month}月{day}日 {hour}時{minute}分"
    return None


def _extract_tracking_number(page: str, fallback: str) -> str:
    match = re.search(r'<th class="number nowrap"><strong>([^<]+)</strong>', page)
    if match:
        value = match.group(1).strip()
        if value:
            return value
    return fallback


def _normalize_html_text(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_event_datetime(value: str, *, looked_up_at: datetime | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.match(r"(\d{2})/(\d{2})\s*(\d{2}):(\d{2})", text)
    if not match:
        return None
    month, day, hour, minute = (int(part) for part in match.groups())
    reference = looked_up_at or datetime.utcnow()
    try:
        candidate = datetime(reference.year, month, day, hour, minute)
    except ValueError:
        return None
    if looked_up_at and candidate > looked_up_at + timedelta(days=2):
        try:
            candidate = datetime(reference.year - 1, month, day, hour, minute)
        except ValueError:
            return None
    return candidate


@dataclass
class TrackingEvent:
    event_status: str
    event_at_text: str | None
    office_name: str | None
    event_order: int
    event_at: datetime | None = None

    def serialize(self) -> dict:
        occurred_at = self.event_at.isoformat() if self.event_at else self.event_at_text
        return {
            "status": self.event_status,
            "event_status": self.event_status,
            "occurred_at": occurred_at,
            "event_at_text": self.event_at_text,
            "time_text": self.event_at_text,
            "facility_name": self.office_name,
            "office_name": self.office_name,
            "event_order": self.event_order,
            "event_at": self.event_at.isoformat() if self.event_at else None,
        }


def _extract_tracking_events(page: str, *, looked_up_at: datetime | None = None) -> list[TrackingEvent]:
    for table_html in _EVENT_TABLE_RE.findall(page):
        rows = _TABLE_ROW_RE.findall(table_html)
        parsed_rows: list[list[str]] = []
        for row_html in rows:
            cells = [_normalize_html_text(cell) for cell in _TABLE_CELL_RE.findall(row_html)]
            cells = [cell for cell in cells if cell]
            if cells:
                parsed_rows.append(cells)
        if not parsed_rows:
            continue
        header = parsed_rows[0][:3]
        if header != ["荷物状況", "日時", "担当営業所"]:
            continue
        events: list[TrackingEvent] = []
        for cells in parsed_rows[1:]:
            if len(cells) < 3:
                continue
            event_status = cells[0]
            event_at_text = cells[1] or None
            office_name = cells[2] or None
            if not event_status:
                continue
            events.append(
                TrackingEvent(
                    event_status=event_status,
                    event_at_text=event_at_text,
                    office_name=office_name,
                    event_order=len(events),
                    event_at=_parse_event_datetime(event_at_text or "", looked_up_at=looked_up_at),
                )
            )
        if events:
            return events
    return []


@dataclass
class TrackingStatus:
    tracking_number: str
    tracking_key: str
    status: str
    delivered: bool
    arrival_text: str | None
    message: str | None = None
    error: str | None = None
    events: list[TrackingEvent] = field(default_factory=list)

    def serialize(self) -> dict:
        return {
            "tracking_number": self.tracking_number,
            "tracking_key": self.tracking_key,
            "status": self.status,
            "delivered": self.delivered,
            "arrival_text": self.arrival_text,
            "message": self.message,
            "error": self.error,
            "events": [item.serialize() for item in self.events],
        }


def lookup_tracking_status(tracking_number: str) -> TrackingStatus:
    query_number = _tracking_for_query(tracking_number)
    tracking_key = normalize_tracking_key(tracking_number)
    if not query_number:
        return TrackingStatus(
            tracking_number=tracking_number,
            tracking_key=tracking_key,
            status="invalid",
            delivered=False,
            arrival_text=None,
            error="invalid_tracking_number",
        )
    opener = _new_opener()
    try:
        initial_request = urllib.request.Request(FORM_URL, headers={"User-Agent": USER_AGENT})
        initial_data = opener.open(initial_request, timeout=TIMEOUT_SECONDS).read()
        initial_page = _decode_body(initial_data)
        hidden_fields = _extract_hidden_fields(initial_page)
        payload = _build_payload(hidden_fields, query_number)
        encoded = urllib.parse.urlencode(payload).encode("utf-8")
        submit_request = urllib.request.Request(
            FORM_URL,
            data=encoded,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        response_data = opener.open(submit_request, timeout=TIMEOUT_SECONDS).read()
        response_page = unescape(_decode_body(response_data))
        status = _extract_status(response_page) or "不明"
        delivered = "配達完了" in status or "お届けが完了" in response_page
        arrival_text = _extract_arrival_text(response_page) if delivered else None
        tracked_number = _extract_tracking_number(response_page, query_number)
        events = _extract_tracking_events(response_page, looked_up_at=datetime.utcnow())
        message = None
        if "該当なし" in status:
            message = "no_match"
        return TrackingStatus(
            tracking_number=tracked_number,
            tracking_key=tracking_key,
            status=status,
            delivered=delivered,
            arrival_text=arrival_text,
            message=message,
            events=events,
        )
    except Exception as exc:  # noqa: BLE001
        return TrackingStatus(
            tracking_number=query_number,
            tracking_key=tracking_key,
            status="error",
            delivered=False,
            arrival_text=None,
            error=str(exc),
        )


def lookup_tracking_statuses(tracking_numbers: list[str]) -> list[TrackingStatus]:
    results: list[TrackingStatus] = []
    cache: dict[str, TrackingStatus] = {}
    for value in tracking_numbers:
        tracking_key = normalize_tracking_key(value)
        if not tracking_key:
            results.append(
                TrackingStatus(
                    tracking_number=value,
                    tracking_key="",
                    status="invalid",
                    delivered=False,
                    arrival_text=None,
                    error="invalid_tracking_number",
                )
            )
            continue
        cached = cache.get(tracking_key)
        if cached:
            results.append(cached)
            continue
        result = lookup_tracking_status(value)
        cache[tracking_key] = result
        results.append(result)
    return results
