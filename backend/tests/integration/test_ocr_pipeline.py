import sys
import pathlib
from datetime import date, datetime
from uuid import uuid4
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import config_service, order_service  # noqa: E402
from src.services.ocr_job_service import get_job  # noqa: E402
from src.services.fax_extractor import FaxExtractedData  # noqa: E402
from src.services.fax_parser import parse_order_lines  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models.menu import MonthlyMenu, MonthlyMenuEntry  # noqa: E402
from src.models.order_ocr_cache import OrderOcrCache  # noqa: E402
from tests.helpers.owned_order import owned_registration  # noqa: E402,F401


@pytest.mark.parametrize('provider,assist', [
    ('gemini', False), ('gemini', True), ('openai', False),
    ('pipeline', False), (None, False), (None, True),
])
def test_registered_original_cannot_start_unowned_legacy_reparse(owned_registration, provider, assist):
    from src.db import get_engine
    from src.services.canonical_facility_store import read_snapshot
    from tests.unit.test_disabled_facility_columns import _snapshot

    order = owned_registration(message_id='unowned-provider-contract', facility_id='AUTHORED-REPARSE',
        received_at=datetime(2026, 2, 1, 9), week='2026-02@2026-02-01~2026-02-07',
        effective_date='2026-02-01', lines=[{
            'date': '2026-02-01', 'daypart': '朝', 'menu_name': 'Authored menu',
            'diet_type': 'regular', 'area_id': '2F', 'bag_type': 'standard', 'quantity_original': 7,
        }])
    before = _snapshot(get_engine())
    original = pathlib.Path(order['document'])
    original_bytes, revision = original.read_bytes(), read_snapshot().revision
    result, error = order_service.reparse_order(order['id'], ocr_provider=provider, llm_assist=assist)
    assert result is None and error == 'workflow_job_required'
    assert _snapshot(get_engine()) == before
    assert original.read_bytes() == original_bytes
    assert read_snapshot().revision == revision


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
        explicit_week="2026-02@2026-02-15~2026-02-15",
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
        explicit_week="2026-02@2026-02-15~2026-02-15",
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
        explicit_week="2026-02@2026-02-15~2026-02-15",
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
        explicit_week="2026-02@2026-02-15~2026-02-15",
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

    def _fake_weekly_entries(_week_id: str, facility_id: str | None):
        assert _week_id == "2026-02"
        assert facility_id is None
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

    def _fake_weekly_entries(_week_id: str, facility_id: str | None):
        assert _week_id == "2026-02"
        assert facility_id is None
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

    def _fake_weekly_entries(_week_id: str, facility_id: str | None):
        assert _week_id == "2026-02"
        assert facility_id is None
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


