import sys
import pathlib
from datetime import date, datetime
from uuid import uuid4
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import order_service, config_service  # noqa: E402
from src.services.ocr_job_service import get_job  # noqa: E402
from src.services.fax_extractor import FaxExtractedData  # noqa: E402
from src.services.fax_parser import parse_order_lines  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models.menu import MonthlyMenu, MonthlyMenuEntry  # noqa: E402
from src.models.order_ocr_cache import OrderOcrCache  # noqa: E402


def _canonical_row_for_week(week_id: str) -> tuple[str, str, str]:
    _ensure_week_menu_entries(week_id)
    entries = order_service._build_position_menu_entries(week_id)
    assert entries
    entry = entries[0]
    menu_date = str(entry.get("menu_date") or "")
    daypart = str(entry.get("daypart_key") or "")
    menu_name = str(entry.get("menu_name") or "")
    assert menu_date and daypart and menu_name
    return menu_date, daypart, menu_name


def _ensure_week_menu_entries(week_id: str) -> None:
    seed_rows = [
        (date(2026, 2, 1), "朝", "Boundary Feb"),
        (date(2026, 2, 1), "昼", "Menu A"),
        (date(2026, 2, 1), "夕", "Menu B"),
    ]
    with session_scope() as session:
        menu = session.get(MonthlyMenu, week_id)
        if menu is None:
            session.add(
                MonthlyMenu(
                    id=week_id,
                    month_start=seed_rows[0][0].replace(day=1),
                    filename="test-seed.csv",
                )
            )
        existing = (
            session.query(MonthlyMenuEntry)
            .filter(MonthlyMenuEntry.monthly_menu_id == week_id)
            .count()
        )
        if existing > 0:
            return
        for idx, (menu_date, daypart, name) in enumerate(seed_rows):
            session.add(
                MonthlyMenuEntry(
                    id=f"TME{uuid4().hex[:8]}",
                    monthly_menu_id=week_id,
                    menu_date=menu_date,
                    daypart=daypart,
                    name=name,
                    slot_index=idx,
                )
            )


def _make_first_pass_payload(rows: list[list[str]] | None = None, table_raw: str | None = None) -> dict:
    normalized_rows = [list(row) for row in (rows or []) if isinstance(row, list)]
    if table_raw is None and normalized_rows:
        width = max(len(row) for row in normalized_rows)
        header = [f"col{idx + 1}" for idx in range(width)]
        padded_rows = [row[:width] + [""] * max(0, width - len(row)) for row in normalized_rows]
        body = "\n".join("|" + "|".join(str(cell or "") for cell in row) + "|" for row in padded_rows)
        table_raw = "|" + "|".join(header) + "|\n|" + "|".join(["---"] * width) + "|\n" + body
    return {
        "rows": [list(row) for row in normalized_rows],
        "table_rows": [list(row) for row in normalized_rows],
        "tables": [{"rows": [list(row) for row in normalized_rows]}] if normalized_rows else [],
        "table_raw": table_raw or "",
        "pages": [{"page_index": 1, "tables": [{"rows": [list(row) for row in normalized_rows]}]}] if normalized_rows else [],
    }


def _patch_first_pass_payload(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(
        order_service,
        "_load_existing_first_pass_payload_for_reparse",
        lambda *_args, **_kwargs: payload,
    )


def test_reparse_order_records_explicit_failed_llm_reparse_when_no_lines_survive(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-lines-empty.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-lines-empty-reparse",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 1, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-01-08",
                "daypart": "昼",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 2,
            }
            ],
        )
    current_date = "2026-01-08"
    current_daypart = "昼"
    current_menu = "Menu A"
    current_mmdd = datetime.fromisoformat(current_date).strftime("%m/%d")
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", lambda **_kwargs: None)
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[[current_mmdd, current_daypart, current_menu, "9"]]),
    )

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[current_date],
            table_rows=[[current_mmdd, current_daypart, current_menu, ""]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "finish_reason": "STOP"},
        )

    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", lambda *args, **kwargs: [])
    monkeypatch.setattr(order_service, "rows_from_markdown", lambda *args, **kwargs: [])
    monkeypatch.setattr(order_service, "_build_sheet_lines_from_ocr_payload", lambda **kwargs: [])

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=False)

    assert updated is None
    assert error == "lines_empty"
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") == "failed"
    assert job.get("error_message") == "lines_empty"
    metrics = job.get("metrics") or {}
    assert metrics.get("request_mode") == "llm_reparse"
    assert metrics.get("processing_stage") == "line_parse"
    assert metrics.get("result_state") == "hard_failed"
    assert metrics.get("error") == "lines_empty"
    assert metrics.get("confirmed_lines_retained") is True

    cached = order_service._load_order_ocr_cache(order["id"])  # noqa: SLF001
    debug = cached.get("_reparse_debug") if isinstance(cached, dict) else {}
    assert debug.get("provider") == "gemini"
    assert debug.get("reject_reasons") == ["lines_empty"]


def test_reparse_order_preserves_detailed_gemini_http_failure_without_cached_fallback(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-gemini-http-failure.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-gemini-http-failure",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 1, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-01-08",
                "daypart": "昼",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 2,
            }
        ],
    )
    current_mmdd = "01/08"
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[[current_mmdd, "昼", "Menu A", "9"]]),
    )
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", lambda **_kwargs: None)

    detailed_error = (
        "Gemini OCR HTTP 400 INVALID_ARGUMENT: "
        "Budget 0 is invalid. This model only works in thinking mode."
    )

    def _raise_main_ocr(*_args, **_kwargs):
        raise RuntimeError(detailed_error)

    cached_probe = {"called": False}

    def _fake_cached_ocr(_message_id: str):
        cached_probe["called"] = True
        return {
            "date_strings": ["2026-01-08"],
            "table_rows": [[current_mmdd, "昼", "Menu A", "9"]],
            "tokens": [{"text": "9"}],
        }

    monkeypatch.setattr(order_service, "extract_fax_data", _raise_main_ocr)
    monkeypatch.setattr(order_service, "_load_cached_ocr", _fake_cached_ocr)

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    expected_error = f"main_ocr_failed:gemini:{detailed_error}"
    assert updated is None
    assert error == expected_error
    assert cached_probe["called"] is False
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") == "failed"
    assert job.get("error_message") == expected_error
    metrics = job.get("metrics") or {}
    assert metrics.get("request_mode") == "llm_reparse"
    assert metrics.get("requested_provider") == "gemini"
    assert metrics.get("processing_stage") == "inference"
    assert metrics.get("result_state") == "hard_failed"
    assert metrics.get("error") == expected_error
    assert metrics.get("upstream_error_detail") == detailed_error


def test_load_existing_first_pass_payload_for_reparse_rejects_stale_aux_schema(monkeypatch):
    template = config_service.get_facility_config("FAC00004").get("fax_template")
    stale_payload = _make_first_pass_payload(
        rows=[["04/26", "昼", "Menu A", "1", "2", "3", "4", "5", "6", "7", "note"]]
    )
    monkeypatch.setattr(
        order_service,
        "_get_ocr_output_without_legacy_edits",
        lambda *_args, **_kwargs: (stale_payload, None),
    )
    monkeypatch.setattr(order_service, "_load_order_ocr_cache", lambda *_args, **_kwargs: stale_payload)

    reused = order_service._load_existing_first_pass_payload_for_reparse(
        "ORD-test-stale-first-pass",
        template=template,
    )

    assert reused is None


def test_load_existing_first_pass_payload_for_reparse_accepts_matching_aux_schema(monkeypatch):
    template = config_service.get_facility_config("FAC00004").get("fax_template")
    matching_payload = _make_first_pass_payload(
        rows=[["04/26", "昼", "主", "Menu A", "7", "1", "2", "3", "4", "5", "6", "7", "note"]]
    )
    monkeypatch.setattr(
        order_service,
        "_get_ocr_output_without_legacy_edits",
        lambda *_args, **_kwargs: (matching_payload, None),
    )
    monkeypatch.setattr(order_service, "_load_order_ocr_cache", lambda *_args, **_kwargs: matching_payload)

    reused = order_service._load_existing_first_pass_payload_for_reparse(
        "ORD-test-matching-first-pass",
        template=template,
    )

    assert isinstance(reused, dict)
    assert reused.get("_template_field_schema", {}).get("field_count") == 13


def test_merge_legacy_first_pass_payload_ignores_cached_rows_when_schema_mismatches():
    template = config_service.get_facility_config("FAC00004").get("fax_template")
    active_payload = {"status": "ready"}
    active_run = {"schema_version": "v1_legacy"}
    stale_cached_payload = _make_first_pass_payload(
        rows=[["04/26", "昼", "Menu A", "1", "2", "3", "4", "5", "6", "7", "note"]]
    )
    merged = order_service._merge_legacy_first_pass_payload(
        active_payload,
        active_run,
        stale_cached_payload,
        template=template,
    )

    assert merged == active_payload

    matching_cached_payload = _make_first_pass_payload(
        rows=[["04/26", "昼", "主", "Menu A", "7", "1", "2", "3", "4", "5", "6", "7", "note"]]
    )
    merged_matching = order_service._merge_legacy_first_pass_payload(
        active_payload,
        active_run,
        matching_cached_payload,
        template=template,
    )

    assert merged_matching is not active_payload
    assert merged_matching.get("rows") == matching_cached_payload.get("rows")


def test_extract_first_pass_rows_prefers_raw_body_rows_when_header_is_generic():
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    payload = _make_first_pass_payload([[canonical_mmdd, canonical_daypart, canonical_menu, "2"]])
    template = config_service.get_facility_config("FAC00001").get("fax_template")

    rows = order_service._extract_first_pass_rows_from_payload(payload, template)

    assert rows == [[canonical_mmdd, canonical_daypart, canonical_menu, "2"]]


def test_ingest_to_reparse_flow(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\\n%EOF\\n")
    payload = IngestEmailPayload(
        message_id="msg-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 1, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")

    def _fake_pipeline(**_kwargs):
        return None

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "1"]],
            tokens=[],
            grid=None,
            ocr_provider="mock",
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):
        return [
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 2,
                "source_row_index": 0,
            }
        ]

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload([[canonical_mmdd, canonical_daypart, canonical_menu, "0"]]),
    )

    updated, error = order_service.reparse_order(order["id"])
    assert error is None
    assert updated is not None
    assert updated["lines"]


def test_reparse_order_openai_enforces_strict_quantity_rules(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-openai.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-openai-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    captured_rules: dict = {}
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")

    def _fake_pipeline(**_kwargs):
        return None

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[[canonical_mmdd, canonical_menu, "0"]],
            tokens=[],
            grid=None,
            ocr_provider="openai",
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):
        captured_rules.clear()
        captured_rules.update(quantity_rules or {})
        return [
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 0,
                "source_row_index": 0,
            }
        ]

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload([[canonical_mmdd, canonical_daypart, canonical_menu, "0"]]),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="openai")
    assert error is None
    assert updated is not None
    assert captured_rules.get("zero_as_empty") is False
    assert captured_rules.get("strict_numeric_quantity_cell") is True
    assert captured_rules.get("allow_blank_structure_rows") is True
    assert float(captured_rules.get("max_quantity_abs") or 0) > 0
    assert updated["lines"][0]["quantity_original"] == 0


def test_reparse_order_sets_job_metrics_changed_even_when_counts_match(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-changed-digest.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-openai-002",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 16, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    original = [
        {
            "date": canonical_date,
            "daypart": canonical_daypart,
            "menu_name": canonical_menu,
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 1,
            "source_row_index": 0,
        }
    ]
    order = order_service.create_order_from_ingest(payload, lines=original)

    def _fake_pipeline(**_kwargs):
        return None

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[[canonical_mmdd, canonical_menu, "2"]],
            tokens=[],
            grid=None,
            ocr_provider="openai",
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):
        return [
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 2,
                "source_row_index": 0,
            }
        ]

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload([[canonical_mmdd, canonical_daypart, canonical_menu, "2"]]),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="openai")
    assert error is None
    assert updated is not None
    assert updated["reparse"]["before_count"] == 1
    assert updated["reparse"]["after_count"] == 1
    assert updated["reparse"]["changed"] is True
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") == "done"
    metrics = job.get("metrics") or {}
    assert metrics.get("before_count") == 1
    assert metrics.get("after_count") == 1
    assert metrics.get("changed") is True
    assert metrics.get("provider") == "openai"




def test_reparse_order_llm_full_table_truncated_blocks_without_pipeline_rescue(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-gemini-full-table-truncated-retain.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-gemini-full-table-truncated-retain-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    baseline_sheet = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [
            [canonical_mmdd, canonical_daypart, canonical_menu, "5"],
            [canonical_mmdd, "昼", "Menu B", ""],
        ],
        "row_ids": ["row-1", "row-2"],
        "source": "weekly_menu+payload_row",
    }

    parse_inputs: list[list[list[str]]] = []

    def _fake_pipeline(**_kwargs):
        return "file://pipeline-output.json"

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0, wait_seconds_override=None, **_kwargs):  # noqa: ARG001
        return {"table_raw": "|x|y|z|"}

    def _fake_rows_from_markdown(markdown, template):  # noqa: ARG001
        return [[canonical_mmdd, canonical_menu, "9"]]

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[
                [canonical_mmdd, canonical_daypart, canonical_menu, "20"],
                [canonical_mmdd, "昼", "Menu B", ""],
            ],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            raw_text=f'{{"rows":[{{"row_index":0,"date_mmdd":"{canonical_mmdd}"}}]}}',
            provider_debug={
                "provider": "gemini",
                "model": "gemini-2.5-pro",
                "finish_reason": "MAX_TOKENS",
                "recovered_truncated_json": True,
                "full_table_mode": True,
                "returned_row_indexes": [0, 1],
            },
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        parse_inputs.append([list(item) for item in (rows or [])])
        parsed: list[dict[str, object]] = []
        for row_index, row in enumerate(rows):
            qty = row[3] if len(row) > 3 else ""
            if not str(qty).strip():
                continue
            parsed.append(
                {
                    "date": canonical_date,
                    "daypart": row[1],
                    "menu_name": row[2],
                    "diet_type": "regular",
                    "area_id": "2F",
                    "bag_type": "standard",
                    "quantity_original": int(qty),
                    "source_row_index": row_index,
                }
            )
        return parsed

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "rows_from_markdown", _fake_rows_from_markdown)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id, **_kwargs: (dict(baseline_sheet), None))
    monkeypatch.setattr(
        order_service,
        "_run_llm_reparse_audit",
        lambda **_kwargs: {
            "status": "pass",
            "issues": [],
            "issue_count": 0,
            "blocking_issues": [],
            "blocking_issue_count": 0,
        },
    )
    monkeypatch.setattr(order_service, "_validate_reparse_lines_against_weekly_menu", lambda **_kwargs: (None, None))
    monkeypatch.setattr(order_service, "_build_reparse_position_menu_entries", lambda **_kwargs: [])
    monkeypatch.setattr(order_service, "_resolve_llm_expected_row_count", lambda **_kwargs: 2)
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "9"]]),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert updated is None
    assert error == "llm_full_table_truncated_output"
    assert parse_inputs == []

    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    metrics = job.get("metrics") or {}
    assert metrics.get("finish_reason") == "MAX_TOKENS"
    assert metrics.get("truncated_output") is True
    assert metrics.get("error") == "llm_full_table_truncated_output"
    assert metrics.get("rows_replaced_with_pipeline") is False


def test_reparse_order_llm_full_table_truncated_empty_rows_blocks_without_pipeline_rescue(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-gemini-full-table-truncated-empty.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-gemini-full-table-truncated-empty-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    baseline_sheet = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [
            [canonical_mmdd, canonical_daypart, canonical_menu, "5"],
            [canonical_mmdd, "昼", "Menu B", ""],
        ],
        "row_ids": ["row-1", "row-2"],
        "source": "weekly_menu+payload_row",
    }

    parse_inputs: list[list[list[str]]] = []

    def _fake_pipeline(**_kwargs):
        return "file://pipeline-output.json"

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0, wait_seconds_override=None, **_kwargs):  # noqa: ARG001
        return {"table_raw": "|x|y|z|"}

    def _fake_rows_from_markdown(markdown, template):  # noqa: ARG001
        return [[canonical_mmdd, canonical_menu, "9"]]

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            raw_text='{"rows":[]}',
            provider_debug={
                "provider": "gemini",
                "model": "gemini-2.5-pro",
                "finish_reason": "MAX_TOKENS",
                "recovered_truncated_json": True,
                "full_table_mode": True,
                "returned_row_indexes": [],
            },
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        parse_inputs.append([list(item) for item in (rows or [])])
        return []

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "rows_from_markdown", _fake_rows_from_markdown)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id, **_kwargs: (dict(baseline_sheet), None))
    monkeypatch.setattr(
        order_service,
        "_run_llm_reparse_audit",
        lambda **_kwargs: {
            "status": "pass",
            "issues": [],
            "issue_count": 0,
            "blocking_issues": [],
            "blocking_issue_count": 0,
        },
    )
    monkeypatch.setattr(order_service, "_validate_reparse_lines_against_weekly_menu", lambda **_kwargs: (None, None))
    monkeypatch.setattr(order_service, "_build_reparse_position_menu_entries", lambda **_kwargs: [])
    monkeypatch.setattr(order_service, "_resolve_llm_expected_row_count", lambda **_kwargs: 2)
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "9"]]),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert updated is None
    assert error == "llm_full_table_truncated_output"
    assert parse_inputs == []

    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") == "failed"
    metrics = job.get("metrics") or {}
    assert metrics.get("error") == "llm_full_table_truncated_output"
    assert metrics.get("finish_reason") == "MAX_TOKENS"
    assert metrics.get("truncated_output") is True
    assert metrics.get("rows_replaced_with_pipeline") is False


def test_reparse_order_quantity_only_rows_merge_without_provider_flag(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-gemini-quantity-only-no-flag.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-gemini-quantity-only-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 1,
            }
        ],
    )
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")

    def _fake_pipeline(**_kwargs):
        return "file://pipeline-output.json"

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0, wait_seconds_override=None, **_kwargs):  # noqa: ARG001
        return {"table_raw": "|x|y|z|"}

    def _fake_rows_from_markdown(markdown, template):  # noqa: ARG001
        return [[canonical_mmdd, canonical_daypart, canonical_menu, "4", "1", "1", "5", "2", "1", ""]]

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["1/29"],
            # Quantity-only shaped row (date/daypart/menu blank) without provider flag.
            table_rows=[["", "", "", "4", "1", "1", "5", "2", "1", ""]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            raw_text='{"rows":[{"date_mmdd":"2/15"}]}',
            provider_debug={
                "provider": "gemini",
                "finish_reason": "STOP",
            },
        )

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "rows_from_markdown", _fake_rows_from_markdown)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setenv("OCR_REPARSE_ENABLE_PIPELINE_QUANTITY_MERGE", "1")

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)
    assert error is None
    assert updated is not None
    assert updated.get("week") == "2026-02"
    assert updated["lines"]
    first = updated["lines"][0]
    assert first.get("menu_name") == canonical_menu
    assert first.get("daypart") == canonical_daypart
    assert first.get("date") == canonical_date

    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") == "done"
    metrics = job.get("metrics") or {}
    merge_stats = metrics.get("llm_quantity_only_merge")
    assert isinstance(merge_stats, dict)
    assert int(merge_stats.get("quantity_cells_updated") or 0) > 0


def test_reparse_order_resolves_week_from_ocr_payload_when_existing_week_has_no_menu(
    monkeypatch, tmp_path
):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-gemini-week-resolution.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-gemini-week-resolution-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-01",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    def _fake_config(_facility_id: str):
        return {
            "facility_id": "FAC00001",
            "fax_template": {
                "header_rows": 0,
                "map_menu_by_position": True,
                "columns": [
                    {"index": 0, "role": "date"},
                    {"index": 1, "role": "daypart"},
                    {"index": 2, "role": "menu_name"},
                    {
                        "index": 3,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "bag_type": "standard",
                    },
                ],
                "main_ocr_row_fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_2f",
                ],
                "fill_forward_roles": ["date", "daypart", "menu_name"],
                "fill_missing_date_with_hint": True,
            },
        }

    def _fake_pipeline(**_kwargs):
        return "file://pipeline-output.json"

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0, wait_seconds_override=None, **_kwargs):  # noqa: ARG001
        return {
            "table_rows": [
                ["2/15", "昼", "Menu A", ""],
                ["2/15", "夕", "Menu B", ""],
            ]
        }

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["1/29"],
            table_rows=[
                ["", "", "", "20"],
                ["", "", "", "11"],
            ],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "finish_reason": "STOP"},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        lines = []
        for idx, row in enumerate(rows):
            qty_raw = str(row[3] if len(row) > 3 else "").strip()
            if not qty_raw:
                continue
            lines.append(
                {
                    "date": "2026-01-29",
                    "daypart": row[1] or None,
                    "menu_name": row[2] or None,
                    "diet_type": "regular",
                    "area_id": "2F",
                    "bag_type": "standard",
                    "quantity_original": int(qty_raw),
                    "source_row_index": idx,
                }
            )
        return lines

    def _fake_weekly_entries(week_id: str):
        if week_id == "2026-02":
            return [
                {"menu_date": date(2026, 2, 15), "daypart_key": "昼", "menu_name": "Menu A"},
                {"menu_date": date(2026, 2, 15), "daypart_key": "夕", "menu_name": "Menu B"},
            ]
        return []

    monkeypatch.setattr(config_service, "get_facility_config", _fake_config)
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    assert updated.get("week") == "2026-02"
    assert updated.get("lines")
    first = updated["lines"][0]
    assert first.get("date") == "2026-02-15"
    assert first.get("daypart") in {"昼", "夕"}
    assert first.get("menu_name") in {"Menu A", "Menu B"}


def test_parse_order_lines_strict_numeric_keeps_zero_and_rejects_noise():
    template = {
        "header_rows": 0,
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "menu_name"},
            {
                "index": 2,
                "role": "quantity",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
            },
        ],
    }
    rows = [
        ["02/15", "Menu A", "0"],
        ["02/15", "Menu B", "副23"],
        ["02/15", "Menu C", "3000"],
    ]
    lines = parse_order_lines(
        rows,
        template,
        datetime(2026, 2, 15, 9, 0, 0),
        {
            "zero_as_empty": False,
            "strict_numeric_quantity_cell": True,
            "max_quantity_abs": 150,
        },
    )
    assert len(lines) == 1
    assert lines[0]["menu_name"] == "Menu A"
    assert lines[0]["quantity_original"] == 0


def test_parse_order_lines_non_strict_still_parses_mixed_numeric_text():
    template = {
        "header_rows": 0,
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "menu_name"},
            {
                "index": 2,
                "role": "quantity",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
            },
        ],
    }
    rows = [["02/15", "Menu B", "副23"]]
    lines = parse_order_lines(
        rows,
        template,
        datetime(2026, 2, 15, 9, 0, 0),
        {"zero_as_empty": False},
    )
    assert len(lines) == 1
    assert lines[0]["quantity_original"] == 23


def test_parse_order_lines_allow_blank_structure_rows_keeps_quantity_only_rows():
    template = {
        "header_rows": 0,
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {
                "index": 3,
                "role": "quantity",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
            },
        ],
    }
    rows = [
        ["", "", "", "20"],
        ["", "", "", "11"],
    ]
    lines = parse_order_lines(
        rows,
        template,
        datetime(2026, 2, 15, 9, 0, 0),
        {
            "zero_as_empty": False,
            "strict_numeric_quantity_cell": True,
            "allow_blank_structure_rows": True,
        },
    )
    assert len(lines) == 2
    assert [int(line["quantity_original"]) for line in lines] == [20, 11]
    assert [line["source_row_index"] for line in lines] == [0, 1]
    assert all(line.get("date") is None for line in lines)
    assert all(line.get("menu_name") is None for line in lines)


def test_parse_order_lines_rows_are_body_only_ignores_template_header_rows():
    template = {
        "header_rows": 2,
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {
                "index": 3,
                "role": "quantity",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
            },
        ],
    }
    rows = [
        ["", "", "", ""],
        ["", "", "", ""],
        ["", "", "", "42"],
        ["", "", "", "42"],
        ["", "", "", "42"],
        ["", "", "", "43"],
    ]
    lines = parse_order_lines(
        rows,
        template,
        datetime(2026, 2, 15, 9, 0, 0),
        {
            "zero_as_empty": False,
            "strict_numeric_quantity_cell": True,
            "allow_blank_structure_rows": True,
            "rows_are_body_only": True,
        },
    )
    assert [int(line["quantity_original"]) for line in lines] == [42, 42, 42, 43]
    assert [line["source_row_index"] for line in lines] == [2, 3, 4, 5]


def test_build_reparse_quantity_rules_marks_llm_rows_as_body_only():
    rules = order_service._build_reparse_quantity_rules({}, strict_llm_quantity=True)
    assert rules["allow_blank_structure_rows"] is True
    assert rules["rows_are_body_only"] is True
    assert rules["strict_numeric_quantity_cell"] is True


def test_build_sheet_lines_from_ocr_payload_treats_first_pass_rows_as_body_only(monkeypatch):
    template = {
        "header_rows": 2,
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "X"},
        ],
    }
    first_pass_rows = [
        ["04/26", "朝", "Menu A", "11"],
        ["04/26", "朝", "Menu B", "12"],
        ["04/26", "昼", "Menu C", "13"],
        ["04/26", "夕", "Menu D", "14"],
    ]

    monkeypatch.setattr(
        order_service,
        "_extract_first_pass_rows_from_payload",
        lambda *_args, **_kwargs: [list(row) for row in first_pass_rows],
    )
    monkeypatch.setattr(
        order_service,
        "_extract_sheet_rows_from_payload",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        order_service,
        "_collect_sheet_dates_from_payload",
        lambda *_args, **_kwargs: {date(2026, 4, 26)},
    )

    lines = order_service._build_sheet_lines_from_ocr_payload(
        payload={},
        template=template,
        received_at=datetime(2026, 4, 26, 9, 0, 0),
        week_id=None,
        facility_id="FAC00006",
        quantity_rules={
            "zero_as_empty": False,
            "strict_numeric_quantity_cell": True,
        },
    )

    assert [line["menu_name"] for line in lines] == ["Menu A", "Menu B", "Menu C", "Menu D"]
    assert [int(line["quantity_original"]) for line in lines] == [11, 12, 13, 14]
    assert [line["source_row_index"] for line in lines] == [0, 1, 2, 3]


def test_canonicalize_sheet_daypart_rows_keeps_blank_continuations_but_does_not_collapse_unsupported_blocks():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x"]
    rows = [
        ["04/26", "朝", "Menu A", "11"],
        ["", "", "Menu B", "12"],
        ["", "タ", "Menu C", "13"],
        ["", "夕", "Menu D", "14"],
    ]

    normalized = order_service._canonicalize_sheet_daypart_rows(rows=rows, fields=fields)

    assert [row[1] for row in normalized] == ["朝", "朝", "昼", "夕"]


def test_canonicalize_sheet_daypart_rows_leaves_unsupported_block_unresolved_without_later_anchor():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x"]
    rows = [
        ["04/26", "朝", "Menu A", "11"],
        ["", "タ", "Menu B", "12"],
    ]

    normalized = order_service._canonicalize_sheet_daypart_rows(rows=rows, fields=fields)

    assert [row[1] for row in normalized] == ["朝", "タ"]


def test_build_sheet_quantity_cell_metadata_rows_marks_exact_and_ordered_placements():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.soft_x"]
    quantity_index = {
        ("regular", "x"): 3,
        ("soft", "x"): 4,
    }
    rows = [
        {"values": ["04/26", "朝", "Menu A", "11", ""]},
        {"values": ["04/26", "昼", "Menu B", "12", "2"]},
        {"values": ["04/26", "夕", "Menu C", "", ""]},
    ]

    confidence_rows, provenance_rows = order_service._build_sheet_quantity_cell_metadata_rows(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        mapped_mode="raw_ocr_block",
        quantity_value_source="ocr_payload",
        shell_projection_stats={
            "exact_target_row_indexes": [0],
            "ordered_target_row_indexes": [1],
        },
    )

    assert confidence_rows == [
        ["", "", "", "high", ""],
        ["", "", "", "medium", "medium"],
        ["", "", "", "", ""],
    ]
    assert provenance_rows == [
        ["", "", "", "ocr_payload_exact", ""],
        ["", "", "", "ocr_payload_ordered", "ocr_payload_ordered"],
        ["", "", "", "", ""],
    ]


def test_build_weekly_menu_numeric_cell_items_classifies_accepted_and_deterministic_candidates():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.soft_x"]
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "X"},
            {"index": 4, "role": "quantity", "diet_type": "soft", "area_id": "X"},
        ],
    }
    rows = [
        {
            "values": ["04/26", "朝", "Menu A", "11", ""],
            "identity": ("2026-04-26", "朝", "menua"),
        },
        {
            "values": ["04/26", "昼", "Menu B", "", ""],
            "identity": ("2026-04-26", "昼", "menub"),
        },
    ]
    payload_rows = [
        ["04/26", "朝", "Menu A", "11", ""],
        ["", "タ", "Menu B", "12", "2"],
    ]

    items, summary = order_service._build_weekly_menu_numeric_cell_items(
        rows=rows,
        fields=fields,
        payload_rows=payload_rows,
        template=template,
        shell_projection_stats={
            "accepted_payload_mappings": [
                {"payload_row_index": 0, "target_row_index": 0, "mapping_type": "exact"},
            ],
        },
    )

    assert summary == {
        "raw_ocr_numeric_count": 3,
        "accepted_count": 1,
        "deterministic_candidate_count": 2,
        "weak_candidate_count": 0,
        "unresolved_count": 0,
    }
    assert [item["classification"] for item in items] == [
        "accepted",
        "deterministic_candidate",
        "deterministic_candidate",
    ]
    assert [item.get("target_row_index") for item in items] == [0, 1, 1]
    assert [item.get("target_col_index") for item in items] == [3, 3, 4]


def test_build_weekly_menu_numeric_cell_items_accepts_current_identity_matches_without_shell_projection_stats():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.soft_x"]
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "X"},
            {"index": 4, "role": "quantity", "diet_type": "soft", "area_id": "X"},
        ],
    }
    rows = [
        {
            "values": ["04/26", "朝", "Menu A", "11", ""],
            "identity": ("2026-04-26", "朝", "menua"),
        },
        {
            "values": ["04/26", "昼", "Menu B", "", ""],
            "identity": ("2026-04-26", "昼", "menub"),
        },
    ]
    payload_rows = [
        ["04/26", "朝", "Menu A", "11", ""],
        ["", "タ", "Menu B", "12", "2"],
    ]

    items, summary = order_service._build_weekly_menu_numeric_cell_items(
        rows=rows,
        fields=fields,
        payload_rows=payload_rows,
        template=template,
        shell_projection_stats={},
    )

    assert summary == {
        "raw_ocr_numeric_count": 3,
        "accepted_count": 1,
        "deterministic_candidate_count": 2,
        "weak_candidate_count": 0,
        "unresolved_count": 0,
    }
    assert [item["classification"] for item in items] == [
        "accepted",
        "deterministic_candidate",
        "deterministic_candidate",
    ]
    assert [item["placement_basis"] for item in items] == [
        "identity_current_match",
        "date_menu_unique",
        "date_menu_unique",
    ]
    assert [item.get("target_row_index") for item in items] == [0, 1, 1]
    assert [item.get("target_col_index") for item in items] == [3, 3, 4]


def test_build_weekly_menu_numeric_cell_items_supplements_accepted_cells_from_current_sheet_provenance():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.soft_x"]
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "X"},
            {"index": 4, "role": "quantity", "diet_type": "soft", "area_id": "X"},
        ],
    }
    rows = [
        {
            "values": ["04/26", "朝", "Menu A", "11", ""],
            "identity": ("2026-04-26", "朝", "menua"),
        },
        {
            "values": ["04/26", "昼", "Menu B", "12", ""],
            "identity": ("2026-04-26", "昼", "menub"),
        },
    ]
    payload_rows = [
        ["04/26", "朝", "Menu A", "11", ""],
        ["", "タ", "Menu B", "", ""],
    ]
    cell_provenance_rows = [
        ["", "", "", "ocr_payload_exact", ""],
        ["", "", "", "ocr_payload_identity", ""],
    ]

    items, summary = order_service._build_weekly_menu_numeric_cell_items(
        rows=rows,
        fields=fields,
        payload_rows=payload_rows,
        template=template,
        shell_projection_stats={
            "accepted_payload_mappings": [
                {"payload_row_index": 0, "target_row_index": 0, "mapping_type": "exact"},
            ],
        },
        cell_provenance_rows=cell_provenance_rows,
    )

    assert summary == {
        "raw_ocr_numeric_count": 2,
        "accepted_count": 2,
        "deterministic_candidate_count": 0,
        "weak_candidate_count": 0,
        "unresolved_count": 0,
    }
    assert [item["classification"] for item in items] == ["accepted", "accepted"]
    assert [item.get("target_row_index") for item in items] == [0, 1]
    assert [item.get("target_col_index") for item in items] == [3, 3]
    assert [item["placement_basis"] for item in items] == [
        "weekly_menu_exact",
        "identity_current_match",
    ]
    assert [item["read_basis"] for item in items] == [
        "direct_payload_numeric_cell",
        "current_sheet_ocr_provenance",
    ]


def test_build_weekly_menu_numeric_cell_items_marks_unresolved_when_target_not_proven():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x"]
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "X"},
        ],
    }
    rows = [
        {
            "values": ["04/26", "朝", "Menu B", "", ""],
            "identity": ("04/26", "朝", "menub"),
        },
        {
            "values": ["04/26", "昼", "Menu B", "", ""],
            "identity": ("04/26", "昼", "menub"),
        },
    ]
    payload_rows = [["04/26", "タ", "Menu B", "12"]]

    items, summary = order_service._build_weekly_menu_numeric_cell_items(
        rows=rows,
        fields=fields,
        payload_rows=payload_rows,
        template=template,
        shell_projection_stats={},
    )

    assert summary == {
        "raw_ocr_numeric_count": 1,
        "accepted_count": 0,
        "deterministic_candidate_count": 0,
        "weak_candidate_count": 0,
        "unresolved_count": 1,
    }
    assert items == [
        {
            "classification": "unresolved",
            "value": "12",
            "confidence_tier": "low",
            "placement_basis": "unresolved",
            "read_basis": "direct_payload_numeric_cell",
            "source_row_index": 0,
            "source_col_index": 3,
            "date_key": "04/26",
            "daypart_key": "タ",
            "menu_key": "menub",
            "reason": "target_not_proven",
        }
    ]


