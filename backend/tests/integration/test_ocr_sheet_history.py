import sys
import pathlib
import json
from copy import deepcopy
from datetime import date, datetime

import pytest

from sqlalchemy import delete

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.models.menu import MonthlyMenu, MonthlyMenuEntry  # noqa: E402
from src.models.facility import Facility, FacilityConfig  # noqa: E402
from src.models.order import Order, OrderLine  # noqa: E402
from src.models.order_ocr_cache import OrderOcrCache  # noqa: E402
from src.services import order_service, order_workflow_v2_service  # noqa: E402
from src.services import config_service  # noqa: E402
from src.services import facility_service  # noqa: E402
from src.services import ocr_evidence_service  # noqa: E402
from src.services import fax_parser  # noqa: E402
from src.services import fax_extractor  # noqa: E402
from src.services.ocr_job_service import create_job, update_job  # noqa: E402
from src.services.fax_extractor import FaxExtractedData, rows_from_markdown  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402
from tests.helpers.owned_order import authored_layout_registration, owned_registration  # noqa: E402,F401

pytestmark = pytest.mark.usefixtures("isolated_formal_schema")

DAYPART_SOURCE_ROWS = (
    ("12/15", "朝", "Menu A", "4", "", "", "", "", "", ""),
    ("12/15", "昼", "Menu B", "5", "", "", "", "", "", ""),
    ("12/16", "朝", "Menu C", "6", "", "", "", "", "", ""),
    ("12/16", "昼", "Menu D", "7", "", "", "", "", "", ""),
    ("12/17", "朝", "Menu E", "8", "", "", "", "", "", ""),
)


def _seed_daypart_source_menu():
    _seed_monthly_menu_custom_entries(month_id="2099-12", month_start=date(2099, 12, 1),
        entries=[(datetime.strptime("2099/" + row[0], "%Y/%m/%d").date(), row[1], row[2], 0)
                 for row in DAYPART_SOURCE_ROWS])


def _save_formal_sheet(*, order_id, sheet, edited_by):
    current, error = order_workflow_v2_service.get_workflow_readonly(order_id)
    assert error is None and current is not None
    return order_workflow_v2_service.save_sheet(order_id=order_id, sheet=sheet,
        edited_by=edited_by, expected_revision=current["revision"])


def _read_order(order_id):
    return order_service.get_order_by_id(order_id, allow_unresolved_menu_amount=True)


@pytest.fixture
def history_registration(owned_registration, authored_layout_registration):
    """The historical component's declared layout, not today's live facility."""
    columns = [
        {'index': 0, 'source_index': 0, 'role': 'date', 'name': 'date_mmdd', 'header': '日付'},
        {'index': 1, 'source_index': 1, 'role': 'daypart', 'name': 'daypart', 'header': '区分'},
        {'index': 2, 'source_index': 2, 'role': 'menu_name', 'name': 'menu', 'header': 'メニュー'},
    ]
    headers = {'regular': '常食', 'soft': '軟菜', 'mixer': 'ミキサー'}
    for diet in ('regular', 'soft', 'mixer'):
        for area in ('2F', '3F'):
            index = len(columns)
            columns.append({'index': index, 'source_index': index, 'role': 'quantity',
                'name': f'qty.{diet}_{area.lower()}', 'header': headers[diet] + area,
                'diet_type': diet, 'area_id': area})
    columns.append({'index': 9, 'source_index': 9, 'role': 'note', 'name': 'remarks', 'header': '備考'})
    facility = {'facility_id': 'FAC00001', 'facility_name': 'Authored history component',
        'areas': [{'area_id': area, 'name': area} for area in ('2F', '3F')],
        'fax_template_id': 'authored-history-columns',
        'fax_template': {'template_id': 'authored-history-columns', 'page': 1, 'columns': columns}}

    def register(**inputs):
        from src.services.sheet_week_service import parse_sheet_week_value
        from tests.helpers.authored_menu_recipe import declare_period_recipes
        _, start, end = parse_sheet_week_value(inputs['week'])
        with session_scope() as session:
            declare_period_recipes(session, facility_id=inputs['facility_id'], period=inputs['week'],
                qty_per_serving=1, unit_type='g')
        if inputs['facility_id'] == 'FAC00001':
            order = owned_registration(**inputs, facility_config=facility)
        else:
            assert inputs['facility_id'] == 'FAC00005', 'explicit component layout required'
            order = authored_layout_registration(**inputs)
        current, error = order_workflow_v2_service.get_workflow_readonly(order['id'])
        assert error is None
        confirmed, error = order_workflow_v2_service.confirm_context(order_id=order['id'],
            facility_id=inputs['facility_id'], week_start=start.isoformat(), week_end=end.isoformat(),
            expected_revision=current['revision'])
        assert error is None and confirmed is not None, error
        return _read_order(order['id'])
    return register


@pytest.fixture
def selected_history_order(history_registration, monkeypatch, tmp_path):
    """Real producer/terminal receipt/selection, with only external OCR doubled."""
    from src.services.canonical_facility_store import read_snapshot
    from tests.helpers.native_selection import select_authored_native_result

    def register(ocr_rows=None, blank_source=False, **inputs):
        assert type(blank_source) is bool
        if blank_source:
            assert ocr_rows is None and not inputs['lines'], 'blank source cannot replace any supplied rows or line quantities'
        order = history_registration(**inputs)
        facility = next(item for item in read_snapshot().master['facilities']
                        if item['facility_id'] == inputs['facility_id'])
        columns = sorted(facility['fax_template']['columns'], key=lambda column: column['source_index'])
        groups = {}
        for line in inputs['lines'] or []:
            groups.setdefault((line['date'], line['daypart'], line['menu_name']), []).append(line)
        rows = [] if ocr_rows is None else [list(row) for row in ocr_rows]
        for (day, daypart, menu), lines in groups.items():
            quantities = {(line['diet_type'], line['area_id']): line['quantity_original'] for line in lines}
            assert len(quantities) == len(lines), 'duplicate authored quantity identity'
            if ocr_rows is None:
                declared = {(column['diet_type'], column.get('area_id'))
                            for column in columns if column['role'] == 'quantity'}
                assert set(quantities) <= declared, 'authored line quantity has no declared physical column'
            row = [''] * (max(column['source_index'] for column in columns) + 1)
            for column in columns:
                role = column['role']
                value = {'date': date.fromisoformat(day).strftime('%m/%d'), 'daypart': daypart,
                         'menu_name': menu}.get(role, '')
                if role == 'quantity':
                    value = quantities.get((column['diet_type'], column.get('area_id')), '')
                row[column['source_index']] = '' if value is None else str(value)
            if ocr_rows is None:
                rows.append(row)
        assert rows or blank_source, 'selected output cases require explicit rows or an explicitly blank source'
        assert not (rows and blank_source), 'choose one authored recognition input'
        assert all(len(row) == max(column['source_index'] for column in columns) + 1 for row in rows)
        select_authored_native_result(monkeypatch, order_id=order['id'],
            table_rows=rows, columns=columns, unlisted_cells='', artifact_root=tmp_path / 'native-artifacts')
        if inputs['lines']:
            assert order_service.update_lines(order['id'], deepcopy(inputs['lines'])) is True
        if not blank_source:
            sheet, error = order_service.get_ocr_sheet(order['id'])
            assert error is None and sheet is not None, error
            current, error = order_workflow_v2_service.get_workflow_readonly(order['id'])
            assert error is None
            reviewed, error = order_workflow_v2_service.run_sheet_anomaly_review(
                order['id'], sheet=sheet, use_llm=False, expected_revision=current['revision'])
            assert error is None and reviewed is not None, error
            current, error = order_workflow_v2_service.get_workflow_readonly(order['id'])
            assert error is None
            reviewed, error = order_workflow_v2_service.confirm_sheet_review(
                order_id=order['id'], sheet=sheet, expected_revision=current['revision'])
            assert error is None and reviewed is not None, error
            saved, error = _save_formal_sheet(order_id=order['id'], sheet=sheet, edited_by='history-component')
            assert error is None and saved['saved_sheet']['sheet'] == sheet, error
        return _read_order(order['id'])
    return register


def _seed_monthly_menu_2026_01() -> None:
    with session_scope() as session:
        menu = session.get(MonthlyMenu, "2026-01")
        if not menu:
            session.add(
                MonthlyMenu(
                    id="2026-01",
                    month_start=date(2026, 1, 1),
                    filename="seed-2026-01.xlsx",
                )
            )
        exists = (
            session.query(MonthlyMenuEntry)
            .filter(
                MonthlyMenuEntry.monthly_menu_id == "2026-01",
                MonthlyMenuEntry.menu_date == date(2026, 1, 8),
                MonthlyMenuEntry.daypart == "昼",
                MonthlyMenuEntry.name == "Menu A",
            )
            .first()
        )
        if not exists:
            session.add(
                MonthlyMenuEntry(
                    id="seed-entry-2026-01-08-lunch-menu-a",
                    monthly_menu_id="2026-01",
                    menu_date=date(2026, 1, 8),
                    daypart="昼",
                    name="Menu A",
                    slot_index=0,
                    diet_type="regular",
                )
            )


def _seed_monthly_menu_custom_entries(
    *,
    month_id: str,
    month_start: date,
    entries: list[tuple[date, str, str, int]],
) -> None:
    with session_scope() as session:
        menu = session.get(MonthlyMenu, month_id)
        if not menu:
            session.add(
                MonthlyMenu(
                    id=month_id,
                    month_start=month_start,
                    filename=f"seed-{month_id}.xlsx",
                )
            )
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == month_id))
        for idx, (menu_date, daypart, name, slot_index) in enumerate(entries):
            session.add(
                MonthlyMenuEntry(
                    id=f"seed-entry-{month_id}-custom-{idx}",
                    monthly_menu_id=month_id,
                    menu_date=menu_date,
                    daypart=daypart,
                    name=name,
                    slot_index=slot_index,
                    diet_type="regular",
                )
            )


def _seed_order(history_registration, *, message_id: str):
    _seed_monthly_menu_2026_01()
    lines = [
        {
            "date": "2026-01-08",
            "daypart": "昼",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 2,
        },
        {
            "date": "2026-01-08",
            "daypart": "昼",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "3F",
            "bag_type": "standard",
            "quantity_original": 1,
        },
        {
            "date": "2026-01-08",
            "daypart": "昼",
            "menu_name": "Menu A",
            "diet_type": "soft",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 3,
        },
    ]
    return history_registration(message_id=message_id, facility_id="FAC00001",
        received_at=datetime(2026, 1, 8, 9), week="2026-01@2026-01-08~2026-01-14",
        effective_date="2026-01-08", lines=lines)


def _seed_custom_order(history_registration, *, message_id: str, received_at: datetime,
                       week: str, effective_date: str, lines: list[dict[str, object]],
                       ocr_rows=None, blank_source=False):
    extra = {} if ocr_rows is None else {'ocr_rows': ocr_rows}
    if blank_source:
        extra['blank_source'] = True
    return history_registration(message_id=message_id, facility_id="FAC00001",
        received_at=received_at, week=week, effective_date=effective_date, lines=lines, **extra)


def _register_scoped_order(history_registration, payload, *, lines, week, ocr_rows=None, blank_source=False):
    """Authored component input, not evidence of recognition or live intake."""
    from src.services.sheet_week_service import parse_sheet_week_value
    _, start, end = parse_sheet_week_value(week)
    assert start is not None and end is not None
    extra = {} if ocr_rows is None else {'ocr_rows': ocr_rows}
    if blank_source:
        extra['blank_source'] = True
    return history_registration(message_id=payload.message_id,
        facility_id=payload.facility_hint, received_at=payload.received_at,
        week=week, effective_date=start.isoformat(), lines=lines, **extra)


def _seed_order_without_facility(*, message_id: str):
    # Deliberately unresolved legacy state, not a successful intake registration.
    ident = "ORD-" + message_id
    with session_scope() as session:
        order = Order(id=ident, message_id=message_id, document_uri="component://unresolved-original",
                      received_at=datetime(2026, 2, 13, 9), facility_code=None,
                      week_code="2026-02@2026-02-15~2026-02-21")
        order.lines.append(OrderLine(id=ident + "-line", date=date(2026, 2, 15), daypart="朝",
            menu_name="Menu B", diet_type="regular", area_id="2F", bag_type="standard", quantity_original=4))
        session.add(order)
    return _read_order(ident)


def _assert_unselected_sheet_cannot_be_reconstructed(order_id):
    from src.db import get_engine
    from src.services.canonical_facility_store import read_snapshot
    from tests.unit.test_disabled_facility_columns import _snapshot

    before = _snapshot(get_engine())
    original = pathlib.Path(_read_order(order_id)["document"])
    original_bytes, revision = original.read_bytes(), read_snapshot().revision
    sheet, error = order_service.get_ocr_sheet(order_id)
    assert (sheet, error) == (None, "selected_ocr_required")
    for _ in range(2):
        recovered, recovery_error = order_service.build_recoverable_ocr_sheet_payload(
            order_id, "menu_entries_missing", use_saved_draft=True)
        assert (recovered, recovery_error) == (None, "selected_ocr_required")
    context = order_service.get_current_sheet_context(order_id)
    assert context["draft_payload"] is None
    assert context["fields"] == context["rows"] == []
    assert context["has_persisted_draft"] is False
    assert "selected_ocr_required" in context["blockers"]
    assert _snapshot(get_engine()) == before
    assert original.read_bytes() == original_bytes
    assert read_snapshot().revision == revision


def _assert_raw_diagnostics_do_not_override_selected_source(order_id, payload, issue_key):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    captured = order_service._capture_synchronous_cache_write_input(order_id)
    assert captured is not None
    before = _snapshot(get_engine())
    issues = order_service._extract_payload_cell_issues(payload, captured['configured_input']['facility']['fax_template'])
    assert len(issues) == 1
    issue, original = issues[0], payload[issue_key][0]
    assert issue['field'] == 'qty.regular_2f'
    assert issue['column_index'] == 3
    assert issue['source_row_index'] == 0
    for key, value in original.items():
        if key != 'row_index':
            assert issue[key] == value
    order_service._save_order_ocr_cache(order_id, payload)
    sheet, error = order_service.get_ocr_sheet(order_id)
    assert error is None and sheet is not None, error
    assert sheet['base_evidence_run_id'] == captured['evidence_run_id']
    fields = sheet['fields']
    row = next(row for row in sheet['rows'] if row[fields.index('menu')] == 'Menu A')
    assert [row[fields.index(field)] for field in ('qty.regular_2f', 'qty.regular_3f', 'qty.soft_2f')] == ['2', '1', '3']
    assert not any(item.get('issue_code') == issue['issue_code'] and item.get('source') == issue['source']
                   for item in sheet.get('cell_issues', []))
    assert _snapshot(get_engine()) == before


def test_selected_blank_cells_do_not_borrow_shifted_order_line_quantities(selected_history_order):
    order_service.clear_all()
    received_at = datetime(2099, 4, 15, 9, 0, 0)
    _seed_monthly_menu_custom_entries(
        month_id="2099-04",
        month_start=date(2099, 4, 1),
        entries=[
            (date(2099, 4, 15), "昼", "Menu A", 0),
            (date(2099, 4, 15), "昼", "Menu B", 1),
        ],
    )
    order = _seed_custom_order(selected_history_order,
        message_id="msg-sheet-source-row-shift-001",
        received_at=received_at,
        week="2099-04@2099-04-15~2099-04-21",
        effective_date="2099-04-15",
        ocr_rows=[
            ["04/15", "昼", "Menu A", "", "", "", "", "", "", ""],
            ["04/15", "昼", "Menu B", "", "", "", "", "", "", ""],
        ],
        lines=[
            {
                "date": "2099-04-15",
                "daypart": "昼",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 11,
                "source_row_index": 1,
            },
            {
                "date": "2099-04-15",
                "daypart": "昼",
                "menu_name": "Menu B",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 22,
                "source_row_index": 0,
            },
        ],
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert sheet is not None
    assert sheet["base_evidence_run_id"]
    fields = sheet["fields"]
    qty_idx = fields.index("qty.regular_2f")
    menu_idx = fields.index("menu")
    rows_by_menu = {row[menu_idx]: row for row in sheet["rows"]}
    assert rows_by_menu["Menu A"][qty_idx] == ""
    assert rows_by_menu["Menu B"][qty_idx] == ""


def test_selected_blank_source_rejects_unowned_position_index_projection(selected_history_order, monkeypatch):
    order_service.clear_all()
    received_at = datetime(2099, 4, 15, 9, 0, 0)
    _seed_monthly_menu_custom_entries(
        month_id="2099-04",
        month_start=date(2099, 4, 1),
        entries=[
            (date(2099, 4, 15), "昼", "Menu A", 0),
            (date(2099, 4, 15), "昼", "Menu B", 1),
        ],
    )
    order = _seed_custom_order(selected_history_order,
        message_id="msg-sheet-no-position-fallback-001",
        received_at=received_at,
        week="2099-04@2099-04-15~2099-04-21",
        effective_date="2099-04-15",
        lines=[],
        blank_source=True,
    )
    selected_payload = order_service.get_cached_ocr_payload(order["id"])

    unowned_payload = {
            "column_mapping_resolution": {
                "resolved_value": "4:qty.regular_2f",
                "decision_source": "position_fallback",
            },
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "rows": [
                        ["日付", "区分", "", "献立", "常食", "備考欄"],
                        ["2099-04-15", "昼", "", "", "11", ""],
                        ["2099-04-15", "昼", "", "", "22", ""],
                    ],
                }
            ],
            "table_raw": (
                "|日付|区分||献立|常食|備考欄|\n"
                "|---|---|---|---|---|---|\n"
                "|2099-04-15|昼|||11||\n"
                "|2099-04-15|昼|||22||"
            ),
        }
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot
    before = _snapshot(get_engine())
    with pytest.raises(ValueError, match="^configured_execution_input_changed$"):
        ocr_evidence_service.persist_evidence_run(order_id=order["id"], payload=unowned_payload,
            schema_version="v1_legacy", producer_version="test", source="test-evidence")
    assert _snapshot(get_engine()) == before

    def _fail_position_mapping(*_args, **_kwargs):
        raise AssertionError("position fallback must not run on current-sheet quantity path")

    monkeypatch.setattr(order_service, "_apply_menu_position_mapping_safe", _fail_position_mapping)

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert sheet is not None
    assert sheet["base_evidence_run_id"]
    assert order_service.get_cached_ocr_payload(order["id"]) == selected_payload
    assert "sheet_quantity_column_unmapped" not in (sheet.get("warnings") or [])
    fields = sheet["fields"]
    qty_idx = fields.index("qty.regular_2f")
    menu_idx = fields.index("menu")
    rows_by_menu = {row[menu_idx]: row for row in sheet["rows"]}
    assert rows_by_menu["Menu A"][qty_idx] == ""
    assert rows_by_menu["Menu B"][qty_idx] == ""


def test_get_ocr_sheet_keeps_facility_template_schema_when_payload_template_conflicts(owned_registration, monkeypatch, tmp_path):
    order_service.clear_all()
    _seed_monthly_menu_custom_entries(
        month_id="2099-04",
        month_start=date(2099, 4, 1),
        entries=[
            (date(2099, 4, 5), "朝", "豚肉の卵とじ", 0),
        ],
    )
    facility = {"id": "authored-projection-facility"}

    order = owned_registration(
        message_id="msg-facility-schema-payload-template-conflict-001",
        received_at=datetime(2099, 4, 5, 9, 0, 0),
        facility_id=facility["id"],
        week="2099-04@2099-04-05~2099-04-11", effective_date="2099-04-05",
        facility_config={"facility_id": facility["id"], "facility_name": "Projection Facility",
            "areas": [], "fax_template_id": "authored-projection-layout",
            "fax_template": {"template_id": "authored-projection-layout", "page": 1, "columns_authoritative": True,
                "columns": [
                    {"index": 0, "source_index": 0, "role": "date", "name": "date_mmdd", "header": "日付", "format": "MM/DD"},
                    {"index": 1, "source_index": 1, "role": "daypart", "name": "daypart", "header": "区分"},
                    {"index": 2, "source_index": 2, "role": "aux", "name": "aux.col_2", "header": "補助"},
                    {"index": 3, "source_index": 3, "role": "menu_name", "name": "menu", "header": "メニュー"},
                    {"index": 4, "source_index": 4, "role": "quantity", "name": "qty.regular_x", "header": "常食特別", "diet_type": "regular", "area_id": "X"},
                    {"index": 5, "source_index": 5, "role": "note", "name": "remarks", "header": "備考"},
                ]}},
        lines=[
            {
                "date": "2099-04-05",
                "daypart": "朝",
                "menu_name": "豚肉の卵とじ",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 5,
            }
        ],
    )
    from src.services.canonical_facility_store import read_snapshot
    from tests.helpers.native_selection import select_authored_native_result
    current, error = order_workflow_v2_service.get_workflow_readonly(order["id"])
    assert error is None
    _, error = order_workflow_v2_service.confirm_context(order_id=order["id"], facility_id=facility["id"],
        week_start="2099-04-05", week_end="2099-04-11", expected_revision=current["revision"])
    assert error is None
    authored = [item for item in read_snapshot().master["facilities"] if item["facility_id"] == facility["id"]]
    assert len(authored) == 1
    select_authored_native_result(monkeypatch, order_id=order["id"],
        table_rows=[["04/05", "朝", "ま", "豚肉の卵とじ", "5", ""]],
        columns=authored[0]["fax_template"]["columns"], unlisted_cells="",
        artifact_root=tmp_path / "native-artifacts")
    selected_payload = order_service.get_cached_ocr_payload(order["id"])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "template_id": "fax_layout_floor_2f3f_v1",
            "tables": [
                {
                    "table_id": "conflict-table-1",
                    "rows": [
                        ["日付", "区分", "補助", "メニュー", "常食特別", "備考"],
                        ["04/05", "朝", "ま", "豚肉の卵とじ", "5", ""],
                    ],
                }
            ],
            "date_strings": ["04/05"],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"], prefer_order_lines=False)

    assert error is None
    assert sheet is not None
    assert sheet["fields"] == [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "qty.regular_x",
        "remarks",
    ]
    qty_idx = sheet["fields"].index("qty.regular_x")
    menu_idx = sheet["fields"].index("menu")
    assert sheet["header"][qty_idx] == "常食特別"
    assert sheet["rows"][0][menu_idx] == "豚肉の卵とじ"
    assert sheet["rows"][0][qty_idx] == "5"
    assert order_service.get_cached_ocr_payload(order["id"]) == selected_payload


def test_build_position_menu_entries_does_not_borrow_previous_month_container():
    order_service.clear_all()
    _seed_monthly_menu_custom_entries(
        month_id="2098-03",
        month_start=date(2098, 3, 1),
        entries=[
            (date(2098, 4, 5), "朝", "Covered Breakfast", 0),
            (date(2098, 4, 5), "昼", "Covered Lunch", 1),
        ],
    )

    entries = order_service._build_position_menu_entries_safe("2098-04@2098-04-05~2098-04-11")

    assert entries == []


def _assert_legacy_facility_refusal(
    isolated_formal_schema, tmp_path, monkeypatch, *, target_name, target_header, target_area, row_id,
):
    from tests.contract.auth_support import registered_hospital_operator
    from tests.contract.test_orders_ocr_sheet_history_api import HistoryCase, LEGACY, _line
    from tests.contract import test_portal_google_auth

    monkeypatch.setattr(test_portal_google_auth.db, "engine", isolated_formal_schema)

    with registered_hospital_operator(monkeypatch) as actor:
        f = HistoryCase(isolated_formal_schema, tmp_path, monkeypatch, actor,
            lines=[_line(quantity=2), _line(quantity=1, area="3F"),
                   {**_line(quantity=3), "diet_type": "soft"}])
        # The original line fixture has three explicit quantity identities.
        authored = json.loads(f.source.read_text())
        envelope = authored["facilities"][0]
        for config in (envelope, envelope["versions"][0]["facility"]):
            columns = config["fax_template"]["columns"]
            columns[4:4] = [
                {"index": 4, "source_index": 4, "name": "qty.regular_3f", "header": "常食3F",
                 "role": "quantity", "diet_type": "regular", "area_id": "3F"},
                {"index": 5, "source_index": 5, "name": "qty.soft_2f", "header": "軟食2F",
                 "role": "quantity", "diet_type": "soft", "area_id": "2F"},
            ]
            columns[-1].update(index=6, source_index=6)
        f.source.write_text(json.dumps(authored, ensure_ascii=False), encoding="utf-8")
        f.produce()
        target_id = "FAC-legacy-switch-target"
        with session_scope() as session:
            session.add(Facility(id=target_id, name=target_name))
            session.flush()
            session.add(FacilityConfig(facility_id=target_id, config_json={
                "fax_template_override": {"columns_authoritative": True, "columns": [
                    {"index": 0, "role": "date", "header": "日付", "format": "MM/DD"},
                    {"index": 1, "role": "daypart", "header": "区分"},
                    {"index": 2, "role": "menu_name", "header": "メニュー"},
                    {"index": 3, "role": "quantity", "header": target_header,
                     "diet_type": "regular", "area_id": target_area},
                    {"index": 4, "role": "note", "header": "備考"},
                ]}}))
        expected = {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "旧常食2F", "備考"],
            "rows": [["01/08", "昼", "Menu A", "8", "seeded"]],
            "row_ids": [row_id],
            "source": "draft_ready",
        }
        seeded = f.save(expected)
        assert seeded is not None
        with f.readonly():
            unauthenticated = f.client.post(f"/orders/{f.id}/facility",
                json={"facility_id": target_id})
            assert unauthenticated.status_code == 401
            refused = f.client.post(f"/orders/{f.id}/facility",
                json={"facility_id": target_id}, headers=actor.headers)
            assert refused.status_code == 410
            assert refused.json() == {"detail": {**LEGACY, "endpoint": "facility"}}
            current = order_service.get_current_sheet_context(f.id)
            sheet, error = order_service.get_ocr_sheet(f.id)
            assert current is not None
            assert current["facility_id"] == f.facility
            assert error is None
            assert sheet is not None
            assert current["header"][3] == "旧常食2F"
            assert current["fields"][3] == "qty.regular_2f"
            assert sheet["header"][3] == "旧常食2F"
            assert sheet["fields"][3] == "qty.regular_2f"
            assert current["rows"][0][3] == "8"
            assert sheet["rows"][0][3] == "8"
            for key in ("fields", "header", "rows", "row_ids"):
                assert current[key] == sheet[key] == expected[key]
            assert order_service.get_current_sheet_context(f.id) == current
            assert order_service.get_ocr_sheet(f.id) == (sheet, None)
        assert f.get("workflow-v2/sheet")["sheet"] == expected
        state = f.get("workflow-v2")
        assert state["facility_id"] == f.facility
        assert "quantity_template_field_missing" in state["blockers"]
        assert state["menu_amount_resolution"]["blocker"] == "quantity_template_field_missing"
        assert "quantity_template_field_missing" in current["blockers"]
        for key in ("apply_blockers", "confirm_blockers", "blockers"):
            assert "quantity_template_field_missing" in sheet[key]
        assert sheet["can_apply"] is False and sheet["can_confirm"] is False
        f.client.close()


def test_legacy_set_facility_refusal_keeps_saved_sheet_until_operator_resolves_context_change(
    isolated_formal_schema, tmp_path, monkeypatch,
):
    _assert_legacy_facility_refusal(isolated_formal_schema, tmp_path, monkeypatch,
        target_name="Facility Switch Target", target_header="施設常食", target_area="X",
        row_id="row-facility-refresh-1")


def test_legacy_set_facility_refusal_does_not_mutate_clean_saved_sheet_header_when_fields_match(
    isolated_formal_schema, tmp_path, monkeypatch,
):
    _assert_legacy_facility_refusal(isolated_formal_schema, tmp_path, monkeypatch,
        target_name="Facility Same Field Header", target_header="別名常食2F", target_area="2F",
        row_id="row-facility-header-stable-1")


def test_get_ocr_sheet_does_not_reintroduce_stale_evidence_blockers_for_clean_saved_draft(selected_history_order, monkeypatch):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-sheet-clean-draft-override-001")
    selected_before, selected_error = order_service.get_ocr_sheet(order["id"])
    assert selected_error is None and selected_before is not None
    stale_payload = {
            "input_reference": "gs://bucket/orders/order.pdf",
            "pages": [
                {
                    "page_index": 1,
                    "ocr_overlay_uri": "gs://bucket/orders/page1-ocr.png",
                    "layout_overlay_uri": "gs://bucket/orders/page1-layout.png",
                }
            ],
            "table_raw": "\n".join(
                [
                    "|日付|区分|メニュー|常食|",
                    "|---|---|---|---|",
                    "|01/08|昼|Menu A|5|",
                ]
            ),
            "template_resolution": {
                "resolved_template_id": None,
                "candidate_template_ids": ["fax_layout_regular_soft_mixer_forbidden_v1"],
                "confidence": 0.41,
                "blocked": True,
                "blocked_reasons": ["template_resolution_missing"],
            },
            "quantity_subgrid_passes": [],
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
        }
    with pytest.raises(ValueError, match="^configured_execution_input_changed$"):
        order_service.persist_ocr_evidence_run(order["id"], stale_payload)
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    from hashlib import sha256
    with session_scope() as session:
        session.add(OrderOcrEvidenceRun(id="stale-history-diagnostic", order_id=order["id"],
            schema_version="v1_legacy", status="diagnostic_only", payload_json=deepcopy(stale_payload),
            artifact_digest=sha256(json.dumps(stale_payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()))
    persisted = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["01/08", "昼", "Menu A", "8", ""]],
            "row_ids": ["draft-row-1"],
            "ui_mode": "sheet",
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
     synchronous_write_input=order_service._capture_synchronous_cache_write_input(order['id']))
    assert persisted is not None
    monkeypatch.setattr(order_service, "_maybe_refresh_semantic_sheet_draft", lambda _order_id, draft: draft)

    sheet, error = order_service.get_ocr_sheet(order["id"])
    current_context = order_service.get_current_sheet_context(order["id"])

    assert error is None
    assert isinstance(sheet, dict)
    assert isinstance(current_context, dict)
    assert sheet["source"] == current_context["source"]
    for key in ("rows", "row_ids", "base_evidence_run_id", "warnings", "apply_blockers",
                "confirm_blockers", "can_apply", "can_confirm"):
        assert sheet[key] == selected_before[key]


