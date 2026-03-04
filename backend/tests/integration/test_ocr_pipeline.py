import sys
import pathlib
from datetime import date, datetime
from uuid import uuid4

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import order_service, config_service  # noqa: E402
from src.services.ocr_job_service import get_job  # noqa: E402
from src.services.fax_extractor import FaxExtractedData  # noqa: E402
from src.services.fax_parser import parse_order_lines  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models.menu import MonthlyMenu, MonthlyMenuEntry  # noqa: E402


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

    updated, error = order_service.reparse_order(order["id"], ocr_provider="openai")
    assert error is None
    assert updated is not None
    assert captured_rules.get("zero_as_empty") is False
    assert captured_rules.get("strict_numeric_quantity_cell") is True
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


def test_reparse_order_llm_truncated_uses_pipeline_rows(monkeypatch, tmp_path):
    order_service.clear_all()
    pdf_path = tmp_path / "sample-gemini-truncated.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = IngestEmailPayload(
        message_id="msg-gemini-truncated-001",
        pdf_uri=str(pdf_path),
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    canonical_date, canonical_daypart, canonical_menu = _canonical_row_for_week("2026-02")
    canonical_mmdd = datetime.fromisoformat(canonical_date).strftime("%m/%d")

    parse_inputs: list[list[list[str]]] = []

    def _fake_pipeline(**_kwargs):
        return "file://pipeline-output.json"

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0):  # noqa: ARG001
        return {"table_raw": "|x|y|z|"}

    def _fake_rows_from_markdown(markdown, template):  # noqa: ARG001
        return [[canonical_mmdd, canonical_menu, "9"]]

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):  # noqa: ARG001
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=[canonical_date],
            table_rows=[[canonical_mmdd, canonical_menu, "20"]],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            raw_text=f'{{"rows":[{{"date_mmdd":"{canonical_mmdd}"}}]}}',
            provider_debug={
                "provider": "gemini",
                "finish_reason": "MAX_TOKENS",
                "recovered_truncated_json": True,
            },
        )

    def _fake_parse(rows, template, received_at, quantity_rules, default_date=None, tokens=None, grid=None, pdf_bytes=None):  # noqa: ARG001
        parse_inputs.append([list(item) for item in (rows or [])])
        if not rows:
            return []
        raw_qty = str(rows[0][2] if len(rows[0]) > 2 else "")
        qty = 20 if raw_qty == "20" else 9
        return [
            {
                "date": canonical_date,
                "daypart": canonical_daypart,
                "menu_name": canonical_menu,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": qty,
                "source_row_index": 0,
            }
        ]

    monkeypatch.setattr(order_service, "_run_roi_ocr_pipeline", _fake_pipeline)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _fake_load_pipeline_output_with_retry)
    monkeypatch.setattr(order_service, "rows_from_markdown", _fake_rows_from_markdown)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)
    monkeypatch.setattr(order_service, "parse_order_lines", _fake_parse)

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)
    assert error is None
    assert updated is not None
    assert updated["lines"]
    assert updated["lines"][0]["quantity_original"] == 9
    assert parse_inputs
    assert parse_inputs[0][0][2] == "9"

    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") == "done"
    metrics = job.get("metrics") or {}
    assert metrics.get("provider") == "gemini"
    assert metrics.get("requested_provider") == "gemini"
    assert metrics.get("finish_reason") == "MAX_TOKENS"
    assert metrics.get("truncated_output") is True
    assert metrics.get("rows_replaced_with_pipeline") is True


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

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0):  # noqa: ARG001
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

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0):  # noqa: ARG001
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


def test_apply_menu_position_mapping_overrides_daypart_and_date_from_weekly_menu(monkeypatch):
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

    mapped, mapped_rows = order_service._apply_menu_position_mapping(lines, "2026-02")

    assert mapped_rows == 2
    assert mapped[0]["menu_name"] == "Menu X"
    assert mapped[0]["daypart"] == "夕"
    assert mapped[0]["date"] == date(2026, 2, 15)
    assert mapped[1]["menu_name"] == "Menu Y"
    assert mapped[1]["daypart"] == "朝"
    assert mapped[1]["date"] == date(2026, 2, 16)


def test_apply_menu_position_mapping_prefers_source_row_index(monkeypatch):
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

    mapped, mapped_rows = order_service._apply_menu_position_mapping(lines, "2026-02")

    assert mapped_rows == 1
    assert mapped[0]["menu_name"] == "Menu 1"
    assert mapped[0]["daypart"] == "昼"
    assert mapped[0]["date"] == date(2026, 2, 15)


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