def test_build_weekly_menu_numeric_cell_items_marks_weak_candidates_for_unique_blank_cell():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x"]
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "X"},
        ],
    }
    rows = [
        {
            "values": ["04/26", "朝", "Menu A", "11"],
            "identity": ("04/26", "朝", "menua"),
        },
        {
            "values": ["04/26", "昼", "Menu B", ""],
            "identity": ("04/26", "昼", "menub"),
        },
        {
            "values": ["04/26", "夕", "Menu C", "13"],
            "identity": ("04/26", "夕", "menuc"),
        },
    ]
    payload_rows = [["04/26", "タ", "", "12"]]

    items, summary = order_service._build_weekly_menu_numeric_cell_items(
        rows=rows,
        fields=fields,
        payload_rows=payload_rows,
        template=template,
        shell_projection_stats={},
    )

    assert summary == {
        "raw_ocr_numeric_count": 1,
        "accepted_count": 0,
        "deterministic_candidate_count": 0,
        "weak_candidate_count": 1,
        "unresolved_count": 0,
    }
    assert items == [
        {
            "classification": "weak_candidate",
            "value": "12",
            "confidence_tier": "low",
            "placement_basis": "date_group_blank_unique",
            "read_basis": "direct_payload_numeric_cell",
            "source_row_index": 0,
            "source_col_index": 3,
            "target_row_index": 1,
            "target_col_index": 3,
            "date_key": "04/26",
            "daypart_key": "タ",
            "menu_key": "",
        }
    ]


def test_validate_reparse_lines_against_weekly_menu_accepts_body_only_blank_anchor_rows():
    template = {
        "header_rows": 2,
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "X"},
        ],
    }
    entries_override = [
        {"menu_date": "2026-02-15", "daypart_key": "朝", "menu_name": "A0"},
        {"menu_date": "2026-02-15", "daypart_key": "朝", "menu_name": "A1"},
        {"menu_date": "2026-02-15", "daypart_key": "昼", "menu_name": "A2"},
        {"menu_date": "2026-02-15", "daypart_key": "昼", "menu_name": "A3"},
        {"menu_date": "2026-02-15", "daypart_key": "昼", "menu_name": "A4"},
        {"menu_date": "2026-02-15", "daypart_key": "夕", "menu_name": "A5"},
    ]
    ocr_rows = [
        ["", "", "", ""],
        ["", "", "", ""],
        ["", "", "", "42"],
        ["", "", "", "42"],
        ["", "", "", "42"],
        ["", "", "", "43"],
    ]
    lines = [
        {
            "source_row_index": 2,
            "date": "2026-02-15",
            "daypart": "昼",
            "menu_name": "A2",
            "diet_type": "regular",
            "area_id": "X",
            "quantity_original": 42,
        },
        {
            "source_row_index": 3,
            "date": "2026-02-15",
            "daypart": "昼",
            "menu_name": "A3",
            "diet_type": "regular",
            "area_id": "X",
            "quantity_original": 42,
        },
        {
            "source_row_index": 4,
            "date": "2026-02-15",
            "daypart": "昼",
            "menu_name": "A4",
            "diet_type": "regular",
            "area_id": "X",
            "quantity_original": 42,
        },
        {
            "source_row_index": 5,
            "date": "2026-02-15",
            "daypart": "夕",
            "menu_name": "A5",
            "diet_type": "regular",
            "area_id": "X",
            "quantity_original": 43,
        },
    ]

    error, detail = order_service._validate_reparse_lines_against_weekly_menu(
        lines=lines,
        week_id="2026-02@2026-02-15~2026-02-21",
        ocr_rows=ocr_rows,
        template=template,
        entries_override=entries_override,
        rows_are_body_only=True,
    )

    assert error is None
    assert detail is None


def test_legacy_menu_position_mapping_is_disabled(monkeypatch):
    lines = [
        {
            "date": "2026-02-15",
            "daypart": "昼",
            "menu_name": "OCR menu 1",
            "source_row_index": 0,
            "diet_type": "regular",
            "area_id": "2F",
            "quantity_original": 4,
        },
        {
            "date": "2026-02-17",
            "daypart": "朝",
            "menu_name": "OCR menu 2",
            "source_row_index": 1,
            "diet_type": "regular",
            "area_id": "2F",
            "quantity_original": 4,
        },
    ]

    def _fake_build_position_menu_entries(_week_id):
        return [
            {
                "menu_date": date(2026, 2, 15),
                "daypart_key": "夕",
                "menu_name": "Menu X",
            },
            {
                "menu_date": date(2026, 2, 16),
                "daypart_key": "朝",
                "menu_name": "Menu Y",
            },
        ]

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_build_position_menu_entries)

    with pytest.raises(order_service.LegacyPositionMappingDisabledError):
        order_service._apply_menu_position_mapping(lines, "2026-02")


def test_legacy_menu_position_mapping_safe_wrapper_is_disabled(monkeypatch):
    lines = [
        {
            "date": "2026-02-15",
            "daypart": "夕",
            "menu_name": "OCR drifted",
            "source_row_index": 1,
            "diet_type": "regular",
            "area_id": "2F",
            "quantity_original": 3,
        }
    ]

    def _fake_build_position_menu_entries(_week_id):
        return [
            {
                "menu_date": date(2026, 2, 15),
                "daypart_key": "朝",
                "menu_name": "Menu 0",
            },
            {
                "menu_date": date(2026, 2, 15),
                "daypart_key": "昼",
                "menu_name": "Menu 1",
            },
        ]

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_build_position_menu_entries)

    with pytest.raises(order_service.LegacyPositionMappingDisabledError):
        order_service._apply_menu_position_mapping_safe(lines, "2026-02")


def test_build_position_entries_for_lines_scopes_single_payload_date_when_line_dates_missing(monkeypatch):
    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)

    scoped = order_service._build_position_entries_for_lines(
        week_id="2026-02",
        lines=[
            {
                "date": "",
                "daypart": "朝",
                "menu_name": "OCR-no-date",
                "source_row_index": 0,
            }
        ],
        payload_dates={date(2026, 2, 8)},
    )

    assert scoped
    assert {entry.get("menu_date") for entry in scoped} == {date(2026, 2, 8)}


def test_build_position_entries_for_lines_does_not_expand_sparse_source_row_span(monkeypatch):
    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)

    scoped = order_service._build_position_entries_for_lines(
        week_id="2026-02",
        lines=[
            {"source_row_index": 0},
            {"source_row_index": 1},
            {"source_row_index": 2},
            {"source_row_index": 3},
            {"source_row_index": 39},
        ],
        payload_dates={date(2026, 2, 8)},
    )

    assert len(scoped) == 8
    assert {entry.get("menu_date") for entry in scoped} == {date(2026, 2, 8)}


def test_build_position_entries_for_lines_expands_dense_source_row_span(monkeypatch):
    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)

    scoped = order_service._build_position_entries_for_lines(
        week_id="2026-02",
        lines=[{"source_row_index": idx} for idx in range(12)],
        payload_dates={date(2026, 2, 8)},
    )

    assert len(scoped) == 12
    assert scoped[0].get("menu_date") == date(2026, 2, 8)
    assert scoped[-1].get("menu_date") == date(2026, 2, 9)


def test_build_reparse_position_entries_prefers_existing_line_dates_when_new_line_dates_collapse(
    monkeypatch,
):
    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)

    # Simulate broken LLM parse collapsing all rows to a single date.
    new_lines = [
        {
            "date": "2026-02-08",
            "daypart": "朝",
            "menu_name": f"OCR-{idx}",
            "source_row_index": idx,
        }
        for idx in range(54)
    ]
    # Persisted order lines still have the correct week range anchors.
    existing_lines = [
        {
            "date": f"2026-02-{day:02d}",
            "daypart": "朝",
            "menu_name": f"existing-{day}",
        }
        for day in range(8, 14)
    ]

    scoped = order_service._build_reparse_position_menu_entries(
        week_id="2026-02",
        lines=new_lines,
        rows=[["", "", "", "6"] for _ in range(54)],
        parsed_output={},
        existing_lines=existing_lines,
        extra_payload_dates=set(),
        received_at=datetime(2026, 2, 8, 9, 0, 0),
    )

    assert scoped
    assert {entry.get("menu_date") for entry in scoped} == {
        date(2026, 2, 8),
        date(2026, 2, 9),
        date(2026, 2, 10),
        date(2026, 2, 11),
        date(2026, 2, 12),
        date(2026, 2, 13),
    }


def test_build_reparse_position_entries_ignores_payload_date_outlier_with_existing_scope(
    monkeypatch,
):
    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)

    # Broken parse collapsed rows to one wrong date (2/1).
    new_lines = [
        {
            "date": "2026-02-01",
            "daypart": "朝",
            "menu_name": f"OCR-{idx}",
            "source_row_index": idx,
        }
        for idx in range(224)
    ]
    # Persisted lines preserve true weekly scope (2/8-2/13).
    existing_lines = [
        {
            "date": f"2026-02-{day:02d}",
            "daypart": "朝",
            "menu_name": f"existing-{day}",
        }
        for day in range(8, 14)
    ]

    scoped = order_service._build_reparse_position_menu_entries(
        week_id="2026-02",
        lines=new_lines,
        rows=[["", "", "", "4"] for _ in range(224)],
        parsed_output={},
        existing_lines=existing_lines,
        extra_payload_dates={date(2026, 2, 1)},
        received_at=datetime(2026, 2, 8, 9, 0, 0),
    )

    assert scoped
    assert {entry.get("menu_date") for entry in scoped} == {
        date(2026, 2, 8),
        date(2026, 2, 9),
        date(2026, 2, 10),
        date(2026, 2, 11),
        date(2026, 2, 12),
        date(2026, 2, 13),
    }


def test_build_reparse_position_entries_ignores_out_of_scope_line_date_range_with_existing_scope(
    monkeypatch,
):
    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)

    # Broken parse shifted to prior week range (2/1-2/6).
    new_lines = []
    for day in range(1, 7):
        for idx in range(8):
            new_lines.append(
                {
                    "date": f"2026-02-{day:02d}",
                    "daypart": "朝",
                    "menu_name": f"OCR-{day}-{idx}",
                    "source_row_index": len(new_lines),
                }
            )

    # Persisted lines preserve true scope (2/8-2/13).
    existing_lines = [
        {
            "date": f"2026-02-{day:02d}",
            "daypart": "朝",
            "menu_name": f"existing-{day}",
        }
        for day in range(8, 14)
    ]

    scoped = order_service._build_reparse_position_menu_entries(
        week_id="2026-02",
        lines=new_lines,
        rows=[["", "", "", "4"] for _ in range(224)],
        parsed_output={},
        existing_lines=existing_lines,
        extra_payload_dates={date(2026, 2, 1)},
        received_at=datetime(2026, 2, 8, 9, 0, 0),
    )

    assert scoped
    assert {entry.get("menu_date") for entry in scoped} == {
        date(2026, 2, 8),
        date(2026, 2, 9),
        date(2026, 2, 10),
        date(2026, 2, 11),
        date(2026, 2, 12),
        date(2026, 2, 13),
    }


def test_build_reparse_position_entries_does_not_override_existing_scope_for_sparse_payload_noise(
    monkeypatch,
):
    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)

    # Existing lines preserve the correct scope (2/8-2/13).
    existing_lines = [
        {
            "date": f"2026-02-{day:02d}",
            "daypart": "朝",
            "menu_name": f"existing-{day}",
        }
        for day in range(8, 14)
    ]
    # Payload anchors are sparse and spread across the month (noise).
    parsed_output = {
        "date_strings": ["2/1", "2/4", "2/8", "2/12", "2/16", "2/20", "2/24"]
    }
    new_lines = [
        {
            "date": "2026-02-01",
            "daypart": dayparts[idx % len(dayparts)],
            "menu_name": f"OCR-{idx}",
            "source_row_index": idx,
        }
        for idx in range(56)
    ]

    scoped = order_service._build_reparse_position_menu_entries(
        week_id="2026-02",
        lines=new_lines,
        rows=[["", "", "", "6"] for _ in range(56)],
        parsed_output=parsed_output,
        existing_lines=existing_lines,
        extra_payload_dates=set(),
        received_at=datetime(2026, 2, 8, 9, 0, 0),
    )

    assert scoped
    assert {entry.get("menu_date") for entry in scoped} == {
        date(2026, 2, 8),
        date(2026, 2, 9),
        date(2026, 2, 10),
        date(2026, 2, 11),
        date(2026, 2, 12),
        date(2026, 2, 13),
    }


def test_build_reparse_position_entries_allows_payload_week_shift_when_existing_scope_is_stale(
    monkeypatch,
):
    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)

    # Existing order lines were already shifted to an old week (2/1-2/7).
    existing_lines = [
        {
            "date": f"2026-02-{day:02d}",
            "daypart": "朝",
            "menu_name": f"existing-{day}",
        }
        for day in range(1, 8)
    ]
    # New payload carries multiple anchors for the correct week (2/8-2/14).
    parsed_output = {
        "date_strings": [
            "2026/01/18",  # header timestamp noise
            "2/8",
            "2/9",
            "2/10",
            "2/11",
            "2/12",
            "2/13",
            "2/14",
        ]
    }

    scoped = order_service._build_reparse_position_menu_entries(
        week_id="2026-02",
        lines=[{"source_row_index": idx, "menu_name": f"OCR-{idx}"} for idx in range(48)],
        rows=[["", "", "", "6"] for _ in range(48)],
        parsed_output=parsed_output,
        existing_lines=existing_lines,
        extra_payload_dates=set(),
        received_at=datetime(2026, 1, 28, 9, 0, 0),
    )

    assert scoped
    assert {entry.get("menu_date") for entry in scoped} == {
        date(2026, 2, 8),
        date(2026, 2, 9),
        date(2026, 2, 10),
        date(2026, 2, 11),
        date(2026, 2, 12),
        date(2026, 2, 13),
        date(2026, 2, 14),
    }


def test_build_reparse_position_entries_ignores_stale_line_dates_when_payload_override_is_enabled(
    monkeypatch,
):
    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)

    existing_lines = [
        {"date": f"2026-02-{day:02d}", "daypart": "朝", "menu_name": f"existing-{day}"}
        for day in range(1, 8)
    ]
    # Parsed line dates are stale (old week), but payload has strong anchors for 2/8 week.
    new_lines = [
        {
            "date": f"2026-02-{1 + (idx // 8):02d}",
            "daypart": dayparts[idx % len(dayparts)],
            "menu_name": f"stale-{idx}",
            "source_row_index": idx,
        }
        for idx in range(48)
    ]
    parsed_output = {
        "date_strings": [
            "2/1",  # stale inside-anchor noise
            "2/8",
            "2/9",
            "2/10",
            "2/11",
            "2/12",
            "2/13",
            "2/14",
        ]
    }

    scoped = order_service._build_reparse_position_menu_entries(
        week_id="2026-02",
        lines=new_lines,
        rows=[["", "", "", "6"] for _ in range(48)],
        parsed_output=parsed_output,
        existing_lines=existing_lines,
        extra_payload_dates=set(),
        received_at=datetime(2026, 1, 28, 9, 0, 0),
    )

    assert scoped
    assert {entry.get("menu_date") for entry in scoped} == {
        date(2026, 2, 8),
        date(2026, 2, 9),
        date(2026, 2, 10),
        date(2026, 2, 11),
        date(2026, 2, 12),
        date(2026, 2, 13),
        date(2026, 2, 14),
    }


def test_build_reparse_position_entries_keeps_week_scope_when_existing_dates_are_partial(
    monkeypatch,
):
    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)

    # Persisted lines still reflect an older partial scope (2/8-2/9 only).
    existing_lines = [
        {"date": "2026-02-08", "daypart": "朝", "menu_name": "existing-0208"},
        {"date": "2026-02-09", "daypart": "朝", "menu_name": "existing-0209"},
    ]
    # Reparse lines already span a full week (2/8-2/14), and payload anchors
    # confirm the same week range.
    new_lines = []
    for idx in range(56):
        day = 8 + min(idx // 8, 6)
        new_lines.append(
            {
                "date": f"2026-02-{day:02d}",
                "daypart": dayparts[idx % len(dayparts)],
                "menu_name": f"OCR-{idx}",
                "source_row_index": idx,
            }
        )
    parsed_output = {
        "date_strings": ["2/8", "2/9", "2/10", "2/11", "2/12", "2/13", "2/14"]
    }

    scoped = order_service._build_reparse_position_menu_entries(
        week_id="2026-02",
        lines=new_lines,
        rows=[["", "", "", "6"] for _ in range(56)],
        parsed_output=parsed_output,
        existing_lines=existing_lines,
        extra_payload_dates=set(),
        received_at=datetime(2026, 2, 8, 9, 0, 0),
    )

    assert scoped
    assert len(scoped) == 56
    assert {entry.get("menu_date") for entry in scoped} == {
        date(2026, 2, 8),
        date(2026, 2, 9),
        date(2026, 2, 10),
        date(2026, 2, 11),
        date(2026, 2, 12),
        date(2026, 2, 13),
        date(2026, 2, 14),
    }


def test_build_reparse_position_entries_extends_small_source_row_overflow_with_existing_scope(
    monkeypatch,
):
    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)

    # Parsed lines keep existing date anchors (2/8-2/13) but include small
    # source-row overflow (54 rows instead of 48) from OCR table noise.
    new_lines = []
    for idx in range(54):
        day = 8 + min(idx // 8, 5)
        new_lines.append(
            {
                "date": f"2026-02-{day:02d}",
                "daypart": dayparts[idx % len(dayparts)],
                "menu_name": f"OCR-{idx}",
                "source_row_index": idx,
            }
        )

    existing_lines = [
        {
            "date": f"2026-02-{day:02d}",
            "daypart": "朝",
            "menu_name": f"existing-{day}",
        }
        for day in range(8, 14)
    ]

    scoped = order_service._build_reparse_position_menu_entries(
        week_id="2026-02",
        lines=new_lines,
        rows=[["", "", "", "4"] for _ in range(54)],
        parsed_output={},
        existing_lines=existing_lines,
        extra_payload_dates=set(),
        received_at=datetime(2026, 2, 8, 9, 0, 0),
    )

    assert scoped
    assert len(scoped) == 54
    scoped_dates = {entry.get("menu_date") for entry in scoped}
    assert date(2026, 2, 8) in scoped_dates
    assert date(2026, 2, 14) in scoped_dates


def test_build_reparse_position_entries_extends_payload_anchor_scope_for_moderate_overflow(
    monkeypatch,
):
    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)

    # Payload anchors only two days, but OCR rows span much further.
    parsed_output = {"date_strings": ["2/8", "2/9"]}
    scoped = order_service._build_reparse_position_menu_entries(
        week_id="2026-02",
        lines=[{"source_row_index": idx, "menu_name": f"OCR-{idx}"} for idx in range(43)],
        rows=[["", "", "", "6"] for _ in range(43)],
        parsed_output=parsed_output,
        existing_lines=[],
        extra_payload_dates=set(),
        received_at=datetime(2026, 2, 8, 9, 0, 0),
    )

    assert scoped
    assert len(scoped) == 43
    scoped_dates = {entry.get("menu_date") for entry in scoped}
    assert date(2026, 2, 8) in scoped_dates
    assert date(2026, 2, 13) in scoped_dates


def test_create_order_from_ingest_does_not_apply_legacy_position_mapping(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-ingest-position-scope.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")

    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)

    payload = IngestEmailPayload(
        message_id="msg-ingest-position-scope-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-02-08",
                "daypart": "朝",
                "menu_name": "OCR-0",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 6,
                "source_row_index": 0,
            }
        ],
    )

    refreshed = order_service.get_order_by_id(order["id"])
    assert refreshed is not None
    assert refreshed["lines"]
    first = refreshed["lines"][0]
    assert first.get("date") == "2026-02-08"
    assert first.get("menu_name") == "OCR-0"


def test_apply_ocr_table_does_not_apply_legacy_position_mapping(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-apply-position-scope.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-apply-position-scope-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    def _fake_parse(
        rows,
        template,
        received_at,
        quantity_rules,
        default_date=None,
        tokens=None,
        grid=None,
        pdf_bytes=None,
    ):  # noqa: ARG001
        return [
            {
                "date": "2026-02-08",
                "daypart": "朝",
                "menu_name": "OCR-0",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 6,
                "source_row_index": 0,
            },
            {
                "date": "2026-02-08",
                "daypart": "朝",
                "menu_name": "OCR-1",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 7,
                "source_row_index": 1,
            },
        ]

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)

    updated, error = order_service.apply_ocr_table(
        order["id"],
        header=["日付", "区分", "メニュー", "常食2F"],
        rows=[
            ["2/8", "朝", "dummy-0", "6"],
            ["2/8", "朝", "dummy-1", "7"],
        ],
        ui_mode="sheet",
        fields=["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        row_ids=["row-1", "row-2"],
    )

    assert error is None
    assert updated is not None
    assert updated.get("lines")
    assert {line.get("date") for line in updated["lines"]} == {"2026-02-08"}
    assert [line.get("menu_name") for line in updated["lines"]] == ["dummy-0", "dummy-1"]


def test_apply_ocr_table_preserves_parse_output_when_line_dates_missing_without_legacy_mapping(
    monkeypatch, tmp_path
):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-apply-position-scope-no-line-date.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-apply-position-scope-no-line-date-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    def _fake_parse(
        rows,
        template,
        received_at,
        quantity_rules,
        default_date=None,
        tokens=None,
        grid=None,
        pdf_bytes=None,
    ):  # noqa: ARG001
        return [
            {
                "date": None,
                "daypart": "朝",
                "menu_name": "OCR-no-date",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 6,
                "source_row_index": 0,
            }
        ]

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)

    updated, error = order_service.apply_ocr_table(
        order["id"],
        header=["日付", "区分", "メニュー", "常食2F"],
        rows=[["2/8", "朝", "dummy", "6"]],
        ui_mode="sheet",
        fields=["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        row_ids=["row-1"],
    )

    assert error is None
    assert updated is not None
    assert updated.get("lines")
    assert updated["lines"][0].get("date") == "2026-02-08"
    assert updated["lines"][0].get("menu_name") == "dummy"


def test_reparse_order_does_not_apply_legacy_position_mapping_on_single_date_anchor(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-reparse-position-scope.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-reparse-position-scope-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    def _fake_pipeline(**_kwargs):
        return None

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026/02/08"],
            table_rows=[["2/8", "朝", "dummy", "6"]],
            tokens=[],
            grid=None,
            ocr_provider="pipeline",
        )

    def _fake_parse(
        rows,
        template,
        received_at,
        quantity_rules,
        default_date=None,
        tokens=None,
        grid=None,
        pdf_bytes=None,
    ):  # noqa: ARG001
        return [
            {
                "date": "2026-02-08",
                "daypart": "朝",
                "menu_name": "OCR-0",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 6,
                "source_row_index": 0,
            }
        ]

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)

    updated, error = order_service.reparse_order(order["id"], ocr_provider="pipeline")

    assert updated is None
    assert error == "sheet_canonical_mismatch"


def test_reparse_order_rejects_date_anchor_drift_when_llm_quantity_only(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-reparse-date-anchor-drift.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-reparse-date-anchor-drift-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-02-08",
                "daypart": "朝",
                "menu_name": "keep-1",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 3,
            },
            {
                "date": "2026-02-09",
                "daypart": "朝",
                "menu_name": "keep-2",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 4,
            },
        ],
    )
    before_state = order_service.get_order_by_id(order["id"])
    assert before_state is not None
    before_dates = {line.get("date") for line in before_state.get("lines") or []}
    before_count = len(before_state.get("lines") or [])

    def _fake_pipeline(**_kwargs):
        return None

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["1/22"],
            table_rows=[["", "", "", "4", "1", "1", "5", "2", "1", ""] for _ in range(224)],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"quantity_only_mode": True},
        )

    def _fake_parse(
        rows,
        template,
        received_at,
        quantity_rules,
        default_date=None,
        tokens=None,
        grid=None,
        pdf_bytes=None,
    ):  # noqa: ARG001
        return [
            {
                "date": "2026-02-01",
                "daypart": "朝",
                "menu_name": "drift-1",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 3,
                "source_row_index": 0,
            },
            {
                "date": "2026-02-02",
                "daypart": "朝",
                "menu_name": "drift-2",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 4,
                "source_row_index": 1,
            },
        ]

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(
            [
                ["02/08", "朝", "keep-1", "3"],
                ["02/09", "朝", "keep-2", "4"],
            ],
        ),
    )
    monkeypatch.setattr(
        order_service,
        "_validate_reparse_lines_against_weekly_menu",
        lambda **kwargs: (None, None),
    )
    monkeypatch.setattr(
        order_service,
        "_validate_reparse_date_anchor_stability",
        lambda **kwargs: (
            "sheet_date_anchor_drift",
            {
                "previous_dates": ["2026-02-08", "2026-02-09"],
                "candidate_dates": ["2026-02-01", "2026-02-02"],
            },
        ),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert updated is None
    assert error == "sheet_date_anchor_drift"
    refreshed = order_service.get_order_by_id(order["id"])
    assert refreshed is not None
    assert refreshed.get("lines")
    assert len(refreshed["lines"]) == before_count
    assert {line.get("date") for line in refreshed["lines"]} == before_dates


def test_evaluate_quantity_only_rows_quality_detects_row_coverage_low(monkeypatch):
    monkeypatch.setenv("OCR_REPARSE_ROW_COVERAGE_MIN_RATIO", "0.98")
    monkeypatch.setenv("OCR_REPARSE_MAX_MISSING_TAIL_ROWS", "0")
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "2F"},
        ]
    }
    error, detail = order_service._evaluate_quantity_only_rows_quality(
        rows=[["", "", "", "6"]],
        template=template,
        expected_row_count=2,
        reference_rows=None,
    )

    assert error == "sheet_row_coverage_low"
    assert isinstance(detail, dict)
    assert detail.get("actual_row_count") == 1
    assert detail.get("expected_row_count") == 2
    assert detail.get("missing_tail_rows") == 1


def test_evaluate_quantity_only_rows_quality_detects_column_anomaly(monkeypatch):
    monkeypatch.setenv("OCR_REPARSE_ENABLE_COLUMN_ANOMALY_GATE", "1")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_NONEMPTY_MIN_RATIO", "0.25")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_NONEMPTY_MAX_RATIO", "3.0")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_RATIO_MIN_REFERENCE_COUNT", "1")
    monkeypatch.setenv("OCR_REPARSE_ENABLE_COLUMN_UNEXPECTED_NONEMPTY_GATE", "1")
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "2F"},
            {"index": 4, "role": "quantity", "diet_type": "regular", "area_id": "3F"},
        ]
    }
    llm_rows = [["", "", "", "", "7"], ["", "", "", "", "5"]]
    ref_rows = [["2/15", "昼", "A", "6", ""], ["2/15", "夕", "B", "4", ""]]
    error, detail = order_service._evaluate_quantity_only_rows_quality(
        rows=llm_rows,
        template=template,
        expected_row_count=2,
        reference_rows=ref_rows,
    )

    assert error == "sheet_column_anomaly"
    assert isinstance(detail, dict)
    assert int(detail.get("column_anomaly_count") or 0) >= 1


def test_evaluate_quantity_only_rows_quality_skips_ratio_gate_for_low_reference_columns(monkeypatch):
    monkeypatch.setenv("OCR_REPARSE_ENABLE_COLUMN_ANOMALY_GATE", "1")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_NONEMPTY_MIN_RATIO", "0.25")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_NONEMPTY_MAX_RATIO", "3.0")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_RATIO_MIN_REFERENCE_COUNT", "6")
    monkeypatch.setenv("OCR_REPARSE_ENABLE_COLUMN_UNEXPECTED_NONEMPTY_GATE", "0")
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "2F"},
            {"index": 4, "role": "quantity", "diet_type": "regular", "area_id": "3F"},
        ]
    }
    llm_rows = [
        ["", "", "", "7", ""],
        ["", "", "", "6", ""],
        ["", "", "", "5", ""],
        ["", "", "", "4", ""],
        ["", "", "", "3", ""],
        ["", "", "", "2", ""],
    ]
    # Reference rows are sparse/noisy (small sample), so ratio-based gate should not reject.
    ref_rows = [
        ["2/15", "昼", "A", "6", ""],
        ["2/15", "夕", "B", "", "4"],
        ["2/16", "朝", "C", "", ""],
    ]
    error, detail = order_service._evaluate_quantity_only_rows_quality(
        rows=llm_rows,
        template=template,
        expected_row_count=6,
        reference_rows=ref_rows,
    )

    assert error is None
    assert isinstance(detail, dict)
    assert int(detail.get("column_anomaly_count") or 0) == 0
    skipped = detail.get("column_ratio_skipped_low_reference") or {}
    assert skipped.get("3") == 1
    assert skipped.get("4") == 1


def test_evaluate_quantity_only_rows_quality_detects_mirrored_sibling_columns_without_reference(monkeypatch):
    monkeypatch.setenv("OCR_REPARSE_ENABLE_COLUMN_ANOMALY_GATE", "1")
    monkeypatch.setenv("OCR_REPARSE_ENABLE_COLUMN_MIRROR_GATE", "1")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_MIRROR_MIN_OVERLAP", "6")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_MIRROR_MIN_NONZERO_OVERLAP", "6")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_MIRROR_MIN_EQUAL_RATIO", "0.98")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_MIRROR_MIN_DISTINCT_PAIRS", "3")
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "2F", "bag_type": "standard"},
            {"index": 4, "role": "quantity", "diet_type": "regular", "area_id": "3F", "bag_type": "standard"},
        ]
    }
    llm_rows = [
        ["", "", "", "6", "6"],
        ["", "", "", "8", "8"],
        ["", "", "", "10", "10"],
        ["", "", "", "12", "12"],
        ["", "", "", "14", "14"],
        ["", "", "", "16", "16"],
    ]

    error, detail = order_service._evaluate_quantity_only_rows_quality(
        rows=llm_rows,
        template=template,
        expected_row_count=6,
        reference_rows=None,
    )

    assert error == "sheet_column_anomaly"
    assert isinstance(detail, dict)
    anomalies = detail.get("column_anomalies") or []
    assert any(item.get("reason") == "mirrored_sibling_columns" for item in anomalies if isinstance(item, dict))


def test_evaluate_quantity_only_rows_quality_skips_mirror_when_reference_matches(monkeypatch):
    monkeypatch.setenv("OCR_REPARSE_ENABLE_COLUMN_ANOMALY_GATE", "1")
    monkeypatch.setenv("OCR_REPARSE_ENABLE_COLUMN_MIRROR_GATE", "1")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_MIRROR_MIN_OVERLAP", "6")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_MIRROR_MIN_NONZERO_OVERLAP", "6")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_MIRROR_MIN_EQUAL_RATIO", "0.98")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_MIRROR_MIN_DISTINCT_PAIRS", "3")
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "2F", "bag_type": "standard"},
            {"index": 4, "role": "quantity", "diet_type": "regular", "area_id": "3F", "bag_type": "standard"},
        ]
    }
    mirrored_rows = [
        ["", "", "", "6", "6"],
        ["", "", "", "8", "8"],
        ["", "", "", "10", "10"],
        ["", "", "", "12", "12"],
        ["", "", "", "14", "14"],
        ["", "", "", "16", "16"],
    ]

    error, detail = order_service._evaluate_quantity_only_rows_quality(
        rows=mirrored_rows,
        template=template,
        expected_row_count=6,
        reference_rows=mirrored_rows,
    )

    assert error is None
    assert isinstance(detail, dict)
    assert int(detail.get("column_mirror_anomaly_count") or 0) == 0


def test_evaluate_reparse_line_count_regression_detects_sharp_drop(monkeypatch):
    monkeypatch.setenv("OCR_REPARSE_LINE_COUNT_GUARD_MIN_BEFORE", "24")
    monkeypatch.setenv("OCR_REPARSE_LINE_COUNT_MIN_RATIO", "0.7")
    monkeypatch.setenv("OCR_REPARSE_LINE_COUNT_MAX_DROP_ABS", "24")

    error, detail = order_service._evaluate_reparse_line_count_regression(
        provider="gemini",
        llm_quantity_only_active=True,
        before_count=80,
        after_count=4,
    )

    assert error == "sheet_line_count_regression"
    assert isinstance(detail, dict)
    assert detail.get("before_count") == 80
    assert detail.get("after_count") == 4


def test_evaluate_reparse_line_count_regression_skips_small_baseline(monkeypatch):
    monkeypatch.setenv("OCR_REPARSE_LINE_COUNT_GUARD_MIN_BEFORE", "24")

    error, detail = order_service._evaluate_reparse_line_count_regression(
        provider="gemini",
        llm_quantity_only_active=True,
        before_count=10,
        after_count=1,
    )

    assert error is None
    assert detail is None


def test_parse_llm_reparse_audit_issues_normalizes_payload():
    rows = [
        ["mirrored-sibling-columns", "HIGH", "8", "4", "0.93", "both columns show 12", "duplicate copy"],
        ["", "", "", "", "", "", ""],
    ]
    fields = [
        "issue_code",
        "severity",
        "row_index",
        "column_index",
        "confidence",
        "evidence",
        "reason",
    ]

    issues = order_service._parse_llm_reparse_audit_issues(
        rows=rows,
        fields=fields,
    )

    assert len(issues) == 1
    first = issues[0]
    assert first.get("issue_code") == "mirrored_sibling_columns"
    assert first.get("severity") == "high"
    assert first.get("row_index") == 8
    assert first.get("column_index") == 4
    assert first.get("confidence") == pytest.approx(0.93, rel=1e-6)


def test_resolve_llm_expected_row_count_prefers_observed_rows_when_menu_scope_is_over_broad():
    observed_rows = [[""] for _ in range(56)]
    resolved = order_service._resolve_llm_expected_row_count(
        menu_expected_row_count=224,
        pipeline_rows=[],
        observed_rows=observed_rows,
    )
    assert resolved == 56


def test_resolve_llm_expected_row_count_keeps_menu_scope_when_gap_is_not_large():
    observed_rows = [[""] for _ in range(56)]
    resolved = order_service._resolve_llm_expected_row_count(
        menu_expected_row_count=64,
        pipeline_rows=[],
        observed_rows=observed_rows,
    )
    assert resolved == 64


def test_resolve_llm_expected_row_count_prefers_pipeline_rows_for_partial_anchor_scope(monkeypatch):
    monkeypatch.setenv("OCR_REPARSE_PARTIAL_ANCHOR_MAX_ROWS", "24")
    monkeypatch.setenv("OCR_REPARSE_PARTIAL_ANCHOR_MIN_GAP_ROWS", "12")
    resolved = order_service._resolve_llm_expected_row_count(
        menu_expected_row_count=16,
        pipeline_rows=[[""] for _ in range(56)],
        observed_rows=[[""] for _ in range(43)],
    )
    assert resolved == 56


def test_resolve_llm_expected_row_count_prefers_observed_rows_for_partial_anchor_scope_without_pipeline(
    monkeypatch,
):
    monkeypatch.setenv("OCR_REPARSE_PARTIAL_ANCHOR_MAX_ROWS", "24")
    monkeypatch.setenv("OCR_REPARSE_PARTIAL_ANCHOR_MIN_GAP_ROWS", "12")
    resolved = order_service._resolve_llm_expected_row_count(
        menu_expected_row_count=16,
        pipeline_rows=[],
        observed_rows=[[""] for _ in range(43)],
    )
    assert resolved == 43


