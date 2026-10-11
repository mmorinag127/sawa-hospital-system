from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
from io import BytesIO
from pathlib import Path
import os
import re
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.worksheet.print_settings import PrintArea
from sqlalchemy import select

from src.db import session_scope
from src.models.order import Order
from src.models.order_version import OrderVersion
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_workflow_state import OrderWorkflowState
from src.services import config_service, menu_service, sheet_week_service, master_order_form_template_service
from src.services import canonical_facility_store
from src.services.storage_service import (
    get_default_output_bucket,
    load_bytes_from_uri,
    save_artifact_bytes_to_gcs,
)


_MONTH_ID_RE = re.compile(r"^\d{4}-\d{2}$")
_OUTPUT_DIR = Path(os.getenv("ORDER_FORM_OUTPUT_DIR", "/tmp/order-form-outputs"))
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
_FAX_OUTPUT_DIR = Path(os.getenv("FAX_ORDER_FORM_OUTPUT_DIR", "/tmp/fax-order-form-prototypes"))
_FAX_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
_DEFAULT_WEEK_SHEET = "3月22日～3月28日"
_ORDER_FORM_BODY_START_ROW = 11
_ORDER_FORM_BODY_END_ROW = 67
_ORDER_FORM_DEADLINE_SEARCH_MAX_ROW = 6
_ORDER_FORM_DEADLINE_SEARCH_MAX_COL = 16
_BOTTOM_MARKER_ROW = 69
_WEEKDAY_LABELS = ["（月）", "（火）", "（水）", "（木）", "（金）", "（土）", "（日）"]
_MARKER_FILL = PatternFill(start_color="000000", end_color="000000", fill_type="solid")
_META_FILL = PatternFill(start_color="E9EEF5", end_color="E9EEF5", fill_type="solid")
_THIN_BORDER = Border(
    left=Side(style="thin", color="000000"),
    right=Side(style="thin", color="000000"),
    top=Side(style="thin", color="000000"),
    bottom=Side(style="thin", color="000000"),
)
_ORDER_FORM_SOURCE_TEMPLATE_ASSET_DIR = Path(__file__).resolve().parents[1] / "data" / "order_form_source_workbooks"
_ORDER_FORM_SOURCE_TEMPLATE_MANIFEST = _ORDER_FORM_SOURCE_TEMPLATE_ASSET_DIR / "manifest.json"
_SOURCE_WORKBOOK_MATERIALIZED_DIR = Path(
    os.getenv("FAX_SOURCE_TEMPLATE_MATERIALIZED_DIR", "/tmp/fax-source-template-workbooks")
)


class WeekEntriesWriteResult(int):
    def __new__(
        cls,
        written_rows: int,
        *,
        source_rows: int,
        overflow_entries: list[dict[str, str]],
    ):
        obj = int.__new__(cls, written_rows)
        obj.written_rows = int(written_rows)
        obj.source_rows = int(source_rows)
        obj.overflow_entries = overflow_entries
        return obj

    @property
    def overflow_rows(self) -> int:
        return len(self.overflow_entries)


def _resolve_fax_source_template_dir() -> Path:
    configured = os.getenv("FAX_SOURCE_TEMPLATE_DIR", "").strip()
    if configured:
        return Path(configured)
    return _ORDER_FORM_SOURCE_TEMPLATE_ASSET_DIR


def _format_generated_at(value: datetime | None = None) -> str:
    return (value or datetime.utcnow()).strftime("%Y-%m-%d %H:%M")


def _current_order_version_metadata(session, order_id: str) -> dict[str, Any]:
    if not hasattr(session, "execute"):
        return {}
    order = session.get(Order, order_id) if hasattr(session, "get") else None
    try:
        rows = (
            session.execute(
                select(OrderVersion)
                .where(OrderVersion.order_id == order_id)
                .order_by(OrderVersion.version_no.desc(), OrderVersion.received_at.desc(), OrderVersion.id.desc())
            )
            .scalars()
            .all()
        )
    except Exception:
        return {}
    if not rows:
        if order is None or not str(getattr(order, "current_document_id", "") or "").strip():
            return {}
        received_at = getattr(order, "received_at", None)
        return {
            "fax_version_no": 1,
            "fax_version_count": 1,
            "fax_version_document_id": getattr(order, "current_document_id", "") or "",
            "fax_version_message_id": getattr(order, "message_id", "") or "",
            "fax_version_received_at": received_at.isoformat() if hasattr(received_at, "isoformat") else "",
        }
    current = next((row for row in rows if bool(row.is_current)), rows[0])
    return {
        "fax_version_no": int(current.version_no or 0),
        "fax_version_count": len(rows),
        "fax_version_document_id": current.document_id,
        "fax_version_message_id": current.message_id,
        "fax_version_received_at": current.received_at.isoformat() if current.received_at else "",
    }


def _load_fax_source_manifest() -> dict[str, dict]:
    if not _ORDER_FORM_SOURCE_TEMPLATE_MANIFEST.exists():
        raise RuntimeError(
            f"order-form source manifest not found: {_ORDER_FORM_SOURCE_TEMPLATE_MANIFEST}"
        )
    payload = json.loads(_ORDER_FORM_SOURCE_TEMPLATE_MANIFEST.read_text(encoding="utf-8"))
    families = payload.get("families")
    if not isinstance(families, dict) or not families:
        raise RuntimeError(
            f"order-form source manifest has no families: {_ORDER_FORM_SOURCE_TEMPLATE_MANIFEST}"
        )
    normalized: dict[str, dict] = {}
    for template_id, raw_spec in families.items():
        if not isinstance(raw_spec, dict):
            raise RuntimeError(f"invalid order-form source spec: {template_id}")
        family_label = str(raw_spec.get("family_label") or "").strip()
        if not family_label:
            raise RuntimeError(f"missing family_label for order-form source spec: {template_id}")
        month_sources_raw = raw_spec.get("month_sources") or {}
        month_sources: dict[str, str] = {}
        if isinstance(month_sources_raw, dict):
            for month_id, filename in month_sources_raw.items():
                normalized_month = _normalize_month_id(month_id)
                filename_text = str(filename or "").strip()
                if not filename_text:
                    raise RuntimeError(
                        f"missing filename for order-form source spec: {template_id} month={month_id}"
                    )
                month_sources[normalized_month] = filename_text
        source_workbook = str(raw_spec.get("source_workbook") or "").strip()
        if not month_sources and not source_workbook:
            raise RuntimeError(
                f"order-form source spec must define month_sources or source_workbook: {template_id}"
            )
        normalized[str(template_id).strip()] = {
            "family_label": family_label,
            "month_sources": month_sources,
            "source_workbook": source_workbook,
        }
    return normalized


def list_order_form_patterns() -> list[dict]:
    return config_service.get_order_form_patterns()


def _normalize_month_id(month_id: str) -> str:
    value = str(month_id or "").strip()
    if not _MONTH_ID_RE.match(value):
        raise ValueError("month_id must be YYYY-MM")
    return value


_FAX_SOURCE_TEMPLATE_DIR = _resolve_fax_source_template_dir()
_FAX_FAMILY_SOURCE_MAP = _load_fax_source_manifest()
_SOURCE_WORKBOOK_SHEET_CACHE: dict[str, tuple[str, ...]] = {}
_SOURCE_WORKBOOK_MERGED_QUANTITY_CACHE: dict[tuple[str, str], bool] = {}
_ORDER_FORM_HEADER_ROWS = (7, 8, 9)
_ORDER_FORM_QUANTITY_HEADER_KEYWORDS = (
    "常食",
    "糖尿",
    "軟菜",
    "ミキサ",
    "ﾐｷｻ",
    "禁食",
    "肉禁",
    "魚禁",
    "職員",
    "通所",
    "変更",
    "袋分",
    "合計",
    "その他",
    "2F",
    "3F",
    "２F",
    "３F",
    "２Ｆ",
    "３Ｆ",
    "1回",
    "2回",
    "3回",
    "１回",
    "２回",
    "３回",
)
_ORDER_FORM_NON_QUANTITY_HEADER_KEYWORDS = (
    "日付",
    "区分",
    "献立",
    "メニュー",
    "備考",
)


def _resolve_pattern(facility: dict, pattern_id: str | None, *, master: dict) -> dict:
    selected = pattern_id if pattern_id is not None else facility.get("order_form_pattern_id")
    if selected is None or (pattern_id is None and selected == ""):
        return {}
    if not isinstance(selected, str) or not selected.strip():
        raise ValueError("order form pattern invalid")
    selected = selected.strip()
    patterns = master.get("order_form_patterns", [])
    if not isinstance(patterns, list) or any(not isinstance(pattern, dict) for pattern in patterns):
        raise ValueError("order form patterns invalid")
    matches = [pattern for pattern in patterns if str(pattern.get("pattern_id") or "").strip() == selected]
    if len(matches) != 1:
        raise ValueError("order form pattern not found" if not matches else "order form pattern ambiguous")
    pattern = deepcopy(matches[0])
    pattern["pattern_id"] = selected
    return pattern