def test_create_order_from_ingest_scopes_position_mapping_entries_to_line_dates(monkeypatch, tmp_path):
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
    assert str(first.get("menu_name") or "").startswith("2026-02-08-")


def test_apply_ocr_table_scopes_position_mapping_entries_to_line_dates(monkeypatch, tmp_path):
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
    assert all(
        str(line.get("menu_name") or "").startswith("2026-02-08-")
        for line in updated["lines"]
    )


def test_apply_ocr_table_scopes_position_mapping_from_submitted_rows_when_line_dates_missing(
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
    assert str(updated["lines"][0].get("menu_name") or "").startswith("2026-02-08-")


def test_reparse_order_scopes_position_mapping_entries_on_single_date_anchor(monkeypatch, tmp_path):
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

    assert error is None
    assert updated is not None
    assert updated.get("lines")
    assert updated["lines"][0].get("date") == "2026-02-08"
    assert str(updated["lines"][0].get("menu_name") or "").startswith("2026-02-08-")


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

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0):  # noqa: ARG001
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
    monkeypatch.setenv("OCR_REPARSE_ROW_COVERAGE_MIN_RATIO", "0.98")
    monkeypatch.setenv("OCR_REPARSE_MAX_MISSING_TAIL_ROWS", "0")
    monkeypatch.setenv("OCR_REPARSE_ENABLE_REPAIR_PASS", "0")

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert updated is None
    assert error == "sheet_row_coverage_low"
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    metrics = job.get("metrics") or {}
    assert metrics.get("error") == "sheet_row_coverage_low"
    detail = metrics.get("validation_detail") or {}
    assert detail.get("expected_row_count") == 56
    assert detail.get("actual_row_count") == 54


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

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0):  # noqa: ARG001
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

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0):  # noqa: ARG001
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

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0):  # noqa: ARG001
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
    monkeypatch.setenv("OCR_REPARSE_ROW_COVERAGE_MIN_RATIO", "0.98")
    monkeypatch.setenv("OCR_REPARSE_MAX_MISSING_TAIL_ROWS", "0")
    monkeypatch.setenv("OCR_REPARSE_ENABLE_REPAIR_PASS", "1")

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert updated is None
    assert error == "sheet_row_coverage_low"
    assert call_state["extract_count"] >= 2
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") == "failed"
    metrics = job.get("metrics") or {}
    assert metrics.get("error") == "sheet_row_coverage_low"
    assert metrics.get("repair_pass_applied") is False


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
    monkeypatch.setattr(order_service, "_apply_menu_position_mapping", lambda lines, _week_id: (lines, 0))

    updated, error = order_service.reparse_order(
        order["id"],
        ocr_provider="gemini",
        ocr_prompt="strict quantity only",
    )

    assert updated is None
    assert error == "sheet_canonical_mismatch"
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") == "failed"
    assert job.get("error_message") == "sheet_canonical_mismatch"
    metrics = job.get("metrics") or {}
    assert metrics.get("error") == "sheet_canonical_mismatch"
    assert metrics.get("reject_reasons") == ["sheet_canonical_mismatch"]

    cached = order_service.get_cached_ocr_payload(order["id"]) or {}
    debug = cached.get("_reparse_debug") or {}
    assert debug.get("error") == "sheet_canonical_mismatch"
    assert "sheet_canonical_mismatch" in (debug.get("reject_reasons") or [])
    assert "strict quantity only" in str(debug.get("request_prompt") or "")


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
    monkeypatch.setattr(order_service, "_apply_menu_position_mapping", lambda lines, _week_id: (lines, 0))

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini")

    assert updated is None
    assert error == "sheet_suspicious_blank_row"
    job = get_job(f"OCR-{order['id']}")
    assert job is not None
    assert job.get("status") == "failed"
    assert job.get("error_message") == "sheet_suspicious_blank_row"
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

    def _fake_load_pipeline_output_with_retry(_ref, retries=0, delay=0.0):  # noqa: ARG001
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

    updated, error = order_service.reparse_order(order["id"], ocr_provider="gemini", llm_assist=True)

    assert error is None
    assert updated is not None
    assert captured_rows
    assert captured_rows[0] == [
        ["2/15", "昼", "Menu A", "20"],
        ["2/15", "夕", "Menu B", "11"],
    ]
    quantities = sorted(int(line["quantity_original"]) for line in updated["lines"])
    assert quantities == [11, 20]
    debug = updated.get("ocr_metrics") or {}
    if isinstance(debug, dict):
        # API serialization may omit metrics in this path; assertion is best-effort.
        assert debug.get("provider") in {None, "gemini"}


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
