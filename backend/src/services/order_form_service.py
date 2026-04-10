from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
import os
import re

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter, range_boundaries

from src.services import config_service, menu_service


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
_FAX_FAMILY_SOURCE_MAP = {
    "fax_layout_regular_forbidden_v1": {
        "source_workbook": "共通　2603.xlsx",
        "family_label": "共通・禁食2種",
    },
    "fax_layout_floor_2f3f_v1": {
        "source_workbook": "春日苑松茂　2603.xlsx",
        "family_label": "2F/3F分割",
    },
    "fax_layout_regular_soft_mixer_forbidden_v1": {
        "source_workbook": "藍テラス　2603.xlsx",
        "family_label": "軟菜・ミキサー・禁食",
    },
    "fax_layout_regular_staff_daycare_v1": {
        "source_workbook": "湘南さくら病院 2603.xlsx",
        "family_label": "職員・禁食拡張",
    },
    "fax_layout_regular_diabetes_v1": {
        "source_workbook": "いこいの森プラス　2603.xlsx",
        "family_label": "糖尿併記",
    },
    "fax_layout_regular_staff_daycare_other_forbidden_v1": {
        "source_workbook": "ふれあいの丘 2603.xlsx",
        "family_label": "老健・職員通所・禁食",
    },
    "fax_layout_soft_packaging_forbidden_v1": {
        "source_workbook": "池袋病院　2603.xlsx",
        "family_label": "軟菜・袋分け・禁食",
    },
}
_MARKER_FILL = PatternFill(start_color="000000", end_color="000000", fill_type="solid")
_META_FILL = PatternFill(start_color="E9EEF5", end_color="E9EEF5", fill_type="solid")
_THIN_BORDER = Border(
    left=Side(style="thin", color="000000"),
    right=Side(style="thin", color="000000"),
    top=Side(style="thin", color="000000"),
    bottom=Side(style="thin", color="000000"),
)


def _resolve_fax_source_template_dir() -> Path:
    configured = os.getenv("FAX_SOURCE_TEMPLATE_DIR", "").strip()
    if configured:
        return Path(configured)

    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "input_example" / "発注書"
        if candidate.exists():
            return candidate

    return Path("/app/input_example/発注書")


_FAX_SOURCE_TEMPLATE_DIR = _resolve_fax_source_template_dir()


def list_order_form_patterns() -> list[dict]:
    return config_service.get_order_form_patterns()


def _normalize_month_id(month_id: str) -> str:
    value = str(month_id or "").strip()
    if not _MONTH_ID_RE.match(value):
        raise ValueError("month_id must be YYYY-MM")
    return value


def _resolve_pattern(facility: dict, pattern_id: str | None) -> dict:
    if pattern_id:
        pattern = config_service.get_order_form_pattern(pattern_id)
        if pattern:
            return pattern
    facility_pattern = facility.get("order_form_pattern_id")
    if isinstance(facility_pattern, str) and facility_pattern.strip():
        pattern = config_service.get_order_form_pattern(facility_pattern.strip())
        if pattern:
            return pattern
    patterns = config_service.get_order_form_patterns()
    if patterns:
        return dict(patterns[0])
    return {"pattern_id": "PATTERN_A", "label": "標準A", "marker_cells": []}


def _resolve_facility(facility_id: str) -> dict:
    facility = config_service.get_facility_config(facility_id)
    if not facility:
        facility = config_service.get_facility_by_id(facility_id)
    if not facility:
        raise ValueError("facility not found")
    return facility


