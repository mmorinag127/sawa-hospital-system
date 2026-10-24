import pathlib
import sys
from datetime import date, datetime

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.models.order import OrderLine  # noqa: E402
from src.models.order_sheet_draft import OrderSheetDraft  # noqa: E402
from src.services import draft_sheet_service, order_current_state_service, order_service  # noqa: E402
from tests.helpers.authored_sheet_case import authored_sheet_case, prepare_rerun, publish_current, save_draft  # noqa: E402,F401
from tests.contract.test_orders_ocr_sheet_history_api import history_case  # noqa: E402,F401
from tests.unit.test_disabled_facility_columns import _snapshot  # noqa: E402

pytestmark = pytest.mark.usefixtures("isolated_formal_schema")






def _sample_payload(quantity: str = "3") -> dict:
    return {
        "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
        "pages": [
            {
                "page_index": 1,
                "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                "figure_uris": [],
            }
        ],
        "table_raw": f"|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|03/22|朝|Menu A|{quantity}|",
        "tables": [
            {
                "table_id": "p1_t1",
                "page_index": 1,
                "rows": [["日付", "区分", "メニュー", "常食2F"], ["03/22", "朝", "Menu A", quantity]],
            }
        ],
        "template_resolution": {
            "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "blocked": False,
            "blocked_reasons": [],
        },
        "table_box": [0.1, 0.2, 0.9, 0.8],
        "grid_column_edges": [0.1, 0.5, 0.9],
        "grid_row_edges": [0.2, 0.4, 0.8],
    }










def _append_sheet_revision(
    *,
    order_id: str,
    fields: list[str],
    header: list[str],
    rows: list[list[str]],
    row_ids: list[str],
    revision_meta: dict,
) -> None:
    digest = order_service._sheet_digest(
        fields=fields,
        header=header,
        rows_payload=rows,
        row_ids=row_ids,
    )
    order_service._append_edited_ocr_revision(
        order_id=order_id,
        ui_mode="sheet",
        fields=fields,
        header=header,
        rows_payload=rows,
        row_ids=row_ids,
        before_digest=digest,
        after_digest=digest,
        revision_meta=revision_meta,
        synchronous_write_input=order_service._capture_synchronous_cache_write_input(order_id),
    )


def test_build_initial_sheet_draft_prefers_latest_saved_draft(authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=3)
    order = {"id": case.id}

    saved = save_draft(case, {
            "order_id": order["id"],
            "source": "manual_draft",
            "fields": ["col1"],
            "header": ["数量"],
            "rows": [["9"]],
            "row_ids": ["row-1"],
        })

    with case.readonly():
        built = draft_sheet_service.build_initial_sheet_draft(order["id"])
    assert draft_sheet_service.get_selected_sheet_draft(case.id)["id"] == saved["id"]

    assert isinstance(built, dict)
    assert built["source"] == "manual_draft"
    assert built["rows"] == [["9"]]


def test_initial_draft_uses_actual_selected_native_evidence_five(authored_sheet_case):
    case = authored_sheet_case(quantity=3, cell_values="5", include_remarks=False)
    job = order_service.get_ocr_job(case.job)
    assert job["status"] == "done"
    assert job["metrics"]["rerun_terminal_receipt"]["evidence_run_id"] == case.evidence
    with case.readonly():
        built = draft_sheet_service.build_initial_sheet_draft(case.id)
    assert isinstance(built, dict)
    assert built == case.selected_sheet
    assert built["base_evidence_run_id"] == case.evidence
    assert built["source"] == "workflow_v2_selected_ocr_projection"
    assert built["rows"] == [["03/22", "朝", "Menu A", "5"]]
    assert order_service.get_order_by_id(case.id)["week_value"] == "2026-03@2026-03-22~2026-03-28"


UNSELECTED_BOOTSTRAP_CASES = (
    ("refuse_unselected_legacy_evidence_payload", "test_build_initial_sheet_draft_from_latest_evidence_run", "draft", _sample_payload('5')),
    ("refuse_unselected_edited_cache_revision", "test_build_initial_sheet_draft_refuses_legacy_cache_revision_as_current_source", "draft", {'table_raw': '|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|03/22|朝|Menu A|3|', '_edited_ocr': {'latest': {'ui_mode': 'sheet', 'fields': ['date_mmdd', 'daypart', 'menu', 'qty'], 'header': ['日付', '区分', 'メニュー', '常食2F'], 'rows': [['03/22', '朝', 'Menu A', '8']], 'row_ids': ['row-a']}}}),
    ("refuse_semantic_bootstrap_without_selected_source", "test_order_service_build_initial_sheet_draft_prefers_semantic_sheet", "order", None),
    ("refuse_warning_only_bootstrap_without_selected_source", "test_order_service_build_initial_sheet_draft_keeps_semantic_sheet_when_only_evidence_recovery_warning_remains", "order", None),
    ("refuse_recoverable_bootstrap_without_selected_source", "test_build_initial_sheet_draft_uses_recoverable_semantic_payload", "order", None),
    ("refuse_weekly_warning_bootstrap_without_selected_source", "test_build_initial_sheet_draft_keeps_semantic_shell_when_weekly_menu_warning_remains", "order", None),
    ("refuse_empty_shell_bootstrap_without_selected_source", "test_build_initial_sheet_draft_keeps_blocked_empty_semantic_shell", "order", None),
    ("refuse_structured_payload_bootstrap_without_selected_source", "test_build_initial_sheet_draft_keeps_blocked_shell_when_menu_missing_even_if_structured_rows_exist", "order", _sample_payload('7')),
    ("refuse_current_structured_payload_without_selected_source", "test_get_current_sheet_context_keeps_blocked_shell_when_menu_missing_even_if_structured_rows_exist", "current", _sample_payload('9')),
    ("refuse_current_wrapped_payload_without_selected_source", "test_get_current_sheet_context_keeps_blocked_shell_for_wrapped_raw_output_when_menu_missing", "current", {**_sample_payload('9'), 'raw_output': _sample_payload('9'), 'capabilities': {'step2_view_ready': True, 'step2_edit_ready': True, 'semantic_shell_only': True}}),
)