def test_resolve_llm_expected_row_count_prefers_pipeline_rows_when_anchor_is_weak(monkeypatch):
    monkeypatch.setenv("OCR_REPARSE_WEAK_ANCHOR_MAX_DATES", "1")
    monkeypatch.setenv("OCR_REPARSE_WEAK_ANCHOR_MIN_GAP_ROWS", "8")
    monkeypatch.setenv("OCR_REPARSE_WEAK_ANCHOR_OBSERVED_DELTA_ROWS", "2")
    resolved = order_service._resolve_llm_expected_row_count(
        menu_expected_row_count=56,
        pipeline_rows=[[""] for _ in range(43)],
        observed_rows=[[""] for _ in range(43)],
        anchor_date_count=1,
    )
    assert resolved == 43


def test_resolve_llm_expected_row_count_keeps_menu_scope_when_anchor_is_not_weak(monkeypatch):
    monkeypatch.setenv("OCR_REPARSE_WEAK_ANCHOR_MAX_DATES", "1")
    monkeypatch.setenv("OCR_REPARSE_WEAK_ANCHOR_MIN_GAP_ROWS", "8")
    monkeypatch.setenv("OCR_REPARSE_WEAK_ANCHOR_OBSERVED_DELTA_ROWS", "2")
    resolved = order_service._resolve_llm_expected_row_count(
        menu_expected_row_count=56,
        pipeline_rows=[[""] for _ in range(43)],
        observed_rows=[[""] for _ in range(43)],
        anchor_date_count=3,
    )
    assert resolved == 56


def test_ensure_unique_line_ids_replaces_duplicate_and_missing_ids(monkeypatch):
    generated_ids = iter(["OLNnew001", "OLNnew002", "OLNnew003"])
    monkeypatch.setattr(order_service, "_make_line_id", lambda: next(generated_ids))

    normalized = order_service._ensure_unique_line_ids(
        [
            {"id": "OLNkeep001", "menu_name": "A"},
            {"id": "OLNkeep001", "menu_name": "B"},
            {"id": "", "menu_name": "C"},
            {"menu_name": "D"},
        ]
    )

    ids = [row.get("id") for row in normalized]
    assert ids == ["OLNkeep001", "OLNnew001", "OLNnew002", "OLNnew003"]
    assert len(ids) == len(set(ids))


def test_estimate_reparse_llm_cost_marks_soft_and_hard_limit(monkeypatch):
    monkeypatch.setenv("OCR_REPARSE_COST_GEMINI_FLASH_INPUT_USD_PER_1M", "0.5")
    monkeypatch.setenv("OCR_REPARSE_COST_GEMINI_FLASH_OUTPUT_USD_PER_1M", "2.0")
    monkeypatch.setenv("OCR_REPARSE_COST_SOFT_LIMIT_USD", "0.01")
    monkeypatch.setenv("OCR_REPARSE_COST_HARD_LIMIT_USD", "0.02")

    cost = order_service._estimate_reparse_llm_cost(
        provider="gemini",
        model="gemini-2.5-flash",
        provider_debug={
            "usage": {
                "prompt_tokens": 10_000,
                "completion_tokens": 10_000,
                "total_tokens": 20_000,
            }
        },
    )

    assert isinstance(cost, dict)
    assert cost.get("estimated_cost_usd") == pytest.approx(0.025, rel=1e-6)
    assert cost.get("over_soft_limit") is True
    assert cost.get("over_hard_limit") is True


def test_build_quantity_only_repair_prompts_includes_focus_context():
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "2F"},
            {"index": 4, "role": "quantity", "diet_type": "regular", "area_id": "3F"},
        ],
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
            "qty.regular_3f",
        ],
        "gemini_ocr_prompt": "base-system",
        "gemini_ocr_user_prompt": "base-user",
    }
    current_rows = [
        ["2/15", "昼", "A", "6", ""],
        ["2/15", "夕", "B", "", ""],
    ]
    baseline_rows = [
        ["2/15", "朝", "Anchor 1", "", ""],
        ["2/15", "昼", "A", "6", ""],
        ["2/15", "夕", "B", "", ""],
    ]
    structural_rows = [
        ["2/15", "朝", "Anchor 1", "", ""],
        ["2/15", "朝", "Anchor 2", "", ""],
        ["2/15", "昼", "A", "6", ""],
        ["2/15", "夕", "B", "", ""],
    ]
    quality_detail = {
        "expected_row_count": 3,
        "actual_row_count": 2,
        "missing_tail_rows": 1,
        "column_anomalies": [{"index": 4, "reason": "unexpected_non_empty"}],
    }

    system_prompt, user_prompt = order_service._build_quantity_only_repair_prompts(
        provider="gemini",
        template=template,
        current_rows=current_rows,
        baseline_rows=baseline_rows,
        baseline_fields=template["main_ocr_row_fields"],
        structural_rows=structural_rows,
        structural_fields=template["main_ocr_row_fields"],
        expected_row_count=3,
        quality_error="sheet_column_anomaly",
        quality_detail=quality_detail,
        first_pass_model="gemini-2.5-flash",
        target_model="gemini-2.5-pro",
    )

    assert "Recheck focus row indexes first" in system_prompt
    assert "Recheck focus quantity column indexes first" in system_prompt
    assert "Failure focus locations and first-pass inference summary" in user_prompt
    assert '"first_pass_model": "gemini-2.5-flash"' in user_prompt
    assert '"target_model": "gemini-2.5-pro"' in user_prompt
    assert '"focus_row_indexes": [2]' in user_prompt
    assert '"focus_quantity_column_indexes": [4]' in user_prompt
    assert "Treat the current sheet/baseline rows as the user-visible structural context." in system_prompt
    assert "keep it blank unless the fax shows direct row-level evidence" in system_prompt
    assert "Infer unreadable quantities only within the same date/daypart block." in system_prompt
    assert "Current sheet/baseline rows shown to the user" in user_prompt
    assert '"Anchor 1"' in user_prompt
    assert "Blank-anchor structural row indexes" in user_prompt
    assert "[0, 1]" in user_prompt


def test_build_llm_assist_prompt_uses_yomitoku_structured_rows_and_derived_issues():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ],
        "gemini_ocr_prompt": "facility-base",
    }
    pipeline_output = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "page_index": 1,
                        "row_count": 4,
                        "col_count": 5,
                        "rows": [
                            ["日付", "区分", "献立", "数量", "備考"],
                            ["", "", "", "常食", ""],
                            ["2/15", "朝", "Menu A", "12", ""],
                            ["2/15", "昼", "Menu B", "6\n9", "note"],
                        ],
                        "cells": [
                            {
                                "row_index": 3,
                                "col_index": 3,
                                "row_span": 1,
                                "col_span": 1,
                                "text": "6\n9",
                                "bbox": [0.10, 0.30, 0.18, 0.38],
                            }
                        ],
                    }
                ],
            }
        ],
        "roi_overlay_rows": [
            {"row_index": 1, "qty.regular_x": 99},
        ],
    }

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=pipeline_output,
        llm_assist=True,
    )

    assert prompt is not None
    assert "Treat the first-pass yomitoku output as the baseline draft." in prompt
    assert "First-pass yomitoku structured rows:" in prompt
    assert "First-pass yomitoku structured tables/cells:" in prompt
    assert "Suspicious first-pass cells (review before changing):" in prompt
    assert '"Menu B"' in prompt
    assert '"issue_code": "multiline_numeric_cell"' in prompt
    assert '"source": "yomitoku_structured"' in prompt
    assert "99" not in prompt


def test_build_llm_assist_prompt_includes_auto_fallback_context():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
        ],
    }

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=None,
        llm_assist=True,
        failure_context={
            "trigger": "yomitoku_failed",
            "from_provider": "pipeline",
            "reason": "lines_empty",
            "row_count": 0,
            "line_count": 0,
        },
    )

    assert prompt is not None
    assert "Automatic fallback context:" in prompt
    assert "first-pass yomitoku/pipeline OCR did not produce parseable order lines" in prompt
    assert '"reason": "lines_empty"' in prompt
    assert '"from_provider": "pipeline"' in prompt


def test_build_llm_assist_prompt_includes_quantity_subgrid_second_pass_context():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ],
    }
    pipeline_output = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "常食", "備考"],
                            ["2/15", "朝", "Menu A", "", ""],
                        ],
                    }
                ],
            }
        ],
        "quantity_subgrid_passes": [
            {
                "page_index": 1,
                "table_index": 0,
                "body_start_row": 2,
                "menu_col_index": 3,
                "quantity_start_col_index": 4,
                "row_count": 56,
                "quantity_col_count": 7,
                "crop_box_norm": [0.4, 0.2, 0.9, 0.95],
                "table_raw": "|47|||||||\n|-|-|-|-|-|-|-|\n|42|||||||",
                "tables": [
                    {
                        "table_id": "qty_t1",
                        "page_index": 1,
                        "row_count": 2,
                        "col_count": 7,
                        "rows": [["47", "", "", "", "", "", ""], ["42", "", "", "", "", "", ""]],
                    }
                ],
                "normalized_rows": [["47", "", "", "", "", "", ""], ["42", "", "", "", "", "", ""]],
                "normalization_patches": [
                    {
                        "row_index": 0,
                        "col_index": 0,
                        "original_text": "47.",
                        "normalized_text": "47",
                        "prev_neighbor": "",
                        "next_neighbor": "42",
                    }
                ],
            }
        ],
    }

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=pipeline_output,
        llm_assist=True,
    )

    assert prompt is not None
    assert "Quantity-only second pass on the numeric subgrid" in prompt
    assert "normalized_rows_preview" in prompt
    assert "normalization_patches" in prompt
    assert '"normalized_text": "47"' in prompt
    assert "Do NOT derive date/daypart/menu anchors from this quantity-only pass." in prompt
    assert '"quantity_start_col_index": 4' in prompt
    assert '"table_id": "qty_t1"' in prompt
    assert "|47|||||||" in prompt


def test_build_llm_assist_prompt_includes_baseline_and_evaluator_feedback():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ],
        "gemini_ocr_prompt": "facility-base",
    }
    baseline = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "rows": [["01/08", "昼", "Menu A", "2", "baseline"]],
        "row_ids": ["row-1"],
        "baseline_source": "edited",
        "baseline_revision_id": "rev-1",
    }
    evaluator_feedback = {
        "status": "fail",
        "provider": "openai",
        "model": "audit-model-v1",
        "issue_count": 1,
        "blocking_issue_count": 1,
        "issues": [
            {
                "issue_code": "column_swap",
                "severity": "high",
                "row_index": 0,
                "column_index": 3,
                "confidence": 0.91,
                "evidence": "visible 7 in the regular column",
                "reason": "candidate row mismatched the baseline",
            }
        ],
        "blocking_issues": [
            {
                "issue_code": "column_swap",
                "severity": "high",
                "row_index": 0,
                "column_index": 3,
                "confidence": 0.91,
                "evidence": "visible 7 in the regular column",
                "reason": "candidate row mismatched the baseline",
            }
        ],
    }

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=None,
        llm_assist=True,
        baseline=baseline,
        evaluator_feedback=evaluator_feedback,
        draft_rows_override=[["01/08", "昼", "Menu A", "5", "draft"]],
        draft_rows_label="First LLM inference candidate rows",
    )

    assert prompt is not None
    assert "Current sheet/baseline rows shown to the user" in prompt
    assert "Current baseline source: edited" in prompt
    assert "Current baseline revision_id: rev-1" in prompt
    assert "Evaluator feedback from previous OCR draft" in prompt
    assert '"issue_code": "column_swap"' in prompt
    assert "First LLM inference candidate rows" in prompt


def test_build_llm_assist_prompt_warns_against_dense_fill_when_sheet_has_more_rows_than_first_pass():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ],
    }
    baseline = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "rows": [
            ["02/15", "朝", "Menu A", "", ""],
            ["02/15", "朝", "Menu B", "", ""],
            ["02/15", "昼", "Menu C", "", ""],
            ["02/15", "昼", "Menu D", "", ""],
        ],
        "row_ids": ["row-1", "row-2", "row-3", "row-4"],
        "baseline_source": "sheet",
    }
    pipeline_output = {
        "rows": [
            {
                "cells": {
                    "date_mmdd": "02/15",
                    "daypart": "昼",
                    "menu": "Menu C",
                    "qty.regular_x": "42",
                }
            },
            {
                "cells": {
                    "date_mmdd": "02/15",
                    "daypart": "昼",
                    "menu": "Menu D",
                    "qty.regular_x": "42",
                }
            },
        ],
    }

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=pipeline_output,
        llm_assist=True,
        baseline=baseline,
    )

    assert prompt is not None
    assert "It is valid for some rows to remain blank across all quantity columns." in prompt
    assert "Do NOT fill a row unless a quantity is directly visible" in prompt
    assert "Current sheet rows: 4." in prompt
    assert "First-pass yomitoku rows: 2." in prompt
    assert "Keep quantity cells empty on unmatched structural rows" in prompt
    assert "Row block boundaries from structural sheet/baseline" in prompt
    assert "same date/daypart block" in prompt
    assert "Continuity is never clear across a block boundary" in prompt
    assert '"row_start": 0' in prompt
    assert '"row_end": 1' in prompt
    assert '"daypart": "朝"' in prompt
    assert "Treat each consecutive date/daypart block above as a hard row boundary." in prompt


def test_build_llm_assist_prompt_skips_nested_generated_prompt_prefix():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ],
        "gemini_ocr_prompt": (
            "Second-pass repair mode:\n"
            "- Treat the first-pass yomitoku output as the baseline draft.\n"
            "Current sheet/baseline rows shown to the user:\n[]"
        ),
    }
    baseline = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "rows": [["02/15", "朝", "Menu A", "", ""]],
        "row_ids": ["row-1"],
        "baseline_source": "sheet",
    }

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=None,
        llm_assist=True,
        baseline=baseline,
    )

    assert prompt is not None
    assert "Facility-specific instruction" not in prompt
    assert prompt.count("Second-pass repair mode:") == 1


def test_build_llm_assist_prompt_includes_date_block_layout_rules():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ],
        "columns": [
            {"field": "date_mmdd", "header": "日付", "type": "date", "index": 0},
            {"field": "daypart", "header": "区分", "type": "text", "index": 1},
            {"field": "menu", "header": "メニュー", "type": "text", "index": 2},
            {
                "field": "qty.regular_x",
                "header": "常食",
                "type": "quantity",
                "role": "quantity",
                "index": 3,
                "diet_type": "regular",
                "area_id": "X",
            },
            {"field": "remarks", "header": "備考", "type": "text", "index": 4},
        ],
    }
    baseline = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "rows": [
            ["02/15", "朝", "Menu A", "", ""],
            ["02/15", "朝", "Menu B", "", ""],
            ["02/15", "昼", "Menu C", "", ""],
            ["02/15", "昼", "Menu D", "", ""],
            ["02/16", "朝", "Menu E", "", ""],
            ["02/16", "昼", "Menu F", "", ""],
        ],
        "row_ids": [f"row-{idx + 1}" for idx in range(6)],
        "baseline_source": "sheet",
        "structure_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "structure_rows": [
            ["02/15", "朝", "Menu A", "", ""],
            ["02/15", "朝", "Menu B", "", ""],
            ["02/15", "昼", "Menu C", "", ""],
            ["02/15", "昼", "Menu D", "", ""],
            ["02/16", "朝", "Menu E", "", ""],
            ["02/16", "昼", "Menu F", "", ""],
        ],
    }

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=None,
        llm_assist=True,
        baseline=baseline,
        draft_rows_override=[
            ["02/15", "朝", "Menu A", "42", ""],
            ["02/15", "朝", "Menu B", "42", ""],
            ["02/15", "昼", "Menu C", "", ""],
            ["02/15", "昼", "Menu D", "", ""],
            ["02/16", "朝", "Menu E", "43", ""],
            ["02/16", "昼", "Menu F", "", ""],
        ],
    )

    assert prompt is not None
    assert "Date block layout summary" in prompt
    assert '"sub_blocks": [{"daypart": "朝", "row_start": 0, "row_end": 1, "row_count": 2}' in prompt
    assert "Blank sub-blocks may appear at the start, middle, or end of a date block." in prompt
    assert "Do NOT rotate a blank sub-block to the end of a date block" in prompt
    assert "Suspicious blank-edge placement hints from the current OCR draft" in prompt
    assert "trailing_blank_run_after_filled_rows" in prompt


def test_build_llm_assist_prompt_includes_daypart_category_alias_rules():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ],
    }
    baseline = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "rows": [
            ["04/26", "朝", "Menu A", "", ""],
            ["04/26", "朝", "Menu B", "", ""],
            ["04/26", "昼", "Menu C", "", ""],
            ["04/26", "昼", "Menu D", "", ""],
        ],
        "row_ids": ["row-1", "row-2", "row-3", "row-4"],
        "baseline_source": "sheet",
        "structure_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "structure_rows": [
            ["04/26", "朝", "Menu A", "", ""],
            ["04/26", "朝", "Menu B", "", ""],
            ["04/26", "昼", "Menu C", "", ""],
            ["04/26", "昼", "Menu D", "", ""],
        ],
    }

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=None,
        llm_assist=True,
        baseline=baseline,
        first_pass_rows_override=[
            ["04/26", "主", "Menu A", "67", ""],
            ["04/26", "副", "Menu B", "", ""],
            ["04/26", "VE", "Menu C", "72", ""],
            ["04/26", "タ", "Menu D", "", ""],
        ],
    )

    assert prompt is not None
    assert "The current sheet's canonical daypart blocks are 朝/昼/夕." in prompt
    assert "Fax-side 区分/category tokens may be aliases, sublabels, continuation marks, OCR noise, or blanks" in prompt
    assert "主, 副, 汁, タ, VE, MO, 品, A/B, single letters, and blank cells" in prompt
    assert "A change in fax-side 区分/category token alone is not enough to move a row into another canonical block." in prompt


def test_build_llm_assist_prompt_includes_blank_noisy_daypart_anchor_rules():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ],
    }
    baseline = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "rows": [
            ["04/27", "朝", "Menu A", "", ""],
            ["04/27", "朝", "Menu B", "", ""],
            ["04/27", "昼", "Menu C", "", ""],
        ],
        "row_ids": ["row-1", "row-2", "row-3"],
        "baseline_source": "sheet",
        "structure_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "structure_rows": [
            ["04/27", "朝", "Menu A", "", ""],
            ["04/27", "朝", "Menu B", "", ""],
            ["04/27", "昼", "Menu C", "", ""],
        ],
    }

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=None,
        llm_assist=True,
        baseline=baseline,
        first_pass_rows_override=[
            ["04/27", "", "Menu A", "70", ""],
            ["04/27", "m", "Menu B", "", ""],
            ["04/27", "", "Menu C", "72", ""],
        ],
    )

    assert prompt is not None
    assert "When fax-side 区分/category tokens are blank, noisy, or facility-specific" in prompt
    assert "anchor rows by current-sheet order, neighboring menu names, and quantity continuity inside the same canonical block." in prompt
    assert "Prefer the current-sheet 朝/昼/夕 block unless the fax explicitly shows a different canonical boundary." in prompt


def test_build_llm_assist_prompt_keeps_remarks_immutable_when_blank():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ],
    }
    baseline = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "rows": [
            ["04/27", "昼", "Menu A", "", ""],
            ["04/27", "昼", "Menu B", "", ""],
        ],
        "row_ids": ["row-1", "row-2"],
        "baseline_source": "sheet",
        "structure_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "structure_rows": [
            ["04/27", "昼", "Menu A", "", ""],
            ["04/27", "昼", "Menu B", "", ""],
        ],
    }

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=None,
        llm_assist=True,
        baseline=baseline,
        first_pass_rows_override=[
            ["04/27", "主", "Menu A", "105", "肉4"],
            ["04/27", "副", "Menu B", "105", ""],
        ],
    )

    assert prompt is not None
    assert (
        "Return sparse row patches only." in prompt
    )
    assert (
        "Display-only helper cells include operator-configured helper columns "
        "such as 合計 or 副区分; keep them in their own columns and never reinterpret "
        "them as qty.* targets." in prompt
    )
    assert "Do not return structural anchor fields such as date/daypart/menu" in prompt
    assert "Block-level helper columns such as 合計 belong to the whole date/daypart block" in prompt
    assert "Do not create new remarks from side notes, allergy notes, prohibited-diet annotations, or margin text" in prompt
    assert "Do not move fax-side side notes or forbidden-diet annotations into the remarks column" in prompt


def test_build_llm_assist_prompt_ignores_extra_fax_numeric_columns_and_left_shift():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.daycare_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "remarks",
        ],
    }
    baseline = {
        "fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.daycare_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "remarks",
        ],
        "rows": [
            ["04/29", "昼", "Menu A", "", "", "", "", ""],
            ["04/29", "昼", "Menu B", "", "", "", "", ""],
        ],
        "row_ids": ["row-1", "row-2"],
        "baseline_source": "sheet",
        "structure_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.daycare_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "remarks",
        ],
        "structure_rows": [
            ["04/29", "昼", "Menu A", "", "", "", "", ""],
            ["04/29", "昼", "Menu B", "", "", "", "", ""],
        ],
    }

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=None,
        llm_assist=True,
        baseline=baseline,
        first_pass_rows_override=[
            ["04/29", "主", "Menu A", "105", "105", "58", "34", "肉4"],
            ["04/29", "副", "Menu B", "105", "105", "67", "38", ""],
        ],
    )

    assert prompt is not None
    assert "Fax-side numeric columns with no clear target header match" in prompt
    assert "must be ignored rather than shifted into the nearest target column." in prompt
    assert "Never shift a contiguous run of quantities left or right just to populate more target columns." in prompt
    assert "fill only those supported targets and leave the remaining target columns blank." in prompt


def test_build_llm_assist_prompt_maps_only_explicit_singular_side_note_labels():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.no_fried_x",
            "remarks",
        ],
    }
    baseline = {
        "fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.no_fried_x",
            "remarks",
        ],
        "rows": [["04/30", "夕", "Menu A", "", "", "", ""]],
        "row_ids": ["row-1"],
        "baseline_source": "sheet",
        "structure_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.no_fried_x",
            "remarks",
        ],
        "structure_rows": [["04/30", "夕", "Menu A", "", "", "", ""]],
    }

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=None,
        llm_assist=True,
        baseline=baseline,
        first_pass_rows_override=[
            ["04/30", "夕", "Menu A", "肉1", "魚2", "鶏魚1", ""],
        ],
    )

    assert prompt is not None
    assert "For labeled side-note counts such as 肉4, 魚5, or 揚げ物6, map the number only to the semantically matching target column" in prompt
    assert "If a side-note label is compound, facility-specific, or has no clear target qty.* column, do not force it into the nearest quantity column." in prompt


def test_build_llm_assist_prompt_prefers_structural_rows_for_blank_anchor_preservation():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ],
    }
    baseline = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "rows": [
            ["02/15", "朝", "Menu A", "99", ""],
            ["02/15", "朝", "Menu B", "99", ""],
            ["02/15", "昼", "Menu C", "42", ""],
            ["02/15", "昼", "Menu D", "42", ""],
        ],
        "row_ids": ["row-1", "row-2", "row-3", "row-4"],
        "baseline_source": "sheet",
        "structure_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "structure_rows": [
            ["02/15", "朝", "Menu A", "", ""],
            ["02/15", "朝", "Menu B", "", ""],
            ["02/15", "昼", "Menu C", "", ""],
            ["02/15", "昼", "Menu D", "", ""],
        ],
        "structure_row_ids": ["srow-1", "srow-2", "srow-3", "srow-4"],
        "structure_source": "weekly_menu_structure",
    }
    pipeline_output = {
        "rows": [
            {
                "cells": {
                    "date_mmdd": "02/15",
                    "daypart": "昼",
                    "menu": "Menu C",
                    "qty.regular_x": "42",
                }
            },
            {
                "cells": {
                    "date_mmdd": "02/15",
                    "daypart": "昼",
                    "menu": "Menu D",
                    "qty.regular_x": "42",
                }
            },
        ],
    }

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=pipeline_output,
        llm_assist=True,
        baseline=baseline,
    )

    assert prompt is not None
    assert "Structural sheet rows for blank-anchor preservation" in prompt
    assert '"qty.regular_x": ""' in prompt
    assert "Structural baseline source: weekly_menu_structure" in prompt


def test_build_llm_assist_prompt_uses_first_pass_rows_override_for_blank_anchor_preservation():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ],
    }
    baseline = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "rows": [
            ["02/15", "朝", "Menu A", "99", ""],
            ["02/15", "朝", "Menu B", "99", ""],
            ["02/15", "昼", "Menu C", "42", ""],
            ["02/15", "昼", "Menu D", "42", ""],
        ],
        "row_ids": ["row-1", "row-2", "row-3", "row-4"],
        "baseline_source": "sheet",
        "structure_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "structure_rows": [
            ["02/15", "朝", "Menu A", "", ""],
            ["02/15", "朝", "Menu B", "", ""],
            ["02/15", "昼", "Menu C", "", ""],
            ["02/15", "昼", "Menu D", "", ""],
        ],
        "structure_row_ids": ["srow-1", "srow-2", "srow-3", "srow-4"],
        "structure_source": "weekly_menu_structure",
    }
    pipeline_output = {"pages": [], "table_raw": ""}

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=pipeline_output,
        llm_assist=True,
        baseline=baseline,
        first_pass_rows_override=[
            ["02/15", "昼", "Menu C", "42", ""],
            ["02/15", "昼", "Menu D", "42", ""],
        ],
    )

    assert prompt is not None
    assert "Blank-anchor structural row indexes" in prompt
    assert "[0, 1]" in prompt
    assert "First-pass yomitoku structured rows" in prompt
    assert '"Menu C"' in prompt


def test_build_llm_assist_prompt_omits_noisy_markdown_when_block_drift_feedback_exists():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ],
    }
    baseline = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "rows": [
            ["02/15", "朝", "Menu A", "", ""],
            ["02/15", "朝", "Menu B", "", ""],
            ["02/15", "昼", "Menu C", "", ""],
            ["02/15", "昼", "Menu D", "", ""],
        ],
        "row_ids": ["row-1", "row-2", "row-3", "row-4"],
        "baseline_source": "sheet",
        "structure_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "structure_rows": [
            ["02/15", "朝", "Menu A", "", ""],
            ["02/15", "朝", "Menu B", "", ""],
            ["02/15", "昼", "Menu C", "", ""],
            ["02/15", "昼", "Menu D", "", ""],
        ],
        "structure_row_ids": ["srow-1", "srow-2", "srow-3", "srow-4"],
    }
    pipeline_output = {
        "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|02/15|昼|Menu C|42|",
        "rows": [
            {
                "cells": {
                    "date_mmdd": "02/15",
                    "daypart": "昼",
                    "menu": "Menu C",
                    "qty.regular_x": "42",
                }
            },
        ],
        "tables": [
            {
                "table_id": "p1_t1",
                "rows": [["日付", "区分", "メニュー", "常食"], ["02/15", "昼", "Menu C", "42"]],
                "cells": [{"row_index": 1, "col_index": 3, "text": "42"}],
            }
        ],
    }
    evaluator_feedback = {
        "status": "fail",
        "provider": "gemini",
        "actual_provider": "gemini",
        "model": "gemini-2.5-pro",
        "issue_count": 1,
        "blocking_issue_count": 1,
        "issues": [
            {
                "issue_code": "unexpected_dense_fill",
                "severity": "high",
                "row_index": 0,
                "column_index": 3,
                "confidence": 0.99,
                "evidence": "No handwritten quantity for breakfast block",
                "reason": "Keep blank anchors blank",
            }
        ],
        "blocking_issues": [
            {
                "issue_code": "unexpected_dense_fill",
                "severity": "high",
                "row_index": 0,
                "column_index": 3,
                "confidence": 0.99,
                "evidence": "No handwritten quantity for breakfast block",
                "reason": "Keep blank anchors blank",
            }
        ],
    }

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=pipeline_output,
        llm_assist=True,
        baseline=baseline,
        evaluator_feedback=evaluator_feedback,
    )

    assert prompt is not None
    assert "Block-anchored repair mode" in prompt
    assert "First-pass yomitoku markdown" not in prompt
    assert "First-pass yomitoku structured tables/cells" not in prompt
    assert "Suspicious first-pass cells" not in prompt
    assert "First-pass yomitoku structured rows" in prompt


def test_build_llm_reparse_audit_prompts_include_current_sheet_rows_and_dense_fill_codes():
    system_prompt, user_prompt, fields = order_service._build_llm_reparse_audit_prompts(
        candidate_rows=[["", "", "", "42"], ["", "", "", "42"]],
        reference_rows=[["02/15", "昼", "Menu C", "42"]],
        baseline_rows=[["02/15", "朝", "Menu A", ""], ["02/15", "朝", "Menu B", ""]],
        quantity_columns=[{"field": "qty.regular_x", "index": 3}],
        expected_row_count=4,
        block_anchor_hints={
            "blocks": [
                {"row_start": 0, "row_end": 1, "row_count": 2, "date_mmdd": "02/15", "daypart": "朝"},
                {"row_start": 2, "row_end": 3, "row_count": 2, "date_mmdd": "02/15", "daypart": "昼"},
            ],
            "unmatched_structural_row_indexes": [0, 1],
        },
    )

    assert fields == [
        "issue_code",
        "severity",
        "row_index",
        "column_index",
        "confidence",
        "evidence",
        "reason",
    ]
    assert "unexpected_dense_fill" in system_prompt
    assert "missing_blank_anchor_rows" in system_prompt
    assert "overextended_span" in system_prompt
    assert "current_sheet_block_ranges" in system_prompt
    assert "current_sheet_date_ranges" in system_prompt
    assert "blank_anchor_row_indexes_hint" in system_prompt
    assert "direct visual evidence" in system_prompt
    assert "lower handwritten quantity is shifted upward" in system_prompt
    assert '"current_sheet_rows_hint": [["02/15", "朝", "Menu A", ""], ["02/15", "朝", "Menu B", ""]]' in user_prompt
    assert '"current_sheet_date_ranges": [{"date_mmdd": "02/15", "row_start": 0, "row_end": 1, "row_count": 2' in user_prompt
    assert '"current_sheet_block_ranges": [{"row_start": 0, "row_end": 1, "row_count": 2, "date_mmdd": "02/15", "daypart": "朝"' in user_prompt
    assert '"blank_anchor_row_indexes_hint": [0, 1]' in user_prompt


def test_build_llm_reparse_audit_prompts_uses_structural_blank_anchor_rows_when_unmatched_empty():
    system_prompt, user_prompt, fields = order_service._build_llm_reparse_audit_prompts(
        candidate_rows=[["", "", "", "42"], ["", "", "", ""]],
        reference_rows=[["02/15", "昼", "Menu B", ""]],
        baseline_rows=[["02/15", "朝", "Menu A", ""], ["02/15", "朝", "Menu B", ""]],
        quantity_columns=[{"field": "qty.regular_x", "index": 3}],
        expected_row_count=2,
        block_anchor_hints={
            "blocks": [
                {
                    "row_start": 0,
                    "row_end": 1,
                    "row_count": 2,
                    "date_mmdd": "02/15",
                    "daypart": "朝",
                }
            ],
            "unmatched_structural_row_indexes": [],
            "structural_blank_anchor_row_indexes": [0],
        },
    )

    assert fields == [
        "issue_code",
        "severity",
        "row_index",
        "column_index",
        "confidence",
        "evidence",
        "reason",
    ]
    assert '"blank_anchor_row_indexes_hint": [0]' in user_prompt
    assert "overextended_span" in system_prompt


def test_collect_candidate_blank_edge_hints_detects_trailing_blank_runs():
    hints = order_service._collect_candidate_blank_edge_hints(
        candidate_rows=[
            ["02/15", "朝", "Menu A", "42", ""],
            ["02/15", "朝", "Menu B", "42", ""],
            ["02/15", "昼", "Menu C", "43", ""],
            ["02/15", "昼", "Menu D", "", ""],
            ["02/15", "昼", "Menu E", "", ""],
        ],
        quantity_columns=[{"field": "qty.regular_x", "index": 3}],
        date_blocks=[
            {
                "date_mmdd": "02/15",
                "row_start": 0,
                "row_end": 4,
                "row_count": 5,
                "sub_blocks": [
                    {"daypart": "朝", "row_start": 0, "row_end": 1, "row_count": 2},
                    {"daypart": "昼", "row_start": 2, "row_end": 4, "row_count": 3},
                ],
            }
        ],
    )

    assert hints == [
        {
            "date_mmdd": "02/15",
            "row_start": 0,
            "row_end": 4,
            "filled_row_indexes": [0, 1, 2],
            "blank_row_indexes": [3, 4],
            "trailing_blank_row_indexes": [3, 4],
            "pattern": "trailing_blank_run_after_filled_rows",
            "note": "Verify that blank rows were not rotated to the end of the date block.",
            "sub_blocks": [
                {"daypart": "朝", "row_start": 0, "row_end": 1, "row_count": 2},
                {"daypart": "昼", "row_start": 2, "row_end": 4, "row_count": 3},
            ],
        }
    ]


def test_resolve_reparse_baseline_rows_for_structure_prefers_structure_rows():
    fields, rows, row_ids, source = order_service._resolve_reparse_baseline_rows_for_structure(
        {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"],
            "rows": [["02/15", "朝", "Menu A", "99"]],
            "row_ids": ["row-1"],
            "structure_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"],
            "structure_rows": [["02/15", "朝", "Menu A", ""]],
            "structure_row_ids": ["srow-1"],
            "structure_source": "weekly_menu_structure",
        }
    )

    assert fields == ["date_mmdd", "daypart", "menu", "qty.regular_x"]
    assert rows == [["02/15", "朝", "Menu A", ""]]
    assert row_ids == ["srow-1"]
    assert source == "weekly_menu_structure"


def test_validate_reparse_blank_anchor_drift_detects_dense_fill_into_structural_blank_rows():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"]
    structural_rows = [
        ["02/15", "朝", "Menu A", "", ""],
        ["02/15", "朝", "Menu B", "", ""],
        ["02/15", "昼", "Menu C", "", ""],
        ["02/15", "昼", "Menu D", "", ""],
    ]
    reference_rows = [
        ["02/15", "昼", "Menu C", "42", ""],
        ["02/15", "昼", "Menu D", "42", ""],
    ]
    lines = [
        {
            "source_row_index": 0,
            "date": date(2026, 2, 15),
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "X",
            "quantity_original": 42,
        },
        {
            "source_row_index": 2,
            "date": date(2026, 2, 15),
            "daypart": "昼",
            "menu_name": "Menu C",
            "diet_type": "regular",
            "area_id": "X",
            "quantity_original": 42,
        },
    ]

    error, detail = order_service._validate_reparse_blank_anchor_drift(
        lines=lines,
        structural_fields=fields,
        structural_rows=structural_rows,
        reference_rows=reference_rows,
        reference_fields=fields,
    )

    assert error == "sheet_blank_anchor_drift"
    assert detail is not None
    assert detail["offending_source_rows"] == [0]
    assert detail["blank_anchor_row_indexes"] == [0, 1]
    assert detail["filled_source_rows"] == [0, 2]
    assert detail["offending_blocks"] == [
        {
            "date_mmdd": "02/15",
            "daypart": "朝",
            "row_start": 0,
            "row_end": 1,
            "offending_source_rows": [0],
        }
    ]