def test_selected_draft_ignores_unselected_stale_rows_without_pruning_stored_diagnostics(selected_history_order, monkeypatch):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-sheet-refresh-prune-stale-rows-001")
    selected_before = order_service.get_latest_sheet_draft(order["id"])
    assert selected_before is not None
    persisted = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [
                ["03/22", "昼", "Menu A", "17", ""],
                ["01/01", "\"", "Ghost Menu", "23", ""],
            ],
            "row_ids": ["draft-2", "draft-1"],
            "ui_mode": "sheet",
        },
        draft_state="draft_ready",
        blockers=["sheet_canonical_mismatch"],
        warnings=["sheet_ocr_review_required"],
     synchronous_write_input=order_service._capture_synchronous_cache_write_input(order['id']))
    assert persisted is not None

    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft",
        lambda _order_id, use_saved_draft=False: {
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [
                ["03/22", "昼", "Menu A", "", ""],
                ["03/23", "昼", "Menu B", "", ""],
            ],
            "row_ids": ["fresh-1", "fresh-2"],
            "warnings": ["sheet_payload_mapping_low_confidence"],
            "ui_mode": "sheet",
        },
    )

    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    before = _snapshot(get_engine())
    latest = order_service.get_latest_sheet_draft(order["id"])
    assert latest == selected_before
    assert latest["id"] != persisted["id"]
    assert _snapshot(get_engine()) == before


def test_selected_formal_manual_rows_are_not_pruned_or_reconstructed_on_draft_read(selected_history_order, monkeypatch):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    _seed_monthly_menu_custom_entries(month_id="2026-03", month_start=date(2026, 3, 1),
        entries=[(date(2026, 3, 22), "昼", "Menu A", 0),
                 (date(2026, 3, 24), "昼", "Manual Extra", 0)])
    order = _seed_custom_order(selected_history_order,
        message_id="msg-sheet-refresh-preserve-manual-rows-001",
        received_at=datetime(2026, 3, 22, 9), week="2026-03@2026-03-22~2026-03-28",
        effective_date="2026-03-22", lines=[],
        ocr_rows=[["03/22", "昼", "Menu A", "2", "", "", "", "", "", ""],
                  ["03/24", "昼", "Manual Extra", "", "", "", "", "", "", ""]])
    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and sheet is not None
    original_source = deepcopy(order_service.get_latest_ocr_evidence_run(order["id"]))
    fields = sheet["fields"]
    for row in sheet["rows"]:
        menu = row[fields.index("menu")]
        if menu == "Menu A":
            row[fields.index("qty.regular_2f")] = "17"
        elif menu == "Manual Extra":
            row[fields.index("qty.regular_2f")], row[fields.index("remarks")] = "4", "manual"
    saved, error = _save_formal_sheet(
        order_id=order["id"], sheet=sheet, edited_by="authored-component-operator")
    assert error is None and saved is not None
    selected = order_service.get_latest_sheet_draft(order["id"])
    assert selected is not None
    def forbidden(*args, **kwargs):
        pytest.fail("An explicit selected operator sheet must not be rebuilt during reads")
    monkeypatch.setattr(order_service, "_build_best_available_semantic_draft", forbidden)
    before = _snapshot(get_engine())
    for _ in range(2):
        latest = order_service.get_latest_sheet_draft(order["id"])
        assert latest == selected
        current, error = order_service.get_ocr_sheet(order["id"])
        assert error is None
        for key in ("fields", "header", "rows", "row_ids"):
            assert current[key] == sheet[key]
    assert order_service.get_latest_ocr_evidence_run(order["id"]) == original_source
    assert _snapshot(get_engine()) == before


def test_selected_draft_is_not_replaced_by_unselected_blank_rows_or_synthetic_fresh_quantities(selected_history_order, monkeypatch):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-sheet-refresh-blank-current-qty-001")
    selected_before = order_service.get_latest_sheet_draft(order["id"])
    assert selected_before is not None
    persisted = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [
                ["03/22", "昼", "Menu A", "", ""],
                ["03/23", "昼", "Menu B", "", ""],
            ],
            "row_ids": ["draft-1", "draft-2"],
            "ui_mode": "sheet",
        },
        draft_state="auto_apply_blocked",
        blockers=["sheet_quantity_column_unmapped"],
        warnings=["sheet_ocr_review_required"],
     synchronous_write_input=order_service._capture_synchronous_cache_write_input(order['id']))
    assert persisted is not None

    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft",
        lambda _order_id, use_saved_draft=False: {
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [
                ["03/22", "昼", "Menu A", "23", ""],
                ["03/23", "昼", "Menu B", "18", ""],
            ],
            "row_ids": ["fresh-1", "fresh-2"],
            "warnings": [],
            "ui_mode": "sheet",
        },
    )

    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    before = _snapshot(get_engine())
    latest = order_service.get_latest_sheet_draft(order["id"])
    assert latest == selected_before
    assert latest["id"] != persisted["id"]
    assert _snapshot(get_engine()) == before


@pytest.mark.parametrize("saved_quantity", ["", "0", "17"])
def test_merge_current_draft_quantity_values_preserves_exact_saved_values(saved_quantity):
    merged = order_service._merge_current_draft_quantity_values_into_semantic_sheet(
        {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "rows": [["03/22", "昼", "Menu A", saved_quantity, ""]],
            "row_ids": ["explicit-same-row"],
        },
        {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "qty.no_meat_x", "remarks"],
            "rows": [["03/22", "昼", "Menu A", "23", "4", ""]],
            "row_ids": ["explicit-same-row"],
        },
    )

    assert merged is not None
    assert merged["rows"] == [["03/22", "昼", "Menu A", saved_quantity, "", ""]]


def test_merge_current_draft_does_not_pair_different_ids_by_matching_labels():
    current = {"fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
               "rows": [["03/22", "昼", "Menu A", "17"]], "row_ids": ["saved-row"]}
    fresh = {"fields": list(current["fields"]),
             "rows": [["03/22", "昼", "Menu A", "23"]], "row_ids": ["different-row"]}
    before = deepcopy((current, fresh))
    with pytest.raises(ValueError, match="^saved_draft_row_identity_unresolved$"):
        order_service._merge_current_draft_quantity_values_into_semantic_sheet(current, fresh)
    assert (current, fresh) == before


def test_selected_draft_blockers_are_not_rewritten_from_an_unselected_neighbor(selected_history_order, monkeypatch):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-sheet-refresh-clear-stale-blockers-001")
    selected_before = order_service.get_latest_sheet_draft(order["id"])
    assert selected_before is not None
    persisted = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["03/22", "昼", "Menu A", "17", ""]],
            "row_ids": ["draft-1"],
            "ui_mode": "sheet",
            "warnings": [],
        },
        draft_state="auto_apply_blocked",
        blockers=["sheet_canonical_mismatch"],
        warnings=[],
     synchronous_write_input=order_service._capture_synchronous_cache_write_input(order['id']))
    assert persisted is not None

    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft",
        lambda _order_id, use_saved_draft=False: {
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["03/22", "昼", "Menu A", "17", ""]],
            "row_ids": ["fresh-1"],
            "warnings": [],
            "ui_mode": "sheet",
        },
    )

    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    before = _snapshot(get_engine())
    latest = order_service.get_latest_sheet_draft(order["id"])
    assert latest == selected_before
    assert latest["id"] != persisted["id"]
    assert _snapshot(get_engine()) == before


def test_should_prefer_source_row_candidate_when_duplicate_rows_are_clean():
    quantity_index = {("regular", "2F"): 3}
    duplicate_identity = order_service._sheet_row_identity("2026-01-08", "昼", "Menu A")
    base_rows = [
        {"identity": duplicate_identity, "values": ["01/08", "昼", "Menu A", ""]},
        {"identity": duplicate_identity, "values": ["01/08", "昼", "Menu A", ""]},
    ]
    order_lines = [
        {
            "date": "2026-01-08",
            "daypart": "昼",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "quantity_original": 11,
            "source_row_index": 0,
        },
        {
            "date": "2026-01-08",
            "daypart": "昼",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "quantity_original": 22,
            "source_row_index": 1,
        },
    ]

    summary = order_service._summarize_order_line_source_row_mapping(
        base_rows=base_rows,
        quantity_index=quantity_index,
        order_lines=order_lines,
    )

    assert summary == {
        "eligible_line_count": 2,
        "matched_source_row_count": 2,
        "mismatched_source_row_count": 0,
        "missing_source_row_count": 0,
        "invalid_identity_line_count": 0,
    }
    assert order_service._should_prefer_source_row_candidate(
        identity_count=1,
        source_row_count=2,
        source_row_summary=summary,
    )


def test_raw_roi_issue_metadata_does_not_override_selected_source(selected_history_order):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-sheet-roi-review-001")
    diagnostic_payload = {
            "rows": [
                {
                    "row_index": 0,
                    "date_mmdd": "01/08",
                    "daypart": "昼",
                    "menu": "Menu A",
                    "qty": {
                        "regular_2f": 2,
                        "regular_3f": 1,
                        "soft_2f": 3,
                    },
                }
            ],
            "roi_cell_issues": [
                {
                    "row_index": 0,
                    "field": "qty.regular_2f",
                    "issue_code": "sanity_fail",
                    "severity": "warning",
                    "confidence": 0.88,
                    "value": 66,
                    "max_allowed": 50,
                }
            ],
        }
    _assert_raw_diagnostics_do_not_override_selected_source(order["id"], diagnostic_payload, 'roi_cell_issues')


def test_export_ocr_sheet_label_writes_current_sheet_json(selected_history_order, tmp_path):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-export-sheet-label-001")

    current_sheet, current_error = order_service.get_ocr_sheet(order["id"])

    assert current_error is None
    assert current_sheet is not None

    export_path = tmp_path / "labels" / f"{order['id']}.expected_sheet.json"
    exported, export_error = order_service.export_ocr_sheet_label(
        order["id"],
        output_path=export_path,
    )

    assert export_error is None
    assert exported is not None
    saved_json = json.loads(export_path.read_text(encoding="utf-8"))
    assert saved_json["rows"] == current_sheet["rows"]
    assert saved_json["row_ids"] == current_sheet["row_ids"]
    assert saved_json["fields"] == current_sheet["fields"]
    assert saved_json["header"] == current_sheet["header"]
    assert export_path.exists()
    saved = json.loads(export_path.read_text(encoding="utf-8"))
    assert saved == current_sheet
    assert exported["order_id"] == order["id"]
    assert exported["output_path"] == str(export_path)


def test_save_ocr_sheet_exact_persists_revision_without_reparsing_lines(selected_history_order):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-sheet-exact-save-001")

    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "qty.regular_3f",
        "qty.soft_2f",
        "qty.soft_3f",
        "qty.mixer_2f",
        "qty.mixer_3f",
        "remarks",
    ]
    header = ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"]
    rows = [["01/08", "昼", "Menu A", "9", "4", "", "", "", "", "manual-save"]]
    row_ids = ["row-exact-save-1"]

    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=header,
        rows=rows,
        ui_mode="sheet",
        fields=fields,
        row_ids=row_ids,
    )

    assert error is None
    assert saved is not None
    revision = saved.get("revision")
    assert isinstance(revision, dict)
    assert revision.get("ui_mode") == "sheet"
    assert revision.get("sheet_save_only") is True
    assert revision.get("sheet_save_mode") == "exact"
    assert revision.get("row_ids") == row_ids
    assert revision.get("rows") == rows

    current_order = _read_order(order["id"])
    assert current_order is not None
    quantities = sorted(
        int(line.get("quantity_original"))
        for line in (current_order.get("lines") or [])
        if line.get("quantity_original") is not None
    )
    assert quantities == [1, 2, 3]

    output, output_error = order_service.get_ocr_output(order["id"])
    assert output_error is None
    assert output is not None
    assert output.get("edited_table") is None
    cached = order_service._load_order_ocr_cache(order["id"])
    assert cached["_edited_ocr"]["latest"]["rows"] == rows


def test_export_ocr_sheet_label_prefers_exact_saved_sheet_revision(selected_history_order, tmp_path):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-export-sheet-label-exact-001")

    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "qty.regular_3f",
        "qty.soft_2f",
        "qty.soft_3f",
        "qty.mixer_2f",
        "qty.mixer_3f",
        "remarks",
    ]
    header = ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"]
    rows = [["01/08", "昼", "Menu A", "12", "", "", "", "", "", "gold-label"]]
    row_ids = ["row-exact-export-1"]

    saved, error = _save_formal_sheet(order_id=order["id"],
        sheet={"fields": fields, "header": header, "rows": rows, "row_ids": row_ids},
        edited_by="authored-component-operator")
    assert error is None and saved is not None

    export_path = tmp_path / "labels" / f"{order['id']}.expected_sheet.json"
    exported, export_error = order_service.export_ocr_sheet_label(
        order["id"],
        output_path=export_path,
    )

    assert export_error is None
    assert exported is not None
    actual = json.loads(export_path.read_text(encoding="utf-8"))
    assert {key: actual[key] for key in ("fields", "header", "rows", "row_ids")} == {
        "fields": fields, "header": header, "rows": rows, "row_ids": row_ids}


def test_selected_output_does_not_fall_back_to_unowned_message_job(selected_history_order):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-ocr-first-pass-fallback-001")
    create_job(f"OCR-{order['id']}", input_reference="gs://order/reparse.json", status="failed")
    create_job(f"OCR-{order['message_id']}", input_reference="gs://message/first-pass.json", status="done")
    unowned = {"table_raw": "|01/08|昼|Menu A|4||",
        "pages": [{"page_index": 1, "markdown_uri": "gs://bucket/markdown.md",
                  "ocr_overlay_uri": "gs://bucket/ocr.png", "layout_overlay_uri": "gs://bucket/layout.png"}]}
    captured = order_service._capture_synchronous_cache_write_input(order["id"])
    assert captured is not None
    original = deepcopy(order_service._load_order_ocr_cache(order["id"]))
    before = _snapshot(get_engine())
    assert order_service._save_order_ocr_cache(order["id"], unowned) is None
    output, error = order_service.get_ocr_output(order["id"])
    assert error is None and output is not None
    assert output["_ocr_execution"]["evidence_run_id"] == captured["evidence_run_id"]
    assert output.get("table_raw") != unowned["table_raw"]
    assert order_service._load_order_ocr_cache(order["id"]) == original
    assert _snapshot(get_engine()) == before


def test_get_ocr_output_keeps_owned_raw_artifacts_separate_from_edited_table(selected_history_order):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-ocr-raw-vs-edited-separation-001")
    original = deepcopy(order_service._load_order_ocr_cache(order["id"]))
    raw_table = original.get("table_raw")
    raw_records = order_service._extract_hakodate_ocr_evidence_records_from_payload(original, order_id=order["id"])
    assert raw_records
    assert order_service._save_order_ocr_cache(
        order["id"],
        {
            "status": "success",
            "table_raw": raw_table,
            "pages": [
                {
                    "page_index": 1,
                    "markdown_text": raw_table,
                }
            ],
        },
    ) is None

    current, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and current is not None
    fields, header = current["fields"], current["header"]
    index = next(index for index, row in enumerate(current["rows"]) if row[fields.index("menu")] == "Menu A")
    edited = list(current["rows"][index])
    for field, value in (("qty.regular_2f", "12"), ("qty.regular_3f", "11"), ("qty.soft_2f", "13")):
        edited[fields.index(field)] = value
    rows, row_ids = [edited], [current["row_ids"][index]]

    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=header,
        rows=rows,
        ui_mode="sheet",
        fields=fields,
        row_ids=row_ids,
    )

    assert error is None
    assert saved is not None

    output, output_error = order_service.get_ocr_output(order["id"])

    assert output_error is None
    assert output is not None
    assert output.get("table_raw") == raw_table
    assert order_service._extract_hakodate_ocr_evidence_records_from_payload(output, order_id=order["id"]) == raw_records
    assert output.get("ocr_source") != "edited"
    assert output.get("edited_table") is None
    with session_scope() as session:
        cache = session.get(OrderOcrCache, order["id"])
        assert cache is not None
        assert isinstance(cache.payload, dict)
        assert cache.payload.get("table_raw") == raw_table
        assert cache.payload["_edited_ocr"]["latest"]["rows"] == rows
    operator_sheet = {**current, "rows": rows, "row_ids": row_ids}
    published, error = _save_formal_sheet(order_id=order["id"],
        sheet=operator_sheet, edited_by="authored-component-operator")
    assert error is None and published is not None
    visible, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and visible["rows"] == rows


def test_unselected_polluted_cache_cannot_become_raw_output(history_registration):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    order = _seed_order(history_registration, message_id="msg-ocr-no-legacy-polluted-table-raw-001")
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "qty.regular_3f",
        "qty.soft_2f",
    ]
    header = ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F"]
    polluted_rows = [["01/08", "昼", "Menu A", "70", "137", "137"]]
    polluted_markdown = order_service._build_markdown_table_string(header, polluted_rows)  # noqa: SLF001
    polluted_latest = {
        "revision_id": "OCRREV-polluted-no-raw",
        "edited_at": "2026-02-15T00:00:00",
        "ui_mode": "sheet",
        "fields": fields,
        "header": header,
        "row_ids": ["row-polluted-1"],
        "rows": polluted_rows,
        "row_count": 1,
        "before_digest": "digest-before",
        "after_digest": "digest-after",
        "changed": True,
        "markdown": polluted_markdown,
    }

    with session_scope() as session:
        cache = session.get(OrderOcrCache, order["id"])
        if cache is None:
            cache = OrderOcrCache(order_id=order["id"], payload={})
            session.add(cache)
        cache.payload = {
            "status": "success",
            "table_raw": polluted_markdown,
            "ocr_source": "edited",
            "edited_table": {
                "header": header,
                "rows": polluted_rows,
                "row_ids": ["row-polluted-1"],
                "edited_at": polluted_latest["edited_at"],
                "revision_id": polluted_latest["revision_id"],
            },
            "_edited_ocr": {
                "latest": polluted_latest,
                "revisions": [polluted_latest],
            },
        }

    before = _snapshot(get_engine())
    output, output_error = order_service.get_ocr_output(
        order["id"],
        include_legacy_edits=False,
    )

    assert output is None
    assert output_error == "ocr_job_not_found"
    assert _snapshot(get_engine()) == before


def test_selected_page_preview_cannot_borrow_unowned_message_job_images(selected_history_order):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-ocr-pages-fallback-001")
    create_job(f"OCR-{order['id']}", input_reference="gs://order/reparse.json", status="failed")
    create_job(f"OCR-{order['message_id']}", input_reference="gs://message/first-pass.json", status="done")
    unowned = {"pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                          "layout_overlay_uri": "gs://bucket/layout-page-1.png"}]}
    original = deepcopy(order_service._load_order_ocr_cache(order["id"]))
    before = _snapshot(get_engine())
    assert order_service._save_order_ocr_cache(order["id"], unowned) is None
    pages, error = order_service.get_ocr_pages(order["id"], preview_only=True)
    assert error is None and isinstance(pages, dict)
    assert pages["pages"] and pages["page_count"] == len(pages["pages"])
    for page in pages["pages"]:
        assert page["ocr_overlay_uri"] is None
        assert page["ocr_overlay_url"] is None
        assert page["layout_overlay_uri"] is None
        assert "gs://bucket/" not in str(page.get("hakodate_overlay_uri"))
    assert order_service._load_order_ocr_cache(order["id"]) == original
    assert _snapshot(get_engine()) == before


def test_unselected_overlay_fragments_do_not_create_an_output(history_registration):
    order_service.clear_all()
    order = _seed_order(history_registration, message_id="msg-ocr-pages-synthetic-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|01/08|昼|Menu A|2|",
            "combined": {
                "corrected_pdf": "gs://bucket/corrected.pdf",
            },
        },
    )

    pages, error = order_service.get_ocr_pages(order["id"])

    assert pages is None
    assert error == "ocr_output_not_found"


def test_declared_template_grid_is_synthesized_without_pdf_detection(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Declared grid geometry must not trigger detection")
    monkeypatch.setattr(order_service, "detect_table_grid", forbidden)
    monkeypatch.setattr(order_service, "detect_table_grid_image", forbidden)
    box, units, columns, rows = order_service._resolve_ocr_pages_grid_metadata(
        template={"table_box": [0.1, 0.2, 0.9, 0.8], "grid_expected_columns": 4},
        pages=[], existing_table_box=None, existing_table_units=None,
        existing_grid_column_edges=None, existing_grid_row_edges=None, allow_expensive_detection=False)
    assert box == [0.1, 0.2, 0.9, 0.8]
    assert isinstance(columns, list) and len(columns) == 5
    assert abs(columns[0] - 0.1) < 1e-9 and abs(columns[-1] - 0.9) < 1e-9
    assert rows is None


def test_native_preview_defers_expensive_grid_detection_in_request_path(selected_history_order, monkeypatch):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-ocr-pages-overlay-grid-001")
    def forbidden(*args, **kwargs):
        pytest.fail("Preview reads must not load old overlays or detect grids")
    monkeypatch.setattr(order_service, "load_bytes_from_uri", forbidden)
    monkeypatch.setattr(order_service, "detect_table_grid", forbidden)
    monkeypatch.setattr(order_service, "detect_table_grid_image", forbidden)
    pages, error = order_service.get_ocr_pages(order["id"], preview_only=True)
    assert error is None and isinstance(pages, dict)
    assert pages["table_box"] is None
    assert pages["grid_column_edges"] is None
    assert pages["grid_row_edges"] is None
    assert pages["grid_detection_status"] == "deferred"
    assert pages["grid_detection_deferred_reason"] == "missing_template_grid_metadata:table_box,grid_column_edges,grid_row_edges"


def test_native_preview_only_does_not_load_markdown_and_compact_tables_preserve_cells(selected_history_order, monkeypatch):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-ocr-pages-preview-only-001")
    table = {"table_id": "tbl-1", "page_index": 1, "row_count": 2, "col_count": 4,
        "rows": [["日付", "区分", "メニュー", "常食"], ["04/26", "朝", "大豆のトマト煮", "11"]]}
    def forbidden(*args, **kwargs):
        pytest.fail("Preview-only must not load markdown")
    monkeypatch.setattr(order_service, "load_bytes_from_uri", forbidden)
    # Structured-table compaction remains a separate formatting component, not selectable evidence.
    assert order_service._compact_ocr_preview_tables_for_page(
        {"page_index": 1, "markdown_uri": "gs://bucket/page-1.md", "tables": [table]}, {}) == [table]
    pages, error = order_service.get_ocr_pages(order["id"], preview_only=True)
    assert error is None and pages["pages"]
    for page in pages["pages"]:
        assert page["markdown_uri"] is None and page["markdown_text"] is None
        assert page["ocr_overlay_uri"] is None and page["ocr_overlay_url"] is None
        assert page["layout_overlay_url"] is None
        assert page["figure_urls"] == []
    assert pages["combined"] == {}


def test_native_preview_does_not_regenerate_or_publish_cache_side_effects(selected_history_order, monkeypatch):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-ocr-pages-light-read-001")
    def forbidden(*args, **kwargs):
        pytest.fail("Preview reads must not regenerate, publish or refresh state")
    monkeypatch.setattr(order_service, "_augment_hakodate_ocr_payload_artifacts", forbidden)
    monkeypatch.setattr(order_service, "persist_ocr_evidence_run", forbidden)
    monkeypatch.setattr(order_service.workflow_state_service, "refresh_workflow_state", forbidden)
    before = _snapshot(get_engine())
    pages, error = order_service.get_ocr_pages(order["id"],
        preview_only=True, quantity_assignment_strategy="hakodate")
    assert error is None and isinstance(pages, dict)
    assert pages["hakodate_overlay_status"] in {"ready", "blocked"}
    assert isinstance(pages["hakodate_assignment"], dict)
    assert "target_cells" in pages["hakodate_assignment"]
    assert "assignments" in pages["hakodate_assignment"]
    assert "evidence_records" not in pages["hakodate_assignment"]
    assert _snapshot(get_engine()) == before


def test_hakodate_assignment_rejects_order_document_grid_and_full_page_tesseract():
    payload = {
        "hakodate_preprocessing": {
            "target_cell_map": [
                {
                    "target_cell_id": "order_document_grid-r0c3",
                    "sheet_cell": "R1C4",
                    "worksheet_row": 1,
                    "worksheet_col": 4,
                    "semantic_field": "qty.regular_x",
                    "bbox": [0.1, 0.1, 0.2, 0.2],
                    "center": [0.15, 0.15],
                    "source": "order_document_grid",
                }
            ]
        },
        "hakodate_ocr_evidence_records": [
            {
                "evidence_id": "ev-full-page",
                "run_id": "ORD-test:order-document",
                "engine": "hakodate_full_page_tesseract",
                "source_scope": "order_document_full_page",
                "raw_text": "3",
                "normalized_value": "3",
                "source_bbox": [0.1, 0.1, 0.2, 0.2],
                "center": [0.15, 0.15],
            }
        ],
    }

    before = deepcopy(payload)
    with pytest.raises(ValueError, match="^hakodate_target_source_invalid$"):
        order_service._build_hakodate_evidence_assignment_from_payload(
            order_id="ORD-test", facility_id="FAC-test", template_id="template-test",
            payload=payload, facility_config={},
        )
    assert order_service._hakodate_evidence_source_allowed(
        payload["hakodate_ocr_evidence_records"][0],
    ) is False
    assert payload == before


@pytest.mark.parametrize("cache", [
    {},
    {"hakodate_overlay": {"uri": "gs://bucket/unowned-overlay.png"}},
    {"hakodate_overlay": {"uri": "gs://bucket/unowned-overlay.png",
                         "producer": "hakodate_best_method_pipeline",
                         "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION}},
    {"hakodate_assignment_preview": {"fingerprint": "unowned", "assignment": {
        "target_cells": [{"target_cell_id": "cell-1", "bbox": [1, 2, 3, 4]}],
        "assignments": [{"target_cell_id": "cell-1", "assigned_value": "2"}]}}},
    {"pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/unowned-page.png"}]},
], ids=["missing-artifact", "unowned-uri", "unowned-pipeline", "unowned-compact", "legacy-page"])
def test_unowned_preview_cannot_publish_cached_artifacts(monkeypatch, tmp_path, cache):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot
    from tests.helpers.ingest_original import authored_sources

    authored_sources(monkeypatch, tmp_path, facility_id="preview-component", menu_date=date(2026, 1, 8),
                     seed_menu=False, seed_month=False)
    original = deepcopy(cache)
    before = _snapshot(get_engine())
    def forbidden(*_args, **_kwargs):
        pytest.fail("Unowned preview must not render, sign, build or publish artifacts")
    monkeypatch.setattr(order_service, "_load_order_ocr_cache", lambda _order_id: cache)
    for name in ("build_order_hakodate_assignment", "load_bytes_from_uri",
                 "render_pdf_to_png_bytes", "save_artifact_bytes_to_gcs", "_signed_url_from_uri"):
        monkeypatch.setattr(order_service, name, forbidden)
    preview = order_service._build_hakodate_overlay_preview(
        order_id="missing-preview-order", document_uri="gs://bucket/unowned-source.pdf")
    assert preview["status"] == "blocked"
    assert preview["blockers"] == ["synchronous_input_missing"]
    assert preview["overlay_url"] is None
    assert cache == original
    assert _snapshot(get_engine()) == before


def test_partial_cache_read_cannot_replace_owned_hakodate_artifacts(selected_history_order):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-ocr-cache-preserve-hakodate-001")
    captured = order_service._capture_synchronous_cache_write_input(order["id"])
    assert captured is not None
    before = _snapshot(get_engine())
    original = deepcopy(order_service._load_order_ocr_cache(order["id"]))
    assert order_service._extract_hakodate_target_cells_from_payload(original)
    assert order_service._extract_hakodate_ocr_evidence_records_from_payload(original, order_id=order["id"])
    result = order_service._save_order_ocr_cache(order["id"],
        {"pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr-page-1-updated.png"}],
         "engine": "read-preview"},
        synchronous_write_input=captured, augment_hakodate_artifacts=False,
        persist_evidence=False, refresh_workflow=False)
    assert result is None
    assert _snapshot(get_engine()) == before
    assert order_service._load_order_ocr_cache(order["id"]) == original


