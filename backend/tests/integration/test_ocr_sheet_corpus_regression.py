import json
import os
import pathlib
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from typing import Any

import pytest
from sqlalchemy import delete

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.models.menu import MonthlyMenu, MonthlyMenuEntry  # noqa: E402
from src.services import order_service  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "ocr_sheet_corpus"
NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _load_json(relative_path: str) -> dict[str, Any]:
    path = FIXTURE_ROOT / relative_path
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


MANIFEST = _load_json("corpus_manifest.json")


def _parse_week_id(week_id: str | None) -> tuple[int, int]:
    normalized = str(week_id or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}", normalized):
        year, month = normalized.split("-")
        return int(year), int(month)
    return 2026, 2


def _parse_mmdd(mmdd: str, fallback_year: int) -> date:
    month_text, day_text = str(mmdd).strip().split("/", 1)
    return date(fallback_year, int(month_text), int(day_text))


def _qty_indexes(fields: list[str]) -> list[int]:
    return [idx for idx, field in enumerate(fields) if str(field).startswith("qty.")]


def _field_shape(fields: list[str]) -> list[str]:
    shape: list[str] = []
    for field in fields:
        token = str(field or "")
        shape.append("qty" if token.startswith("qty.") else token)
    return shape


def _normalize_qty_cell(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = order_service._parse_sheet_quantity_cell(text)  # noqa: SLF001
    if parsed is None:
        return text
    return order_service._format_merged_quantity_cell(float(parsed))  # noqa: SLF001


def _rows_without_qty(rows: list[list[str]], qty_indexes: list[int]) -> list[list[str]]:
    excluded = set(qty_indexes)
    normalized: list[list[str]] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        normalized.append(
            [str(cell or "").strip() for idx, cell in enumerate(row) if idx not in excluded]
        )
    return normalized


def _qty_matrix_by_position(rows: list[list[str]], qty_indexes: list[int]) -> list[list[str]]:
    normalized: list[list[str]] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        normalized.append(
            [
                _normalize_qty_cell(row[idx] if idx < len(row) else "")
                for idx in qty_indexes
            ]
        )
    return normalized


def _manifest_case_meta(suite: str, case_id: str) -> dict[str, Any]:
    suites = MANIFEST.get("suites") or {}
    suite_cases = suites.get(suite) or {}
    meta = suite_cases.get(case_id) or {}
    return dict(meta) if isinstance(meta, dict) else {}


def _allowed_manifest_statuses() -> set[str]:
    configured = str(os.getenv("OCR_CORPUS_INCLUDE_STATUSES") or "").strip()
    if configured:
        return {token.strip() for token in configured.split(",") if token.strip()}
    defaults = MANIFEST.get("default_statuses") or ["trusted"]
    return {str(token).strip() for token in defaults if str(token).strip()}


def _enforce_corpus_benchmark() -> bool:
    value = str(os.getenv("OCR_CORPUS_ENFORCE") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _filter_cases_by_manifest(suite: str, cases: list[dict[str, str]]) -> list[dict[str, str]]:
    allowed_statuses = _allowed_manifest_statuses()
    filtered: list[dict[str, str]] = []
    for case in cases:
        meta = _manifest_case_meta(suite, str(case.get("case_id") or ""))
        status = str(meta.get("status") or "trusted").strip() or "trusted"
        if status not in allowed_statuses:
            continue
        merged = dict(case)
        merged["manifest_status"] = status
        if meta.get("note") is not None:
            merged["manifest_note"] = str(meta.get("note"))
        filtered.append(merged)
    return filtered


def _discover_manual_label_cases() -> list[dict[str, str]]:
    manual_root = FIXTURE_ROOT / "manual_labels"
    if not manual_root.exists():
        return []
    discovered: list[dict[str, str]] = []
    for path in sorted(manual_root.glob("*.expected_sheet.json")):
        order_id = path.name.removesuffix(".expected_sheet.json")
        pdf_candidates = [
            FIXTURE_ROOT / "pdfs" / f"{order_id}.pdf",
            FIXTURE_ROOT / "pdfs" / f"{order_id}.yomitoku.pdf",
        ]
        pdf_path = next((candidate for candidate in pdf_candidates if candidate.exists()), None)
        if pdf_path is None:
            continue
        discovered.append(
            {
                "case_id": order_id,
                "sheet_path": f"manual_labels/{path.name}",
                "pdf_path": str(pdf_path.relative_to(FIXTURE_ROOT)),
            }
        )
    return discovered


def _filled_qty_row_count(rows: list[list[str]], qty_indexes: list[int]) -> int:
    count = 0
    for row in rows:
        if not isinstance(row, list):
            continue
        has_numeric = False
        for idx in qty_indexes:
            if idx >= len(row):
                continue
            value = str(row[idx] or "").strip()
            if value and NUMERIC_RE.fullmatch(value):
                has_numeric = True
                break
        if has_numeric:
            count += 1
    return count


def _seed_monthly_menu_from_sheet(sheet: dict[str, Any]) -> None:
    fields = list(sheet.get("fields") or [])
    rows = list(sheet.get("rows") or [])
    week_year, _week_month = _parse_week_id(sheet.get("week_id"))
    date_idx = next((idx for idx, field in enumerate(fields) if str(field).startswith("date")), 0)
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")

    menu_entries: list[tuple[date, str, str]] = []
    month_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, list):
            continue
        if max(date_idx, daypart_idx, menu_idx) >= len(row):
            continue
        mmdd = str(row[date_idx] or "").strip()
        daypart = str(row[daypart_idx] or "").strip()
        menu_name = str(row[menu_idx] or "").strip()
        if not mmdd or not menu_name:
            continue
        menu_date = _parse_mmdd(mmdd, week_year)
        menu_entries.append((menu_date, daypart, menu_name))
        month_ids.add(f"{menu_date.year:04d}-{menu_date.month:02d}")

    with session_scope() as session:
        # Keep this corpus deterministic by resetting menu masters between cases.
        session.execute(delete(MonthlyMenuEntry))
        session.execute(delete(MonthlyMenu))
        for month_id in sorted(month_ids):
            year_text, month_text = month_id.split("-")
            session.add(
                MonthlyMenu(
                    id=month_id,
                    month_start=date(int(year_text), int(month_text), 1),
                    filename=f"fixture-{month_id}.xlsx",
                )
            )

        slot_counters: dict[tuple[str, date, str], int] = defaultdict(int)
        for seq, (menu_date, daypart, menu_name) in enumerate(menu_entries, start=1):
            month_id = f"{menu_date.year:04d}-{menu_date.month:02d}"
            slot_key = (month_id, menu_date, daypart)
            slot_index = slot_counters[slot_key]
            slot_counters[slot_key] += 1
            session.add(
                MonthlyMenuEntry(
                    id=f"corpus-{month_id}-{seq}",
                    monthly_menu_id=month_id,
                    menu_date=menu_date,
                    daypart=daypart,
                    name=menu_name,
                    slot_index=slot_index,
                )
            )


def _build_order_for_fixture(
    *,
    case_id: str,
    facility_id: str,
    week_id: str | None,
    pdf_relative_path: str,
) -> dict[str, Any]:
    week_year, week_month = _parse_week_id(week_id)
    pdf_path = FIXTURE_ROOT / pdf_relative_path
    assert pdf_path.exists(), f"fixture PDF is missing: {pdf_path}"

    payload = IngestEmailPayload(
        message_id=f"fixture:{case_id}",
        pdf_uri=f"file://{pdf_path}",
        received_at=datetime(week_year, week_month, 10, 9, 0, 0),
        facility_hint=facility_id,
        week_hint=week_id,
    )
    return order_service.create_order_from_ingest(payload, lines=[])


YOMITOKU_CASES = _filter_cases_by_manifest("yomitoku", [
    {
        "case_id": "ORD7499f262",
        "payload_path": "yomitoku/ORD7499f262.ocr_output.json",
        "sheet_path": "yomitoku/ORD7499f262.expected_sheet.json",
        "pdf_path": "pdfs/ORD7499f262.pdf",
    },
    {
        "case_id": "ORDb266d5d9",
        "payload_path": "yomitoku/ORDb266d5d9.ocr_output.json",
        "sheet_path": "yomitoku/ORDb266d5d9.expected_sheet.json",
        "pdf_path": "pdfs/ORDb266d5d9.pdf",
    },
    {
        "case_id": "ORDb67687a7",
        "payload_path": "yomitoku/ORDb67687a7.ocr_output.json",
        "sheet_path": "yomitoku/ORDb67687a7.expected_sheet.json",
        "pdf_path": "pdfs/ORDb67687a7.pdf",
    },
])

TRUSTED_YOMITOKU_CASES = [
    case for case in YOMITOKU_CASES if str(case.get("manifest_status") or "").strip() == "trusted"
]


def _assert_yomitoku_case(case: dict[str, str]) -> None:
    order_service.clear_all()
    payload = _load_json(case["payload_path"])
    expected_sheet = _load_json(case["sheet_path"])
    _seed_monthly_menu_from_sheet(expected_sheet)

    order = _build_order_for_fixture(
        case_id=case["case_id"],
        facility_id=str(expected_sheet.get("facility_id") or payload.get("facility_id") or "FAC00001"),
        week_id=expected_sheet.get("week_id"),
        pdf_relative_path=case["pdf_path"],
    )
    order_service._save_order_ocr_cache(order["id"], payload)  # noqa: SLF001

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    actual_source = str(sheet.get("source") or "")
    expected_source = str(expected_sheet.get("source") or "")
    if actual_source != expected_source:
        assert actual_source.startswith("weekly_menu")
        assert expected_source.startswith("weekly_menu")
    assert _field_shape(sheet["fields"]) == _field_shape(expected_sheet["fields"])
    actual_qty_indexes = _qty_indexes(sheet["fields"])
    expected_qty_indexes = _qty_indexes(expected_sheet["fields"])
    assert len(actual_qty_indexes) == len(expected_qty_indexes)
    assert _rows_without_qty(sheet["rows"], actual_qty_indexes) == _rows_without_qty(
        expected_sheet["rows"],
        expected_qty_indexes,
    )
    assert _qty_matrix_by_position(sheet["rows"], actual_qty_indexes) == _qty_matrix_by_position(
        expected_sheet["rows"],
        expected_qty_indexes,
    )
    actual_warnings = list(sheet.get("warnings") or [])
    expected_warnings = list(expected_sheet.get("warnings") or [])
    if not expected_warnings:
        assert actual_warnings == []
    assert "sheet_quantity_column_unmapped" not in actual_warnings
    assert _filled_qty_row_count(sheet["rows"], actual_qty_indexes) == _filled_qty_row_count(
        expected_sheet["rows"], expected_qty_indexes
    )

@pytest.mark.parametrize("case", YOMITOKU_CASES, ids=[item["case_id"] for item in YOMITOKU_CASES])
def test_ocr_sheet_regression_corpus_yomitoku(case: dict[str, str]) -> None:
    if not _enforce_corpus_benchmark():
        pytest.skip("corpus benchmark assertions are opt-in; set OCR_CORPUS_ENFORCE=1")
    _assert_yomitoku_case(case)


LLM_CASES = _filter_cases_by_manifest("llm_row_index_copy", [
    {
        "case_id": "ORDb266d5d9",
        "base_sheet_path": "llm/ORDb266d5d9.base_sheet.json",
        "llm_rows_path": "llm/ORDb266d5d9.llm_rows.json",
        "pdf_path": "pdfs/ORDb266d5d9.pdf",
    },
    {
        "case_id": "ORDbabf3c73",
        "base_sheet_path": "llm/ORDbabf3c73.base_sheet.json",
        "llm_rows_path": "llm/ORDbabf3c73.llm_rows.json",
        "pdf_path": "pdfs/ORDbabf3c73.pdf",
    },
])

TRUSTED_LLM_CASES = [
    case for case in LLM_CASES if str(case.get("manifest_status") or "").strip() == "trusted"
]

MANUAL_LABEL_CASES = _filter_cases_by_manifest("manual_labels", _discover_manual_label_cases())

TRUSTED_MANUAL_LABEL_CASES = [
    case for case in MANUAL_LABEL_CASES if str(case.get("manifest_status") or "").strip() == "trusted"
]


def test_corpus_manifest_defaults_to_trusted_cases_only() -> None:
    assert [case["case_id"] for case in YOMITOKU_CASES] == ["ORD7499f262"]
    assert [case["case_id"] for case in LLM_CASES] == ["ORDbabf3c73"]


def test_manual_label_case_discovery_is_safe_when_fixture_files_are_missing() -> None:
    assert all(str(case.get("sheet_path") or "").startswith("manual_labels/") for case in MANUAL_LABEL_CASES)
    assert all(str(case.get("pdf_path") or "").startswith("pdfs/") for case in MANUAL_LABEL_CASES)
    assert [case["case_id"] for case in TRUSTED_MANUAL_LABEL_CASES] == ["ORDbabf3c73"]


@pytest.mark.parametrize("case", TRUSTED_YOMITOKU_CASES, ids=[item["case_id"] for item in TRUSTED_YOMITOKU_CASES])
def test_ocr_sheet_regression_corpus_yomitoku_trusted_smoke(case: dict[str, str]) -> None:
    _assert_yomitoku_case(case)


def _assert_llm_case(case: dict[str, str]) -> None:
    order_service.clear_all()
    base_sheet = _load_json(case["base_sheet_path"])
    llm_payload = _load_json(case["llm_rows_path"])
    _seed_monthly_menu_from_sheet(base_sheet)

    fields = list(base_sheet.get("fields") or [])
    qty_indexes = _qty_indexes(fields)
    field_to_index = {field: idx for idx, field in enumerate(fields)}
    table_rows: list[list[str]] = [["" for _ in fields] for _ in range(len(base_sheet.get("rows") or []))]

    raw_rows = llm_payload.get("rows") or []
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            continue
        row_index_text = str(raw_row.get("row_index") or "").strip()
        if not row_index_text.isdigit():
            continue
        row_index = int(row_index_text)
        if row_index < 0 or row_index >= len(table_rows):
            continue
        for field in fields:
            if not field.startswith("qty."):
                continue
            value = str(raw_row.get(field) or "").strip()
            table_rows[row_index][field_to_index[field]] = value

    payload = {
        "engine": "gemini",
        "table_rows": table_rows,
        "rows": raw_rows,
        "date_strings": llm_payload.get("date_strings") or [],
        "_ocr_debug": llm_payload.get("_ocr_debug") or {},
    }

    order = _build_order_for_fixture(
        case_id=f"{case['case_id']}:gemini",
        facility_id=str(base_sheet.get("facility_id") or "FAC00001"),
        week_id=base_sheet.get("week_id"),
        pdf_relative_path=case["pdf_path"],
    )
    order_service._save_order_ocr_cache(order["id"], payload)  # noqa: SLF001

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert _field_shape(sheet["fields"]) == _field_shape(fields)
    assert len(sheet["rows"]) == len(table_rows)
    assert sheet.get("warnings") == []

    expected_rows: list[list[str]] = []
    base_rows = [list(row) for row in (base_sheet.get("rows") or []) if isinstance(row, list)]
    for row_index, source_row in enumerate(table_rows):
        baseline_row = list(base_rows[row_index]) if row_index < len(base_rows) else [""] * len(fields)
        normalized_row = list(baseline_row) if baseline_row else [""] * len(fields)
        if len(normalized_row) < len(fields):
            normalized_row.extend([""] * (len(fields) - len(normalized_row)))
        for qty_idx in qty_indexes:
            raw_value = source_row[qty_idx]
            parsed = order_service._parse_sheet_quantity_cell(raw_value)  # noqa: SLF001
            normalized_row[qty_idx] = (
                order_service._format_merged_quantity_cell(float(parsed))  # noqa: SLF001
                if parsed is not None
                else ""
            )
        expected_rows.append(normalized_row)

    actual_qty_indexes = _qty_indexes(sheet["fields"])
    assert len(actual_qty_indexes) == len(qty_indexes)
    assert _rows_without_qty(sheet["rows"], actual_qty_indexes) == _rows_without_qty(expected_rows, qty_indexes)
    assert _qty_matrix_by_position(sheet["rows"], actual_qty_indexes) == _qty_matrix_by_position(
        expected_rows,
        qty_indexes,
    )
    assert _filled_qty_row_count(sheet["rows"], actual_qty_indexes) == _filled_qty_row_count(
        expected_rows, qty_indexes
    )


def _assert_manual_label_case(case: dict[str, str]) -> None:
    order_service.clear_all()
    expected_sheet = _load_json(case["sheet_path"])
    _seed_monthly_menu_from_sheet(expected_sheet)

    order = _build_order_for_fixture(
        case_id=case["case_id"],
        facility_id=str(expected_sheet.get("facility_id") or "FAC00001"),
        week_id=expected_sheet.get("week_id"),
        pdf_relative_path=case["pdf_path"],
    )

    fields = list(expected_sheet.get("fields") or [])
    rows = [list(row) for row in (expected_sheet.get("rows") or []) if isinstance(row, list)]
    row_ids = [
        str(item).strip() or f"manual-row-{idx + 1}"
        for idx, item in enumerate(expected_sheet.get("row_ids") or [])
    ]
    if len(row_ids) < len(rows):
        row_ids.extend([f"manual-row-{idx + 1}" for idx in range(len(row_ids), len(rows))])
    saved, save_error = order_service.save_ocr_sheet_exact(
        order["id"],
        rows=rows,
        fields=fields,
        header=list(expected_sheet.get("header") or []),
        row_ids=row_ids[: len(rows)],
        ui_mode="sheet",
    )
    assert save_error is None
    assert saved is not None

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None

    exported, export_error = order_service.export_ocr_sheet_label(order["id"])
    assert export_error is None
    assert isinstance(exported, dict)
    exported_sheet = exported.get("sheet")
    assert isinstance(exported_sheet, dict)

    actual_qty_indexes = _qty_indexes(exported_sheet["fields"])
    expected_qty_indexes = _qty_indexes(expected_sheet["fields"])
    assert _field_shape(exported_sheet["fields"]) == _field_shape(expected_sheet["fields"])
    assert _rows_without_qty(exported_sheet["rows"], actual_qty_indexes) == _rows_without_qty(
        expected_sheet["rows"],
        expected_qty_indexes,
    )
    assert _qty_matrix_by_position(exported_sheet["rows"], actual_qty_indexes) == _qty_matrix_by_position(
        expected_sheet["rows"],
        expected_qty_indexes,
    )


@pytest.mark.parametrize("case", TRUSTED_LLM_CASES, ids=[item["case_id"] for item in TRUSTED_LLM_CASES])
def test_ocr_sheet_regression_corpus_llm_row_index_copy_trusted_smoke(case: dict[str, str]) -> None:
    _assert_llm_case(case)


@pytest.mark.parametrize("case", TRUSTED_MANUAL_LABEL_CASES, ids=[item["case_id"] for item in TRUSTED_MANUAL_LABEL_CASES])
def test_ocr_sheet_regression_corpus_manual_labels_trusted_smoke(case: dict[str, str]) -> None:
    _assert_manual_label_case(case)


@pytest.mark.parametrize("case", LLM_CASES, ids=[item["case_id"] for item in LLM_CASES])
def test_ocr_sheet_regression_corpus_llm_row_index_copy(case: dict[str, str]) -> None:
    if not _enforce_corpus_benchmark():
        pytest.skip("corpus benchmark assertions are opt-in; set OCR_CORPUS_ENFORCE=1")
    _assert_llm_case(case)


@pytest.mark.parametrize("case", MANUAL_LABEL_CASES, ids=[item["case_id"] for item in MANUAL_LABEL_CASES])
def test_ocr_sheet_regression_corpus_manual_labels(case: dict[str, str]) -> None:
    if not _enforce_corpus_benchmark():
        pytest.skip("corpus benchmark assertions are opt-in; set OCR_CORPUS_ENFORCE=1")
    _assert_manual_label_case(case)
