"""Selected-period mapping regressions; no recognizer or quantity substitution."""

import ast
import inspect
from datetime import date, datetime

import pytest

from src.services.fax_parser import parse_order_lines
from src.services.ingest_policy import parse_date_string


WEEK = "2026-02@2026-02-15~2026-02-21"
LATE_RECEIPT = datetime(2026, 10, 12)
POLICY = {"week_id_policy": {"date_format": "MM/DD"}}


@pytest.mark.parametrize("received", [datetime(2026, 2, 20), LATE_RECEIPT, datetime(2028, 10, 12)])
def test_selected_period_preserves_original_recovery_date_despite_late_receipt(received):
    assert parse_date_string("02/20", received, policy=POLICY, explicit_week=WEEK) == date(2026, 2, 20)


@pytest.mark.parametrize("raw,expected", [
    ("12/31", date(2026, 12, 31)), ("01/01", date(2027, 1, 1)),
    ("2026-12-31", date(2026, 12, 31)), ("2027/01/01", date(2027, 1, 1)),
])
def test_selected_period_resolves_year_crossing(raw, expected):
    assert parse_date_string(raw, LATE_RECEIPT, policy=POLICY,
        explicit_week="2026-12@2026-12-28~2027-01-03") == expected


@pytest.mark.parametrize("raw", ["2026/02/20", "2026-02-20", "２０２６年０２月２０日"])
def test_explicit_year_is_preserved_even_under_month_day_policy(raw):
    assert parse_date_string(raw, LATE_RECEIPT, policy=POLICY, explicit_week=WEEK) == date(2026, 2, 20)


@pytest.mark.parametrize("raw", ["2027/02/20", "2025-02-20", "02/22", "02/30", "not a date", "02/20,02/21", "2026/02/20,02/21"])
def test_nonempty_outside_invalid_or_ambiguous_input_stops(raw):
    with pytest.raises(ValueError, match="ingest_date_.*selected_period"):
        parse_date_string(raw, LATE_RECEIPT, policy=POLICY, explicit_week=WEEK)


def test_repeated_month_day_in_long_selected_period_stops():
    with pytest.raises(ValueError, match="ingest_date_unresolved_in_selected_period"):
        parse_date_string("02/20", LATE_RECEIPT, policy=POLICY,
            explicit_week="2026-02@2026-02-15~2027-02-21")


@pytest.mark.parametrize("raw", [None, "", " \t "])
def test_empty_date_remains_none(raw):
    assert parse_date_string(raw, LATE_RECEIPT, policy=POLICY, explicit_week=WEEK) is None


@pytest.mark.parametrize("raw", ["02/20", "2026-02-20", None, "", " "])
def test_missing_period_stops_even_for_empty_or_explicit_year_input(raw):
    with pytest.raises(ValueError, match="week_hint_required"):
        parse_date_string(raw, LATE_RECEIPT, policy=POLICY)


def _template(first_seen=False):
    return {"header_rows": 0, "fill_missing_date_with_first_seen": first_seen,
        "columns": [
            {"index": 0, "role": "date"}, {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "2F"},
        ]}


@pytest.mark.parametrize("quantity,expected", [("2", 2), ("0", 0), ("", None)])
def test_real_parser_date_cells_use_period_without_quantity_changes(quantity, expected):
    lines = parse_order_lines([["02/20", "昼", "Menu A", quantity]], _template(),
        LATE_RECEIPT, {"zero_as_empty": False}, date_policy=POLICY, explicit_week=WEEK)
    assert len(lines) == 1
    assert lines[0]["date"] == date(2026, 2, 20)
    assert lines[0]["quantity_original"] == expected
    assert lines[0]["quantity_corrected"] is None
    assert lines[0]["area_id"] == "2F"
    assert lines[0]["menu_name"] == "Menu A"


def test_real_parser_first_seen_date_uses_selected_period():
    lines = parse_order_lines([["", "昼", "Menu A", "2"], ["02/20", "昼", "Menu B", "0"]],
        _template(first_seen=True), LATE_RECEIPT, {"zero_as_empty": False},
        date_policy=POLICY, explicit_week=WEEK)
    assert [line["date"] for line in lines] == [date(2026, 2, 20), date(2026, 2, 20)]
    assert [line["quantity_original"] for line in lines] == [2, 0]