def test_owned_registration_does_not_apply_legacy_position_mapping(monkeypatch, owned_registration):
    order_service.clear_all()

    def forbidden_mapping(*_args, **_kwargs):
        raise AssertionError("explicit registration lines must not use legacy position mapping")

    monkeypatch.setattr(order_service, "_apply_menu_position_mapping", forbidden_mapping)
    monkeypatch.setattr(order_service, "_apply_menu_position_mapping_safe", forbidden_mapping)

    order = owned_registration(
        message_id="msg-ingest-position-scope-001",
        facility_id="FAC00001",
        received_at=datetime(2026, 2, 8, 9, 0, 0),
        week="2026-02@2026-02-08~2026-02-14",
        effective_date="2026-02-08",
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
    assert first.get("quantity_original") == 6


@pytest.fixture
def position_case(isolated_formal_schema, tmp_path, monkeypatch):
    from tests.contract.auth_support import registered_hospital_operator
    from tests.contract.test_orders_ocr_sheet_history_api import HistoryCase

    names = ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    columns = [{"index": index, "source_index": index, "name": name, "header": name, "role": role}
        for index, (name, role) in enumerate(zip(names, ["date", "daypart", "menu_name", "quantity"], strict=True))]
    columns[3].update(diet_type="regular", area_id="2F")
    with registered_hospital_operator(monkeypatch) as actor:
        case = HistoryCase(isolated_formal_schema, tmp_path, monkeypatch, actor,
            week="2026-02@2026-02-08~2026-02-14", period="2026-02@2026-02-08~2026-02-14",
            received=datetime(2026, 2, 8, 9), columns=columns, lines=[],
            menu_entries=[{"id": f"position-menu-{index}", "monthly_menu_id": "2026-02",
                "menu_date": date(2026, 2, 8), "daypart": "朝", "name": menu,
                "slot_index": index, "diet_type": "regular"}
                for index, menu in enumerate(["dummy-0", "dummy-1", "dummy"])])
        case.produce(cell_values="6")
        yield case


def test_apply_ocr_table_does_not_apply_legacy_position_mapping(monkeypatch, position_case):
    case = position_case
    def forbidden_mapping(*_args, **_kwargs):
        raise AssertionError("selected rows must not use legacy menu position mapping")
    monkeypatch.setattr(order_service, "_apply_menu_position_mapping", forbidden_mapping)
    monkeypatch.setattr(order_service, "_apply_menu_position_mapping_safe", forbidden_mapping)
    updated, error = order_service.apply_ocr_table(case.id,
        header=["日付", "区分", "メニュー", "常食2F"],
        rows=[["2/8", "朝", "dummy-0", "6"], ["2/8", "朝", "dummy-1", "7"]],
        ui_mode="sheet", fields=["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        row_ids=["row-1", "row-2"])
    assert error is None and updated is not None, error
    assert {line["date"] for line in updated["lines"]} == {"2026-02-08"}
    assert [line["menu_name"] for line in updated["lines"]] == ["dummy-0", "dummy-1"]
    assert [line["quantity_original"] for line in updated["lines"]] == [6, 7]


def test_apply_ocr_table_uses_submitted_identity_not_legacy_parser_guess(monkeypatch, position_case):
    case = position_case
    def forbidden_guess(*_args, **_kwargs):
        raise AssertionError("submitted dated cells must not borrow legacy parser identity")
    monkeypatch.setattr(order_service, "_apply_menu_position_mapping", forbidden_guess)
    monkeypatch.setattr(order_service, "_apply_menu_position_mapping_safe", forbidden_guess)
    monkeypatch.setattr(order_service, "parse_order_lines", forbidden_guess)
    updated, error = order_service.apply_ocr_table(case.id,
        header=["日付", "区分", "メニュー", "常食2F"], rows=[["2/8", "朝", "dummy", "6"]],
        ui_mode="sheet", fields=["date_mmdd", "daypart", "menu", "qty.regular_2f"], row_ids=["row-1"])
    assert error is None and updated is not None, error
    assert len(updated["lines"]) == 1
    assert updated["lines"][0]["date"] == "2026-02-08"
    assert updated["lines"][0]["menu_name"] == "dummy"
    assert updated["lines"][0]["quantity_original"] == 6






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
        "columns": [{"index": 3, "source_index": 3, "role": "quantity",
                     "name": "qty.regular_x", "diet_type": "regular", "area_id": "X"}],
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
        "columns": [{"index": 3, "source_index": 3, "role": "quantity",
                     "name": "qty.regular_x", "diet_type": "regular", "area_id": "X"}],
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
        "columns": [{"index": 3, "source_index": 3, "role": "quantity",
                     "name": "qty.regular_x", "diet_type": "regular", "area_id": "X"}],
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












































@pytest.fixture
def revision_case(isolated_formal_schema, tmp_path, monkeypatch):
    """Owned February source; only external recognition/rendering is doubled."""
    from copy import deepcopy
    import json
    from tests.contract.auth_support import registered_hospital_operator
    from tests.contract.test_orders_ocr_sheet_history_api import HistoryCase
    from src.services.template_field_schema_service import canonical_field_name_from_template_column

    specimens = json.loads((ROOT / "src/data/facility_master.template.json").read_text())["facilities"]
    specimen = next(item for item in specimens if item["facility_id"] == "FAC00004")
    template = specimen.get("fax_template") or specimen["fax_template_override"]
    columns = deepcopy(template["columns"])
    for column in columns:
        column["name"] = canonical_field_name_from_template_column(column)
    with registered_hospital_operator(monkeypatch) as actor:
        case = HistoryCase(isolated_formal_schema, tmp_path, monkeypatch, actor,
            facility="FAC00004", week="2026-02@2026-02-01~2026-02-07",
            period="2026-02@2026-02-01~2026-02-07", received=datetime(2026, 2, 15, 9),
            columns=columns, lines=[], menu_entries=[{
                "id": "revision-february-menu", "monthly_menu_id": "2026-02",
                "menu_date": date(2026, 2, 1), "daypart": "昼", "name": "Menu A",
                "slot_index": 0, "diet_type": "regular",
            }])
        case.selected_sheet = case.produce(cell_values="5")["sheet"]
        yield case


def _revision_sheet(case, *, total="70", expanded=False):
    from copy import deepcopy
    sheet = deepcopy(case.selected_sheet)
    fields = sheet["fields"]
    aux = [field for field in fields if field.startswith("aux.")]
    assert len(aux) >= 2
    assert all(field in fields for field in ("qty.regular_x", "qty.no_fish_x", "qty.no_fried_x"))
    rows = []
    values = [(total, total, "", ""), ("", "", "3", ""), ("", "", "", "6")] if expanded else [(total, total, "", "")]
    for aux_total, regular, no_fish, no_fried in values:
        row = [""] * len(fields)
        for field, value in {
            "date_mmdd": "02/01", "daypart": "昼",
            "menu" if "menu" in fields else "menu_name": "Menu A",
            aux[0]: "主", aux[1]: aux_total,
            "qty.regular_x": regular, "qty.no_fish_x": no_fish, "qty.no_fried_x": no_fried,
        }.items():
            row[fields.index(field)] = value
        rows.append(row)
    sheet.update(rows=rows, row_ids=[f"row-{index + 1}" for index in range(len(rows))])
    return sheet


def _save_revision_sheet(case, sheet):
    result, error = order_service.save_ocr_sheet_exact(case.id, fields=sheet["fields"],
        header=sheet["header"], rows=sheet["rows"], row_ids=sheet["row_ids"], ui_mode="sheet")
    assert error is None and result is not None, error
    payload = order_service.get_cached_ocr_payload(case.id)
    latest = payload["_edited_ocr"]["latest"]
    assert latest["sheet_save_mode"] == "exact"
    assert latest["base_evidence_run_id"] == case.evidence
    assert latest["rows"] == sheet["rows"] and latest["row_ids"] == sheet["row_ids"]
    return payload


def test_selected_exact_revision_supersedes_stale_reject_candidate_and_preserves_aux_display(revision_case):
    from copy import deepcopy
    case = revision_case
    payload = _save_revision_sheet(case, _revision_sheet(case))
    stale = deepcopy(payload)
    stale["_edited_ocr"]["latest"].update(
        revision_id="unselected-reject", sheet_save_mode="draft_candidate", draft_from_reparse_reject=True,
        rows=_revision_sheet(case, total="5")["rows"])
    synced, changed = order_service._sync_edited_ocr_latest_from_canonical_revision(
        order_id=case.id, payload=stale)
    assert changed is True
    assert synced["_edited_ocr"]["latest"] == payload["_edited_ocr"]["latest"]
    assert synced["_edited_ocr"]["latest"]["rows"][0][synced["_edited_ocr"]["latest"]["fields"].index("qty.regular_x")] == "70"
    assert order_service.get_cached_ocr_payload(case.id) == payload


def test_selected_exact_revision_same_sheet_resave_preserves_values_and_evidence(revision_case):
    case = revision_case
    sheet = _revision_sheet(case)
    first = _save_revision_sheet(case, sheet)["_edited_ocr"]["latest"]
    second = _save_revision_sheet(case, sheet)["_edited_ocr"]["latest"]
    assert first["rows"] == second["rows"] == sheet["rows"]
    assert first["row_ids"] == second["row_ids"] == sheet["row_ids"]
    assert first["after_digest"] == second["after_digest"]
    assert first["base_evidence_run_id"] == second["base_evidence_run_id"] == case.evidence
    # A deliberate operator save is not an automatic idempotent OCR adoption.
    assert first["revision_id"] != second["revision_id"]


def test_selected_exact_revision_refreshes_latest_without_replacing_raw_source(revision_case):
    from copy import deepcopy
    case = revision_case
    before = _save_revision_sheet(case, _revision_sheet(case, total="5"))
    after = _save_revision_sheet(case, _revision_sheet(case))
    raw_source = deepcopy(after["_edited_ocr"]["raw_output"])
    stale = deepcopy(after)
    stale["_edited_ocr"]["latest"] = before["_edited_ocr"]["latest"]
    synced, changed = order_service._sync_edited_ocr_latest_from_canonical_revision(
        order_id=case.id, payload=stale)
    assert changed is True
    assert synced["_edited_ocr"]["latest"] == after["_edited_ocr"]["latest"]
    assert synced["_edited_ocr"]["raw_output"] == raw_source
    assert order_service.get_cached_ocr_payload(case.id) == after


def test_selected_exact_revision_overrides_unselected_cached_latest_without_writing(revision_case):
    from copy import deepcopy
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot
    case = revision_case
    payload = _save_revision_sheet(case, _revision_sheet(case))
    before = _snapshot(get_engine())
    stale = deepcopy(payload)
    stale["_edited_ocr"]["latest"] = {"revision_id": "foreign-cached-revision", "rows": [["999"]]}
    synced, changed = order_service._sync_edited_ocr_latest_from_canonical_revision(
        order_id=case.id, payload=stale)
    assert changed is True
    assert synced["_edited_ocr"]["latest"] == payload["_edited_ocr"]["latest"]
    assert _snapshot(get_engine()) == before


def test_selected_exact_revision_matching_payload_needs_no_refresh(revision_case):
    from copy import deepcopy
    case = revision_case
    payload = _save_revision_sheet(case, _revision_sheet(case))
    before = deepcopy(payload)
    synced, changed = order_service._sync_edited_ocr_latest_from_canonical_revision(
        order_id=case.id, payload=payload)
    assert changed is False
    assert synced == payload == before


def test_selected_exact_revision_keeps_expanded_rows_and_auxiliary_values_immutable(revision_case):
    from copy import deepcopy
    case = revision_case
    sheet = _revision_sheet(case, expanded=True)
    payload = _save_revision_sheet(case, sheet)
    stale = deepcopy(payload)
    stale["_edited_ocr"]["latest"] = {"revision_id": "collapsed", "rows": [_revision_sheet(case)["rows"][0]]}
    synced, changed = order_service._sync_edited_ocr_latest_from_canonical_revision(
        order_id=case.id, payload=stale)
    assert changed is True
    latest = synced["_edited_ocr"]["latest"]
    assert latest["row_count"] == 3
    fields, rows = latest["fields"], latest["rows"]
    aux = [field for field in fields if field.startswith("aux.")]
    assert [row[fields.index(aux[0])] for row in rows] == ["主", "主", "主"]
    assert [row[fields.index(aux[1])] for row in rows] == ["70", "", ""]
    assert [row[fields.index("qty.regular_x")] for row in rows] == ["70", "", ""]
    assert [row[fields.index("qty.no_fish_x")] for row in rows] == ["", "3", ""]
    assert [row[fields.index("qty.no_fried_x")] for row in rows] == ["", "", "6"]
    assert latest["row_ids"] == ["row-1", "row-2", "row-3"]
    assert order_service.get_cached_ocr_payload(case.id) == payload


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
        "columns": [{"index": 3, "source_index": 3, "role": "quantity",
                     "name": "qty.regular_x", "diet_type": "regular", "area_id": "X"}],
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
        "columns": [{"index": 3, "source_index": 3, "role": "quantity",
                     "name": "qty.regular_x", "diet_type": "regular", "area_id": "X"}],
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
        "columns": [{"index": 3, "source_index": 3, "role": "quantity",
                     "name": "qty.regular_x", "diet_type": "regular", "area_id": "X"}],
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






def test_reconcile_completed_output_without_execution_owner_cannot_resume(monkeypatch, tmp_path, owned_registration):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    order = owned_registration(
        message_id="msg-gemini-awaiting-output-reconcile-001",
        received_at=datetime(2026, 2, 16, 9, 0, 0),
        facility_id="FAC00001",
        week="2026-02@2026-02-16~2026-02-22",
        effective_date="2026-02-16", lines=[],
    )
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

    before = _snapshot(get_engine())
    output_bytes = output_path.read_bytes()

    def _fake_reparse(*_args, **_kwargs):
        raise AssertionError("a completed file is not execution ownership")

    monkeypatch.setattr(order_service, "reparse_order", _fake_reparse)

    assert order_service.reconcile_completed_ocr_job(job_id) is False
    assert _snapshot(get_engine()) == before
    assert output_path.read_bytes() == output_bytes
    job = get_job(job_id)
    assert job is not None
    assert job["status"] == "awaiting_output"
    assert job["metrics"]["result_state"] == "awaiting_output"




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


def test_build_rows_from_menu_entries_populates_slot_label_from_weekly_menu_category(repository_layout_source):
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