def test_assignment_repair_rejects_artifacts_without_source_proof_before_cache_write(selected_history_order, monkeypatch):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-hakodate-assignment-repair-001")
    target_cell = {
        "target_cell_id": "cell-1",
        "sheet_cell": "D3",
        "bbox": [0.1, 0.2, 0.3, 0.4],
        "center": [0.2, 0.3],
    }
    evidence_record = {
        "evidence_id": "ev-1",
        "engine": "hakodate_cell_crop_ocr",
        "source_scope": "hakodate_cell_crop_batch",
        "text": "2",
        "bbox": [0.12, 0.22, 0.28, 0.38],
        "center": [0.2, 0.3],
    }
    order_service._save_order_ocr_cache(
        order["id"],
        {"pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr-page-1.png"}]},
        augment_hakodate_artifacts=False,
        persist_evidence=False,
        refresh_workflow=False,
    )

    monkeypatch.setattr(
        order_service,
        "_resolve_effective_sheet_template",
        lambda **_kwargs: (
            {"facility_id": "FAC00001"},
            {"template_id": "template-hakodate", "quantity_assignment_strategy": "hakodate"},
            "template-hakodate",
            None,
        ),
    )
    monkeypatch.setattr(
        order_service,
        "_resolve_llm_review_baseline",
        lambda **_kwargs: {"baseline_source": "test", "rows": []},
    )

    attempts = []
    def _fake_augment(*, order_id, payload, template, force_hakodate=False, **ownership):
        attempts.append(ownership)
        assert order_id == order["id"]
        assert template["template_id"] == "template-hakodate"
        assert force_hakodate is False
        repaired = dict(payload)
        repaired["hakodate_preprocessing"] = {"target_cell_map": [target_cell]}
        repaired["hakodate_ocr_evidence_records"] = [evidence_record]
        return repaired

    monkeypatch.setattr(order_service, "_augment_hakodate_ocr_payload_artifacts", _fake_augment)

    before = order_service._load_order_ocr_cache(order["id"])
    captured = order_service._capture_synchronous_cache_write_input(order["id"])
    assert captured is not None
    database_before = _snapshot(get_engine())
    assignment, error = order_service.build_order_hakodate_assignment(order["id"],
        synchronous_write_input=captured, expected_revision=captured['workflow_revision'])
    cached = order_service._load_order_ocr_cache(order["id"])

    assert error is None and assignment is not None
    assert attempts == []
    assert _snapshot(get_engine()) == database_before
    assert cached == before


def test_explicit_assignment_repair_rejects_legacy_partial_artifacts(selected_history_order, monkeypatch):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-hakodate-explicit-strategy-repair-001")
    target_cell = {
        "target_cell_id": "cell-1",
        "sheet_cell": "D3",
        "bbox": [0.1, 0.2, 0.3, 0.4],
        "center": [0.2, 0.3],
    }
    evidence_record = {
        "evidence_id": "ev-1",
        "engine": "hakodate_cell_crop_ocr",
        "source_scope": "hakodate_cell_crop_batch",
        "text": "2",
        "bbox": [0.12, 0.22, 0.28, 0.38],
        "center": [0.2, 0.3],
    }
    order_service._save_order_ocr_cache(
        order["id"],
        {"pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr-page-1.png"}]},
        augment_hakodate_artifacts=False,
        persist_evidence=False,
        refresh_workflow=False,
    )
    monkeypatch.setattr(
        order_service,
        "_resolve_effective_sheet_template",
        lambda **_kwargs: (
            {"facility_id": "FAC00001"},
            {"template_id": "template-legacy", "quantity_assignment_strategy": "legacy"},
            "template-legacy",
            None,
        ),
    )
    monkeypatch.setattr(
        order_service,
        "_resolve_llm_review_baseline",
        lambda **_kwargs: {"baseline_source": "test", "rows": []},
    )

    attempts = []
    def _fake_augment(*, order_id, payload, template, force_hakodate=False, **ownership):
        attempts.append(ownership)
        assert order_id == order["id"]
        assert template["template_id"] == "template-legacy"
        assert force_hakodate is True
        repaired = dict(payload)
        repaired["hakodate_preprocessing"] = {"target_cell_map": [target_cell]}
        repaired["hakodate_ocr_evidence_records"] = [evidence_record]
        return repaired

    monkeypatch.setattr(order_service, "_augment_hakodate_ocr_payload_artifacts", _fake_augment)

    before = order_service._load_order_ocr_cache(order["id"])
    captured = order_service._capture_synchronous_cache_write_input(order["id"])
    assert captured is not None
    database_before = _snapshot(get_engine())
    assignment, error = order_service.build_order_hakodate_assignment(order["id"], strategy="hakodate",
        synchronous_write_input=captured, expected_revision=captured['workflow_revision'])

    assert error == "hakodate_own_artifact_representations_conflict" and assignment is None
    assert len(attempts) == 1 and attempts[0]['synchronous_write_input'] is captured
    assert order_service._load_order_ocr_cache(order['id']) == before
    assert _snapshot(get_engine()) == database_before


def test_explicit_assignment_repair_cannot_publish_unowned_replacements(selected_history_order, monkeypatch):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-hakodate-explicit-regenerates-stale-001")
    stale_target = {
        "target_cell_id": "cell-1",
        "sheet_cell": "D3",
        "bbox": [0.1, 0.2, 0.3, 0.4],
        "center": [0.2, 0.3],
    }
    fresh_target = {
        "target_cell_id": "cell-1",
        "sheet_cell": "D3",
        "bbox": [0.1, 0.2, 0.3, 0.4],
        "center": [0.2, 0.3],
    }
    stale_evidence = {
        "evidence_id": "ev-stale",
        "engine": "hakodate_cell_crop_ocr",
        "source_scope": "hakodate_cell_crop_batch",
        "text": "9",
        "bbox": [0.12, 0.22, 0.28, 0.38],
        "center": [0.2, 0.3],
    }
    fresh_evidence = {
        "evidence_id": "ev-fresh",
        "engine": "hakodate_cell_crop_ocr",
        "source_scope": "hakodate_cell_crop_batch",
        "text": "2",
        "bbox": [0.12, 0.22, 0.28, 0.38],
        "center": [0.2, 0.3],
    }
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "hakodate_preprocessing": {"target_cell_map": [stale_target]},
            "hakodate_ocr_evidence_records": [stale_evidence],
        },
        augment_hakodate_artifacts=False,
        persist_evidence=False,
        refresh_workflow=False,
    )
    monkeypatch.setattr(
        order_service,
        "_resolve_effective_sheet_template",
        lambda **_kwargs: (
            {"facility_id": "FAC00001"},
            {"template_id": "template-legacy", "quantity_assignment_strategy": "legacy"},
            "template-legacy",
            None,
        ),
    )
    monkeypatch.setattr(
        order_service,
        "_resolve_llm_review_baseline",
        lambda **_kwargs: {"baseline_source": "test", "rows": []},
    )

    attempts = []
    def _fake_augment(*, order_id, payload, template, force_hakodate=False, **ownership):
        attempts.append(ownership)
        assert order_id == order["id"]
        assert force_hakodate is True
        repaired = dict(payload)
        repaired["hakodate_preprocessing"] = {"target_cell_map": [fresh_target]}
        repaired["hakodate_ocr_evidence_records"] = [fresh_evidence]
        return repaired

    monkeypatch.setattr(order_service, "_augment_hakodate_ocr_payload_artifacts", _fake_augment)

    before = order_service._load_order_ocr_cache(order["id"])
    captured = order_service._capture_synchronous_cache_write_input(order["id"])
    assert captured is not None
    database_before = _snapshot(get_engine())
    assignment, error = order_service.build_order_hakodate_assignment(order["id"], strategy="hakodate",
        synchronous_write_input=captured, expected_revision=captured['workflow_revision'])

    assert error == "hakodate_own_artifact_representations_conflict" and assignment is None
    assert len(attempts) == 1 and attempts[0]['synchronous_write_input'] is captured
    assert order_service._load_order_ocr_cache(order['id']) == before
    assert _snapshot(get_engine()) == database_before


@pytest.mark.parametrize("validation", ["baseline", "assignment"])
def test_assignment_repair_validates_before_publishing_cache(selected_history_order, monkeypatch, validation):
    from src.db import get_engine
    from types import SimpleNamespace
    from src.services import hakodate_cell_ocr_batch_service as batch
    from src.services.storage_service import load_selected_pdf_bytes
    from tests.helpers.authored_cell_values import declared_field
    from tests.unit.test_h2_operator_repair_revision import _provider_output_files
    from tests.unit.test_disabled_facility_columns import _snapshot

    order = _seed_order(selected_history_order, message_id=f"repair-validation-before-save-{validation}")
    captured = order_service._capture_synchronous_cache_write_input(order["id"])
    assert captured is not None
    original = deepcopy(captured["evidence"]["payload_json"])
    before = _snapshot(get_engine())
    calls = []
    actual_augment = order_service._augment_hakodate_ocr_payload_artifacts
    declared = {"qty.regular_2f": "2", "qty.regular_3f": "1", "qty.soft_2f": "3",
                "qty.soft_3f": "", "qty.mixer_2f": "", "qty.mixer_3f": "", "remarks": ""}

    def render(**kwargs):
        assert kwargs["order_id"] == order["id"]
        receipt = captured["selected_pdf_receipt"]
        return {"configured_input": deepcopy(captured["configured_input"]),
                "selected_pdf_receipt": deepcopy(receipt),
                "_verified_pdf_bytes": load_selected_pdf_bytes(receipt["storage_uri"], receipt)}

    def recognize(*, item, output_dir, **kwargs):
        regions = [{**deepcopy(region), "bbox": [index * 20, 0, index * 20 + 10, 10]}
                   for index, region in enumerate(item["original_target_semantics"])]
        values = {}
        for region in regions:
            assigned = set()
            for target in region["logical_targets"]:
                assert (target["date"], order_service._normalize_daypart_key(target["daypart"]),
                        target["menu_name"]) == ("2026-01-08", "昼", "Menu A")
                assigned.add(declared[declared_field(target, list(declared))])
            assert len(assigned) == 1
            values[region["sheet_cell"]] = assigned.pop()
        records = [{**region, "ocr_text": values[region["sheet_cell"]], "recognizer_score": 1.0,
                    "recognizer_accepted_candidate": {"normalized_digits": values[region["sheet_cell"]]}}
                   for region in regions]
        return SimpleNamespace(metrics={"expected_sheet_cells": item["original_expected_sheet_cells"]},
            expected_sheet_cells=tuple(item["original_expected_sheet_cells"]),
            outputs=_provider_output_files(output_dir, records=records, regions=regions, sheet_values=values),
            ocr_engine="authored-component-external-recognizer"), None

    def produce_existing_owned_artifacts(**kwargs):
        assert kwargs["synchronous_write_input"] is captured
        calls.append("repair")
        repaired = actual_augment(**kwargs)
        assert repaired["hakodate_canonical_pipeline"]["status"] == "ready", repaired
        return repaired

    def refuse(**kwargs):
        calls.append(validation)
        raise order_service._CanonicalOcrProjectionError("hakodate_target_source_invalid")

    def forbidden(*args, **kwargs):
        pytest.fail("Rejected assignment must not reach cache publication")

    monkeypatch.setattr(order_service, "_augment_hakodate_ocr_payload_artifacts", produce_existing_owned_artifacts)
    monkeypatch.setattr(order_service, "_build_live_hakodate_manifest_item", render)
    monkeypatch.setattr(batch, "build_hakodate_best_method_for_manifest_item", recognize)
    boundary = ("_resolve_llm_review_baseline" if validation == "baseline"
                else "_build_hakodate_evidence_assignment_from_payload")
    monkeypatch.setattr(order_service, boundary, refuse)
    monkeypatch.setattr(order_service, "_save_order_ocr_cache", forbidden)
    assignment, error = order_service.build_order_hakodate_assignment(
        order["id"], strategy="hakodate", synchronous_write_input=captured,
        expected_revision=captured["workflow_revision"],
    )
    assert assignment is None and error == "hakodate_target_source_invalid"
    assert calls == ["repair", validation]
    assert _snapshot(get_engine()) == before
    assert captured["evidence"]["payload_json"] == original


def test_ensure_hakodate_current_refuses_unselected_cache_artifacts(selected_history_order, monkeypatch):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-hakodate-cache-draft-current-001")
    target_cell = {
        "target_cell_id": "D3",
        "sheet_cell": "D3",
        "worksheet_row": 3,
        "worksheet_col": 4,
        "bbox": [0.1, 0.2, 0.3, 0.4],
        "center": [0.2, 0.3],
    }
    evidence_record = {
        "evidence_id": "ev-1",
        "engine": "hakodate_cell_crop_ocr",
        "source_scope": "hakodate_cell_crop_batch",
        "text": "2",
        "bbox": [0.12, 0.22, 0.28, 0.38],
        "center": [0.2, 0.3],
    }
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "hakodate_preprocessing": {"target_cell_map": [target_cell]},
            "hakodate_ocr_evidence_records": [evidence_record],
        },
        augment_hakodate_artifacts=False,
        persist_evidence=False,
        refresh_workflow=False,
    )
    projected_sheet = {
        "fields": ["date", "menu", "qty.regular"],
        "rows": [["04/26", "menu", "2"]],
        "source": "hakodate_ocr_evidence_sheet",
        "hakodate_evidence_projection": {"metrics": {"applied_count": 1}},
    }
    monkeypatch.setattr(
        order_service,
        "build_order_hakodate_projected_sheet",
        lambda _order_id, **_kwargs: ({"projected_sheet": projected_sheet}, None),
    )

    captured = order_service._capture_synchronous_cache_write_input(order["id"])
    assert captured is not None
    before = order_service.get_latest_sheet_draft(order["id"], backfill_from_revision=False)
    assert before is not None and captured["draft"] == before
    database_before = _snapshot(get_engine())
    draft, error = order_service.ensure_hakodate_evidence_draft_current(order["id"],
        synchronous_write_input=captured)

    assert error is None and draft == before
    assert draft["draft_sheet_json"] != projected_sheet
    assert order_service.get_latest_sheet_draft(order["id"], backfill_from_revision=False) == before
    assert _snapshot(get_engine()) == database_before


def test_ensure_hakodate_current_preserves_selected_operator_sheet_without_projection_metadata(selected_history_order, monkeypatch):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-owned-native-current-projection")
    captured = order_service._capture_synchronous_cache_write_input(order["id"])
    assert captured is not None
    assert order_service._draft_record_is_authoritative_current_sheet(captured["draft"])
    assert not order_service._draft_payload_has_hakodate_evidence_projection(captured["draft"])
    selected_rows = deepcopy(captured["draft"]["draft_sheet_json"]["rows"])

    def forbidden(*args, **kwargs):
        pytest.fail("A captured selected native projection must not be replaced on read")

    monkeypatch.setattr(order_service, "get_latest_ocr_evidence_run", forbidden)
    monkeypatch.setattr(order_service, "switch_draft_to_latest_evidence", forbidden)
    before = _snapshot(get_engine())
    draft, error = order_service.ensure_hakodate_evidence_draft_current(
        order["id"], synchronous_write_input=captured)
    assert error is None and draft == captured["draft"]
    assert draft["draft_sheet_json"]["rows"] == selected_rows
    assert _snapshot(get_engine()) == before


def test_unselected_partial_page_fragments_cannot_trigger_read_time_repair(history_registration, monkeypatch):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    order = _seed_order(history_registration, message_id="msg-ocr-pages-repair-on-hakodate-preview-001")
    def forbidden(*args, **kwargs):
        pytest.fail("An unselected read must not manufacture owned artifacts")
    monkeypatch.setattr(order_service, "_augment_hakodate_ocr_payload_artifacts", forbidden)
    before = _snapshot(get_engine())
    assert order_service._save_order_ocr_cache(order["id"],
        {"pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr-page-1.png"}]},
        augment_hakodate_artifacts=False, persist_evidence=False, refresh_workflow=False) is None
    assert order_service.get_ocr_pages(order["id"], preview_only=True,
        quantity_assignment_strategy="hakodate") == (None, "ocr_output_not_found")
    assert _snapshot(get_engine()) == before


def test_actual_owned_hakodate_overlay_is_available_without_legacy_pages(selected_history_order, monkeypatch):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-ocr-pages-hakodate-overlay-only-001")
    cached = deepcopy(order_service._load_order_ocr_cache(order["id"]))
    assert not cached.get("pages")
    def forbidden(*args, **kwargs):
        pytest.fail("A native preview read must not repair source artifacts")
    monkeypatch.setattr(order_service, "_augment_hakodate_ocr_payload_artifacts", forbidden)
    pages, error = order_service.get_ocr_pages(order["id"],
        preview_only=True, quantity_assignment_strategy="hakodate")
    assert error is None and isinstance(pages, dict)
    assert pages["page_count"] == 1
    assert pages["pages"][0]["synthetic_source"] == "hakodate_overlay_only"
    assert pages["pages"][0]["ocr_overlay_url"] is None
    assert pages["hakodate_overlay_status"] == "ready"
    assert pages["pages"][0]["hakodate_overlay_url"]
    assert order_service._load_order_ocr_cache(order["id"]) == cached


def test_actual_native_page_remains_available_without_template_grid_metadata(selected_history_order, monkeypatch):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-ocr-pages-no-grid-001")
    def forbidden(*args, **kwargs):
        pytest.fail("Missing grid metadata must not trigger overlay or PDF detection")
    monkeypatch.setattr(order_service, "detect_table_grid_image", forbidden)
    monkeypatch.setattr(order_service, "detect_table_grid", forbidden)
    pages, error = order_service.get_ocr_pages(order["id"])
    assert error is None and isinstance(pages, dict)
    assert len(pages["pages"]) == pages["page_count"] == 1
    assert pages["active_page_index"] == pages["table_page_index"] == 1
    assert pages["table_box"] is None
    assert pages["grid_column_edges"] is None and pages["grid_row_edges"] is None
    assert pages["grid_detection_status"] == "deferred"


def test_partial_cache_write_cannot_reuse_old_page_images_or_identity(selected_history_order):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-ocr-pages-cache-preserve-001")
    captured = order_service._capture_synchronous_cache_write_input(order["id"])
    assert captured is not None
    original = deepcopy(order_service._load_order_ocr_cache(order["id"]))
    before = _snapshot(get_engine())
    for partial in (
        {"status": "success", "table_raw": "|日付|区分|メニュー|常食2F|"},
        {"combined": {"corrected_pdf": "gs://bucket/corrected.pdf"},
         "template_id": "fax-template", "engine": "yomitoku", "facility_id": "FAC00001"},
    ):
        result = order_service._save_order_ocr_cache(order["id"], partial,
            synchronous_write_input=captured, augment_hakodate_artifacts=False,
            persist_evidence=False, refresh_workflow=False)
        assert result is None
        assert order_service._load_order_ocr_cache(order["id"]) == original
        assert _snapshot(get_engine()) == before


def test_export_saved_label_cannot_rebase_header_through_removed_legacy_template_write(selected_history_order, tmp_path):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-export-sheet-label-rebase-001")
    current, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and current is not None
    quantity = current["fields"].index("qty.regular_2f")
    saved_row = list(current["rows"][0])
    saved_row[quantity], saved_row[-1] = "12", "rebased-label"
    saved, error = order_service.save_ocr_sheet_exact(order["id"], header=current["header"],
        rows=[saved_row], ui_mode="sheet", fields=current["fields"], row_ids=[current["row_ids"][0]])
    assert error is None and saved is not None
    selected, error = _save_formal_sheet(order_id=order["id"],
        sheet={**current, "rows": [saved_row], "row_ids": [current["row_ids"][0]]},
        edited_by="authored-component-operator")
    assert error is None and selected is not None
    captured = order_service._capture_synchronous_cache_write_input(order["id"])
    columns = deepcopy(captured["configured_input"]["facility"]["fax_template"]["columns"])
    for column in columns:
        if column.get("role") == "quantity" and column.get("diet_type") == "regular" and column.get("area_id") == "2F":
            column["header"] = "新常食2F"
    before = _snapshot(get_engine())
    assert order_service.save_order_facility_template_columns(order["id"], columns) == (
        None, "legacy_facility_template_columns_removed")
    assert _snapshot(get_engine()) == before
    path = tmp_path / "labels" / f"{order['id']}.expected_sheet.json"
    exported, error = order_service.export_ocr_sheet_label(order["id"], output_path=path)
    assert error is None and exported is not None
    actual = json.loads(path.read_text(encoding="utf-8"))
    assert actual["fields"] == current["fields"]
    assert actual["header"] == current["header"]
    assert actual["rows"] == [saved_row]
    assert actual["row_ids"] == [current["row_ids"][0]]
    assert actual["rows"][0][quantity] == "12" and actual["rows"][0][-1] == "rebased-label"


def test_raw_generic_issue_metadata_does_not_override_selected_source(selected_history_order):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-sheet-generic-review-001")
    diagnostic_payload = {
            "rows": [
                {
                    "row_index": 0,
                    "date_mmdd": "01/08",
                    "daypart": "昼",
                    "menu": "Menu A",
                    "qty": {
                        "regular_2f": 2,
                        "regular_3f": 1,
                        "soft_2f": 3,
                    },
                }
            ],
            "cell_issues": [
                {
                    "row_index": 0,
                    "column_index": 3,
                    "field": "qty.regular_2f",
                    "issue_code": "merged_numeric_cell",
                    "severity": "warning",
                    "source": "yomitoku_structured",
                    "bbox": [0.11, 0.22, 0.33, 0.44],
                    "text": "6\n9",
                }
            ],
        }
    _assert_raw_diagnostics_do_not_override_selected_source(order["id"], diagnostic_payload, 'cell_issues')


def _seed_monthly_menu_daypart_order_2099_11() -> None:
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-11"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-11"))
        menu = session.get(MonthlyMenu, "2099-11")
        if not menu:
            session.add(
                MonthlyMenu(
                    id="2099-11",
                    month_start=date(2099, 11, 1),
                    filename="seed-2099-11.xlsx",
                )
            )
        seed_entries = [
            ("seed-entry-2099-11-15-breakfast", "朝食", "朝メニュー", 1),
            ("seed-entry-2099-11-15-lunch", "昼食", "昼メニュー", 1),
            ("seed-entry-2099-11-15-dinner", "夕食", "夕メニュー", 1),
        ]
        for entry_id, daypart, name, slot_index in seed_entries:
            exists = session.get(MonthlyMenuEntry, entry_id)
            if exists:
                continue
            session.add(
                MonthlyMenuEntry(
                    id=entry_id,
                    monthly_menu_id="2099-11",
                    menu_date=date(2099, 11, 15),
                    daypart=daypart,
                    name=name,
                    slot_index=slot_index,
                    diet_type="regular",
                )
            )


def _seed_monthly_menu_boundary_2026_01_02() -> None:
    with session_scope() as session:
        session.execute(
            delete(MonthlyMenuEntry).where(
                MonthlyMenuEntry.monthly_menu_id.in_(["2026-01", "2026-02"])
            )
        )
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id.in_(["2026-01", "2026-02"])))
        session.add_all(
            [
                MonthlyMenu(
                    id="2026-01",
                    month_start=date(2026, 1, 1),
                    filename="seed-2026-01-boundary.xlsx",
                ),
                MonthlyMenu(
                    id="2026-02",
                    month_start=date(2026, 2, 1),
                    filename="seed-2026-02-boundary.xlsx",
                ),
            ]
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="seed-entry-2026-01-31-breakfast-boundary",
                    monthly_menu_id="2026-01",
                    menu_date=date(2026, 1, 31),
                    daypart="朝食",
                    name="Boundary Jan",
                    slot_index=0,
                    diet_type="regular",
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2026-02-01-breakfast-boundary",
                    monthly_menu_id="2026-02",
                    menu_date=date(2026, 2, 1),
                    daypart="朝食",
                    name="Boundary Feb",
                    slot_index=0,
                    diet_type="regular",
                ),
            ]
        )


def test_get_ocr_sheet_from_selected_owned_evidence(selected_history_order):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-sheet-001")

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["order_id"] == order["id"]
    assert sheet["facility_id"] == "FAC00001"
    current, current_error = order_workflow_v2_service.get_workflow_readonly(order["id"])
    assert current_error is None
    assert sheet["base_evidence_run_id"] == current["selected_ocr_result_id"]
    assert sum(field.startswith("qty.") for field in sheet["fields"]) == 6
    assert isinstance(sheet.get("fields"), list) and len(sheet["fields"]) >= 1
    assert isinstance(sheet.get("header"), list) and len(sheet["header"]) == len(sheet["fields"])
    assert isinstance(sheet.get("rows"), list) and len(sheet["rows"]) >= 1
    assert isinstance(sheet.get("row_ids"), list) and len(sheet["row_ids"]) == len(sheet["rows"])

    fields = sheet["fields"]
    first_row = sheet["rows"][0]
    regular_2f_idx = fields.index("qty.regular_2f")
    regular_3f_idx = fields.index("qty.regular_3f")
    soft_2f_idx = fields.index("qty.soft_2f")
    assert first_row[regular_2f_idx] == "2"
    assert first_row[regular_3f_idx] == "1"
    assert first_row[soft_2f_idx] == "3"


def test_selected_sheet_daypart_order_is_morning_first(selected_history_order):
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-daypart-order-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    lines = [
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 1,
        }
    ]
    order = _register_scoped_order(selected_history_order, payload, lines=lines, week='2099-11@2099-11-15~2099-11-21')

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["base_evidence_run_id"]

    fields = sheet["fields"]
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")

    target_rows = [row for row in sheet["rows"] if date_idx < len(row) and row[date_idx] == "11/15"]
    assert len(target_rows) >= 3
    assert [target_rows[0][daypart_idx], target_rows[1][daypart_idx], target_rows[2][daypart_idx]] == [
        "朝",
        "昼",
        "夕",
    ]
    assert [target_rows[0][menu_idx], target_rows[1][menu_idx], target_rows[2][menu_idx]] == [
        "朝メニュー",
        "昼メニュー",
        "夕メニュー",
    ]


def test_selected_identity_ignores_unowned_payload_menu_noise(selected_history_order):
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-payload-priority-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    # The explicit source contains 6/5; an unrelated cache cannot relabel them.
    lines = [
        {
            "date": "2099-11-15",
            "daypart": "昼",
            "menu_name": "昼メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 6,
        },
        {
            "date": "2099-11-15",
            "daypart": "夕",
            "menu_name": "夕メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 5,
        },
    ]
    order = _register_scoped_order(selected_history_order, payload, lines=lines,
        week='2099-11@2099-11-15~2099-11-21')
    selected_payload = order_service.get_cached_ocr_payload(order["id"])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "夕", "OCRノイズメニュー", "7", "", "", "", "", "", "payload-note"],
                ["11/15", "夕", "OCRノイズメニュー2", "", "", "", "", "", "", ""],
                ["11/15", "夕", "OCRノイズメニュー3", "", "", "", "", "", "", ""],
            ],
            "date_strings": ["11/15"],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert order_service.get_cached_ocr_payload(order["id"]) == selected_payload

    fields = sheet["fields"]
    qty_idx = next(
        idx
        for idx, field in enumerate(fields)
        if field in {"qty.regular_2f", "qty.regular_x"}
    )
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")

    target_rows = [row for row in sheet["rows"] if row and row[0] == "11/15"]
    assert len(target_rows) >= 3
    breakfast = next(row for row in target_rows if row[daypart_idx] == "朝" and row[menu_idx] == "朝メニュー")
    lunch = next(row for row in target_rows if row[daypart_idx] == "昼" and row[menu_idx] == "昼メニュー")
    dinner = next(row for row in target_rows if row[daypart_idx] == "夕" and row[menu_idx] == "夕メニュー")
    assert breakfast[qty_idx] == ""
    assert lunch[qty_idx] == "6"
    assert dinner[qty_idx] == "5"