def test_unselected_legacy_bootstrap_inputs_never_create_successful_sheet(authored_sheet_case):
    from src.models.menu import MonthlyMenu, MonthlyMenuEntry, MonthlyMenuItem
    from src.models.order_ocr_cache import OrderOcrCache

    case = authored_sheet_case(quantity=3, include_remarks=False, produce=False)
    case.queue()
    with session_scope() as session:
        session.query(MonthlyMenuEntry).delete()
        session.query(MonthlyMenuItem).delete()
        session.query(MonthlyMenu).delete()
    for case_id, old_name, kind, payload in UNSELECTED_BOOTSTRAP_CASES:
        if payload is None:
            with session_scope() as session:
                session.query(OrderOcrCache).filter_by(order_id=case.id).delete()
        else:
            _store_unowned_overlay(case, payload)
        with case.readonly():
            if kind == "draft":
                with pytest.raises(ValueError, match="^selected_ocr_required$"):
                    draft_sheet_service.build_initial_sheet_draft(case.id)
            elif kind == "order":
                with pytest.raises(ValueError, match="^selected_ocr_required$"):
                    order_service.build_initial_sheet_draft(case.id)
            else:
                current = order_service.get_current_sheet_context(case.id)
                assert isinstance(current, dict), case_id
                assert current["draft_id"] is None
                assert current["draft_payload"] is None
                assert current["rows"] == []
                assert current["blockers"] == ["selected_ocr_required"]
            diagnostics = order_service._build_monthly_menu_diagnostics(
                week_id="2026-03@2026-03-22~2026-03-28", facility_id=case.facility)
            assert diagnostics["order_codes"] == ["monthly_menu_object_missing"]
            assert diagnostics["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"
            assert order_service.get_latest_ocr_evidence_run(case.id, backfill_from_cache=False) is None
            assert draft_sheet_service.get_selected_sheet_draft(case.id) is None
        case.observations.append({"case_id": case_id, "former_node": old_name})
        with session_scope() as session:
            cached = session.get(OrderOcrCache, case.id)
            if payload is None:
                assert cached is None
            else:
                assert isinstance(cached, OrderOcrCache)
                assert cached.payload == payload








@pytest.mark.parametrize("case_id,quantity_header,remove_menu", [
    ("retain_generic_over_semantic_reprojection", "常食2F", False),
    ("retain_generic_over_recoverable_reprojection", "常食", False),
    ("retain_generic_when_physical_menu_missing", "常食", True),
], ids=["retain_generic_over_semantic_reprojection",
        "retain_generic_over_recoverable_reprojection", "retain_generic_when_physical_menu_missing"])
def test_selected_generic_saved_values_are_not_replaced_by_semantic_refresh(
    authored_sheet_case, case_id, quantity_header, remove_menu,
) -> None:
    from src.models.menu import MonthlyMenuEntry

    case = authored_sheet_case(quantity=3, include_remarks=False)
    assert case.selected_sheet["fields"][:3] == ["date_mmdd", "daypart", "menu"]
    assert "qty.regular_2f" in case.selected_sheet["fields"]
    assert case.selected_sheet["rows"][0][:3] == ["03/22", "朝", "Menu A"]
    assert str(case.selected_sheet["rows"][0][3]) == "3"
    sheet = {
        "order_id": case.id, "source": "ocr_evidence",
        "fields": ["col1", "col2", "col3", "col4"],
        "header": ["日付", "区分", "メニュー", quantity_header],
        "rows": [["03/22", "朝", "Menu A", "5"]], "row_ids": ["row-1"],
    }
    saved = save_draft(case, sheet)
    assert isinstance(saved, dict)
    if remove_menu:
        with session_scope() as session:
            session.query(MonthlyMenuEntry).delete()
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id,
            refresh_draft_from_semantic=True, upgrade_generic_from_sheet=True,
            backfill_from_revision=False)
    assert isinstance(current, dict), case_id
    assert current["draft_id"] == saved["id"]
    assert current["draft_payload"] == sheet
    for key in ("fields", "header", "rows", "row_ids"):
        assert current[key] == sheet[key], (case_id, key)
        assert current["draft_record"]["draft_sheet_json"][key] == sheet[key], (case_id, key)
    assert current["source"] == "ocr_evidence"
    assert current["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"
    assert draft_sheet_service.get_selected_sheet_draft(case.id)["draft_sheet_json"] == sheet
    if remove_menu:
        assert current["sheet_unavailable"] is True
        blocker_tokens = set(current.get("blockers") or []) | set(
            (current.get("menu_diagnostics") or {}).get("order_codes") or [])
        assert "monthly_menu_object_missing" in blocker_tokens or "menu_entries_missing" in blocker_tokens
        with case.readonly():
            payload, error = order_service.get_ocr_sheet(case.id)
        assert payload is None
        assert error in blocker_tokens














def _fac00004_owned_source(history_case, *, rows, menu_entries, lines,
    period="2026-04@2026-04-26~2026-04-30", received=datetime(2026, 4, 26, 9)):
    from src.services import hakodate_assignment_service
    fields = ["date_mmdd", "daypart", "aux.col_2", "menu",
        "qty.regular_x", "qty.daycare_x", "qty.staff_x", "qty.no_meat_x",
        "qty.no_fish_x", "qty.no_fried_x", "qty.change_1_x", "remarks"]
    header = ["日付", "区分", "副区分", "メニュー", "常食", "通所", "職員",
        "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"]
    roles = ["date", "daypart", "aux", "menu_name",
        "quantity", "quantity", "quantity", "quantity", "quantity", "quantity", "quantity", "note"]
    columns = [{"index": index, "source_index": index, "name": field,
                "header": header[index], "role": roles[index]} for index, field in enumerate(fields)]
    for column in columns:
        if column["role"] == "quantity":
            column.update(diet_type=column["name"][4:-2], area_id="X")
    assert len(rows) == len(menu_entries)
    declared = {}
    for row, entry in zip(rows, menu_entries, strict=True):
        assert row[:4] == [entry["menu_date"].strftime("%m/%d"), entry["daypart"],
                           entry["category"], entry["name"]]
        identity = (entry["menu_date"].isoformat(), order_service._normalize_daypart_key(entry["daypart"]),
                    hakodate_assignment_service._normalize_slot_text(entry["name"]))
        assert identity not in declared, "duplicate authored menu identity"
        declared[identity] = row
    case = history_case(facility="FAC00004", week=period, period=period,
        received=received, lines=lines, columns=columns,
        menu_entries=menu_entries)
    def recognize(region):
        values = set()
        for target in region["logical_targets"]:
            identity = (str(target["date"]), order_service._normalize_daypart_key(target["daypart"]),
                        hakodate_assignment_service._normalize_slot_text(target["menu_name"]))
            assert identity in declared, "physical target lacks an explicit authored menu"
            row = declared[identity]
            from tests.helpers.authored_cell_values import declared_field
            values.add(row[fields.index(declared_field(target, fields))])
        assert len(values) == 1, "a physical cell cannot merge different declared values"
        return values.pop()
    source = case.produce(cell_values=recognize)
    assert source["sheet"]["fields"] == fields
    assert source["sheet"]["header"] == header
    assert source["sheet"]["rows"] == rows
    return case, source, fields, header


def _legacy_aux_target_refused(case, value):
    from src.services.hakodate_ocr_evidence_service import authored_cell_role
    target = {"role": "aux", "field": "aux.col_4", "semantic_field": "aux.col_4",
              "worksheet_col": 5, "field_label": "合計", "ocr_text": value}
    with case.readonly():
        with pytest.raises(ValueError, match="^hakodate_target_role_unresolved$"):
            authored_cell_role(target)
    assert target["ocr_text"] == value


@pytest.mark.parametrize("saved_has_aux", [False, True], ids=[
    "retain_saved_schema_without_aux", "retain_saved_aux_blanks_with_matching_schema",
])
def test_owned_aux_source_does_not_overwrite_selected_operator_schema_or_values(history_case, saved_has_aux):
    period = "2026-04@2026-04-26~2026-04-30"
    original_row = ["04/26", "朝", "主", "鶏じゃが", "66", "", "", "", "", "", "", ""]
    lines = [{"date": date(2026, 4, 26), "daypart": "朝", "menu_name": "鶏じゃが",
        "diet_type": "regular", "area_id": "X", "bag_type": "standard", "quantity_original": 3}] if saved_has_aux else []
    case, source, fields, header = _fac00004_owned_source(history_case,
        rows=[original_row], lines=lines, menu_entries=[{
            "id": "aux-april-26", "monthly_menu_id": "2026-04",
            "menu_date": date(2026, 4, 26), "daypart": "朝", "name": "鶏じゃが",
            "category": "主", "slot_index": 0, "diet_type": "regular"}])
    rebuilt = source["sheet"]
    assert isinstance(rebuilt, dict)
    assert rebuilt["fields"] == fields
    assert rebuilt["header"] == header
    assert rebuilt["rows"] == [original_row]
    assert rebuilt["rows"][0][:6] == ["04/26", "朝", "主", "鶏じゃが", "66", ""]
    _legacy_aux_target_refused(case, "67")
    if saved_has_aux:
        saved_fields = ["date_mmdd", "daypart", "aux.col_2", "menu", "aux.col_4",
            "qty.regular_x", "qty.daycare_x", "qty.staff_x", "qty.no_meat_x",
            "qty.no_fish_x", "qty.no_fried_x", "qty.change_1_x", "remarks"]
        saved_header = ["日付", "区分", "副区分", "メニュー", "合計", "常食", "通所", "職員",
            "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"]
        saved_rows = [["04/26", "朝", "", "鶏じゃが", "", "9", "4", "", "", "", "", "", ""]]
    else:
        saved_fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.daycare_x",
            "qty.staff_x", "qty.no_meat_x", "qty.no_fish_x", "qty.no_fried_x", "qty.change_1_x", "remarks"]
        saved_header = ["日付", "区分", "メニュー", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"]
        saved_rows = [["04/26", "朝", "鶏じゃが", "9", "4", "", "", "", "", "", ""]]
    sheet = {"order_id": case.id, "source": "weekly_menu+ocr_payload", "facility_id": "FAC00004",
        "resolved_week_id": period, "fields": saved_fields, "header": saved_header,
        "rows": saved_rows, "row_ids": ["row-1"], "warnings": ["sheet_quantity_column_unmapped"]}
    saved_draft = save_draft(case, sheet)
    assert isinstance(saved_draft, dict)
    published = publish_current(case)
    assert published["draft_id"] == saved_draft["id"]
    latest = draft_sheet_service.get_selected_sheet_draft(case.id)
    assert isinstance(latest, dict)
    captured = order_service._capture_synchronous_cache_write_input(case.id)
    assert isinstance(captured, dict)
    with case.readonly():
        rebase_required, replacement = order_service._draft_record_requires_current_sheet_semantic_rebase(
            case.id, latest)
        refreshed = order_service._rebase_draft_record_to_facility_schema(
            case.id, latest, edited_by="current-sheet-refresh", synchronous_write_input=captured)
        current = order_service.get_current_sheet_context(case.id)
    assert rebase_required is False
    assert replacement is None
    refreshed_payload = refreshed.get("draft_sheet_json") if isinstance(refreshed, dict) else None
    assert isinstance(refreshed_payload, dict)
    assert refreshed_payload["fields"] == saved_fields
    assert refreshed_payload["header"] == saved_header
    assert refreshed_payload["rows"] == saved_rows
    assert current["draft_payload"] == sheet
    assert current["rows"] == saved_rows
    assert current["row_ids"] == ["row-1"]
    assert order_current_state_service.get_current_state_payload(case.id) == published




def test_get_ocr_sheet_preserves_formally_saved_quantity_and_aux_display(history_case) -> None:
    original_rows = [["04/26", "朝", "主", "大豆のトマト煮", "", "", "", "", "", "", "", ""],
        ["04/26", "朝", "副①", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "", "", "", "", "", "", "", ""]]
    case, source, native_fields, native_header = _fac00004_owned_source(history_case,
        rows=original_rows, menu_entries=[{"id": "owned-aux-main", "monthly_menu_id": "2026-04",
        "menu_date": date(2026, 4, 26), "daypart": "朝", "name": "大豆のトマト煮",
        "category": "主", "slot_index": 0, "diet_type": "regular"},
        {"id": "owned-aux-side", "monthly_menu_id": "2026-04",
        "menu_date": date(2026, 4, 26), "daypart": "朝", "name": "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ",
        "category": "副①", "slot_index": 1, "diet_type": "regular"}], lines=[])
    _legacy_aux_target_refused(case, "70")
    order = {"id": case.id}
    fields = ["date_mmdd", "daypart", "aux.col_2", "menu", "aux.col_4",
        "qty.regular_x", "qty.daycare_x", "qty.staff_x", "qty.no_meat_x",
        "qty.no_fish_x", "qty.no_fried_x", "qty.change_1_x", "remarks"]
    header = ["日付", "区分", "副区分", "メニュー", "合計", "常食", "通所", "職員",
        "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"]
    revision_rows = [['04/26', '朝', '', '大豆のトマト煮', '', '70', '', '', '', '', '', '', ''], ['04/26', '朝', '', '胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ', '', '70', '', '', '', '', '', '', '']]
    _append_sheet_revision(order_id=order['id'], fields=fields, header=header, rows=[['04/26', '朝', '', '大豆のトマト煮', '', '70', '', '', '', '', '', '', ''], ['04/26', '朝', '', '胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ', '', '70', '', '', '', '', '', '', '']], row_ids=['ocr-row-1', 'ocr-row-2'], revision_meta={'sheet_save_mode': 'applied', 'reparse_applied': True, 'provider': 'gemini', 'llm_assist': True})
    history, history_error = order_service.get_ocr_edit_history(case.id)
    assert history_error is None
    assert any(item["rows"] == revision_rows for item in history["revisions"])
    edited_rows = [["04/26", "朝", "主", "大豆のトマト煮", "70", "70", "", "", "", "", "", "", ""],
        ["04/26", "朝", "副①", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "70", "70", "", "", "", "", "", "", ""]]
    edited = {**source["sheet"], "fields": fields, "header": header, "rows": edited_rows,
              "row_ids": ["ocr-row-1", "ocr-row-2"]}
    saved = save_draft(case, edited)
    assert saved["base_evidence_run_id"] == case.evidence
    with case.readonly():
        sheet, error = order_service.get_ocr_sheet(order["id"], use_saved_draft=True)

    assert error is None
    assert isinstance(sheet, dict)
    assert len(sheet["rows"]) == 2
    assert sheet["rows"][0][0] == "04/26"
    assert sheet["rows"][0][2] == "主"
    assert sheet["rows"][0][3] == "大豆のトマト煮"
    assert sheet["rows"][0][4] == "70"
    assert sheet["rows"][0][5] == "70"
    assert sheet["rows"][1][2] == "副①"
    assert sheet["rows"][1][3] == "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ"
    assert sheet["rows"][1][4] == "70"
    assert sheet["rows"][1][5] == "70"
    assert case.get("workflow-v2/sheet-source")["sheet"]["rows"] == original_rows


def test_get_current_sheet_context_preserves_formally_saved_quantity_and_aux_display(history_case) -> None:
    original_rows = [["04/26", "朝", "主", "大豆のトマト煮", "", "", "", "", "", "", "", ""]]
    case, source, native_fields, native_header = _fac00004_owned_source(history_case,
        rows=original_rows, menu_entries=[{"id": "owned-aux-main", "monthly_menu_id": "2026-04",
        "menu_date": date(2026, 4, 26), "daypart": "朝", "name": "大豆のトマト煮",
        "category": "主", "slot_index": 0, "diet_type": "regular"}], lines=[])
    _legacy_aux_target_refused(case, "70")
    order = {"id": case.id}
    fields = ["date_mmdd", "daypart", "aux.col_2", "menu", "aux.col_4",
        "qty.regular_x", "qty.daycare_x", "qty.staff_x", "qty.no_meat_x",
        "qty.no_fish_x", "qty.no_fried_x", "qty.change_1_x", "remarks"]
    header = ["日付", "区分", "副区分", "メニュー", "合計", "常食", "通所", "職員",
        "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"]
    revision_rows = [['04/26', '朝', '', '大豆のトマト煮', '', '70', '', '', '', '', '', '', '']]
    _append_sheet_revision(order_id=order['id'], fields=fields, header=header, rows=[['04/26', '朝', '', '大豆のトマト煮', '', '70', '', '', '', '', '', '', '']], row_ids=['ocr-row-1'], revision_meta={'sheet_save_mode': 'applied', 'reparse_applied': True, 'provider': 'gemini', 'llm_assist': True})
    history, history_error = order_service.get_ocr_edit_history(case.id)
    assert history_error is None
    assert any(item["rows"] == revision_rows for item in history["revisions"])
    edited_rows = [["04/26", "朝", "主", "大豆のトマト煮", "70", "70", "", "", "", "", "", "", ""]]
    edited = {**source["sheet"], "fields": fields, "header": header, "rows": edited_rows,
              "row_ids": ["ocr-row-1"]}
    saved = save_draft(case, edited)
    assert saved["base_evidence_run_id"] == case.evidence
    with case.readonly():
        current = order_service.get_current_sheet_context(order["id"])
    assert isinstance(current, dict)
    assert current["rows"] == [["04/26", "朝", "主", "大豆のトマト煮", "70", "70", "", "", "", "", "", "", ""]]
    assert case.get("workflow-v2/sheet-source")["sheet"]["rows"] == original_rows


def test_get_ocr_sheet_ignores_non_authoritative_reparse_candidate_revision(history_case) -> None:
    original_rows = [["04/26", "朝", "主", "大豆のトマト煮", "", "", "", "", "", "", "", ""]]
    case, source, native_fields, native_header = _fac00004_owned_source(history_case,
        rows=original_rows, menu_entries=[{"id": "owned-aux-main", "monthly_menu_id": "2026-04",
        "menu_date": date(2026, 4, 26), "daypart": "朝", "name": "大豆のトマト煮",
        "category": "主", "slot_index": 0, "diet_type": "regular"}], lines=[])
    _legacy_aux_target_refused(case, "70")
    order = {"id": case.id}
    fields = ["date_mmdd", "daypart", "aux.col_2", "menu", "aux.col_4",
        "qty.regular_x", "qty.daycare_x", "qty.staff_x", "qty.no_meat_x",
        "qty.no_fish_x", "qty.no_fried_x", "qty.change_1_x", "remarks"]
    header = ["日付", "区分", "副区分", "メニュー", "合計", "常食", "通所", "職員",
        "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"]
    revision_rows = [['04/26', '朝', '', '大豆のトマト煮', '', '99', '', '', '', '', '', '', '']]
    _append_sheet_revision(order_id=order['id'], fields=fields, header=header, rows=[['04/26', '朝', '', '大豆のトマト煮', '', '99', '', '', '', '', '', '', '']], row_ids=['ocr-row-1'], revision_meta={'sheet_save_mode': 'draft_candidate', 'draft_from_reparse_reject': True, 'provider': 'gemini', 'llm_assist': True})
    history, history_error = order_service.get_ocr_edit_history(case.id)
    assert history_error is None
    assert any(item["rows"] == revision_rows for item in history["revisions"])
    with case.readonly():
        sheet, error = order_service.get_ocr_sheet(case.id, use_saved_draft=False)
    assert error is None
    assert isinstance(sheet, dict)
    assert sheet["rows"] == original_rows
    assert sheet["fields"] == native_fields
    assert all("99" not in row for row in sheet["rows"])
    assert case.get("workflow-v2/sheet-source")["sheet"]["rows"] == original_rows


def test_get_current_sheet_context_does_not_rebase_auto_apply_blocked_draft_on_read(authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=3, area="X")
    order = {"id": case.id}
    original_rows = [['03/22', '朝', 'Menu A', '5', ''], ['01/01', '"', 'Ghost Menu', '23', '']]
    sheet = {'order_id': order['id'], 'source': 'draft_sheet', 'fields': ['date_mmdd', 'daypart', 'menu', 'qty.regular_x', 'remarks'], 'header': ['日付', '区分', 'メニュー', '常食', '備考'], 'rows': original_rows, 'row_ids': ['draft-2', 'draft-1'], 'warnings': ['stale-current-warning-should-drop']}
    captured = order_service._capture_synchronous_cache_write_input(case.id)
    assert isinstance(captured, dict)
    historical = draft_sheet_service.persist_sheet_draft(
        order_id=case.id, draft_sheet_json=sheet,
        base_evidence_run_id=captured["evidence_run_id"],
        template_version_id=captured["evidence"]["template_version_id"],
        draft_state="auto_apply_blocked", blockers=["sheet_canonical_mismatch"],
        warnings=["sheet_ocr_review_required"], edited_by="tester",
        synchronous_write_input=captured,
    )
    assert isinstance(historical, dict)
    saved_draft = save_draft(case, sheet)
    assert isinstance(saved_draft, dict)
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id)
        refreshed_draft = draft_sheet_service.get_latest_sheet_draft(case.id)
    assert isinstance(current, dict)
    assert current["source"] == "draft_sheet"
    assert current["rows"][0] == original_rows[0]
    assert current["rows"][1] == original_rows[1]
    assert current["rows"][1][3] == "23"
    assert refreshed_draft is not None
    assert refreshed_draft["id"] == saved_draft["id"]
    assert refreshed_draft["draft_sheet_json"]["rows"] == original_rows
    assert current["row_ids"] == ["draft-2", "draft-1"]
    with session_scope() as session:
        retained = session.get(OrderSheetDraft, historical["id"])
        assert retained.draft_state == "auto_apply_blocked"
        assert retained.blockers_json == ["sheet_canonical_mismatch"]
        assert retained.warnings_json == ["sheet_ocr_review_required"]
        assert retained.draft_sheet_json["rows"] == original_rows