@pytest.mark.parametrize("first_seen", [False, True])
def test_real_parser_rejects_outside_date_in_both_paths(first_seen):
    with pytest.raises(ValueError, match="ingest_date_outside_selected_period"):
        parse_order_lines([["2027/02/20", "昼", "Menu A", "2"]], _template(first_seen),
            LATE_RECEIPT, {"zero_as_empty": False}, date_policy=POLICY, explicit_week=WEEK)


def test_firstpass_source_passes_selected_week_to_hints_and_table_parser():
    from src.workers.ingest_worker import map_own_ingest_result

    tree = ast.parse(inspect.getsource(map_own_ingest_result))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id in {"parse_date_string", "parse_order_lines"}]
    assert sorted(call.func.id for call in calls) == ["parse_date_string", "parse_order_lines"]
    for call in calls:
        supplied = [keyword.value for keyword in call.keywords if keyword.arg == "explicit_week"]
        assert len(supplied) == 1
        assert isinstance(supplied[0], ast.Attribute)
        assert supplied[0].attr == "week_hint"
        assert isinstance(supplied[0].value, ast.Name)
        assert supplied[0].value.id == "payload"


@pytest.mark.parametrize("raw", ["02/20", "2026-02-20", date(2026, 2, 20)])
def test_order_date_helpers_share_selected_period(raw):
    from src.services import order_service

    assert order_service._extract_entry_date_from_sheet_cell(raw, LATE_RECEIPT,
        explicit_week=WEEK) == date(2026, 2, 20)
    assert order_service._collect_dates_from_sheet_rows([{"values": [raw]}],
        fields=["date"], received_at=LATE_RECEIPT, explicit_week=WEEK) == {date(2026, 2, 20)}
    assert order_service._collect_sheet_dates_from_rows([[raw]],
        fields=["date"], received_at=LATE_RECEIPT, explicit_week=WEEK) == {date(2026, 2, 20)}


@pytest.mark.parametrize("raw", ["2027-02-20", date(2027, 2, 20), "金", "02/20,02/21"])
def test_order_date_helpers_do_not_reenter_legacy_resolution(raw):
    from src.services import order_service

    with pytest.raises(ValueError, match="ingest_date_.*selected_period"):
        order_service._extract_entry_date_from_sheet_cell(raw, LATE_RECEIPT, explicit_week=WEEK)
    with pytest.raises(ValueError, match="ingest_date_.*selected_period"):
        order_service._collect_dates_from_sheet_rows([{"values": [raw]}],
            fields=["date"], received_at=LATE_RECEIPT, explicit_week=WEEK)
    with pytest.raises(ValueError, match="ingest_date_.*selected_period"):
        order_service._collect_sheet_dates_from_rows([[raw]],
            fields=["date"], received_at=LATE_RECEIPT, explicit_week=WEEK)


def _source_template():
    return {"template_id": "synthetic-period", "header_rows": 0, "columns": [
        {"index": 0, "source_index": 0, "role": "date", "name": "date"},
        {"index": 1, "source_index": 1, "role": "daypart", "name": "daypart"},
        {"index": 2, "source_index": 2, "role": "menu_name", "name": "menu"},
        {"index": 3, "source_index": 3, "role": "quantity", "name": "qty.regular_2f",
         "diet_type": "regular", "area_id": "2F"},
    ]}


def test_payload_collector_resolves_late_receipt_without_legacy_year():
    from src.services import order_service

    payload = {"table_rows": [["02/20", "昼", "Menu A", "2"]]}
    assert order_service._collect_sheet_dates_from_payload(payload, LATE_RECEIPT,
        _source_template(), explicit_week=WEEK) == [date(2026, 2, 20)]


@pytest.mark.parametrize("raw", ["2027/02/20", "金", "02/20,02/21"])
def test_payload_collector_cannot_reparse_or_infer_weekday_after_period_rejection(raw):
    from src.services import order_service

    payload = {"table_rows": [[raw, "昼", "Menu A", "2"]]}
    with pytest.raises(ValueError, match="ingest_date_.*selected_period"):
        order_service._collect_sheet_dates_from_payload(payload, LATE_RECEIPT,
            _source_template(), explicit_week=WEEK)