def test_canonicalize_sheet_daypart_rows_preserves_unknown_tokens_and_blocks_projection():
    from copy import deepcopy
    from tests.unit.test_modernization_ocr_canonical_projection import orphan_projection

    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    rows = [
        ["04/05", '"', "Menu A", ""],
        ["04/05", "61", "Menu B", ""],
        ["04/07", "&", "Menu C", ""],
        ["04/07", "€", "Menu D", ""],
        ["04/11", "品", "Menu E", ""],
    ]

    before = deepcopy((fields, rows))
    normalized = order_service._canonicalize_sheet_daypart_rows(rows=rows, fields=fields)

    assert normalized == before[1]
    assert (fields, rows) == before
    with pytest.raises(fax_extractor.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        orphan_projection(fields, [{"values": row} for row in normalized], normalized)
    assert normalized == before[1]
    assert (fields, rows) == before


def test_canonicalize_sheet_daypart_rows_preserves_unknown_and_blank_beside_known_anchors():
    from copy import deepcopy
    from tests.unit.test_modernization_ocr_canonical_projection import orphan_projection

    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    rows = [
        ["04/05", "61", "Menu A", ""],
        ["04/05", "昼", "Menu B", ""],
        ["04/07", "", "Menu C", ""],
        ["04/07", "夕", "Menu D", ""],
    ]

    before = deepcopy((fields, rows))
    normalized = order_service._canonicalize_sheet_daypart_rows(rows=rows, fields=fields)

    assert normalized == before[1]
    assert (fields, rows) == before
    with pytest.raises(fax_extractor.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        orphan_projection(fields, [{"values": row} for row in normalized], normalized)
    assert normalized == before[1]
    assert (fields, rows) == before


def test_overlay_payload_dayparts_onto_sheet_rows_by_date_order_keeps_menu_rows():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    rows = [
        {"values": ["04/05", "朝", "豚肉の卵とじ", "4"]},
        {"values": ["04/05", "朝", "いんげんのカニ和え", "4"]},
        {"values": ["04/05", "朝", "サワラの西京焼き", "2"]},
        {"values": ["04/05", "朝", "じゃが芋の煮物", "2"]},
    ]
    payload_rows = [
        ["04/05", "朝", "am", "豚肉の卵とじ"],
        ["", "昼", "W2", "いんげんのカニ和え"],
        ["", "夕", "主人", "サワラの西京焼き"],
        ["", "夕", "DKD", "じゃが芋の煮物"],
    ]

    overlaid, count = order_service._overlay_payload_dayparts_onto_sheet_rows(
        rows=rows,
        fields=fields,
        payload_rows=payload_rows,
    )

    assert count == 3
    assert [row["values"][1] for row in overlaid] == ["朝", "昼", "夕", "夕"]
    assert [row["values"][2] for row in overlaid] == [
        "豚肉の卵とじ",
        "いんげんのカニ和え",
        "サワラの西京焼き",
        "じゃが芋の煮物",
    ]


def test_apply_payload_quantities_numeric_only_skips_cross_date_rows():
    from tests.unit.test_modernization_ocr_canonical_projection import orphan_projection

    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
    rows = [
        {"values": ["02/15", "朝", "Menu A", "", "keep-a"]},
        {"values": ["02/15", "昼", "Menu B", "", "keep-b"]},
    ]
    payload_rows = [
        ["12/31", "夕", "OCRノイズA", "20", "payload-note-a"],
        ["01/01", "朝", "OCRノイズB", "10", "payload-note-b"],
    ]

    with pytest.raises(fax_extractor.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        orphan_projection(fields, rows, payload_rows)
    assert rows[0]["values"][0] == "02/15"
    assert rows[0]["values"][1] == "朝"
    assert rows[0]["values"][2] == "Menu A"
    assert rows[0]["values"][3] == ""
    assert rows[0]["values"][4] == "keep-a"
    assert rows[1]["values"][0] == "02/15"
    assert rows[1]["values"][1] == "昼"
    assert rows[1]["values"][2] == "Menu B"
    assert rows[1]["values"][3] == ""
    assert rows[1]["values"][4] == "keep-b"


def test_canonical_projection_rejects_missing_dates_and_mismatched_menus():
    from tests.unit.test_modernization_ocr_canonical_projection import orphan_projection

    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
    rows = [
        {"values": ["02/15", "朝", "Menu A", "", "keep-a"]},
        {"values": ["02/15", "昼", "Menu B", "", "keep-b"]},
    ]
    payload_rows = [
        ["", "", "OCRノイズA", "20", "payload-note-a"],
        ["", "", "OCRノイズB", "10", "payload-note-b"],
    ]

    with pytest.raises(fax_extractor.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        orphan_projection(fields, rows, payload_rows)


def test_canonical_projection_rejects_noisy_dayparts_and_missing_identity():
    from tests.unit.test_modernization_ocr_canonical_projection import orphan_projection

    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
    rows = [
        {"values": ["02/15", "朝", "Menu A", "", "keep-a"]},
        {"values": ["02/15", "昼", "Menu B", "", "keep-b"]},
        {"values": ["02/15", "夕", "Menu C", "", "keep-c"]},
    ]
    payload_rows = [
        ["02/15", "朝", "", "20", "payload-note-a"],
        ["", "61", "", "10", "payload-note-b"],
        ["", "€", "", "8", "payload-note-c"],
    ]

    with pytest.raises(fax_extractor.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        orphan_projection(fields, rows, payload_rows)


def test_canonical_projection_rejects_sparse_quantities_with_mismatched_menus():
    from tests.unit.test_modernization_ocr_canonical_projection import orphan_projection

    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "qty.regular_3f", "qty.mixer_3f", "remarks"]
    rows = [
        {"values": ["02/15", "朝", "Menu A", "", "", "", "keep-a"]},
        {"values": ["02/15", "昼", "Menu B", "", "", "", "keep-b"]},
    ]
    payload_rows = [
        ["02/15", "朝", "OCR A", "4", "9", "", "note-a"],
        ["02/15", "昼", "OCR B", "4", "9", "58", "note-b"],
    ]

    with pytest.raises(fax_extractor.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        orphan_projection(fields, rows, payload_rows)


def test_canonical_projection_rejects_identityless_cluster_edge_quantities():
    from tests.unit.test_modernization_ocr_canonical_projection import orphan_projection

    fields = ["date_mmdd", "daypart", "menu", "qty.regular_3f", "qty.soft_2f", "remarks"]
    rows = [
        {"values": ["02/15", "朝", "Menu A", "", "", "keep-a"]},
        {"values": ["02/15", "朝", "Menu B", "", "", "keep-b"]},
    ]
    payload_rows = [
        ["", "", "", "", "", ""],
        ["", "", "", "4", "9", ""],
    ]

    with pytest.raises(fax_extractor.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        orphan_projection(fields, rows, payload_rows)


def test_canonical_projection_rejects_identityless_quantities_without_consensus():
    from tests.unit.test_modernization_ocr_canonical_projection import orphan_projection

    fields = ["date_mmdd", "daypart", "menu", "qty.regular_3f", "qty.soft_2f", "remarks"]
    rows = [
        {"values": ["02/15", "朝", "Menu A", "", "", "keep-a"]},
        {"values": ["02/15", "朝", "Menu B", "", "", "keep-b"]},
    ]
    payload_rows = [
        ["", "", "", "", "", ""],
        ["", "", "", "4", "9", ""],
    ]

    with pytest.raises(fax_extractor.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        orphan_projection(fields, rows, payload_rows)


def test_canonical_projection_preserves_blanks_with_three_explicit_quantities():
    from tests.unit.test_modernization_ocr_canonical_projection import orphan_projection

    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
    rows = [
        {"values": ["02/15", "朝", "Menu A1", "", ""]},
        {"values": ["02/15", "朝", "Menu A2", "", ""]},
        {"values": ["02/16", "朝", "Menu B1", "", ""]},
        {"values": ["02/16", "朝", "Menu B2", "", ""]},
        {"values": ["02/17", "朝", "Menu C1", "", ""]},
        {"values": ["02/17", "朝", "Menu C2", "", ""]},
        {"values": ["02/18", "朝", "Menu D1", "", ""]},
        {"values": ["02/18", "朝", "Menu D2", "", ""]},
    ]
    payload_rows = [
        ["02/15", "朝", "Menu A1", "", ""],
        ["02/15", "朝", "Menu A2", "", ""],
        ["02/16", "朝", "Menu B1", "20", ""],
        ["02/16", "朝", "Menu B2", "", ""],
        ["02/17", "朝", "Menu C1", "20", ""],
        ["02/17", "朝", "Menu C2", "", ""],
        ["02/18", "朝", "Menu D1", "20", ""],
        ["02/18", "朝", "Menu D2", "", ""],
    ]

    projected, stats = orphan_projection(fields, rows, payload_rows)
    assert projected == payload_rows
    assert stats["exact"] == len(rows)
    assert stats["row_index"] == stats["cluster_fill"] == stats["span_copy"] == 0
    assert [row[3] for row in projected] == ['', '', '20', '', '20', '', '20', '']
    assert [row[:3] + row[4:] for row in projected] == [
        row["values"][:3] + row["values"][4:] for row in rows
    ]


def test_canonical_projection_preserves_blanks_with_two_explicit_quantities():
    from tests.unit.test_modernization_ocr_canonical_projection import orphan_projection

    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
    rows = [
        {"values": ["02/15", "朝", "Menu A1", "", ""]},
        {"values": ["02/15", "朝", "Menu A2", "", ""]},
        {"values": ["02/16", "朝", "Menu B1", "", ""]},
        {"values": ["02/16", "朝", "Menu B2", "", ""]},
        {"values": ["02/17", "朝", "Menu C1", "", ""]},
        {"values": ["02/17", "朝", "Menu C2", "", ""]},
    ]
    payload_rows = [
        ["02/15", "朝", "Menu A1", "", ""],
        ["02/15", "朝", "Menu A2", "", ""],
        ["02/16", "朝", "Menu B1", "20", ""],
        ["02/16", "朝", "Menu B2", "", ""],
        ["02/17", "朝", "Menu C1", "20", ""],
        ["02/17", "朝", "Menu C2", "", ""],
    ]

    projected, stats = orphan_projection(fields, rows, payload_rows)
    assert projected == payload_rows
    assert stats["exact"] == len(rows)
    assert stats["row_index"] == stats["cluster_fill"] == stats["span_copy"] == 0
    assert [row[3] for row in projected] == ['', '', '20', '', '20', '']
    assert [row[:3] + row[4:] for row in projected] == [
        row["values"][:3] + row["values"][4:] for row in rows
    ]


def test_canonical_projection_rejects_span_markers_without_matching_identity():
    from tests.unit.test_modernization_ocr_canonical_projection import orphan_projection

    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
    rows = [
        {"values": ["02/15", "朝", "Menu A", "", ""]},
        {"values": ["02/15", "朝", "Menu B", "", ""]},
        {"values": ["02/15", "朝", "Menu C", "", ""]},
    ]
    payload_rows = [
        ["", "", "OCR-A", ")20)20)20", ""],
    ]

    with pytest.raises(fax_extractor.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        orphan_projection(fields, rows, payload_rows)


def test_canonical_projection_rejects_parenthesized_hints_with_incomplete_identity():
    from tests.unit.test_modernization_ocr_canonical_projection import orphan_projection

    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
    rows = [
        {"values": ["02/19", "昼", "Menu A", "", ""]},
        {"values": ["02/19", "昼", "Menu B", "", ""]},
        {"values": ["02/19", "昼", "Menu C", "", ""]},
    ]
    payload_rows = [
        ["02/19", "昼", "Menu A", "20", ""],
        ["", "", "取川(2)", "", ""],
        ["", "", "Menu C", "", ""],
    ]

    with pytest.raises(fax_extractor.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        orphan_projection(fields, rows, payload_rows)


def test_canonical_projection_rejects_mixed_quantity_text_without_identity():
    from tests.unit.test_modernization_ocr_canonical_projection import orphan_projection

    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_3f",
        "qty.soft_2f",
        "qty.mixer_3f",
        "remarks",
    ]
    rows = [
        {"values": ["02/15", "昼", f"Menu {idx}", "", "", "", ""]}
        for idx in range(8)
    ]
    payload_rows = [
        ["", "", "", "4", "9", "58", ""],
        ["", "", "", "4", "9", "8", ""],
        ["", "", "", "4", "9", "タ", ""],
        ["", "", "", "4", "9", "\"", ""],
        ["", "", "", "4", "9", "a", ""],
        ["", "", "", "4", "9", "END", ""],
        ["", "", "", "4", "9", "", ""],
        ["", "", "", "4", "9", "", ""],
    ]

    with pytest.raises(fax_extractor.CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        orphan_projection(fields, rows, payload_rows)


def test_extract_sheet_rows_from_payload_supports_escaped_newlines(tmp_path, monkeypatch):
    columns = [
        {"index": 0, "source_index": 0, "role": "date", "name": "日付"},
        {"index": 1, "source_index": 1, "role": "daypart", "name": "区分"},
        {"index": 2, "source_index": 2, "role": "menu", "name": "メニュー"},
    ]
    for index, (diet, area, name) in enumerate([
        ("regular", "2F", "常食2F"), ("regular", "3F", "常食3F"),
        ("soft", "2F", "軟菜2F"), ("soft", "3F", "軟菜3F"),
        ("mixer", "2F", "ミキサー2F"), ("mixer", "3F", "ミキサー3F"),
    ], start=3):
        columns.append({"index": index, "source_index": index, "role": "quantity",
                        "diet_type": diet, "area_id": area, "name": name})
    columns.append({"index": 9, "source_index": 9, "role": "remarks", "name": "備考"})
    source = tmp_path / "synthetic-escaped-component.json"
    source.write_text(json.dumps({"facilities": [{
        "facility_id": "FAC00001", "facility_name": "Synthetic escaped component",
        "fax_template_id": "synthetic-escaped-component",
        "fax_template": {"template_id": "synthetic-escaped-component", "columns": columns},
    }]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.delenv("FACILITY_MASTER_URI", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(source))
    facility_cfg = config_service.get_facility_config("FAC00001")
    template = facility_cfg["fax_template"]
    payload = {
        "table_raw": (
            "| 日付 | 区分 | メニュー | 常食2F | 常食3F | 軟菜2F | 軟菜3F | ミキサー2F | ミキサー3F | 備考 |\\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\\n"
            "| 02/15 | 朝 | 朝メニュー | 7 |  |  |  |  |  |  |"
        )
    }
    rows = order_service._extract_sheet_rows_from_payload(payload, template)
    assert len(rows) == 1
    assert rows[0][0] == "02/15"
    assert rows[0][1] == "朝"
    assert rows[0][2] == "朝メニュー"
    assert rows[0][3] == "7"


def test_parse_sheet_quantity_cell_rescues_common_single_glyph_noise():
    assert order_service._parse_sheet_quantity_cell("g") == 9
    assert order_service._parse_sheet_quantity_cell("4.") == 4
    assert order_service._parse_sheet_quantity_cell("2.1") == 21
    assert order_service._parse_sheet_quantity_cell("1.5") == 15
    assert order_service._parse_sheet_quantity_cell("A") is None


def test_sanitize_payload_table_raw_trims_non_table_tail():
    payload = {
        "table_raw": (
            "| 日付 | 区分 | 献立 | 常食 |\n"
            "| --- | --- | --- | --- |\n"
            "| 02/15 | 朝 | Menu A | 7 |\n"
            "\n"
            "自由領域の文章\n"
            "16\n"
        )
    }
    sanitized = order_service._sanitize_payload_table_raw(payload)
    assert sanitized.get("table_raw_truncated") is True
    assert sanitized["table_raw"].splitlines() == [
        "| 日付 | 区分 | 献立 | 常食 |",
        "| --- | --- | --- | --- |",
        "| 02/15 | 朝 | Menu A | 7 |",
    ]


def test_apply_payload_quantities_numeric_only_keeps_valid_large_counts():
    from tests.unit.test_modernization_ocr_canonical_projection import orphan_projection

    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.soft_x",
        "qty.regular_bag_x",
    ]
    rows = [
        {"values": ["04/05", "夕", "じゃが芋の煮物", "", ""]},
    ]
    payload_rows = [
        ["04/05", "夕", "じゃが芋の煮物", "58", "2"],
    ]

    projected, stats = orphan_projection(fields, rows, payload_rows)

    assert stats["exact"] == 1
    assert projected == [["04/05", "夕", "じゃが芋の煮物", "58", "2"]]


def test_parse_sheet_quantity_cell_rejects_embedded_text_tokens():
    assert order_service._parse_sheet_quantity_cell("23") == 23
    assert order_service._parse_sheet_quantity_cell("（23）") == 23
    assert order_service._parse_sheet_quantity_cell("99") == 99
    assert order_service._parse_sheet_quantity_cell("58") == 58
    assert order_service._parse_sheet_quantity_cell("3000") is None
    assert order_service._parse_sheet_quantity_cell("副23") is None
    assert order_service._parse_sheet_quantity_cell("No.23") is None


def test_sanitize_reparse_line_quantities_keeps_valid_large_counts(monkeypatch):
    monkeypatch.delenv("OCR_REPARSE_MAX_QTY", raising=False)
    monkeypatch.delenv("OCR_SHEET_MAX_QTY", raising=False)

    lines = [
        {"menu_name": "A", "quantity_corrected": 58, "quantity_original": 58},
        {"menu_name": "B", "quantity_corrected": 200, "quantity_original": 200},
        {"menu_name": "C", "quantity_corrected": 3000, "quantity_original": 3000},
    ]

    sanitized, stats = order_service._sanitize_reparse_line_quantities(lines)

    assert [line["quantity_corrected"] for line in sanitized] == [58.0, 200.0]
    assert [line["quantity_original"] for line in sanitized] == [58.0, 200.0]
    assert stats["max_abs_qty"] == 999
    assert stats["quantity_dropped"] == 2
    assert stats["lines_dropped"] == 1


def test_get_ocr_sheet_blocks_when_monthly_menu_object_is_missing(history_registration):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
    payload = IngestEmailPayload(
        message_id="msg-sheet-ocr-fallback-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    lines = [
        {
            "date": "2099-12-15",
            "daypart": "朝",
            "menu_name": "切干大根煮",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 7,
        }
    ]
    order = _register_scoped_order(history_registration, payload, lines=lines, week='2099-12@2099-12-15~2099-12-21')
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/15", "朝", "じゃが芋のコンソメ煮", "", "", "", "", "", "", ""],
                ["12/15", "朝", "切干大根煮", "7", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/15"],
        },
    )

    _assert_unselected_sheet_cannot_be_reconstructed(order["id"])


def test_current_sheet_context_canonicalizes_invalid_daypart_tokens_when_monthly_menu_is_missing(history_registration):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
    payload = IngestEmailPayload(
        message_id="msg-sheet-daypart-canonicalization-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = _register_scoped_order(history_registration, payload, lines=None, week='2099-12@2099-12-15~2099-12-21')
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/15", '"', "Menu A", "4", "", "", "", "", "", ""],
                ["12/15", "61", "Menu B", "5", "", "", "", "", "", ""],
                ["12/16", "&", "Menu C", "6", "", "", "", "", "", ""],
                ["12/16", "€", "Menu D", "7", "", "", "", "", "", ""],
                ["12/17", "品", "Menu E", "8", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/15", "12/16", "12/17"],
        },
    )

    _assert_unselected_sheet_cannot_be_reconstructed(order["id"])


def test_get_ocr_sheet_blocks_when_monthly_menu_missing_even_if_historical_dayparts_exist(history_registration):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))

    base_payload = IngestEmailPayload(
        message_id="msg-sheet-history-daypart-source-001",
        pdf_uri="file://historical.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-12",
    )
    historical_lines = [
        {
            "date": "2099-12-15",
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 1,
        },
        {
            "date": "2099-12-15",
            "daypart": "昼",
            "menu_name": "Menu B",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 2,
        },
        {
            "date": "2099-12-16",
            "daypart": "朝",
            "menu_name": "Menu C",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 3,
        },
        {
            "date": "2099-12-16",
            "daypart": "昼",
            "menu_name": "Menu D",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 4,
        },
        {
            "date": "2099-12-17",
            "daypart": "朝",
            "menu_name": "Menu E",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 5,
        },
    ]
    _register_scoped_order(history_registration, base_payload, lines=historical_lines, week='2099-12@2099-12-15~2099-12-21')

    payload = IngestEmailPayload(
        message_id="msg-sheet-history-daypart-source-002",
        pdf_uri="file://current.pdf",
        received_at=datetime(2099, 12, 15, 9, 10, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = _register_scoped_order(history_registration, payload, lines=None, week='2099-12@2099-12-15~2099-12-21')
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/15", '"', "Menu A", "1", "", "", "", "", "", ""],
                ["12/15", "61", "Menu B", "2", "", "", "", "", "", ""],
                ["12/16", "&", "Menu C", "3", "", "", "", "", "", ""],
                ["12/16", "€", "Menu D", "4", "", "", "", "", "", ""],
                ["12/17", "品", "Menu E", "5", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/15", "12/16", "12/17"],
        },
    )

    _assert_unselected_sheet_cannot_be_reconstructed(order["id"])


def test_build_recoverable_ocr_sheet_payload_stays_blocked_and_stable_when_menu_entries_missing(history_registration):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))

    payload = IngestEmailPayload(
        message_id="msg-sheet-history-stable-blocked-context-001",
        pdf_uri="file://stable-blocked.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = _register_scoped_order(history_registration, payload, lines=[], week='2099-12@2099-12-15~2099-12-21')
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/15", "朝", "Menu A", "4", "", "", "", "", "", ""],
                ["12/16", "昼", "Menu B", "5", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/15", "12/16"],
        },
    )

    _assert_unselected_sheet_cannot_be_reconstructed(order["id"])


def test_unowned_draft_diagnostics_cannot_create_a_current_sheet(history_registration):
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-sheet-history-saved-draft-menu-diagnostics-001",
        pdf_uri="file://stable-saved-diagnostics.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = _register_scoped_order(history_registration, payload, lines=[], week='2099-12@2099-12-15~2099-12-21')
    unowned = order_service.draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "source": "review_blocked",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [],
            "row_ids": [],
            "warnings": ["monthly_menu_object_missing"],
            "menu_diagnostics": {
                "month_id": "2099-12",
                "resolved_week_id": "2099-12",
                "order_codes": ["monthly_menu_object_missing"],
                "row_codes": [],
            },
        },
        draft_state="draft_ready",
        edited_by="test",
    )
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/15", "朝", "Menu A", "4", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/15"],
        },
    )

    assert unowned is None
    assert order_service.get_latest_sheet_draft(order["id"]) is None
    _assert_unselected_sheet_cannot_be_reconstructed(order["id"])


def test_get_ocr_sheet_blocks_when_monthly_menu_missing_even_if_historical_dayparts_match_by_date_order(history_registration):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))

    base_payload = IngestEmailPayload(
        message_id="msg-sheet-history-daypart-order-fallback-001",
        pdf_uri="file://historical.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-12",
    )
    historical_lines = [
        {
            "date": "2099-12-15",
            "daypart": "朝",
            "menu_name": "Canonical A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 1,
        },
        {
            "date": "2099-12-15",
            "daypart": "昼",
            "menu_name": "Canonical B",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 2,
        },
        {
            "date": "2099-12-16",
            "daypart": "朝",
            "menu_name": "Canonical C",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 3,
        },
        {
            "date": "2099-12-16",
            "daypart": "昼",
            "menu_name": "Canonical D",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 4,
        },
    ]
    _register_scoped_order(history_registration, base_payload, lines=historical_lines, week='2099-12@2099-12-15~2099-12-21')

    payload = IngestEmailPayload(
        message_id="msg-sheet-history-daypart-order-fallback-002",
        pdf_uri="file://current.pdf",
        received_at=datetime(2099, 12, 15, 9, 10, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = _register_scoped_order(history_registration, payload, lines=None, week='2099-12@2099-12-15~2099-12-21')
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/15", '"', "OCR A", "1", "", "", "", "", "", ""],
                ["12/15", "61", "OCR B", "2", "", "", "", "", "", ""],
                ["12/16", "&", "OCR C", "3", "", "", "", "", "", ""],
                ["12/16", "€", "OCR D", "4", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/15", "12/16"],
        },
    )

    _assert_unselected_sheet_cannot_be_reconstructed(order["id"])


def test_get_ocr_sheet_blocks_when_monthly_menu_missing_even_if_ocr_rows_can_be_week_scoped(history_registration):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))

    base_payload = IngestEmailPayload(
        message_id="msg-sheet-history-daypart-week-scope-001",
        pdf_uri="file://historical.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-12@2099-12-15~2099-12-17",
    )
    historical_lines = [
        {
            "date": "2099-12-15",
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 1,
        },
        {
            "date": "2099-12-15",
            "daypart": "昼",
            "menu_name": "Menu B",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 2,
        },
        {
            "date": "2099-12-16",
            "daypart": "朝",
            "menu_name": "Menu C",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 3,
        },
    ]
    _register_scoped_order(history_registration, base_payload, lines=historical_lines, week='2099-12@2099-12-15~2099-12-17')

    payload = IngestEmailPayload(
        message_id="msg-sheet-history-daypart-week-scope-002",
        pdf_uri="file://current.pdf",
        received_at=datetime(2099, 12, 15, 9, 10, 0),
        facility_hint="FAC00001",
        week_hint="2099-12@2099-12-15~2099-12-17",
    )
    order = _register_scoped_order(history_registration, payload, lines=None, week='2099-12@2099-12-15~2099-12-17')
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/12", "朝", "Offweek 1", "1", "", "", "", "", "", ""],
                ["12/12", "昼", "Offweek 2", "2", "", "", "", "", "", ""],
                ["12/15", "\"", "Menu A", "1", "", "", "", "", "", ""],
                ["12/15", "61", "Menu B", "2", "", "", "", "", "", ""],
                ["12/16", "&", "Menu C", "3", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/12", "12/15", "12/16"],
        },
    )

    _assert_unselected_sheet_cannot_be_reconstructed(order["id"])


def test_prefer_week_scoped_ocr_entries_falls_back_to_original_rows_when_clip_would_empty():
    entries = [
        {"menu_date": date(2099, 12, 12), "daypart_key": "朝", "menu_name": "Offweek 1"},
        {"menu_date": date(2099, 12, 12), "daypart_key": "昼", "menu_name": "Offweek 2"},
    ]

    scoped = order_service._prefer_week_scoped_ocr_entries(
        entries,
        "2099-12@2099-12-15~2099-12-17",
    )

    assert [item["menu_name"] for item in scoped] == ["Offweek 1", "Offweek 2"]


def test_get_ocr_sheet_blocks_when_monthly_menu_missing_even_if_ocr_rows_can_be_semantically_ordered(history_registration):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))

    base_payload = IngestEmailPayload(
        message_id="msg-sheet-history-daypart-week-order-001",
        pdf_uri="file://historical.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-12@2099-12-15~2099-12-17",
    )
    historical_lines = [
        {
            "date": "2099-12-15",
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 1,
        },
        {
            "date": "2099-12-15",
            "daypart": "昼",
            "menu_name": "Menu B",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 2,
        },
        {
            "date": "2099-12-16",
            "daypart": "朝",
            "menu_name": "Menu C",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 3,
        },
        {
            "date": "2099-12-17",
            "daypart": "夕",
            "menu_name": "Menu D",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 4,
        },
    ]
    _register_scoped_order(history_registration, base_payload, lines=historical_lines, week='2099-12@2099-12-15~2099-12-17')

    payload = IngestEmailPayload(
        message_id="msg-sheet-history-daypart-week-order-002",
        pdf_uri="file://current.pdf",
        received_at=datetime(2099, 12, 15, 9, 10, 0),
        facility_hint="FAC00001",
        week_hint="2099-12@2099-12-15~2099-12-17",
    )
    order = _register_scoped_order(history_registration, payload, lines=None, week='2099-12@2099-12-15~2099-12-17')
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/16", "&", "Menu C", "3", "", "", "", "", "", ""],
                ["12/15", "\"", "Menu A", "1", "", "", "", "", "", ""],
                ["12/17", "品", "Menu D", "4", "", "", "", "", "", ""],
                ["12/15", "61", "Menu B", "2", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/16", "12/15", "12/17"],
        },
    )

    _assert_unselected_sheet_cannot_be_reconstructed(order["id"])


def test_get_ocr_sheet_blocks_broken_structured_daypart_ocr_when_weekly_shell_is_missing(history_registration):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))

    payload = IngestEmailPayload(
        message_id="msg-sheet-daypart-structured-blocks-001",
        pdf_uri="file://structured.pdf",
        received_at=datetime(2099, 12, 15, 9, 10, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = _register_scoped_order(history_registration, payload, lines=None, week='2099-12@2099-12-15~2099-12-21')
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "rows": [
                        ["日付", "区分", "献立", "常食2F"],
                        ["", "", "", ""],
                        ["12/15", "朝", "Menu A", "1"],
                        ["", "", "Menu B", "1"],
                        ["", "&", "Menu C", "1"],
                        ["", "", "Menu D", "1"],
                        ["", "", "Menu E", "1"],
                        ["", "タ", "Menu F", "1"],
                        ["", "", "Menu G", "1"],
                        ["", "", "Menu H", "1"],
                    ],
                    "cells": [
                        {"row_index": 2, "col_index": 1, "row_span": 2, "col_span": 1, "text": "朝"},
                        {"row_index": 4, "col_index": 1, "row_span": 3, "col_span": 1, "text": "&"},
                        {"row_index": 7, "col_index": 1, "row_span": 3, "col_span": 1, "text": "タ"},
                    ],
                }
            ],
            "table_rows": [
                ["12/15", "朝", "Menu A", "1"],
                ["", "", "Menu B", "1"],
                ["", "&", "Menu C", "1"],
                ["", "", "Menu D", "1"],
                ["", "", "Menu E", "1"],
                ["", "タ", "Menu F", "1"],
                ["", "", "Menu G", "1"],
                ["", "", "Menu H", "1"],
            ],
            "date_strings": ["12/15"],
        },
    )

    _assert_unselected_sheet_cannot_be_reconstructed(order["id"])


def test_current_sheet_context_preserves_operator_daypart_tokens_from_formally_saved_draft(selected_history_order):
    order_service.clear_all()
    _seed_daypart_source_menu()
    payload = IngestEmailPayload(
        message_id="msg-sheet-daypart-clean-draft-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = _register_scoped_order(selected_history_order, payload, lines=None,
        week='2099-12@2099-12-15~2099-12-21', ocr_rows=DAYPART_SOURCE_ROWS)
    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and sheet is not None
    edited = deepcopy(sheet)
    daypart_index, menu_index = edited["fields"].index("daypart"), edited["fields"].index("menu")
    tokens = dict(zip(["Menu A", "Menu B", "Menu C", "Menu D", "Menu E"], ['"', "61", "&", "€", "品"], strict=True))
    for row in edited["rows"]:
        row[daypart_index] = tokens[row[menu_index]]
    saved, error = _save_formal_sheet(order_id=order["id"], sheet=edited, edited_by="test")
    assert error is None and saved is not None

    context = order_service.get_current_sheet_context(
        order["id"],
        refresh_draft_from_semantic=True,
        upgrade_generic_from_sheet=True,
        backfill_from_revision=False,
    )

    assert isinstance(context, dict)
    assert context["draft_id"] is not None
    daypart_idx = context["fields"].index("daypart")
    menu_idx = context["fields"].index("menu")
    rows_by_menu = {row[menu_idx]: row for row in context["rows"] if menu_idx < len(row)}
    assert [rows_by_menu[name][daypart_idx] for name in ["Menu A", "Menu B", "Menu C", "Menu D", "Menu E"]] == [
        '"', "61", "&", "€", "品",
    ]
    quantity_index = context["fields"].index("qty.regular_2f")
    assert [rows_by_menu[name][quantity_index] for name in tokens] == ["4", "5", "6", "7", "8"]