def _owned_march_projection(history_case, *, menu_entries, regular_values):
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "qty.regular_3f",
        "qty.soft_2f", "qty.soft_3f", "qty.mixer_2f", "qty.mixer_3f", "remarks"]
    header = ["日付", "区分", "メニュー", "常食2F", "常食3F",
        "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"]
    roles = ["date", "daypart", "menu_name", *["quantity"] * 6, "note"]
    columns = [{"index": i, "source_index": i, "name": field, "header": header[i],
                "role": roles[i]} for i, field in enumerate(fields)]
    for column, (diet, area) in zip(columns[3:9], [
        ("regular", "2F"), ("regular", "3F"), ("soft", "2F"),
        ("soft", "3F"), ("mixer", "2F"), ("mixer", "3F"),
    ], strict=True):
        column.update(diet_type=diet, area_id=area)
    assert set(regular_values) == {entry["name"] for entry in menu_entries}
    period = "2026-03@2026-03-22~2026-03-28"
    case = history_case(week=period, period=period, received=datetime(2026, 3, 22, 9),
        columns=columns, lines=[{"date": date(2026, 3, 22), "daypart": "朝", "menu_name": "Menu A",
            "diet_type": "regular", "area_id": "2F", "bag_type": "standard", "quantity_original": 3}],
        menu_entries=menu_entries)
    values = {(name, field): regular_values[name] if field == "qty.regular_2f" else ""
              for name in regular_values for field in fields[3:]}
    def recognize(region):
        from tests.helpers.authored_cell_values import declared_field
        recognized = {values[(target["menu_name"], declared_field(target, fields))]
                      for target in region["logical_targets"]}
        assert len(recognized) == 1
        return recognized.pop()
    source = case.produce(cell_values=recognize)
    assert source["sheet"]["fields"] == fields
    assert source["sheet"]["header"] == header
    return case, source