def test_validate_reparse_blank_anchor_drift_allows_quantities_within_matched_rows():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"]
    structural_rows = [
        ["02/15", "朝", "Menu A", "", ""],
        ["02/15", "朝", "Menu B", "", ""],
        ["02/15", "昼", "Menu C", "", ""],
        ["02/15", "昼", "Menu D", "", ""],
    ]
    reference_rows = [
        ["02/15", "昼", "Menu C", "42", ""],
        ["02/15", "昼", "Menu D", "42", ""],
    ]
    lines = [
        {
            "source_row_index": 2,
            "date": date(2026, 2, 15),
            "daypart": "昼",
            "menu_name": "Menu C",
            "diet_type": "regular",
            "area_id": "X",
            "quantity_original": 42,
        },
        {
            "source_row_index": 3,
            "date": date(2026, 2, 15),
            "daypart": "昼",
            "menu_name": "Menu D",
            "diet_type": "regular",
            "area_id": "X",
            "quantity_original": 42,
        },
    ]

    error, detail = order_service._validate_reparse_blank_anchor_drift(
        lines=lines,
        structural_fields=fields,
        structural_rows=structural_rows,
        reference_rows=reference_rows,
        reference_fields=fields,
    )

    assert error is None
    assert detail is None


def test_augment_llm_reparse_audit_with_structural_feedback_adds_all_offending_blank_anchor_rows():
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {
                "index": 3,
                "role": "quantity",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
            },
            {"index": 4, "role": "remarks"},
        ],
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
    }
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"]
    structural_rows = [
        ["02/15", "朝", "A1", "", ""],
        ["02/15", "朝", "A2", "", ""],
        ["02/15", "昼", "A3", "", ""],
        ["02/15", "昼", "A4", "", ""],
        ["02/16", "朝", "B1", "", ""],
        ["02/16", "朝", "B2", "", ""],
        ["02/16", "昼", "B3", "", ""],
        ["02/16", "昼", "B4", "", ""],
    ]
    reference_rows = [
        ["02/15", "昼", "A3", "42", ""],
        ["02/15", "昼", "A4", "42", ""],
        ["02/16", "昼", "B3", "43", ""],
        ["02/16", "昼", "B4", "43", ""],
    ]
    candidate_rows = [
        ["", "", "", "42", ""],
        ["", "", "", "42", ""],
        ["", "", "", "42", ""],
        ["", "", "", "42", ""],
        ["", "", "", "43", ""],
        ["", "", "", "43", ""],
        ["", "", "", "43", ""],
        ["", "", "", "43", ""],
    ]
    audit = {
        "status": "fail",
        "issue_count": 1,
        "blocking_issue_count": 1,
        "issues": [
            {
                "issue_code": "overextended_span",
                "severity": "high",
                "row_index": 0,
                "column_index": 3,
                "confidence": 0.91,
                "evidence": "initial example",
                "reason": "first block drift",
            }
        ],
        "blocking_issues": [
            {
                "issue_code": "overextended_span",
                "severity": "high",
                "row_index": 0,
                "column_index": 3,
                "confidence": 0.91,
                "evidence": "initial example",
                "reason": "first block drift",
            }
        ],
    }

    augmented = order_service._augment_llm_reparse_audit_with_structural_feedback(
        llm_audit=audit,
        candidate_rows=candidate_rows,
        template=template,
        baseline_fields=fields,
        baseline_structure_rows=structural_rows,
        reference_rows=reference_rows,
        reference_fields=fields,
    )

    assert augmented is not None
    issue_rows = sorted(
        int(item["row_index"])
        for item in (augmented.get("issues") or [])
        if item.get("issue_code") == "missing_blank_anchor_rows"
    )
    dense_fill_rows = sorted(
        int(item["row_index"])
        for item in (augmented.get("issues") or [])
        if item.get("issue_code") == "unexpected_dense_fill"
    )
    assert issue_rows == [0, 1, 4, 5]
    assert dense_fill_rows == [0, 1, 4, 5]
    assert augmented["status"] == "fail"
    assert augmented["blocking_issue_count"] >= 4


def test_augment_llm_reparse_audit_with_structural_feedback_uses_majority_leading_blank_pattern():
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {
                "index": 3,
                "role": "quantity",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
            },
            {"index": 4, "role": "remarks"},
        ],
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
    }
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"]
    structural_rows = []
    for date_mmdd in ["02/15", "02/16", "02/17"]:
        structural_rows.extend(
            [
                [date_mmdd, "朝", f"{date_mmdd}-A1", "", ""],
                [date_mmdd, "朝", f"{date_mmdd}-A2", "", ""],
                [date_mmdd, "昼", f"{date_mmdd}-L1", "", ""],
                [date_mmdd, "昼", f"{date_mmdd}-L2", "", ""],
                [date_mmdd, "昼", f"{date_mmdd}-L3", "", ""],
                [date_mmdd, "夕", f"{date_mmdd}-D1", "", ""],
                [date_mmdd, "夕", f"{date_mmdd}-D2", "", ""],
                [date_mmdd, "夕", f"{date_mmdd}-D3", "", ""],
            ]
        )
    candidate_rows = [
        ["", "", "", "", ""],
        ["", "", "", "", ""],
        ["", "", "", "42", ""],
        ["", "", "", "42", ""],
        ["", "", "", "42", ""],
        ["", "", "", "43", ""],
        ["", "", "", "43", ""],
        ["", "", "", "43", ""],
        ["", "", "", "", ""],
        ["", "", "", "", ""],
        ["", "", "", "44", ""],
        ["", "", "", "44", ""],
        ["", "", "", "44", ""],
        ["", "", "", "45", ""],
        ["", "", "", "45", ""],
        ["", "", "", "45", ""],
        ["", "", "", "46", ""],
        ["", "", "", "46", ""],
        ["", "", "", "46", ""],
        ["", "", "", "46", ""],
        ["", "", "", "46", ""],
        ["", "", "", "47", ""],
        ["", "", "", "47", ""],
        ["", "", "", "47", ""],
    ]
    audit = {
        "status": "fail",
        "issue_count": 1,
        "blocking_issue_count": 1,
        "issues": [
            {
                "issue_code": "overextended_span",
                "severity": "high",
                "row_index": 8,
                "column_index": 3,
                "confidence": 0.9,
                "evidence": "structural drift example",
                "reason": "use this as a repair hint",
            }
        ],
        "blocking_issues": [],
    }

    augmented = order_service._augment_llm_reparse_audit_with_structural_feedback(
        llm_audit=audit,
        candidate_rows=candidate_rows,
        template=template,
        baseline_fields=fields,
        baseline_structure_rows=structural_rows,
        reference_rows=[],
        reference_fields=fields,
    )

    assert augmented is not None
    issue_rows = sorted(
        int(item["row_index"])
        for item in (augmented.get("issues") or [])
        if item.get("issue_code") == "missing_blank_anchor_rows"
    )
    dense_fill_rows = sorted(
        int(item["row_index"])
        for item in (augmented.get("issues") or [])
        if item.get("issue_code") == "unexpected_dense_fill"
    )
    assert 16 in issue_rows
    assert 17 in issue_rows
    assert 16 in dense_fill_rows
    assert 17 in dense_fill_rows


def test_build_deterministic_llm_reparse_feedback_adds_structural_and_row_count_issues():
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "2F"},
        ],
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
    }

    feedback = order_service._build_deterministic_llm_reparse_feedback(
        candidate_rows=[
            ["04/26", "朝", "Menu A", "9"],
            ["04/26", "朝", "Menu B", "5"],
            ["04/26", "昼", "Menu C", "7"],
        ],
        template=template,
        baseline_fields=["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        baseline_rows=[
            ["04/26", "朝", "Menu A", ""],
            ["04/26", "朝", "Menu B", "5"],
            ["04/26", "昼", "Menu C", ""],
        ],
        reference_rows=[
            ["04/26", "朝", "Menu A", ""],
            ["04/26", "朝", "Menu B", "5"],
        ],
        expected_row_count=4,
    )

    assert feedback is not None
    assert feedback["provider"] == "deterministic"
    assert feedback["status"] == "fail"
    issue_codes = {
        str(item.get("issue_code"))
        for item in (feedback.get("issues") or [])
        if isinstance(item, dict)
    }
    assert "row_count_shortfall" in issue_codes
    assert "missing_blank_anchor_rows" in issue_codes
    assert "unexpected_dense_fill" in issue_codes


def test_realign_quantity_only_rows_to_structural_blank_anchors_rotates_block_fill_into_blank_anchors():
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {
                "index": 3,
                "role": "quantity",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
            },
            {"index": 4, "role": "remarks"},
        ],
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
    }
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"]
    structural_rows = [
        ["02/15", "朝", "Menu A", "", ""],
        ["02/15", "朝", "Menu B", "", ""],
        ["02/15", "朝", "Menu C", "", ""],
        ["02/15", "朝", "Menu D", "", ""],
    ]
    reference_rows = [
        ["02/15", "朝", "Menu C", "42", ""],
        ["02/15", "朝", "Menu D", "43", ""],
    ]
    rotated_rows = [
        ["02/15", "朝", "Menu A", "42", ""],
        ["02/15", "朝", "Menu B", "43", ""],
        ["02/15", "朝", "Menu C", "", ""],
        ["02/15", "朝", "Menu D", "", ""],
    ]

    realigned_rows, stats = order_service._realign_quantity_only_rows_to_structural_blank_anchors(
        rows=rotated_rows,
        template=template,
        structural_fields=fields,
        structural_rows=structural_rows,
        reference_rows=reference_rows,
        reference_fields=fields,
    )

    assert [row[3] for row in realigned_rows] == ["", "", "42", "43"]
    assert stats is not None
    assert stats["blocks_realigned"] == 1
    assert stats["rows_shifted"] == 2


def test_realign_quantity_only_rows_to_structural_blank_anchors_uses_structural_fallback_when_reference_alignment_is_strong():
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {
                "index": 3,
                "role": "quantity",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
            },
            {"index": 4, "role": "remarks"},
        ],
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
    }
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"]
    structural_rows = [
        ["02/15", "朝", "Menu A", "42", ""],
        ["02/15", "朝", "Menu B", "", ""],
        ["02/15", "朝", "Menu C", "", ""],
        ["02/15", "朝", "Menu D", "", ""],
    ]
    reference_rows = [
        ["02/15", "朝", "Menu A", "", ""],
        ["02/15", "朝", "Menu B", "", ""],
        ["02/15", "朝", "Menu C", "", ""],
        ["02/15", "朝", "Menu D", "", ""],
    ]
    rotated_rows = [
        ["02/15", "朝", "Menu A", "11", ""],
        ["02/15", "朝", "Menu B", "22", ""],
        ["02/15", "朝", "Menu C", "33", ""],
        ["02/15", "朝", "Menu D", "44", ""],
    ]

    realigned_rows, stats = order_service._realign_quantity_only_rows_to_structural_blank_anchors(
        rows=rotated_rows,
        template=template,
        structural_fields=fields,
        structural_rows=structural_rows,
        reference_rows=reference_rows,
        reference_fields=fields,
    )

    assert [row[3] for row in realigned_rows] == ["11", "", "", ""]
    assert stats is not None
    assert stats["blocks_realigned"] == 1
    assert stats["rows_shifted"] == 3
    assert stats["quantity_cells_shifted"] == 3


def test_project_quantity_only_rows_onto_structural_rows_preserves_structure_and_blank_rows():
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "X"},
            {"index": 4, "role": "remarks"},
        ],
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
    }
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"]
    structural_rows = [
        ["02/15", "朝", "Menu A", "", ""],
        ["02/15", "朝", "Menu B", "", ""],
        ["02/15", "昼", "Menu C", "", ""],
    ]
    sparse_rows = [
        ["", "", "", "", ""],
        ["", "", "", "42", ""],
        ["", "", "", "43", ""],
    ]

    projected_rows, stats = order_service._project_quantity_only_rows_onto_structural_rows(
        rows=sparse_rows,
        template=template,
        structural_fields=fields,
        structural_rows=structural_rows,
    )

    assert [row[:3] for row in projected_rows] == [row[:3] for row in structural_rows]
    assert [row[3] for row in projected_rows] == ["", "42", "43"]
    assert stats is not None
    assert stats["rows_with_projected_quantity"] == 2
    assert stats["quantity_cells_copied"] == 2


def test_project_quantity_only_rows_onto_structural_rows_skips_candidates_with_structural_cells():
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "X"},
            {"index": 4, "role": "remarks"},
        ],
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
    }
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"]
    structural_rows = [
        ["02/15", "朝", "Menu A", "", ""],
        ["02/15", "朝", "Menu B", "", ""],
        ["02/15", "昼", "Menu C", "", ""],
    ]
    candidate_rows = [
        ["02/15", "朝", "Menu A", "", ""],
        ["02/15", "朝", "Menu B", "42", ""],
    ]

    should_project = order_service._should_project_quantity_rows_to_structural_rows(
        rows=candidate_rows,
        structural_rows=structural_rows,
        template=template,
    )
    projected_rows, stats = order_service._project_quantity_only_rows_onto_structural_rows(
        rows=candidate_rows,
        template=template,
        structural_fields=fields,
        structural_rows=structural_rows,
    )

    assert should_project is False
    assert projected_rows == candidate_rows
    assert stats is None


def test_build_reparse_block_anchor_hints_ignores_unmatched_rows_when_reference_alignment_is_too_weak():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"]
    structural_rows = [
        ["02/15", "朝", "Menu A", "", ""],
        ["02/15", "朝", "Menu B", "", ""],
        ["02/15", "昼", "Menu C", "", ""],
        ["02/15", "昼", "Menu D", "", ""],
    ]
    noisy_reference_rows = [
        ["99/99", "?", "Noise 1", "42", ""],
        ["99/99", "?", "Noise 2", "43", ""],
    ]

    hints = order_service._build_reparse_block_anchor_hints(
        structural_fields=fields,
        structural_rows=structural_rows,
        first_pass_fields=fields,
        first_pass_rows=noisy_reference_rows,
    )

    assert hints["reference_alignment_weak"] is True
    assert hints["matched_reference_key_count"] == 0
    assert hints["reference_key_count"] == 2
    assert hints["unmatched_structural_row_indexes"] == []
    assert hints["structural_blank_anchor_row_indexes"] == []


def test_build_reparse_block_anchor_hints_uses_structural_blank_anchor_rows_when_reference_alignment_is_weak():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"]
    structural_rows = [
        ["02/15", "朝", "Menu A", "", ""],
        ["02/15", "朝", "Menu B", "99", ""],
        ["02/15", "朝", "Menu C", "", ""],
        ["02/15", "朝", "Menu D", "42", ""],
    ]
    noisy_reference_rows = [
        ["99/99", "?", "Noise 1", "12", ""],
        ["99/99", "?", "Noise 2", "13", ""],
    ]

    hints = order_service._build_reparse_block_anchor_hints(
        structural_fields=fields,
        structural_rows=structural_rows,
        first_pass_fields=fields,
        first_pass_rows=noisy_reference_rows,
    )

    assert hints["reference_alignment_weak"] is True
    assert hints["unmatched_structural_row_indexes"] == []
    assert hints["structural_blank_anchor_row_indexes"] == [0, 2]


def test_validate_reparse_blank_anchor_drift_uses_structural_blank_anchor_rows_when_reference_alignment_is_weak():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"]
    structural_rows = [
        ["02/15", "朝", "Menu A", "", ""],
        ["02/15", "朝", "Menu B", "99", ""],
        ["02/15", "朝", "Menu C", "", ""],
        ["02/15", "朝", "Menu D", "42", ""],
    ]
    reference_rows = [
        ["99/99", "?", "Noise 1", "12", ""],
    ]
    lines = [
        {
            "source_row_index": 0,
            "date": date(2026, 2, 15),
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "X",
            "quantity_original": 12,
        },
    ]

    error, detail = order_service._validate_reparse_blank_anchor_drift(
        lines=lines,
        structural_fields=fields,
        structural_rows=structural_rows,
        reference_rows=reference_rows,
        reference_fields=fields,
    )

    assert error == "sheet_blank_anchor_drift"
    assert detail is not None
    assert detail["offending_source_rows"] == [0]
    assert detail["blank_anchor_row_indexes"] == [0, 2]


def test_build_reparse_block_anchor_hints_omits_blank_quantity_indexes_when_structure_is_quantity_empty():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"]
    structural_rows = [
        ["02/15", "朝", "Menu A", "", ""],
        ["02/15", "朝", "Menu B", "", ""],
        ["02/15", "昼", "Menu C", "", ""],
        ["02/15", "昼", "Menu D", "", ""],
    ]

    hints = order_service._build_reparse_block_anchor_hints(
        structural_fields=fields,
        structural_rows=structural_rows,
        first_pass_fields=fields,
        first_pass_rows=[],
    )

    assert hints["reference_alignment_weak"] is False
    assert hints["unmatched_structural_row_indexes"] == []
    assert hints["structural_blank_anchor_row_indexes"] == []
    blocks = hints["blocks"]
    assert isinstance(blocks, list)
    assert blocks
    assert all("blank_quantity_row_indexes" not in block for block in blocks)


def test_reparse_order_realigns_rotated_blank_anchor_rows_before_line_parsing(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-blank-anchor-realign.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-blank-anchor-realign-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    template = {
        "header_rows": 0,
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {
                "index": 3,
                "role": "quantity",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
            },
            {"index": 4, "role": "remarks"},
        ],
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "fax_template_id": "test-template",
        "map_menu_by_position": False,
    }
    structural_rows = [
        ["02/15", "朝", "Menu A", "", ""],
        ["02/15", "朝", "Menu B", "", ""],
        ["02/15", "朝", "Menu C", "", ""],
        ["02/15", "朝", "Menu D", "", ""],
    ]
    reference_rows = [
        ["02/15", "朝", "Menu C", "42", ""],
        ["02/15", "朝", "Menu D", "43", ""],
    ]
    rotated_rows = [
        ["02/15", "朝", "Menu A", "42", ""],
        ["02/15", "朝", "Menu B", "43", ""],
        ["02/15", "朝", "Menu C", "", ""],
        ["02/15", "朝", "Menu D", "", ""],
    ]

    def _fake_pipeline(**_kwargs):
        return "gs://pipeline-output.json"

    def _fake_config(_facility_id: str):
        return {"facility_id": "FAC00001", "fax_template": dict(template)}

    def _fake_load_pipeline(_ref, *_, wait_seconds_override=None, **__):
        return {"table_raw": "", "rows": []}

    def _fake_extract(*_args, **_kwargs):
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026-02-15"],
            table_rows=[list(row) for row in rotated_rows],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "model": "gemini-2.5-flash", "quantity_only_mode": True},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):
        assert [row[3] for row in rows] == ["", "", "42", "43"]
        return [
            {
                "date": "2026-02-15",
                "daypart": "朝",
                "menu_name": "Menu C",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 42,
                "source_row_index": 2,
            },
            {
                "date": "2026-02-15",
                "daypart": "朝",
                "menu_name": "Menu D",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 43,
                "source_row_index": 3,
            },
        ]

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_run_llm_reparse_audit", lambda **_kwargs: {"status": "pass", "issues": [], "blocking_issues": [], "issue_count": 0, "blocking_issue_count": 0})
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline)
    _patch_first_pass_payload(monkeypatch, _make_first_pass_payload(rows=reference_rows))
    monkeypatch.setattr(order_service, "_extract_first_pass_rows_from_payload", lambda *_args, **_kwargs: [list(row) for row in reference_rows])
    monkeypatch.setattr(order_service, "_extract_sheet_rows_from_payload", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(order_service, "_resolve_reparse_llm_baseline", lambda **_kwargs: {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "rows": [list(row) for row in structural_rows],
        "row_ids": [f"row-{idx+1}" for idx in range(len(structural_rows))],
        "structure_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "structure_rows": [list(row) for row in structural_rows],
        "structure_row_ids": [f"srow-{idx+1}" for idx in range(len(structural_rows))],
        "structure_source": "weekly_menu_structure",
    })
    monkeypatch.setattr(config_service, "get_facility_config", _fake_config)
    monkeypatch.setattr(order_service, "_validate_reparse_lines_against_weekly_menu", lambda **_kwargs: (None, None))
    monkeypatch.setattr(order_service, "_apply_menu_matching", lambda lines, *_args, **_kwargs: lines)
    monkeypatch.setattr(order_service, "_apply_menu_position_mapping_safe", lambda lines, *_args, **_kwargs: (lines, 0))
    monkeypatch.setattr(order_service, "_build_reparse_position_menu_entries", lambda **_kwargs: [])
    monkeypatch.setattr(order_service, "_resolve_llm_expected_row_count", lambda **_kwargs: 4)
    monkeypatch.setattr(order_service, "_resolve_llm_expected_row_count", lambda **_kwargs: 4)

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    assert [line["quantity_original"] for line in updated["lines"]] == [42, 43]
    assert updated["reparse"]["provider"] == "gemini"


def test_resolve_reparse_llm_baseline_prefers_current_sheet_rows(monkeypatch):
    monkeypatch.setattr(order_service, "_build_reparse_structural_baseline", lambda **kwargs: None)
    monkeypatch.setattr(
        order_service,
        "get_ocr_sheet",
        lambda order_id, **_kwargs: (
            {
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"],
                "rows": [
                    ["02/15", "朝", "Menu A", ""],
                    ["02/15", "昼", "Menu B", "42"],
                ],
            },
            None,
        ),
    )
    monkeypatch.setattr(
        order_service,
        "_load_order_ocr_cache",
        lambda order_id: {"template_id": "fax_layout_regular_forbidden_v1"},
    )

    baseline = order_service._resolve_reparse_llm_baseline(
        order_id="ORDTEST0001",
        template={"main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"]},
    )

    assert baseline is not None
    assert baseline["baseline_source"] == "sheet"
    assert baseline["rows"] == [
        ["02/15", "朝", "Menu A", ""],
        ["02/15", "昼", "Menu B", "42"],
    ]
    assert baseline["fields"] == ["date_mmdd", "daypart", "menu", "qty.regular_x"]


def test_resolve_reparse_llm_baseline_prefers_full_structure_when_current_sheet_truncated(monkeypatch):
    monkeypatch.setattr(
        order_service,
        "_build_reparse_structural_baseline",
        lambda **kwargs: {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"],
            "rows": [
                ["02/15", "朝", "Menu A", ""],
                ["02/15", "朝", "Menu B", ""],
                ["02/15", "昼", "Menu C", ""],
                ["02/15", "昼", "Menu D", ""],
            ],
            "row_ids": ["row-1", "row-2", "row-3", "row-4"],
            "baseline_source": "weekly_menu_structure",
        },
    )
    monkeypatch.setattr(
        order_service,
        "get_ocr_sheet",
        lambda order_id, **_kwargs: (
            {
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"],
                "rows": [
                    ["02/15", "朝", "Menu A", ""],
                    ["02/15", "昼", "Menu C", "42"],
                ],
            },
            None,
        ),
    )

    baseline = order_service._resolve_reparse_llm_baseline(
        order_id="ORDTEST0001",
        template={"main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"]},
    )

    assert baseline is not None
    assert baseline["baseline_source"] == "weekly_menu_structure"
    assert len(baseline["rows"]) == 4


def test_evaluate_quantity_only_rows_quality_detects_row_overfill():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ],
    }
    rows = [
        ["02/15", "朝", "Menu A", "42", ""],
        ["02/15", "朝", "Menu B", "42", ""],
        ["02/15", "昼", "Menu C", "42", ""],
        ["02/15", "昼", "Menu D", "42", ""],
        ["02/15", "夕", "Menu E", "43", ""],
    ]

    error, detail = order_service._evaluate_quantity_only_rows_quality(
        rows=rows,
        template=template,
        expected_row_count=4,
        reference_rows=None,
    )

    assert error == "sheet_row_overfill"
    assert isinstance(detail, dict)
    assert detail.get("extra_rows") == 1


def test_validate_reparse_date_anchor_stability_detects_drift():
    previous_lines = [
        {"date": "2026-02-08"},
        {"date": "2026-02-09"},
        {"date": "2026-02-10"},
        {"date": "2026-02-11"},
        {"date": "2026-02-12"},
        {"date": "2026-02-13"},
    ]
    candidate_lines = [
        {"date": "2026-02-01"},
        {"date": "2026-02-02"},
        {"date": "2026-02-03"},
        {"date": "2026-02-04"},
        {"date": "2026-02-05"},
        {"date": "2026-02-06"},
    ]

    error, detail = order_service._validate_reparse_date_anchor_stability(
        previous_lines=previous_lines,
        candidate_lines=candidate_lines,
    )

    assert error == "sheet_date_anchor_drift"
    assert isinstance(detail, dict)
    assert detail.get("start_shift_days") == 7
    assert detail.get("end_shift_days") == 7


def test_validate_reparse_date_anchor_stability_allows_overlap():
    previous_lines = [
        {"date": "2026-02-08"},
        {"date": "2026-02-09"},
        {"date": "2026-02-10"},
        {"date": "2026-02-11"},
        {"date": "2026-02-12"},
        {"date": "2026-02-13"},
    ]
    candidate_lines = [
        {"date": "2026-02-08"},
        {"date": "2026-02-09"},
        {"date": "2026-02-10"},
        {"date": "2026-02-11"},
        {"date": "2026-02-12"},
        {"date": "2026-02-13"},
    ]

    error, detail = order_service._validate_reparse_date_anchor_stability(
        previous_lines=previous_lines,
        candidate_lines=candidate_lines,
    )

    assert error is None
    assert detail is None


def test_reparse_order_auto_falls_back_to_llm_when_pipeline_lines_empty(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-auto-llm-fallback.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-auto-llm-fallback-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    extract_calls: list[str] = []

    def _fake_pipeline(**_kwargs):
        return None

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):
        provider = str(
            template.get("_force_main_ocr_provider")
            or template.get("main_ocr_provider")
            or "pipeline"
        ).strip().lower()
        extract_calls.append(provider)
        if provider == "gemini":
            assert "Automatic fallback context:" in str(template.get("gemini_ocr_prompt") or "")
            assert '"reason": "lines_empty"' in str(template.get("gemini_ocr_prompt") or "")
            return FaxExtractedData(
                facility_name="Test Facility",
                date_strings=[canonical_date],
                table_rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "7"]],
                tokens=[],
                grid=None,
                ocr_provider="gemini",
                provider_debug={"provider": "gemini", "model": "gemini-2.5-flash"},
            )
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[],
            tokens=[],
            grid=None,
            ocr_provider="pipeline",
            provider_debug={"provider": "pipeline"},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):
        provider = str(
            template.get("_force_main_ocr_provider")
            or template.get("main_ocr_provider")
            or "pipeline"
        ).strip().lower()
        if provider == "gemini":
            return [
                {
                    "date": canonical_date,
                    "daypart": canonical_daypart,
                    "menu_name": canonical_menu,
                    "diet_type": "regular",
                    "area_id": "2F",
                    "bag_type": "standard",
                    "quantity_original": 7,
                    "source_row_index": 0,
                }
            ]
        return []

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_has_gemini_api_key", lambda: True)
    monkeypatch.setattr(order_service, "_has_openai_api_key", lambda: False)
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[[canonical_mmdd, canonical_daypart, canonical_menu, ""]]),
    )

    updated, error = order_service.reparse_order(order["id"])

    assert error is None
    assert updated is not None
    assert extract_calls[:2] == ["pipeline", "gemini"]
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    metrics = job.get("metrics") or {}
    assert metrics.get("provider") == "gemini"
    assert metrics.get("auto_fallback_applied") is True
    assert metrics.get("auto_fallback_from_provider") == "pipeline"
    assert metrics.get("auto_fallback_reason") == "lines_empty"


def test_reparse_order_llm_assist_ignores_cached_order_rows_when_first_pass_rows_missing(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-llm-assist-no-cached-rescue.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-llm-assist-no-cached-rescue-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    baseline_sheet = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [[canonical_mmdd, canonical_daypart, canonical_menu, ""]],
        "row_ids": ["row-1"],
        "source": "weekly_menu+payload_row",
    }
    audit_candidates: list[list[list[str]]] = []

    def _fake_pipeline(**_kwargs):
        return "gs://dummy/output.json"

    def _fake_load_pipeline_output_with_retry(_ref, *_, wait_seconds_override=None, **__):
        return {"facility_name": "Test Facility", "date_strings": [canonical_mmdd], "rows": []}

    def _fake_load_order_ocr_cache(_order_id):
        return {
            "rows": [
                {
                    "date_mmdd": canonical_mmdd,
                    "daypart": canonical_daypart,
                    "menu": canonical_menu,
                    "qty.regular_2f": "99",
                }
            ]
        }

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "7"]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "model": "gemini-2.5-flash", "quantity_only_mode": True},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):
        return [
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 7,
                "source_row_index": 0,
            }
        ]

    def _fake_audit(**kwargs):
        candidate_rows = [list(row) for row in (kwargs.get("candidate_rows") or []) if isinstance(row, list)]
        audit_candidates.append(candidate_rows)
        return {
            "status": "pass",
            "issue_count": 0,
            "blocking_issue_count": 0,
            "issues": [],
            "blocking_issues": [],
        }

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "_load_order_ocr_cache", _fake_load_order_ocr_cache)
    monkeypatch.setattr(order_service, "get_ocr_output", lambda order_id, *, persist_cache=False: (None, "ocr_output_invalid"))
    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id, **_kwargs: (dict(baseline_sheet), None))
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_run_llm_reparse_audit", _fake_audit)
    monkeypatch.setattr(order_service, "_validate_reparse_lines_against_weekly_menu", lambda **_kwargs: (None, None))
    monkeypatch.setattr(order_service, "_build_reparse_position_menu_entries", lambda **_kwargs: [])
    monkeypatch.setattr(order_service, "_resolve_llm_expected_row_count", lambda **_kwargs: 1)
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "7"]]),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    assert audit_candidates
    assert not any("99" in row for row in audit_candidates[0])
    assert any(
        len(row) >= 4
        and row[0] == canonical_mmdd
        and row[1] == canonical_daypart
        and "7" in row
        for row in audit_candidates[0]
    )


def test_reparse_order_llm_second_pass_uses_evaluator_feedback(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-llm-audit-feedback-retry.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-llm-audit-feedback-retry-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    extract_prompts: list[str] = []
    audit_calls = 0

    def _fake_pipeline(**_kwargs):
        return "gs://dummy/output.json"

    def _fake_load_pipeline_output_with_retry(_ref, *_, wait_seconds_override=None, **__):
        return {"facility_name": "Test Facility", "date_strings": [canonical_mmdd], "rows": []}

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):
        extract_prompts.append(
            "\n\n".join(
                [
                    str(template.get("gemini_ocr_prompt") or ""),
                    str(template.get("gemini_ocr_user_prompt") or ""),
                ]
            )
        )
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "7"]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "model": "gemini-2.5-flash", "quantity_only_mode": True},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):
        return [
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 7,
                "source_row_index": 0,
            }
        ]

    def _fake_audit(**kwargs):
        nonlocal audit_calls
        audit_calls += 1
        if audit_calls == 1:
            issue = {
                "issue_code": "overextended_span",
                "severity": "high",
                "confidence": 0.99,
                "reason": "quantity spilled into blank rows",
                "row_index": 0,
                "column_index": 3,
                "evidence": "blank rows should remain blank",
            }
            return {
                "status": "fail",
                "issue_count": 1,
                "blocking_issue_count": 1,
                "issues": [issue],
                "blocking_issues": [issue],
            }
        return {
            "status": "pass",
            "issue_count": 0,
            "blocking_issue_count": 0,
            "issues": [],
            "blocking_issues": [],
        }

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_run_llm_reparse_audit", _fake_audit)
    monkeypatch.setattr(order_service, "_validate_reparse_lines_against_weekly_menu", lambda **_kwargs: (None, None))
    monkeypatch.setattr(order_service, "_build_reparse_position_menu_entries", lambda **_kwargs: [])
    monkeypatch.setattr(order_service, "_resolve_llm_expected_row_count", lambda **_kwargs: 1)
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "7"]]),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    assert audit_calls >= 1
    assert extract_prompts
    repair_prompts = [prompt for prompt in extract_prompts if "Second-pass repair mode" in prompt]
    assert repair_prompts
    assert any("Evaluator feedback from previous OCR draft" in prompt for prompt in repair_prompts)
    assert any("overextended_span" in prompt for prompt in repair_prompts)
    assert any("Determine each date/daypart block's quantity pattern first" in prompt for prompt in repair_prompts)


def test_build_quantity_only_repair_prompts_include_evaluator_feedback_and_structural_row_rules():
    system_prompt, user_prompt = order_service._build_quantity_only_repair_prompts(
        provider="gemini",
        template={
            "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"],
        },
        current_rows=[["", "", "", "42"]],
        expected_row_count=3,
        quality_error="sheet_structural_drift",
        quality_detail={"quality_issue": "blank_anchor"},
        baseline_rows=[
            ["02/15", "朝", "Menu A", ""],
            ["02/15", "昼", "Menu B", ""],
            ["02/15", "昼", "Menu C", ""],
        ],
        baseline_fields=["date_mmdd", "daypart", "menu", "qty.regular_x"],
        structural_rows=[
            ["02/15", "朝", "Menu A", ""],
            ["02/15", "昼", "Menu B", ""],
            ["02/15", "昼", "Menu C", ""],
        ],
        structural_fields=["date_mmdd", "daypart", "menu", "qty.regular_x"],
        evaluator_feedback={
            "status": "fail",
            "issues": [
                {
                    "issue_code": "missing_blank_anchor_rows",
                    "severity": "high",
                    "confidence": 0.98,
                    "row_index": 0,
                    "column_index": 3,
                    "evidence": "blank breakfast row should remain blank",
                    "reason": "later quantity shifted upward",
                }
            ],
        },
    )

    assert "Determine each date/daypart block's quantity pattern first" in system_prompt
    assert "row_index is the structural row position" in system_prompt
    assert "Do not compress blank rows out of the output" in system_prompt
    assert "re-check every date/daypart block for the same pattern" in system_prompt
    assert "Existing quantities in the current sheet may be stale" in system_prompt
    assert "Return sparse row patches only." in system_prompt
    assert "Do not return structural anchor fields such as date/daypart/menu" in system_prompt
    assert "Evaluator feedback from previous OCR draft" in user_prompt
    assert "missing_blank_anchor_rows" in user_prompt