def test_sheet_and_reparse_position_entries_reject_month_only_even_without_rows():
    from src.services import order_service

    with pytest.raises(ValueError, match="week_choice_required"):
        order_service._build_sheet_lines_from_ocr_payload(payload={}, template=_source_template(),
            received_at=LATE_RECEIPT, week_id="2026-02", facility_id="synthetic-period")
    with pytest.raises(ValueError, match="week_choice_required"):
        order_service._build_reparse_position_menu_entries(week_id="2026-02",
            lines=[], rows=[], parsed_output={}, received_at=LATE_RECEIPT, template=_source_template())


def test_reparse_position_entries_reject_outside_date_before_menu_resolution():
    from src.services import order_service

    with pytest.raises(ValueError, match="ingest_date_outside_selected_period"):
        order_service._build_reparse_position_menu_entries(week_id=WEEK, lines=[], rows=[],
            parsed_output={"table_rows": [["2027/02/20", "昼", "Menu A", "2"]]},
            received_at=LATE_RECEIPT, template=_source_template())


def test_order_sheet_and_reparse_callers_explicitly_thread_period():
    from src.services import order_service

    functions = [order_service._build_sheet_lines_from_ocr_payload,
        order_service._reparse_order_with_attempt, order_service._build_reparse_position_menu_entries,
        order_service._build_position_menu_entries_from_ocr_payload,
        order_service._build_position_menu_entries_from_structured_tables,
        order_service._merge_missing_weekly_menu_entries_from_ocr_payload]
    targets = {"parse_date_string", "parse_order_lines", "_collect_sheet_dates_from_payload", "_collect_sheet_dates_from_rows",
        "_extract_entry_date_from_sheet_cell", "_build_position_menu_entries_from_ocr_payload",
        "_build_position_menu_entries_from_structured_tables"}
    for function in functions:
        tree = ast.parse(inspect.getsource(function))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id in targets]
        assert calls, function.__name__
        for call in calls:
            supplied = [keyword.value for keyword in call.keywords if keyword.arg == "explicit_week"]
            assert len(supplied) == 1, (function.__name__, call.func.id)


def test_parser_rejects_missing_period_even_without_rows():
    with pytest.raises(ValueError, match="week_hint_required"):
        parse_order_lines([], _template(), LATE_RECEIPT, date_policy=POLICY)


def test_parser_rejects_outside_default_date_without_rows():
    with pytest.raises(ValueError, match="ingest_date_outside_selected_period"):
        parse_order_lines([], _template(), LATE_RECEIPT, default_date=date(2027, 2, 20),
            date_policy=POLICY, explicit_week=WEEK)


def test_all_date_collectors_and_position_helpers_require_period_even_empty():
    from src.services import order_service

    calls = [
        lambda: order_service._collect_sheet_dates_from_payload({}, LATE_RECEIPT, _source_template()),
        lambda: order_service._collect_sheet_dates_from_rows([], received_at=LATE_RECEIPT, fields=[]),
        lambda: order_service._collect_dates_from_sheet_rows([], received_at=LATE_RECEIPT, fields=[]),
        lambda: order_service._extract_entry_date_from_sheet_cell(None, LATE_RECEIPT),
        lambda: order_service._build_position_menu_entries_from_ocr_payload(payload={},
            template=_source_template(), received_at=LATE_RECEIPT),
        lambda: order_service._build_position_menu_entries_from_structured_tables(payload={},
            template=_source_template(), received_at=LATE_RECEIPT),
    ]
    for call in calls:
        with pytest.raises(ValueError, match="week_hint_required"):
            call()


@pytest.mark.parametrize("function_name", ["week_id_from_dates", "month_id_from_dates"])
def test_period_summaries_cannot_restore_nearest_year(function_name):
    from src.services import ingest_policy

    function = getattr(ingest_policy, function_name)
    with pytest.raises(ValueError, match="week_hint_required"):
        function([], LATE_RECEIPT, POLICY)
    with pytest.raises(ValueError, match="ingest_date_outside_selected_period"):
        function([date(2027, 2, 20)], LATE_RECEIPT, POLICY, explicit_week=WEEK)
    result = function(["02/20"], LATE_RECEIPT, POLICY, explicit_week=WEEK)
    assert result == ("2026-02" if function_name == "month_id_from_dates" else "WEK2026W08")