def _store_unowned_overlay(case, payload):
    from src.models.order_ocr_cache import OrderOcrCache
    with session_scope() as session:
        cached = session.get(OrderOcrCache, case.id)
        if cached is None:
            session.add(OrderOcrCache(order_id=case.id, payload=payload))
        else:
            cached.payload = payload


@pytest.mark.parametrize("mapping_location", ["payload-position-metadata", "unowned-candidate-table"])
def test_unowned_legacy_table_cannot_overlay_owned_saved_quantities(history_case, mapping_location):
    entries = [
        {"id": "march-a", "monthly_menu_id": "2026-03", "menu_date": date(2026, 3, 22),
         "daypart": "朝", "name": "Menu A", "slot_index": 0, "diet_type": "regular"},
        {"id": "march-b", "monthly_menu_id": "2026-03", "menu_date": date(2026, 3, 22),
         "daypart": "昼", "name": "Menu B", "slot_index": 0, "diet_type": "regular"},
    ]
    case, source = _owned_march_projection(history_case,
        menu_entries=entries, regular_values={"Menu A": "", "Menu B": ""})
    saved = save_draft(case, source["sheet"])
    if mapping_location == "payload-position-metadata":
        legacy = {'column_mapping_resolution': {'resolved_value': '4:qty.regular_2f|5:qty.regular_3f|6:qty.soft_2f|7:qty.soft_3f|8:qty.mixer_2f|9:qty.mixer_3f', 'decision_source': 'position_fallback'}, 'tables': [{'table_id': 'p1_t1', 'page_index': 1, 'rows': [['日付', '区分', '', '献立', '常食', '', '軟菜', '', 'ミキサー', '', '備考欄'], ['', '', '', '', '2F', '3F', '2F', '3F', '2F', '3F', ''], ['03/22', '朝', '主A', 'Menu A', '9', '', '', '', '', '', ''], ['', '昼', '主A', 'Menu B', '8', '', '', '', '', '', '']]}], 'table_raw': '|日付|区分||献立|常食| |軟菜| |ミキサー| |備考欄|\n|---|---|---|---|---|---|---|---|---|---|---|\n|03/22|朝|主A|Menu A|9|||||||\n||昼|主A|Menu B|8||||||'}
    else:
        legacy = {'tables': [{'table_id': 'p1_t1', 'page_index': 1, 'rows': [['日付', '区分', '', '献立', '常食', '', '軟菜', '', 'ミキサー', '', '備考欄'], ['', '', '', '', '2F', '3F', '2F', '3F', '2F', '3F', ''], ['03/22', '朝', '主A', 'Menu A', '9', '', '', '', '', '', ''], ['', '昼', '主A', 'Menu B', '8', '', '', '', '', '', '']]}], 'table_raw': '|日付|区分||献立|常食| |軟菜| |ミキサー| |備考欄|\n|---|---|---|---|---|---|---|---|---|---|---|\n|03/22|朝|主A|Menu A|9|||||||\n||昼|主A|Menu B|8||||||'}
    _store_unowned_overlay(case, legacy)
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id)
    assert isinstance(current, dict)
    assert current["source"] == "workflow_v2_selected_ocr_projection"
    assert current["draft_id"] == saved["id"]
    assert current["rows"] == [
        ["03/22", "朝", "Menu A", "", "", "", "", "", "", ""],
        ["03/22", "昼", "Menu B", "", "", "", "", "", "", ""],
    ]
    assert current["draft_payload"] == source["sheet"]
    from src.models.order_ocr_cache import OrderOcrCache
    with session_scope() as session:
        assert session.get(OrderOcrCache, case.id).payload == legacy