def _safe_source_workbook_suffix(value: str) -> str:
    suffix = Path(str(value or "")).suffix.lower()
    return suffix if suffix in {".xlsx", ".xlsm"} else ".xlsx"


def _materialize_source_workbook_uri(uri: str) -> Path:
    data = load_bytes_from_uri(uri)
    digest = hashlib.sha256(data).hexdigest()
    target = _SOURCE_WORKBOOK_MATERIALIZED_DIR / f"{digest}{_safe_source_workbook_suffix(uri)}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(data)
    return target


def _resolve_source_workbook_path(source_workbook_name: str) -> Path:
    source_ref = str(source_workbook_name or "").strip()
    parsed = urlparse(source_ref)
    if parsed.scheme in {"gs", "file"}:
        source_path = _materialize_source_workbook_uri(source_ref)
    else:
        direct_path = Path(source_ref)
        source_path = direct_path if direct_path.is_absolute() else _FAX_SOURCE_TEMPLATE_DIR / source_ref
    if not source_path.exists():
        raise ValueError(
            f"source workbook not found: {source_workbook_name} (dir={_FAX_SOURCE_TEMPLATE_DIR})"
        )
    return source_path


def _facility_source_workbook_names(facility: dict | None) -> list[str]:
    if not isinstance(facility, dict):
        return []
    source_names: list[str] = []
    raw_month_sources = facility.get("order_form_month_sources")
    if isinstance(raw_month_sources, dict):
        for month_id in sorted(raw_month_sources):
            source_workbook = str(raw_month_sources.get(month_id) or "").strip()
            if source_workbook:
                source_names.append(source_workbook)
    raw_month_source_uris = facility.get("order_form_month_source_uris")
    if isinstance(raw_month_source_uris, dict):
        for month_id in sorted(raw_month_source_uris):
            source_workbook = str(raw_month_source_uris.get(month_id) or "").strip()
            if source_workbook:
                source_names.append(source_workbook)
    source_workbook = str(facility.get("order_form_source_workbook") or "").strip()
    if source_workbook:
        source_names.append(source_workbook)
    source_workbook_uri = str(facility.get("order_form_source_workbook_uri") or "").strip()
    if source_workbook_uri:
        source_names.append(source_workbook_uri)
    result: list[str] = []
    seen: set[str] = set()
    for source_workbook in source_names:
        if source_workbook in seen:
            continue
        seen.add(source_workbook)
        result.append(source_workbook)
    return result


def _resolve_facility_source_workbook_name_for_month(
    facility: dict, *, fax_template_id: str, month_id: str,
) -> str:
    return _read_saved_sheet_source_workbook(facility, _normalize_month_id(month_id))["reference"]


def _source_workbook_sheetnames(source_workbook_name: str) -> tuple[str, ...]:
    source_path = _resolve_source_workbook_path(source_workbook_name)
    cache_key = str(source_path)
    cached = _SOURCE_WORKBOOK_SHEET_CACHE.get(cache_key)
    if cached is not None:
        return cached
    workbook = load_workbook(source_path, read_only=True)
    try:
        sheetnames = tuple(workbook.sheetnames)
    finally:
        workbook.close()
    _SOURCE_WORKBOOK_SHEET_CACHE[cache_key] = sheetnames
    return sheetnames


def _normalize_order_form_header_text(value: object) -> str:
    return re.sub(r"[\s　]+", "", str(value or "")).strip()


def _worksheet_effective_merged_values(worksheet) -> dict[tuple[int, int], object]:
    values: dict[tuple[int, int], object] = {}
    for merged_range in worksheet.merged_cells.ranges:
        min_col, min_row, max_col, max_row = range_boundaries(str(merged_range))
        anchor_value = worksheet.cell(row=min_row, column=min_col).value
        for row_idx in range(min_row, max_row + 1):
            for col_idx in range(min_col, max_col + 1):
                values[(row_idx, col_idx)] = anchor_value
    return values


def _worksheet_quantity_column_indexes(worksheet) -> set[int]:
    merged_values = _worksheet_effective_merged_values(worksheet)
    quantity_columns: set[int] = set()
    for col_idx in range(1, worksheet.max_column + 1):
        header_blob = "".join(
            _normalize_order_form_header_text(
                worksheet.cell(row=row_idx, column=col_idx).value
                or merged_values.get((row_idx, col_idx))
            )
            for row_idx in _ORDER_FORM_HEADER_ROWS
        )
        if not header_blob:
            continue
        if any(keyword in header_blob for keyword in _ORDER_FORM_NON_QUANTITY_HEADER_KEYWORDS):
            continue
        if any(keyword in header_blob for keyword in _ORDER_FORM_QUANTITY_HEADER_KEYWORDS):
            quantity_columns.add(col_idx)
    return quantity_columns


def _worksheet_has_vertical_merged_quantity_cells(worksheet) -> bool:
    quantity_columns = _worksheet_quantity_column_indexes(worksheet)
    if not quantity_columns:
        return False
    for merged_range in worksheet.merged_cells.ranges:
        min_col, min_row, max_col, max_row = range_boundaries(str(merged_range))
        if max_row <= min_row:
            continue
        if max_row < _ORDER_FORM_BODY_START_ROW or min_row > _ORDER_FORM_BODY_END_ROW:
            continue
        if any(col_idx in quantity_columns for col_idx in range(min_col, max_col + 1)):
            return True
    return False


def source_workbook_has_vertical_merged_quantity_cells(
    source_workbook_name: str,
    *,
    week_sheet_name: str | None = None,
) -> bool:
    source_path = _resolve_source_workbook_path(source_workbook_name)
    cache_key = (str(source_path), str(week_sheet_name or ""))
    cached = _SOURCE_WORKBOOK_MERGED_QUANTITY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    workbook = load_workbook(source_path, data_only=True)
    try:
        if week_sheet_name:
            if week_sheet_name not in workbook.sheetnames:
                raise ValueError(f"week sheet not found in source workbook: {week_sheet_name}")
            worksheets = [workbook[week_sheet_name]]
        else:
            worksheets = list(workbook.worksheets)
        detected = any(_worksheet_has_vertical_merged_quantity_cells(worksheet) for worksheet in worksheets)
    finally:
        workbook.close()
    _SOURCE_WORKBOOK_MERGED_QUANTITY_CACHE[cache_key] = detected
    return detected


def facility_template_has_vertical_merged_quantity_cells(
    facility: dict[str, Any] | None,
    *,
    week_sheet_name: str | None = None,
) -> bool:
    _ = week_sheet_name
    if not isinstance(facility, dict):
        return False
    if isinstance(facility.get("expanded_cell_same_daypart_copy_enabled"), bool):
        return bool(facility.get("expanded_cell_same_daypart_copy_enabled"))
    template_candidates = [
        facility.get("fax_template"),
    ]
    for template in template_candidates:
        if not isinstance(template, dict):
            continue
        policy = template.get("body_merge_policy")
        if isinstance(policy, dict):
            mode = str(policy.get("mode") or "").strip().lower()
            columns = [str(item or "").strip() for item in (policy.get("columns") or []) if str(item or "").strip()]
            if mode == "daypart" and columns:
                return True
        for column in template.get("columns") or []:
            if not isinstance(column, dict):
                continue
            body_merge = str(column.get("body_merge") or column.get("body_merge_mode") or "").strip().lower()
            if body_merge == "daypart":
                return True
    return False


def _resolve_source_workbook_name_for_week_sheet(fax_template_id: str, week_sheet_name: str) -> str:
    spec = _resolve_fax_family_spec(fax_template_id)
    month_sources = spec.get("month_sources") or {}
    for month_id in sorted(month_sources):
        source_workbook_name = str(month_sources[month_id])
        if week_sheet_name in _source_workbook_sheetnames(source_workbook_name):
            return source_workbook_name
    source_workbook = str(spec.get("source_workbook") or "").strip()
    if source_workbook:
        return source_workbook
    raise ValueError(
        f"week sheet not configured for fax_template_id={fax_template_id}: {week_sheet_name}"
    )