def test_get_ocr_sheet_saved_draft_path_does_not_surface_invalid_daypart_tokens(selected_history_order):
    order_service.clear_all()
    _seed_daypart_source_menu()
    payload = IngestEmailPayload(
        message_id="msg-sheet-invalid-daypart-saved-draft-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = _register_scoped_order(selected_history_order, payload, lines=None,
        week='2099-12@2099-12-15~2099-12-21', ocr_rows=DAYPART_SOURCE_ROWS)
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "draft_sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [
                ["12/15", '"', "Menu A", "4"],
                ["12/15", "61", "Menu B", "5"],
                ["12/16", "&", "Menu C", "6"],
                ["12/16", "€", "Menu D", "7"],
                ["12/17", "品", "Menu E", "8"],
            ],
            "row_ids": [f"draft-{idx + 1}" for idx in range(5)],
        },
        blockers=[],
        warnings=[],
        edited_by="test",
     synchronous_write_input=order_service._capture_synchronous_cache_write_input(order['id']))

    assert saved is not None

    sheet, error = order_service.get_ocr_sheet(order["id"])
    current_context = order_service.get_current_sheet_context(order["id"])

    assert error is None
    assert sheet is not None
    assert isinstance(current_context, dict)
    assert sheet["source"] == current_context["source"]
    daypart_idx = sheet["fields"].index("daypart")
    assert all(
        daypart_idx < len(row) and row[daypart_idx] in {"朝", "昼", "夕"}
        for row in sheet["rows"]
    )


def test_selected_draft_does_not_rebase_from_unselected_wrong_dayparts_or_menu(selected_history_order, monkeypatch):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-weekly-shell-rebase-clean-draft-001")

    selected_before = order_service.get_latest_sheet_draft(order["id"])
    assert selected_before is not None
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "draft_sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [
                ["01/08", "朝", "Menu A", "7", ""],
                ["01/08", "夕", "Menu B", "8", ""],
                ["01/08", "夕", "Menu C WRONG", "9", ""],
            ],
            "row_ids": ["draft-1", "draft-2", "draft-3"],
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
        edited_by="test",
     synchronous_write_input=order_service._capture_synchronous_cache_write_input(order['id']))
    assert saved is not None

    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft",
        lambda order_id, use_saved_draft=False, evidence_run_override=None: {
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [
                ["01/08", "朝", "Menu A", "1", ""],
                ["01/08", "昼", "Menu B", "2", ""],
                ["01/08", "夕", "Menu C", "3", ""],
            ],
            "row_ids": ["fresh-1", "fresh-2", "fresh-3"],
            "warnings": [],
        },
    )

    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    before = _snapshot(get_engine())
    latest = order_service.get_latest_sheet_draft(order["id"],
        backfill_from_revision=False, upgrade_generic_from_sheet=False)
    assert latest == selected_before
    assert latest["id"] != saved["id"]
    assert _snapshot(get_engine()) == before




def test_unowned_reparse_candidate_is_refused_without_losing_daypart_sanitizer_coverage(history_registration):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2026-04"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2026-04"))
    payload = IngestEmailPayload(
        message_id="msg-sheet-invalid-daypart-reparse-candidate-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 4, 5, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = _register_scoped_order(history_registration, payload, lines=[], week='2026-04@2026-04-05~2026-04-11')

    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    rows = [
        ["04/05", '"', "Menu A", "4"],
        ["04/05", "61", "Menu B", "5"],
        ["04/06", "&", "Menu C", "6"],
        ["04/06", "€", "Menu D", "7"],
        ["04/07", "品", "Menu E", "8"],
    ]
    before = _snapshot(get_engine())
    with pytest.raises(ValueError, match="^reparse_pipeline_result_missing$"):
        order_service._save_reparse_candidate_as_draft(order_id=order["id"],
            template={"main_ocr_row_fields": fields}, rows=rows, before_digest="before",
            review_state="auto_apply_blocked", review_blockers=["quantity_review_required"],
            review_warnings=[])
    assert _snapshot(get_engine()) == before
    sanitized = order_service._sanitize_semantic_sheet_rows(rows_payload=rows, fields=fields)
    assert [row[1] for row in sanitized] == ['"', "61", "&", "€", "品"]
    assert [row[3] for row in sanitized] == ["4", "5", "6", "7", "8"]
    assert [row[2] for row in sanitized] == ["Menu A", "Menu B", "Menu C", "Menu D", "Menu E"]


def test_get_current_sheet_context_ignores_reparse_candidate_draft_for_current_surface(selected_history_order):
    order_service.clear_all()
    _seed_monthly_menu_custom_entries(month_id="2099-12", month_start=date(2099, 12, 1),
        entries=[(date(2099, 12, 15), "朝", "Menu A", 0),
                 (date(2099, 12, 15), "昼", "Menu B", 0)])
    payload = IngestEmailPayload(
        message_id="msg-sheet-current-context-ignores-reparse-candidate-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = _register_scoped_order(selected_history_order, payload, lines=[], week='2099-12@2099-12-15~2099-12-21',
        ocr_rows=[["12/15", "朝", "Menu A", "4", "", "", "", "", "", ""],
                  ["12/15", "昼", "Menu B", "5", "", "", "", "", "", ""]])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/15", "朝", "Menu A", "4", "", "", "", "", "", ""],
                ["12/15", "昼", "Menu B", "5", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/15"],
        },
    )
    baseline, baseline_error = order_service.get_ocr_sheet(order["id"])
    assert baseline_error is None and baseline is not None
    captured = order_service._capture_synchronous_cache_write_input(order["id"])
    assert captured is not None
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "source": "reparse_candidate",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["12/15", "夕", "Wrong Menu", "9"]],
            "row_ids": ["draft-1"],
        },
        draft_state="auto_apply_blocked",
        blockers=["sheet_llm_audit_failed"],
        warnings=[],
        edited_by="reparse-reject-candidate",
     synchronous_write_input=order_service._capture_synchronous_cache_write_input(order['id']))
    assert saved is not None

    context = order_service.get_current_sheet_context(
        order["id"],
        refresh_draft_from_semantic=True,
        upgrade_generic_from_sheet=True,
        backfill_from_revision=False,
    )

    assert isinstance(context, dict)
    assert context["draft_id"] == captured["draft_id"]
    assert context["draft_id"] != saved["id"]
    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    for key in ("fields", "header", "rows", "row_ids", "base_evidence_run_id"):
        assert sheet[key] == baseline[key]
    assert "Wrong Menu" not in [cell for row in sheet["rows"] for cell in row]


def test_get_ocr_sheet_does_not_rehydrate_revision_into_current_surface(history_registration, monkeypatch):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
    payload = IngestEmailPayload(
        message_id="msg-sheet-ignore-revision-current-surface-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = _register_scoped_order(history_registration, payload, lines=[], week='2099-12@2099-12-15~2099-12-21')
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/15", "朝", "Menu A", "4", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/15"],
        },
    )

    calls = {"count": 0}

    def _unexpected_revision(**_kwargs):
        calls["count"] += 1
        return {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["01/01", "朝", "Revision Menu", "9"]],
            "row_ids": ["rev-1"],
            "revision_id": "REV-1",
        }

    monkeypatch.setattr(order_service, "_select_order_sheet_revision", _unexpected_revision)

    _assert_unselected_sheet_cannot_be_reconstructed(order["id"])
    assert calls["count"] == 0


def test_apply_ocr_table_saves_revision_and_output_uses_edited(selected_history_order):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-sheet-002")

    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "qty.regular_3f",
        "qty.soft_2f",
        "qty.soft_3f",
        "qty.mixer_2f",
        "qty.mixer_3f",
        "remarks",
    ]
    header = ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"]
    rows = [["01/08", "昼", "Menu A", "3", "1", "", "", "", "", "manual"]]
    row_ids = ["row-1"]

    updated, error = order_service.apply_ocr_table(
        order["id"],
        header=header,
        rows=rows,
        ui_mode="sheet",
        fields=fields,
        row_ids=row_ids,
    )
    assert error is None
    assert updated is not None
    assert updated["reparse"]["provider"] == "structured_rows"

    history, error = order_service.get_ocr_edit_history(order["id"])
    assert error is None
    assert history is not None
    assert isinstance(history.get("revisions"), list)
    assert len(history["revisions"]) >= 1
    latest = history.get("latest")
    assert isinstance(latest, dict)
    assert latest.get("ui_mode") == "sheet"
    assert latest.get("row_count") == 1
    assert latest.get("row_ids") == row_ids

    output, error = order_service.get_ocr_output(order["id"])
    assert error is None
    assert output is not None
    assert output.get("ocr_source") != "edited"
    assert output.get("edited_table") is None
    assert order_service._load_order_ocr_cache(order["id"])["_edited_ocr"]["latest"]["row_ids"] == row_ids
    saved, error = _save_formal_sheet(order_id=order["id"],
        sheet={"fields": fields, "header": header, "rows": rows, "row_ids": row_ids},
        edited_by="authored-component-operator")
    assert error is None and saved is not None
    visible, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and visible["rows"] == rows


def test_formal_save_preserves_explicit_period_without_using_legacy_materialization_guesses(selected_history_order, monkeypatch):
    order_service.clear_all()
    _seed_monthly_menu_custom_entries(month_id="2026-05", month_start=date(2026, 5, 1),
        entries=[(date(2026, 5, 1), "昼", "Menu A", 0)])
    order = _seed_custom_order(selected_history_order,
        message_id="msg-sheet-explicit-week-preserve-001", received_at=datetime(2026, 5, 1, 9),
        week="2026-05@2026-05-01~2026-05-02", effective_date="2026-05-01", lines=[],
        ocr_rows=[["05/01", "昼", "Menu A", "2", "", "", "", "", "", ""]])
    original_week = _read_order(order["id"])["persisted_week_value"]
    def forbidden(*args, **kwargs):
        pytest.fail("Explicit May source must not invoke a legacy derived January week")
    monkeypatch.setattr(order_service, "_build_materialization_candidate_from_draft_record", forbidden)
    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and sheet is not None
    fields = sheet["fields"]
    row = next(row for row in sheet["rows"] if row[fields.index("menu")] == "Menu A")
    row[fields.index("qty.regular_2f")] = "3"
    row[fields.index("remarks")] = "manual"
    saved, error = _save_formal_sheet(order_id=order["id"], sheet=sheet,
        edited_by="authored-component-operator")
    assert error is None and saved is not None
    assert _read_order(order["id"])["persisted_week_value"] == original_week
    visible, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and visible["rows"] == sheet["rows"]
    assert all(row[fields.index("date_mmdd")] == "05/01" for row in visible["rows"])


def test_rows_from_markdown_maps_kanji_regular_column(repository_layout_source):
    master = config_service.load_facility_master()
    base_template = master.get("fax_template_base", {})
    facility = next(
        fac for fac in master.get("facilities", []) if fac.get("facility_id") == "FAC00001"
    )
    template = facility.get("fax_template") or config_service._merge_template(
        base_template,
        facility.get("fax_template_override"),
    )

    markdown = """
|日 付|区 分||献立|常<br>☆||薬食||変更(1)|変更(2)|備考欄|
|-|-|-|-|-|-|-|-|-|-|-|
|2/15|朝|WD|豚肉とれんこんの炒め煮|33|||||||
""".strip()
    rows = rows_from_markdown(markdown, template)
    assert isinstance(rows, list)
    assert len(rows) == 1
    first = rows[0]
    # qty.regular_2f column
    assert first[3] == "33"


def test_rows_from_markdown_maps_hana_getsu_subheader_and_escaped_pipe():
    # This twelve-column component specimen is not the current FAC00003 layout.
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "qty.regular_3f",
              "qty.soft_2f", "qty.soft_3f", "qty.mixer_2f", "qty.mixer_3f",
              "qty.no_fish_regular_x", "remarks"]
    template = {
        "main_ocr_row_fields": fields,
        "columns_authoritative": True,
        "columns": [
            {"role": "date", "source_index": 0},
            {"role": "daypart", "source_index": 2},
            {"role": "menu_name", "source_index": 3},
            *[{"role": "quantity", "name": field, "source_index": index,
               "diet_type": field[4:].rsplit("_", 1)[0], "area_id": field.rsplit("_", 1)[1].upper()}
              for index, field in enumerate(fields[3:10], 4)],
            {"role": "note", "source_index": 11},
        ],
    }

    markdown = """
|日 付||区 分|献立|常食||軟菜||ミキサー||魚焼\\(常食\\)|備考欄|
|-|-|-|-|-|-|-|-|-|-|-|-|
|||||花|月|花|月|花|月|||
|2/15||朝|じゃが芋のコンソメ煮|8|6|||2|3|||
|||夕|\\|鶏肉のケチャップ炒め|8|7|||2|3|||
""".strip()
    rows = rows_from_markdown(markdown, template)
    assert isinstance(rows, list)
    assert len(rows) == 3
    assert rows[0] == ["", "", "", "花", "月", "花", "月", "花", "月", "", ""]

    first = rows[1]
    assert first[2] == "じゃが芋のコンソメ煮"
    assert first[3] == "8"
    assert first[4] == "6"
    assert first[7] == "2"
    assert first[8] == "3"

    second = rows[2]
    assert second[2] == "|鶏肉のケチャップ炒め"
    assert second[3] == "8"
    assert second[4] == "7"
    assert second[7] == "2"
    assert second[8] == "3"


def test_merge_header_rows_keeps_forbidden_split_markers_unprefixed():
    merged = fax_extractor._merge_header_rows(
        ["日付", "区分", "献立", "常食", "", "ミキサー", "", "", "", "備考欄"],
        ["", "", "", "2F", "3F", "2F", "3F", "肉禁", "魚禁", ""],
    )

    assert merged[7] == "肉禁"
    assert merged[8] == "魚禁"


def test_merge_header_rows_avoids_duplicate_forbidden_parent_and_change_prefix():
    merged = fax_extractor._merge_header_rows(
        ["日付", "区分", "メニュー", "常食", "禁食", "", "変更1", "", "備考欄"],
        ["", "", "", "", "禁食", "", "", "変更2", ""],
    )

    assert merged[4] == "禁食"
    assert merged[7] == "変更2"


def test_rows_from_markdown_maps_split_forbidden_subheaders_without_parent_group():
    template = {
        "columns": [
            {"role": "quantity", "name": "qty.regular_2f", "source_index": 3},
            {"role": "quantity", "name": "qty.regular_3f", "source_index": 4},
            {"role": "quantity", "name": "qty.mixer_2f", "source_index": 5},
            {"role": "quantity", "name": "qty.mixer_3f", "source_index": 6},
            {"role": "quantity", "name": "qty.no_meat_x", "source_index": 7},
            {"role": "quantity", "name": "qty.no_fish_x", "source_index": 8},
        ],
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
            "qty.regular_3f",
            "qty.mixer_2f",
            "qty.mixer_3f",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "remarks",
        ]
    }
    markdown = """
|日付|区分|献立|常食||ミキサー||||備考欄|
|-|-|-|-|-|-|-|-|-|-|
||||2F|3F|2F|3F|肉禁|魚禁||
|2/15|朝|Menu A|8|6|1|2|3|4||
""".strip()

    rows = rows_from_markdown(markdown, template)

    assert rows == [
        ["", "", "", "2F", "3F", "2F", "3F", "肉禁", "魚禁", ""],
        ["2/15", "朝", "Menu A", "8", "6", "1", "2", "3", "4", ""],
    ]


def test_parse_order_lines_does_not_fill_forward_blank_menu_name_in_large_cell_mode():
    template = {
        "header_rows": 0,
        "large_cell_mode": True,
        "fill_forward_roles": ["date", "daypart", "menu_name"],
        "fill_missing_date_with_first_seen": True,
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "soft", "area_id": "X"},
        ],
    }
    rows = [
        ["4/8(水)", "朝", "アジの南蛮漬 さつま芋の煮物", "1"],
        ["", "", "", "1"],
        ["", "", "小松菜のおかか和え", "1"],
    ]

    lines = fax_parser.parse_order_lines(
        rows,
        template,
        datetime(2026, 4, 8, 9, 0, 0),
        quantity_rules={"zero_as_empty": False},
        explicit_week="2026-04@2026-04-08~2026-04-08",
    )

    menu_names = [line.get("menu_name") for line in lines]
    assert menu_names.count("アジの南蛮漬 さつま芋の煮物") == 1
    assert menu_names.count("小松菜のおかか和え") == 1
    assert None in menu_names


def test_ocr_menu_entries_require_explicit_week_before_reading_blank_continuations(monkeypatch):
    template = {
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.soft_x", "qty.regular_bag_x"],
        "large_cell_mode": True,
        "fill_forward_roles": ["date", "daypart", "menu_name"],
        "fill_missing_date_with_first_seen": True,
    }
    rows = [
        ["4/8(水)", "朝", "アジの南蛮漬 さつま芋の煮物", "0", "0"],
        ["", "", "", "0", "0"],
        ["", "", "小松菜のおかか和え", "0", "0"],
    ]

    monkeypatch.setattr(
        order_service,
        "_extract_sheet_rows_from_payload_uncanonicalized",
        lambda payload, template: pytest.fail("Missing period must be rejected before OCR row extraction"),
    )

    original_rows, original_template = deepcopy(rows), deepcopy(template)
    with pytest.raises(ValueError, match="^week_hint_required$"):
        order_service._build_position_menu_entries_from_ocr_payload(
            payload={"tables": []}, template=template,
            received_at=datetime(2026, 4, 8, 9, 0, 0),
        )
    assert rows == original_rows and template == original_template


def test_get_ocr_sheet_rejects_broken_ocr_shell_when_no_weekly_source_exists(history_registration, monkeypatch):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-04"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-04"))
    payload = IngestEmailPayload(
        message_id="msg-reconstructed-order-001",
        pdf_uri="file://current.pdf",
        received_at=datetime(2099, 4, 8, 9, 0, 0),
        facility_hint="FAC00005",
        week_hint=None,
    )
    order = _register_scoped_order(history_registration, payload, lines=None, week='2099-04@2099-04-08~2099-04-14')

    ocr_payload = {
        "table_rows": [
            ["4/8(水)", "朝", "けんちん煮", "0", "0", "", "", "", "", ""],
            ["", "", "白菜と平天のお浸し", "0", "0", "", "", "", "", ""],
            ["", "", "アジの南蛮漬 さつま芋の煮物", "0", "0", "", "", "", "", ""],
            ["", "", "", "0", "0", "", "", "", "", ""],
            ["", "", "小松菜のおかか和え", "0", "0", "", "", "", "", ""],
            ["", "昼", "鶏肉の治部煮", "58", "2", "", "", "", "", ""],
            ["", "", "ポテトソテー", "58", "2", "", "", "", "", ""],
            ["", "夕", "ならあえ", "58", "2", "", "", "", "", ""],
        ],
        "date_strings": ["4/8"],
    }
    order_service._save_order_ocr_cache(order["id"], ocr_payload)

    _assert_unselected_sheet_cannot_be_reconstructed(order["id"])


def test_rows_from_markdown_maps_three_row_forbidden_split_headers_and_change2():
    template = {
        "columns": [
            {"role": "quantity", "name": "qty.regular_x", "source_index": 3},
            {"role": "quantity", "name": "qty.no_meat_x", "source_index": 5},
            {"role": "quantity", "name": "qty.no_fish_x", "source_index": 6},
            {"role": "quantity", "name": "qty.change_1_x", "source_index": 7},
            {"role": "quantity", "name": "qty.change_2_x", "source_index": 8},
        ],
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.change_1_x",
            "qty.change_2_x",
            "remarks",
        ]
    }
    markdown = """
|日付|区分|メニュー|常食|禁食|||変更1||備考欄|
|-|-|-|-|-|-|-|-|-|-|
|||||禁食||||変更2||
|||||肉禁|魚禁||||
|2/15|朝|Menu A|20||4|5|6|7||
""".strip()

    rows = rows_from_markdown(markdown, template)

    assert rows == [
        ["", "", "", "", "", "", "", "変更2", ""],
        ["", "", "", "", "魚禁", "", "", "", ""],
        ["2/15", "朝", "Menu A", "20", "4", "5", "6", "7", ""],
    ]


def test_rows_from_markdown_uses_declared_columns_without_sparse_header_inference():
    template = {"columns_authoritative": True, "columns": [
        {"role": "date", "source_index": 0, "name": "date_mmdd"},
        {"role": "daypart", "source_index": 1, "name": "daypart"},
        {"role": "menu_name", "source_index": 3, "name": "menu"},
        {"role": "quantity", "source_index": 4, "name": "qty.regular_x", "diet_type": "regular", "area_id": "X"},
        {"role": "quantity", "source_index": 5, "name": "qty.diabetes_x", "diet_type": "diabetes", "area_id": "X"},
        {"role": "note", "source_index": 8, "name": "remarks"},
    ], "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.diabetes_x", "remarks"]}

    markdown = """
||||||変更の|変更(2)|備考|
|-|-|-|-|-|-|-|-|-|
||区分||献立|常危|糖尿||||
|2/15|朝||じゃが芋のコンソメ煮|20|4||||
|2/16|昼||キャベツサラダ|18|3||||
""".strip()

    rows = rows_from_markdown(markdown, template)
    assert isinstance(rows, list)
    assert len(rows) == 3
    assert rows[0] == ["", "区分", "献立", "常危", "糖尿", ""]

    first = rows[1]
    assert first[0] == "2/15"
    assert first[1] == "朝"
    assert first[2] == "じゃが芋のコンソメ煮"
    assert first[3] == "20"
    assert first[4] == "4"

    second = rows[2]
    assert second[0] == "2/16"
    assert second[1] == "昼"
    assert second[2] == "キャベツサラダ"
    assert second[3] == "18"
    assert second[4] == "3"


def test_count_source_row_alignment_penalty_cells_counts_shifted_rows():
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_x",
        "remarks",
    ]
    template = {"columns": [{"index": 3, "source_index": 3, "role": "quantity",
                             "diet_type": "regular", "area_id": "X"}]}
    quantity_index = order_service._build_sheet_quantity_index(fields, template=template)
    base_rows = [
        {
            "row_id": "r0",
            "values": ["01/08", "朝", "Menu A", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 1, 8), "朝", "Menu A"),
        },
        {
            "row_id": "r1",
            "values": ["01/09", "朝", "Menu B", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 1, 9), "朝", "Menu B"),
        },
    ]
    rows_by_source_index = [
        {"row_id": "r0", "values": ["01/08", "朝", "Menu A", "6", ""]},
        {"row_id": "r1", "values": ["01/09", "朝", "Menu B", "5", ""]},
    ]
    order_lines = [
        {
            "date": date(2026, 1, 8),
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "X",
            "quantity_original": 5,
            "quantity_corrected": None,
            "source_row_index": 1,
        },
        {
            "date": date(2026, 1, 9),
            "daypart": "朝",
            "menu_name": "Menu B",
            "diet_type": "regular",
            "area_id": "X",
            "quantity_original": 6,
            "quantity_corrected": None,
            "source_row_index": 0,
        },
    ]

    penalty = order_service._count_source_row_alignment_penalty_cells(
        base_rows=base_rows,
        rows_by_source_index=rows_by_source_index,
        fields=fields,
        quantity_index=quantity_index,
        order_lines=order_lines,
    )

    assert penalty == 2


def test_apply_order_line_quantities_uses_explicit_unscoped_area_column():
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_x",
        "qty.soft_x",
        "qty.mixer_x",
        "remarks",
    ]
    template = {"columns": [
        {"index": index, "role": "quantity", "diet_type": diet, "area_id": None}
        for index, diet in enumerate(("regular", "soft", "mixer"), 3)
    ]}
    quantity_index = order_service._build_sheet_quantity_index(fields, template=template)
    rows = [
        {
            "row_id": "r0",
            "values": ["02/16", "朝", "ごった煮", "", "", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 2, 16), "朝", "ごった煮"),
        }
    ]
    order_lines = [
        {
            "date": date(2026, 2, 16),
            "daypart": "朝",
            "menu_name": "ごった煮",
            "diet_type": "regular",
            "area_id": None,
            "quantity_original": 20,
            "quantity_corrected": None,
            "change_note": None,
        }
    ]

    order_service._apply_order_line_quantities_to_sheet_rows(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        order_lines=order_lines,
    )
    assert rows[0]["values"][3] == "20"


def test_apply_order_line_quantities_does_not_fallback_date_menu_when_daypart_field_exists():
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "remarks",
    ]
    template = {"columns": [{"index": 3, "source_index": 3, "role": "quantity",
                             "diet_type": "regular", "area_id": "2F"}]}
    quantity_index = order_service._build_sheet_quantity_index(fields, template=template)
    rows = [
        {
            "row_id": "r0",
            "values": ["02/16", "朝", "ごった煮", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 2, 16), "朝", "ごった煮"),
        }
    ]
    order_lines = [
        {
            "date": date(2026, 2, 16),
            "daypart": "昼",
            "menu_name": "ごった煮",
            "diet_type": "regular",
            "area_id": "2F",
            "quantity_original": 20,
            "quantity_corrected": None,
            "change_note": None,
        }
    ]

    order_service._apply_order_line_quantities_to_sheet_rows(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        order_lines=order_lines,
    )
    assert rows[0]["values"][3] == ""


def test_apply_order_line_quantities_fallbacks_date_menu_when_daypart_field_absent():
    fields = [
        "date_mmdd",
        "menu",
        "qty.regular_2f",
        "remarks",
    ]
    template = {"columns": [{"index": 2, "role": "quantity", "diet_type": "regular", "area_id": "2F"}]}
    quantity_index = order_service._build_sheet_quantity_index(fields, template=template)
    rows = [
        {
            "row_id": "r0",
            "values": ["02/16", "ごった煮", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 2, 16), "", "ごった煮"),
        }
    ]
    order_lines = [
        {
            "date": date(2026, 2, 16),
            "daypart": "昼",
            "menu_name": "ごった煮",
            "diet_type": "regular",
            "area_id": "2F",
            "quantity_original": 20,
            "quantity_corrected": None,
            "change_note": None,
        }
    ]

    order_service._apply_order_line_quantities_to_sheet_rows(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        order_lines=order_lines,
    )
    assert rows[0]["values"][2] == "20"


def test_apply_order_line_quantities_preserves_zero_value():
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "remarks",
    ]
    template = {"columns": [{"index": 3, "source_index": 3, "role": "quantity",
                             "diet_type": "regular", "area_id": "2F"}]}
    quantity_index = order_service._build_sheet_quantity_index(fields, template=template)
    rows = [
        {
            "row_id": "r0",
            "values": ["02/16", "朝", "ごった煮", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 2, 16), "朝", "ごった煮"),
        }
    ]
    order_lines = [
        {
            "date": date(2026, 2, 16),
            "daypart": "朝",
            "menu_name": "ごった煮",
            "diet_type": "regular",
            "area_id": "2F",
            "quantity_original": 0,
            "quantity_corrected": None,
            "change_note": None,
        }
    ]

    order_service._apply_order_line_quantities_to_sheet_rows(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        order_lines=order_lines,
    )
    assert rows[0]["values"][3] == "0"


def test_collect_missing_weekly_menu_dates_detects_intermediate_gap():
    entries = [
        {"menu_date": date(2099, 12, 26)},
        {"menu_date": date(2099, 12, 27)},
        {"menu_date": date(2099, 12, 28)},
    ]
    rows = [
        {"identity": ("2099-12-26", "朝", "Menu A")},
        {"identity": ("2099-12-28", "朝", "Menu C")},
    ]
    line_dates = {date(2099, 12, 26), date(2099, 12, 28)}
    missing = order_service._collect_missing_weekly_menu_dates(
        entries=entries,
        rows=rows,
        line_dates=line_dates,
    )
    assert missing == [date(2099, 12, 27)]


@pytest.mark.parametrize("area,quantity", [("9F", 5), (None, 8)])
def test_selected_blank_source_does_not_invent_columns_for_unmapped_stored_areas(selected_history_order, area, quantity):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(message_id=f"unmapped-stored-area-{area}", pdf_uri="component:original",
        received_at=datetime(2099, 11, 15, 9), facility_hint="FAC00001", week_hint="2099-11")
    order = _register_scoped_order(selected_history_order, payload,
        lines=[{"date": "2099-11-15", "daypart": "朝", "menu_name": "朝メニュー",
                "diet_type": "regular", "area_id": area, "bag_type": "standard",
                "quantity_original": quantity}],
        week="2099-11@2099-11-15~2099-11-21",
        ocr_rows=[["11/15", "朝", "朝メニュー", "", "", "", "", "", "", ""]])
    before = _snapshot(get_engine())
    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and sheet is not None, error
    fields = sheet["fields"]
    quantities = [field for field in fields if field.startswith("qty.")]
    assert quantities == ["qty.regular_2f", "qty.regular_3f", "qty.soft_2f",
                          "qty.soft_3f", "qty.mixer_2f", "qty.mixer_3f"]
    breakfast = next(row for row in sheet["rows"]
        if row[fields.index("date_mmdd")] == "11/15"
        and row[fields.index("daypart")] == "朝"
        and row[fields.index("menu")] == "朝メニュー")
    assert all(breakfast[fields.index(field)] == "" for field in quantities)
    assert sheet["base_evidence_run_id"]
    retained = _read_order(order["id"])["lines"]
    assert len(retained) == 1
    assert retained[0]["area_id"] == area and retained[0]["quantity_original"] == quantity
    assert _snapshot(get_engine()) == before