def test_owned_projection_and_formal_save_keep_nine_and_eight_in_exact_menu_cells(history_case):
    entries = [
        {"id": "march-a", "monthly_menu_id": "2026-03", "menu_date": date(2026, 3, 22),
         "daypart": "朝", "name": "Menu A", "slot_index": 0, "diet_type": "regular"},
        {"id": "march-b", "monthly_menu_id": "2026-03", "menu_date": date(2026, 3, 22),
         "daypart": "昼", "name": "Menu B", "slot_index": 0, "diet_type": "regular"},
    ]
    case, source = _owned_march_projection(history_case,
        menu_entries=entries, regular_values={"Menu A": "9", "Menu B": "8"})
    saved = save_draft(case, source["sheet"])
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id)
    assert current["draft_id"] == saved["id"]
    assert current["rows"] == [
        ["03/22", "朝", "Menu A", "9", "", "", "", "", "", ""],
        ["03/22", "昼", "Menu B", "8", "", "", "", "", "", ""],
    ]
    assert case.get("workflow-v2/sheet")["sheet"] == source["sheet"]




def test_current_owned_projection_does_not_append_unowned_ocr_menu_candidates(history_case):
    entries = [{"id": "march-a", "monthly_menu_id": "2026-03", "menu_date": date(2026, 3, 22),
        "daypart": "朝", "name": "Menu A", "slot_index": 0, "diet_type": "regular"}]
    case, source = _owned_march_projection(history_case,
        menu_entries=entries, regular_values={"Menu A": "4"})
    saved = save_draft(case, source["sheet"])
    legacy = _sample_payload("4")
    legacy["tables"][0]["rows"].append(["03/23", "lunch", "OCR Extra", ""])
    legacy["table_raw"] += "\n|03/23|lunch|OCR Extra||"
    _store_unowned_overlay(case, legacy)
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id)
    assert isinstance(current, dict)
    assert current["source"] == "workflow_v2_selected_ocr_projection"
    assert current["rows"] == [["03/22", "朝", "Menu A", "4", "", "", "", "", "", ""]]
    assert current["draft_id"] == saved["id"]
    assert all(row[2] != "OCR Extra" for row in current["rows"])
    assert case.get("workflow-v2/sheet")["sheet"] == source["sheet"]
    from src.models.order_ocr_cache import OrderOcrCache
    with session_scope() as session:
        assert session.get(OrderOcrCache, case.id).payload == legacy


def test_get_current_sheet_context_does_not_persist_semantic_refresh_on_read(authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=3, cell_values="9")
    assert case.selected_sheet["rows"][0][3] == "9"
    order = {"id": case.id}

    saved = save_draft(case, {
            "order_id": order["id"],
            "source": "draft_sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["03/22", "朝", "Menu A", "3", "saved"]],
            "row_ids": ["row-1"],
            "warnings": [],
        })
    assert isinstance(saved, dict)

    with session_scope() as session:
        before_count = (
            session.query(OrderSheetDraft)
            .filter(OrderSheetDraft.order_id == order["id"])
            .count()
        )

    with case.readonly():
        current = order_service.get_current_sheet_context(
            order["id"],
            refresh_draft_from_semantic=True,
            upgrade_generic_from_sheet=True,
            backfill_from_revision=True,
        )

    assert isinstance(current, dict)
    assert current["rows"] == [["03/22", "朝", "Menu A", "3", "saved"]]
    assert current["draft_id"] == saved["id"]

    with session_scope() as session:
        after_count = (
            session.query(OrderSheetDraft)
            .filter(OrderSheetDraft.order_id == order["id"])
            .count()
        )

    assert after_count == before_count


def _unselected_blocked_draft(case):
    captured = order_service._capture_synchronous_cache_write_input(case.id)
    assert isinstance(captured, dict)
    blocked = draft_sheet_service.persist_sheet_draft(
        order_id=case.id,
        draft_sheet_json={
            "order_id": case.id, "source": "review_blocked",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [], "row_ids": [], "warnings": ["menu_entries_missing", "rows_empty"],
        },
        base_evidence_run_id=captured["evidence_run_id"],
        template_version_id=captured["evidence"]["template_version_id"],
        draft_state="draft_ready", blockers=["rows_empty"],
        warnings=["menu_entries_missing", "rows_empty"], edited_by="rerun-ocr-evidence",
        synchronous_write_input=captured,
    )
    assert isinstance(blocked, dict)
    assert blocked["draft_sheet_json"]["rows"] == []
    return blocked