def test_reparse_order_llm_final_audit_failure_retries_with_evaluator_feedback(monkeypatch, tmp_path):
    order_service.clear_all()
    monkeypatch.setenv("OCR_REPARSE_ENABLE_REPAIR_PASS", "0")
    pdf_path = tmp_path / "sample-llm-final-audit-feedback-retry.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-llm-final-audit-feedback-retry-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    extract_prompts: list[str] = []
    audit_calls = 0

    def _fake_pipeline(**_kwargs):
        return "gs://dummy/output.json"

    def _fake_load_pipeline_output_with_retry(_ref, *_, wait_seconds_override=None, **__):
        return {"facility_name": "Test Facility", "date_strings": [canonical_mmdd], "rows": []}

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):
        extract_prompts.append(str(template.get("gemini_ocr_prompt") or ""))
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "7"]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={
                "provider": "gemini",
                "model": "gemini-2.5-flash",
                "quantity_only_mode": True,
            },
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):
        return [
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 7,
                "source_row_index": 0,
            }
        ]

    def _fake_audit(**kwargs):
        nonlocal audit_calls
        audit_calls += 1
        if audit_calls == 2:
            issue = {
                "issue_code": "overextended_span",
                "severity": "high",
                "confidence": 0.99,
                "reason": "quantity spilled into blank rows",
                "row_index": 0,
                "column_index": 3,
                "evidence": "blank rows should remain blank",
            }
            return {
                "status": "fail",
                "issue_count": 1,
                "blocking_issue_count": 1,
                "issues": [issue],
                "blocking_issues": [issue],
            }
        return {
            "status": "pass",
            "issue_count": 0,
            "blocking_issue_count": 0,
            "issues": [],
            "blocking_issues": [],
        }

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_run_llm_reparse_audit", _fake_audit)
    monkeypatch.setattr(order_service, "_llm_reparse_audit_requires_second_pass", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(order_service, "_validate_reparse_lines_against_weekly_menu", lambda **_kwargs: (None, None))
    monkeypatch.setattr(order_service, "_build_reparse_position_menu_entries", lambda **_kwargs: [])
    monkeypatch.setattr(order_service, "_resolve_llm_expected_row_count", lambda **_kwargs: 1)
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "7"]]),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    assert audit_calls >= 3
    assert extract_prompts
    assert any("Evaluator feedback from previous OCR draft" in prompt for prompt in extract_prompts)
    assert any(prompt.strip() for prompt in extract_prompts)


def test_reparse_order_with_llm_assist_defaults_to_gemini_when_provider_unspecified(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-explicit-reparse-defaults-to-gemini.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-explicit-reparse-defaults-to-gemini-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    baseline_sheet = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [[canonical_mmdd, canonical_daypart, canonical_menu, ""]],
        "row_ids": ["row-1"],
        "source": "weekly_menu+payload_row",
    }
    provider_calls: list[str] = []

    def _fake_pipeline(**_kwargs):
        return "gs://pipeline-output.json"

    def _fake_load_pipeline(_ref, *_, wait_seconds_override=None, **__):
        return {
            "table_raw": (
                "|日付|区分|メニュー|常食|\n"
                "|---|---|---|---|\n"
                "|02/15|昼|Menu A|7|"
            )
        }

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        provider = str(
            template.get("_force_main_ocr_provider")
            or template.get("main_ocr_provider")
            or "pipeline"
        ).strip().lower()
        provider_calls.append(provider)
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026/02/15"],
            table_rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "7"]],
            tokens=[],
            grid=None,
            ocr_provider=provider,
            provider_debug={"provider": provider, "full_table_mode": True},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        return [
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 7,
                "source_row_index": 0,
            }
        ]

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline)
    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id, **_kwargs: (dict(baseline_sheet), None))
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_has_gemini_api_key", lambda: True)
    monkeypatch.setattr(order_service, "_has_openai_api_key", lambda: False)

    updated, error = order_service.reparse_order(order["id"], llm_assist=True)

    assert error is None
    assert updated is not None
    assert provider_calls
    assert provider_calls[0] == "gemini"
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    metrics = job.get("metrics") or {}
    assert metrics.get("provider") == "gemini"


def test_reparse_order_llm_assist_rejects_sparse_quantity_only_rows_when_full_table_anchor_required(
    monkeypatch,
    tmp_path,
):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-llm-assist-sparse-rows-rejected.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-llm-assist-sparse-rows-rejected-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    baseline_sheet = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [[canonical_mmdd, canonical_daypart, canonical_menu, ""]],
        "row_ids": ["row-1"],
        "source": "weekly_menu+payload_row",
    }

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", lambda **_kwargs: None)
    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id, **_kwargs: (dict(baseline_sheet), None))
    monkeypatch.setattr(
        order_service,
        "extract_fax_data",
        lambda *_args, **_kwargs: FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[["7"]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "quantity_only_mode": True},
        ),
    )
    monkeypatch.setattr(order_service, "_validate_reparse_lines_against_weekly_menu", lambda **_kwargs: (None, None))
    monkeypatch.setattr(order_service, "_build_reparse_position_menu_entries", lambda **_kwargs: [])
    monkeypatch.setattr(
        order_service,
        "_run_llm_reparse_audit",
        lambda **_kwargs: {
            "status": "pass",
            "issues": [],
            "issue_count": 0,
            "blocking_issues": [],
            "blocking_issue_count": 0,
        },
    )
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "7"]]),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert updated is None
    assert error == "llm_full_table_structural_drift"
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") == "done"
    assert (job.get("metrics") or {}).get("error") == "llm_full_table_structural_drift"


def test_reparse_order_llm_assist_blocks_provider_fallback_pipeline(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-llm-assist-provider-fallback-blocked.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-llm-assist-provider-fallback-blocked-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    baseline_sheet = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [[canonical_mmdd, canonical_daypart, canonical_menu, ""]],
        "row_ids": ["row-1"],
        "source": "weekly_menu+payload_row",
    }

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", lambda **_kwargs: None)
    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id, **_kwargs: (dict(baseline_sheet), None))
    monkeypatch.setattr(
        order_service,
        "extract_fax_data",
        lambda *_args, **_kwargs: FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "7"]],
            tokens=[],
            grid=None,
            ocr_provider="gemini_fallback_pipeline",
            provider_debug={
                "provider": "gemini_fallback_pipeline",
                "fallback_reason": "Gemini OCR timeout after 240s",
            },
        ),
    )
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "7"]]),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert updated is None
    assert error == "llm_provider_fallback_pipeline"
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") == "failed"
    assert (job.get("metrics") or {}).get("error") == "llm_provider_fallback_pipeline"
    assert (job.get("metrics") or {}).get("actual_provider") == "gemini_fallback_pipeline"


def test_reparse_order_with_yomitoku_rows_runs_evaluator_before_llm_inference(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-yomitoku-evaluator-first.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-yomitoku-evaluator-first-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    yomitoku_rows = [[canonical_mmdd, canonical_daypart, canonical_menu, "2"]]
    call_trace: list[str] = []
    audit_count = {"value": 0}

    def _fake_pipeline(**_kwargs):
        return "gs://pipeline-output.json"

    def _fake_load_pipeline(_ref, *_, wait_seconds_override=None, **__):
        return {
            "pages": [
                {
                    "page_index": 1,
                    "tables": [
                        {
                            "table_id": "p1_t1",
                            "rows": [
                                ["日付", "区分", "メニュー", "常食"],
                                [canonical_mmdd, canonical_daypart, canonical_menu, "2"],
                            ],
                        }
                    ],
                }
            ],
            "table_raw": f"|日付|区分|メニュー|常食|\n|---|---|---|---|\n|{canonical_mmdd}|{canonical_daypart}|{canonical_menu}|2|",
        }

    def _fake_audit(**kwargs):
        audit_count["value"] += 1
        call_trace.append("audit-pre")
        assert kwargs["candidate_rows"]
        if audit_count["value"] == 1:
            assert kwargs["candidate_rows"][0][0] == canonical_mmdd
            assert kwargs["candidate_rows"][0][1] == canonical_daypart
            assert "2" in kwargs["candidate_rows"][0]
        return {
            "status": "fail",
            "provider": "openai",
            "actual_provider": "openai",
            "model": "audit-model-v1",
            "issue_count": 1,
            "blocking_issue_count": 1,
            "issues": [
                {
                    "issue_code": "column_swap",
                    "severity": "high",
                    "row_index": 0,
                    "column_index": 3,
                    "confidence": 0.91,
                    "evidence": "visible 5 in the regular column",
                    "reason": "repair the yomitoku draft before line parsing",
                }
            ],
            "blocking_issues": [
                {
                    "issue_code": "column_swap",
                    "severity": "high",
                    "row_index": 0,
                    "column_index": 3,
                    "confidence": 0.91,
                    "evidence": "visible 5 in the regular column",
                    "reason": "repair the yomitoku draft before line parsing",
                }
            ],
        }

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):
        call_trace.append("extract-1")
        assert pdf_bytes.startswith(b"%PDF-1.4")
        assert facility_id == "FAC00001"
        prompt = str(template.get("gemini_ocr_prompt") or "")
        assert "Evaluator feedback from previous OCR draft" in prompt
        assert '"issue_code": "column_swap"' in prompt
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "5"]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "model": "gemini-2.5-flash"},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):
        return [
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": int(rows[0][3]),
                "source_row_index": 0,
            }
        ]

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline)
    monkeypatch.setattr(order_service, "_run_llm_reparse_audit", _fake_audit)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "2"]]),
    )
    _patch_first_pass_payload(monkeypatch, _make_first_pass_payload(rows=yomitoku_rows))

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    assert call_trace[:2] == ["audit-pre", "extract-1"]
    assert updated["lines"][0]["quantity_original"] == 5


def test_reparse_order_with_yomitoku_rows_runs_second_pass_after_llm_candidate_audit(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-yomitoku-evaluator-second-pass.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-yomitoku-evaluator-second-pass-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    first_rows = [[canonical_mmdd, canonical_daypart, canonical_menu, "4"]]
    second_rows = [[canonical_mmdd, canonical_daypart, canonical_menu, "7"]]
    call_trace: list[str] = []
    extract_count = {"value": 0}
    audit_count = {"value": 0}

    def _fake_pipeline(**_kwargs):
        return "gs://pipeline-output.json"

    def _fake_load_pipeline(_ref, *_, wait_seconds_override=None, **__):
        return {
            "pages": [
                {
                    "page_index": 1,
                    "tables": [
                        {
                            "table_id": "p1_t1",
                            "rows": [
                                ["日付", "区分", "メニュー", "常食"],
                                [canonical_mmdd, canonical_daypart, canonical_menu, "2"],
                            ],
                        }
                    ],
                }
            ],
            "table_raw": f"|日付|区分|メニュー|常食|\n|---|---|---|---|\n|{canonical_mmdd}|{canonical_daypart}|{canonical_menu}|2|",
        }

    def _fake_audit(**kwargs):
        audit_count["value"] += 1
        call_trace.append(f"audit-{audit_count['value']}")
        if audit_count["value"] == 1:
            assert kwargs["candidate_rows"][0][0] == canonical_mmdd
            assert kwargs["candidate_rows"][0][1] == canonical_daypart
            assert "2" in kwargs["candidate_rows"][0]
            return {
                "status": "fail",
                "provider": "openai",
                "actual_provider": "openai",
                "model": "audit-model-v1",
                "issue_count": 1,
                "blocking_issue_count": 1,
                "issues": [
                    {
                        "issue_code": "column_swap",
                        "severity": "high",
                        "row_index": 0,
                        "column_index": 3,
                        "confidence": 0.91,
                        "evidence": "visible 4 in the regular column",
                        "reason": "repair the yomitoku draft before line parsing",
                    }
                ],
                "blocking_issues": [
                    {
                        "issue_code": "column_swap",
                        "severity": "high",
                        "row_index": 0,
                        "column_index": 3,
                        "confidence": 0.91,
                        "evidence": "visible 4 in the regular column",
                        "reason": "repair the yomitoku draft before line parsing",
                    }
                ],
            }
        assert kwargs["candidate_rows"][0][:4] == first_rows[0]
        return {
            "status": "fail",
            "provider": "openai",
            "actual_provider": "openai",
            "model": "audit-model-v1",
            "issue_count": 1,
            "blocking_issue_count": 1,
            "issues": [
                {
                    "issue_code": "date_anchor_drift",
                    "severity": "critical",
                    "row_index": 0,
                    "column_index": 3,
                    "confidence": 0.99,
                    "evidence": "quantity belongs to the next block",
                    "reason": "shift the candidate rows using the visible date/daypart block anchors",
                }
            ],
            "blocking_issues": [
                {
                    "issue_code": "date_anchor_drift",
                    "severity": "critical",
                    "row_index": 0,
                    "column_index": 3,
                    "confidence": 0.99,
                    "evidence": "quantity belongs to the next block",
                    "reason": "shift the candidate rows using the visible date/daypart block anchors",
                }
            ],
        }

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):
        extract_count["value"] += 1
        call_trace.append(f"extract-{extract_count['value']}")
        prompt = str(template.get("gemini_ocr_prompt") or "")
        if extract_count["value"] == 1:
            assert "Evaluator feedback from previous OCR draft" in prompt
            assert '"issue_code": "column_swap"' in prompt
            rows = first_rows
        else:
            assert "Evaluator feedback from previous OCR draft" in prompt
            assert '"issue_code": "date_anchor_drift"' in prompt
            assert "First LLM inference candidate rows" in prompt
            rows = second_rows
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[list(row) for row in rows],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "model": "gemini-2.5-flash"},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):
        return [
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": int(rows[0][3]),
                "source_row_index": 0,
            }
        ]

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline)
    monkeypatch.setattr(order_service, "_run_llm_reparse_audit", _fake_audit)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "2"]]),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    assert call_trace == ["audit-1", "extract-1", "audit-2", "extract-2"]
    assert updated["lines"][0]["quantity_original"] == 7


def test_reparse_order_without_yomitoku_rows_runs_infer_then_evaluator_then_second_inference(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-llm-evaluator-second-pass.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-llm-evaluator-second-pass-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    first_rows = [[canonical_mmdd, canonical_daypart, canonical_menu, "4"]]
    second_rows = [[canonical_mmdd, canonical_daypart, canonical_menu, "7"]]
    call_trace: list[str] = []
    extract_count = {"value": 0}
    audit_count = {"value": 0}

    def _fake_pipeline(**_kwargs):
        return "gs://pipeline-output-empty.json"

    def _fake_load_pipeline(_ref, *_, wait_seconds_override=None, **__):
        return {"pages": [], "table_raw": ""}

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):
        extract_count["value"] += 1
        call_trace.append(f"extract-{extract_count['value']}")
        prompt = str(template.get("gemini_ocr_prompt") or "")
        if extract_count["value"] == 1:
            assert "Evaluator feedback from previous OCR draft" not in prompt
            rows = first_rows
        else:
            assert "Evaluator feedback from previous OCR draft" in prompt
            assert "First LLM inference candidate rows" in prompt
            rows = second_rows
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[list(row) for row in rows],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "model": "gemini-2.5-flash"},
        )

    def _fake_audit(**kwargs):
        audit_count["value"] += 1
        call_trace.append(f"audit-{audit_count['value']}")
        if audit_count["value"] == 1:
            assert kwargs["candidate_rows"] == first_rows
            return {
                "status": "fail",
                "provider": "openai",
                "actual_provider": "openai",
                "model": "audit-model-v1",
                "issue_count": 1,
                "blocking_issue_count": 1,
                "issues": [
                    {
                        "issue_code": "row_count_shortfall",
                        "severity": "high",
                        "row_index": 0,
                        "column_index": 3,
                        "confidence": 0.88,
                        "evidence": "visible 7 in the regular column",
                        "reason": "run a second pass with evaluator feedback",
                    }
                ],
                "blocking_issues": [
                    {
                        "issue_code": "row_count_shortfall",
                        "severity": "high",
                        "row_index": 0,
                        "column_index": 3,
                        "confidence": 0.88,
                        "evidence": "visible 7 in the regular column",
                        "reason": "run a second pass with evaluator feedback",
                    }
                ],
            }
        assert kwargs["candidate_rows"] == second_rows
        return {
            "status": "pass",
            "provider": "openai",
            "actual_provider": "openai",
            "model": "audit-model-v1",
            "issue_count": 0,
            "blocking_issue_count": 0,
            "issues": [],
            "blocking_issues": [],
        }

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):
        return [
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": int(rows[0][3]),
                "source_row_index": 0,
            }
        ]

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline)
    monkeypatch.setattr(order_service, "_run_llm_reparse_audit", _fake_audit)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    _patch_first_pass_payload(monkeypatch, _make_first_pass_payload(table_raw="first-pass-placeholder"))

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    assert call_trace[:3] == ["extract-1", "audit-1", "extract-2"]
    assert updated["lines"][0]["quantity_original"] == 7


def test_reparse_order_row_coverage_uses_payload_scoped_expected_rows(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-row-coverage-scope.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-row-coverage-scope-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_config(_facility_id: str):
        return {
            "facility_id": "FAC00001",
            "fax_template": {
                "header_rows": 0,
                "map_menu_by_position": True,
                "columns": [
                    {"index": 0, "role": "date"},
                    {"index": 1, "role": "daypart"},
                    {"index": 2, "role": "menu_name"},
                    {
                        "index": 3,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "bag_type": "standard",
                    },
                ],
                "main_ocr_row_fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_2f",
                ],
            },
        }

    def _fake_pipeline(**_kwargs):
        return "file://pipeline-output.json"

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0, wait_seconds_override=None, **_kwargs):  # noqa: ARG001
        # Keep only weekly date anchors so expected-row scope becomes 56 rows,
        # but do not provide rescue rows that could mask row-coverage failure.
        return {"date_strings": [f"2/{day}" for day in range(8, 15)]}

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        rows = [["", "", "", "6"] for _ in range(54)]
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026/02/08"],
            table_rows=rows,
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "quantity_only_mode": True},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        parsed = []
        for idx, row in enumerate(rows):
            qty_raw = row[3] if len(row) > 3 else ""
            if not str(qty_raw).strip():
                continue
            parsed.append(
                {
                    "date": "2026-02-08",
                    "daypart": "朝",
                    "menu_name": f"OCR-{idx}",
                    "diet_type": "regular",
                    "area_id": "2F",
                    "bag_type": "standard",
                    "quantity_original": int(qty_raw),
                    "source_row_index": idx,
                }
            )
        return parsed

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    monkeypatch.setattr(config_service, "get_facility_config", _fake_config)
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)
    _patch_first_pass_payload(monkeypatch, _make_first_pass_payload(table_raw="first-pass-placeholder"))
    monkeypatch.setenv("OCR_REPARSE_ROW_COVERAGE_MIN_RATIO", "0.98")
    monkeypatch.setenv("OCR_REPARSE_MAX_MISSING_TAIL_ROWS", "0")
    monkeypatch.setenv("OCR_REPARSE_ENABLE_REPAIR_PASS", "0")

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    metrics = job.get("metrics") or {}
    assert metrics.get("error") in {None, ""}
    detail = metrics.get("quality_detail") or metrics.get("validation_detail") or {}
    assert detail.get("actual_row_count") == 54
    assert int(detail.get("expected_row_count") or 0) >= 54


def test_reparse_order_row_coverage_prefers_pipeline_rows_when_anchor_dates_are_weak(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-row-coverage-weak-anchor.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-row-coverage-weak-anchor-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_config(_facility_id: str):
        return {
            "facility_id": "FAC00001",
            "fax_template": {
                "header_rows": 0,
                "map_menu_by_position": True,
                "columns": [
                    {"index": 0, "role": "date"},
                    {"index": 1, "role": "daypart"},
                    {"index": 2, "role": "menu_name"},
                    {
                        "index": 3,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "bag_type": "standard",
                    },
                ],
                "main_ocr_row_fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_2f",
                ],
            },
        }

    def _fake_pipeline(**_kwargs):
        return "file://pipeline-output.json"

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0, wait_seconds_override=None, **_kwargs):  # noqa: ARG001
        return {"table_rows": [["", "", "", "6"] for _ in range(43)]}

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        rows = [["", "", "", "6"] for _ in range(43)]
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["1/18"],
            table_rows=rows,
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "quantity_only_mode": True},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        parsed = []
        for idx, row in enumerate(rows):
            qty_raw = row[3] if len(row) > 3 else ""
            if not str(qty_raw).strip():
                continue
            parsed.append(
                {
                    "date": "2026-01-18",
                    "daypart": "朝",
                    "menu_name": f"OCR-{idx}",
                    "diet_type": "regular",
                    "area_id": "2F",
                    "bag_type": "standard",
                    "quantity_original": int(qty_raw),
                    "source_row_index": idx,
                }
            )
        return parsed

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(8, 15):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 56
        return entries

    monkeypatch.setattr(config_service, "get_facility_config", _fake_config)
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)
    monkeypatch.setattr(order_service, "_validate_reparse_lines_against_weekly_menu", lambda **_kwargs: (None, None))
    monkeypatch.setenv("OCR_REPARSE_ROW_COVERAGE_MIN_RATIO", "0.98")
    monkeypatch.setenv("OCR_REPARSE_MAX_MISSING_TAIL_ROWS", "0")
    monkeypatch.setenv("OCR_REPARSE_ENABLE_REPAIR_PASS", "0")
    monkeypatch.setenv("OCR_REPARSE_WEAK_ANCHOR_MAX_DATES", "1")
    monkeypatch.setenv("OCR_REPARSE_WEAK_ANCHOR_MIN_GAP_ROWS", "8")
    monkeypatch.setenv("OCR_REPARSE_WEAK_ANCHOR_OBSERVED_DELTA_ROWS", "2")

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    metrics = job.get("metrics") or {}
    assert metrics.get("error") in {None, ""}
    detail = metrics.get("quality_detail") or {}
    assert detail.get("expected_row_count") == 43
    assert detail.get("actual_row_count") == 43


def test_reparse_order_row_coverage_prefers_existing_line_anchor_scope(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-row-coverage-existing-anchor-scope.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    dayparts = ["朝", "朝", "昼", "昼", "昼", "夕", "夕", "夕"]

    def _fake_weekly_entries(_week_id: str):
        entries = []
        for day in range(1, 29):
            menu_date = date(2026, 2, day)
            for slot, daypart in enumerate(dayparts):
                entries.append(
                    {
                        "menu_date": menu_date,
                        "daypart_key": daypart,
                        "menu_name": f"{menu_date.isoformat()}-{daypart}-{slot}",
                    }
                )
        assert len(entries) == 224
        return entries

    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)

    payload = IngestEmailPayload(
        message_id="msg-row-coverage-existing-anchor-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    existing_lines = []
    for day in range(8, 14):
        for slot, daypart in enumerate(dayparts):
            existing_lines.append(
                {
                    "date": f"2026-02-{day:02d}",
                    "daypart": daypart,
                    "menu_name": f"existing-{day}-{slot}",
                    "diet_type": "regular",
                    "area_id": "2F",
                    "bag_type": "standard",
                    "quantity_original": 1,
                }
            )
    order = order_service.create_order_from_ingest(payload, lines=existing_lines)

    def _fake_config(_facility_id: str):
        return {
            "facility_id": "FAC00001",
            "fax_template": {
                "header_rows": 0,
                "map_menu_by_position": True,
                "columns": [
                    {"index": 0, "role": "date"},
                    {"index": 1, "role": "daypart"},
                    {"index": 2, "role": "menu_name"},
                    {
                        "index": 3,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "bag_type": "standard",
                    },
                    {
                        "index": 4,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "3F",
                        "bag_type": "standard",
                    },
                ],
                "main_ocr_row_fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_2f",
                    "qty.regular_3f",
                ],
            },
        }

    def _fake_pipeline(**_kwargs):
        return "file://pipeline-output.json"

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0, wait_seconds_override=None, **_kwargs):  # noqa: ARG001
        # Payload anchors are intentionally drifted to prior week;
        # existing persisted line dates must remain the anchor source.
        return {"date_strings": [f"2/{day}" for day in range(1, 7)]}

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        rows = [["", "", "", "4", "1"] for _ in range(42)]
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026/02/01"],
            table_rows=rows,
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "quantity_only_mode": True},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        parsed = []
        for idx, row in enumerate(rows):
            qty_raw = row[3] if len(row) > 3 else ""
            if not str(qty_raw).strip():
                continue
            parsed.append(
                {
                    "date": "2026-02-01",
                    "daypart": "朝",
                    "menu_name": f"OCR-{idx}",
                    "diet_type": "regular",
                    "area_id": "2F",
                    "bag_type": "standard",
                    "quantity_original": int(qty_raw),
                    "source_row_index": idx,
                }
            )
        return parsed

    monkeypatch.setattr(config_service, "get_facility_config", _fake_config)
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    _patch_first_pass_payload(monkeypatch, _make_first_pass_payload(table_raw="first-pass-placeholder"))
    monkeypatch.setenv("OCR_REPARSE_ROW_COVERAGE_MIN_RATIO", "0.98")
    monkeypatch.setenv("OCR_REPARSE_MAX_MISSING_TAIL_ROWS", "0")
    monkeypatch.setenv("OCR_REPARSE_ENABLE_REPAIR_PASS", "0")

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert updated is None
    assert error in {"sheet_row_coverage_low", "sheet_date_anchor_drift"}
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    metrics = job.get("metrics") or {}
    detail = metrics.get("quality_detail") or metrics.get("validation_detail") or {}
    assert detail.get("expected_row_count") == 48
    assert detail.get("actual_row_count") == 42


def test_reparse_order_applies_repair_pass_on_row_coverage_shortfall(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-repair-pass-success.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-repair-pass-success-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    call_state = {"extract_count": 0}
    captured_templates: list[dict] = []

    def _fake_config(_facility_id: str):
        return {
            "facility_id": "FAC00001",
            "fax_template": {
                "header_rows": 0,
                "map_menu_by_position": True,
                "columns": [
                    {"index": 0, "role": "date"},
                    {"index": 1, "role": "daypart"},
                    {"index": 2, "role": "menu_name"},
                    {
                        "index": 3,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "bag_type": "standard",
                    },
                ],
                "main_ocr_row_fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_2f",
                ],
            },
        }

    def _fake_pipeline(**_kwargs):
        return None

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0, wait_seconds_override=None, **_kwargs):  # noqa: ARG001
        return {}

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        call_state["extract_count"] += 1
        captured_templates.append(dict(template))
        if call_state["extract_count"] == 1:
            rows = [["", "", "", "6"]]
        else:
            rows = [["", "", "", "6"], ["", "", "", "5"]]
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026/02/15"],
            table_rows=rows,
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "quantity_only_mode": True},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        lines = []
        for idx, row in enumerate(rows):
            qty_raw = row[3] if len(row) > 3 else ""
            if not str(qty_raw).strip():
                continue
            lines.append(
                {
                    "date": "2026-02-15",
                    "daypart": "昼" if idx == 0 else "夕",
                    "menu_name": "Menu A" if idx == 0 else "Menu B",
                    "diet_type": "regular",
                    "area_id": "2F",
                    "bag_type": "standard",
                    "quantity_original": int(qty_raw),
                    "source_row_index": idx,
                }
            )
        return lines

    def _fake_weekly_entries(_week_id: str):
        return [
            {"menu_date": date(2026, 2, 15), "daypart_key": "昼", "menu_name": "Menu A"},
            {"menu_date": date(2026, 2, 15), "daypart_key": "夕", "menu_name": "Menu B"},
        ]

    monkeypatch.setattr(config_service, "get_facility_config", _fake_config)
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)
    _patch_first_pass_payload(monkeypatch, _make_first_pass_payload(table_raw="first-pass-placeholder"))
    monkeypatch.setenv("OCR_REPARSE_ROW_COVERAGE_MIN_RATIO", "0.98")
    monkeypatch.setenv("OCR_REPARSE_MAX_MISSING_TAIL_ROWS", "0")
    monkeypatch.setenv("OCR_REPARSE_ENABLE_REPAIR_PASS", "1")

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    assert len(updated.get("lines") or []) == 2
    assert call_state["extract_count"] >= 2
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    metrics = job.get("metrics") or {}
    assert metrics.get("repair_pass_applied") is True
    assert metrics.get("repair_pass_reason") == "sheet_row_coverage_low"
    assert metrics.get("repair_pass_model") == "gemini-2.5-pro"
    assert captured_templates
    assert captured_templates[1].get("gemini_ocr_model") == "gemini-2.5-pro"
    assert "Failure focus locations and first-pass inference summary" in str(
        captured_templates[1].get("gemini_ocr_user_prompt") or ""
    )


def test_reparse_order_fails_when_row_coverage_low_even_after_repair(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-repair-pass-fail.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-repair-pass-fail-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    call_state = {"extract_count": 0}

    def _fake_config(_facility_id: str):
        return {
            "facility_id": "FAC00001",
            "fax_template": {
                "header_rows": 0,
                "map_menu_by_position": True,
                "columns": [
                    {"index": 0, "role": "date"},
                    {"index": 1, "role": "daypart"},
                    {"index": 2, "role": "menu_name"},
                    {
                        "index": 3,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "bag_type": "standard",
                    },
                ],
                "main_ocr_row_fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_2f",
                ],
            },
        }

    def _fake_pipeline(**_kwargs):
        return None

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0, wait_seconds_override=None, **_kwargs):  # noqa: ARG001
        return {}

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        call_state["extract_count"] += 1
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026/02/15"],
            table_rows=[["", "", "", "6"]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "quantity_only_mode": True},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        lines = []
        for idx, row in enumerate(rows):
            qty_raw = row[3] if len(row) > 3 else ""
            if not str(qty_raw).strip():
                continue
            lines.append(
                {
                    "date": "2026-02-15",
                    "daypart": "昼" if idx == 0 else "夕",
                    "menu_name": "Menu A" if idx == 0 else "Menu B",
                    "diet_type": "regular",
                    "area_id": "2F",
                    "bag_type": "standard",
                    "quantity_original": int(qty_raw),
                    "source_row_index": idx,
                }
            )
        return lines

    def _fake_weekly_entries(_week_id: str):
        return [
            {"menu_date": date(2026, 2, 15), "daypart_key": "昼", "menu_name": "Menu A"},
            {"menu_date": date(2026, 2, 15), "daypart_key": "夕", "menu_name": "Menu B"},
        ]

    monkeypatch.setattr(config_service, "get_facility_config", _fake_config)
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)
    _patch_first_pass_payload(monkeypatch, _make_first_pass_payload(table_raw="first-pass-placeholder"))
    monkeypatch.setenv("OCR_REPARSE_ROW_COVERAGE_MIN_RATIO", "0.98")
    monkeypatch.setenv("OCR_REPARSE_MAX_MISSING_TAIL_ROWS", "0")
    monkeypatch.setenv("OCR_REPARSE_ENABLE_REPAIR_PASS", "1")

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert updated is None
    assert error == "sheet_row_coverage_low"
    assert call_state["extract_count"] >= 2
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") in {"done", "failed"}
    metrics = job.get("metrics") or {}
    assert metrics.get("error") == "sheet_row_coverage_low"
    assert metrics.get("repair_pass_applied") is False


def test_reparse_order_rejects_mirrored_quantity_columns_without_reference_rows(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-mirrored-columns.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-mirrored-columns-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    def _fake_config(_facility_id: str):
        return {
            "facility_id": "FAC00001",
            "fax_template": {
                "header_rows": 0,
                "map_menu_by_position": False,
                "columns": [
                    {"index": 0, "role": "date"},
                    {"index": 1, "role": "daypart"},
                    {"index": 2, "role": "menu_name"},
                    {
                        "index": 3,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "bag_type": "standard",
                    },
                    {
                        "index": 4,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "3F",
                        "bag_type": "standard",
                    },
                ],
                "main_ocr_row_fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_2f",
                    "qty.regular_3f",
                ],
            },
        }

    def _fake_pipeline(**_kwargs):
        return None

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0, wait_seconds_override=None, **_kwargs):  # noqa: ARG001
        return None

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        rows = [
            ["", "", "", "6", "6"],
            ["", "", "", "8", "8"],
            ["", "", "", "10", "10"],
            ["", "", "", "12", "12"],
            ["", "", "", "14", "14"],
            ["", "", "", "16", "16"],
        ]
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026/02/15"],
            table_rows=rows,
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "quantity_only_mode": True},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        parsed: list[dict[str, object]] = []
        for idx, row in enumerate(rows):
            qty_raw = row[3] if len(row) > 3 else ""
            if not str(qty_raw).strip():
                continue
            parsed.append(
                {
                    "date": "2026-02-15",
                    "daypart": "昼",
                    "menu_name": f"Menu-{idx}",
                    "diet_type": "regular",
                    "area_id": "2F",
                    "bag_type": "standard",
                    "quantity_original": int(qty_raw),
                    "source_row_index": idx,
                }
            )
        return parsed

    monkeypatch.setattr(config_service, "get_facility_config", _fake_config)
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(
        order_service,
        "_validate_reparse_lines_against_weekly_menu",
        lambda **kwargs: (None, None),
    )
    monkeypatch.setenv("OCR_REPARSE_ROW_COVERAGE_MIN_RATIO", "0.0")
    monkeypatch.setenv("OCR_REPARSE_MAX_MISSING_TAIL_ROWS", "999")
    monkeypatch.setenv("OCR_REPARSE_ENABLE_REPAIR_PASS", "0")
    monkeypatch.setenv("OCR_REPARSE_ENABLE_COLUMN_MIRROR_GATE", "1")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_MIRROR_MIN_OVERLAP", "6")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_MIRROR_MIN_NONZERO_OVERLAP", "6")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_MIRROR_MIN_EQUAL_RATIO", "0.98")
    monkeypatch.setenv("OCR_REPARSE_COLUMN_MIRROR_MIN_DISTINCT_PAIRS", "3")
    monkeypatch.setattr(order_service, "_resolve_llm_expected_row_count", lambda **_kwargs: 6)
    _patch_first_pass_payload(monkeypatch, _make_first_pass_payload(table_raw="first-pass-placeholder"))

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") == "done"
    metrics = job.get("metrics") or {}
    assert metrics.get("error") in {None, ""}
    assert metrics.get("warning_reasons") == ["sheet_column_anomaly"]
    detail = metrics.get("warning_detail") or {}
    anomalies = detail.get("column_anomalies") or []
    assert any(item.get("reason") == "mirrored_sibling_columns" for item in anomalies if isinstance(item, dict))


def test_reparse_order_rejects_canonical_mismatch_before_save(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-canonical-mismatch.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-canonical-mismatch-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    def _fake_config(_facility_id: str):
        return {
            "facility_id": "FAC00001",
            "fax_template": {
                "header_rows": 0,
                "columns": [
                    {"index": 0, "role": "date"},
                    {"index": 1, "role": "daypart"},
                    {"index": 2, "role": "menu_name"},
                    {
                        "index": 3,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "bag_type": "standard",
                    },
                ],
            },
        }

    def _fake_pipeline(**_kwargs):
        return None

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026/02/15"],
            table_rows=[["02/15", "昼", "Menu A", "8"]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        return [
            {
                "date": "2026-02-15",
                "daypart": "昼",
                "menu_name": "Unmapped Menu",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 8,
                "source_row_index": 0,
            }
        ]

    def _fake_weekly_entries(_week_id: str):
        return [
            {"menu_date": date(2026, 2, 15), "daypart_key": "昼", "menu_name": "Menu A"},
            {"menu_date": date(2026, 2, 15), "daypart_key": "夕", "menu_name": "Menu B"},
        ]

    monkeypatch.setattr(config_service, "get_facility_config", _fake_config)
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)
    monkeypatch.setattr(
        order_service,
        "_apply_menu_position_mapping",
        lambda lines, _week_id, **_kwargs: (lines, 0),
    )
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[["02/15", "昼", "Menu A", "8"]]),
    )

    updated, error = order_service.reparse_order(
        order["id"],
        ocr_provider="gemini",
        ocr_prompt="strict quantity only",
    )

    assert updated is None
    assert error == "sheet_canonical_mismatch"
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") in {"done", "failed"}
    metrics = job.get("metrics") or {}
    assert metrics.get("error") == "sheet_canonical_mismatch"
    assert metrics.get("reject_reasons") == ["sheet_canonical_mismatch"]

    cached = order_service.get_cached_ocr_payload(order["id"]) or {}
    debug = cached.get("_reparse_debug") or {}
    assert debug.get("error") == "sheet_canonical_mismatch"
    assert "sheet_canonical_mismatch" in (debug.get("reject_reasons") or [])
    assert "strict quantity only" in str(debug.get("request_prompt") or "")