def resolve_facility_source_workbook_name_for_week_sheet(facility: dict, week_sheet_name: str) -> str:
    fax_template_id = str(_infer_fax_template_id_from_facility(facility) or "").strip()
    if not fax_template_id:
        raise ValueError("facility fax_template_id not found")
    for source_workbook_name in _facility_source_workbook_names(facility):
        if week_sheet_name in _source_workbook_sheetnames(source_workbook_name):
            return source_workbook_name
    return _resolve_source_workbook_name_for_week_sheet(fax_template_id, week_sheet_name)


def save_facility_source_workbook_upload(
    *,
    facility_id: str,
    filename: str,
    data: bytes,
) -> dict[str, Any]:
    if not data:
        raise ValueError("source workbook is empty")
    suffix = _safe_source_workbook_suffix(filename)
    digest = hashlib.sha256(data).hexdigest()
    safe_name = _sanitize_filename_fragment(Path(filename or "source_workbook").stem)
    object_name = f"{safe_name}_{digest[:12]}{suffix}"
    bucket = get_default_output_bucket()
    if not bucket:
        raise ValueError("source_workbook_gcs_storage_required")
    uri = save_artifact_bytes_to_gcs(
        bucket,
        f"facility-template-{facility_id}",
        object_name,
        data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    # Validate the uploaded workbook before it is referenced by facility config.
    workbook_path = _resolve_source_workbook_path(uri)
    workbook = load_workbook(workbook_path, read_only=True)
    try:
        sheetnames = list(workbook.sheetnames)
    finally:
        workbook.close()
    if not sheetnames:
        raise ValueError("source workbook has no sheets")
    return {
        "uri": uri,
        "storage": "gcs",
        "filename": filename,
        "sha256": digest,
        "sheetnames": sheetnames,
    }


def _resolve_monthly_facility(
    snapshot: canonical_facility_store.MasterSnapshot, facility_id: str, month_id: str,
) -> dict:
    month_start = date.fromisoformat(f"{month_id}-01")
    selections = [canonical_facility_store.resolve_facility_version(snapshot, facility_id, month_start + timedelta(days=day))
                  for day in range(_month_end(month_start).day)]
    version_ids = {selection["version"]["version_id"] for selection in selections}
    if len(version_ids) != 1:
        raise canonical_facility_store.CanonicalStoreError(
            "order_form_month_multiple_versions_unresolved", status_code=409,
            facility_id=facility_id, month_id=month_id, version_ids=sorted(version_ids),
        )
    # Every calendar date has resolved uniquely against this same captured master.
    return config_service._validate_authored_facility_config(selections[0]["facility"], None)


def _infer_fax_template_id_from_facility(facility: dict) -> str | None:
    explicit = str(facility.get("fax_template_id") or "").strip()
    if explicit:
        return explicit
    return None


def _parse_menu_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except Exception:
        return None


def _collect_menu_entries(month_id: str, facility_id: str) -> list[dict]:
    payload = menu_service.get_menu_for_facility(month_id, facility_id)
    if not payload:
        raise ValueError("monthly menu not found")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("monthly menu entries not found")
    normalized: list[dict] = []
    for source_index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError("monthly menu entry invalid")
        menu_date = _parse_menu_date(entry.get("menu_date"))
        daypart = entry.get("daypart")
        name = entry.get("name")
        if not menu_date or not daypart or not name:
            raise ValueError("monthly menu entry identity unresolved")
        normalized.append(
            {
                **entry,
                "_menu_date_obj": menu_date,
                "_source_index": source_index,
            }
        )
    if not normalized:
        raise ValueError("no usable menu entries")
    return normalized


def _month_end(month_start: date) -> date:
    if month_start.month == 12:
        next_month = date(month_start.year + 1, 1, 1)
    else:
        next_month = date(month_start.year, month_start.month + 1, 1)
    return next_month - timedelta(days=1)


def _build_week_ranges_for_month(month_id: str) -> list[tuple[date, date]]:
    month_start = date.fromisoformat(f"{month_id}-01")
    month_end = _month_end(month_start)
    ranges: list[tuple[date, date]] = []
    current = month_start
    while current <= month_end:
        days_until_saturday = (5 - current.weekday()) % 7
        week_end = min(current + timedelta(days=days_until_saturday), month_end)
        ranges.append((current, week_end))
        current = week_end + timedelta(days=1)
    return ranges


def _format_week_sheet_name(start_date: date, end_date: date) -> str:
    return f"{start_date.month}月{start_date.day}日～{end_date.month}月{end_date.day}日"


def _select_entries_for_range(entries: list[dict], start_date: date, end_date: date) -> list[dict]:
    return [
        entry
        for entry in entries
        if isinstance(entry.get("_menu_date_obj"), date) and start_date <= entry["_menu_date_obj"] <= end_date
    ]


def _clone_sheet_images(source_worksheet, target_worksheet) -> None:
    for image in getattr(source_worksheet, "_images", []):
        target_worksheet.add_image(deepcopy(image))


def _ensure_workbook_sheet_count(workbook: Workbook, template_sheet_name: str, target_count: int) -> None:
    if target_count < 1:
        raise ValueError("target_count must be >= 1")
    template_sheet = workbook[template_sheet_name]
    while len(workbook.sheetnames) < target_count:
        copied = workbook.copy_worksheet(template_sheet)
        _clone_sheet_images(template_sheet, copied)
    while len(workbook.sheetnames) > target_count:
        del workbook[workbook.sheetnames[-1]]


def _clear_week_sheet_body(worksheet) -> None:
    for row in worksheet.iter_rows(
        min_row=_ORDER_FORM_BODY_START_ROW,
        max_row=_ORDER_FORM_BODY_END_ROW,
        min_col=1,
        max_col=worksheet.max_column,
    ):
        for cell in row:
            if isinstance(cell, MergedCell):
                continue
            cell.value = None


def _daypart_key(value: object) -> str:
    return str(value or "").strip()


def _week_menu_daypart_key(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith("朝"):
        return "朝"
    if text.startswith("昼"):
        return "昼"
    if text.startswith("夕"):
        return "夕"
    return text


def _week_menu_daypart_capacity(value: object) -> int | None:
    key = _week_menu_daypart_key(value)
    if key == "朝":
        return 2
    if key in {"昼", "夕"}:
        return 3
    return None


def _week_entry_overflow_payload(entry: dict) -> dict[str, str]:
    menu_date = entry.get("_menu_date_obj")
    return {
        "date": menu_date.isoformat() if isinstance(menu_date, date) else str(entry.get("menu_date") or ""),
        "daypart": str(entry.get("daypart") or ""),
        "category": str(entry.get("category") or ""),
        "name": str(entry.get("name") or ""),
    }


def _split_week_entries_for_order_form(week_entries: list[dict]) -> tuple[list[dict], list[dict[str, str]], int]:
    for entry in week_entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get("_menu_date_obj"), date)
                or not str(entry.get("daypart") or "").strip() or not str(entry.get("name") or "").strip()):
            raise ValueError("week menu entry identity unresolved")
    return list(week_entries), [], len(week_entries)


def _write_facility_name_in_box(worksheet, facility_name: str) -> None:
    text = str(facility_name or "").strip()
    name_length = len(text)
    if name_length <= 10:
        font_size = 18
    elif name_length <= 16:
        font_size = 16
    elif name_length <= 24:
        font_size = 14
    elif name_length <= 32:
        font_size = 12
    else:
        font_size = 10
    worksheet["A4"] = text
    worksheet["A4"].font = Font(name="Meiryo", size=font_size, bold=True)
    worksheet["A4"].alignment = Alignment(horizontal="left", vertical="center", shrink_to_fit=True, indent=1)


def _weekday_label(menu_date: date) -> str:
    return _WEEKDAY_LABELS[menu_date.weekday()]


def _column_merged_ranges(worksheet, col_idx: int) -> list[tuple[int, int, int, int]]:
    ranges: list[tuple[int, int, int, int]] = []
    for merged_range in worksheet.merged_cells.ranges:
        min_col, min_row, max_col, max_row = range_boundaries(str(merged_range))
        if min_col <= col_idx <= max_col:
            ranges.append((min_col, min_row, max_col, max_row))
    return sorted(ranges, key=lambda item: (item[1], item[0], item[3], item[2]))


def _write_weekday_label_for_date_block(
    worksheet,
    *,
    date_start_row: int,
    date_end_row: int,
    menu_date: date,
) -> None:
    date_range: tuple[int, int, int, int] | None = None
    col_ranges = _column_merged_ranges(worksheet, 1)
    for merged in col_ranges:
        _min_col, min_row, _max_col, max_row = merged
        if min_row <= date_start_row <= max_row:
            date_range = merged
            break
    target_row = date_end_row
    if date_range is not None:
        _min_col, _min_row, _max_col, date_range_end = date_range
        for merged in col_ranges:
            _min_col, min_row, _max_col, max_row = merged
            if min_row > date_range_end and min_row <= date_end_row <= max_row:
                target_row = min_row
                break
    _write_to_cell_or_merged_anchor(
        worksheet,
        row_idx=target_row,
        col_idx=1,
        value=_weekday_label(menu_date),
    )