def test_get_current_sheet_context_ignores_non_authoritative_auto_blocked_draft_and_uses_canonical_skeleton(authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=3, include_remarks=False)
    blocked = _unselected_blocked_draft(case)
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id,
            refresh_draft_from_semantic=True, upgrade_generic_from_sheet=True,
            backfill_from_revision=False)
    assert isinstance(current, dict)
    assert current["draft_id"] is None
    assert current["source"] == "workflow_v2_selected_ocr_projection"
    assert current["fields"][:3] == ["date_mmdd", "daypart", "menu"]
    assert current["rows"] == [["03/22", "朝", "Menu A", "3"]]
    with session_scope() as session:
        assert session.get(OrderSheetDraft, blocked["id"]).draft_sheet_json["rows"] == []


def test_get_current_sheet_context_keeps_operator_forced_repair_draft_as_authoritative_current_truth(authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=3)
    order = {"id": case.id}

    saved = save_draft(case, {
            "order_id": order["id"],
            "source": "forced_weekly_menu",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "7"]],
            "row_ids": ["row-1"],
            "warnings": ["forced_weekly_menu_overwrite"],
            "repair_mode": "forced_weekly_menu_overwrite",
            "repair_metadata": {
                "mode": "forced_weekly_menu_overwrite",
                "origin": "operator",
                "blank_quantities": False,
            },
        })
    assert isinstance(saved, dict)

    with case.readonly():
        current = order_service.get_current_sheet_context(
            order["id"],
            refresh_draft_from_semantic=True,
            upgrade_generic_from_sheet=True,
            backfill_from_revision=False,
        )

    assert isinstance(current, dict)
    assert current["draft_id"] == saved["id"]
    assert current["source"] == "forced_weekly_menu"
    assert current["repair_mode"] == "forced_weekly_menu_overwrite"
    assert current["rows"] == [["03/22", "朝", "Menu A", "7"]]


def test_resolve_active_ocr_evidence_run_ignores_non_authoritative_auto_draft_base_evidence(authored_sheet_case) -> None:
    case, first, saved, second = _saved_three_and_owned_candidate(authored_sheet_case, candidate_quantity="9")
    blocked = _unselected_blocked_draft(case)
    with case.readonly():
        before_selection = order_service._resolve_active_ocr_evidence_run(case.id)
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert isinstance(blocked, dict)
    assert before_selection["id"] == first["id"]
    assert draft_sheet_service.get_selected_sheet_draft(case.id)["id"] == saved["id"]
    case.command("POST", f"ocr-results/{second['id']}/select")
    with case.readonly():
        resolved = order_service._resolve_active_ocr_evidence_run(case.id)
    assert isinstance(resolved, dict)
    assert resolved["id"] == second["id"]
    source = case.get("workflow-v2/sheet-source")
    assert str(source["sheet"]["rows"][0][3]) == "9"
    with session_scope() as session:
        assert session.get(OrderSheetDraft, blocked["id"]).draft_sheet_json["rows"] == []
        assert session.get(OrderSheetDraft, saved["id"]).draft_sheet_json == saved["draft_sheet_json"]