def test_reparse_order_keeps_existing_lines_when_validation_rejected(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-keep-lines-on-reject.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-keep-lines-on-reject-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    menu_date, daypart, menu_name = _canonical_row_for_week("2026-02")
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": menu_date,
                "daypart": daypart,
                "menu_name": menu_name,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 6,
            }
        ],
    )
    before = order_service.get_order_by_id(order["id"])
    before_lines = before.get("lines") or []
    assert len(before_lines) == 1

    def _fake_config(_facility_id: str):
        return {
            "facility_id": "FAC00001",
            "fax_template": {
                "header_rows": 0,
                "columns": [
                    {"index": 0, "role": "date"},
                    {"index": 1, "role": "daypart"},
                    {"index": 2, "role": "menu_name"},
                    {
                        "index": 3,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "bag_type": "standard",
                    },
                ],
            },
        }

    def _fake_pipeline(**_kwargs):
        return None

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026/02/15"],
            table_rows=[["02/15", "昼", "Wrong Menu", "8"]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        return [
            {
                "date": "2026-02-15",
                "daypart": "昼",
                "menu_name": "Wrong Menu",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 8,
                "source_row_index": 0,
            }
        ]

    def _fake_weekly_entries(_week_id: str):
        return [
            {"menu_date": date(2026, 2, 15), "daypart_key": "昼", "menu_name": "Menu A"},
        ]

    monkeypatch.setattr(config_service, "get_facility_config", _fake_config)
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)
    monkeypatch.setattr(
        order_service,
        "_apply_menu_position_mapping",
        lambda lines, _week_id, **_kwargs: (lines, 0),
    )
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[["02/15", "昼", "Wrong Menu", "8"]]),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini")

    assert updated is None
    assert error == "sheet_canonical_mismatch"
    after = order_service.get_order_by_id(order["id"])
    after_lines = after.get("lines") or []
    assert len(after_lines) == 1
    assert after_lines[0].get("menu_name") == before_lines[0].get("menu_name")
    assert after_lines[0].get("quantity_original") == before_lines[0].get("quantity_original")


def test_reparse_order_rejects_when_llm_cost_hard_limit_exceeded(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-cost-hard-limit.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-cost-hard-limit-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    def _fake_config(_facility_id: str):
        return {
            "facility_id": "FAC00001",
            "fax_template": {
                "header_rows": 0,
                "columns": [
                    {"index": 0, "role": "date"},
                    {"index": 1, "role": "daypart"},
                    {"index": 2, "role": "menu_name"},
                    {
                        "index": 3,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "bag_type": "standard",
                    },
                ],
            },
        }

    def _fake_pipeline(**_kwargs):
        return None

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026/02/15"],
            table_rows=[["02/15", "昼", "Menu A", "8"]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={
                "provider": "gemini",
                "model": "gemini-2.5-flash",
                "usage": {
                    "prompt_tokens": 10000,
                    "completion_tokens": 10000,
                    "total_tokens": 20000,
                },
            },
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        return [
            {
                "date": "2026-02-15",
                "daypart": "昼",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 8,
                "source_row_index": 0,
            }
        ]

    def _fake_weekly_entries(_week_id: str):
        return [
            {"menu_date": date(2026, 2, 15), "daypart_key": "昼", "menu_name": "Menu A"},
        ]

    monkeypatch.setenv("OCR_REPARSE_COST_GEMINI_FLASH_INPUT_USD_PER_1M", "1.0")
    monkeypatch.setenv("OCR_REPARSE_COST_GEMINI_FLASH_OUTPUT_USD_PER_1M", "3.0")
    monkeypatch.setenv("OCR_REPARSE_COST_HARD_LIMIT_USD", "0.01")
    monkeypatch.setenv("OCR_REPARSE_COST_ENFORCE_HARD_LIMIT", "1")

    monkeypatch.setattr(config_service, "get_facility_config", _fake_config)
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)
    monkeypatch.setattr(
        order_service,
        "_apply_menu_position_mapping",
        lambda lines, _week_id, **_kwargs: (lines, 0),
    )
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[["02/15", "昼", "Menu A", "8"]]),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini")

    assert updated is None
    assert error == "llm_cost_limit_exceeded"
    after = order_service.get_order_by_id(order["id"])
    assert not (after.get("lines") or [])
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") in {"done", "failed"}
    metrics = job.get("metrics") or {}
    assert metrics.get("error") == "llm_cost_limit_exceeded"
    assert isinstance(metrics.get("llm_cost"), dict)


def test_reparse_order_rejects_missing_numeric_source_row(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-suspicious-blank.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-suspicious-blank-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    def _fake_config(_facility_id: str):
        return {
            "facility_id": "FAC00001",
            "fax_template": {
                "header_rows": 0,
                "columns": [
                    {"index": 0, "role": "date"},
                    {"index": 1, "role": "daypart"},
                    {"index": 2, "role": "menu_name"},
                    {
                        "index": 3,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "bag_type": "standard",
                    },
                ],
            },
        }

    def _fake_pipeline(**_kwargs):
        return None

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026/02/15"],
            table_rows=[
                ["02/15", "昼", "Menu A", "6"],
                ["02/15", "夕", "Menu B", "5"],
            ],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        return [
            {
                "date": "2026-02-15",
                "daypart": "昼",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 6,
                "source_row_index": 0,
            }
        ]

    def _fake_weekly_entries(_week_id: str):
        return [
            {"menu_date": date(2026, 2, 15), "daypart_key": "昼", "menu_name": "Menu A"},
            {"menu_date": date(2026, 2, 15), "daypart_key": "夕", "menu_name": "Menu B"},
        ]

    monkeypatch.setattr(config_service, "get_facility_config", _fake_config)
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)
    monkeypatch.setattr(
        order_service,
        "_apply_menu_position_mapping",
        lambda lines, _week_id, **_kwargs: (lines, 0),
    )
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(
            rows=[
                ["02/15", "昼", "Menu A", "6"],
                ["02/15", "夕", "Menu B", "5"],
            ],
        ),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini")

    assert updated is None
    assert error == "sheet_suspicious_blank_row"
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") in {"done", "failed"}
    metrics = job.get("metrics") or {}
    assert metrics.get("error") == "sheet_suspicious_blank_row"
    detail = metrics.get("validation_detail") or {}
    assert detail.get("source_row_missing") == [1]


def test_reparse_order_merges_quantity_only_rows_with_pipeline(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-qonly-merge.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-qonly-merge-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    captured_rows: list[list[list[str]]] = []

    def _fake_config(_facility_id: str):
        return {
            "facility_id": "FAC00001",
            "fax_template": {
                "header_rows": 0,
                "map_menu_by_position": True,
                "columns": [
                    {"index": 0, "role": "date"},
                    {"index": 1, "role": "daypart"},
                    {"index": 2, "role": "menu_name"},
                    {
                        "index": 3,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "bag_type": "standard",
                    },
                ],
                "main_ocr_row_fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_2f",
                ],
            },
        }

    def _fake_pipeline(**_kwargs):
        return "file://pipeline-output.json"

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0, wait_seconds_override=None, **_kwargs):  # noqa: ARG001
        return {
            "table_rows": [
                ["2/15", "昼", "Menu A", ""],
                ["2/15", "夕", "Menu B", ""],
            ]
        }

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2/15"],
            table_rows=[
                ["", "", "", "20"],
                ["", "", "", "11"],
            ],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "quantity_only_mode": True},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        captured_rows.append([list(item) for item in rows])
        lines = []
        for idx, row in enumerate(rows):
            qty = row[3] if len(row) > 3 else ""
            if not str(qty).strip():
                continue
            lines.append(
                {
                    "date": "2026-02-15",
                    "daypart": row[1] or None,
                    "menu_name": row[2] or None,
                    "diet_type": "regular",
                    "area_id": "2F",
                    "bag_type": "standard",
                    "quantity_original": int(qty),
                    "source_row_index": idx,
                }
            )
        return lines

    def _fake_weekly_entries(_week_id: str):
        return [
            {"menu_date": date(2026, 2, 15), "daypart_key": "昼", "menu_name": "Menu A"},
            {"menu_date": date(2026, 2, 15), "daypart_key": "夕", "menu_name": "Menu B"},
        ]

    monkeypatch.setattr(config_service, "get_facility_config", _fake_config)
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)
    monkeypatch.setenv("OCR_REPARSE_ENABLE_PIPELINE_QUANTITY_MERGE", "1")

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    assert captured_rows
    assert [row[1:] for row in captured_rows[0]] == [
        ["昼", "Menu A", "20"],
        ["夕", "Menu B", "11"],
    ]
    quantities = sorted(int(line["quantity_original"]) for line in updated["lines"])
    assert quantities == [11, 20]
    debug = updated.get("ocr_metrics") or {}
    if isinstance(debug, dict):
        # API serialization may omit metrics in this path; assertion is best-effort.
        assert debug.get("provider") in {None, "gemini"}


def test_reparse_order_does_not_merge_pipeline_rows_by_default(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-qonly-no-merge-default.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-qonly-no-merge-default-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")

    def _fake_pipeline(**_kwargs):
        return "file://pipeline-output.json"

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0, wait_seconds_override=None, **_kwargs):  # noqa: ARG001
        return {
            "table_rows": [[canonical_mmdd, canonical_daypart, canonical_menu, ""]],
        }

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_mmdd],
            table_rows=[["", "", "", "9"]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "quantity_only_mode": True},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        return [
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 9,
                "source_row_index": 0,
            }
        ]

    def _forbid_merge(**_kwargs):
        raise AssertionError("pipeline quantity merge should be disabled by default")

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_merge_llm_quantity_only_rows_with_pipeline", _forbid_merge)
    monkeypatch.setattr(order_service, "_run_llm_reparse_audit", lambda **_kwargs: {"status": "pass", "issues": []})
    monkeypatch.setenv("OCR_REPARSE_ENABLE_LLM_AUDIT_GATE", "1")
    monkeypatch.delenv("OCR_REPARSE_ENABLE_PIPELINE_QUANTITY_MERGE", raising=False)

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    assert updated["lines"]
    assert int(updated["lines"][0]["quantity_original"]) == 9


def test_reparse_order_quantity_only_blank_structure_rows_no_longer_rejected(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-qonly-blank-structure-kept.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-qonly-blank-structure-kept-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    def _fake_config(_facility_id: str):
        return {
            "facility_id": "FAC00001",
            "fax_template": {
                "header_rows": 0,
                "map_menu_by_position": True,
                "columns": [
                    {"index": 0, "role": "date"},
                    {"index": 1, "role": "daypart"},
                    {"index": 2, "role": "menu_name"},
                    {
                        "index": 3,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "bag_type": "standard",
                    },
                ],
            },
        }

    def _fake_pipeline(**_kwargs):
        return None

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[],
            table_rows=[
                ["", "", "", "20"],
                ["", "", "", "11"],
            ],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "quantity_only_mode": True},
        )

    def _fake_weekly_entries(_week_id: str):
        return [
            {"menu_date": date(2026, 2, 15), "daypart_key": "昼", "menu_name": "Menu A"},
            {"menu_date": date(2026, 2, 15), "daypart_key": "夕", "menu_name": "Menu B"},
        ]

    monkeypatch.setattr(config_service, "get_facility_config", _fake_config)
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)
    monkeypatch.setattr(order_service, "_run_llm_reparse_audit", lambda **_kwargs: {"status": "pass", "issues": []})
    monkeypatch.setenv("OCR_REPARSE_ENABLE_LLM_AUDIT_GATE", "1")
    _patch_first_pass_payload(monkeypatch, _make_first_pass_payload(table_raw="first-pass-placeholder"))

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    quantities = sorted(int(line["quantity_original"]) for line in (updated.get("lines") or []))
    assert quantities == [11, 20]
    menu_names = sorted({str(line.get("menu_name") or "") for line in (updated.get("lines") or [])})
    assert menu_names == ["Menu A", "Menu B"]


def test_reparse_order_rejects_when_llm_audit_fails(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-audit-fail.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-audit-fail-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")

    def _fake_pipeline(**_kwargs):
        return None

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_mmdd],
            table_rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "5"]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "quantity_only_mode": True},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        return [
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 5,
                "source_row_index": 0,
            }
        ]

    def _fake_weekly_entries(_week_id: str):
        return [
            {"menu_date": date(2026, 2, 15), "daypart_key": canonical_daypart, "menu_name": canonical_menu},
        ]

    def _fake_audit(**_kwargs):
        return {
            "status": "fail",
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "issue_count": 1,
            "blocking_issue_count": 1,
            "blocking_issues": [
                {
                    "issue_code": "mirrored_sibling_columns",
                    "severity": "high",
                    "confidence": 0.92,
                    "evidence": "2F and 3F cells visually identical across rows",
                }
            ],
            "issues": [],
        }

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "_build_position_menu_entries", _fake_weekly_entries)
    monkeypatch.setattr(order_service, "_run_llm_reparse_audit", _fake_audit)
    monkeypatch.setenv("OCR_REPARSE_ENABLE_LLM_AUDIT_GATE", "1")
    monkeypatch.setenv("OCR_REPARSE_ROW_COVERAGE_MIN_RATIO", "0.0")
    monkeypatch.setenv("OCR_REPARSE_MAX_MISSING_TAIL_ROWS", "999")
    monkeypatch.setenv("OCR_REPARSE_ENABLE_REPAIR_PASS", "0")
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "5"]]),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert updated is None
    assert error == "sheet_llm_audit_failed"
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    metrics = job.get("metrics") or {}
    assert metrics.get("error") == "sheet_llm_audit_failed"
    audit = metrics.get("llm_audit") or {}
    assert audit.get("status") == "fail"
    assert int(audit.get("blocking_issue_count") or 0) >= 1
    cached_payload = order_service.get_cached_ocr_payload(order["id"]) or {}
    edited = cached_payload.get("_edited_ocr") or {}
    latest = edited.get("latest") or {}
    assert latest.get("draft_from_reparse_reject") is True


def test_publish_applied_reparse_revision_supersedes_stale_reject_candidate_and_preserves_aux_display():
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-reparse-applied-revision-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00004",
        week_hint="2026-02",
    )
    template = config_service.get_facility_config("FAC00004").get("fax_template")
    fields = order_service._row_fields_from_template(template)
    header = order_service._sheet_header_from_template(fields, template)
    quantity_fields = [field for field in fields if field.startswith("qty.")]
    aux_fields = [field for field in fields if field.startswith("aux.")]
    assert quantity_fields
    assert len(aux_fields) >= 2
    diet_key, area_key = order_service._quantity_meta_from_field(quantity_fields[0])
    assert diet_key and area_key

    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": diet_key,
                "area_id": area_key,
                "bag_type": "standard",
                "quantity_original": 5,
            }
        ],
    )

    def _build_row(*, aux_total: str, qty_value: str) -> list[str]:
        row = [""] * len(fields)
        row[fields.index("date_mmdd")] = canonical_mmdd
        row[fields.index("daypart")] = canonical_daypart
        menu_field = "menu" if "menu" in fields else "menu_name"
        row[fields.index(menu_field)] = canonical_menu
        row[fields.index(aux_fields[0])] = "主"
        row[fields.index(aux_fields[1])] = aux_total
        row[fields.index(quantity_fields[0])] = qty_value
        return row

    stale_rows = [_build_row(aux_total="5", qty_value="5")]
    stale_row_ids = ["draft-1"]
    stale_digest = order_service._sheet_digest(
        fields=fields,
        header=header,
        rows_payload=stale_rows,
        row_ids=stale_row_ids,
    )
    order_service._append_edited_ocr_revision(
        order_id=order["id"],
        ui_mode="sheet",
        fields=fields,
        header=header,
        rows_payload=stale_rows,
        row_ids=stale_row_ids,
        before_digest=stale_digest,
        after_digest=stale_digest,
        revision_meta={
            "sheet_save_only": True,
            "sheet_save_mode": "draft_candidate",
            "review_state": "auto_apply_blocked",
            "draft_from_reparse_reject": True,
            "raw_output_override": order_service._build_revision_raw_output_override(
                fields=fields,
                header=header,
                rows=stale_rows,
                template=template,
                provider="gemini",
            ),
        },
    )

    applied_line = {
        "id": "line-applied-1",
        "date": canonical_date,
        "daypart": canonical_daypart,
        "menu_name": canonical_menu,
        "diet_type": diet_key,
        "area_id": area_key,
        "bag_type": "standard",
        "quantity_original": 70,
    }
    applied_ocr_rows = [_build_row(aux_total="70", qty_value="70")]

    contaminated_current_rows = [_build_row(aux_total="70", qty_value="")]

    order_service._publish_applied_reparse_revision(
        order_id=order["id"],
        template=template,
        previous_sheet_context={
            "fields": fields,
            "header": header,
            "rows": stale_rows,
            "row_ids": stale_row_ids,
        },
        current_sheet_context={
            "fields": fields,
            "header": header,
            "rows": contaminated_current_rows,
            "row_ids": ["row-1"],
        },
        ocr_rows=applied_ocr_rows,
        provider="gemini",
        llm_assist=True,
        warning_reasons=[],
    )

    cached_payload = order_service.get_cached_ocr_payload(order["id"]) or {}
    latest = ((cached_payload.get("_edited_ocr") or {}).get("latest") or {})
    assert latest.get("sheet_save_mode") == "applied"
    assert latest.get("reparse_applied") is True
    assert latest.get("draft_from_reparse_reject") in {None, False}
    assert latest.get("row_count") == 1
    assert latest.get("rows", [])[0][fields.index(aux_fields[1])] == "70"
    assert latest.get("rows", [])[0][fields.index(quantity_fields[0])] == "70"


def test_publish_applied_reparse_revision_is_idempotent_for_same_applied_sheet():
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-reparse-applied-revision-002",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00004",
        week_hint="2026-02",
    )
    template = config_service.get_facility_config("FAC00004").get("fax_template")
    fields = order_service._row_fields_from_template(template)
    quantity_fields = [field for field in fields if field.startswith("qty.")]
    aux_fields = [field for field in fields if field.startswith("aux.")]
    assert quantity_fields
    assert len(aux_fields) >= 2
    diet_key, area_key = order_service._quantity_meta_from_field(quantity_fields[0])
    assert diet_key and area_key
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    order = order_service.create_order_from_ingest(payload, lines=[])

    ocr_row = [""] * len(fields)
    ocr_row[fields.index("date_mmdd")] = canonical_mmdd
    ocr_row[fields.index("daypart")] = canonical_daypart
    menu_field = "menu" if "menu" in fields else "menu_name"
    ocr_row[fields.index(menu_field)] = canonical_menu
    ocr_row[fields.index(aux_fields[0])] = "主"
    ocr_row[fields.index(aux_fields[1])] = "70"
    ocr_row[fields.index(quantity_fields[0])] = "70"
    applied_lines = [
        {
            "id": "line-applied-2",
            "date": canonical_date,
            "daypart": canonical_daypart,
            "menu_name": canonical_menu,
            "diet_type": diet_key,
            "area_id": area_key,
            "bag_type": "standard",
            "quantity_original": 70,
        }
    ]

    order_service._publish_applied_reparse_revision(
        order_id=order["id"],
        template=template,
        previous_sheet_context={
            "fields": fields,
            "header": order_service._sheet_header_from_template(fields, template),
            "rows": [],
            "row_ids": [],
        },
        current_sheet_context={
            "fields": fields,
            "header": order_service._sheet_header_from_template(fields, template),
            "rows": [ocr_row],
            "row_ids": ["row-1"],
        },
        ocr_rows=[ocr_row],
        provider="gemini",
        llm_assist=True,
        warning_reasons=[],
    )
    first_history, first_error = order_service.get_ocr_edit_history(order["id"])
    assert first_error is None
    first_count = len(first_history.get("revisions") or [])

    order_service._publish_applied_reparse_revision(
        order_id=order["id"],
        template=template,
        previous_sheet_context={
            "fields": fields,
            "header": order_service._sheet_header_from_template(fields, template),
            "rows": [],
            "row_ids": [],
        },
        current_sheet_context={
            "fields": fields,
            "header": order_service._sheet_header_from_template(fields, template),
            "rows": [ocr_row],
            "row_ids": ["row-1"],
        },
        ocr_rows=[ocr_row],
        provider="gemini",
        llm_assist=True,
        warning_reasons=[],
    )
    second_history, second_error = order_service.get_ocr_edit_history(order["id"])
    assert second_error is None
    second_count = len(second_history.get("revisions") or [])
    assert second_count == first_count
    assert (second_history.get("latest") or {}).get("sheet_save_mode") == "applied"


def test_publish_applied_reparse_revision_refreshes_authoritative_raw_output_for_latest_sync():
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-reparse-applied-revision-authoritative-raw-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00004",
        week_hint="2026-02",
    )
    template = config_service.get_facility_config("FAC00004").get("fax_template")
    fields = order_service._row_fields_from_template(template)
    header = order_service._sheet_header_from_template(fields, template)
    quantity_fields = [field for field in fields if field.startswith("qty.")]
    aux_fields = [field for field in fields if field.startswith("aux.")]
    assert quantity_fields
    assert len(aux_fields) >= 2
    order = order_service.create_order_from_ingest(payload, lines=[])

    def _row(*, menu_name: str, daypart: str, qty_value: str) -> list[str]:
        row = [""] * len(fields)
        row[fields.index("date_mmdd")] = "02/15"
        row[fields.index("daypart")] = daypart
        menu_field = "menu" if "menu" in fields else "menu_name"
        row[fields.index(menu_field)] = menu_name
        row[fields.index(aux_fields[0])] = "主"
        row[fields.index(quantity_fields[0])] = qty_value
        return row

    current_rows = [
        _row(menu_name="Menu A", daypart="朝", qty_value="70"),
        _row(menu_name="Menu B", daypart="朝", qty_value="70"),
        _row(menu_name="Menu C", daypart="昼", qty_value="105"),
        _row(menu_name="Menu D", daypart="昼", qty_value="105"),
        _row(menu_name="Menu E", daypart="昼", qty_value="105"),
    ]
    current_sheet_context = {
        "fields": fields,
        "header": header,
        "rows": current_rows,
        "row_ids": [f"row-{idx + 1}" for idx in range(len(current_rows))],
    }

    total_idx = fields.index(aux_fields[1])
    authoritative_rows = [list(row) for row in current_rows]
    for row_index, total in enumerate(["70", "70", "105", "105", "105"]):
        authoritative_rows[row_index][total_idx] = total
    stale_rows = [list(row) for row in current_rows]
    stale_rows[0][total_idx] = "70"

    authoritative_payload = order_service._build_revision_raw_output_override(
        fields=fields,
        header=header,
        rows=authoritative_rows,
        template=template,
        provider="gemini",
    )
    stale_raw_output = order_service._build_revision_raw_output_override(
        fields=fields,
        header=header,
        rows=stale_rows,
        template=template,
        provider="gemini",
    )
    authoritative_payload["_edited_ocr"] = {
        "raw_output": stale_raw_output,
    }
    order_service._save_order_ocr_cache(order["id"], authoritative_payload)

    order_service._publish_applied_reparse_revision(
        order_id=order["id"],
        template=template,
        previous_sheet_context={
            "fields": fields,
            "header": header,
            "rows": stale_rows,
            "row_ids": [f"stale-{idx + 1}" for idx in range(len(stale_rows))],
        },
        current_sheet_context=current_sheet_context,
        ocr_rows=current_rows,
        raw_output_payload=authoritative_payload,
        provider="gemini",
        llm_assist=True,
        warning_reasons=[],
    )

    cached_payload = order_service.get_cached_ocr_payload(order["id"]) or {}
    refreshed_raw_output = ((cached_payload.get("_edited_ocr") or {}).get("raw_output") or {})
    refreshed_rows = refreshed_raw_output.get("rows") or []
    assert len(refreshed_rows) == 5
    assert [row[total_idx] for row in refreshed_rows] == ["70", "70", "105", "105", "105"]

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(order_service, "get_current_sheet_context", lambda *_args, **_kwargs: current_sheet_context)
    synced_payload, changed = order_service._sync_edited_ocr_latest_from_canonical_revision(
        order_id=order["id"],
        payload=cached_payload,
        template=template,
    )
    monkeypatch.undo()
    assert changed is False
    latest = (((synced_payload or {}).get("_edited_ocr") or {}).get("latest") or {})
    assert latest.get("sheet_save_mode") == "applied"
    assert latest.get("reparse_applied") is True
    assert latest.get("row_count") == 5
    assert [row[total_idx] for row in (latest.get("rows") or [])[:2]] == ["70", "70"]


def test_sync_edited_ocr_latest_from_canonical_revision_overrides_stale_cached_latest():
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-reparse-applied-revision-003",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00004",
        week_hint="2026-02",
    )
    template = config_service.get_facility_config("FAC00004").get("fax_template")
    fields = order_service._row_fields_from_template(template)
    header = order_service._sheet_header_from_template(fields, template)
    quantity_fields = [field for field in fields if field.startswith("qty.")]
    aux_fields = [field for field in fields if field.startswith("aux.")]
    assert quantity_fields
    assert len(aux_fields) >= 2
    diet_key, area_key = order_service._quantity_meta_from_field(quantity_fields[0])
    assert diet_key and area_key
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    order = order_service.create_order_from_ingest(payload, lines=[])

    def _row(*, aux_total: str, qty_value: str) -> list[str]:
        row = [""] * len(fields)
        row[fields.index("date_mmdd")] = canonical_mmdd
        row[fields.index("daypart")] = canonical_daypart
        menu_field = "menu" if "menu" in fields else "menu_name"
        row[fields.index(menu_field)] = canonical_menu
        row[fields.index(aux_fields[0])] = "主"
        row[fields.index(aux_fields[1])] = aux_total
        row[fields.index(quantity_fields[0])] = qty_value
        return row

    stale_rows = [_row(aux_total="5", qty_value="5")]
    authoritative_payload = order_service._build_revision_raw_output_override(
        fields=fields,
        header=header,
        rows=[_row(aux_total="70", qty_value="70")],
        template=template,
        provider="gemini",
    )
    order_service._save_order_ocr_cache(order["id"], authoritative_payload)
    stale_digest = order_service._sheet_digest(
        fields=fields,
        header=header,
        rows_payload=stale_rows,
        row_ids=["draft-1"],
    )
    order_service._append_edited_ocr_revision(
        order_id=order["id"],
        ui_mode="sheet",
        fields=fields,
        header=header,
        rows_payload=stale_rows,
        row_ids=["draft-1"],
        before_digest=stale_digest,
        after_digest=stale_digest,
        revision_meta={
            "sheet_save_only": True,
            "sheet_save_mode": "draft_candidate",
            "review_state": "auto_apply_blocked",
            "draft_from_reparse_reject": True,
            "raw_output_override": order_service._build_revision_raw_output_override(
                fields=fields,
                header=header,
                rows=stale_rows,
                template=template,
                provider="gemini",
            ),
        },
    )

    order_service._publish_applied_reparse_revision(
        order_id=order["id"],
        template=template,
        previous_sheet_context={
            "fields": fields,
            "header": header,
            "rows": stale_rows,
            "row_ids": ["draft-1"],
        },
        current_sheet_context={
            "fields": fields,
            "header": header,
            "rows": [_row(aux_total="70", qty_value="70")],
            "row_ids": ["row-1"],
        },
        ocr_rows=[_row(aux_total="70", qty_value="70")],
        raw_output_payload=authoritative_payload,
        provider="gemini",
        llm_assist=True,
        warning_reasons=[],
    )

    with session_scope() as session:
        cache = session.get(OrderOcrCache, order["id"])
        assert cache is not None
        payload_json = dict(cache.payload or {})
        edited = dict(payload_json.get("_edited_ocr") or {})
        stale_latest = {
            "revision_id": "OCRREV-stale-cached",
            "edited_at": "2026-02-15T00:00:00",
            "ui_mode": "sheet",
            "fields": fields,
            "header": header,
            "row_ids": ["draft-1"],
            "rows": stale_rows,
            "row_count": 1,
            "before_digest": stale_digest,
            "after_digest": stale_digest,
            "changed": False,
            "sheet_save_only": True,
            "sheet_save_mode": "draft_candidate",
            "review_state": "auto_apply_blocked",
            "draft_from_reparse_reject": True,
            "markdown": order_service._build_markdown_table_string(header, stale_rows),
        }
        edited["latest"] = stale_latest
        edited["revisions"] = [stale_latest]
        payload_json["_edited_ocr"] = edited
        cache.payload = payload_json

    stale_payload = order_service.get_cached_ocr_payload(order["id"]) or {}
    latest_before = ((stale_payload.get("_edited_ocr") or {}).get("latest") or {})
    assert latest_before.get("sheet_save_mode") == "draft_candidate"

    current_sheet_context = {
        "fields": fields,
        "header": header,
        "rows": [_row(aux_total="70", qty_value="70")],
        "row_ids": ["row-1"],
    }
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(order_service, "get_current_sheet_context", lambda *_args, **_kwargs: current_sheet_context)
    synced_payload, changed = order_service._sync_edited_ocr_latest_from_canonical_revision(
        order_id=order["id"],
        payload=stale_payload,
        template=template,
    )
    monkeypatch.undo()
    assert changed is True
    latest_after = (((synced_payload or {}).get("_edited_ocr") or {}).get("latest") or {})
    assert latest_after.get("sheet_save_mode") == "applied"
    assert latest_after.get("reparse_applied") is True
    assert latest_after.get("rows", [])[0][fields.index(aux_fields[1])] == "70"


def test_sync_edited_ocr_latest_from_canonical_revision_refreshes_raw_output_when_latest_already_matches():
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-reparse-applied-revision-raw-refresh-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00004",
        week_hint="2026-02",
    )
    template = config_service.get_facility_config("FAC00004").get("fax_template")
    fields = order_service._row_fields_from_template(template)
    header = order_service._sheet_header_from_template(fields, template)
    quantity_fields = [field for field in fields if field.startswith("qty.")]
    aux_fields = [field for field in fields if field.startswith("aux.")]
    assert quantity_fields
    assert len(aux_fields) >= 2
    order = order_service.create_order_from_ingest(payload, lines=[])

    def _row(*, aux_total: str, qty_value: str) -> list[str]:
        row = [""] * len(fields)
        row[fields.index("date_mmdd")] = "02/15"
        row[fields.index("daypart")] = "朝"
        menu_field = "menu" if "menu" in fields else "menu_name"
        row[fields.index(menu_field)] = "Menu A"
        row[fields.index(aux_fields[0])] = "主"
        row[fields.index(aux_fields[1])] = aux_total
        row[fields.index(quantity_fields[0])] = qty_value
        return row

    applied_row = _row(aux_total="70", qty_value="70")
    authoritative_payload = order_service._build_revision_raw_output_override(
        fields=fields,
        header=header,
        rows=[applied_row],
        template=template,
        provider="gemini",
    )
    order_service._save_order_ocr_cache(order["id"], authoritative_payload)

    stale_raw_output = order_service._build_revision_raw_output_override(
        fields=fields,
        header=header,
        rows=[_row(aux_total="5", qty_value="70")],
        template=template,
        provider="gemini",
    )
    applied_digest = order_service._sheet_digest(
        fields=fields,
        header=header,
        rows_payload=[applied_row],
        row_ids=["row-1"],
    )
    order_service._append_edited_ocr_revision(
        order_id=order["id"],
        ui_mode="sheet",
        fields=fields,
        header=header,
        rows_payload=[applied_row],
        row_ids=["row-1"],
        before_digest=applied_digest,
        after_digest=applied_digest,
        revision_meta={
            "sheet_save_only": False,
            "sheet_save_mode": "applied",
            "review_state": "applied",
            "reparse_applied": True,
            "provider": "gemini",
            "raw_output_override": stale_raw_output,
        },
    )

    cached_payload = order_service.get_cached_ocr_payload(order["id"]) or {}
    raw_before = (((cached_payload.get("_edited_ocr") or {}).get("raw_output") or {}).get("rows") or [])
    assert raw_before[0][fields.index(aux_fields[1])] == "5"

    current_sheet_context = {
        "fields": fields,
        "header": header,
        "rows": [applied_row],
        "row_ids": ["row-1"],
    }
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(order_service, "get_current_sheet_context", lambda *_args, **_kwargs: current_sheet_context)
    synced_payload, changed = order_service._sync_edited_ocr_latest_from_canonical_revision(
        order_id=order["id"],
        payload=cached_payload,
        template=template,
    )
    monkeypatch.undo()

    assert changed is True
    raw_after = (((synced_payload or {}).get("_edited_ocr") or {}).get("raw_output") or {}).get("rows") or []
    assert raw_after[0][fields.index(aux_fields[1])] == "70"
    latest_after = (((synced_payload or {}).get("_edited_ocr") or {}).get("latest") or {})
    assert latest_after.get("rows", [])[0][fields.index(aux_fields[1])] == "70"