def test_selected_quantity_nine_is_not_replaced_by_unmapped_stored_twelve(selected_history_order):
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(message_id="selected-nine-stored-twelve", pdf_uri="component:original",
        received_at=datetime(2099, 11, 15, 9), facility_hint="FAC00001", week_hint="2099-11")
    order = _register_scoped_order(selected_history_order, payload,
        lines=[{"date": "2099-11-15", "daypart": "朝", "menu_name": "朝メニュー",
                "diet_type": "regular", "area_id": None, "bag_type": "standard",
                "quantity_original": 12}],
        week="2099-11@2099-11-15~2099-11-21",
        ocr_rows=[["11/15", "朝", "朝メニュー", "9", "", "", "", "", "", ""]])
    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and sheet is not None, error
    fields = sheet["fields"]
    breakfast = next(row for row in sheet["rows"]
        if row[fields.index("menu")] == "朝メニュー")
    assert breakfast[fields.index("qty.regular_2f")] == "9"
    assert "qty.regular_x" not in fields
    assert "sheet_order_lines_unmapped_fallback_payload" not in sheet.get("warnings", [])
    retained = _read_order(order["id"])["lines"]
    assert len(retained) == 1
    assert retained[0]["area_id"] is None and retained[0]["quantity_original"] == 12


def test_selected_authored_family_does_not_borrow_an_unmapped_stored_quantity(owned_registration, monkeypatch, tmp_path):
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-unmapped-stale-family-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="AUTHORED-FAMILY-X",
        week_hint="2099-11",
    )
    lines = [
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "regular",
            "area_id": "X",
            "bag_type": "standard",
            "quantity_original": 20,
        },
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "staff",
            "area_id": "X",
            "bag_type": "standard",
            "quantity_original": 4,
        },
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "business",
            "area_id": "X",
            "bag_type": "standard",
            "quantity_original": 5,
        },
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "no_meat",
            "area_id": "X",
            "bag_type": "standard",
            "quantity_original": 6,
        },
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "no_fish",
            "area_id": "X",
            "bag_type": "standard",
            "quantity_original": 7,
        },
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "sesame_allergy",
            "area_id": "X",
            "bag_type": "standard",
            "quantity_original": 8,
        },
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "change_1",
            "area_id": "X",
            "bag_type": "standard",
            "quantity_original": 9,
        },
    ]
    from tests.helpers.native_selection import select_authored_native_result
    diets = ["regular", "staff", "no_meat", "no_fish", "sesame_allergy", "change_1"]
    fields = ["date_mmdd", "daypart", "menu", *[f"qty.{diet}_x" for diet in diets], "remarks"]
    columns = [{"index": index, "source_index": index, "name": field,
                "header": field, "role": role}
               for index, (field, role) in enumerate(zip(fields,
                   ["date", "daypart", "menu_name", *["quantity"] * 6, "note"], strict=True))]
    for column, diet in zip(columns[3:9], diets, strict=True):
        column.update(diet_type=diet, area_id="X")
    facility = {"facility_id": "AUTHORED-FAMILY-X", "facility_name": "Authored six-diet component",
        "areas": [], "fax_template_id": "authored-six-diet",
        "fax_template": {"template_id": "authored-six-diet", "page": 1, "columns": columns}}
    order = owned_registration(message_id=payload.message_id, facility_id=facility["facility_id"],
        received_at=payload.received_at, week="2099-11@2099-11-15~2099-11-21",
        effective_date="2099-11-15", lines=lines, facility_config=facility)
    current, error = order_workflow_v2_service.get_workflow_readonly(order["id"])
    assert error is None
    _, error = order_workflow_v2_service.confirm_context(order_id=order["id"], facility_id=facility["facility_id"],
        week_start="2099-11-15", week_end="2099-11-21", expected_revision=current["revision"])
    assert error is None
    select_authored_native_result(monkeypatch, order_id=order["id"],
        table_rows=[["11/15", "朝", "朝メニュー", "20", "4", "6", "7", "8", "9", ""]],
        columns=columns, unlisted_cells="", artifact_root=tmp_path / "native-artifacts")
    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and sheet is not None, error
    assert sheet["fields"] == fields
    assert sheet["rows"][0][3:9] == ["20", "4", "6", "7", "8", "9"]
    assert "qty.business_x" not in fields
    assert sheet["base_evidence_run_id"]
    assert _read_order(order["id"])["lines"] == []


@pytest.mark.parametrize("unowned_rows", [
    [["", "", "", "6", ""], ["", "", "", "5", ""]],
    [["01/08", "朝", "Menu A", "99", ""], ["01/09", "朝", "Menu B", "88", ""]],
], ids=["row-index-tie-without-identity", "conflicting-raw-rows"])
def test_selected_source_identity_cannot_be_overridden_by_unowned_raw_rows(selected_history_order, unowned_rows):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    _seed_monthly_menu_custom_entries(month_id="2026-01", month_start=date(2026, 1, 1),
        entries=[(date(2026, 1, 8), "朝", "Menu A", 0),
                 (date(2026, 1, 9), "朝", "Menu B", 0)])
    order = _seed_custom_order(selected_history_order,
        message_id="msg-sheet-selected-identity-001", received_at=datetime(2026, 1, 8, 9),
        week="2026-01@2026-01-08~2026-01-14", effective_date="2026-01-08", lines=[],
        ocr_rows=[["01/08", "朝", "Menu A", "5", "", "", "", "", "", ""],
                  ["01/09", "朝", "Menu B", "6", "", "", "", "", "", ""]])
    original = deepcopy(order_service._load_order_ocr_cache(order["id"]))
    before = _snapshot(get_engine())
    assert order_service._save_order_ocr_cache(order["id"], {"table_rows": unowned_rows}) is None
    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and sheet is not None
    menu_idx, qty_idx = sheet["fields"].index("menu"), sheet["fields"].index("qty.regular_2f")
    actual = {row[menu_idx]: row[qty_idx] for row in sheet["rows"]}
    assert actual["Menu A"] == "5"
    assert actual["Menu B"] == "6"
    assert order_service._load_order_ocr_cache(order["id"]) == original
    assert _snapshot(get_engine()) == before


@pytest.mark.parametrize("unresolved", [False, True], ids=["wrong-date-first-pass", "unknown-menu-first-pass"])
def test_selected_physical_rows_never_borrow_or_realign_unowned_first_pass_groups(selected_history_order, unresolved):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    source = [("朝", "Menu A", "70"), ("朝", "Menu B", "70"),
              ("昼", "Menu C", "58"), ("昼", "Menu D", "59"), ("昼", "Menu E", "60")]
    _seed_monthly_menu_custom_entries(month_id="2026-04", month_start=date(2026, 4, 1),
        entries=[(date(2026, 4, 29), part, menu, index)
                 for index, (part, menu, _) in enumerate(source)])
    # The positive source explicitly says April 29; April 20 is never realigned.
    rows = [["04/29", part, menu, "" if unresolved else value, "", "", "", "", "", ""]
            for part, menu, value in source]
    order = _seed_custom_order(selected_history_order,
        message_id="msg-sheet-selected-first-pass-001", received_at=datetime(2026, 4, 29, 9),
        week="2026-04@2026-04-29~2026-05-05", effective_date="2026-04-29",
        lines=[], ocr_rows=rows)
    first_pass = (
        [["04/20", "朝", "Unknown A", "91", ""], ["", "朝", "Unknown B", "92", ""]]
        if unresolved else
        [["04/20", "朝", "", "70", ""], ["", "朝", "Menu B", "70", ""],
         ["", "昼", "Menu C", "58", ""], ["", "昼", "Menu D", "59", ""],
         ["", "昼", "Menu E", "60", ""]]
    )
    original = deepcopy(order_service._load_order_ocr_cache(order["id"]))
    before = _snapshot(get_engine())
    assert order_service._save_order_ocr_cache(order["id"], {
        "table_rows": [["04/20", "朝", "Polluted A", "999", ""],
                       ["", "朝", "Polluted B", "999", ""]],
        "first_pass_rows": first_pass,
    }) is None
    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and sheet is not None
    menu_idx, qty_idx = sheet["fields"].index("menu"), sheet["fields"].index("qty.regular_2f")
    actual = {row[menu_idx]: row[qty_idx] for row in sheet["rows"]}
    for _, menu, value in source:
        assert actual[menu] == ("" if unresolved else value)
    assert "Polluted A" not in actual and "Unknown A" not in actual
    assert order_service._load_order_ocr_cache(order["id"]) == original
    assert _snapshot(get_engine()) == before


def test_unselected_diagnostic_quantity_gate_cannot_override_current_selected_sheet(selected_history_order, monkeypatch):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-sheet-authoritative-gate-001")
    before, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and before is not None
    captured = order_service._capture_synchronous_cache_write_input(order["id"])
    assert captured is not None
    diagnostic = deepcopy(before)
    diagnostic["source"] = "unselected-diagnostic"
    diagnostic["rows"][0][diagnostic["fields"].index("qty.regular_2f")] = ""
    persisted = order_service.persist_sheet_draft(order_id=order["id"],
        draft_sheet_json=diagnostic, draft_state="review_required",
        blockers=["sheet_quantity_column_unmapped"],
        warnings=["sheet_payload_mapping_low_confidence", "sheet_ocr_review_required"],
        synchronous_write_input=captured)
    assert persisted is not None and persisted["id"] != captured["draft_id"]
    def forbidden(*args, **kwargs):
        pytest.fail("Current source authority must not be derived from positional fallbacks")
    monkeypatch.setattr(order_service.position_column_mapping_service, "payload_uses_ready_position_fallback", forbidden)
    monkeypatch.setattr(order_service.position_column_mapping_service, "payload_uses_partial_position_fallback", forbidden)
    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and sheet is not None
    for key in ("fields", "header", "rows", "row_ids", "base_evidence_run_id",
                "can_apply", "can_confirm", "apply_blockers", "confirm_blockers"):
        assert sheet[key] == before[key]
    assert "sheet_quantity_column_unmapped" not in sheet["apply_blockers"]


def test_resolve_sheet_week_does_not_infer_period_from_ocr_month_or_stale_hints():
    received_at = datetime(2026, 2, 3, 9, 0, 0)
    payload = {
        "table_raw": """
|日付|区分|献立|常|
|-|-|-|-|
|2/15|朝|A|10|
""".strip()
    }
    resolved = order_service._resolve_sheet_week_id(
        current_week_id=None,
        received_at=received_at,
        order_lines=[],
        ocr_payload=payload,
        facility_id="FAC00001",
        week_hints=["2025-12"],
    )
    assert resolved is None


def test_resolve_sheet_week_missing_period_cannot_use_far_ocr_month_or_old_hint():
    received_at = datetime(2026, 2, 13, 9, 0, 0)
    payload = {
        "table_raw": """
|日付|区分|献立|常|
|-|-|-|-|
|12/26|朝|A|10|
""".strip()
    }

    original_builder = order_service._build_position_menu_entries

    def _mock_build_position_menu_entries(month_id: str):
        if month_id == "2026-02":
            return [
                {
                    "menu_name": "A",
                    "menu_date": date(2026, 2, 26),
                    "daypart_key": "朝",
                    "slot_index": 0,
                    "order": 0,
                }
            ]
        if month_id == "2025-12":
            return [
                {
                    "menu_name": "A",
                    "menu_date": date(2025, 12, 26),
                    "daypart_key": "朝",
                    "slot_index": 0,
                    "order": 0,
                }
            ]
        return []

    order_service._build_position_menu_entries = _mock_build_position_menu_entries
    try:
        resolved = order_service._resolve_sheet_week_id(
            current_week_id=None,
            received_at=received_at,
            order_lines=[],
            ocr_payload=payload,
            facility_id="FAC00001",
            week_hints=["2025-12"],
        )
        assert resolved is None
    finally:
        order_service._build_position_menu_entries = original_builder


def test_resolve_sheet_week_preserves_explicit_range_when_ocr_dates_run_longer():
    received_at = datetime(2026, 3, 23, 9, 0, 0)
    payload = {
        "table_raw": """
|日付|区分|献立|常|
|-|-|-|-|
|3/22|朝|A|10|
|3/23|朝|B|10|
|3/24|朝|C|10|
|3/25|朝|D|10|
|3/26|朝|E|10|
|3/27|朝|F|10|
|3/28|朝|G|10|
|3/29|朝|H|10|
""".strip()
    }
    resolved = order_service._resolve_sheet_week_id(
        current_week_id="2026-03@2026-03-22~2026-03-28",
        received_at=received_at,
        order_lines=[],
        ocr_payload=payload,
        facility_id="FAC00003",
        week_hints=[],
    )
    assert resolved == "2026-03@2026-03-22~2026-03-28"


def _declared_date_collection_template():
    return {"main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"],
        "columns": [
            {"index": 0, "source_index": 0, "name": "date_mmdd", "role": "date"},
            {"index": 1, "source_index": 1, "name": "daypart", "role": "daypart"},
            {"index": 2, "source_index": 2, "name": "menu", "role": "menu_name"},
            {"index": 3, "source_index": 3, "name": "qty.regular_x", "role": "quantity",
             "diet_type": "regular", "area_id": "X"}]}


def test_collect_sheet_dates_ignores_footer_timestamp_when_table_dates_exist():
    payload = {
        "table_raw": """
|日付|区分|献立|常|
|-|-|-|-|
|2/15|朝|A|10|
|2/16|朝|B|11|
OV:LL 08/10/920Z
""".strip()
    }
    received_at = datetime(2026, 2, 13, 9, 0, 0)
    dates = order_service._collect_sheet_dates_from_payload(payload, received_at,
        _declared_date_collection_template(), explicit_week="2026-02@2026-02-15~2026-02-21")
    assert len(dates) >= 2
    assert all(item.month == 2 for item in dates)


def test_collect_sheet_dates_infers_weekday_hint_beyond_legacy_row_scan_window():
    received_at = datetime(2026, 3, 23, 9, 0, 0)
    payload = {
        "table_rows": [
            ["3/22", "朝", "A", "10"],
            ["3/23", "朝", "B", "10"],
            ["3/24", "朝", "C", "10"],
            ["3/25", "朝", "D", "10"],
            ["3/26", "朝", "E", "10"],
        ]
        + [["", "", "", ""] for _ in range(36)]
        + [["LZ/E\n(金)", "", "F", "10"]]
    }

    from copy import deepcopy
    original = deepcopy(payload)
    template = _declared_date_collection_template()
    period = "2026-03@2026-03-22~2026-03-28"
    with pytest.raises(ValueError, match="ingest_date_unresolved_in_selected_period"):
        order_service._collect_sheet_dates_from_payload(payload, received_at, template, explicit_week=period)
    assert payload == original
    # Separate explicit operator correction, never an inferred OCR replacement.
    corrected = deepcopy(payload)
    corrected["table_rows"][-1][0] = "3/27\n(金)"
    dates = order_service._collect_sheet_dates_from_payload(corrected, received_at, template, explicit_week=period)

    assert date(2026, 3, 27) in dates


def test_collect_sheet_dates_reads_structured_table_rows_for_weekday_hint():
    received_at = datetime(2026, 3, 23, 9, 0, 0)
    payload = {
        "tables": [
            {
                "table_id": "t1",
                "page_index": 1,
                "rows": [
                    ["3/26\n(木)", "朝", "ごぼうと竹輪の煮物", "7"],
                    ["", "", "いんげんの味噌和え", "7"],
                    ["LZ/E\n(金)", "朝", "豆腐と大根の含め煮", "7"],
                    ["", "", "白菜のおかか和え", "7"],
                ],
            }
        ]
    }

    from copy import deepcopy
    original = deepcopy(payload)
    template = _declared_date_collection_template()
    period = "2026-03@2026-03-22~2026-03-28"
    with pytest.raises(ValueError, match="ingest_date_unresolved_in_selected_period"):
        order_service._collect_sheet_dates_from_payload(payload, received_at, template, explicit_week=period)
    assert payload == original
    corrected = deepcopy(payload)
    corrected["tables"][0]["rows"][2][0] = "3/27\n(金)"
    dates = order_service._collect_sheet_dates_from_payload(corrected, received_at, template, explicit_week=period)

    assert date(2026, 3, 26) in dates
    assert date(2026, 3, 27) in dates


def test_ocr_menu_entries_cannot_infer_missing_period_from_noisy_weekday_anchor():
    template = {
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"],
        "large_cell_mode": True,
    }
    payload = {
        "table_rows": [
            ["3/26\n(木)", "朝", "ごぼうと竹輪の煮物", "7"],
            ["", "", "いんげんの味噌和え", "7"],
            ["LZ/E\n(金)", "朝", "豆腐と大根の含め煮", "7"],
            ["", "", "白菜のおかか和え", "7"],
        ]
    }

    original_payload, original_template = deepcopy(payload), deepcopy(template)
    with pytest.raises(ValueError, match="^week_hint_required$"):
        order_service._build_position_menu_entries_from_ocr_payload(
            payload=payload, template=template, received_at=datetime(2026, 3, 23, 9, 0, 0),
        )
    assert payload == original_payload and template == original_template


def test_ocr_menu_entries_cannot_resolve_missing_period_from_neighboring_daypart_tokens():
    template = {
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"],
        "large_cell_mode": True,
    }
    payload = {
        "table_rows": [
            ["4/05", "朝", "A", "7"],
            ["", "\"", "B", "7"],
            ["", "61", "C", "7"],
            ["4/05", "昼", "D", "7"],
            ["4/07", "&", "E", "7"],
            ["", "", "F", "7"],
            ["", "夕", "G", "7"],
        ]
    }

    original_payload, original_template = deepcopy(payload), deepcopy(template)
    with pytest.raises(ValueError, match="^week_hint_required$"):
        order_service._build_position_menu_entries_from_ocr_payload(
            payload=payload, template=template, received_at=datetime(2026, 4, 5, 9, 0, 0),
        )
    assert payload == original_payload and template == original_template


def test_selected_physical_dayparts_are_not_replaced_by_unowned_invalid_tokens(selected_history_order):
    order_service.clear_all()
    _seed_monthly_menu_custom_entries(
        month_id="2026-04",
        month_start=date(2026, 4, 1),
        entries=[
            (date(2026, 4, 5), "朝", "A", 0),
            (date(2026, 4, 5), "朝", "B", 1),
            (date(2026, 4, 5), "朝", "C", 2),
            (date(2026, 4, 5), "昼", "D", 3),
            (date(2026, 4, 7), "夕", "E", 4),
            (date(2026, 4, 7), "夕", "F", 5),
            (date(2026, 4, 7), "夕", "G", 6),
        ],
    )
    payload = IngestEmailPayload(
        message_id="msg-sheet-invalid-daypart-ocr-table-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 4, 5, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = _register_scoped_order(selected_history_order, payload, lines=[],
        week='2026-04@2026-04-05~2026-04-11', blank_source=True)
    original = deepcopy(order_service._load_order_ocr_cache(order["id"]))
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["04/05", "朝", "A", "7"],
                ["", "\"", "B", "7"],
                ["", "61", "C", "7"],
                ["04/05", "昼", "D", "7"],
                ["04/07", "&", "E", "7"],
                ["", "", "F", "7"],
                ["", "夕", "G", "7"],
            ],
            "date_strings": ["04/05", "04/07"],
            "cell_issues": [
                {
                    "row_index": 2,
                    "column_index": 1,
                    "field": "daypart",
                    "issue_code": "merged_numeric_cell",
                    "severity": "warning",
                    "source": "yomitoku_structured",
                    "text": "61",
                }
            ],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert sheet is not None
    fields = sheet["fields"]
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    rows_by_menu = {row[menu_idx]: row for row in sheet["rows"]}
    assert rows_by_menu["A"][daypart_idx] == "朝"
    assert rows_by_menu["B"][daypart_idx] == "朝"
    assert rows_by_menu["C"][daypart_idx] == "朝"
    assert rows_by_menu["D"][daypart_idx] == "昼"
    assert rows_by_menu["E"][daypart_idx] == "夕"
    assert rows_by_menu["F"][daypart_idx] == "夕"
    assert rows_by_menu["G"][daypart_idx] == "夕"
    assert all(row[daypart_idx] in {"朝", "昼", "夕"} for row in sheet["rows"])
    assert order_service._load_order_ocr_cache(order["id"]) == original


def test_merge_weekly_menu_entries_with_ocr_tail_keeps_weekly_entries_only_for_current_sheet():
    weekly_entries = [
        {"menu_date": date(2026, 3, 22), "daypart_key": "朝", "menu_name": "A", "slot_index": 0, "order": 0},
        {"menu_date": date(2026, 3, 26), "daypart_key": "夕", "menu_name": "B", "slot_index": 1, "order": 1},
    ]
    ocr_entries = [
        {"menu_date": date(2026, 3, 26), "daypart_key": "夕", "menu_name": "B", "slot_index": 1, "order": 1},
        {"menu_date": date(2026, 3, 27), "daypart_key": "朝", "menu_name": "C", "slot_index": 2, "order": 2},
    ]

    merged = order_service._merge_weekly_menu_entries_with_ocr_tail(weekly_entries, ocr_entries)

    assert [item.get("menu_date") for item in merged] == [
        date(2026, 3, 22),
        date(2026, 3, 26),
    ]
    assert [item.get("menu_name") for item in merged] == ["A", "B"]


def test_get_ocr_sheet_without_facility_returns_error(monkeypatch, tmp_path):
    from tests.helpers.ingest_original import authored_sources
    order_service.clear_all()
    authored_sources(monkeypatch, tmp_path, facility_id="missing-facility-component", menu_date=date(2026, 2, 15),
                     seed_menu=False, seed_month=False)
    order = _seed_order_without_facility(message_id="msg-sheet-no-fac-001")

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert sheet is None
    assert error == "facility_missing"


def test_apply_without_facility_cannot_capture_or_publish_a_source(monkeypatch, tmp_path):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot
    from tests.helpers.ingest_original import authored_sources

    order_service.clear_all()
    authored_sources(monkeypatch, tmp_path, facility_id="missing-facility-component", menu_date=date(2026, 2, 15),
                     seed_menu=False, seed_month=False)
    order = _seed_order_without_facility(message_id="msg-sheet-no-fac-002")
    before = _snapshot(get_engine())
    assert order_service.get_ocr_sheet(order["id"]) == (None, "facility_missing")
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "qty.regular_3f",
        "qty.soft_2f",
        "qty.soft_3f",
        "qty.mixer_2f",
        "qty.mixer_3f",
        "remarks",
    ]
    header = ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"]
    rows = [["02/15", "朝", "Menu B", "7", "", "", "", "", "", "manual"]]

    updated, error = order_service.apply_ocr_table(
        order["id"],
        header=header,
        rows=rows,
        ui_mode="sheet",
        fields=fields,
        row_ids=["row-no-fac-1"],
    )
    assert updated is None
    assert error == "synchronous_input_missing"
    assert _snapshot(get_engine()) == before


def test_selected_sheet_without_order_lines_keeps_physical_menu_dates(selected_history_order):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
        session.add(
            MonthlyMenu(
                id="2099-12",
                month_start=date(2099, 12, 1),
                filename="seed-2099-12.xlsx",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-26-breakfast-a",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 26),
                    daypart="朝食",
                    name="Menu A",
                    slot_index=0,
                    diet_type="regular",
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-27-breakfast-b",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 27),
                    daypart="朝食",
                    name="Menu B",
                    slot_index=0,
                    diet_type="regular",
                ),
            ]
        )
    payload = IngestEmailPayload(
        message_id="msg-sheet-payload-dates-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 26, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = _register_scoped_order(selected_history_order, payload, lines=[],
        week='2099-12@2099-12-26~2100-01-01', ocr_rows=[
            ["12/26", "朝", "Menu A", "7", "", "", "", "", "", ""],
            ["12/27", "朝", "Menu B", "", "", "", "", "", "", ""],
        ])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/26", "朝", "Menu A", "7", "", "", "", "", "", ""],
                ["12/27", "朝", "Menu B", "", "", "", "", "", "", ""],
            ]
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["base_evidence_run_id"]
    dates = {row[0] for row in sheet["rows"] if row and row[0]}
    assert "12/26" in dates
    assert "12/27" in dates


def test_get_ocr_sheet_respects_explicit_selected_week_range(selected_history_order):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
        session.add(
            MonthlyMenu(
                id="2099-12",
                month_start=date(2099, 12, 1),
                filename="seed-2099-12.xlsx",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-26-breakfast-a-range",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 26),
                    daypart="朝食",
                    name="Menu A",
                    slot_index=0,
                    diet_type="regular",
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-27-breakfast-b-range",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 27),
                    daypart="朝食",
                    name="Menu B",
                    slot_index=0,
                    diet_type="regular",
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-28-breakfast-c-range",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 28),
                    daypart="朝食",
                    name="Menu C",
                    slot_index=0,
                    diet_type="regular",
                ),
            ]
        )
    payload = IngestEmailPayload(
        message_id="msg-sheet-week-range-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 26, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-12",
    )
    order = _register_scoped_order(selected_history_order, payload, lines=[],
        week='2099-12@2099-12-26~2099-12-27', blank_source=True)

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["week_id"] == "2099-12@2099-12-26~2099-12-27"
    dates = [row[0] for row in sheet["rows"] if row and row[0]]
    assert dates == ["12/26", "12/27"]


def test_selected_sheet_uses_explicit_period_with_quantities_5_6_7(selected_history_order):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-02"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-02"))
        session.add(
            MonthlyMenu(
                id="2099-02",
                month_start=date(2099, 2, 1),
                filename="seed-2099-02.xlsx",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="seed-entry-2099-02-01-breakfast-a",
                    monthly_menu_id="2099-02",
                    menu_date=date(2099, 2, 1),
                    daypart="朝食",
                    name="Menu 01",
                    slot_index=0,
                    diet_type="regular",
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-02-08-breakfast-a",
                    monthly_menu_id="2099-02",
                    menu_date=date(2099, 2, 8),
                    daypart="朝食",
                    name="Menu 08",
                    slot_index=0,
                    diet_type="regular",
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-02-09-breakfast-a",
                    monthly_menu_id="2099-02",
                    menu_date=date(2099, 2, 9),
                    daypart="朝食",
                    name="Menu 09",
                    slot_index=0,
                    diet_type="regular",
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-02-10-breakfast-a",
                    monthly_menu_id="2099-02",
                    menu_date=date(2099, 2, 10),
                    daypart="朝食",
                    name="Menu 10",
                    slot_index=0,
                    diet_type="regular",
                ),
            ]
        )
    payload = IngestEmailPayload(
        message_id="msg-sheet-payload-filter-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 2, 10, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = _register_scoped_order(selected_history_order, payload, lines=[],
        week='2099-02@2099-02-08~2099-02-10', ocr_rows=[
            ["2/8", "朝", "Menu 08", "5", "", "", "", "", "", ""],
            ["2/9", "朝", "Menu 09", "6", "", "", "", "", "", ""],
            ["2/10", "朝", "Menu 10", "7", "", "", "", "", "", ""],
        ])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["2/8", "朝", "Menu 08", "5", "", "", "", "", "", ""],
                ["2/9", "朝", "Menu 09", "6", "", "", "", "", "", ""],
                ["2/10", "朝", "Menu 10", "7", "", "", "", "", "", ""],
            ]
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["base_evidence_run_id"]

    date_idx = next((idx for idx, field in enumerate(sheet["fields"]) if field.startswith("date")), 0)
    dates = {row[date_idx] for row in sheet["rows"] if row and date_idx < len(row) and row[date_idx]}
    assert dates == {"02/08", "02/09", "02/10"}
    assert [row[sheet["fields"].index("qty.regular_2f")] for row in sheet["rows"]] == ["5", "6", "7"]


def test_get_ocr_sheet_without_date_anchors_preserves_explicit_range_despite_payload_row_count(selected_history_order):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-03"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-03"))
        session.add(
            MonthlyMenu(
                id="2099-03",
                month_start=date(2099, 3, 1),
                filename="seed-2099-03.xlsx",
            )
        )
        entries = []
        for day in range(1, 29):
            entries.append(
                MonthlyMenuEntry(
                    id=f"seed-entry-2099-03-{day:02d}-breakfast-a",
                    monthly_menu_id="2099-03",
                    menu_date=date(2099, 3, day),
                    daypart="朝食",
                    name=f"Menu {day:02d}",
                    slot_index=0,
                    diet_type="regular",
                )
            )
        session.add_all(entries)
    payload = IngestEmailPayload(
        message_id="msg-sheet-payload-rowcount-scope-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 3, 20, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = _register_scoped_order(selected_history_order, payload, lines=[],
        week='2099-03@2099-03-01~2099-03-28', blank_source=True)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["", "", f"OCR {idx}", "5", "", "", "", "", "", ""]
                for idx in range(7)
            ]
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["base_evidence_run_id"]
    assert len(sheet["rows"]) == 28

    date_idx = next((idx for idx, field in enumerate(sheet["fields"]) if field.startswith("date")), 0)
    dates = [
        row[date_idx]
        for row in sheet["rows"]
        if row and date_idx < len(row) and row[date_idx]
    ]
    assert dates
    assert "03/20" in set(dates)