def test_get_latest_sheet_draft_keeps_generic_cols_without_opt_in(authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=3, cell_values="6")
    assert case.selected_sheet["rows"][0][3] == "6"
    order = {"id": case.id}

    saved = save_draft(case, {
            "order_id": order["id"],
            "source": "ocr_evidence",
            "fields": ["col1", "col2", "col3", "col4"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        })
    assert isinstance(saved, dict)

    with case.readonly():
        current = order_service.get_latest_sheet_draft(
            order["id"],
            backfill_from_revision=False,
        )
    assert current["id"] == saved["id"]

    assert isinstance(current, dict)
    draft_json = current["draft_sheet_json"]
    assert draft_json["fields"] == ["col1", "col2", "col3", "col4"]
    assert draft_json["rows"] == [["03/22", "朝", "Menu A", "5"]]


def test_owned_rerun_keeps_saved_quantities_and_requires_explicit_candidate_adoption(authored_sheet_case):
    case = authored_sheet_case(quantity=3)
    saved_sheet = {"fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
        "rows": [["03/22", "朝", "Menu A", "3", "retained"]], "row_ids": ["row-1"],
        "week_id": "2026-03@2026-03-22~2026-03-28"}
    saved = save_draft(case, saved_sheet)
    before = _snapshot(case.engine)
    original_pdf = case.pdf.read_bytes()
    calls = prepare_rerun(case, cell_values="9")
    with case.execution():
        rerun, error = order_service.rerun_ocr_evidence_only(case.id, job_id=case.job,
            project_sheet=False, refresh_workflow=False)
    assert error is None and isinstance(rerun, dict)
    assert len(calls) == 1
    assert rerun["id"] != saved["base_evidence_run_id"]
    assert rerun["terminal_receipt"]["status"] == "done"
    job = order_service.get_ocr_job(case.job)
    assert job["status"] == "done"
    after = _snapshot(case.engine)
    for table in ("order_sheet_drafts", "order_lines"):
        assert after[table] == before[table], table
    for table in ("order_documents", "uploaded_pdfs", "order_versions"):
        assert after[table] == before[table], table
    assert case.pdf.read_bytes() == original_pdf
    with session_scope() as session:
        retained = session.get(OrderSheetDraft, saved["id"])
        assert retained.draft_sheet_json == saved_sheet
        assert retained.base_evidence_run_id == saved["base_evidence_run_id"]
        lines = session.query(OrderLine).filter_by(order_id=case.id).all()
        # A saved sheet is not a bagging materialization; reruns cannot create one.
        assert lines == []
    case.command("POST", f"ocr-results/{rerun['id']}/select")
    source = case.get("workflow-v2/sheet-source")
    assert source["selected_ocr_result_id"] == rerun["id"]
    assert str(source["sheet"]["rows"][0][3]) == "9"
    with session_scope() as session:
        assert session.get(OrderSheetDraft, saved["id"]).draft_sheet_json == saved_sheet


def _assert_rejected_rerun(case, rerun, error, diagnostic, before):
    from src.models.order_workflow_state import OrderWorkflowState
    from src.models.workflow_ocr_delivery import WorkflowOcrDelivery
    from tests.contract.test_saved_sheet_quantity_schema import unchanged_after_refusal

    receipt = rerun["terminal_receipt"]
    assert receipt == {
        "order_id": case.id, "job_id": case.job, "execution_id": None,
        "workflow_attempt_id": case.queued["ocr_job"]["workflow_attempt_id"],
        "workflow_revision": case.queued["workflow"]["revision"],
        "evidence_run_id": rerun["id"], "status": "failed", "error": error,
        "workflow_delivery_owner": {key: case.claim[key] for key in ("delivery_id", "generation", "claim_token")},
    }
    assert rerun["status"] == "diagnostic_only"
    assert rerun["order_id"] == case.id
    payload = rerun["payload_json"]
    assert {key: payload[key] for key in diagnostic} == diagnostic
    lineage = payload["_ocr_execution"]
    assert lineage["disposition"] == "rejected"
    for key in ("job_id", "workflow_attempt_id", "workflow_revision", "evidence_run_id"):
        assert lineage[key] == receipt[key]
    latest = order_service.get_latest_ocr_evidence_run(case.id, backfill_from_cache=False)
    assert rerun["created"] is True
    assert latest == {key: value for key, value in rerun.items() if key not in {"terminal_receipt", "created"}}
    job = order_service.get_ocr_job(case.job)
    assert job["status"] == "failed"
    assert job["error_message"] == error
    assert job["metrics"]["adopted"] is False
    assert job["metrics"]["rerun_terminal_receipt"] == receipt
    after = _snapshot(case.engine)
    for table in ("order_sheet_drafts", "order_ocr_cache", "order_ocr_revisions",
                  "order_current_states", "order_confirmed_snapshots", "order_lines"):
        assert after[table] == before[table], table
    with session_scope() as session:
        delivery = session.get(WorkflowOcrDelivery, case.claim["delivery_id"])
        assert delivery.order_id == case.id and delivery.job_id == case.job
        assert delivery.delivery_generation == case.claim["generation"]
        assert delivery.claim_token == case.claim["claim_token"]
        state = session.get(OrderWorkflowState, case.id)
        assert state.evidence_run_id is None and state.draft_id is None
        assert state.confirmed_snapshot_id is None
        assert state.state == "ocr_failed" and state.blockers_json == [error]
        assert state.revision == receipt["workflow_revision"] + 1
        assert before["order_lines"] == []
        assert session.query(OrderLine).filter(OrderLine.order_id == case.id).count() == 0

    # Real authenticated commands may acquire CAS locks, but cannot adopt or mutate state.
    revision = case.revision()
    headers = {**case.actor.headers, "If-Match": f'"{revision}"'}
    with unchanged_after_refusal(case, revision):
        selected = case.client.post(f"/orders/{case.id}/workflow-v2/ocr-results/{rerun['id']}/select", headers=headers)
        assert selected.status_code == 400
        assert selected.json()["detail"] == "ocr_diagnostic_result_not_selectable"
    with unchanged_after_refusal(case, revision):
        applied = case.client.post(f"/orders/{case.id}/ocr-apply", headers=headers, json={"evidence_run_id": rerun["id"]})
        assert applied.status_code == 410
        assert applied.json()["detail"]["error"] == "legacy_order_workflow_disabled"
    with unchanged_after_refusal(case, revision):
        reviewed = case.client.post(f"/orders/{case.id}/workflow-v2/sheet/review-confirm", headers=headers,
            json={"sheet": {"fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
                            "header": ["日付", "区分", "メニュー", "常食2F"],
                            "rows": [["03/22", "朝", "Menu A", "3"]]}})
        assert reviewed.status_code == 400
        assert reviewed.json()["detail"] == "selected_ocr_required"
    with unchanged_after_refusal(case, revision):
        confirmed = case.client.post(f"/orders/{case.id}/workflow-v2/confirm", headers=headers,
                                     json={"confirmed_by": "operator"})
        assert confirmed.status_code == 400
        assert confirmed.json()["detail"] == "output_review_required"


def test_rerun_ocr_evidence_only_maps_failed_partial_output_to_pipeline_failure(monkeypatch, authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=3, produce=False)
    with session_scope() as session:
        line = session.query(OrderLine).filter(OrderLine.order_id == case.id).one()
        assert (line.date, line.daypart, line.menu_name, line.diet_type, line.area_id, line.quantity_original) == (
            date(2026, 3, 22), "朝", "Menu A", "regular", "2F", 3)
    calls = prepare_rerun(case)
    order = {"id": case.id}
    diagnostic = {
        "status": "failed", "stage": "error", "error": "template resolution failed",
        "input_reference": "gs://bucket/input.pdf", "output_reference": "gs://bucket/output.json",
    }
    monkeypatch.setattr(
        order_service,
        "_hakodate_canonical_payload_from_manifest_item",
        lambda **_kwargs: diagnostic.copy(),
    )
    before = _snapshot(case.engine)
    with case.execution():
        rerun, error = order_service.rerun_ocr_evidence_only(order["id"], job_id=case.job)

    assert error == "ocr_pipeline_failed"
    assert calls == []
    _assert_rejected_rerun(case, rerun, error, diagnostic, before)


def test_rerun_ocr_evidence_only_maps_empty_done_output_to_evidence_unusable(monkeypatch, authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=3, produce=False)
    with session_scope() as session:
        line = session.query(OrderLine).filter(OrderLine.order_id == case.id).one()
        assert (line.date, line.daypart, line.menu_name, line.diet_type, line.area_id, line.quantity_original) == (
            date(2026, 3, 22), "朝", "Menu A", "regular", "2F", 3)
    calls = prepare_rerun(case)
    order = {"id": case.id}
    diagnostic = {
        "status": "done", "stage": "done",
        "input_reference": "gs://bucket/input.pdf", "output_reference": "gs://bucket/output.json",
    }
    monkeypatch.setattr(
        order_service,
        "_hakodate_canonical_payload_from_manifest_item",
        lambda **_kwargs: diagnostic.copy(),
    )
    before = _snapshot(case.engine)
    with case.execution():
        rerun, error = order_service.rerun_ocr_evidence_only(order["id"], job_id=case.job)

    assert error == "evidence_unusable"
    assert calls == []
    _assert_rejected_rerun(case, rerun, error, diagnostic, before)


def test_unowned_rerun_cannot_publish_partial_pipeline_output(authored_sheet_case):
    case = authored_sheet_case(quantity=3)
    saved = save_draft(case, {"fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "rows": [["03/22", "朝", "Menu A", "3"]], "row_ids": ["row-1"],
        "week_id": "2026-03@2026-03-22~2026-03-28"})
    with case.readonly():
        rerun, error = order_service.rerun_ocr_evidence_only(case.id)
    assert rerun is None and error == "workflow_job_required"
    with session_scope() as session:
        retained = session.get(OrderSheetDraft, saved["id"])
        assert retained.draft_sheet_json["rows"] == [["03/22", "朝", "Menu A", "3"]]
        assert retained.base_evidence_run_id == case.evidence


def test_rerun_ocr_evidence_only_completes_actual_generated_job_identity(monkeypatch, authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=3, produce=False)
    prepare_rerun(case, cell_values="9")
    order = {"id": case.id}

    with case.execution():
        rerun, error = order_service.rerun_ocr_evidence_only(order["id"], job_id=case.job)

    assert error is None
    assert isinstance(rerun, dict)
    job = order_service.get_ocr_job(case.job)
    assert isinstance(job, dict)
    assert job["id"] == case.job
    assert job["status"] == "done"
    metrics = job.get("metrics") or {}
    assert metrics.get("result_state") == "evidence_ready"
    latest_evidence = order_service.get_latest_ocr_evidence_run(order["id"], backfill_from_cache=False)
    assert isinstance(latest_evidence, dict)
    assert latest_evidence["producer_version"] == "hakodate_best_method_pipeline"


def test_rerun_ocr_evidence_only_completes_actual_explicit_job_identity(monkeypatch, authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=3, produce=False)
    case.prepare_provider(cell_values="9", job_id="explicit-owned-native-job")
    order = {"id": case.id}

    with case.execution():
        rerun, error = order_service.rerun_ocr_evidence_only(order["id"], job_id=case.job)

    assert error is None
    assert isinstance(rerun, dict)
    job = order_service.get_ocr_job(case.job)
    assert isinstance(job, dict)
    assert job["id"] == case.job == "explicit-owned-native-job"
    assert job["status"] == "done"
    metrics = job.get("metrics") or {}
    assert metrics.get("result_state") == "evidence_ready"
    latest_evidence = order_service.get_latest_ocr_evidence_run(order["id"], backfill_from_cache=False)
    assert isinstance(latest_evidence, dict)
    assert latest_evidence["producer_version"] == "hakodate_best_method_pipeline"


def test_rerun_ocr_evidence_only_uses_pipeline_wait_timeout_plus_grace(monkeypatch, authored_sheet_case) -> None:
    case = authored_sheet_case(quantity=3, produce=False)
    prepare_rerun(case, cell_values="9")
    order = {"id": case.id}
    monkeypatch.setenv("OCR_PIPELINE_TIMEOUT_SECONDS", "1200")
    monkeypatch.setenv("OCR_RERUN_PIPELINE_STAGE_GRACE_SECONDS", "45")

    captured: dict[str, object] = {}
    produce = order_service._hakodate_canonical_payload_from_manifest_item

    def _observe_hakodate(**kwargs):
        captured.update(kwargs)
        return produce(**kwargs)

    monkeypatch.setattr(order_service, "_hakodate_canonical_payload_from_manifest_item", _observe_hakodate)

    with case.execution():
        rerun, error = order_service.rerun_ocr_evidence_only(order["id"], job_id=case.job)

    assert error is None
    assert isinstance(rerun, dict)
    assert captured["order_id"] == order["id"]
    assert captured["item"] == {"source": "live_order_facility_source_workbook"}


def _saved_three_and_owned_candidate(authored_sheet_case, *, candidate_quantity="8"):
    case = authored_sheet_case(quantity=3, include_remarks=False)
    first = order_service.get_ocr_evidence_run(case.evidence)
    assert isinstance(first, dict)
    saved = save_draft(case, {
        "order_id": case.id, "source": "manual_draft",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [["03/22", "朝", "Menu A", "3"]], "row_ids": ["row-1"],
    })
    assert isinstance(saved, dict)
    assert saved["base_evidence_run_id"] == first["id"]
    calls = prepare_rerun(case, cell_values=candidate_quantity)
    with case.execution():
        second, error = order_service.rerun_ocr_evidence_only(case.id, job_id=case.job,
            project_sheet=False, refresh_workflow=False)
    assert error is None and isinstance(second, dict)
    assert len(calls) == 1
    assert second["id"] != first["id"]
    assert second["terminal_receipt"]["status"] == "done"
    assert second["terminal_receipt"]["evidence_run_id"] == second["id"]
    with session_scope() as session:
        assert session.get(OrderSheetDraft, saved["id"]).draft_sheet_json == saved["draft_sheet_json"]
    return case, first, saved, second


def test_switch_draft_to_latest_evidence_explicitly_adopts_new_candidate(authored_sheet_case) -> None:
    case, first, saved, second = _saved_three_and_owned_candidate(authored_sheet_case)
    case.command("POST", f"ocr-results/{second['id']}/select")
    source = case.get("workflow-v2/sheet-source")
    assert source["selected_ocr_result_id"] == second["id"]
    case.save(source["sheet"])
    switched = draft_sheet_service.get_selected_sheet_draft(case.id)
    with case.readonly():
        saved_sheet, error = order_service.get_ocr_sheet(case.id)
    assert error is None
    assert isinstance(switched, dict)
    assert switched["base_evidence_run_id"] == second["id"]
    assert switched["draft_sheet_json"]["rows"] == [["03/22", "朝", "Menu A", "8"]]
    assert saved_sheet["rows"] == switched["draft_sheet_json"]["rows"]
    with session_scope() as session:
        original = session.get(OrderSheetDraft, saved["id"])
        assert original.base_evidence_run_id == first["id"]
        assert original.draft_sheet_json == saved["draft_sheet_json"]


def test_get_candidate_draft_preview_returns_latest_candidate_without_mutating_current_draft(authored_sheet_case) -> None:
    case, first, saved, second = _saved_three_and_owned_candidate(authored_sheet_case)
    order_service.workflow_state_service.refresh_workflow_state(case.id)
    with case.readonly():
        preview, error = order_service.get_candidate_draft_preview(case.id)
    assert error is None
    assert isinstance(preview, dict)
    assert preview["base_evidence_run_id"] == second["id"]
    assert preview["draft_sheet_json"]["rows"] == [["03/22", "朝", "Menu A", "8"]]
    latest = draft_sheet_service.get_selected_sheet_draft(case.id)
    assert isinstance(latest, dict)
    assert latest["base_evidence_run_id"] == first["id"]
    assert latest["id"] == saved["id"]
    assert latest["draft_sheet_json"] == saved["draft_sheet_json"]


def test_candidate_sheet_state_marks_real_candidate_diff_previewable(authored_sheet_case) -> None:
    case, first, saved, second = _saved_three_and_owned_candidate(authored_sheet_case)
    with case.readonly():
        state = order_service.candidate_sheet_state(case.id, candidate_evidence_run_id=second["id"])
    assert state["candidate_preview_available"] is True
    assert state["candidate_has_meaningful_diff"] is True
    assert state["candidate_preview_error"] is None
    assert state["current_sheet_revision_id"] == saved["id"]
    preview = state["candidate_preview_draft"]
    assert isinstance(preview, dict)
    assert preview["base_evidence_run_id"] == second["id"]
    assert preview["draft_sheet_json"]["rows"] == [["03/22", "朝", "Menu A", "8"]]
    retained = draft_sheet_service.get_selected_sheet_draft(case.id)
    assert retained["base_evidence_run_id"] == first["id"]
    assert retained["draft_sheet_json"] == saved["draft_sheet_json"]


def test_owned_transient_source_six_and_actual_menu_loss_are_not_fallback_success(authored_sheet_case):
    from src.models.menu import MonthlyMenuEntry

    case = authored_sheet_case(quantity=3, cell_values="6", include_remarks=False)
    evidence = order_service.get_ocr_evidence_run(case.evidence)
    with case.readonly():
        context = order_service.get_current_sheet_context(case.id,
            refresh_draft_from_semantic=True, upgrade_generic_from_sheet=True,
            backfill_from_revision=False)
    assert isinstance(context, dict)
    assert context["draft_id"] is None
    assert context["source"] == "workflow_v2_selected_ocr_projection"
    assert context["fields"] == ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    assert context["rows"] == [["03/22", "朝", "Menu A", "6"]]
    assert context["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"
    assert context["menu_diagnostics"]["order_codes"] == []
    assert context["has_semantic_fields"] is True
    assert str(context["current_sheet_revision_id"]).startswith("current:")
    assert order_current_state_service.get_current_state(case.id) is None
    with session_scope() as session:
        session.query(MonthlyMenuEntry).delete()
    with case.readonly():
        blocked = order_service.get_current_sheet_context(case.id)
        diagnostics = order_service._build_monthly_menu_diagnostics(
            week_id="2026-03@2026-03-22~2026-03-28", facility_id=case.facility)
    assert blocked["draft_payload"] is None
    assert blocked["rows"] == []
    assert blocked["blockers"] == ["hakodate_original_expectation_required"]
    assert diagnostics["order_codes"] == ["menu_entries_missing"]
    assert order_service.get_ocr_evidence_run(case.evidence) == evidence
    assert order_current_state_service.get_current_state(case.id) is None


def test_current_sheet_context_prefers_canonical_order_week_over_stale_draft_week(authored_sheet_case) -> None:
    week = "2026-03@2026-03-22~2026-03-28"
    f = authored_sheet_case(quantity=3, include_remarks=False)
    source = f.selected_sheet
    assert source["rows"][0][:3] == ["03/22", "朝", "Menu A"]
    sheet = {
        "order_id": f.id, "source": "manual_draft",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [["03/22", "朝", "Menu A", "5"]], "row_ids": ["row-1"],
        "resolved_week_id": "2026-03@2026-03-01~2026-03-07",
        "week_id": "2026-03@2026-03-01~2026-03-07",
    }
    saved = f.save(sheet)
    assert isinstance(saved, dict)
    with f.readonly():
        current = order_service.get_current_sheet_context(f.id,
            refresh_draft_from_semantic=False, upgrade_generic_from_sheet=False,
            backfill_from_revision=False)
        assert isinstance(current, dict)
        assert current["resolved_week_id"] == week
        flattened = order_service.flatten_current_sheet_payload(f.id, current)
        assert flattened["resolved_week_id"] == week
        for key in ("fields", "header", "rows", "row_ids"):
            assert current[key] == flattened[key] == sheet[key]
    assert f.get("workflow-v2/sheet")["sheet"] == sheet
    assert f.get()["persisted_week_value"] == week
    assert f.get("workflow-v2/sheet-source")["sheet"]["row_ids"] == source["row_ids"]