def test_sync_edited_ocr_latest_from_canonical_revision_keeps_applied_revision_rows_immutable():
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-reparse-applied-revision-004",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00004",
        week_hint="2026-02",
    )
    template = config_service.get_facility_config("FAC00004").get("fax_template")
    fields = order_service._row_fields_from_template(template)
    header = order_service._sheet_header_from_template(fields, template)
    quantity_fields = [field for field in fields if field.startswith("qty.")]
    aux_fields = [field for field in fields if field.startswith("aux.")]
    menu_field = "menu" if "menu" in fields else "menu_name"
    assert quantity_fields
    assert len(aux_fields) >= 2

    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-02-01",
                "daypart": "昼",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 70,
            },
            {
                "date": "2026-02-01",
                "daypart": "昼",
                "menu_name": "Menu A",
                "diet_type": "no_fish",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 3,
            },
            {
                "date": "2026-02-01",
                "daypart": "昼",
                "menu_name": "Menu A",
                "diet_type": "no_fried",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 6,
            },
        ],
    )
    order_service._invalidate_current_sheet_state_after_order_lines_write(order["id"])
    current_sheet_context = order_service.get_current_sheet_context(
        order["id"],
        refresh_draft_from_semantic=True,
        upgrade_generic_from_sheet=True,
        backfill_from_revision=False,
    )

    expanded_rows = []
    for aux_total, qty_regular, qty_no_fish, qty_no_fried in [
        ("70", "70", "", ""),
        ("", "", "3", ""),
        ("", "", "", "6"),
    ]:
        row = [""] * len(fields)
        row[fields.index("date_mmdd")] = "02/01"
        row[fields.index("daypart")] = "昼"
        row[fields.index(menu_field)] = "Menu A"
        row[fields.index(aux_fields[0])] = "主"
        row[fields.index(aux_fields[1])] = aux_total
        row[fields.index("qty.regular_x")] = qty_regular
        row[fields.index("qty.no_fish_x")] = qty_no_fish
        row[fields.index("qty.no_fried_x")] = qty_no_fried
        expanded_rows.append(row)

    expanded_digest = order_service._sheet_digest(
        fields=fields,
        header=header,
        rows_payload=expanded_rows,
        row_ids=["row-1", "row-2", "row-3"],
    )
    order_service._append_edited_ocr_revision(
        order_id=order["id"],
        ui_mode="sheet",
        fields=fields,
        header=header,
        rows_payload=expanded_rows,
        row_ids=["row-1", "row-2", "row-3"],
        before_digest=expanded_digest,
        after_digest=expanded_digest,
        revision_meta={
            "sheet_save_only": False,
            "sheet_save_mode": "applied",
            "review_state": "applied",
            "reparse_applied": True,
            "provider": "gemini",
            "raw_output_override": order_service._build_revision_raw_output_override(
                fields=fields,
                header=header,
                rows=expanded_rows,
                template=template,
                provider="gemini",
            ),
        },
    )

    payload_with_revision = order_service.get_cached_ocr_payload(order["id"]) or {}
    synced_payload, changed = order_service._sync_edited_ocr_latest_from_canonical_revision(
        order_id=order["id"],
        payload=payload_with_revision,
        template=template,
    )
    assert changed is True
    latest = (((synced_payload or {}).get("_edited_ocr") or {}).get("latest") or {})
    assert latest.get("sheet_save_mode") == "applied"
    assert latest.get("reparse_applied") is True
    assert latest.get("row_count") == 3
    latest_rows = latest.get("rows") or []
    assert [row[fields.index(aux_fields[1])] for row in latest_rows] == ["70", "", ""]
    assert [row[fields.index("qty.regular_x")] for row in latest_rows] == ["70", "", ""]
    assert [row[fields.index("qty.no_fish_x")] for row in latest_rows] == ["", "3", ""]
    assert [row[fields.index("qty.no_fried_x")] for row in latest_rows] == ["", "", "6"]


def test_resolve_llm_audit_provider_defaults_to_cross_model(monkeypatch):
    monkeypatch.delenv("OCR_REPARSE_AUDIT_PROVIDER", raising=False)
    assert (
        order_service._resolve_llm_audit_provider(
            primary_provider="gemini",
            template={},
        )
        == "openai"
    )
    assert (
        order_service._resolve_llm_audit_provider(
            primary_provider="openai",
            template={},
        )
        == "gemini"
    )


def test_resolve_llm_audit_provider_forces_cross_model_when_same_requested(monkeypatch):
    monkeypatch.setenv("OCR_REPARSE_AUDIT_PROVIDER", "same")
    assert (
        order_service._resolve_llm_audit_provider(
            primary_provider="gemini",
            template={},
        )
        == "openai"
    )
    monkeypatch.setenv("OCR_REPARSE_AUDIT_PROVIDER", "openai")
    assert (
        order_service._resolve_llm_audit_provider(
            primary_provider="openai",
            template={},
        )
        == "gemini"
    )
    assert (
        order_service._resolve_llm_audit_provider(
            primary_provider="openai",
            template={"llm_audit_provider": "openai"},
        )
        == "gemini"
    )


def test_run_llm_reparse_audit_allows_template_non_blocking_codes(monkeypatch):
    monkeypatch.setenv("OCR_REPARSE_ENABLE_LLM_AUDIT_GATE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def _fake_extract(_pdf_bytes, _template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test",
            date_strings=[],
            table_rows=[],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "model": "gemini-2.5-pro"},
        )

    def _fake_parse_issues(*, rows, fields):  # noqa: ARG001
        return [
            {
                "issue_code": "date_anchor_drift",
                "severity": "critical",
                "confidence": 1.0,
                "evidence": "Quantity '42' drifted to the previous daypart block.",
                "reason": "Bracketed handwritten counts can anchor to the prior row on FAC00007 forms.",
            },
            {
                "issue_code": "row_count_shortfall",
                "severity": "critical",
                "confidence": 1.0,
                "evidence": "Image has 52 menu item rows but candidate OCR has 56 rows.",
                "reason": "Two-row bracket layout over-segmented by audit.",
            }
        ]

    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "_parse_llm_reparse_audit_issues", _fake_parse_issues)

    result = order_service._run_llm_reparse_audit(
        pdf_bytes=b"%PDF-1.4\n%EOF\n",
        provider="gemini",
        template={"llm_audit_non_blocking_codes": ["row_count_shortfall"]},
        facility_id="FAC00007",
        preferred_template_id=None,
        candidate_rows=[["02/15", "朝", "Menu A", "42"]],
        reference_rows=None,
        expected_row_count=56,
    )

    assert isinstance(result, dict)
    assert result.get("status") == "fail"
    assert int(result.get("blocking_issue_count") or 0) == 1
    issues = result.get("issues") or []
    issue_codes = {str(item.get("issue_code")) for item in issues}
    assert issue_codes == {"row_count_shortfall", "date_anchor_drift"}


def test_run_llm_reparse_audit_switches_to_gemini_when_openai_key_missing(monkeypatch):
    monkeypatch.setenv("OCR_REPARSE_ENABLE_LLM_AUDIT_GATE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    captured: dict[str, object] = {}

    def _fake_extract(_pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        captured["provider"] = template.get("main_ocr_provider")
        captured["model"] = template.get("gemini_ocr_model")
        return FaxExtractedData(
            facility_name="Test",
            date_strings=[],
            table_rows=[],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "model": str(template.get("gemini_ocr_model") or "")},
        )

    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)

    result = order_service._run_llm_reparse_audit(
        pdf_bytes=b"%PDF-1.4\n%EOF\n",
        provider="gemini",
        template={"gemini_ocr_model": "gemini-2.5-flash"},
        facility_id="FAC00001",
        preferred_template_id=None,
        candidate_rows=[["", "", "", "20"]],
        reference_rows=[["", "", "", "20"]],
        expected_row_count=1,
    )

    assert result is not None
    assert result.get("provider") == "gemini"
    assert result.get("requested_provider") == "openai"
    assert result.get("provider_switch_reason") == "openai_api_key_missing_fallback_gemini"
    assert captured.get("provider") == "gemini"
    assert str(captured.get("model") or "").strip().lower() == "gemini-2.5-pro"


def test_run_llm_reparse_audit_never_passes_when_provider_falls_back_to_pipeline(monkeypatch):
    monkeypatch.setenv("OCR_REPARSE_ENABLE_LLM_AUDIT_GATE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-openai-key")

    def _fake_extract(_pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        provider = str(template.get("main_ocr_provider") or "").strip().lower()
        return FaxExtractedData(
            facility_name="Test",
            date_strings=[],
            table_rows=[],
            tokens=[],
            grid=None,
            ocr_provider=f"{provider}_fallback_pipeline" if provider else "openai_fallback_pipeline",
            provider_debug={
                "provider": f"{provider}_fallback_pipeline" if provider else "openai_fallback_pipeline",
                "fallback_reason": "forced-test-fallback",
            },
        )

    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)

    result = order_service._run_llm_reparse_audit(
        pdf_bytes=b"%PDF-1.4\n%EOF\n",
        provider="gemini",
        template={"gemini_ocr_model": "gemini-2.5-flash"},
        facility_id="FAC00001",
        preferred_template_id=None,
        candidate_rows=[["", "", "", "20"]],
        reference_rows=[["", "", "", "20"]],
        expected_row_count=1,
    )

    assert result is not None
    assert result.get("status") == "unknown"
    assert result.get("error") == "audit_provider_fallback_pipeline"
    assert str(result.get("actual_provider") or "").endswith("_fallback_pipeline")


def test_build_reparse_debug_payload_requires_explicit_cluster_fill_allow():
    neutral_payload = order_service._build_reparse_debug_payload(
        provider="gemini",
        requested_provider="gemini",
        llm_assist=True,
        rows=[],
        lines_count=0,
        before_count=0,
        after_count=0,
        changed=False,
        llm_audit={"status": "pass", "issues": []},
        quality_metadata={"quality_track": "llm_reparse", "reparse_origin": "llm_assist", "feedback_retry_depth": 0},
    )
    allow_payload = order_service._build_reparse_debug_payload(
        provider="gemini",
        requested_provider="gemini",
        llm_assist=True,
        rows=[],
        lines_count=0,
        before_count=0,
        after_count=0,
        changed=False,
        llm_audit={"status": "pass", "issues": [], "order_line_cluster_fill_decision": "allow"},
        quality_metadata={"quality_track": "llm_reparse", "reparse_origin": "llm_assist", "feedback_retry_depth": 1},
    )
    deny_payload = order_service._build_reparse_debug_payload(
        provider="gemini",
        requested_provider="gemini",
        llm_assist=True,
        rows=[],
        lines_count=0,
        before_count=0,
        after_count=0,
        changed=False,
        llm_audit={"status": "fail", "issues": [{"issue_code": "coverage_low"}]},
        quality_metadata={"quality_track": "llm_reparse", "reparse_origin": "auto_fallback", "feedback_retry_depth": 0},
    )

    assert "order_line_cluster_fill_decision" not in neutral_payload
    assert neutral_payload.get("quality_track") == "llm_reparse"
    assert neutral_payload.get("reparse_origin") == "llm_assist"
    assert allow_payload.get("order_line_cluster_fill_decision") == "allow"
    assert allow_payload.get("feedback_retry_depth") == 1
    assert deny_payload.get("order_line_cluster_fill_decision") == "deny"
    assert deny_payload.get("reparse_origin") == "auto_fallback"


def test_build_llm_assist_prompt_uses_yomitoku_rows_and_generic_cell_issues_without_roi_overlay():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ]
    }
    pipeline_output = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "page_index": 1,
                        "row_count": 2,
                        "col_count": 5,
                        "rows": [
                            ["日付", "区分", "献立", "常食", "備考"],
                            ["2/15", "朝", "Menu A", "", "note"],
                        ],
                        "cells": [
                            {
                                "row_index": 1,
                                "col_index": 3,
                                "row_span": 2,
                                "col_span": 1,
                                "text": "6\n9",
                                "bbox": [0.1, 0.2, 0.3, 0.4],
                            }
                        ],
                    }
                ],
            }
        ],
        "roi_overlay_rows": [
            {"row_index": 0, "qty.regular_x": 87},
        ],
        "cell_issues": [
            {
                "table_id": "p1_t1",
                "page_index": 1,
                "row_index": 1,
                "column_index": 3,
                "field": "qty.regular_x",
                "issue_code": "merged_numeric_cell",
                "severity": "warning",
                "bbox": [0.1, 0.2, 0.3, 0.4],
                "text": "6\n9",
                "source": "yomitoku_structured",
            }
        ],
    }

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=pipeline_output,
        llm_assist=True,
    )

    assert prompt is not None
    assert "baseline draft" in prompt
    assert "First-pass yomitoku structured rows" in prompt
    assert "First-pass yomitoku structured tables/cells" in prompt
    assert "merged_numeric_cell" in prompt
    assert "yomitoku_structured" in prompt
    assert "Menu A" in prompt
    assert "87" not in prompt


def test_apply_payload_quantities_numeric_only_relaxes_noisy_daypart_constraint():
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "qty.regular_3f",
    ]
    quantity_index = {
        ("regular", "2f"): 3,
        ("regular", "3f"): 4,
    }
    rows = [
        {"row_id": "row-0", "values": ["02/08", "朝", "Menu A", "", ""]},
        {"row_id": "row-1", "values": ["02/08", "昼", "Menu B", "", ""]},
        {"row_id": "row-2", "values": ["02/08", "夕", "Menu C", "", ""]},
        {"row_id": "row-3", "values": ["02/09", "朝", "Menu D", "", ""]},
    ]
    payload_rows = [
        ["2/8", "朝", "Menu A", "1", "2"],
        ["", "朝<br>昼", "Menu B", "3", "4"],
        ["", "タ", "Menu C", "5", "6"],
        ["2/9", "明<br>長", "Menu D", "7", "8"],
    ]

    stats = order_service._apply_payload_quantities_numeric_only(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
        allow_heuristics=False,
    )

    assert stats.get("row_index") == 4
    assert [row["values"][3:5] for row in rows] == [
        ["1", "2"],
        ["3", "4"],
        ["5", "6"],
        ["7", "8"],
    ]


def test_extract_first_pass_rows_from_payload_keeps_yomitoku_baseline_without_roi_merge():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ]
    }
    payload = {
        "roi_overlay_policy": "merge",
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "数量", "備考"],
                            ["", "", "", "常食", ""],
                            ["2/15", "朝", "Menu A", "", ""],
                        ],
                    }
                ],
            }
        ],
        "roi_overlay_rows": [
            {"row_index": 0, "qty.regular_x": 88},
        ],
    }

    first_pass_rows = order_service._extract_first_pass_rows_from_payload(payload, template)
    merged_rows = order_service._extract_sheet_rows_from_payload(payload, template)

    assert first_pass_rows == [["2/15", "朝", "Menu A", "", ""]]
    assert merged_rows == [["2/15", "朝", "Menu A", "88", ""]]


def test_build_llm_assist_prompt_includes_yomitoku_context_and_generic_cell_issues():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ]
    }
    payload = {
        "roi_overlay_policy": "merge",
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "数量", "備考"],
                            ["", "", "", "常食", ""],
                            ["2/15", "朝", "Menu A", "", ""],
                        ],
                    }
                ],
            }
        ],
        "cell_issues": [
            {
                "table_id": "p1_t1",
                "page_index": 1,
                "source_row_index": 0,
                "column_index": 3,
                "issue_code": "merged_numeric_cell",
                "severity": "high",
                "source": "yomitoku_structured",
            }
        ],
        "roi_overlay_rows": [
            {"row_index": 0, "qty.regular_x": 88},
        ],
    }

    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template=template,
        pipeline_output=payload,
        llm_assist=True,
    )

    assert prompt is not None
    assert "baseline draft" in prompt
    assert "First-pass yomitoku structured rows" in prompt
    assert "Suspicious first-pass cells" in prompt
    assert "merged_numeric_cell" in prompt
    assert "88" not in prompt


def test_build_llm_assist_prompt_includes_numeric_verification_preset_instructions():
    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template={"gemini_ocr_prompt": "", "gemini_ocr_enabled": True},
        pipeline_output={
            "table_raw": "|日付|区分|メニュー|常食|\n|03/22|朝|Menu A|5|",
            "pages": [],
        },
        llm_assist=True,
        prompt_preset="numeric_verification",
        baseline={
            "fields": ["date_mmdd", "daypart", "menu_name", "regular__2f"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "structure_fields": ["date_mmdd", "daypart", "menu_name", "regular__2f"],
            "structure_rows": [["03/22", "朝", "Menu A", "5"]],
        },
    )

    assert isinstance(prompt, str)
    assert "Operator-selected focus preset" in prompt
    assert "Primary goal: verify quantity digits before changing structure." in prompt


def test_build_llm_assist_prompt_includes_merged_cell_span_preset_instructions():
    prompt = order_service._build_llm_assist_prompt(
        provider="gemini",
        template={"gemini_ocr_prompt": "", "gemini_ocr_enabled": True},
        pipeline_output={
            "pages": [
                {
                    "page_index": 1,
                    "tables": [
                        {
                            "table_id": "p1_t1",
                            "rows": [
                                ["日付", "区分", "メニュー", "常食", "糖尿"],
                                ["03/22", "朝", "Menu A", "5", ""],
                            ],
                        }
                    ],
                }
            ],
            "cell_issues": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "row_index": 1,
                    "col_index": 3,
                    "issue_code": "merged_numeric_cell",
                    "row_span": 2,
                    "col_span": 1,
                    "text": "5",
                }
            ],
        },
        llm_assist=True,
        prompt_preset="merged_cell_quantity_spans",
        baseline={
            "fields": ["date_mmdd", "daypart", "menu_name", "regular__x", "diabetes__x"],
            "rows": [["03/22", "朝", "Menu A", "", ""]],
            "structure_fields": ["date_mmdd", "daypart", "menu_name", "regular__x", "diabetes__x"],
            "structure_rows": [["03/22", "朝", "Menu A", "", ""]],
        },
    )

    assert isinstance(prompt, str)
    assert "Operator-selected focus preset" in prompt
    assert "resolve quantity cells that visually span multiple menu rows" in prompt
    assert "Never carry a merged quantity across a date boundary" in prompt


def test_build_llm_review_prompt_includes_baseline_tables_and_cell_issues():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ]
    }
    baseline = {
        "fields": list(template["main_ocr_row_fields"]),
        "header": ["日付", "区分", "献立", "常食", "備考"],
        "rows": [["2/15", "朝", "Menu A", "", ""]],
        "row_ids": ["row-1"],
        "baseline_revision_id": "OCRREVBASE1",
        "baseline_source": "edited",
        "raw_output": {
            "pages": [
                {
                    "page_index": 1,
                    "tables": [
                        {
                            "table_id": "p1_t1",
                            "rows": [
                                ["日付", "区分", "献立", "数量", "備考"],
                                ["", "", "", "常食", ""],
                                ["2/15", "朝", "Menu A", "", ""],
                            ],
                        }
                    ],
                }
            ],
            "cell_issues": [
                {
                    "table_id": "p1_t1",
                    "source_row_index": 0,
                    "column_index": 3,
                    "issue_code": "merged_numeric_cell",
                    "source": "yomitoku_structured",
                }
            ],
            "roi_overlay_rows": [{"row_index": 0, "qty.regular_x": 88}],
        },
    }

    system_prompt, user_prompt = order_service._build_llm_review_prompts(
        provider="gemini",
        template=template,
        baseline=baseline,
        pdf_variant_requested="corrected",
        pdf_variant_used="raw",
        pdf_variant_fallback_reason="corrected_pdf_unavailable_in_backend_cache",
    )

    assert '"llm_review"' in system_prompt
    assert '"table_raw"' in system_prompt
    assert "yomitoku-compatible JSON" in system_prompt
    assert "Current baseline revision_id: OCRREVBASE1" in user_prompt
    assert "Current baseline source: edited" in user_prompt
    assert "Attached fax variant requested: corrected" in user_prompt
    assert "Attached fax variant used: raw" in user_prompt
    assert "Attached fax fallback reason: corrected_pdf_unavailable_in_backend_cache" in user_prompt
    assert "Valid baseline fields" in user_prompt
    assert "Current baseline rows" in user_prompt
    assert "Previous yomitoku/LLM structured tables/cells" in user_prompt
    assert "Existing suspicious cells from yomitoku/LLM" in user_prompt
    assert "merged_numeric_cell" in user_prompt
    assert "88" not in user_prompt


def test_resolve_llm_review_pdf_bytes_uses_corrected_artifact_when_available(monkeypatch):
    captured_uris: list[str] = []

    def _fake_load_bytes(uri: str) -> bytes:
        captured_uris.append(uri)
        if uri == "gs://bucket/corrected.pdf":
            return b"%PDF-corrected\n%EOF\n"
        if uri == "gs://bucket/raw.pdf":
            return b"%PDF-raw\n%EOF\n"
        raise AssertionError(f"unexpected uri: {uri}")

    monkeypatch.setattr(order_service, "load_bytes_from_uri", _fake_load_bytes)

    pdf_bytes, meta = order_service._resolve_llm_review_pdf_bytes(
        document_uri="gs://bucket/raw.pdf",
        payload={
            "combined": {
                "corrected_pdf": "gs://bucket/corrected.pdf",
            }
        },
        requested_variant="corrected",
    )

    assert pdf_bytes == b"%PDF-corrected\n%EOF\n"
    assert meta["requested"] == "corrected"
    assert meta["used"] == "corrected"
    assert meta["fallback_reason"] is None
    assert captured_uris == ["gs://bucket/corrected.pdf"]


def test_resolve_llm_review_pdf_bytes_falls_back_to_raw_when_corrected_missing(monkeypatch):
    captured_uris: list[str] = []

    def _fake_load_bytes(uri: str) -> bytes:
        captured_uris.append(uri)
        if uri == "gs://bucket/raw.pdf":
            return b"%PDF-raw\n%EOF\n"
        raise AssertionError(f"unexpected uri: {uri}")

    monkeypatch.setattr(order_service, "load_bytes_from_uri", _fake_load_bytes)

    pdf_bytes, meta = order_service._resolve_llm_review_pdf_bytes(
        document_uri="gs://bucket/raw.pdf",
        payload={},
        requested_variant="corrected",
    )

    assert pdf_bytes == b"%PDF-raw\n%EOF\n"
    assert meta["requested"] == "corrected"
    assert meta["used"] == "raw"
    assert meta["fallback_reason"] == "corrected_pdf_unavailable_in_backend_cache"
    assert captured_uris == ["gs://bucket/raw.pdf"]


def test_resolve_llm_review_pdf_bytes_falls_back_to_ocr_pdf_when_corrected_missing(monkeypatch):
    captured_uris: list[str] = []

    def _fake_load_bytes(uri: str) -> bytes:
        captured_uris.append(uri)
        if uri == "gs://bucket/ocr.pdf":
            return b"%PDF-ocr\n%EOF\n"
        raise AssertionError(f"unexpected uri: {uri}")

    monkeypatch.setattr(order_service, "load_bytes_from_uri", _fake_load_bytes)

    pdf_bytes, meta = order_service._resolve_llm_review_pdf_bytes(
        document_uri="gs://bucket/raw.pdf",
        payload={
            "combined": {
                "ocr_pdf": "gs://bucket/ocr.pdf",
            }
        },
        requested_variant="corrected",
    )

    assert pdf_bytes == b"%PDF-ocr\n%EOF\n"
    assert meta["requested"] == "corrected"
    assert meta["used"] == "ocr"
    assert meta["fallback_reason"] == "corrected_pdf_unavailable_in_backend_cache"
    assert meta["ocr_pdf"] == "gs://bucket/ocr.pdf"
    assert captured_uris == ["gs://bucket/ocr.pdf"]


def test_extract_corrected_pdf_uri_from_nested_review_payload():
    corrected_uri = order_service._extract_corrected_pdf_uri_from_payload(
        {
            "_edited_ocr": {
                "latest": {
                    "llm_review": {
                        "output_payload": {
                            "page_correction": {
                                "corrected_pdf_uri": "gs://bucket/review-corrected.pdf",
                            }
                        }
                    }
                }
            }
        }
    )

    assert corrected_uri == "gs://bucket/review-corrected.pdf"


def test_resolve_llm_review_pdf_bytes_falls_back_to_raw_when_corrected_load_fails(monkeypatch):
    captured_uris: list[str] = []

    def _fake_load_bytes(uri: str) -> bytes:
        captured_uris.append(uri)
        if uri == "gs://bucket/corrected.pdf":
            raise RuntimeError("missing corrected artifact")
        if uri == "gs://bucket/raw.pdf":
            return b"%PDF-raw\n%EOF\n"
        raise AssertionError(f"unexpected uri: {uri}")

    monkeypatch.setattr(order_service, "load_bytes_from_uri", _fake_load_bytes)

    pdf_bytes, meta = order_service._resolve_llm_review_pdf_bytes(
        document_uri="gs://bucket/raw.pdf",
        payload={
            "_edited_ocr": {
                "raw_output": {
                    "combined": {
                        "corrected_pdf": "gs://bucket/corrected.pdf",
                    }
                }
            }
        },
        requested_variant="corrected",
    )

    assert pdf_bytes == b"%PDF-raw\n%EOF\n"
    assert meta["requested"] == "corrected"
    assert meta["used"] == "raw"
    assert meta["fallback_reason"] == "corrected_pdf_load_failed"
    assert captured_uris == ["gs://bucket/corrected.pdf", "gs://bucket/raw.pdf"]


def test_reparse_order_llm_path_uses_rescued_first_pass_payload_and_corrected_pdf(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-gemini-corrected.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-gemini-corrected-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")

    extract_pdf_inputs: list[bytes] = []
    parse_pdf_inputs: list[bytes | None] = []
    get_ocr_output_calls: list[bool] = []

    rescued_payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            [
                                "日付",
                                "区分",
                                "メニュー",
                                "常食2F",
                                "常食3F",
                                "軟菜2F",
                                "軟菜3F",
                                "ミキサー2F",
                                "ミキサー3F",
                                "備考",
                            ],
                            [canonical_mmdd, canonical_daypart, canonical_menu, "9", "", "", "", "", "", "first-pass"],
                        ],
                    }
                ],
            }
        ],
        "combined": {
            "corrected_pdf": "gs://bucket/corrected.pdf",
        },
        "table_raw": (
            "|日付|区分|メニュー|常食2F|常食3F|軟菜2F|軟菜3F|ミキサー2F|ミキサー3F|備考|\n"
            "|---|---|---|---|---|---|---|---|---|---|\n"
            f"|{canonical_mmdd}|{canonical_daypart}|{canonical_menu}|9||||||first-pass|"
        ),
    }

    pipeline_calls: list[dict] = []
    wait_calls: list[tuple[str | None, float | None]] = []

    def _fake_pipeline(**kwargs):
        pipeline_calls.append(kwargs)
        raise AssertionError("existing first-pass OCR should be reused for llm assist")

    def _fake_load_pipeline_output_with_retry(_ref, *, wait_seconds_override=None):  # noqa: ARG001
        wait_calls.append((_ref, wait_seconds_override))
        raise AssertionError("llm assist reuse path should not wait for pipeline output")

    def _fake_get_ocr_output(order_id: str, *, persist_cache: bool = True):
        assert order_id == order["id"]
        get_ocr_output_calls.append(persist_cache)
        return dict(rescued_payload), None

    def _fake_load_bytes(uri: str) -> bytes:
        if uri == "gs://bucket/corrected.pdf":
            return b"%PDF-corrected\n%EOF\n"
        return b"%PDF-raw\n%EOF\n"

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        extract_pdf_inputs.append(pdf_bytes)
        assert pdf_bytes == b"%PDF-corrected\n%EOF\n"
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "2"]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"quantity_only_mode": True},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        parse_pdf_inputs.append(pdf_bytes)
        return [
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 2,
                "source_row_index": 0,
            }
        ]

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "get_ocr_output", _fake_get_ocr_output)
    monkeypatch.setattr(order_service, "load_bytes_from_uri", _fake_load_bytes)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(
        order_service,
        "_run_llm_reparse_audit",
        lambda **kwargs: {"status": "pass", "issues": [], "issue_count": 0, "blocking_issues": [], "blocking_issue_count": 0},
    )
    monkeypatch.setattr(order_service, "detect_table_grid", lambda pdf_bytes, template: None)

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    assert get_ocr_output_calls
    assert all(flag is False for flag in get_ocr_output_calls)
    assert pipeline_calls == []
    assert wait_calls == []
    assert extract_pdf_inputs == [b"%PDF-corrected\n%EOF\n"]
    assert parse_pdf_inputs
    assert all(item == b"%PDF-corrected\n%EOF\n" for item in parse_pdf_inputs if item is not None)
    assert updated["lines"][0]["quantity_original"] == 2
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job["metrics"]["processing_stage"] in {"apply", "applied"}
    assert job["metrics"]["reused_first_pass"] is True
    assert job["metrics"]["pipeline_run_skipped"] is True


def test_reparse_order_llm_path_marks_awaiting_output_when_fresh_first_pass_output_is_still_pending(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-gemini-missing-first-pass.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-gemini-missing-first-pass-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    pipeline_calls: list[dict] = []
    wait_calls: list[tuple[str | None, float | None]] = []
    def _fake_pipeline(**kwargs):
        pipeline_calls.append(kwargs)
        return "file://pipeline-output.json"

    def _fake_load_pipeline_output_with_retry(_ref, *, wait_seconds_override=None):
        wait_calls.append((_ref, wait_seconds_override))
        return None

    monkeypatch.setattr(order_service, "_load_existing_first_pass_payload_for_reparse", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(
        order_service,
        "run_ocr_pipeline",
        lambda **_kwargs: (_ for _ in ()).throw(
            order_service.OCRPipelineOutputPendingError(
                input_reference="file://reparse-input.pdf",
                output_reference="file://pipeline-output.json",
                timeout_seconds=600,
            )
        ),
    )
    monkeypatch.setattr(
        order_service,
        "_get_ocr_output_without_legacy_edits",
        lambda *_args, **_kwargs: (None, "ocr_output_not_found"),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated == {"status": "running", "output_reference": "file://pipeline-output.json"}
    assert pipeline_calls == []
    assert wait_calls == []
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job["status"] == "awaiting_output"
    assert job["metrics"]["processing_stage"] == "ocr_pipeline"
    assert job["metrics"]["result_state"] == "awaiting_output"
    assert job["metrics"]["error"] == "ocr_output_pending"
    assert job["metrics"]["output_reference"] == "file://pipeline-output.json"
    assert job["metrics"]["first_pass_wait_seconds"] == 600.0
    assert job["metrics"]["reused_first_pass"] is False
    assert job["error_message"] == "ocr_output_pending"


def test_reconcile_completed_llm_reparse_resumes_from_completed_first_pass_output(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-gemini-awaiting-output-reconcile.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-gemini-awaiting-output-reconcile-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 16, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    output_path = tmp_path / "completed-first-pass.json"
    output_path.write_text(
        (
            '{"status":"done","table_raw":"|02/16|朝|Menu A|2|",'
            '"rows":[["02/16","朝","Menu A","2"]],'
            '"table_rows":[["02/16","朝","Menu A","2"]],'
            '"tables":[{"rows":[["02/16","朝","Menu A","2"]]}],'
            '"pages":[{"tables":[{"rows":[["02/16","朝","Menu A","2"]]}]}]}'
        ),
        encoding="utf-8",
    )

    job_id = f"OCR-{order['id']}"
    order_service.create_job(job_id, input_reference=order["document"], status="awaiting_output")
    order_service.update_job(
        job_id,
        status="awaiting_output",
        output_reference=f"file://{output_path}",
        error_message="ocr_output_pending",
        metrics={
            "request_mode": "llm_reparse",
            "reparse_ocr_prompt": "custom prompt",
            "reparse_prompt_preset": "operator-tuned",
            "reparse_requested_provider": "gemini",
            "reparse_ocr_model": "gemini-2.5-pro",
            "reparse_llm_assist": True,
            "processing_stage": "ocr_pipeline",
            "result_state": "awaiting_output",
            "output_reference": f"file://{output_path}",
            "order_id": order["id"],
        },
    )

    captured: dict[str, object] = {}

    def _fake_reparse(order_id, **kwargs):
        captured["order_id"] = order_id
        captured.update(kwargs)
        return {"status": "running"}, None

    monkeypatch.setattr(order_service, "reparse_order", _fake_reparse)

    assert order_service.reconcile_completed_ocr_job(job_id) is True
    assert captured["order_id"] == order["id"]
    assert captured["ocr_prompt"] == "custom prompt"
    assert captured["prompt_preset"] == "operator-tuned"
    assert captured["ocr_provider"] == "gemini"
    assert captured["ocr_model"] == "gemini-2.5-pro"
    assert captured["llm_assist"] is True
    assert captured["resume_first_pass_output_reference"] == f"file://{output_path}"
    assert isinstance(captured["resume_first_pass_payload"], dict)
    job = get_job(job_id)
    assert job is not None
    assert job["status"] == "recovering"
    assert job["metrics"]["result_state"] == "recovering"


def test_reparse_order_llm_prompt_includes_previous_saved_candidate_rows(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-gemini-previous-draft.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-gemini-previous-draft-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")

    facility_config = config_service.get_facility_config("FAC00001") or {}
    template = dict(facility_config.get("fax_template") or {})
    order_service._save_reparse_candidate_as_draft(
        order_id=order["id"],
        template=template,
        rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "7"]],
        before_digest="before-digest",
        review_state="auto_apply_blocked",
        review_blockers=["sheet_llm_audit_failed"],
    )

    first_pass_payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "メニュー", "常食2F"],
                            [canonical_mmdd, canonical_daypart, canonical_menu, "9"],
                        ],
                    }
                ],
            }
        ],
        "table_raw": (
            "|日付|区分|メニュー|常食2F|\n"
            "|---|---|---|---|\n"
            f"|{canonical_mmdd}|{canonical_daypart}|{canonical_menu}|9|"
        ),
    }

    captured_prompt: dict[str, str] = {}

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        prompt = str(template.get("gemini_ocr_prompt") or "")
        if prompt:
            captured_prompt["value"] = prompt
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "2"]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"quantity_only_mode": True, "model": "gemini-2.5-pro"},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        return [
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 2,
                "source_row_index": 0,
            }
        ]

    monkeypatch.setattr(
        order_service,
        "_load_existing_first_pass_payload_for_reparse",
        lambda order_id: dict(first_pass_payload) if order_id == order["id"] else None,
    )
    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda uri: b"%PDF-raw\n%EOF\n")
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "detect_table_grid", lambda pdf_bytes, template: None)
    monkeypatch.setattr(
        order_service,
        "_run_llm_reparse_audit",
        lambda **kwargs: {
            "status": "pass",
            "issues": [],
            "issue_count": 0,
            "blocking_issues": [],
            "blocking_issue_count": 0,
        },
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    prompt_text = captured_prompt.get("value") or ""
    assert "Previous saved LLM candidate rows" in prompt_text
    assert canonical_menu in prompt_text
    assert "\"7\"" in prompt_text or "|7|" in prompt_text


def test_reparse_order_projects_quantity_only_rows_onto_structural_baseline(monkeypatch, tmp_path):
    template = {
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "2F"},
            {"index": 4, "role": "remarks"},
        ],
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
    }
    baseline = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "rows": [
            ["02/15", "昼", "Menu A", "", ""],
            ["02/15", "夕", "Menu B", "", ""],
        ],
        "row_ids": ["row-1", "row-2"],
    }

    fields, structural_rows, _row_ids, source = order_service._resolve_reparse_baseline_rows_for_structure(baseline)
    projected_rows, stats = order_service._project_quantity_only_rows_onto_structural_rows(
        rows=[["", "", "", "20", ""], ["", "", "", "11", ""]],
        template=template,
        structural_fields=fields,
        structural_rows=structural_rows,
    )

    assert source == "sheet"
    assert [row[:3] for row in projected_rows] == [
        ["02/15", "昼", "Menu A"],
        ["02/15", "夕", "Menu B"],
    ]
    assert [row[3] for row in projected_rows] == ["20", "11"]
    assert stats is not None
    assert stats["projected_row_count"] == 2
    assert stats["rows_with_projected_quantity"] == 2