def test_selected_sheet_ignores_unowned_payload_dates_for_row_selection(selected_history_order):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
        session.add(
            MonthlyMenu(
                id="2099-12",
                month_start=date(2099, 12, 1),
                filename="seed-2099-12.xlsx",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-26-breakfast-a-2",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 26),
                    daypart="朝食",
                    name="Menu A",
                    slot_index=0,
                    diet_type="regular",
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-27-breakfast-b-2",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 27),
                    daypart="朝食",
                    name="Menu B",
                    slot_index=0,
                    diet_type="regular",
                ),
            ]
        )
    payload = IngestEmailPayload(
        message_id="msg-sheet-payload-dates-merge-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 26, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    # Simulate sparse persisted lines after manual apply: only one date remains.
    lines = [
        {
            "date": "2099-12-26",
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 7,
        }
    ]
    order = _register_scoped_order(selected_history_order, payload, lines=lines, week='2099-12@2099-12-26~2100-01-01')
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/26", "朝", "Menu A", "7", "", "", "", "", "", ""],
                ["12/27", "朝", "Menu B", "", "", "", "", "", "", ""],
            ]
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    dates = {row[0] for row in sheet["rows"] if row and row[0]}
    assert dates == {"12/26", "12/27"}


def test_get_ocr_sheet_keeps_late_week_dates_without_borrowing_unowned_table_rows(selected_history_order):
    config_service.reload_configs()
    order_service.clear_all()
    _seed_monthly_menu_custom_entries(
        month_id="2026-04",
        month_start=date(2026, 4, 1),
        entries=[
            (date(2026, 4, 10), "朝", "なすとピーマンのオイスター炒め", 0),
            (date(2026, 4, 10), "朝", "ひじきのごま和え", 1),
            (date(2026, 4, 10), "昼", "鶏肉の味噌炒め", 2),
            (date(2026, 4, 10), "昼", "切干大根煮", 3),
            (date(2026, 4, 10), "昼", "いんげんのおかか和え", 4),
            (date(2026, 4, 10), "夕", "タラのムニエル 添)ｷｬﾍﾞﾂ", 5),
            (date(2026, 4, 10), "夕", "人参の卵とじ", 6),
            (date(2026, 4, 10), "夕", "カリフラワーマリネ", 7),
            (date(2026, 4, 11), "朝", "厚揚げと竹輪の煮物", 0),
            (date(2026, 4, 11), "朝", "ほうれん草のお浸し", 1),
            (date(2026, 4, 11), "昼", "ポークチャップ", 2),
            (date(2026, 4, 11), "昼", "マカロニソテー", 3),
            (date(2026, 4, 11), "昼", "胡瓜とコーンのサラダ", 4),
            (date(2026, 4, 11), "夕", "カレイの照焼き 添)ﾌﾞﾛｯｺﾘｰ", 5),
            (date(2026, 4, 11), "夕", "さつま芋の煮物", 6),
            (date(2026, 4, 11), "夕", "三色ナムル", 7),
        ],
    )
    payload = IngestEmailPayload(
        message_id="msg-weekly-shell-late-week-dates-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 4, 10, 9, 0, 0),
        facility_hint="FAC00005",
        week_hint="2026-04",
    )
    lines = []
    order = _register_scoped_order(selected_history_order, payload, lines=lines,
        week='2026-04@2026-04-10~2026-04-16', blank_source=True)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "template_id": "fax_layout_soft_packaging_forbidden_v1",
            "tables": [
                {
                        "page_index": 1,
                        "table_id": "page1_table1",
                        "row_count": 16,
                        "col_count": 10,
                        "rows": [
                            ["4/10\n(金)", "", "物", "", "", "0", "0", "", "", ""],
                            ["", "", "", "謝れか", "小松菜のごま和え", "0", "0", "", "", ""],
                            ["", "", "社", "#A\nAND", "鶏肉の味噌炒め", "58", "2", "", "", ""],
                            ["", "", "", "", "", "58", "2", "", "", ""],
                            ["", "", "", "", "", "58", "2", "", "", ""],
                            ["", "", "", "", "", "0", "0", "", "", ""],
                            ["", "", "", "", "", "0", "0", "", "", ""],
                            ["", "", "", "", "", "0", "0", "", "", ""],
                            ["4/11\n(土)", "", "", "", "厚揚げと竹輪の煮物\nほうれん草のお浸し", "0", "0", "", "", ""],
                            ["", "", "", "", "", "0", "n", "", "", ""],
                            ["", "", "", "", "ポークチャップ\nマカロニソテー\n胡瓜とコーンのサラダ", "0", "0", "", "", ""],
                            ["", "", "", "", "", "0", "0", "", "", ""],
                            ["", "", "", "", "", "0", "0", "", "", ""],
                            ["", "", "", "", "カレイの照焼き\n添)プロッコリー\nさつま芋の煮物", "58\n58", "2", "", "", ""],
                            ["", "", "", "", "", "", "2", "", "", ""],
                            ["", "", "", "", "", "58", "2", "", "", ""],
                        ],
                    }
                ],
            },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert isinstance(sheet, dict)
    fields = list(sheet.get("fields") or [])
    date_idx = fields.index("date_mmdd")
    menu_idx = fields.index("menu")
    dates = [row[date_idx] for row in sheet["rows"] if row and row[date_idx]]
    assert "04/10" in dates
    assert "04/11" in dates
    rows_by_identity = {
        (row[date_idx], row[menu_idx]): row
        for row in sheet["rows"]
        if len(row) > max(date_idx, menu_idx)
    }
    assert ("04/10", "鶏肉の味噌炒め") in rows_by_identity
    assert ("04/11", "厚揚げと竹輪の煮物") in rows_by_identity


def test_unmapped_late_week_raw_columns_cannot_be_realigned_into_weekly_quantities(repository_layout_source):
    config_service.reload_configs()
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.soft_x",
        "qty.regular_bag_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.change_1_x",
        "qty.change_2_x",
        "remarks",
    ]
    rows = [
        {"values": ["04/10", "朝", "なすとピーマンのオイスター炒め", "", "", "", "", "", "", ""]},
        {"values": ["04/10", "朝", "ひじきのごま和え", "", "", "", "", "", "", ""]},
        {"values": ["04/10", "昼", "鶏肉の味噌炒め", "", "", "", "", "", "", ""]},
        {"values": ["04/10", "昼", "切干大根煮", "", "", "", "", "", "", ""]},
        {"values": ["04/10", "昼", "いんげんのおかか和え", "", "", "", "", "", "", ""]},
        {"values": ["04/10", "夕", "タラのムニエル 添)ｷｬﾍﾞﾂ", "", "", "", "", "", "", ""]},
        {"values": ["04/10", "夕", "人参の卵とじ", "", "", "", "", "", "", ""]},
        {"values": ["04/10", "夕", "カリフラワーマリネ", "", "", "", "", "", "", ""]},
        {"values": ["04/11", "朝", "厚揚げと竹輪の煮物", "", "", "", "", "", "", ""]},
        {"values": ["04/11", "朝", "ほうれん草のお浸し", "", "", "", "", "", "", ""]},
        {"values": ["04/11", "昼", "ポークチャップ", "", "", "", "", "", "", ""]},
        {"values": ["04/11", "昼", "マカロニソテー", "", "", "", "", "", "", ""]},
        {"values": ["04/11", "昼", "胡瓜とコーンのサラダ", "", "", "", "", "", "", ""]},
        {"values": ["04/11", "夕", "カレイの照焼き 添)ﾌﾞﾛｯｺﾘｰ", "", "", "", "", "", "", ""]},
        {"values": ["04/11", "夕", "さつま芋の煮物", "", "", "", "", "", "", ""]},
        {"values": ["04/11", "夕", "三色ナムル", "", "", "", "", "", "", ""]},
    ]
    template = (config_service.get_facility_config("FAC00005") or {}).get("fax_template") or {}
    raw_payload = {
            "template_id": "fax_layout_soft_packaging_forbidden_v1",
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": 16,
                    "col_count": 10,
                    "rows": [
                        ["4/10\n(金)", "", "物", "", "", "0", "0", "", "", ""],
                        ["", "", "", "謝れか", "小松菜のごま和え", "0", "0", "", "", ""],
                        ["", "", "社", "#A\nAND", "鶏肉の味噌炒め", "58", "2", "", "", ""],
                        ["", "", "", "", "", "58", "2", "", "", ""],
                        ["", "", "", "", "", "58", "2", "", "", ""],
                        ["", "", "", "", "", "0", "0", "", "", ""],
                        ["", "", "", "", "", "0", "0", "", "", ""],
                        ["", "", "", "", "", "0", "0", "", "", ""],
                        ["4/11\n(土)", "", "", "", "厚揚げと竹輪の煮物\nほうれん草のお浸し", "0", "0", "", "", ""],
                        ["", "", "", "", "", "0", "n", "", "", ""],
                        ["", "", "", "", "ポークチャップ\nマカロニソテー\n胡瓜とコーンのサラダ", "0", "0", "", "", ""],
                        ["", "", "", "", "", "0", "0", "", "", ""],
                        ["", "", "", "", "", "0", "0", "", "", ""],
                        ["", "", "", "", "カレイの照焼き\n添)プロッコリー\nさつま芋の煮物", "58\n58", "2", "", "", ""],
                        ["", "", "", "", "", "", "2", "", "", ""],
                        ["", "", "", "", "", "58", "2", "", "", ""],
                    ],
                }
            ],
        }
    original_rows, original_payload = deepcopy(rows), deepcopy(raw_payload)
    original_template = deepcopy(template)
    with pytest.raises(order_service._CanonicalOcrProjectionError, match="sheet_quantity_column_unmapped"):
        order_service._extract_sheet_rows_from_payload(raw_payload, template)
    assert rows == original_rows and raw_payload == original_payload
    assert template == original_template
    assert all(row["values"][index] == "" for row in rows for index, field in enumerate(fields) if field.startswith("qty."))


def test_selected_physical_menu_includes_intermediate_dates_when_stored_lines_are_sparse(selected_history_order):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
        session.add(
            MonthlyMenu(
                id="2099-12",
                month_start=date(2099, 12, 1),
                filename="seed-2099-12.xlsx",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-26-breakfast-a-3",
                    diet_type="regular",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 26),
                    daypart="朝食",
                    name="Menu A",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-27-breakfast-b-3",
                    diet_type="regular",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 27),
                    daypart="朝食",
                    name="Menu B",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-28-breakfast-c-3",
                    diet_type="regular",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 28),
                    daypart="朝食",
                    name="Menu C",
                    slot_index=0,
                ),
            ]
        )
    payload = IngestEmailPayload(
        message_id="msg-sheet-intermediate-dates-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 26, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-12",
    )
    # Simulate sparse parsed dates (middle day missing in OCR/order lines).
    lines = [
        {
            "date": "2099-12-26",
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 7,
        },
        {
            "date": "2099-12-28",
            "daypart": "朝",
            "menu_name": "Menu C",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 9,
        },
    ]
    order = _register_scoped_order(selected_history_order, payload, lines=lines,
        week='2099-12@2099-12-26~2100-01-01', ocr_rows=[
            ["12/26", "朝", "Menu A", "7", "", "", "", "", "", ""],
            ["12/27", "朝", "Menu B", "", "", "", "", "", "", ""],
            ["12/28", "朝", "Menu C", "9", "", "", "", "", "", ""],
        ])

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    dates = {row[0] for row in sheet["rows"] if row and row[0]}
    assert "12/26" in dates
    assert "12/27" in dates
    assert "12/28" in dates
    menu_idx, qty_idx = sheet["fields"].index("menu"), sheet["fields"].index("qty.regular_2f")
    actual = {row[menu_idx]: row[qty_idx] for row in sheet["rows"]}
    assert {menu: actual[menu] for menu in ("Menu A", "Menu B", "Menu C")} == {
        "Menu A": "7", "Menu B": "", "Menu C": "9"}


def test_selected_canonical_fields_cannot_be_replaced_by_invalid_legacy_config(selected_history_order):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-sheet-invalid-template-001")
    original_sheet, original_error = order_service.get_ocr_sheet(order["id"])
    assert original_error is None and original_sheet is not None
    original_get = config_service.get_facility_config

    def _mock_get(facility_id: str):
        current = original_get(facility_id)
        if not current or facility_id != "FAC00001":
            return current
        payload = dict(current)
        template = dict(payload.get("fax_template") or {})
        template["main_ocr_row_fields"] = ["date_mmdd", "menu", "invalid_field"]
        payload["fax_template"] = template
        return payload

    config_service.get_facility_config = _mock_get
    try:
        sheet, error = order_service.get_ocr_sheet(order["id"])
        assert error is None
        assert isinstance(sheet, dict)
        for key in ("fields", "header", "rows", "row_ids", "base_evidence_run_id"):
            assert sheet[key] == original_sheet[key]
        assert "invalid_field" not in sheet["fields"]
    finally:
        config_service.get_facility_config = original_get


def test_unbound_archived_order_cannot_reconstruct_a_sheet_from_legacy_db_and_raw_cache(repository_layout_source):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(Facility).where(Facility.id == "FAC00002"))
        session.execute(delete(FacilityConfig).where(FacilityConfig.facility_id == "FAC00002"))
        session.execute(delete(OrderOcrCache).where(OrderOcrCache.order_id == "ORDc935f9e2"))
        session.execute(delete(Order).where(Order.id == "ORDc935f9e2"))
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2026-02"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2026-02"))
        session.add(
            MonthlyMenu(
                id="2026-02",
                month_start=date(2026, 2, 1),
                filename="fixture-2026-02.xlsx",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="fixture-2026-02-15-breakfast-main",
                    monthly_menu_id="2026-02",
                    menu_date=date(2026, 2, 15),
                    daypart="朝食",
                    name="じゃが芋のコンソメ煮",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="fixture-2026-02-15-breakfast-sub",
                    monthly_menu_id="2026-02",
                    menu_date=date(2026, 2, 15),
                    daypart="朝食",
                    name="キャベツサラダ",
                    slot_index=1,
                ),
            ]
        )
        session.add(
            Facility(
                id="FAC00002",
                name="Fixture FAC00002",
            )
        )
        session.add(
            FacilityConfig(
                facility_id="FAC00002",
                config_json={
                    "fax_template_override": {
                        "main_ocr_row_fields": [
                            "date_mmdd",
                            "daypart",
                            "menu",
                            "qty.regular_x",
                            "qty.soft_x",
                            "qty.mixer_x",
                            "qty.no_meat_x",
                            "qty.no_fish_x",
                            "remarks",
                        ]
                    }
                },
            )
        )
        session.add(
            Order(
                id="ORDc935f9e2",
                facility_code="FAC00002",
                week_code=None,
                status="要確認",
                document_uri="file://fixture.pdf",
                message_id="fixture:fax000335185_0215-1.pdf",
                received_at=datetime(2026, 2, 3, 3, 7, 39),
            )
        )
        session.add(
            OrderOcrCache(
                order_id="ORDc935f9e2",
                payload={
                    "table_raw": """
|日付|区分|献立|常食|軟菜|ミキサー|肉禁|魚禁|備考|
|-|-|-|-|-|-|-|-|-|
|2/15|朝|じゃが芋のコンソメ煮|||||||
|2/15|朝|キャベツサラダ|23|||||||
""".strip()
                },
            )
        )

    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    before = _snapshot(get_engine())
    assert order_service.get_ocr_sheet("ORDc935f9e2") == (None, "week_unresolved")
    assert "23" in order_service._load_order_ocr_cache("ORDc935f9e2")["table_raw"]
    assert _snapshot(get_engine()) == before


def test_extract_sheet_rows_from_payload_merges_blocks_and_collects_unstructured_qty():
    payload = {
        "table_raw": """
|日付|区分|献立|常食|備考|
|-|-|-|-|-|
|2/15|朝|Menu A|23||

|日付|区分|献立|常食|備考|
|-|-|-|-|-|
|2/15|朝|Menu B|||

16
""".strip()
    }
    template = {
        "columns": [{"index": 3, "source_index": 3, "role": "quantity",
                     "name": "qty.regular_x", "diet_type": "regular", "area_id": "X"}],
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"]
    }

    rows = order_service._extract_sheet_rows_from_payload(payload, template)
    assert len(rows) == 2
    assert rows[0][2] == "Menu A"
    assert rows[1][2] == "Menu B"

    sanitized = order_service._sanitize_payload_table_raw(payload)
    candidates = order_service._extract_payload_unstructured_quantity_candidates(sanitized)
    assert "16" in candidates


def test_hakodate_projection_does_not_adopt_unowned_target_truth_for_remarks():
    base_sheet = {
        "fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
            "qty.regular_3f",
            "qty.soft_2f",
            "qty.soft_3f",
            "qty.mixer_2f",
            "qty.mixer_3f",
            "qty.regular_x",
            "remarks",
        ],
        "rows": [
            ["04/26", "朝", "Menu A", "", "", "", "", "", "", "", ""],
            ["04/26", "朝", "Menu B", "", "", "", "", "", "", "", ""],
            ["04/26", "昼", "Menu C", "", "", "", "", "", "", "", ""],
        ],
    }
    assignment = {
        "target_cells": [
            {
                "target_cell_id": "K13",
                "sheet_cell": "K13",
                "worksheet_row": 13,
                "worksheet_col": 11,
                "semantic_field": "note",
                "metadata": {"truth": {"row_index": 2, "field": "remarks"}},
            }
        ],
        "sheet_output": {
            "cells": {
                "K13": {
                    "sheet_cell": "K13",
                    "worksheet_row": 13,
                    "worksheet_col": 11,
                    "semantic_field": "note",
                    "value_text": "111",
                    "value_normalized": "111",
                    "assignment_confidence": 0.66,
                }
            }
        },
    }

    original = deepcopy(base_sheet)
    with pytest.raises(ValueError, match="^hakodate_projection_config_required$"):
        order_service._apply_hakodate_sheet_output_to_sheet_payload(
            base_sheet=base_sheet, assignment=assignment,
        )
    assert base_sheet == original


def test_hakodate_projection_does_not_fallback_to_worksheet_position_without_truth():
    base_sheet = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "rows": [["04/26", "朝", "Menu A", "", ""]],
    }
    assignment = {
        "target_cells": [
            {
                "target_cell_id": "D3",
                "sheet_cell": "D3",
                "worksheet_row": 3,
                "worksheet_col": 4,
                "semantic_field": "qty.regular_2f",
            }
        ],
        "sheet_output": {
            "cells": {
                "D3": {
                    "sheet_cell": "D3",
                    "worksheet_row": 3,
                    "worksheet_col": 4,
                    "semantic_field": "qty.regular_2f",
                    "value_text": "70",
                    "value_normalized": "70",
                    "assignment_confidence": 0.9,
                }
            }
        },
    }

    original = deepcopy(base_sheet)
    with pytest.raises(ValueError, match="^hakodate_projection_config_required$"):
        order_service._apply_hakodate_sheet_output_to_sheet_payload(
            base_sheet=base_sheet, assignment=assignment,
        )
    assert base_sheet == original


def test_selected_source_blocks_unowned_off_month_noise(selected_history_order):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
        session.add(
            MonthlyMenu(
                id="2099-12",
                month_start=date(2099, 12, 1),
                filename="seed-2099-12.xlsx",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-26-breakfast-a-4",
                    diet_type="regular",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 26),
                    daypart="朝食",
                    name="Menu A",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-27-breakfast-b-4",
                    diet_type="regular",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 27),
                    daypart="朝食",
                    name="Menu B",
                    slot_index=0,
                ),
            ]
        )
    payload = IngestEmailPayload(
        message_id="msg-sheet-payload-offmonth-noise-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 26, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-12",
    )
    lines = [
        {
            "date": "2099-12-26",
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 7,
        },
        {
            "date": "2099-12-27",
            "daypart": "朝",
            "menu_name": "Menu B",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 8,
        },
    ]
    order = _register_scoped_order(selected_history_order, payload, lines=lines, week='2099-12@2099-12-26~2100-01-01')
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/01", "朝", "NOISE MENU", "99", "", "", "", "", "", ""],
                ["12/26", "朝", "Menu A", "1", "", "", "", "", "", ""],
                ["12/27", "朝", "Menu B", "2", "", "", "", "", "", ""],
            ]
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    fields = sheet["fields"]
    qty_idx = next(
        idx
        for idx, field in enumerate(fields)
        if field in {"qty.regular_2f", "qty.regular_x"}
    )
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)

    dates = {row[date_idx] for row in sheet["rows"] if row and date_idx < len(row) and row[date_idx]}
    assert dates == {"12/26", "12/27"}
    assert all("NOISE" not in row[menu_idx] for row in sheet["rows"] if len(row) > menu_idx)

    menu_a = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "12/26" and row[daypart_idx] == "朝" and row[menu_idx] == "Menu A"
    )
    menu_b = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "12/27" and row[daypart_idx] == "朝" and row[menu_idx] == "Menu B"
    )
    assert menu_a[qty_idx] == "7"
    assert menu_b[qty_idx] == "8"


def test_selected_source_and_stored_line_edits_remain_separate(selected_history_order):
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-update-lines-priority-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    initial_lines = [
        {
            "line_id": "line-initial-1",
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 6,
        }
    ]
    order = _register_scoped_order(selected_history_order, payload, lines=initial_lines, week='2099-11@2099-11-15~2099-11-21')
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "朝", "OCRノイズメニュー", "99", "", "", "", "", "", "payload-note"],
            ]
        },
    )
    replaced_lines = [
        {
            "line_id": "line-updated-1",
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 20,
        }
    ]
    assert order_service.update_lines(order["id"], replaced_lines) is True

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    fields = sheet["fields"]
    qty_idx = next(
        idx
        for idx, field in enumerate(fields)
        if field in {"qty.regular_2f", "qty.regular_x"}
    )
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)

    breakfast = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "11/15" and row[daypart_idx] == "朝" and row[menu_idx] == "朝メニュー"
    )
    assert breakfast[qty_idx] == "6"
    assert _read_order(order["id"])["lines"][0]["quantity_original"] == 20


def test_selected_sheets_are_consistent_but_missing_explicit_week_is_refused(selected_history_order):
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    base_payload = IngestEmailPayload(
        message_id="msg-sheet-consistency-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    lines = [
        {
            "line_id": "line-consistent-1",
            "date": "2099-11-15",
            "daypart": "昼",
            "menu_name": "昼メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 6,
        },
        {
            "line_id": "line-consistent-2",
            "date": "2099-11-15",
            "daypart": "夕",
            "menu_name": "夕メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 5,
        },
    ]
    order_a = _register_scoped_order(selected_history_order, base_payload, lines=lines, week='2099-11@2099-11-15~2099-11-21')
    payload_b = IngestEmailPayload(
        message_id="msg-sheet-consistency-002",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 1),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order_b = _register_scoped_order(selected_history_order, payload_b, lines=lines, week='2099-11@2099-11-15~2099-11-21')
    order_service._save_order_ocr_cache(
        order_a["id"],
        {
            "table_rows": [
                ["11/15", "夕", "OCR A", "70", "", "", "", "", "", ""],
            ]
        },
    )
    order_service._save_order_ocr_cache(
        order_b["id"],
        {
            "table_rows": [
                ["11/15", "夕", "OCR B", "999", "", "", "", "", "", ""],
            ]
        },
    )

    sheet_a, error_a = order_service.get_ocr_sheet(order_a["id"])
    sheet_b, error_b = order_service.get_ocr_sheet(order_b["id"])
    assert error_a is None
    assert error_b is None
    assert sheet_a is not None
    assert sheet_b is not None
    assert sheet_a["fields"] == sheet_b["fields"]
    assert sheet_a["rows"] == sheet_b["rows"]
    with session_scope() as session:
        raw = session.get(Order, order_b["id"])
        assert raw is not None
        raw.week_code = None
    assert order_service.get_ocr_sheet(order_b["id"]) == (None, "week_unresolved")


def test_resolve_sheet_week_month_boundary_requires_explicit_period_regardless_of_row_order():
    _seed_monthly_menu_boundary_2026_01_02()
    received_at = datetime(2026, 2, 1, 9, 0, 0)
    order_lines = [{"date": date(2026, 2, 1)}]
    payload_a = {
        "table_raw": """
|日付|区分|献立|常|
|-|-|-|-|
|1/31|朝|Boundary Jan|10|
|2/1|朝|Boundary Feb|11|
""".strip()
    }
    payload_b = {
        "table_raw": """
|日付|区分|献立|常|
|-|-|-|-|
|2/1|朝|Boundary Feb|11|
|1/31|朝|Boundary Jan|10|
""".strip()
    }

    resolved_a = order_service._resolve_sheet_week_id(
        current_week_id="2026-01",
        received_at=received_at,
        order_lines=order_lines,
        ocr_payload=payload_a,
        facility_id="FAC00001",
        week_hints=["2026-01"],
    )
    resolved_b = order_service._resolve_sheet_week_id(
        current_week_id="2026-01",
        received_at=received_at,
        order_lines=order_lines,
        ocr_payload=payload_b,
        facility_id="FAC00001",
        week_hints=["2026-01"],
    )

    assert resolved_a is None
    assert resolved_b is None


def test_selected_canonical_columns_ignore_legacy_split_or_x_override(selected_history_order):
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-template-variant-priority-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    lines = [
        {
            "line_id": "line-template-variant-1",
            "date": "2099-11-15",
            "daypart": "昼",
            "menu_name": "昼メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 20,
        }
    ]
    order = _register_scoped_order(selected_history_order, payload, lines=lines, week='2099-11@2099-11-15~2099-11-21')
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "昼", "OCRノイズメニュー", "99", "", "", "", "", "", ""],
            ]
        },
    )

    original_get = config_service.get_facility_config
    cases = [
        (
            "split",
            [
                "date_mmdd",
                "daypart",
                "menu",
                "qty.regular_2f",
                "qty.regular_3f",
                "qty.soft_2f",
                "qty.soft_3f",
                "qty.mixer_2f",
                "qty.mixer_3f",
                "remarks",
            ],
            "qty.regular_2f",
        ),
        (
            "x",
            [
                "date_mmdd",
                "daypart",
                "menu",
                "qty.regular_x",
                "qty.soft_x",
                "qty.mixer_x",
                "remarks",
            ],
            "qty.regular_x",
        ),
    ]
    try:
        for _label, row_fields, qty_field in cases:
            def _mock_get(facility_id: str, _row_fields=row_fields):
                current = original_get(facility_id)
                if not current or facility_id != "FAC00001":
                    return current
                payload_cfg = dict(current)
                template = dict(payload_cfg.get("fax_template") or {})
                template["main_ocr_row_fields"] = _row_fields
                payload_cfg["fax_template"] = template
                return payload_cfg

            config_service.get_facility_config = _mock_get
            sheet, error = order_service.get_ocr_sheet(order["id"])
            assert error is None
            assert sheet is not None
            fields = sheet["fields"]
            assert "qty.regular_2f" in fields and "qty.regular_x" not in fields
            qty_idx = fields.index("qty.regular_2f")
            daypart_idx = fields.index("daypart")
            menu_idx = fields.index("menu")
            date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)

            assert all(
                "OCRノイズ" not in row[menu_idx]
                for row in sheet["rows"]
                if len(row) > menu_idx
            )
            lunch = next(
                row
                for row in sheet["rows"]
                if row[date_idx] == "11/15" and row[daypart_idx] == "昼" and row[menu_idx] == "昼メニュー"
            )
            assert lunch[qty_idx] == "20"
    finally:
        config_service.get_facility_config = original_get