def _write_week_entries(worksheet, week_entries: list[dict], *, facility_config: dict) -> WeekEntriesWriteResult:
    physical_entries, overflow_entries, source_rows = _split_week_entries_for_order_form(week_entries)
    if not physical_entries:
        raise ValueError("week menu source unavailable")
    targets = _worksheet_quantity_column_role_map(worksheet, template=facility_config.get("fax_template"))
    merge_columns = _source_body_merge_columns(worksheet, facility_config=facility_config, targets=targets)
    plan = master_order_form_template_service.write_source_workbook_body(
        worksheet, physical_entries, declared_body_end_row=_ORDER_FORM_BODY_END_ROW,
        body_merge_columns=merge_columns, source_marker_row=_BOTTOM_MARKER_ROW,
    )
    result = WeekEntriesWriteResult(plan["written_rows"], source_rows=source_rows, overflow_entries=overflow_entries)
    result.body_plan = plan
    return result


def _sheet_field_indexes(fields: list[Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for idx, field in enumerate(fields):
        normalized = str(field or "").strip()
        if normalized and normalized not in result:
            result[normalized] = idx
    return result


def _normalize_sheet_label(value: object) -> str:
    return re.sub(r"[\s　・]+", "", str(value or "")).strip().lower()


def _sheet_field_label(fields: list[Any], header: list[Any], idx: int) -> str:
    header_text = str(header[idx] or "").strip() if idx < len(header) else ""
    return header_text or str(fields[idx] or "").strip()


def _first_field_index(field_indexes: dict[str, int], candidates: tuple[str, ...]) -> int | None:
    for candidate in candidates:
        if candidate in field_indexes:
            return field_indexes[candidate]
    return None


def _sheet_cell(row: list[Any], idx: int | None) -> object:
    if idx is None or idx < 0 or idx >= len(row):
        return None
    return row[idx]


def _coerce_quantity_cell(value: object) -> object:
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    normalized = text.replace(",", "")
    try:
        number = float(normalized)
    except ValueError:
        return text
    if number.is_integer():
        return int(number)
    return number


def _field_role(field: object, label: object = "") -> str | None:
    field_text = str(field or "").strip().lower()
    label_text = _normalize_sheet_label(label)
    token = f"{field_text} {label_text}"
    if field_text in {"date", "menu_date", "date_mmdd"} or label_text in {"日付", "日にち"}:
        return "date"
    if field_text in {"daypart", "meal"} or label_text in {"区分", "食区分"}:
        return "daypart"
    if field_text in {"category", "menu_category"} or label_text in {"献立区分", "副区分", "区分2"}:
        return "category"
    if field_text in {"menu_name", "name", "menu"} or label_text in {"献立", "メニュー"}:
        return "menu"
    if field_text in {"remarks", "remark", "note", "notes"} or "備考" in label_text:
        return "remarks"
    if "合計" in label_text or "total" in token:
        return "total"
    if "常食" in label_text or "regular" in token:
        return "regular"
    if "通所" in label_text or "daycare" in token:
        return "daycare"
    if "職員" in label_text or "staff" in token:
        return "staff"
    if "肉禁" in label_text or "nomeat" in token or "no_meat" in token:
        return "no_meat"
    if "魚禁" in label_text or "nofish" in token or "no_fish" in token:
        return "no_fish"
    if "その他" in label_text or "forbiddenother" in token or "forbidden_other" in token:
        return "forbidden_other"
    if "揚禁" in label_text or "nofried" in token or "no_fried" in token:
        return "no_fried"
    if "変更①" in label_text or "変更1" in label_text or "change_1" in token:
        return "change_1"
    if "変更②" in label_text or "変更2" in label_text or "change_2" in token:
        return "change_2"
    if "placeholder" in token or "unknown" in token or "不明" in label_text:
        return None
    return None


def _worksheet_quantity_column_role_map(worksheet, *, template: dict) -> dict[str, int]:
    from src.services.template_field_schema_service import canonical_field_name_from_template_column

    columns = template.get("columns") if isinstance(template, dict) else None
    if not isinstance(columns, list) or not columns:
        raise ValueError("source workbook canonical columns missing")
    effective = _worksheet_effective_merged_values(worksheet)
    result = {}
    occupied = set()
    quantity_targets = set()
    structural_targets = {"date": 1, "daypart": 2, "menu": 4, "menu_name": 4, "category": 3}
    for column in columns:
        if not isinstance(column, dict):
            raise ValueError("source workbook canonical column invalid")
        role = str(column.get("role") or "").strip()
        name = canonical_field_name_from_template_column(column)
        if role == "category":
            name = "category"
        source_index = column.get("source_index")
        if (not name or name in result or not isinstance(source_index, int) or isinstance(source_index, bool)
                or source_index < 0 or source_index in occupied):
            raise ValueError("source workbook canonical column ambiguous")
        target = source_index + 1
        occupied.add(source_index)
        if role in structural_targets and target != structural_targets[role]:
            raise ValueError("source workbook structural layout mismatch")
        if role in {"quantity", "quantity_change"}:
            quantity_targets.add(target)
            if target < 5 or target > worksheet.max_column:
                raise ValueError("source workbook quantity column missing")
            parts = list(dict.fromkeys(
                _normalize_sheet_label(effective.get((row, target), worksheet.cell(row, target).value))
                for row in range(7, 11)
            ))
            parts = [part for part in parts if part]
            expected = _normalize_sheet_label(column.get("delivery_header") or column.get("header"))
            if expected:
                matches = expected in parts or expected == "".join(parts)
            else:
                # Validate the authored destination; never choose a destination from a header.
                diet = config_service._parse_quantity_field_name(name)["diet"]
                matches = any(config_service._normalize_field_diet_token(part) == diet for part in parts)
            if not matches:
                raise ValueError(f"source workbook quantity header mismatch:{name}")
        result[name] = target
    if not quantity_targets or _worksheet_quantity_column_indexes(worksheet) - quantity_targets:
        raise ValueError("source workbook canonical quantity columns incomplete")
    return result


def _source_body_merge_columns(worksheet, *, facility_config: dict, targets: dict[str, int]) -> set[int]:
    from src.services.template_field_schema_service import canonical_field_name_from_template_column

    columns = [
        {**column, "name": canonical_field_name_from_template_column(column)}
        for column in facility_config["fax_template"]["columns"]
    ]
    selected = master_order_form_template_service._configured_body_merge_columns(
        facility_config=facility_config, generated_columns=columns, use_source_indexes=True,
    )
    result = {target for target, _column, _mode in selected}
    if any(target < 5 or target > worksheet.max_column or target not in targets.values() for target in result):
        raise ValueError("source workbook merge column unmapped")
    return result


def _write_to_cell_or_merged_anchor(worksheet, *, row_idx: int, col_idx: int, value: object) -> None:
    cell = worksheet.cell(row=row_idx, column=col_idx)
    if not isinstance(cell, MergedCell):
        cell.value = value
        return
    for merged_range in worksheet.merged_cells.ranges:
        min_col, min_row, max_col, max_row = range_boundaries(str(merged_range))
        if min_row <= row_idx <= max_row and min_col <= col_idx <= max_col:
            anchor = worksheet.cell(row=min_row, column=min_col)
            if anchor.value in (None, "") or anchor.value == value:
                anchor.value = value
            return


def _write_saved_sheet_rows_to_order_form(
    worksheet, *, fields: list[Any], header: list[Any] | None = None,
    rows: list[Any], received_at: datetime, template: dict, week_code: str, facility_config: dict,
) -> WeekEntriesWriteResult:
    from src.services.order_service import _materialization_identity_rows, _resolve_structural_row_field_indexes
    from src.services.template_field_schema_service import canonical_field_name_from_template_column

    identities, issues = _materialization_identity_rows(
        fields=fields, rows_payload=rows, template=template, existing_week_code=week_code,
    )
    if issues or not identities:
        raise ValueError("saved sheet row identity unresolved")
    targets = _worksheet_quantity_column_role_map(worksheet, template=template)
    date_index, daypart_index, menu_index, _quantity_indexes = _resolve_structural_row_field_indexes(fields)
    identity_indexes = {date_index, daypart_index, menu_index}
    category_indexes = [index for index, field in enumerate(fields) if field in {"category", "menu_category"}]
    if len(category_indexes) > 1:
        raise ValueError("saved sheet category field ambiguous")
    identity_indexes.update(category_indexes)
    if any(field not in targets for index, field in enumerate(fields) if index not in identity_indexes):
        raise ValueError("saved sheet canonical field unmapped")
    configured = {
        canonical_field_name_from_template_column(column): column for column in template["columns"]
    }
    quantity_names = {name for name, column in configured.items() if column.get("role") in {"quantity", "quantity_change"}}
    if not quantity_names or not quantity_names.issubset(fields):
        raise ValueError("saved sheet quantity fields missing")
    field_indexes = {field: index for index, field in enumerate(fields)}
    category_name = fields[category_indexes[0]] if category_indexes else None

    def raw_cell(raw_index: int, field: str) -> object:
        row = rows[raw_index]
        return row.get(field) if isinstance(row, dict) else _sheet_cell(row, field_indexes[field])

    entries = [
        {"_source_index": raw_index, "_menu_date_obj": menu_date, "daypart": daypart, "name": menu,
         "category": raw_cell(raw_index, category_name) if category_name in field_indexes else ""}
        for raw_index, menu_date, daypart, menu in identities
    ]
    result = _write_week_entries(worksheet, entries, facility_config=facility_config)
    writes = {}
    for row in result.body_plan["physical_row_plan"]:
        for index, field in enumerate(fields):
            if index in identity_indexes:
                continue
            value = raw_cell(row["source_index"], field)
            writes[(row["worksheet_row"], targets[field])] = (
                _coerce_quantity_cell(value) if field in quantity_names else value
            )
    for region in worksheet.merged_cells.ranges:
        if region.min_row < _ORDER_FORM_BODY_START_ROW or region.min_row > result.body_plan["body_end_row"]:
            continue
        keys = [(row, column) for row in range(region.min_row, region.max_row + 1)
                for column in range(region.min_col, region.max_col + 1) if (row, column) in writes]
        if keys:
            value = writes[keys[0]]
            if any(writes[key] != value for key in keys):
                raise ValueError("saved sheet source merge quantity conflict")
            for key in keys:
                del writes[key]
            writes[(region.min_row, region.min_col)] = value
    for (row, column), value in writes.items():
        worksheet.cell(row, column).value = value
    return result


def _set_deadline_text_for_week(worksheet, start_date: date) -> None:
    deadline = start_date - timedelta(days=16)
    deadline_text = f"締切日{deadline.month}月{deadline.day}日まで"
    for row_idx in range(1, _ORDER_FORM_DEADLINE_SEARCH_MAX_ROW + 1):
        for col_idx in range(1, _ORDER_FORM_DEADLINE_SEARCH_MAX_COL + 1):
            cell = worksheet.cell(row=row_idx, column=col_idx)
            value = str(cell.value or "")
            if value.startswith("締切日"):
                cell.value = deadline_text
                return


def _append_monthly_metadata_sheet(
    workbook: Workbook,
    *,
    source_workbook_name: str,
    facility_id: str,
    facility_name: str,
    month_id: str,
    pattern: dict,
    fax_template_id: str,
    family_label: str,
    week_ranges: list[tuple[date, date]],
    entry_count: int,
    week_write_results: list[WeekEntriesWriteResult] | None = None,
) -> None:
    if "設定" in workbook.sheetnames:
        del workbook["設定"]
    meta = workbook.create_sheet("設定")
    meta.sheet_state = "hidden"
    meta.append(["key", "value"])
    meta.append(["generated_at_utc", datetime.utcnow().isoformat()])
    meta.append(["source_workbook", source_workbook_name])
    meta.append(["facility_id", facility_id])
    meta.append(["facility_name", facility_name])
    meta.append(["month_id", month_id])
    meta.append(["pattern_id", str(pattern.get("pattern_id") or "")])
    meta.append(["pattern_label", str(pattern.get("label") or "")])
    meta.append(["fax_template_id", fax_template_id])
    meta.append(["family_label", family_label])
    meta.append(["sheet_count", len(week_ranges)])
    meta.append(["entry_count", entry_count])
    total_overflow_rows = sum(int(result.overflow_rows) for result in (week_write_results or []))
    meta.append(["week_menu_overflow_rows", total_overflow_rows])
    for index, (start_date, end_date) in enumerate(week_ranges, start=1):
        meta.append([f"week_{index}_sheet_name", _format_week_sheet_name(start_date, end_date)])
        meta.append([f"week_{index}_start", start_date.isoformat()])
        meta.append([f"week_{index}_end", end_date.isoformat()])
        if week_write_results and index <= len(week_write_results):
            result = week_write_results[index - 1]
            meta.append([f"week_{index}_written_rows", int(result.written_rows)])
            meta.append([f"week_{index}_source_rows", int(result.source_rows)])
            meta.append([f"week_{index}_overflow_rows", int(result.overflow_rows)])
            meta.append([f"week_{index}_physical_row_plan", json.dumps(result.body_plan["physical_row_plan"], ensure_ascii=False)])
            if result.overflow_rows:
                meta.append(
                    [
                        f"week_{index}_overflow_entries",
                        json.dumps(result.overflow_entries, ensure_ascii=False, sort_keys=True),
                    ]
                )


def _build_monthly_fax_order_form_workbook(
    *,
    facility: dict,
    month_id: str,
    entries: list[dict],
    pattern: dict,
    source_workbook: dict[str, Any] | None = None,
) -> Workbook:
    fax_template_id = str(_infer_fax_template_id_from_facility(facility) or "").strip()
    if not fax_template_id:
        raise ValueError("facility fax_template_id not found")
    spec = _resolve_fax_family_spec(fax_template_id)
    source = source_workbook if source_workbook is not None else _read_saved_sheet_source_workbook(facility, month_id)
    source_workbook_name = source["reference"]
    workbook = load_workbook(BytesIO(source["bytes"]))
    template_sheet_name = _DEFAULT_WEEK_SHEET if _DEFAULT_WEEK_SHEET in workbook.sheetnames else workbook.sheetnames[0]
    week_ranges = _build_week_ranges_for_month(month_id)
    _ensure_workbook_sheet_count(workbook, template_sheet_name, len(week_ranges))

    facility_id = str(facility.get("facility_id") or facility.get("id") or "")
    facility_name = str(facility.get("facility_name") or facility.get("name") or facility_id)
    family_label = str(spec["family_label"])
    week_write_results: list[WeekEntriesWriteResult] = []

    for index, (start_date, end_date) in enumerate(week_ranges):
        worksheet = workbook.worksheets[index]
        sheet_name = _format_week_sheet_name(start_date, end_date)
        worksheet.title = sheet_name
        _write_facility_name_in_box(worksheet, facility_name)
        _set_deadline_text_for_week(worksheet, start_date)
        week_write_results.append(
            _write_week_entries(worksheet, _select_entries_for_range(entries, start_date, end_date), facility_config=facility)
        )
        _apply_fax_metadata_header(
            worksheet,
            fax_template_id=fax_template_id,
            facility_id=facility_id,
            facility_name=facility_name,
            week_sheet_name=sheet_name,
            family_label=family_label,
        )
        layout = week_write_results[-1].body_plan
        _apply_fax_markers(worksheet, bottom_row=layout["marker_row"])
        _apply_bottom_instruction_strip(worksheet, fax_template_id=fax_template_id, base_label="monthly", bottom_row=layout["instruction_row"])
        _extend_print_area(worksheet, bottom_row=max(layout["marker_row"], layout["instruction_row"]), row_delta=layout["row_delta"])

    _append_monthly_metadata_sheet(
        workbook,
        source_workbook_name=source_workbook_name,
        facility_id=facility_id,
        facility_name=facility_name,
        month_id=month_id,
        pattern=pattern,
        fax_template_id=fax_template_id,
        family_label=family_label,
        week_ranges=week_ranges,
        entry_count=len(entries),
        week_write_results=week_write_results,
    )
    workbook.active = 0
    return workbook


def build_order_form_excel(
    *,
    facility_id: str,
    month_id: str,
    pattern_id: str | None = None,
) -> Path:
    normalized_month = _normalize_month_id(month_id)
    snapshot = canonical_facility_store.read_snapshot()
    facility = _resolve_monthly_facility(snapshot, facility_id, normalized_month)
    entries = _collect_menu_entries(normalized_month, facility_id)
    pattern = _resolve_pattern(facility, pattern_id, master=snapshot.master)
    source = _read_saved_sheet_source_workbook(facility, normalized_month)
    resolved_fax_template_id = _infer_fax_template_id_from_facility(facility)
    wb = _build_monthly_fax_order_form_workbook(
        facility=facility,
        month_id=normalized_month,
        entries=entries,
        pattern=pattern,
        source_workbook=source,
    )

    file_pattern = _sanitize_filename_fragment(str(resolved_fax_template_id))
    safe_facility_id = _sanitize_filename_fragment(facility_id)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_dir = _OUTPUT_DIR / uuid4().hex
    output = output_dir / f"order_form_{safe_facility_id}_{normalized_month}_{file_pattern}_{stamp}.xlsx"
    try:
        buffer = BytesIO()
        wb.save(buffer)
        _verify_monthly_output_sources(snapshot, facility, normalized_month, source)
        output_dir.mkdir()
        try:
            with output.open("xb") as target:
                target.write(buffer.getvalue())
            _verify_monthly_output_sources(snapshot, facility, normalized_month, source)
        except BaseException:
            output.unlink(missing_ok=True)
            output_dir.rmdir()
            raise
    finally:
        wb.close()
    return output


def _verify_monthly_output_sources(
    snapshot: canonical_facility_store.MasterSnapshot,
    facility: dict, month_id: str, source: dict[str, Any],
) -> None:
    current = canonical_facility_store.read_snapshot()
    canonical_facility_store.check_revision(current, snapshot.revision)
    if current.source != snapshot.source:
        raise canonical_facility_store.CanonicalStoreError("facility_master_revision_conflict", status_code=409)
    if _read_saved_sheet_source_workbook(facility, month_id) != source:
        raise ValueError("monthly order form source workbook changed")


def _resolve_week_range_for_saved_sheet(
    *,
    week_code: object,
    fields: list[Any],
    rows: list[Any],
    received_at: datetime,
) -> tuple[str, date, date]:
    month_id, start_date, end_date = sheet_week_service.parse_sheet_week_value(week_code)
    if month_id and isinstance(start_date, date) and isinstance(end_date, date):
        return month_id, start_date, end_date
    raise ValueError("order week is unresolved")


def _capture_saved_sheet_order_form_input(session, order_id: str) -> dict[str, Any]:
    from src.services import canonical_facility_lineage as lineage, ocr_evidence_service
    from src.services.canonical_facility_store import CanonicalStoreError
    from src.services.facility_template_version_service import template_version_applies_on

    order = session.get(Order, order_id)
    if order is None:
        raise ValueError("order not found")
    workflow = session.get(OrderWorkflowState, order_id)
    if workflow is None or not workflow.draft_id:
        raise ValueError("saved sheet not found")
    if type(workflow.revision) is not int:
        raise ValueError("saved sheet workflow revision missing")
    draft = session.get(OrderSheetDraft, workflow.draft_id)
    if draft is None or draft.order_id != order_id:
        raise ValueError("saved sheet order mismatch")
    evidence = session.get(OrderOcrEvidenceRun, workflow.evidence_run_id) if workflow.evidence_run_id else None
    if evidence is None or evidence.order_id != order_id or not ocr_evidence_service.evidence_run_is_selectable(evidence):
        raise ValueError("saved sheet selected OCR mismatch")
    if draft.base_evidence_run_id != evidence.id:
        raise ValueError("saved sheet selected OCR mismatch")
    template_version_id = workflow.template_version_id
    if not template_version_id or any(value != template_version_id for value in (
        order.template_version_id, draft.template_version_id, evidence.template_version_id,
    )):
        raise ValueError("saved sheet template mismatch")
    payload = draft.draft_sheet_json
    if not isinstance(payload, dict) or any(
        payload.get(key) and payload[key] != evidence.id
        for key in ("base_evidence_run_id", "evidence_run_id")
    ):
        raise ValueError("saved sheet OCR lineage mismatch")
    if payload.get("source") == "reparse_candidate" or payload.get("adopted") is False:
        raise ValueError("saved sheet candidate not adopted")
    fields, rows = payload.get("fields"), payload.get("rows")
    if not isinstance(fields, list) or not isinstance(rows, list) or not rows:
        raise ValueError("saved sheet rows not found")
    month_id, week_start, week_end = _resolve_week_range_for_saved_sheet(
        week_code=order.week_code, fields=fields, rows=rows, received_at=order.received_at,
    )
    facility_id = str(order.facility_code or "").strip()
    if not facility_id:
        raise ValueError("facility missing")
    workflow_meta = workflow.secondary_actions_json
    if workflow_meta is not None and not isinstance(workflow_meta, dict):
        raise ValueError("saved sheet context mismatch")
    meta = (workflow_meta or {}).get("workflow_v2", {})
    if not isinstance(meta, dict):
        raise ValueError("saved sheet context mismatch")
    for key, expected in (("facility_id", facility_id), ("week_start", week_start.isoformat()),
                          ("week_end", week_end.isoformat()), ("template_version_id", template_version_id)):
        if meta.get(key) and str(meta[key]) != expected:
            raise ValueError("saved sheet context mismatch")
    try:
        projection = lineage.read_projection(session, template_version_id)
        if projection is None:
            raise ValueError("saved sheet template missing")
        receipt = lineage.verify_projection(projection)
        selected, dated_projection = lineage.resolve(session, facility_id, week_start)
        if (receipt["facility_id"] != facility_id or dated_projection.id != projection.id
                or not template_version_applies_on(projection, week_start)):
            raise ValueError("saved sheet dated template mismatch")
        facility = config_service._validate_authored_facility_config(
            deepcopy(selected), projection.template_id,
        )
    except CanonicalStoreError as exc:
        raise ValueError(str(exc)) from exc
    if draft.base_template_resolution_id and draft.base_template_resolution_id != projection.template_id:
        raise ValueError("saved sheet template lineage mismatch")
    return deepcopy({
        "order_id": order_id, "facility_id": facility_id, "week_code": order.week_code,
        "received_at": order.received_at, "document_id": order.current_document_id,
        "workflow_revision": workflow.revision, "workflow_meta": workflow_meta,
        "draft_id": draft.id, "draft_state": draft.draft_state, "draft_edited_at": draft.edited_at,
        "draft_created_at": draft.created_at, "draft_payload": payload,
        "draft_base_template_resolution_id": draft.base_template_resolution_id, "draft_edited_by": draft.edited_by,
        "evidence_id": evidence.id, "evidence_payload": evidence.payload_json,
        "evidence_manifest": evidence.artifact_manifest_json, "evidence_status": evidence.status,
        "evidence_source": evidence.source, "evidence_producer": evidence.producer_version,
        "evidence_artifact_digest": evidence.artifact_digest,
        "template_version_id": template_version_id, "template_receipt": receipt,
        "facility": facility, "month_id": month_id, "week_start": week_start, "week_end": week_end,
        "fax_version": _current_order_version_metadata(session, order_id),
    })


def _read_saved_sheet_source_workbook(facility: dict, month_id: str) -> dict[str, Any]:
    month_refs = {}
    for key in ("order_form_month_sources", "order_form_month_source_uris"):
        mapping = facility.get(key)
        if mapping is not None and not isinstance(mapping, dict):
            raise ValueError("saved sheet source mapping invalid")
        if isinstance(mapping, dict) and month_id in mapping:
            month_refs[key] = mapping[month_id]
    selected = month_refs if month_refs else {key: facility[key] for key in (
        "order_form_source_workbook", "order_form_source_workbook_uri",
    ) if facility.get(key) is not None}
    refs = list(selected.values())
    if not refs or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
        raise ValueError("saved sheet source workbook not configured")
    refs = list(dict.fromkeys(ref.strip() for ref in refs))
    source_bytes = None
    for ref in refs:
        parsed = urlparse(ref)
        if parsed.scheme in {"gs", "file"}:
            data = load_bytes_from_uri(ref)
        elif parsed.scheme:
            raise ValueError("saved sheet source workbook URI invalid")
        else:
            path = Path(ref)
            data = (path if path.is_absolute() else _FAX_SOURCE_TEMPLATE_DIR / path).read_bytes()
        if not data:
            raise ValueError("saved sheet source workbook empty")
        if source_bytes is not None and source_bytes != data:
            raise ValueError("saved sheet source workbook ambiguous")
        source_bytes = data
    # References are aliases only after their actual bytes have been verified equal.
    reference = selected.get("order_form_month_source_uris") if month_refs else selected.get("order_form_source_workbook_uri")
    if reference is None:
        reference = selected["order_form_month_sources"] if month_refs else selected["order_form_source_workbook"]
    return {"reference": reference.strip(), "references": refs, "bytes": source_bytes,
            "sha256": hashlib.sha256(source_bytes).hexdigest()}


def build_saved_sheet_order_form_excel(*, order_id: str) -> Path:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        raise ValueError("order_id is required")
    generated_at = datetime.utcnow()
    with session_scope() as session:
        captured = _capture_saved_sheet_order_form_input(session, normalized_order_id)
    facility_id = captured["facility_id"]
    received_at = captured["received_at"]
    fax_version = captured["fax_version"]
    draft_payload = captured["draft_payload"]
    fields = draft_payload.get("fields")
    header = draft_payload.get("header")
    rows = draft_payload.get("rows")
    if not isinstance(fields, list) or not isinstance(rows, list) or not rows:
        raise ValueError("saved sheet rows not found")

    facility = captured["facility"]
    fax_template_id = str(_infer_fax_template_id_from_facility(facility) or "").strip()
    if not fax_template_id:
        raise ValueError("facility fax_template_id not found")
    spec = _resolve_fax_family_spec(fax_template_id)
    month_id, week_start, week_end = captured["month_id"], captured["week_start"], captured["week_end"]
    week_sheet_name = _format_week_sheet_name(week_start, week_end)
    source = _read_saved_sheet_source_workbook(facility, month_id)
    source_workbook_name = source["reference"]
    workbook = load_workbook(BytesIO(source["bytes"]))
    if week_sheet_name not in workbook.sheetnames:
        workbook.close()
        raise ValueError("saved sheet week not found in configured workbook")
    _keep_only_target_sheet(workbook, week_sheet_name)
    worksheet = workbook[week_sheet_name]

    facility_name = str(facility.get("facility_name") or facility.get("name") or facility_id)
    _write_facility_name(worksheet, facility_name)
    _write_facility_name_in_box(worksheet, facility_name)
    _set_deadline_text_for_week(worksheet, week_start)
    written_rows = _write_saved_sheet_rows_to_order_form(
        worksheet,
        fields=fields,
        header=header if isinstance(header, list) else None,
        rows=rows,
        received_at=received_at,
        template=facility.get("fax_template"),
        week_code=captured["week_code"],
        facility_config=facility,
    )
    if written_rows <= 0:
        raise ValueError("saved sheet has no writable rows")
    _apply_fax_metadata_header(
        worksheet,
        fax_template_id=fax_template_id,
        facility_id=facility_id,
        facility_name=facility_name,
        week_sheet_name=week_sheet_name,
        family_label=str(spec["family_label"]),
        generated_at=generated_at,
        label="FAX読取シートExcel作成",
        fax_version=fax_version,
    )
    layout = written_rows.body_plan
    _apply_fax_markers(worksheet, bottom_row=layout["marker_row"])
    _apply_bottom_instruction_strip(worksheet, fax_template_id=fax_template_id, base_label="saved_sheet", bottom_row=layout["instruction_row"])
    _extend_print_area(worksheet, bottom_row=max(layout["marker_row"], layout["instruction_row"]), row_delta=layout["row_delta"])
    _append_hidden_metadata_sheet(
        workbook,
        source_workbook_name=source_workbook_name,
        facility_id=facility_id,
        facility_name=facility_name,
        fax_template_id=fax_template_id,
        family_label=str(spec["family_label"]),
        week_sheet_name=week_sheet_name,
        base_label="saved_sheet",
        generated_at=generated_at,
        fax_version=fax_version,
    )
    metadata = workbook["設定"]
    metadata.append(["order_id", normalized_order_id])
    metadata.append(["month_id", month_id])
    metadata.append(["saved_sheet_draft_id", captured["draft_id"]])
    metadata.append(["saved_sheet_row_count", int(written_rows)])
    metadata.append(["physical_row_plan", json.dumps(written_rows.body_plan["physical_row_plan"], ensure_ascii=False)])

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_facility_id = _sanitize_filename_fragment(facility_id)
    safe_week = _sanitize_filename_fragment(week_sheet_name)
    output_dir = _OUTPUT_DIR / uuid4().hex
    output = output_dir / f"fax_read_sheet_excel_{normalized_order_id}_{safe_facility_id}_{safe_week}_{stamp}.xlsx"
    with session_scope() as session:
        current = _capture_saved_sheet_order_form_input(session, normalized_order_id)
        current_source = _read_saved_sheet_source_workbook(current["facility"], current["month_id"])
        if current != captured or current_source != source:
            workbook.close()
            raise ValueError("saved sheet output input changed")
    try:
        output_dir.mkdir()
        with output.open("xb") as target:
            workbook.save(target)
    finally:
        workbook.close()
    return output


def list_fax_order_form_template_specs() -> list[dict]:
    specs: list[dict] = []
    for template_id, payload in _FAX_FAMILY_SOURCE_MAP.items():
        month_sources = dict(payload.get("month_sources") or {})
        specs.append(
            {
                "fax_template_id": template_id,
                "source_workbook": payload.get("source_workbook") or next(iter(month_sources.values()), ""),
                "month_sources": month_sources,
                "family_label": payload["family_label"],
            }
        )
    return specs


def build_fax_base_template_excel(
    *,
    fax_template_id: str,
    week_sheet_name: str = _DEFAULT_WEEK_SHEET,
    output_dir: Path | str | None = None,
) -> Path:
    return _render_fax_order_form_workbook(
        source_workbook_name=_resolve_source_workbook_name_for_week_sheet(fax_template_id, week_sheet_name),
        week_sheet_name=week_sheet_name,
        facility_name="施設名記入欄",
        facility_id="BASE",
        fax_template_id=fax_template_id,
        family_label=str(_resolve_fax_family_spec(fax_template_id)["family_label"]),
        base_label="base",
        output_dir=output_dir,
    )


def build_fax_order_form_excel(
    *,
    facility_id: str,
    week_sheet_name: str = _DEFAULT_WEEK_SHEET,
    output_dir: Path | str | None = None,
) -> Path:
    facility = config_service.get_facility_config(facility_id)
    if not facility:
        raise ValueError("facility not found")
    fax_template_id = str(_infer_fax_template_id_from_facility(facility) or "").strip()
    if not fax_template_id:
        raise ValueError("facility fax_template_id not found")
    spec = _resolve_fax_family_spec(fax_template_id)
    facility_name = str(facility.get("facility_name") or facility.get("name") or facility_id)
    return _render_fax_order_form_workbook(
        source_workbook_name=resolve_facility_source_workbook_name_for_week_sheet(facility, week_sheet_name),
        week_sheet_name=week_sheet_name,
        facility_name=facility_name,
        facility_id=facility_id,
        fax_template_id=fax_template_id,
        family_label=str(spec["family_label"]),
        base_label="facility",
        output_dir=output_dir,
    )


def _clear_order_form_body_values(
    worksheet,
    *,
    start_row: int = _ORDER_FORM_BODY_START_ROW,
    end_row: int = _ORDER_FORM_BODY_END_ROW,
    min_col: int = 1,
    max_col: int | None = None,
) -> None:
    target_max_col = max_col if max_col is not None else worksheet.max_column
    for row in worksheet.iter_rows(
        min_row=start_row,
        max_row=end_row,
        min_col=min_col,
        max_col=target_max_col,
    ):
        for cell in row:
            if isinstance(cell, MergedCell):
                continue
            cell.value = None


def build_fax_structure_only_excel(
    *,
    facility_id: str,
    week_sheet_name: str = _DEFAULT_WEEK_SHEET,
    output_dir: Path | str | None = None,
) -> Path:
    from src.services import master_order_form_template_service  # noqa: PLC0415

    _ = week_sheet_name
    facility = config_service.get_facility_config(facility_id)
    if not facility:
        raise ValueError("facility not found")
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_facility_id = _sanitize_filename_fragment(facility_id)
    output_path = _resolve_fax_output_dir(output_dir) / f"fax_order_form_structure_only_{safe_facility_id}_{stamp}.xlsx"
    return master_order_form_template_service.build_facility_template_xlsx(
        facility_config=facility,
        output_path=output_path,
    )


def _resolve_fax_family_spec(fax_template_id: str) -> dict:
    template_key = str(fax_template_id or "").strip()
    spec = _FAX_FAMILY_SOURCE_MAP.get(template_key)
    if spec:
        return dict(spec)
    raise ValueError(f"unsupported fax_template_id for prototype generation: {template_key}")


def _render_fax_order_form_workbook(
    *,
    source_workbook_name: str,
    week_sheet_name: str,
    facility_name: str,
    facility_id: str,
    fax_template_id: str,
    family_label: str,
    base_label: str,
    output_dir: Path | str | None,
) -> Path:
    source_path = _resolve_source_workbook_path(source_workbook_name)
    workbook = load_workbook(source_path)
    if week_sheet_name not in workbook.sheetnames:
        raise ValueError(f"week sheet not found in source workbook: {week_sheet_name}")
    _keep_only_target_sheet(workbook, week_sheet_name)
    worksheet = workbook[week_sheet_name]

    _write_facility_name(worksheet, facility_name)
    _apply_fax_metadata_header(
        worksheet,
        fax_template_id=fax_template_id,
        facility_id=facility_id,
        facility_name=facility_name,
        week_sheet_name=week_sheet_name,
        family_label=family_label,
    )
    _apply_fax_markers(worksheet)
    _apply_bottom_instruction_strip(worksheet, fax_template_id=fax_template_id, base_label=base_label)
    _extend_print_area(worksheet, bottom_row=_BOTTOM_MARKER_ROW)
    _append_hidden_metadata_sheet(
        workbook,
        source_workbook_name=source_workbook_name,
        facility_id=facility_id,
        facility_name=facility_name,
        fax_template_id=fax_template_id,
        family_label=family_label,
        week_sheet_name=week_sheet_name,
        base_label=base_label,
    )

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_template_id = _sanitize_filename_fragment(fax_template_id)
    safe_facility_id = _sanitize_filename_fragment(facility_id)
    safe_week = _sanitize_filename_fragment(week_sheet_name)
    output_name = f"fax_order_form_{base_label}_{safe_facility_id}_{safe_week}_{safe_template_id}_{stamp}.xlsx"
    output_path = _resolve_fax_output_dir(output_dir) / output_name
    workbook.save(output_path)
    return output_path


def _render_fax_structure_only_workbook(
    *,
    source_workbook_name: str,
    week_sheet_name: str,
    facility_name: str,
    facility_id: str,
    fax_template_id: str,
    family_label: str,
    output_dir: Path | str | None,
) -> Path:
    source_path = _resolve_source_workbook_path(source_workbook_name)
    workbook = load_workbook(source_path)
    if week_sheet_name not in workbook.sheetnames:
        raise ValueError(f"week sheet not found in source workbook: {week_sheet_name}")
    _keep_only_target_sheet(workbook, week_sheet_name)
    worksheet = workbook[week_sheet_name]

    _clear_order_form_body_values(worksheet)
    _write_facility_name(worksheet, facility_name)
    _extend_print_area(worksheet, bottom_row=_BOTTOM_MARKER_ROW)
    _append_hidden_metadata_sheet(
        workbook,
        source_workbook_name=source_workbook_name,
        facility_id=facility_id,
        facility_name=facility_name,
        fax_template_id=fax_template_id,
        family_label=family_label,
        week_sheet_name=week_sheet_name,
        base_label="structure_only",
    )

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_template_id = _sanitize_filename_fragment(fax_template_id)
    safe_facility_id = _sanitize_filename_fragment(facility_id)
    safe_week = _sanitize_filename_fragment(week_sheet_name)
    output_name = (
        f"fax_order_form_structure_only_{safe_facility_id}_{safe_week}_{safe_template_id}_{stamp}.xlsx"
    )
    output_path = _resolve_fax_output_dir(output_dir) / output_name
    workbook.save(output_path)
    return output_path


def _resolve_fax_output_dir(output_dir: Path | str | None) -> Path:
    if output_dir is None:
        path = _FAX_OUTPUT_DIR
    else:
        path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _keep_only_target_sheet(workbook: Workbook, week_sheet_name: str) -> None:
    for sheet_name in list(workbook.sheetnames):
        if sheet_name == week_sheet_name:
            continue
        del workbook[sheet_name]
    workbook.active = 0


def _ensure_week_sheet_from_template(workbook: Workbook, week_sheet_name: str) -> None:
    if week_sheet_name in workbook.sheetnames:
        return
    template_sheet_name = _DEFAULT_WEEK_SHEET if _DEFAULT_WEEK_SHEET in workbook.sheetnames else workbook.sheetnames[0]
    template_sheet = workbook[template_sheet_name]
    copied = workbook.copy_worksheet(template_sheet)
    _clone_sheet_images(template_sheet, copied)
    copied.title = week_sheet_name


def _write_facility_name(worksheet, facility_name: str) -> None:
    worksheet["A3"] = facility_name
    worksheet["A3"].font = Font(name="Meiryo", size=11, bold=True)
    worksheet["A3"].alignment = Alignment(horizontal="left", vertical="center")


def _apply_fax_metadata_header(
    worksheet,
    *,
    fax_template_id: str,
    facility_id: str,
    facility_name: str,
    week_sheet_name: str,
    family_label: str,
    generated_at: datetime | None = None,
    label: str = "発注書作成",
    fax_version: dict[str, Any] | None = None,
) -> None:
    _ = fax_template_id
    _ = facility_id
    _ = facility_name
    _ = week_sheet_name
    _ = family_label
    version_text = ""
    if isinstance(fax_version, dict) and fax_version.get("fax_version_no"):
        version_text = f" / FAX v{fax_version.get('fax_version_no')} of {fax_version.get('fax_version_count') or 1}"
    worksheet.row_dimensions[1].height = 18
    _safe_merge(worksheet, "B1:K1")
    header_cell = worksheet["B1"]
    header_cell.value = f"{label}: {_format_generated_at(generated_at)}{version_text}"
    header_cell.font = Font(name="Meiryo", size=8, bold=True)
    header_cell.alignment = Alignment(horizontal="center", vertical="center")
    header_cell.fill = PatternFill(fill_type=None)
    header_cell.border = _THIN_BORDER


def _apply_fax_markers(worksheet, *, bottom_row: int = _BOTTOM_MARKER_ROW) -> None:
    worksheet.row_dimensions[bottom_row].height = 18
    for cell_ref in ("A1", "L1", f"A{bottom_row}", f"L{bottom_row}"):
        cell = worksheet[cell_ref]
        cell.value = None
        cell.fill = _MARKER_FILL
        cell.border = _THIN_BORDER


def _apply_bottom_instruction_strip(worksheet, *, fax_template_id: str, base_label: str, bottom_row: int = _BOTTOM_MARKER_ROW) -> None:
    _ = fax_template_id
    _ = base_label
    _safe_merge(worksheet, f"B{bottom_row}:K{bottom_row}")
    info_cell = worksheet[f"B{bottom_row}"]
    info_cell.value = None
    info_cell.font = Font(name="Meiryo", size=8, bold=True)
    info_cell.alignment = Alignment(horizontal="center", vertical="center")
    info_cell.fill = PatternFill(fill_type=None)
    info_cell.border = _THIN_BORDER


def _extend_print_area(worksheet, *, bottom_row: int, row_delta: int = 0) -> None:
    if not worksheet.print_area:
        worksheet.print_area = f"A1:L{bottom_row}"
        return
    ranges = list(PrintArea.from_string(worksheet.print_area).ranges)
    if len(ranges) != 1:
        raise ValueError("source workbook print area ambiguous")
    min_col, min_row, max_col, max_row = ranges[0].bounds
    max_row = max(max_row + row_delta, bottom_row)
    worksheet.print_area = f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{max_row}"


def _append_hidden_metadata_sheet(
    workbook: Workbook,
    *,
    source_workbook_name: str,
    facility_id: str,
    facility_name: str,
    fax_template_id: str,
    family_label: str,
    week_sheet_name: str,
    base_label: str,
    generated_at: datetime | None = None,
    fax_version: dict[str, Any] | None = None,
) -> None:
    if "設定" in workbook.sheetnames:
        del workbook["設定"]
    meta = workbook.create_sheet("設定")
    meta.sheet_state = "hidden"
    meta.append(["key", "value"])
    generated_at_value = generated_at or datetime.utcnow()
    meta.append(["generated_at_utc", generated_at_value.isoformat()])
    meta.append(["source_workbook", source_workbook_name])
    meta.append(["facility_id", facility_id])
    meta.append(["facility_name", facility_name])
    meta.append(["fax_template_id", fax_template_id])
    meta.append(["family_label", family_label])
    meta.append(["week_sheet_name", week_sheet_name])
    meta.append(["mode", base_label])
    if isinstance(fax_version, dict):
        for key in (
            "fax_version_no",
            "fax_version_count",
            "fax_version_document_id",
            "fax_version_message_id",
            "fax_version_received_at",
        ):
            if fax_version.get(key) not in (None, ""):
                meta.append([key, fax_version.get(key)])


def _safe_merge(worksheet, cell_range: str) -> None:
    if cell_range in {str(item) for item in worksheet.merged_cells.ranges}:
        return
    worksheet.merge_cells(cell_range)


def _sanitize_filename_fragment(value: str) -> str:
    text = str(value or "").strip()
    safe = re.sub(r"[^0-9A-Za-z_-]+", "_", text)
    return safe.strip("_") or "value"