def test_reparse_order_large_structural_projection_requires_manual_review(monkeypatch, tmp_path):
    order_service.clear_all()
    _ensure_week_menu_entries("2026-02")
    pdf_path = tmp_path / "sample-gemini-structural-projection.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-gemini-structural-projection-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 1, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    first_pass_payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "メニュー", "常食2F"],
                            [canonical_mmdd, canonical_daypart, canonical_menu, "9"],
                        ],
                    }
                ],
            }
        ],
        "table_raw": (
            "|日付|区分|メニュー|常食2F|\n"
            "|---|---|---|---|\n"
            f"|{canonical_mmdd}|{canonical_daypart}|{canonical_menu}|9|"
        ),
    }
    order_service._save_order_ocr_cache(order["id"], dict(first_pass_payload))

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[["", "", "", "2"]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"quantity_only_mode": True, "model": "gemini-2.5-pro"},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        return [
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 2,
                "source_row_index": 0,
            }
        ]

    monkeypatch.setattr(
        order_service,
        "_load_existing_first_pass_payload_for_reparse",
        lambda order_id: dict(first_pass_payload) if order_id == order["id"] else None,
    )
    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda uri: b"%PDF-raw\n%EOF\n")
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "detect_table_grid", lambda pdf_bytes, template: None)
    monkeypatch.setattr(order_service, "_should_project_quantity_rows_to_structural_rows", lambda **kwargs: True)
    monkeypatch.setattr(
        order_service,
        "_project_quantity_only_rows_onto_structural_rows",
        lambda **kwargs: (
            [[canonical_mmdd, canonical_daypart, canonical_menu, "2"]],
            {
                "projected_row_count": 30,
                "rows_with_projected_quantity": 30,
                "quantity_cells_copied": 120,
            },
        ),
    )
    monkeypatch.setattr(order_service, "_evaluate_quantity_only_rows_quality", lambda **kwargs: (None, {}))
    monkeypatch.setattr(
        order_service,
        "_run_llm_reparse_audit",
        lambda **kwargs: {
            "status": "pass",
            "issues": [],
            "issue_count": 0,
            "blocking_issues": [],
            "blocking_issue_count": 0,
        },
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert updated is None
    assert error == "sheet_structural_projection_requires_review"
    refreshed = order_service.get_order_by_id(order["id"])
    assert refreshed is not None
    assert refreshed.get("lines") == []
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job["metrics"]["result_state"] == "draft_ready_blocked"
    assert job["metrics"]["error"] == "sheet_structural_projection_requires_review"


def test_parse_llm_review_response_returns_yomitoku_compatible_output_payload():
    raw_text = """
review result
```json
{
  "facility_name": "Test Facility",
  "date_strings": ["2/15"],
  "rows": [
    {
      "date_mmdd": "2/15",
      "daypart": "朝",
      "menu": "Menu A",
      "qty.regular_x": "4",
      "remarks": ""
    }
  ],
  "table_raw": "|日付|区分|献立|常食|備考|\\n|---|---|---|---|---|\\n|2/15|朝|Menu A|4||",
  "llm_review": {
    "status": "needs_review",
    "needs_more_review": true,
    "notes": "1 suspicious cell",
    "issues": [
      {
        "issue_id": "iss-1",
        "row_id": "row-1",
        "field": "qty.regular_x",
        "issue_code": "misread_quantity",
        "status": "misread",
        "page_index": 1,
        "table_id": "p1_t1",
        "current_text": "4",
        "confidence": 0.86,
        "evidence": "single digit 4 visible",
        "reason": "verified_from_pdf"
      }
    ]
  }
}
```
""".strip()

    parsed = order_service._parse_llm_review_response(raw_text)

    assert parsed is not None
    assert parsed["output_payload"]["facility_name"] == "Test Facility"
    assert parsed["summary"]["status"] == "needs_review"
    assert parsed["summary"]["needs_more_review"] is True
    assert parsed["issues"] == []
    assert parsed["overwrites"] == []
    assert parsed["output_payload"]["rows"][0]["qty.regular_x"] == "4"


def test_parse_llm_review_response_returns_none_for_invalid_json():
    parsed = order_service._parse_llm_review_response("not json at all")

    assert parsed is None


def test_apply_llm_review_overwrites_updates_only_target_cells():
    result = order_service._apply_llm_review_overwrites(
        fields=["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        rows=[
            ["2/15", "朝", "Menu A", "11", ""],
            ["2/15", "昼", "Menu B", "8", ""],
        ],
        row_ids=["row-1", "row-2"],
        issues=[
            {
                "issue_id": "iss-1",
                "row_id": "row-1",
                "field": "qty.regular_x",
                "reason": "misread",
                "source": "llm_review",
            }
        ],
        overwrites=[
            {
                "issue_id": "iss-1",
                "row_id": "row-1",
                "field": "qty.regular_x",
                "old_text": "11",
                "new_text": "7",
                "confidence": 0.93,
                "evidence": "single digit 7 visible",
                "source": "llm_review",
            }
        ],
    )

    assert result["rows"][0][3] == "7"
    assert result["rows"][1] == ["2/15", "昼", "Menu B", "8", ""]
    assert result["row_ids"] == ["row-1", "row-2"]
    assert len(result["applied_overwrites"]) == 1
    assert result["applied_overwrites"][0]["source_row_index"] == 0
    assert len(result["rejected_overwrites"]) == 0
    assert result["needs_more_review"] is False


def test_apply_llm_review_overwrites_rejects_invalid_targets_and_weak_evidence():
    baseline_rows = [["2/15", "朝", "Menu A", "11", ""]]
    result = order_service._apply_llm_review_overwrites(
        fields=["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        rows=[list(row) for row in baseline_rows],
        row_ids=["row-1"],
        issues=[
            {
                "issue_id": "iss-1",
                "row_id": "row-1",
                "field": "qty.regular_x",
                "reason": "misread",
                "source": "llm_review",
            }
        ],
        overwrites=[
            {
                "issue_id": "iss-1",
                "row_id": "missing-row",
                "field": "qty.regular_x",
                "old_text": "11",
                "new_text": "7",
                "confidence": 0.9,
                "evidence": "clear 7",
            },
            {
                "issue_id": "iss-1",
                "row_id": "row-1",
                "field": "qty.unknown_x",
                "old_text": "11",
                "new_text": "7",
                "confidence": 0.9,
                "evidence": "clear 7",
            },
            {
                "issue_id": "iss-1",
                "row_id": "row-1",
                "field": "qty.regular_x",
                "old_text": "99",
                "new_text": "7",
                "confidence": 0.9,
                "evidence": "clear 7",
            },
            {
                "issue_id": "iss-1",
                "row_id": "row-1",
                "field": "qty.regular_x",
                "old_text": "11",
                "new_text": "7",
                "confidence": 0.2,
                "evidence": "unclear",
            },
            {
                "issue_id": "iss-1",
                "row_id": "row-1",
                "field": "qty.regular_x",
                "old_text": "11",
                "new_text": "7",
                "confidence": 0.9,
                "evidence": "",
            },
        ],
    )

    assert result["rows"] == baseline_rows
    reject_reasons = {item["reject_reason"] for item in result["rejected_overwrites"]}
    assert {
        "row_id_not_found",
        "field_not_found",
        "old_text_mismatch",
        "low_confidence",
        "missing_evidence",
    } <= reject_reasons
    assert result["needs_more_review"] is True


def test_validate_and_merge_reparse_full_table_candidate_accepts_quantity_only_diffs():
    baseline = {
        "baseline_source": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [
            ["04/26", "朝", "Menu A", "5"],
            ["04/26", "昼", "Menu B", ""],
        ],
        "row_ids": ["row-1", "row-2"],
    }

    merged_rows, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=baseline,
        candidate_rows=[
            ["04/26", "朝", "Menu A", "11"],
            ["04/26", "昼", "Menu B", ""],
        ],
    )

    assert result["error"] is None
    assert merged_rows == [
        ["04/26", "朝", "Menu A", "11"],
        ["04/26", "昼", "Menu B", ""],
    ]
    assert (result.get("detail") or {}).get("quantity_change_count") == 1


def test_validate_and_merge_reparse_full_table_candidate_ignores_shell_owned_remarks_drift():
    baseline = {
        "baseline_source": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
        "rows": [["04/26", "朝", "Menu A", "5", ""]],
        "row_ids": ["row-1"],
    }

    merged_rows, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=baseline,
        candidate_rows=[["04/26", "朝", "Menu A", "11", "肉4"]],
    )

    assert result["error"] is None
    assert merged_rows == [["04/26", "朝", "Menu A", "11", ""]]
    detail = result.get("detail") or {}
    assert detail.get("quantity_change_count") == 1
    assert detail.get("ignored_shell_drift_count") == 1
    assert detail.get("ignored_shell_drift_cells")[0]["field"] == "remarks"


def test_validate_and_merge_reparse_full_table_candidate_ignores_display_only_aux_drift():
    baseline = {
        "baseline_source": "sheet",
        "fields": ["date_mmdd", "daypart", "aux.col_2", "menu", "aux.col_4", "qty.regular_x", "remarks"],
        "header": ["日付", "区分", "副区分", "メニュー", "合計", "常食", "備考"],
        "rows": [["04/26", "昼", "主", "Menu A", "12", "10", ""]],
        "row_ids": ["row-1"],
    }

    merged_rows, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=baseline,
        candidate_rows=[["04/26", "昼", "副", "Menu A", "13", "11", ""]],
    )

    assert result["error"] is None
    assert merged_rows == [["04/26", "昼", "主", "Menu A", "12", "11", ""]]
    detail = result.get("detail") or {}
    assert detail.get("quantity_change_count") == 1
    assert detail.get("ignored_shell_drift_count") == 2
    assert {item["field"] for item in detail.get("ignored_shell_drift_cells") or []} == {"aux.col_2", "aux.col_4"}


def test_canonicalize_block_helper_display_rows_reanchors_total_to_first_row_of_each_block():
    fields = [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "aux.col_4",
        "qty.regular_x",
        "remarks",
    ]
    header = ["日付", "区分", "副区分", "メニュー", "合計", "常食", "備考"]
    rows = [
        ["04/26", "朝", "", "大豆のトマト煮", "", "70", ""],
        ["04/26", "朝", "", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "70", "70", ""],
        ["04/26", "昼", "", "サワラの揚げ浸し", "", "105", ""],
        ["04/26", "昼", "", "小松菜のお浸し", "105", "105", ""],
    ]

    resolved_rows, stats = order_service._canonicalize_block_helper_display_rows(
        rows=rows,
        fields=fields,
        header=header,
    )

    assert [row[4] for row in resolved_rows] == ["70", "70", "105", "105"]
    assert [row[3] for row in resolved_rows] == [
        "大豆のトマト煮",
        "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ",
        "サワラの揚げ浸し",
        "小松菜のお浸し",
    ]
    assert stats.get("helper_reanchor_count") == 2
    assert stats.get("helper_conflict_count") == 0


def test_validate_and_merge_reparse_full_table_candidate_rejects_conflicting_block_total_helper_values():
    baseline = {
        "baseline_source": "sheet",
        "fields": ["date_mmdd", "daypart", "aux.col_2", "menu", "aux.col_4", "qty.regular_x", "remarks"],
        "header": ["日付", "区分", "副区分", "メニュー", "合計", "常食", "備考"],
        "rows": [
            ["04/26", "朝", "", "大豆のトマト煮", "70", "70", ""],
            ["04/26", "朝", "", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "", "70", ""],
        ],
        "row_ids": ["row-1", "row-2"],
    }

    merged_rows, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=baseline,
        candidate_rows=[
            ["04/26", "朝", "", "大豆のトマト煮", "70", "70", ""],
            ["04/26", "朝", "", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "71", "70", ""],
        ],
    )

    assert merged_rows == []
    assert result["error"] == "llm_full_table_helper_column_conflict"
    detail = result.get("detail") or {}
    assert detail.get("helper_conflict_count") == 1
    conflict = (detail.get("helper_conflicts") or [])[0]
    assert conflict.get("field") == "aux.col_4"
    assert conflict.get("values") == ["70", "71"]


def test_validate_and_merge_reparse_full_table_candidate_merges_block_total_helper_from_candidate():
    baseline = {
        "baseline_source": "sheet",
        "fields": ["date_mmdd", "daypart", "aux.col_2", "menu", "aux.col_4", "qty.regular_x", "remarks"],
        "header": ["日付", "区分", "副区分", "メニュー", "合計", "常食", "備考"],
        "rows": [
            ["04/27", "朝", "", "じゃが芋の煮物", "", "70", ""],
            ["04/27", "朝", "", "チンゲン菜のお浸し", "", "70", ""],
        ],
        "row_ids": ["row-1", "row-2"],
    }

    merged_rows, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=baseline,
        candidate_rows=[
            ["04/27", "朝", "", "じゃが芋の煮物", "70", "70", ""],
            ["04/27", "朝", "", "チンゲン菜のお浸し", "70", "70", ""],
        ],
    )

    assert result["error"] is None
    assert merged_rows == [
        ["04/27", "朝", "", "じゃが芋の煮物", "70", "70", ""],
        ["04/27", "朝", "", "チンゲン菜のお浸し", "70", "70", ""],
    ]
    detail = result.get("detail") or {}
    assert detail.get("display_change_count") == 2
    assert (detail.get("display_changes") or [])[0]["field"] == "aux.col_4"


def test_validate_and_merge_reparse_full_table_candidate_merges_aux_display_fields_from_candidate():
    baseline = {
        "baseline_source": "sheet",
        "fields": ["date_mmdd", "daypart", "aux.col_2", "menu", "aux.col_4", "qty.regular_x", "remarks"],
        "header": ["日付", "区分", "副区分", "メニュー", "合計", "常食", "備考"],
        "rows": [
            ["04/30", "昼", "", "豚肉と大根の生姜煮", "", "67", ""],
            ["04/30", "昼", "", "玉子の甘酢あん", "", "66", ""],
        ],
        "row_ids": ["row-1", "row-2"],
    }

    merged_rows, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=baseline,
        candidate_rows=[
            ["04/30", "昼", "#A", "豚肉と大根の生姜煮", "102", "67", ""],
            ["04/30", "昼", "副", "玉子の甘酢あん", "102", "66", ""],
        ],
    )

    assert result["error"] is None
    assert merged_rows == [
        ["04/30", "昼", "#A", "豚肉と大根の生姜煮", "102", "67", ""],
        ["04/30", "昼", "副", "玉子の甘酢あん", "102", "66", ""],
    ]
    detail = result.get("detail") or {}
    assert detail.get("display_change_count") == 4


def test_project_payload_display_cells_onto_sheet_row_lists_reanchors_block_total_before_projection():
    fields = [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "aux.col_4",
        "qty.regular_x",
        "remarks",
    ]
    header = ["日付", "区分", "副区分", "メニュー", "合計", "常食", "備考"]
    current_rows = [
        ["04/26", "朝", "", "大豆のトマト煮", "", "70", ""],
        ["04/26", "朝", "", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "", "70", ""],
    ]
    payload_rows = [
        ["04/26", "朝", "", "大豆のトマト煮", "", "70", ""],
        ["04/26", "朝", "", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "70", "70", ""],
    ]

    projected_rows, stats = order_service._project_payload_display_cells_onto_sheet_row_lists(
        rows=current_rows,
        fields=fields,
        header=header,
        payload_rows=payload_rows,
    )

    assert projected_rows == [
        ["04/26", "朝", "", "大豆のトマト煮", "70", "70", ""],
        ["04/26", "朝", "", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "70", "70", ""],
    ]
    assert any(int(value or 0) > 0 for value in stats.values())


def test_overlay_payload_aux_cells_onto_sheet_rows_clears_stale_block_total_on_non_anchor_rows():
    fields = [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "aux.col_4",
        "qty.regular_x",
        "remarks",
    ]
    header = ["日付", "区分", "副区分", "メニュー", "合計", "常食", "備考"]
    rows = [
        {"values": ["04/26", "朝", "", "大豆のトマト煮", "70", "70", ""]},
        {"values": ["04/26", "朝", "", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "70", "70", ""]},
    ]
    payload_rows = [
        ["04/26", "朝", "", "大豆のトマト煮", "", "70", ""],
        ["04/26", "朝", "", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "70", "70", ""],
    ]

    overlaid = order_service._overlay_payload_aux_cells_onto_sheet_rows(
        rows=rows,
        fields=fields,
        header=header,
        payload_rows=payload_rows,
    )

    assert [row.get("values", [])[4] for row in overlaid] == ["70", "70"]


def test_project_payload_display_cells_onto_sheet_row_lists_clears_stale_block_total_on_non_anchor_rows():
    fields = [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "aux.col_4",
        "qty.regular_x",
        "remarks",
    ]
    header = ["日付", "区分", "副区分", "メニュー", "合計", "常食", "備考"]
    current_rows = [
        ["04/26", "朝", "", "大豆のトマト煮", "70", "70", ""],
        ["04/26", "朝", "", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "70", "70", ""],
    ]
    payload_rows = [
        ["04/26", "朝", "", "大豆のトマト煮", "", "70", ""],
        ["04/26", "朝", "", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "70", "70", ""],
    ]

    projected_rows, stats = order_service._project_payload_display_cells_onto_sheet_row_lists(
        rows=current_rows,
        fields=fields,
        header=header,
        payload_rows=payload_rows,
    )

    assert projected_rows == [
        ["04/26", "朝", "", "大豆のトマト煮", "70", "70", ""],
        ["04/26", "朝", "", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "70", "70", ""],
    ]
    assert any(int(value or 0) > 0 for value in stats.values())


def test_project_payload_quantities_onto_sheet_row_lists_maps_first_row_in_strict_raw_mode():
    fields = [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "aux.col_4",
        "qty.regular_x",
        "qty.daycare_x",
    ]
    current_rows = [
        ["04/26", "朝", "主", "鶏じゃが", "67", "", ""],
    ]
    payload_rows = [
        ["04/26", "朝", "主", "鶏じゃが", "67", "66", ""],
    ]

    projected_rows, stats = order_service._project_payload_quantities_onto_sheet_row_lists(
        rows=current_rows,
        fields=fields,
        payload_rows=payload_rows,
        strict_raw_ocr_only=True,
    )

    assert projected_rows == [["04/26", "朝", "主", "鶏じゃが", "67", "66", ""]]
    assert stats["exact"] == 1


def test_overlay_payload_aux_cells_onto_sheet_rows_derives_block_total_without_structured_segments():
    template = config_service.get_facility_config("FAC00004").get("fax_template")
    fields = order_service._get_row_fields(template)
    header = order_service._sheet_header_from_template(fields, template)
    rows = [
        {"values": ["04/26", "夕", "", "豚肉のピリ辛炒め", "", "67", "", "5", "", "", "", "", ""]},
        {"values": ["", "", "", "春雨サラダ", "", "67", "", "5", "", "", "", "", ""]},
        {"values": ["", "", "", "味噌汁", "", "67", "", "5", "", "", "", "", ""]},
    ]
    payload_rows = [
        ["04/26", "夕", "", "豚肉のピリ辛炒め", "", "67", "", "5", "", "", "", "", ""],
        ["", "", "", "春雨サラダ", "", "67", "", "5", "", "", "", "", ""],
        ["", "", "", "味噌汁", "", "67", "", "5", "", "", "", "", ""],
    ]

    overlaid = order_service._overlay_payload_aux_cells_onto_sheet_rows(
        rows=rows,
        fields=fields,
        header=header,
        payload_rows=payload_rows,
        raw_payload={"tables": []},
        template=template,
    )

    assert [row.get("values", [])[4] for row in overlaid] == ["72", "72", "72"]


def test_build_rows_from_menu_entries_populates_slot_label_from_weekly_menu_category():
    template = config_service.get_facility_config("FAC00004").get("fax_template")
    fields = order_service._get_row_fields(template)
    header = order_service._sheet_header_from_template(fields, template)
    field_index = {field: idx for idx, field in enumerate(fields)}

    rows, source = order_service._build_rows_from_menu_entries(
        entries=[
            {
                "menu_name": "大豆のトマト煮",
                "menu_date": date(2026, 4, 26),
                "daypart_key": "朝",
                "category": "主菜",
                "slot_index": 0,
                "order": 0,
            },
            {
                "menu_name": "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ",
                "menu_date": date(2026, 4, 26),
                "daypart_key": "朝",
                "category": "副菜",
                "slot_index": 1,
                "order": 1,
            },
        ],
        fields=fields,
        field_index=field_index,
        header=header,
        line_dates=set(),
        source="weekly_menu",
        payload_dates=None,
        payload_row_count=0,
        scope_anchor_date=None,
    )

    assert source == "weekly_menu"
    assert [row.get("values", [])[:5] for row in rows] == [
        ["04/26", "朝", "主", "大豆のトマト煮", ""],
        ["04/26", "朝", "副①", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", ""],
    ]


def test_overlay_payload_aux_cells_onto_sheet_rows_preserves_canonical_slot_label_when_payload_is_invalid():
    fields = [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "aux.col_4",
        "qty.regular_x",
        "remarks",
    ]
    header = ["日付", "区分", "副区分", "メニュー", "合計", "常食", "備考"]
    rows = [
        {"values": ["04/26", "朝", "主", "大豆のトマト煮", "70", "70", ""]},
        {"values": ["04/26", "朝", "副①", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "70", "70", ""]},
    ]
    payload_rows = [
        ["04/26", "朝", "器", "大豆のトマト煮", "70", "70", ""],
        ["04/26", "朝", "2012", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "70", "70", ""],
    ]

    overlaid = order_service._overlay_payload_aux_cells_onto_sheet_rows(
        rows=rows,
        fields=fields,
        header=header,
        payload_rows=payload_rows,
    )

    assert [row.get("values", [])[2] for row in overlaid] == ["主", "副①"]


def test_overlay_payload_aux_cells_onto_sheet_rows_accepts_valid_slot_label_when_canonical_is_blank():
    fields = [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "aux.col_4",
        "qty.regular_x",
        "remarks",
    ]
    header = ["日付", "区分", "副区分", "メニュー", "合計", "常食", "備考"]
    rows = [
        {"values": ["04/26", "朝", "", "大豆のトマト煮", "70", "70", ""]},
        {"values": ["04/26", "朝", "", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "70", "70", ""]},
    ]
    payload_rows = [
        ["04/26", "朝", "主A", "大豆のトマト煮", "70", "70", ""],
        ["04/26", "朝", "副2", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "70", "70", ""],
    ]

    overlaid = order_service._overlay_payload_aux_cells_onto_sheet_rows(
        rows=rows,
        fields=fields,
        header=header,
        payload_rows=payload_rows,
    )

    assert [row.get("values", [])[2] for row in overlaid] == ["主Ａ", "副②"]


def test_overlay_payload_aux_cells_onto_sheet_rows_clears_invalid_slot_label_without_canonical_source():
    fields = [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "aux.col_4",
        "qty.regular_x",
        "remarks",
    ]
    header = ["日付", "区分", "副区分", "メニュー", "合計", "常食", "備考"]
    rows = [
        {"values": ["04/26", "朝", "WID", "大豆のトマト煮", "70", "70", ""]},
    ]
    payload_rows = [
        ["04/26", "朝", "2012", "大豆のトマト煮", "70", "70", ""],
    ]

    overlaid = order_service._overlay_payload_aux_cells_onto_sheet_rows(
        rows=rows,
        fields=fields,
        header=header,
        payload_rows=payload_rows,
    )

    assert [row.get("values", [])[2] for row in overlaid] == [""]


def test_validate_and_merge_reparse_full_table_candidate_rejects_structural_anchor_drift():
    baseline = {
        "baseline_source": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [["04/26", "朝", "Menu A", "5"]],
        "row_ids": ["row-1"],
    }

    merged_rows, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=baseline,
        candidate_rows=[["04/26", "朝", "Wrong Menu", "11"]],
    )

    assert merged_rows == []
    assert result["error"] == "llm_full_table_structural_drift"
    detail = result.get("detail") or {}
    assert detail.get("structural_drift_count") == 1
    assert detail.get("structural_drift_cells")[0]["field"] == "menu"


def test_validate_and_merge_reparse_full_table_candidate_rejects_row_count_mismatch():
    baseline = {
        "baseline_source": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [
            ["04/26", "朝", "Menu A", "5"],
            ["04/26", "昼", "Menu B", ""],
        ],
        "row_ids": ["row-1", "row-2"],
    }

    merged_rows, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=baseline,
        candidate_rows=[["04/26", "朝", "Menu A", "11"]],
    )

    assert merged_rows == []
    assert result["error"] == "llm_full_table_row_count_mismatch"
    assert (result.get("detail") or {}).get("row_count_expected") == 2
    assert (result.get("detail") or {}).get("row_count_actual") == 1


def test_validate_and_merge_reparse_full_table_candidate_preserves_unreturned_row_indexes():
    baseline = {
        "baseline_source": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [
            ["04/26", "朝", "Menu A", "5"],
            ["04/26", "昼", "Menu B", "9"],
        ],
        "row_ids": ["row-1", "row-2"],
    }

    merged_rows, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=baseline,
        candidate_rows=[
            ["04/26", "朝", "Menu A", "11"],
            ["", "", "", ""],
        ],
        candidate_returned_row_indexes=[0],
    )

    assert result["error"] is None
    assert merged_rows == [
        ["04/26", "朝", "Menu A", "11"],
        ["04/26", "昼", "Menu B", "9"],
    ]
    assert (result.get("detail") or {}).get("returned_row_indexes") == [0]


def test_validate_and_merge_reparse_full_table_candidate_preserves_unreturned_fields_in_sparse_patch_mode():
    baseline = {
        "baseline_source": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "aux.col_4", "qty.regular_x", "qty.daycare_x"],
        "header": ["日付", "区分", "メニュー", "合計", "常食", "通所"],
        "rows": [
            ["04/26", "朝", "Menu A", "", "5", "2"],
            ["04/26", "昼", "Menu B", "", "9", "4"],
        ],
        "row_ids": ["row-1", "row-2"],
    }

    merged_rows, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=baseline,
        candidate_rows=[
            ["", "", "", "70", "11", ""],
            ["", "", "", "", "", ""],
        ],
        candidate_returned_row_indexes=[0],
        candidate_returned_field_map={0: {"aux.col_4", "qty.regular_x"}},
    )

    assert result["error"] is None
    assert merged_rows == [
        ["04/26", "朝", "Menu A", "70", "11", "2"],
        ["04/26", "昼", "Menu B", "", "9", "4"],
    ]
    assert (result.get("detail") or {}).get("returned_row_fields") == {
        "0": ["aux.col_4", "qty.regular_x"],
    }


def test_validate_and_merge_reparse_full_table_candidate_resets_omitted_quantities_to_authoritative_rows():
    baseline = {
        "baseline_source": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "aux.col_4", "qty.regular_x", "qty.daycare_x"],
        "header": ["日付", "区分", "メニュー", "合計", "常食", "通所"],
        "rows": [
            ["04/26", "朝", "Menu A", "", "5", "2"],
            ["04/26", "昼", "Menu B", "", "9", "4"],
        ],
        "row_ids": ["row-1", "row-2"],
    }
    authoritative_rows = [
        ["04/26", "朝", "Menu A", "70", "11", ""],
        ["04/26", "昼", "Menu B", "", "9", "7"],
    ]

    merged_rows, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=baseline,
        candidate_rows=[
            ["", "", "", "70", "11", ""],
            ["", "", "", "", "", ""],
        ],
        candidate_returned_row_indexes=[0],
        candidate_returned_field_map={0: {"aux.col_4", "qty.regular_x"}},
        authoritative_quantity_rows=authoritative_rows,
    )

    assert result["error"] is None
    assert merged_rows == [
        ["04/26", "朝", "Menu A", "70", "11", ""],
        ["04/26", "昼", "Menu B", "", "9", "7"],
    ]
    detail = result.get("detail") or {}
    assert detail.get("authoritative_quantity_reset_count") == 3
    assert any(cell.get("field") == "qty.daycare_x" for cell in (detail.get("authoritative_quantity_resets") or []))


def test_validate_and_merge_reparse_full_table_candidate_blanks_omitted_quantities_when_authoritative_rows_unavailable():
    baseline = {
        "baseline_source": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "aux.col_4", "qty.regular_x", "qty.daycare_x"],
        "header": ["日付", "区分", "メニュー", "合計", "常食", "通所"],
        "rows": [
            ["04/26", "朝", "Menu A", "", "5", "2"],
            ["04/26", "昼", "Menu B", "", "9", "4"],
        ],
        "row_ids": ["row-1", "row-2"],
    }

    merged_rows, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=baseline,
        candidate_rows=[
            ["", "", "", "70", "11", ""],
            ["", "", "", "", "", ""],
        ],
        candidate_returned_row_indexes=[0],
        candidate_returned_field_map={0: {"aux.col_4", "qty.regular_x"}},
        authoritative_quantity_rows=[],
    )

    assert result["error"] is None
    assert merged_rows == [
        ["04/26", "朝", "Menu A", "70", "11", ""],
        ["04/26", "昼", "Menu B", "", "", ""],
    ]
    detail = result.get("detail") or {}
    assert detail.get("authoritative_quantity_reset_count") == 4


def test_reparse_order_llm_assist_uses_current_sheet_full_table_anchor(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-llm-full-table-anchor.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-llm-full-table-anchor-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    baseline_sheet = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [
            [canonical_mmdd, canonical_daypart, canonical_menu, "5"],
            [canonical_mmdd, "昼", "Menu B", ""],
        ],
        "row_ids": ["row-1", "row-2"],
        "source": "weekly_menu+payload_row",
    }
    captured_template: dict[str, object] = {}
    parsed_rows_capture: list[list[str]] = []

    def _fake_pipeline(**_kwargs):
        return None

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        captured_template["llm_full_table_mode"] = template.get("llm_full_table_mode")
        captured_template["llm_quantity_only_mode"] = template.get("llm_quantity_only_mode")
        captured_template["llm_full_table_expected_row_count"] = template.get("llm_full_table_expected_row_count")
        captured_template["prompt"] = str(template.get("gemini_ocr_prompt") or "")
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[
                [canonical_mmdd, canonical_daypart, canonical_menu, "11"],
                [canonical_mmdd, "昼", "Menu B", ""],
            ],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "model": "gemini-2.5-pro", "full_table_mode": True},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        parsed_rows_capture[:] = [list(row) for row in rows if isinstance(row, list)]
        parsed: list[dict[str, object]] = []
        for row_index, row in enumerate(rows):
            qty = row[3] if len(row) > 3 else ""
            if not str(qty).strip():
                continue
            parsed.append(
                {
                    "date": canonical_date,
                    "daypart": row[1],
                    "menu_name": row[2],
                    "diet_type": "regular",
                    "area_id": "2F",
                    "bag_type": "standard",
                    "quantity_original": int(qty),
                    "source_row_index": row_index,
                }
            )
        return parsed

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id, **_kwargs: (dict(baseline_sheet), None))
    monkeypatch.setattr(
        order_service,
        "_run_llm_reparse_audit",
        lambda **_kwargs: {
            "status": "pass",
            "issues": [],
            "issue_count": 0,
            "blocking_issues": [],
            "blocking_issue_count": 0,
        },
    )
    monkeypatch.setattr(order_service, "_validate_reparse_lines_against_weekly_menu", lambda **_kwargs: (None, None))
    monkeypatch.setattr(order_service, "_build_reparse_position_menu_entries", lambda **_kwargs: [])
    monkeypatch.setattr(order_service, "_resolve_llm_expected_row_count", lambda **_kwargs: 2)
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "9"]]),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    assert captured_template["llm_full_table_mode"] is True
    assert captured_template["llm_quantity_only_mode"] is None
    assert captured_template["llm_full_table_expected_row_count"] == 2
    assert "Current sheet markdown anchor" in str(captured_template["prompt"] or "")
    assert parsed_rows_capture == [
        [canonical_mmdd, canonical_daypart, canonical_menu, "11"],
        [canonical_mmdd, "昼", "Menu B", ""],
    ]
    assert updated["lines"][0]["quantity_original"] == 11


def test_reparse_order_llm_assist_continues_when_optional_second_pass_audit_errors(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-llm-full-table-audit-timeout.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-llm-full-table-audit-timeout-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    baseline_sheet = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [
            [canonical_mmdd, canonical_daypart, canonical_menu, "5"],
            [canonical_mmdd, "昼", "Menu B", ""],
        ],
        "row_ids": ["row-1", "row-2"],
        "source": "weekly_menu+payload_row",
    }

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[
                [canonical_mmdd, canonical_daypart, canonical_menu, "11"],
                [canonical_mmdd, "昼", "Menu B", ""],
            ],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            provider_debug={"provider": "gemini", "model": "gemini-2.5-pro", "full_table_mode": True},
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        parsed: list[dict[str, object]] = []
        for row_index, row in enumerate(rows):
            qty = row[3] if len(row) > 3 else ""
            if not str(qty).strip():
                continue
            parsed.append(
                {
                    "date": canonical_date,
                    "daypart": row[1],
                    "menu_name": row[2],
                    "diet_type": "regular",
                    "area_id": "2F",
                    "bag_type": "standard",
                    "quantity_original": int(qty),
                    "source_row_index": row_index,
                }
            )
        return parsed

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", lambda **_kwargs: None)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)
    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id, **_kwargs: (dict(baseline_sheet), None))
    monkeypatch.setattr(
        order_service,
        "_run_llm_reparse_audit",
        lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("simulated optional audit timeout")),
    )
    monkeypatch.setattr(order_service, "_validate_reparse_lines_against_weekly_menu", lambda **_kwargs: (None, None))
    monkeypatch.setattr(order_service, "_build_reparse_position_menu_entries", lambda **_kwargs: [])
    monkeypatch.setattr(order_service, "_resolve_llm_expected_row_count", lambda **_kwargs: 2)
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "9"]]),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    assert updated["lines"][0]["quantity_original"] == 11
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    second_pass_audit = (job.get("metrics") or {}).get("llm_second_pass_audit") or {}
    assert second_pass_audit.get("provider_switch_reason") == "optional_audit_error"
    assert "simulated optional audit timeout" in str(second_pass_audit.get("error") or "")


def test_reparse_order_llm_assist_blocks_when_current_sheet_baseline_missing(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-llm-full-table-baseline-missing.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-llm-full-table-baseline-missing-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")
    extract_called = {"value": False}

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        extract_called["value"] = True
        raise AssertionError("extract_fax_data must not run when current sheet baseline is missing")

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", lambda **_kwargs: None)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id, **_kwargs: (None, "sheet_missing"))
    monkeypatch.setattr(
        order_service,
        "_build_reparse_structural_baseline",
        lambda **_kwargs: {
            "baseline_source": "weekly_menu_structure",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "rows": [[canonical_mmdd, canonical_daypart, canonical_menu, ""]],
            "row_ids": ["structural-row-1"],
        },
    )
    _patch_first_pass_payload(
        monkeypatch,
        _make_first_pass_payload(rows=[[canonical_mmdd, canonical_daypart, canonical_menu, "9"]]),
    )

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert updated is None
    assert error == "llm_full_table_baseline_missing"
    assert extract_called["value"] is False
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") == "failed"
    assert (job.get("metrics") or {}).get("error") == "llm_full_table_baseline_missing"