def _infer_fax_template_id_from_facility(facility: dict) -> str | None:
    explicit = str(facility.get("fax_template_id") or "").strip()
    if explicit:
        return explicit

    facility_text = " ".join(
        [
            str(facility.get("facility_name") or ""),
            *[str(item or "") for item in (facility.get("aliases") or [])],
        ]
    )
    if "ふれあい" in facility_text:
        return "fax_layout_regular_staff_daycare_other_forbidden_v1"
    if "池袋病院" in facility_text:
        return "fax_layout_soft_packaging_forbidden_v1"

    columns = (
        ((facility.get("fax_template_override") or {}).get("columns") or [])
        or ((facility.get("invoice_template") or {}).get("columns") or [])
    )
    headers: list[str] = []
    diet_types: set[str] = set()
    area_ids: set[str] = set()
    for column in columns:
        if not isinstance(column, dict):
            continue
        header = str(column.get("header") or column.get("name") or "").strip()
        if header:
            headers.append(header)
        diet_type = str(column.get("diet_type") or "").strip()
        if diet_type:
            diet_types.add(diet_type)
        area_id = str(column.get("area_id") or "").strip()
        if area_id:
            area_ids.add(area_id.lower())

    header_blob = " ".join(headers)
    if "糖尿" in header_blob or "diabetes" in diet_types or "糖尿" in diet_types:
        return "fax_layout_regular_diabetes_v1"
    if "池袋病院" in facility_text or (
        "軟菜" in header_blob
        and ("袋分け" in header_blob or "袋分" in header_blob)
        and "常食" not in header_blob
    ):
        return "fax_layout_soft_packaging_forbidden_v1"
    if "ふれあい" in facility_text or "通所" in header_blob or "その他" in header_blob:
        return "fax_layout_regular_staff_daycare_other_forbidden_v1"
    if {"2f", "3f"} & area_ids or "2F" in header_blob or "3F" in header_blob:
        return "fax_layout_floor_2f3f_v1"
    if (
        ("regular_bag" in diet_types or "袋分け" in header_blob or "袋分" in header_blob)
        and {"soft", "mixer"} & diet_types
    ):
        return "fax_layout_regular_soft_mixer_forbidden_v1"
    if {"staff", "daycare"} & diet_types or "職員" in header_blob or "通所" in header_blob:
        return "fax_layout_regular_staff_daycare_v1"
    if {"no_meat", "no_fish", "change_1", "change_2"} & diet_types or "禁食" in header_blob:
        return "fax_layout_regular_forbidden_v1"
    return "fax_layout_regular_forbidden_v1"


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
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        menu_date = _parse_menu_date(entry.get("menu_date"))
        daypart = entry.get("daypart")
        name = entry.get("name")
        if not menu_date or not daypart or not name:
            continue
        normalized.append(
            {
                **entry,
                "_menu_date_obj": menu_date,
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
    for merged_range in list(worksheet.merged_cells.ranges):
        min_col, min_row, max_col, max_row = range_boundaries(str(merged_range))
        if max_row < _ORDER_FORM_BODY_START_ROW or min_row > _ORDER_FORM_BODY_END_ROW:
            continue
        worksheet.unmerge_cells(str(merged_range))
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


def _write_week_entries(worksheet, week_entries: list[dict]) -> int:
    row_idx = _ORDER_FORM_BODY_START_ROW
    current_date: date | None = None
    date_start_row = _ORDER_FORM_BODY_START_ROW
    current_daypart = ""
    written_rows = 0

    for entry in week_entries:
        menu_date = entry.get("_menu_date_obj")
        if not isinstance(menu_date, date):
            continue
        daypart = str(entry.get("daypart") or "").strip()
        category = str(entry.get("category") or "")
        menu_name = str(entry.get("name") or "")
        if row_idx > _ORDER_FORM_BODY_END_ROW:
            raise ValueError("weekly menu exceeds supported template rows")

        if current_date != menu_date:
            if current_date is not None and row_idx - 1 > date_start_row:
                worksheet.cell(row=row_idx - 1, column=1, value=_weekday_label(current_date))
            current_date = menu_date
            date_start_row = row_idx
            current_daypart = ""
            worksheet.cell(row=row_idx, column=1, value=menu_date)

        worksheet.cell(row=row_idx, column=2, value=daypart if current_daypart != daypart else None)
        worksheet.cell(row=row_idx, column=3, value=category)
        worksheet.cell(row=row_idx, column=4, value=menu_name)

        current_daypart = daypart
        row_idx += 1
        written_rows += 1

    if current_date is not None and row_idx - 1 > date_start_row:
        worksheet.cell(row=row_idx - 1, column=1, value=_weekday_label(current_date))
    return written_rows


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
    for index, (start_date, end_date) in enumerate(week_ranges, start=1):
        meta.append([f"week_{index}_sheet_name", _format_week_sheet_name(start_date, end_date)])
        meta.append([f"week_{index}_start", start_date.isoformat()])
        meta.append([f"week_{index}_end", end_date.isoformat()])


def _build_monthly_fax_order_form_workbook(
    *,
    facility: dict,
    month_id: str,
    entries: list[dict],
    pattern: dict,
) -> Workbook:
    fax_template_id = str(_infer_fax_template_id_from_facility(facility) or "").strip()
    if not fax_template_id:
        raise ValueError("facility fax_template_id not found")
    spec = _resolve_fax_family_spec(fax_template_id)
    source_workbook_name = str(spec["source_workbook"])
    source_path = _FAX_SOURCE_TEMPLATE_DIR / source_workbook_name
    if not source_path.exists():
        raise ValueError(f"source workbook not found: {source_workbook_name}")

    workbook = load_workbook(source_path)
    template_sheet_name = _DEFAULT_WEEK_SHEET if _DEFAULT_WEEK_SHEET in workbook.sheetnames else workbook.sheetnames[0]
    week_ranges = _build_week_ranges_for_month(month_id)
    _ensure_workbook_sheet_count(workbook, template_sheet_name, len(week_ranges))

    facility_id = str(facility.get("facility_id") or facility.get("id") or "")
    facility_name = str(facility.get("facility_name") or facility.get("name") or facility_id)
    family_label = str(spec["family_label"])

    for index, (start_date, end_date) in enumerate(week_ranges):
        worksheet = workbook.worksheets[index]
        sheet_name = _format_week_sheet_name(start_date, end_date)
        worksheet.title = sheet_name
        _clear_week_sheet_body(worksheet)
        _write_facility_name_in_box(worksheet, facility_name)
        _set_deadline_text_for_week(worksheet, start_date)
        _write_week_entries(worksheet, _select_entries_for_range(entries, start_date, end_date))
        _apply_fax_metadata_header(
            worksheet,
            fax_template_id=fax_template_id,
            facility_id=facility_id,
            facility_name=facility_name,
            week_sheet_name=sheet_name,
            family_label=family_label,
        )
        _apply_fax_markers(worksheet)
        _apply_bottom_instruction_strip(worksheet, fax_template_id=fax_template_id, base_label="monthly")
        _extend_print_area(worksheet, bottom_row=_BOTTOM_MARKER_ROW)

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
    facility = _resolve_facility(facility_id)
    entries = _collect_menu_entries(normalized_month, facility_id)
    pattern = _resolve_pattern(facility, pattern_id)
    resolved_fax_template_id = _infer_fax_template_id_from_facility(facility)
    wb = _build_monthly_fax_order_form_workbook(
        facility=facility,
        month_id=normalized_month,
        entries=entries,
        pattern=pattern,
    )

    file_pattern = str(resolved_fax_template_id or pattern.get("pattern_id") or "PATTERN_A")
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output = _OUTPUT_DIR / f"order_form_{facility_id}_{normalized_month}_{file_pattern}_{stamp}.xlsx"
    wb.save(output)
    return output


def list_fax_order_form_template_specs() -> list[dict]:
    specs: list[dict] = []
    for template_id, payload in _FAX_FAMILY_SOURCE_MAP.items():
        specs.append(
            {
                "fax_template_id": template_id,
                "source_workbook": payload["source_workbook"],
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
    spec = _resolve_fax_family_spec(fax_template_id)
    return _render_fax_order_form_workbook(
        source_workbook_name=spec["source_workbook"],
        week_sheet_name=week_sheet_name,
        facility_name="施設名記入欄",
        facility_id="BASE",
        fax_template_id=fax_template_id,
        family_label=str(spec["family_label"]),
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
        source_workbook_name=spec["source_workbook"],
        week_sheet_name=week_sheet_name,
        facility_name=facility_name,
        facility_id=facility_id,
        fax_template_id=fax_template_id,
        family_label=str(spec["family_label"]),
        base_label="facility",
        output_dir=output_dir,
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
    source_path = _FAX_SOURCE_TEMPLATE_DIR / source_workbook_name
    if not source_path.exists():
        raise ValueError(f"source workbook not found: {source_workbook_name}")
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
) -> None:
    worksheet.row_dimensions[1].height = 18
    _safe_merge(worksheet, "B1:K1")
    header_cell = worksheet["B1"]
    header_cell.value = (
        f"TEMPLATE={fax_template_id} | FAMILY={family_label} | "
        f"FACILITY={facility_id}:{facility_name} | WEEK={week_sheet_name} | PAGE=1/1"
    )
    header_cell.font = Font(name="Meiryo", size=8, bold=True)
    header_cell.alignment = Alignment(horizontal="center", vertical="center")
    header_cell.fill = _META_FILL
    header_cell.border = _THIN_BORDER


def _apply_fax_markers(worksheet) -> None:
    worksheet.row_dimensions[_BOTTOM_MARKER_ROW].height = 18
    for cell_ref in ("A1", "L1", f"A{_BOTTOM_MARKER_ROW}", f"L{_BOTTOM_MARKER_ROW}"):
        cell = worksheet[cell_ref]
        cell.value = None
        cell.fill = _MARKER_FILL
        cell.border = _THIN_BORDER


def _apply_bottom_instruction_strip(worksheet, *, fax_template_id: str, base_label: str) -> None:
    _safe_merge(worksheet, f"B{_BOTTOM_MARKER_ROW}:K{_BOTTOM_MARKER_ROW}")
    info_cell = worksheet[f"B{_BOTTOM_MARKER_ROW}"]
    info_cell.value = (
        "OCR補助: 枠内に濃く記入 / 訂正は右側へ追記 / "
        f"template={fax_template_id} / mode={base_label}"
    )
    info_cell.font = Font(name="Meiryo", size=8, bold=True)
    info_cell.alignment = Alignment(horizontal="center", vertical="center")
    info_cell.fill = _META_FILL
    info_cell.border = _THIN_BORDER


def _extend_print_area(worksheet, *, bottom_row: int) -> None:
    print_area = worksheet.print_area
    if not print_area:
        worksheet.print_area = f"A1:L{bottom_row}"
        return
    area_ref = print_area.split("!", 1)[-1].replace("$", "")
    min_col, min_row, max_col, max_row = range_boundaries(area_ref)
    max_row = max(max_row, bottom_row)
    worksheet.print_area = (
        f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{max_row}"
    )


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
    meta.append(["fax_template_id", fax_template_id])
    meta.append(["family_label", family_label])
    meta.append(["week_sheet_name", week_sheet_name])
    meta.append(["mode", base_label])


def _safe_merge(worksheet, cell_range: str) -> None:
    if cell_range in {str(item) for item in worksheet.merged_cells.ranges}:
        return
    worksheet.merge_cells(cell_range)


def _sanitize_filename_fragment(value: str) -> str:
    text = str(value or "").strip()
    safe = re.sub(r"[^0-9A-Za-z_-]+", "_", text)
    return safe.strip("_") or "value"