def test_get_ocr_sheet_final_priority_fixed_after_repeated_apply_and_line_updates(selected_history_order):
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-repeat-priority-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order = _register_scoped_order(selected_history_order,
        payload,
        lines=[
            {
                "line_id": "line-repeat-initial",
                "date": "2099-11-15",
                "daypart": "昼",
                "menu_name": "昼メニュー",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 6,
            }
        ],
     week='2099-11@2099-11-15~2099-11-21')
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "qty.regular_3f",
        "qty.soft_2f",
        "qty.soft_3f",
        "qty.mixer_2f",
        "qty.mixer_3f",
        "remarks",
    ]
    header = ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"]

    updated_1, error_1 = order_service.apply_ocr_table(
        order["id"],
        header=header,
        rows=[["11/15", "昼", "昼メニュー", "11", "", "", "", "", "", "apply-1"]],
        ui_mode="sheet",
        fields=fields,
        row_ids=["row-repeat-1"],
    )
    assert error_1 is None
    assert updated_1 is not None
    selected_1, selected_error_1 = _save_formal_sheet(order_id=order["id"],
        sheet={"fields": fields, "header": header,
               "rows": [["11/15", "昼", "昼メニュー", "11", "", "", "", "", "", "apply-1"]],
               "row_ids": ["row-repeat-1"], "source": "edited_sheet_exact"},
        edited_by="authored-component-operator")
    assert selected_error_1 is None and selected_1 is not None
    assert order_service.update_lines(
        order["id"],
        [
            {
                "line_id": "line-repeat-update-1",
                "date": "2099-11-15",
                "daypart": "昼",
                "menu_name": "昼メニュー",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 20,
            }
        ],
    ) is True

    updated_2, error_2 = order_service.apply_ocr_table(
        order["id"],
        header=header,
        rows=[["11/15", "昼", "昼メニュー", "3", "", "", "", "", "", "apply-2"]],
        ui_mode="sheet",
        fields=fields,
        row_ids=["row-repeat-2"],
    )
    assert error_2 is None
    assert updated_2 is not None
    with session_scope() as session:
        cache = session.get(OrderOcrCache, order["id"])
        persisted_edit = deepcopy(cache.payload["_edited_ocr"])
        assert persisted_edit["latest"]["rows"][0][3] == "3"
        assert persisted_edit["selected_revision_id"] == persisted_edit["latest"]["revision_id"]
        assert [revision["rows"][0][3] for revision in persisted_edit["revisions"]] == ["11", "3"]
    selected_2, selected_error_2 = _save_formal_sheet(order_id=order["id"],
        sheet={"fields": fields, "header": header,
               "rows": [["11/15", "昼", "昼メニュー", "3", "", "", "", "", "", "apply-2"]],
               "row_ids": ["row-repeat-2"], "source": "edited_sheet_exact"},
        edited_by="authored-component-operator")
    assert selected_error_2 is None and selected_2 is not None
    assert order_service.update_lines(
        order["id"],
        [
            {
                "line_id": "line-repeat-final",
                "date": "2099-11-15",
                "daypart": "昼",
                "menu_name": "昼メニュー",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 30,
            }
        ],
    ) is True

    history, history_error = order_service.get_ocr_edit_history(order["id"])
    assert history_error is None
    assert history is not None
    revisions = history.get("revisions") or []
    assert len(revisions) == 2
    assert revisions[0]["rows"][0][3] == "11"
    assert revisions[1]["rows"][0][3] == "3"

    sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    assert sheet_error is None
    assert sheet is not None
    assert sheet["source"] == "edited_sheet_exact"
    qty_idx = sheet["fields"].index("qty.regular_2f")
    daypart_idx = sheet["fields"].index("daypart")
    menu_idx = sheet["fields"].index("menu")
    date_idx = next((idx for idx, field in enumerate(sheet["fields"]) if field.startswith("date")), 0)
    lunch = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "11/15" and row[daypart_idx] == "昼" and row[menu_idx] == "昼メニュー"
    )
    assert lunch[qty_idx] == "3"


def test_get_ocr_sheet_does_not_mutate_order_ocr_cache_timestamp(selected_history_order):
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-readonly-cache-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order = _register_scoped_order(selected_history_order,
        payload,
        lines=[
            {
                "line_id": "line-readonly-1",
                "date": "2099-11-15",
                "daypart": "朝",
                "menu_name": "朝メニュー",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 7,
            }
        ],
     week='2099-11@2099-11-15~2099-11-21')
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "朝", "朝メニュー", "7", "", "", "", "", "", ""],
            ]
        },
    )
    with session_scope() as session:
        cache_before = session.get(OrderOcrCache, order["id"])
        assert cache_before is not None
        updated_before = cache_before.updated_at

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None

    with session_scope() as session:
        cache_after = session.get(OrderOcrCache, order["id"])
        assert cache_after is not None
        updated_after = cache_after.updated_at
    assert updated_after == updated_before


def test_selected_numeric_cells_ignore_unowned_loose_note_and_unstructured_values(selected_history_order):
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-strict-numeric-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order = _register_scoped_order(selected_history_order, payload, lines=[],
        week='2099-11@2099-11-15~2099-11-21', ocr_rows=[
            ["11/15", "朝", "朝メニュー", "7", "", "", "", "", "", ""],
            ["11/15", "昼", "昼メニュー", "", "", "", "", "", "", "note 23"],
        ])
    selected_payload = order_service.get_cached_ocr_payload(order["id"])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "朝", "朝メニュー", "7", "", "", "", "", "", ""],
                ["11/15", "昼", "昼メニュー", "", "", "", "", "", "", "note 23"],
            ],
            "_table_raw_unstructured_qty": ["16"],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert order_service.get_cached_ocr_payload(order["id"]) == selected_payload
    fields = sheet["fields"]
    qty_idx = next(
        idx
        for idx, field in enumerate(fields)
        if field in {"qty.regular_2f", "qty.regular_x"}
    )
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)

    breakfast = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "11/15" and row[daypart_idx] == "朝" and row[menu_idx] == "朝メニュー"
    )
    lunch = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "11/15" and row[daypart_idx] == "昼" and row[menu_idx] == "昼メニュー"
    )
    assert breakfast[qty_idx] == "7"
    assert lunch[qty_idx] == ""


def test_get_ocr_sheet_preserves_zero_from_selected_evidence(selected_history_order):
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-zero-order-lines-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order = _register_scoped_order(selected_history_order,
        payload,
        lines=[
            {
                "line_id": "line-zero-1",
                "date": "2099-11-15",
                "daypart": "昼",
                "menu_name": "昼メニュー",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 0,
            }
        ],
     week='2099-11@2099-11-15~2099-11-21')

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["base_evidence_run_id"]
    fields = sheet["fields"]
    qty_idx = fields.index("qty.regular_2f")
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)

    lunch = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "11/15" and row[daypart_idx] == "昼" and row[menu_idx] == "昼メニュー"
    )
    assert lunch[qty_idx] == "0"


def test_selected_zero_is_not_replaced_by_unowned_payload(selected_history_order):
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-zero-payload-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order = _register_scoped_order(selected_history_order, payload, lines=[],
        week='2099-11@2099-11-15~2099-11-21',
        ocr_rows=[["11/15", "朝", "朝メニュー", "0", "", "", "", "", "", ""]])
    selected_payload = order_service.get_cached_ocr_payload(order["id"])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "朝", "朝メニュー", "0", "", "", "", "", "", ""],
            ],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert order_service.get_cached_ocr_payload(order["id"]) == selected_payload
    fields = sheet["fields"]
    qty_idx = fields.index("qty.regular_2f")
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)

    breakfast = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "11/15" and row[daypart_idx] == "朝" and row[menu_idx] == "朝メニュー"
    )
    assert breakfast[qty_idx] == "0"


@pytest.mark.parametrize("diagnostic_source", ["cache", "job"])
def test_selected_source_survives_unowned_failed_projection_diagnostics(selected_history_order, diagnostic_source):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(message_id=f"failed-projection-{diagnostic_source}",
        pdf_uri="component:authored-original", received_at=datetime(2099, 11, 15, 9),
        facility_hint="FAC00001", week_hint="2099-11")
    order = _register_scoped_order(selected_history_order, payload,
        lines=[{"line_id": "line-suppress-1", "date": "2099-11-15", "daypart": "朝",
                "menu_name": "朝メニュー", "diet_type": "regular", "area_id": "2F",
                "bag_type": "standard", "quantity_original": 30}],
        week="2099-11@2099-11-15~2099-11-21",
        ocr_rows=[["11/15", "朝", "朝メニュー", "7", "", "", "", "", "", ""]])
    selected_payload = order_service.get_cached_ocr_payload(order["id"])
    failure = {"result_state": "hard_failed", "quality_error": "sheet_column_anomaly",
        "structural_row_projection": {"projected_row_count": 30,
            "rows_with_projected_quantity": 30, "quantity_cells_copied": 120}}
    order_service._save_order_ocr_cache(order["id"], {
        "table_rows": [["11/15", "朝", "朝メニュー", "7", "", "", "", "", "", ""]],
        "metrics": {"status": "failed", **failure} if diagnostic_source == "cache" else {"status": "success"},
    })
    if diagnostic_source == "job":
        job_id = f"OCR-{order['id']}"
        create_job(job_id, input_reference="gs://ocr/reparse.json", status="failed")
        update_job(job_id, metrics=failure)
    before = _snapshot(get_engine())
    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and sheet is not None, error
    fields = sheet["fields"]
    breakfast = next(row for row in sheet["rows"]
        if row[fields.index("date_mmdd")] == "11/15"
        and row[fields.index("daypart")] == "朝"
        and row[fields.index("menu")] == "朝メニュー")
    assert breakfast[fields.index("qty.regular_2f")] == "7"
    assert sheet["base_evidence_run_id"]
    assert _read_order(order["id"])["lines"][0]["quantity_original"] == 30
    assert order_service.get_cached_ocr_payload(order["id"]) == selected_payload
    assert _snapshot(get_engine()) == before


def test_selected_numeric_table_does_not_rescue_unowned_unstructured_values(selected_history_order):
    order_service.clear_all()
    _seed_monthly_menu_custom_entries(
        month_id="2099-10",
        month_start=date(2099, 10, 1),
        entries=[
            (date(2099, 10, 15), "朝", "Menu A", 0),
            (date(2099, 10, 15), "昼", "Menu B", 1),
        ],
    )
    payload = IngestEmailPayload(
        message_id="msg-sheet-strict-numeric-ocr-table-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 10, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-10",
    )
    order = _register_scoped_order(selected_history_order, payload, lines=[],
        week='2099-10@2099-10-15~2099-10-21', ocr_rows=[
            ["10/15", "朝", "Menu A", "7", "", "", "", "", "", ""],
            ["10/15", "昼", "Menu B", "", "", "", "", "", "", "note 23"],
        ])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["10/15", "朝", "Menu A", "7", "", "", "", "", "", ""],
                ["10/15", "昼", "Menu B", "", "", "", "", "", "", "note 23"],
            ],
            "_table_raw_unstructured_qty": ["16"],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    fields = sheet["fields"]
    qty_idx = next(
        idx
        for idx, field in enumerate(fields)
        if field in {"qty.regular_2f", "qty.regular_x"}
    )
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)

    breakfast = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "10/15" and row[daypart_idx] == "朝" and row[menu_idx] == "Menu A"
    )
    lunch = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "10/15" and row[daypart_idx] == "昼" and row[menu_idx] == "Menu B"
    )
    assert breakfast[qty_idx] == "7"
    assert lunch[qty_idx] == ""


def test_selected_quantity_exposes_actual_evidence_identity_not_legacy_order_line_trace(selected_history_order):
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(message_id="msg-sheet-trace-001", pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9), facility_hint="FAC00001", week_hint="2099-11")
    order = _register_scoped_order(selected_history_order, payload,
        lines=[{"line_id": "line-trace-1", "date": "2099-11-15", "daypart": "昼",
                "menu_name": "昼メニュー", "diet_type": "regular", "area_id": "2F",
                "bag_type": "standard", "quantity_original": 20}],
        week="2099-11@2099-11-15~2099-11-21")
    captured = order_service._capture_synchronous_cache_write_input(order["id"])
    assert captured is not None
    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None and sheet is not None
    assert sheet["base_evidence_run_id"] == captured["evidence_run_id"]
    fields = sheet["fields"]
    row = next(row for row in sheet["rows"]
        if row[fields.index("date_mmdd")] == "11/15"
        and row[fields.index("daypart")] == "昼" and row[fields.index("menu")] == "昼メニュー")
    assert row[fields.index("qty.regular_2f")] == "20"
    cached = order_service._load_order_ocr_cache(order["id"])
    assert cached["_ocr_execution"]["evidence_run_id"] == captured["evidence_run_id"]
    assert order_service._extract_hakodate_target_cells_from_payload(cached)
    assert order_service._extract_hakodate_ocr_evidence_records_from_payload(cached, order_id=order["id"])


def test_archived_yomitoku_issue_metadata_does_not_override_selected_source(selected_history_order):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-sheet-yomitoku-review-001")
    diagnostic_payload = {
            "pages": [
                {
                    "page_index": 1,
                    "tables": [
                        {
                            "table_id": "p1_t1",
                            "rows": [
                                ["日付", "区分", "献立", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
                                ["", "", "", "常食", "", "", "", "", "", ""],
                                ["01/08", "昼", "Menu A", "2", "1", "3", "", "", "", ""],
                            ],
                        }
                    ],
                }
            ],
            "yomitoku_cell_issues": [
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
        }
    _assert_raw_diagnostics_do_not_override_selected_source(order["id"], diagnostic_payload, 'yomitoku_cell_issues')


def test_review_ocr_table_with_llm_persists_patch_candidates_and_uses_latest_draft_as_baseline(selected_history_order, monkeypatch):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-sheet-llm-review-loop-001")
    selected_before = order_service._capture_synchronous_cache_write_input(order["id"])["evidence"]
    order_service._save_order_ocr_cache(
        order["id"],
        {
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
                                ["01/08", "昼", "Menu A", "2", "1", "3", "", "", "", "first-pass"],
                            ],
                        }
                    ],
                }
            ],
            "table_raw": "|日付|区分|メニュー|常食2F|常食3F|軟菜2F|軟菜3F|ミキサー2F|ミキサー3F|備考|\n|---|---|---|---|---|---|---|---|---|---|\n|01/08|昼|Menu A|2|1|3||||first-pass|",
            "cell_issues": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "source_row_index": 0,
                    "column_index": 3,
                    "field": "qty.regular_2f",
                    "issue_code": "merged_numeric_cell",
                    "severity": "high",
                    "source": "yomitoku_structured",
                }
            ],
        },
    )

    captured_prompts: list[dict[str, str]] = []
    response_index = {"value": 0}

    review_batches = [
        {
            "facility_name": "Test Facility",
            "date_strings": ["1/8"],
            "rows": [
                {
                    "date_mmdd": "01/08",
                    "daypart": "昼",
                    "menu": "Menu A",
                    "qty.regular_2f": "5",
                    "qty.regular_3f": "1",
                    "qty.soft_2f": "3",
                    "qty.soft_3f": "",
                    "qty.mixer_2f": "",
                    "qty.mixer_3f": "",
                    "remarks": "first-pass",
                }
            ],
            "table_raw": "|日付|区分|メニュー|常食2F|常食3F|軟菜2F|軟菜3F|ミキサー2F|ミキサー3F|備考|\n|---|---|---|---|---|---|---|---|---|---|\n|01/08|昼|Menu A|5|1|3||||first-pass|",
            "llm_review": {
                "status": "verified",
                "needs_more_review": False,
                "notes": "updated regular 2F from 2 to 5",
                "issues": [],
            },
        },
        {
            "facility_name": "Test Facility",
            "date_strings": ["1/8"],
            "rows": [
                {
                    "date_mmdd": "01/08",
                    "daypart": "昼",
                    "menu": "Menu A",
                    "qty.regular_2f": "7",
                    "qty.regular_3f": "1",
                    "qty.soft_2f": "3",
                    "qty.soft_3f": "",
                    "qty.mixer_2f": "",
                    "qty.mixer_3f": "",
                    "remarks": "first-pass",
                }
            ],
            "table_raw": "|日付|区分|メニュー|常食2F|常食3F|軟菜2F|軟菜3F|ミキサー2F|ミキサー3F|備考|\n|---|---|---|---|---|---|---|---|---|---|\n|01/08|昼|Menu A|7|1|3||||first-pass|",
            "llm_review": {
                "status": "verified",
                "needs_more_review": False,
                "notes": "updated regular 2F from 5 to 7",
                "issues": [],
            },
        },
    ]

    original_bytes = pathlib.Path(order["document"]).read_bytes()

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):
        assert pdf_bytes == original_bytes
        assert facility_id == "FAC00001"
        assert preferred_template_id is None
        captured_prompts.append(
            {
                "system": str(template.get("gemini_ocr_prompt") or template.get("openai_ocr_prompt") or ""),
                "user": str(
                    template.get("gemini_ocr_user_prompt")
                    or template.get("openai_ocr_user_prompt")
                    or ""
                ),
            }
        )
        current = review_batches[response_index["value"]]
        response_index["value"] += 1
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026-01-08"],
            table_rows=[],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            raw_text=json.dumps(current, ensure_ascii=False),
            provider_debug={"provider": "gemini", "model": "review-model-v1"},
        )

    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)

    initial_input = order_service._capture_synchronous_cache_write_input(order["id"])
    assert initial_input is not None and initial_input["draft_id"]
    updated_1, error_1 = order_service.review_ocr_table_with_llm(order["id"], provider="gemini")
    assert error_1 is None
    assert updated_1 is not None
    assert updated_1["llm_review"]["provider"] == "gemini"
    assert updated_1["llm_review"]["model"] == "review-model-v1"
    assert updated_1["llm_review"]["baseline_revision_id"] == initial_input["draft_id"]
    assert updated_1["llm_review"]["baseline_source"] == "current_draft"
    assert updated_1["llm_review"]["summary"]["status"] == "verified"
    assert updated_1["llm_review"]["needs_more_review"] is False
    assert updated_1["llm_review"]["output_payload"]["rows"][0]["qty.regular_2f"] == "5"
    first_candidate = updated_1["patch_candidate"]
    assert first_candidate["candidate_state"] == "proposed"
    assert first_candidate["proposed_draft_sheet_json"]["rows"][0][3] == "5"

    history_1, history_error_1 = order_service.get_ocr_edit_history(order["id"])
    assert history_error_1 is None
    assert history_1 is not None
    assert history_1["latest"] is None and history_1["revisions"] == []
    assert history_1["raw_output_source"] == "order_ocr_evidence_runs.payload_json"
    assert history_1["raw_output"] == selected_before["payload_json"]
    assert order_service.get_ocr_evidence_run(selected_before["id"]) == selected_before

    apply_result, apply_error = order_service.apply_patch_candidate_to_draft(
        order["id"],
        candidate_id=first_candidate["id"],
        applied_by="test",
    )
    assert apply_error is None
    assert apply_result is not None
    applied_draft = apply_result["draft"]
    assert applied_draft["latest_patch_candidate_id"] == first_candidate["id"]
    assert applied_draft["draft_sheet_json"]["rows"][0][3] == "5"
    for operation, options in (
        (order_workflow_v2_service.run_sheet_anomaly_review, {"use_llm": False}),
        (order_workflow_v2_service.confirm_sheet_review, {}),
    ):
        current, error = order_workflow_v2_service.get_workflow_readonly(order["id"])
        assert error is None
        reviewed, error = operation(order["id"], sheet=applied_draft["draft_sheet_json"],
            expected_revision=current["revision"], **options)
        assert error is None and reviewed is not None
    saved_patch, error = _save_formal_sheet(order_id=order["id"],
        sheet=applied_draft["draft_sheet_json"], edited_by="test")
    assert error is None and saved_patch is not None
    selected_after_save = order_service._capture_synchronous_cache_write_input(order["id"])["draft"]
    assert selected_after_save["draft_sheet_json"]["rows"][0][3] == "5"

    updated_2, error_2 = order_service.review_ocr_table_with_llm(order["id"], provider="gemini")
    assert error_2 is None
    assert updated_2 is not None
    assert updated_2["llm_review"]["baseline_revision_id"] == selected_after_save["id"]
    assert updated_2["llm_review"]["baseline_source"] == "current_draft"
    assert updated_2["llm_review"]["needs_more_review"] is False
    second_candidate = updated_2["patch_candidate"]
    assert second_candidate["proposed_draft_sheet_json"]["rows"][0][3] == "7"

    history_2, history_error_2 = order_service.get_ocr_edit_history(order["id"])
    assert history_error_2 is None
    assert history_2 is not None
    assert history_2["latest"] is None and history_2["revisions"] == []
    assert history_2["raw_output_source"] == "order_ocr_evidence_runs.payload_json"
    assert history_2["raw_output"] == selected_before["payload_json"]
    assert order_service.get_ocr_evidence_run(selected_before["id"]) == selected_before

    assert "Previous yomitoku/LLM markdown" in captured_prompts[0]["user"]
    assert "Previous yomitoku/LLM structured tables/cells" in captured_prompts[0]["user"]
    assert "Current baseline source: current_draft" in captured_prompts[0]["user"]
    assert '"qty.regular_2f": "2"' in captured_prompts[0]["user"]
    assert f"Current baseline revision_id: {selected_after_save['id']}" in captured_prompts[1]["user"]
    assert "Current baseline source: current_draft" in captured_prompts[1]["user"]
    assert '"qty.regular_2f": "5"' in captured_prompts[1]["user"]


def test_review_ocr_table_with_llm_rejects_invalid_overwrite_and_keeps_latest_baseline(selected_history_order, monkeypatch):
    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-sheet-llm-review-reject-001")
    selected_before = order_service._capture_synchronous_cache_write_input(order["id"])["evidence"]
    order_service._save_order_ocr_cache(
        order["id"],
        {
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
                                ["01/08", "昼", "Menu A", "2", "1", "3", "", "", "", "first-pass"],
                            ],
                        }
                    ],
                }
            ],
            "table_raw": "|日付|区分|メニュー|常食2F|常食3F|軟菜2F|軟菜3F|ミキサー2F|ミキサー3F|備考|\n|---|---|---|---|---|---|---|---|---|---|\n|01/08|昼|Menu A|2|1|3||||first-pass|",
        },
    )

    original_bytes = pathlib.Path(order["document"]).read_bytes()

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):
        assert pdf_bytes == original_bytes
        assert facility_id == "FAC00001"
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026-01-08"],
            table_rows=[],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            raw_text=json.dumps(
                {
                    "facility_name": "Test Facility",
                    "date_strings": ["1/8"],
                    "rows": [
                        {
                            "date_mmdd": "01/08",
                            "daypart": "昼",
                            "menu": "Menu A",
                            "qty.regular_2f": "2",
                            "qty.regular_3f": "1",
                            "qty.soft_2f": "3",
                            "qty.soft_3f": "",
                            "qty.mixer_2f": "",
                            "qty.mixer_3f": "",
                            "remarks": "first-pass",
                        }
                    ],
                    "table_raw": "|日付|区分|メニュー|常食2F|常食3F|軟菜2F|軟菜3F|ミキサー2F|ミキサー3F|備考|\n|---|---|---|---|---|---|---|---|---|---|\n|01/08|昼|Menu A|2|1|3||||first-pass|",
                    "llm_review": {
                        "status": "needs_review",
                        "needs_more_review": True,
                        "notes": "manual check required",
                        "issues": [
                            {
                                "issue_id": "iss-reject-1",
                                "row_id": "row-1",
                                "field": "qty.regular_2f",
                                "issue_code": "misread_quantity",
                                "status": "needs_review",
                                "page_index": 1,
                                "table_id": "p1_t1",
                                "current_text": "2",
                                "confidence": 0.88,
                                "evidence": "image still suggests a changed digit",
                                "reason": "manual_check_required",
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            ),
            provider_debug={"provider": "gemini", "model": "review-model-v1"},
        )

    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)

    updated, error = order_service.review_ocr_table_with_llm(order["id"], provider="gemini")

    assert error is None
    assert updated is not None
    assert updated["llm_review"]["baseline_source"] == "current_draft"
    assert updated["llm_review"]["needs_more_review"] is True
    assert len(updated["llm_review"]["applied_overwrites"]) == 1
    assert updated["llm_review"]["applied_overwrites"][0]["field"] == "remarks"
    assert updated["llm_review"]["applied_overwrites"][0]["new_text"] == "first-pass"
    assert updated["llm_review"]["issues"][0]["issue_code"] == "misread_quantity"
    patch_candidate = updated["patch_candidate"]
    assert patch_candidate["candidate_state"] == "proposed"
    assert patch_candidate["issues_json"][0]["issue_code"] == "misread_quantity"

    history, history_error = order_service.get_ocr_edit_history(order["id"])
    assert history_error is None
    assert history is not None
    assert history["latest"] is None and history["revisions"] == []
    assert history["raw_output_source"] == "order_ocr_evidence_runs.payload_json"
    assert history["raw_output"] == selected_before["payload_json"]
    assert order_service.get_ocr_evidence_run(selected_before["id"]) == selected_before


def test_unowned_corrected_pdf_reference_cannot_replace_selected_review_source(selected_history_order, monkeypatch):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    order = _seed_order(selected_history_order, message_id="msg-sheet-llm-review-corrected-001")
    captured = order_service._capture_synchronous_cache_write_input(order["id"])
    assert captured is not None
    original = pathlib.Path(order["document"]).read_bytes()
    unowned = {"combined": {"corrected_pdf": "gs://bucket/corrected.pdf"},
        "table_raw": "|01/08|昼|Menu A|5|1|3||||first-pass|"}
    before = _snapshot(get_engine())
    def forbidden(*args, **kwargs):
        pytest.fail("An unowned corrected-PDF URI must not reach external recognition")
    monkeypatch.setattr(order_service, "extract_fax_data", forbidden)
    assert order_service._save_order_ocr_cache(order["id"], unowned) is None
    with pytest.raises(ValueError, match="corrected_pdf_receipt_required"):
        order_service._resolve_llm_review_pdf_bytes(document_uri=order["document"],
            payload=unowned, requested_variant="corrected",
            selected_pdf_receipt=captured["selected_pdf_receipt"])
    assert pathlib.Path(order["document"]).read_bytes() == original
    assert _snapshot(get_engine()) == before
    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    fields = sheet["fields"]
    row = next(row for row in sheet["rows"] if row[fields.index("menu")] == "Menu A")
    assert row[fields.index("qty.regular_2f")] == "2"


def test_selected_facility_schema_and_quantities_ignore_unowned_column_drift(selected_history_order):
    config_service.reload_configs()
    order_service.clear_all()
    _seed_monthly_menu_custom_entries(
        month_id="2026-04",
        month_start=date(2026, 4, 1),
        entries=[
            (date(2026, 4, 5), "朝", "豚肉の卵とじ", 0),
            (date(2026, 4, 5), "朝", "いんげんのカニ和え", 1),
            (date(2026, 4, 5), "昼", "サワラの西京焼き 添)小松菜", 2),
            (date(2026, 4, 5), "昼", "じゃが芋の煮物", 3),
        ],
    )
    payload = IngestEmailPayload(
        message_id="msg-ocr-sheet-projection-drift-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 4, 5, 9, 0, 0),
        facility_hint="FAC00005",
        week_hint="2026-04",
    )
    order = _register_scoped_order(selected_history_order, payload, lines=[],
        week='2026-04@2026-04-05~2026-04-11', ocr_rows=[
            ["04/05", "朝", "", "豚肉の卵とじ", "0", "0", "", "", "", "", ""],
            ["04/05", "朝", "", "いんげんのカニ和え", "0", "0", "", "", "", "", ""],
            ["04/05", "昼", "", "サワラの西京焼き 添)小松菜", "58", "2", "", "", "", "", ""],
            ["04/05", "昼", "", "じゃが芋の煮物", "58", "2", "", "", "", "", ""],
        ])
    original = deepcopy(order_service._load_order_ocr_cache(order["id"]))
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "template_id": "fax_layout_soft_packaging_forbidden_v1",
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": 6,
                    "col_count": 10,
                    "rows": [
                        ["", "日付", "区 分", "", "献立", "##", "44日", "禁食【軟菜】", "", "備考欄"],
                        ["", "", "", "", "", "", "", "肉禁", "魚禁", ""],
                        ["", "4/5\n(日)", "ま", "...", "豚肉の卵とじ", "0", "0", "", "", ""],
                        ["", "", "", "***", "いんげんのカニ和え", "0", "0", "", "", ""],
                        ["", "", "口", "VT", "サワラの西京焼き 添)小松菜", "58", "2", "", "", ""],
                        ["", "", "", "OK", "じゃが芋の煮物", "58", "2", "", "", ""],
                    ],
                }
            ],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert isinstance(sheet, dict)
    assert list(sheet.get("fields") or []) == [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.soft_x",
        "qty.regular_bag_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.change_1_x",
        "qty.change_2_x",
        "remarks",
    ]
    assert list(sheet.get("header") or []) == [
        "日付",
        "区分",
        "メニュー",
        "軟菜",
        "袋分け",
        "肉禁",
        "魚禁",
        "変更1",
        "変更2",
        "備考欄",
    ]
    fields = list(sheet.get("fields") or [])
    date_idx = next(idx for idx, field in enumerate(fields) if str(field).startswith("date"))
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    first_rows = list(sheet.get("rows") or [])[:4]
    assert first_rows[0][date_idx] == "04/05"
    assert first_rows[0][daypart_idx] == "朝"
    assert first_rows[0][menu_idx] == "豚肉の卵とじ"
    assert first_rows[0][3] == "0"
    assert first_rows[0][4] == "0"
    assert first_rows[1][daypart_idx] == "朝"
    assert first_rows[1][menu_idx] == "いんげんのカニ和え"
    assert [row[3:5] for row in first_rows] == [["0", "0"], ["0", "0"], ["58", "2"], ["58", "2"]]
    assert order_service._load_order_ocr_cache(order["id"]) == original
    assert "sheet_structural_projection_corrupted" not in list(sheet.get("warnings") or [])


def test_formal_menu_parent_cannot_be_deleted_while_physical_rows_exist():
    from sqlalchemy.exc import IntegrityError
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_service.clear_all()
    menus = [
        (date(2026, 4, 5), "朝", "豚肉の卵とじ", 0),
        (date(2026, 4, 5), "朝", "いんげんのカニ和え", 1),
        (date(2026, 4, 5), "昼", "サワラの西京焼き 添)小松菜", 2),
        (date(2026, 4, 5), "昼", "じゃが芋の煮物", 3),
    ]
    _seed_monthly_menu_custom_entries(month_id="2026-04", month_start=date(2026, 4, 1), entries=menus)
    before = _snapshot(get_engine())
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2026-04"))
    assert _snapshot(get_engine()) == before
    with session_scope() as session:
        entries = session.query(MonthlyMenuEntry).filter_by(monthly_menu_id="2026-04").order_by(MonthlyMenuEntry.slot_index).all()
        assert [(entry.menu_date, entry.daypart, entry.name, entry.slot_index) for entry in entries] == menus
        assert session.get(MonthlyMenu, "2026-04") is not None
