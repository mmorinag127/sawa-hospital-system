import sys
import pathlib
from copy import deepcopy
from datetime import date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from src.db import session_scope
from tests.helpers.newnarrowbaggingfixture import selected_bagging_order
from tests.helpers.owned_order import authored_layout_registration, owned_registration  # noqa: F401
from tests.integration.test_ocr_sheet_history import history_registration  # noqa: F401
from tests.helpers.owned_output import owned_outputs  # noqa: F401
from tests.helpers.authored_sheet_case import authored_sheet_case  # noqa: F401
from tests.contract.test_orders_ocr_sheet_history_api import history_case  # noqa: F401
from tests.helpers.job_diagnostics_corruption import inject_stale_job_diagnostics
from tests.helpers.authored_parser_templates import authored_aux_component_template as _authored_aux_component_template

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import candidate_resolution_service, config_service, fax_extractor, order_service  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


@pytest.fixture
def selected_projection_case(history_registration, authored_layout_registration, monkeypatch, tmp_path):
    """Explicit authored cells through the real current producer, never live FAX proof."""
    from src.db import session_scope
    from src.models.menu import MonthlyMenu, MonthlyMenuEntry
    from src.services import order_workflow_v2_service as workflow
    from src.services.canonical_facility_store import read_snapshot
    from src.services.sheet_week_service import parse_sheet_week_value
    from tests.helpers.native_selection import select_authored_native_result

    created = []

    def create(*, layout, facility_id, period, rows, unlisted_cells, recognizer_score=1.0,
               stored_lines=None, body_merge_policy=None):
        assert not created, "one explicit projection source per case"
        assert layout in {"authored-split", "repository-specimen"}
        assert layout != "authored-split" or facility_id == "FAC00001"
        assert isinstance(unlisted_cells, str) and rows
        _, start, end = parse_sheet_week_value(period)
        assert start is not None and end is not None
        with session_scope() as session:
            slots = {}
            for row in rows:
                day = date.fromisoformat(row["date"])
                assert start <= day <= end and row["daypart"] and row["menu"]
                assert isinstance(row["cells"], dict)
                month = day.strftime("%Y-%m")
                if session.get(MonthlyMenu, month) is None:
                    session.add(MonthlyMenu(id=month, month_start=day.replace(day=1), filename="authored-projection.xlsx"))
                    session.flush()
                key = day, row["daypart"]
                slot = slots.get(key, 0)
                slots[key] = slot + 1
                session.add(MonthlyMenuEntry(id="projection-" + uuid4().hex,
                    monthly_menu_id=month, menu_date=day, daypart=row["daypart"],
                    name=row["menu"], diet_type="regular", slot_index=slot))
        register = history_registration if layout == "authored-split" else authored_layout_registration
        registration_options = {}
        if body_merge_policy is not None:
            assert layout == "repository-specimen"
            registration_options["body_merge_policy"] = body_merge_policy
        order = register(message_id="projection-" + uuid4().hex, facility_id=facility_id,
            received_at=datetime.combine(start, datetime.min.time()).replace(hour=9),
            week=period, effective_date=start.isoformat(), lines=stored_lines or [], **registration_options)
        if layout == "repository-specimen":
            current, error = workflow.get_workflow_readonly(order["id"])
            assert error is None
            confirmed, error = workflow.confirm_context(order_id=order["id"], facility_id=facility_id,
                week_start=start.isoformat(), week_end=end.isoformat(), expected_revision=current["revision"])
            assert error is None and confirmed is not None
        configured = next(facility for facility in read_snapshot().master["facilities"]
                          if facility["facility_id"] == facility_id)
        columns = configured["fax_template"]["columns"]
        declared = {column["name"] for column in columns if column["role"] not in {"date", "daypart", "menu_name"}}
        raw_rows = []
        for row in rows:
            assert set(row["cells"]) <= declared, "authored input field has no physical column"
            raw = [""] * (max(column["source_index"] for column in columns) + 1)
            for column in columns:
                role = column["role"]
                if role in {"date", "daypart", "menu_name"}:
                    value = {"date": row["date"], "daypart": row["daypart"], "menu_name": row["menu"]}[role]
                else:
                    value = row["cells"].get(column["name"], unlisted_cells)
                raw[column["source_index"]] = "" if value is None else str(value)
            raw_rows.append(raw)
        evidence = select_authored_native_result(monkeypatch, order_id=order["id"],
            table_rows=raw_rows, columns=columns, unlisted_cells=unlisted_cells,
            artifact_root=tmp_path / "owned-projection-artifacts", recognizer_score=recognizer_score)
        sheet, error = order_service.get_ocr_sheet(order["id"])
        assert error is None and sheet is not None, error
        result = SimpleNamespace(id=order["id"], sheet=deepcopy(sheet), evidence=evidence,
            pdf=pathlib.Path(order["document"]), source_rows=deepcopy(rows), columns=deepcopy(columns))
        fields = sheet["fields"]
        date_index = fields.index("date_mmdd") if "date_mmdd" in fields else fields.index("date")
        for row in rows:
            day = date.fromisoformat(row["date"])
            matching = [candidate for candidate in sheet["rows"]
                if str(candidate[date_index]) in {day.isoformat(), day.strftime("%m/%d")}
                and order_service._normalize_daypart_key(candidate[fields.index("daypart")]) == order_service._normalize_daypart_key(row["daypart"])
                and order_service.hakodate_assignment_service._normalize_slot_text(candidate[fields.index("menu")]) == order_service.hakodate_assignment_service._normalize_slot_text(row["menu"])]
            assert len(matching) == 1, "authored physical row must remain uniquely identifiable"
            for field, value in row["cells"].items():
                assert field in fields, "authored cell cannot disappear from the current sheet"
                assert matching[0][fields.index(field)] == ("" if value is None else str(value))
        created.append(result)
        return result

    return create


def _assert_unowned_projection_is_not_adopted(case, payload):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot

    context_before = deepcopy(order_service.get_current_sheet_context(case.id))
    assert context_before is not None
    before, original = _snapshot(get_engine()), case.pdf.read_bytes()
    captured = order_service._capture_synchronous_cache_write_input(case.id)
    assert captured is not None
    assert order_service._save_order_ocr_cache(case.id, deepcopy(payload),
        synchronous_write_input=captured, augment_hakodate_artifacts=False,
        persist_evidence=False, refresh_workflow=False) is None
    current, error = order_service.get_ocr_sheet(case.id)
    assert error is None and current is not None
    for key in ("fields", "header", "rows", "row_ids", "base_evidence_run_id"):
        assert current[key] == case.sheet[key]
    assert current["base_evidence_run_id"] == case.evidence["id"]
    context = order_service.get_current_sheet_context(case.id)
    assert context is not None
    for key in ("fields", "rows", "warnings", "blockers", "apply_blockers",
                "base_evidence_run_id", "draft_id", "template_version_id"):
        assert context.get(key) == context_before.get(key)
    assert _snapshot(get_engine()) == before
    assert case.pdf.read_bytes() == original


def _structured_cells(rows: list[list[str]]) -> list[dict[str, object]]:
    cells: list[dict[str, object]] = []
    row_height = 1.0 / max(len(rows), 1)
    col_width = 1.0 / max(max((len(row) for row in rows), default=1), 1)
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            cells.append(
                {
                    "row_index": row_index,
                    "col_index": col_index,
                    "text": value,
                    "bbox": [
                        round(col_index * col_width, 4),
                        round(row_index * row_height, 4),
                        round((col_index + 1) * col_width, 4),
                        round((row_index + 1) * row_height, 4),
                    ],
                }
            )
    return cells




def _fac00004_aux_rows() -> list[list[str]]:
    return [
        ["", "", "", "", "", "", "", "", "", "", "", "山田菜", "備考欄"],
        ["", "", "", "献立", "合計", "#☆", "通所", "職員", "平森", "", "", "", ""],
        ["日 付", "区\n分", "", "", "", "", "", "", "肉蒸", "魚禁", "揚げ物", "", ""],
        ["", "", "", "", "70", "", "", "", "", "", "", "", ""],
        ["4/26\n(日)", "朝", "主", "鶏じゃが", "67", "66", "", "", "", "", "", "", ""],
        ["", "夕", "主", "麻婆豆腐", "59", "58", "", "", "", "", "6", "", ""],
    ]


def _fac00004_live_like_block_total_rows() -> list[list[str]]:
    return [
        ["", "", "", "", "", "", "", "", "", "", "", "山田菜", "備考欄"],
        ["", "", "", "献立", "合計", "#☆", "通所", "職員", "平森", "", "", "", ""],
        ["日 付", "区\n分", "", "", "", "", "", "", "肉蒸", "魚禁", "揚げ物", "", ""],
        ["", "", "", "", "70", "", "", "", "", "", "", "", ""],
        ["4/26\n(日)", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "昼", "", "", "67", "", "", "", "", "", "", "", ""],
        ["4/27\n(月)", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "朝", "", "", "70", "", "", "", "", "", "", "", ""],
        ["", "昼", "", "", "105", "", "", "", "", "", "", "", ""],
        ["", "夕", "", "", "72", "", "", "", "", "", "", "", ""],
        ["4/28/\n(火)", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "朝", "", "", "70", "", "", "", "", "", "", "", ""],
        ["", "昼", "", "", "105", "", "", "", "", "", "", "", ""],
        ["", "夕", "", "", "72", "", "", "", "", "", "", "", ""],
        ["4/20\n(水)", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "朝", "", "", "70", "", "", "", "", "", "", "", ""],
        ["", "昼", "", "", "105", "", "", "", "", "", "", "", ""],
        ["", "夕", "", "", "72", "", "", "", "", "", "", "", ""],
        ["4/30\n(木)", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "朝", "", "", "70", "", "", "", "", "", "", "", ""],
        ["", "昼", "", "", "102\n102", "", "", "", "", "", "", "", ""],
    ]










def test_get_order_review_summary_maps_blocked_saved_draft_to_user_facing_states(authored_sheet_case):
    from src.db import session_scope
    from src.models.order_ocr_cache import OrderOcrCache

    case = authored_sheet_case(quantity=2,
        cell_values=lambda region: "2" if region["role"] == "quantity" else "",
        menu_date=date(2026, 3, 21), period="2026-03@2026-03-21~2026-03-28")
    order = {"id": case.id}
    saved = case.save({"header": ["日付", "区分", "メニュー", "常食2F", "備考"],
        "rows": [["03/21", "朝", "Menu A", "7", "draft"]],
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "row_ids": ["draft-row-1"]})
    assert saved is not None

    cached_payload = order_service._load_order_ocr_cache(order["id"]) or {}
    cached_payload["_reparse_debug"] = {
        "error": "sheet_structural_projection_requires_review",
        "reject_reasons": ["sheet_structural_projection_requires_review"],
    }
    with session_scope() as session:
        cached = session.get(OrderOcrCache, case.id)
        assert cached is not None
        cached.payload = cached_payload

    with case.readonly():
        summary = order_service.get_order_review_summary(order["id"], ocr_status="done")
        assert order_service.get_current_sheet_context(case.id)["rows"] == [["03/21", "朝", "Menu A", "7", "draft"]]

    assert summary["ocr_review_state"] == "draft_ready"
    assert summary["ocr_review_stage"] == "needs_human_review"
    assert summary["ocr_reparse_status"] == "blocked"
    assert summary["ocr_has_saved_draft"] is True
    assert summary["ocr_auto_apply_blocked"] is True
    assert summary["ocr_draft_row_count"] == 1
    assert "draft_newer_than_lines" not in (summary.get("ocr_confirm_blockers") or [])
    assert all(
        item.get("code") != "draft_newer_than_lines"
        for item in (summary.get("ocr_confirm_blocker_details") or [])
    )
    assert any(
        item.get("code") == "auto_apply_blocked"
        for item in (summary.get("ocr_confirm_warning_details") or [])
    )
    assert summary["ocr_can_confirm"] is True


def test_prepare_llm_review_output_payload_preserves_evidence_for_applied_and_unresolved_changes():
    baseline = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "header": ["日付", "区分", "メニュー", "常食", "備考"],
        "rows": [
            ["03/21", "朝", "Menu A", "11", ""],
            ["03/21", "昼", "Menu B", "8", ""],
        ],
        "row_ids": ["row-1", "row-2"],
        "raw_output": {
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "rows": [
                        ["日付", "区分", "メニュー", "常食", "備考"],
                        ["03/21", "朝", "Menu A", "11", ""],
                        ["03/21", "昼", "Menu B", "8", ""],
                    ],
                }
            ]
        },
    }
    payload = {
        "rows": [
            ["03/21", "朝", "Menu A", "7", ""],
            ["03/21", "昼", "Menu B", "8", ""],
        ],
        "cell_issues": [
            {
                "row_id": "row-1",
                "field": "qty.regular_x",
                "issue_code": "ocr_misread",
                "confidence": 0.91,
                "evidence": "single digit 7 visible",
                "reason": "correct visible digit",
                "page_index": 1,
                "table_id": "p1_t1",
            },
            {
                "row_id": "row-2",
                "field": "remarks",
                "issue_code": "note_unclear",
                "confidence": 0.82,
                "evidence": "remarks area still unclear",
                "reason": "leave blank until human review",
                "page_index": 1,
                "table_id": "p1_t1",
            },
        ],
        "llm_review": {
            "status": "verified",
            "notes": "Corrected the breakfast quantity only.",
        },
    }
    template = {
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
    }

    prepared = order_service._prepare_llm_review_output_payload(
        payload=payload,
        baseline=baseline,
        template=template,
        pdf_variant_requested="corrected",
        pdf_variant_used="raw",
        pdf_variant_fallback_reason="corrected_pdf_unavailable_in_backend_cache",
    )

    assert prepared is not None
    assert len(prepared["applied_overwrites"]) == 1
    assert prepared["applied_overwrites"][0]["row_id"] == "row-1"
    assert prepared["applied_overwrites"][0]["field"] == "qty.regular_x"
    assert prepared["applied_overwrites"][0]["evidence"] == "single digit 7 visible"
    assert len(prepared["issues"]) == 1
    unresolved = prepared["issues"][0]
    assert unresolved["issue_id"] == "llm-review-2"
    assert unresolved["row_id"] == "row-2"
    assert unresolved["field"] == "remarks"
    assert unresolved["issue_code"] == "note_unclear"
    assert unresolved["current_text"] == ""
    assert unresolved["confidence"] == 0.82
    assert unresolved["evidence"] == "remarks area still unclear"
    assert unresolved["reason"] == "leave blank until human review"
    assert unresolved["table_id"] == "p1_t1"
    assert unresolved["page_index"] == 1
    assert unresolved["row_index"] == 1
    assert unresolved["source_row_index"] == 1
    assert unresolved["column_index"] == 4
    assert unresolved["col_index"] == 4
    assert prepared["needs_more_review"] is True
    assert prepared["output_payload"]["cell_issues"][0]["evidence"] == "remarks area still unclear"
    assert prepared["output_payload"]["llm_review"]["pdf_variant_requested"] == "corrected"
    assert prepared["output_payload"]["llm_review"]["pdf_variant_used"] == "raw"
    assert (
        prepared["output_payload"]["llm_review"]["pdf_variant_fallback_reason"]
        == "corrected_pdf_unavailable_in_backend_cache"
    )


def test_get_ocr_pages_defers_grid_recovery_when_template_metadata_is_partial(monkeypatch, authored_sheet_case):
    import json

    case = authored_sheet_case(quantity=2, produce=False, include_remarks=False,
        menu_date=date(2026, 3, 21), period="2026-03@2026-03-21~2026-03-28")
    authored = json.loads(case.source.read_text())
    envelope = authored["facilities"][0]
    for config in (envelope, envelope["versions"][0]["facility"]):
        template = config["fax_template"]
        template["units"] = "normalized"
        template["table_box"] = [0.1, 0.2, 0.9, 0.8]
        template["grid_column_edges"] = [0.1, 0.3, 0.5, 0.7, 0.9]
        template.pop("grid_row_edges", None)
    case.source.write_text(json.dumps(authored), encoding="utf-8")
    case.produce(cell_values="2")

    def unexpected_detection(*_args, **_kwargs):
        raise AssertionError("request path must not recompute overlay or PDF grid")

    monkeypatch.setattr(order_service, "detect_table_grid_image", unexpected_detection)
    monkeypatch.setattr(order_service, "detect_table_grid", unexpected_detection)
    with case.readonly():
        pages, error = order_service.get_ocr_pages(case.id)
        assert error is None
        assert isinstance(pages, dict)
        assert len(pages["pages"]) == 1
        assert pages["table_box"] == [0.1, 0.2, 0.9, 0.8]
        assert pages["grid_column_edges"] == [0.1, 0.3, 0.5, 0.7, 0.9]
        assert pages["grid_row_edges"] is None
        assert pages["grid_detection_status"] == "deferred"
        assert pages["grid_detection_deferred_reason"] == "missing_template_grid_metadata:grid_row_edges"
        assert pages["hakodate_overlay_status"] == "ready"
        assert pages["pages"][0]["synthetic_source"] == "hakodate_overlay_only"
        assert pages["pages"][0]["hakodate_overlay_url"]
        assert pages["pages"][0]["ocr_overlay_url"] is None


def test_get_ocr_output_and_sheet_prefer_active_evidence_over_stale_cache(authored_sheet_case):
    from src.db import session_scope
    from src.models.order_ocr_cache import OrderOcrCache

    case = authored_sheet_case(quantity=2, cell_values="9", include_remarks=False,
        menu_date=date(2026, 3, 21), period="2026-03@2026-03-21~2026-03-28")
    stale = {
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|03/21|朝|Menu A|3|",
            "table_rows": [["03/21", "朝", "Menu A", "3"]],
            "pages": [
                {
                    "page_index": 1,
                    "ocr_overlay_uri": "gs://bucket/stale-overlay.png",
                    "layout_overlay_uri": "gs://bucket/stale-layout.png",
                    "figure_uris": [],
                }
            ],
            "template_resolution": {
                "requested_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                "requested_template_ids": [
                    "fax_layout_regular_soft_mixer_forbidden_v1",
                    "fax_layout_floor_2f3f_v1",
                ],
                "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                "matched_template_id": "fax_layout_floor_2f3f_v1",
                "blocked": True,
                "blocked_reasons": ["template_mismatch"],
            },
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
        }
    with session_scope() as session:
        cached = session.get(OrderOcrCache, case.id)
        assert cached is not None
        cached.payload = stale
    selected = order_service.get_ocr_evidence_run(case.evidence)
    with case.readonly():
        payload, payload_error = order_service.get_ocr_output(case.id, persist_cache=False)
        sheet, sheet_error = order_service.get_ocr_sheet(case.id)
        assert payload_error is None and isinstance(payload, dict)
        assert payload["hakodate_canonical_pipeline"] == selected["payload_json"]["hakodate_canonical_pipeline"]
        assert payload["hakodate_ocr_evidence_records"] == selected["payload_json"]["hakodate_ocr_evidence_records"]
        assert any(record["raw_text"] == "9" for record in payload["hakodate_ocr_evidence_records"])
        assert sheet_error is None and isinstance(sheet, dict)
        assert sheet["rows"][0][:4] == ["03/21", "朝", "Menu A", "9"]
        with session_scope() as session:
            assert session.get(OrderOcrCache, case.id).payload == stale


def test_get_ocr_output_prefers_active_evidence_over_stale_job_output(authored_sheet_case):
    from copy import deepcopy
    from src.services.ocr_job_service import get_job

    case = authored_sheet_case(quantity=2, cell_values="9", include_remarks=False,
        menu_date=date(2026, 3, 21), period="2026-03@2026-03-21~2026-03-28")
    selected = order_service.get_ocr_evidence_run(case.evidence)
    terminal = deepcopy(get_job(case.job)["metrics"]["rerun_terminal_receipt"])
    from src.models.workflow_ocr_delivery import WorkflowOcrDelivery
    with session_scope() as session:
        owner = terminal["workflow_delivery_owner"]
        delivery = session.get(WorkflowOcrDelivery, owner["delivery_id"])
        assert delivery is not None
        assert delivery.order_id == case.id and delivery.job_id == case.job
        assert owner == {"delivery_id": delivery.id, "generation": delivery.delivery_generation,
                         "claim_token": delivery.claim_token}
    inject_stale_job_diagnostics(case, output_reference="gs://bucket/stale-output.json")
    assert get_job(case.job)["metrics"]["rerun_terminal_receipt"] == terminal
    before = get_job(case.job)
    with case.readonly():
        payload, error = order_service.get_ocr_output(case.id, persist_cache=False)
        assert error is None and isinstance(payload, dict)
        assert payload["hakodate_canonical_pipeline"] == selected["payload_json"]["hakodate_canonical_pipeline"]
        assert payload["hakodate_ocr_evidence_records"] == selected["payload_json"]["hakodate_ocr_evidence_records"]
        assert any(record["raw_text"] == "9" for record in payload["hakodate_ocr_evidence_records"])
        assert case.get("workflow-v2/sheet-source")["sheet"]["rows"] == [["03/21", "朝", "Menu A", "9"]]
        assert get_job(case.job) == before


def _fac4_original_quantities(history_case):
    from tests.integration.test_draft_sheet_service import _fac00004_owned_source
    rows = [
        ["04/26", "朝", "主", "鶏じゃが", "66", "", "", "", "", "", "", ""],
        ["04/26", "夕", "主", "麻婆豆腐", "58", "", "", "", "", "", "6", ""],
    ]
    entries = [{"id": f"redesign-aux-april26-{index}", "monthly_menu_id": "2026-04",
        "menu_date": date(2026, 4, 26), "daypart": row[1], "name": row[3],
        "category": "主", "slot_index": 0, "diet_type": "regular"}
        for index, row in enumerate(rows)]
    case, source, _, _ = _fac00004_owned_source(history_case,
        rows=rows, menu_entries=entries, lines=[])
    case.save(source["sheet"])
    return case, source


def _assert_aux_mapping_is_not_authority(case, stale, expected_rows):
    from src.db import session_scope
    from src.models.order_ocr_cache import OrderOcrCache
    from tests.integration.test_draft_sheet_service import _legacy_aux_target_refused

    for value in ("67", "59", "70"):
        _legacy_aux_target_refused(case, value)
    with session_scope() as session:
        cache = session.get(OrderOcrCache, case.id)
        assert cache is not None
        cache.payload = stale
    with case.readonly():
        resolution = candidate_resolution_service.build_column_mapping_resolution(stale)
        assert resolution["resolved_value"] is None
        assert resolution["blocked"] is True
        assert "position_fallback_removed" in resolution["blocked_reasons"]
        payload, error = order_service.get_ocr_output(case.id, persist_cache=False)
        assert error is None and isinstance(payload, dict)
        selected = order_service.get_ocr_evidence_run(case.evidence)
        assert payload["hakodate_ocr_evidence_records"] == selected["payload_json"]["hakodate_ocr_evidence_records"]
        assert case.get("workflow-v2/sheet-source")["sheet"]["rows"] == expected_rows
        with session_scope() as session:
            assert session.get(OrderOcrCache, case.id).payload == stale


def test_owned_aux_quantities_are_not_shifted_or_recomputed_from_stale_position_mapping(history_case):
    case, source = _fac4_original_quantities(history_case)
    rows = _fac00004_aux_rows()
    stale = {
            "status": "done",
            "table_raw": "|日付|区分|副区分|献立|合計|#☆|通所|職員|平森|肉蒸|魚禁|揚げ物|備考|\n|---|---|---|---|---|---|---|---|---|---|---|---|---|",
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "rows": rows,
                    "cells": _structured_cells(rows),
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                }
            ],
            "column_mapping_resolution": {
                "resolved_value": "4:qty.regular_x|5:qty.daycare_x|6:qty.staff_x|7:qty.no_meat_x|8:qty.no_fish_x|9:qty.no_fried_x|10:qty.change_1_x",
                "resolved_column_mapping_id": "4:qty.regular_x|5:qty.daycare_x|6:qty.staff_x|7:qty.no_meat_x|8:qty.no_fish_x|9:qty.no_fried_x|10:qty.change_1_x",
                "blocked": False,
                "blocked_reasons": [],
                "requires_user_choice": False,
                "decision_source": "position_fallback",
                "partial_quantity_mapping": False,
                "confidence": 0.91,
                "evidence_ref": {
                    "page_index": 1,
                    "table_id": "p1_t1",
                    "source_col_indexes": [4, 5, 6, 7, 8, 9, 10],
                },
            },
            "column_mapping_candidates": [
                {
                    "candidate_id": "pcm-stale-aux",
                    "candidate_type": "position_fallback_candidate",
                    "value": "4:qty.regular_x|5:qty.daycare_x|6:qty.staff_x|7:qty.no_meat_x|8:qty.no_fish_x|9:qty.no_fried_x|10:qty.change_1_x",
                    "label": "stale-aux",
                    "score": 0.91,
                    "decision_source": "position_fallback",
                    "auto_selectable": True,
                }
            ],
        }
    _assert_aux_mapping_is_not_authority(case, stale, source["sheet"]["rows"])


def test_missing_owned_artifact_cannot_be_recovered_from_stale_aux_mapping_without_tables(history_case):
    from src.services import canonical_ocr_artifact_service
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    case, source = _fac4_original_quantities(history_case)
    stale = {
            "status": "done",
            "table_raw": "|日付|区分|副区分|献立|合計|#☆|通所|職員|平森|肉蒸|魚禁|揚げ物|備考|\n|---|---|---|---|---|---|---|---|---|---|---|---|---|",
            "column_mapping_resolution": {
                "resolved_value": "4:qty.regular_x|5:qty.daycare_x|6:qty.staff_x|7:qty.no_meat_x|8:qty.no_fish_x|9:qty.no_fried_x|10:qty.change_1_x",
                "resolved_column_mapping_id": "4:qty.regular_x|5:qty.daycare_x|6:qty.staff_x|7:qty.no_meat_x|8:qty.no_fish_x|9:qty.no_fried_x|10:qty.change_1_x",
                "blocked": False,
                "blocked_reasons": [],
                "requires_user_choice": False,
                "decision_source": "position_fallback",
                "partial_quantity_mapping": False,
                "confidence": 0.91,
            },
            "column_mapping_candidates": [
                {
                    "candidate_id": "pcm-stale-aux",
                    "candidate_type": "position_fallback_candidate",
                    "value": "4:qty.regular_x|5:qty.daycare_x|6:qty.staff_x|7:qty.no_meat_x|8:qty.no_fish_x|9:qty.no_fried_x|10:qty.change_1_x",
                    "label": "stale-aux",
                    "score": 0.91,
                    "decision_source": "position_fallback",
                    "auto_selectable": True,
                }
            ],
        }
    _assert_aux_mapping_is_not_authority(case, stale, source["sheet"]["rows"])
    selected = order_service.get_ocr_evidence_run(case.evidence)
    original = selected["payload_json"]
    overlay = pathlib.Path(original["hakodate_overlay"]["uri"].removeprefix("file://"))
    assert overlay.is_file()
    assert overlay.is_relative_to(case.root)
    with session_scope() as session:
        stored_payload = deepcopy(session.get(OrderOcrEvidenceRun, case.evidence).payload_json)
    overlay.unlink()
    with case.readonly():
        with pytest.raises(ValueError, match="^canonical_artifact_bytes_unavailable$"):
            canonical_ocr_artifact_service.validate_payload(original)
        payload, error = order_service.get_ocr_output(case.id, persist_cache=False)
        assert payload is None
        assert error == "ocr_evidence_recovery_required"
        stored = order_service.get_ocr_evidence_run(case.evidence)
        assert stored["selectable"] is False
        assert stored["artifact_digest"] == selected["artifact_digest"]
        with session_scope() as session:
            assert session.get(OrderOcrEvidenceRun, case.evidence).payload_json == stored_payload


def _assert_selected_native_page(case):
    selected = order_service.get_ocr_evidence_run(case.evidence)
    original = selected["payload_json"]
    assert not original.get("pages")
    with case.readonly():
        pages, error = order_service.get_ocr_pages(case.id)
        assert error is None and isinstance(pages, dict)
        assert pages["page_count"] == 1
        page = pages["pages"][0]
        assert page["synthetic_source"] == "hakodate_overlay_only"
        assert page["hakodate_overlay_status"] == "ready"
        assert page["hakodate_overlay_uri"] == original["hakodate_overlay"]["uri"]
        assert page["hakodate_overlay_url"]
        assert page["ocr_overlay_uri"] is None
        assert page["ocr_overlay_url"] is None
        assert page["layout_overlay_uri"] is None
        assert page["layout_overlay_url"] is None
        assert order_service.get_ocr_evidence_run(case.evidence) == selected
    return pages


def test_get_ocr_pages_prefers_active_evidence_over_stale_cache(authored_sheet_case):
    from src.db import session_scope
    from src.models.order_ocr_cache import OrderOcrCache

    case = authored_sheet_case(quantity=2, cell_values="2", include_remarks=False,
        menu_date=date(2026, 3, 21), period="2026-03@2026-03-21~2026-03-28")
    stale = {"pages": [{"page_index": 1,
        "ocr_overlay_uri": "gs://bucket/stale-overlay.png",
        "layout_overlay_uri": "gs://bucket/stale-layout.png", "figure_uris": []}]}
    with session_scope() as session:
        cached = session.get(OrderOcrCache, case.id)
        assert cached is not None
        cached.payload = stale
    pages = _assert_selected_native_page(case)
    assert pages["pages"][0]["hakodate_overlay_uri"] != "gs://bucket/stale-overlay.png"
    with case.readonly(), session_scope() as session:
        assert session.get(OrderOcrCache, case.id).payload == stale


def test_get_ocr_pages_prefers_active_evidence_over_stale_job_output(authored_sheet_case):
    from copy import deepcopy
    from src.services.ocr_job_service import get_job

    case = authored_sheet_case(quantity=2, cell_values="2", include_remarks=False,
        menu_date=date(2026, 3, 21), period="2026-03@2026-03-21~2026-03-28")
    terminal = deepcopy(get_job(case.job)["metrics"]["rerun_terminal_receipt"])
    inject_stale_job_diagnostics(case, output_reference="gs://bucket/stale-output.json")
    assert get_job(case.job)["metrics"]["rerun_terminal_receipt"] == terminal
    before = get_job(case.job)
    _assert_selected_native_page(case)
    with case.readonly():
        assert get_job(case.job) == before


def test_get_ocr_sheet_prefers_saved_revision_over_confirmed_lines_by_default(authored_sheet_case):
    from src.db import session_scope
    from src.models.order import OrderLine
    from src.models.order_ocr_cache import OrderOcrCache
    from tests.helpers.authored_sheet_case import save_draft

    case = authored_sheet_case(quantity=2,
        cell_values=lambda region: "2" if region["role"] == "quantity" else "",
        menu_date=date(2026, 3, 21), period="2026-03@2026-03-21~2026-03-28")
    saved_sheet = {"order_id": case.id, "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
        "rows": [["03/21", "朝", "Menu A", "9", "draft"]],
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "row_ids": ["draft-row-1"], "source": "weekly_menu+ocr_payload"}
    saved = save_draft(case, saved_sheet)
    assert saved is not None
    stale = {
                "table_raw": "|日付|区分|メニュー|常食2F|備考|\n|---|---|---|---|---|\n|03/21|朝|Menu A|2||",
                "pages": [
                    {
                        "page_index": 1,
                        "markdown_uri": None,
                        "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                        "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                        "figure_uris": [],
                    }
                ],
                "quantity_subgrid_passes": [],
                "template_resolution": {"resolved_template_id": "tpl-1", "blocked": False, "blocked_reasons": []},
                "table_box": [0.1, 0.2, 0.9, 0.8],
                "grid_column_edges": [0.1, 0.5, 0.9],
                "grid_row_edges": [0.2, 0.4, 0.8],
            }
    with session_scope() as session:
        cache = session.get(OrderOcrCache, case.id)
        assert cache is not None
        cache.payload = stale
    with case.readonly():
        sheet, error = order_service.get_ocr_sheet(case.id)
        assert error is None
        assert isinstance(sheet, dict)
        assert sheet["rows"] == [["03/21", "朝", "Menu A", "9", "draft"]]
        assert sheet["rows"][0][3] == "9"
        assert sheet["source"] == saved_sheet["source"]
        context = order_service.get_current_sheet_context(case.id)
        assert context is not None and context["draft_id"] == saved["id"]
        assert context["draft_record"]["draft_sheet_json"] == saved_sheet
        assert case.get("workflow-v2/sheet-source")["sheet"]["rows"] == [["03/21", "朝", "Menu A", "2", ""]]
        with session_scope() as session:
            lines = session.query(OrderLine).filter_by(order_id=case.id).all()
            assert lines == []
            assert session.get(OrderOcrCache, case.id).payload == stale


def test_build_recoverable_ocr_sheet_payload_never_uses_confirmed_lines_fallback(authored_sheet_case):
    from src.db import session_scope
    from src.models.order import OrderLine
    from src.models.order_ocr_cache import OrderOcrCache

    case = authored_sheet_case(quantity=2, cell_values="4", include_remarks=False,
        menu_date=date(2026, 3, 21), period="2026-03@2026-03-21~2026-03-28")
    stale = {
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|03/21|朝|Menu A|4|",
            "pages": [
                {
                    "page_index": 1,
                    "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                    "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                    "figure_uris": [],
                }
            ],
            "template_resolution": {"resolved_template_id": "tpl-1", "blocked": False, "blocked_reasons": []},
            "table_box": [0.1, 0.2, 0.9, 0.8],
            "grid_column_edges": [0.1, 0.5, 0.9],
            "grid_row_edges": [0.2, 0.4, 0.8],
        }
    with session_scope() as session:
        cache = session.get(OrderOcrCache, case.id)
        assert cache is not None
        cache.payload = stale
    with case.readonly():
        sheet, error = order_service.build_recoverable_ocr_sheet_payload(
            case.id, "ocr_evidence_recovery_required")
        assert error is None
        assert isinstance(sheet, dict)
        assert sheet.get("recovery_source") != "confirmed_lines"
        assert (sheet.get("trace") or {}).get("mapped_mode") != "confirmed_lines"
        assert sheet["rows"] == [["03/21", "朝", "Menu A", "4"]]
        with session_scope() as session:
            lines = session.query(OrderLine).filter_by(order_id=case.id).all()
            assert lines == []
            assert session.get(OrderOcrCache, case.id).payload == stale


def test_owned_canonical_sheet_is_not_blocked_by_unowned_template_resolution(selected_projection_case):
    case = selected_projection_case(layout="authored-split", facility_id="FAC00001",
        period="2026-03@2026-03-21~2026-03-28", unlisted_cells="",
        rows=[{"date":"2026-03-21","daypart":"朝","menu":"Menu A","cells":{"qty.regular_2f":"2"}}])
    _assert_unowned_projection_is_not_adopted(case, {"table_raw": "|03/21|朝|Menu A|2||", "table_box": [0.1, 0.2, 0.9, 0.8], "template_resolution": {"resolved_template_id": None, "blocked": True, "blocked_reasons": ["template_resolution_missing"]}})


def test_owned_specimen_ignores_unowned_alternate_template_selection(selected_projection_case):
    case = selected_projection_case(layout="repository-specimen", facility_id="FAC00016",
        period="2026-03@2026-03-21~2026-03-28", unlisted_cells="",
        rows=[{"date":"2026-03-21","daypart":"朝","menu":"Menu A","cells":{"qty.regular_x":"5","qty.diabetes_x":"0","qty.no_meat_x":"1","qty.no_fish_x":"0","qty.change_1_x":"0","qty.change_2_x":"0"}}])
    _assert_unowned_projection_is_not_adopted(case, {"table_rows": [["03/21", "朝", "Menu A", "5", "0", "1", "0", "0", "0", ""]], "template_resolution": {"requested_template_ids": ["fax_layout_regular_diabetes_v1", "fax_layout_regular_forbidden_v1"], "resolved_template_id": "fax_layout_regular_forbidden_v1", "blocked": False}})


def test_owned_canonical_seven_is_not_replaced_by_ambiguous_equivalent_template_hints(selected_projection_case):
    case = selected_projection_case(layout="repository-specimen", facility_id="FAC00016",
        period="2026-03@2026-03-21~2026-03-28", unlisted_cells="",
        rows=[{"date":"2026-03-21","daypart":"朝","menu":"Menu A","cells":{"qty.regular_x":"7","qty.diabetes_x":"0"}}])
    _assert_unowned_projection_is_not_adopted(case, {"table_rows": [["03/21", "朝", "Menu A", "5", "0", "1", "0", "0", "0", ""]], "template_resolution": {"requested_template_ids": ["fax_layout_regular_diabetes_v1", "fax_layout_regular_forbidden_v1"], "resolved_template_id": None, "blocked": True, "blocked_reasons": ["template_resolution_missing"]}})


def test_missing_canonical_template_is_not_supplied_by_unowned_table_semantics(selected_projection_case):
    case = selected_projection_case(layout="authored-split", facility_id="FAC00001",
        period="2026-03@2026-03-21~2026-03-28", unlisted_cells="",
        rows=[{"date":"2026-03-21","daypart":"朝","menu":"Menu A","cells":{"qty.regular_2f":"9"}}])
    captured = order_service._capture_synchronous_cache_write_input(case.id)
    missing = deepcopy(captured["configured_input"]["facility"])
    missing.pop("fax_template")
    with pytest.raises(ValueError, match="fax_template"):
        config_service._validate_authored_facility_config(missing, None)
    _assert_unowned_projection_is_not_adopted(case, {"table_raw": "|03/21|朝|Menu A|9||", "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr-page-1.png"}]})


def test_owned_six_columns_preserve_nine_through_four_without_raw_header_adoption(selected_projection_case):
    case = selected_projection_case(layout="authored-split", facility_id="FAC00001",
        period="2026-03@2026-03-21~2026-03-28", unlisted_cells="",
        rows=[{"date":"2026-03-21","daypart":"朝","menu":"Menu A","cells":{"qty.regular_2f":"9","qty.regular_3f":"8","qty.soft_2f":"7","qty.soft_3f":"6","qty.mixer_2f":"5","qty.mixer_3f":"4"}}])
    _assert_unowned_projection_is_not_adopted(case, {"template_id": "fax_layout_regular_soft_mixer_forbidden_v1", "table_raw": "|日付|区分|メニュー|常食2F|常食3F|軟菜2F|軟菜3F|ミキサー2F|ミキサー3F|備考|\n|03/21|朝|Menu A|9|8|7|6|5|4||"})


def test_owned_six_columns_do_not_execute_legacy_position_fallback(selected_projection_case, monkeypatch):
    case = selected_projection_case(layout="authored-split", facility_id="FAC00001",
        period="2026-03@2026-03-21~2026-03-28", unlisted_cells="",
        rows=[{"date":"2026-03-21","daypart":"朝","menu":"Menu A","cells":{"qty.regular_2f":"9","qty.regular_3f":"8","qty.soft_2f":"7","qty.soft_3f":"6","qty.mixer_2f":"5","qty.mixer_3f":"4"}}])
    def forbidden(*args, **kwargs):
        pytest.fail("Current selected quantities must not use legacy position fallback")
    monkeypatch.setattr(order_service, "_apply_menu_position_mapping", forbidden)
    rows = [["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"], ["03/21", "朝", "Menu A", "9", "8", "7", "6", "5", "4", ""]]
    _assert_unowned_projection_is_not_adopted(case, {"tables": [{"page_index": 1, "table_id": "page1_table1", "rows": rows, "row_count": len(rows), "col_count": len(rows[0]), "cells": _structured_cells(rows)}]})


def test_owned_cross_month_projection_keeps_explicit_period_and_typed_quantities(selected_projection_case):
    case = selected_projection_case(layout="repository-specimen", facility_id="FAC00014",
        period="2026-04@2026-04-26~2026-05-02", unlisted_cells="",
        rows=[{"date":"2026-04-26","daypart":"朝","menu":"Menu A","cells":{"qty.regular_x":"102","qty.staff_x":"2","qty.no_meat_x":"2"}},{"date":"2026-04-26","daypart":"朝","menu":"Menu B","cells":{"qty.regular_x":"104","qty.staff_x":"2"}},{"date":"2026-05-01","daypart":"朝","menu":"Menu C","cells":{"qty.regular_x":"107","qty.staff_x":"3"}}])
    period = "2026-04@2026-04-26~2026-05-02"
    assert case.sheet["week_id"] == period
    assert order_service.get_order_by_id(case.id)["persisted_week_value"] == period
    _assert_unowned_projection_is_not_adopted(case, {"candidate_resolution": {"resolved_week_id": "2026-04@2026-04-26~2026-05-01", "resolved_template_id": "fax_layout_regular_staff_daycare_v1"}, "table_rows": [["04/26", "朝", "Menu A", "102", "2", "2"], ["04/26", "朝", "Menu B", "104", "2", ""], ["05/01", "朝", "Menu C", "107", "3", ""]]})


def test_owned_cross_month_rows_do_not_discover_unowned_page_markdown(selected_projection_case, monkeypatch):
    case = selected_projection_case(layout="repository-specimen", facility_id="FAC00014",
        period="2026-04@2026-04-26~2026-05-02", unlisted_cells="",
        rows=[{"date":"2026-04-26","daypart":"朝","menu":"Menu A","cells":{"qty.regular_x":"102","qty.staff_x":"2","qty.no_meat_x":"2"}},{"date":"2026-04-26","daypart":"朝","menu":"Menu B","cells":{"qty.regular_x":"104","qty.staff_x":"2"}},{"date":"2026-05-01","daypart":"朝","menu":"Menu C","cells":{"qty.regular_x":"107","qty.staff_x":"3"}},{"date":"2026-05-02","daypart":"朝","menu":"Menu D","cells":{"qty.regular_x":"109","qty.staff_x":"4"}}])
    real_load = order_service.load_bytes_from_uri
    def reject_unowned(uri, *args, **kwargs):
        assert uri not in {"gs://bucket/page-1.md", "gs://bucket/page-2.md"}
        return real_load(uri, *args, **kwargs)
    monkeypatch.setattr(order_service, "load_bytes_from_uri", reject_unowned)
    assert case.sheet["week_id"] == "2026-04@2026-04-26~2026-05-02"
    _assert_unowned_projection_is_not_adopted(case, {"pages": [{"page_index": 1, "markdown_uri": "gs://bucket/page-1.md"}, {"page_index": 2, "markdown_uri": "gs://bucket/page-2.md"}], "table_rows": [["04/26", "朝", "Menu A", "102", "2", "2"], ["04/26", "朝", "Menu B", "104", "2", ""], ["05/01", "朝", "Menu C", "107", "3", ""], ["05/02", "朝", "Menu D", "109", "4", ""]]})


def test_augment_payload_with_candidate_resolution_skips_choice_required_column_mapping():
    payload = {
        "template_id": "fax_layout_regular_staff_daycare_v1",
        "column_mapping_resolution": {
            "blocked": True,
            "blocked_reasons": ["existing_block"],
            "requires_user_choice": True,
        },
    }

    original_get_order_candidate_resolution = order_service.get_order_candidate_resolution
    order_service.get_order_candidate_resolution = lambda _order_id: {
        "order_id": "ORD-test",
        "resolutions": {
            "template": {
                "decision_type": "template",
                "resolved_value": "fax_layout_regular_staff_daycare_v1",
                "confidence": "high",
                "blocked": False,
                "blocked_reasons": [],
                "requires_user_choice": False,
                "candidates": [],
            },
            "column_mapping": {
                "decision_type": "column_mapping",
                "resolved_value": "4:qty.regular_x",
                "confidence": "high",
                "blocked": True,
                "blocked_reasons": ["column_mapping_choice_required"],
                "requires_user_choice": True,
                "candidates": [
                    {
                        "candidate_id": "pcm-a",
                        "value": "4:qty.regular_x",
                        "label": "candidate-a",
                        "score": 0.55,
                        "decision_source": "position_fallback",
                    }
                ],
            },
        },
    }
    try:
        augmented = order_service._augment_payload_with_candidate_resolution("ORD-test", payload)
    finally:
        order_service.get_order_candidate_resolution = original_get_order_candidate_resolution

    assert isinstance(augmented, dict)
    resolution = augmented.get("column_mapping_resolution")
    assert isinstance(resolution, dict)
    assert resolution.get("resolved_value") in (None, "")
    assert resolution.get("blocked") is True
    assert resolution.get("requires_user_choice") is True
    assert augmented.get("column_mapping_candidates") in (None, [])


def test_sheet_payload_mapping_block_reason_rejects_payload_declared_position_fallback():
    from copy import deepcopy
    template = {
        "row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "qty.change_1_x",
            "remarks",
        ]
    }
    payload = {
        "tables": [
            {"table_id": "p1_t1", "rows": [["4/26", "朝", "Menu A", "102", "2", "2", "", "", "", ""]]},
            {"table_id": "p2_t1", "rows": [["5/01", "朝", "Menu B", "107", "3", "", "", "", "", ""]]},
        ],
        "template_resolution": {
            "resolved_template_id": "fax_layout_regular_staff_daycare_v1",
            "blocked": False,
            "blocked_reasons": [],
        },
        "column_mapping_resolution": {
            "resolved_value": "4:qty.regular_x|5:qty.staff_x|6:qty.no_meat_x|7:qty.no_fish_x|8:qty.sesame_allergy_x|9:qty.change_1_x",
            "decision_source": "position_fallback",
            "blocked": False,
            "blocked_reasons": [],
            "requires_user_choice": False,
            "mapped_quantity_fields": [
                "qty.regular_x",
                "qty.staff_x",
                "qty.no_meat_x",
                "qty.no_fish_x",
                "qty.sesame_allergy_x",
                "qty.change_1_x",
            ],
            "expected_quantity_fields": [
                "qty.regular_x",
                "qty.staff_x",
                "qty.no_meat_x",
                "qty.no_fish_x",
                "qty.sesame_allergy_x",
                "qty.change_1_x",
            ],
            "partial_quantity_mapping": False,
            "evidence_ref": {"page_index": 1, "table_id": "p1_t1"},
        },
    }

    original_payload, original_template = deepcopy(payload), deepcopy(template)
    reason = order_service._sheet_payload_mapping_block_reason(
        source="weekly_menu",
        ocr_payload=payload,
        template=template,
        evidence_missing=[],
        template_blockers=[],
    )

    assert reason == "unresolved_template"
    assert payload == original_payload
    assert template == original_template


def test_owned_multi_row_current_sheet_retains_selected_quantities(selected_projection_case):
    case = selected_projection_case(layout="repository-specimen", facility_id="FAC00014",
        period="2026-04@2026-04-26~2026-05-02",
        rows=[{"date":"2026-04-26","daypart":"朝","menu":"Menu A","cells":{"qty.regular_x":"102","qty.staff_x":"2","qty.no_meat_x":"2"}},{"date":"2026-04-26","daypart":"朝","menu":"Menu B","cells":{"qty.regular_x":"104","qty.staff_x":"2"}},{"date":"2026-05-01","daypart":"朝","menu":"Menu C","cells":{"qty.regular_x":"107","qty.staff_x":"3"}}], unlisted_cells="")
    payload = {"tables": [
        {"page_index": 1, "table_id": "unowned-april",
         "rows": [["4/26", "朝", "", "Menu A", "102", "2", "2"]]},
        {"page_index": 2, "table_id": "unowned-may",
         "rows": [["5/1", "朝", "", "Menu C", "107", "3"]]}],
        "column_mapping": {"decision_source": "position_fallback", "resolved_value": "4:qty.regular_x|5:qty.staff_x"}}
    _assert_unowned_projection_is_not_adopted(case, payload)
    current = order_service.get_current_sheet_context(case.id)
    assert current["base_evidence_run_id"] == case.evidence["id"]
    fields = current["fields"]
    for source in case.source_rows:
        matching = [row for row in current["rows"]
                    if row[fields.index("menu")] == source["menu"]]
        assert len(matching) == 1
        for field, value in source["cells"].items():
            assert matching[0][fields.index(field)] == value


def test_unowned_ambiguous_multi_table_mapping_cannot_replace_selected_source(selected_projection_case):
    case = selected_projection_case(layout="repository-specimen", facility_id="FAC00014",
        period="2026-04@2026-04-26~2026-05-02",
        rows=[{"date":"2026-04-26","daypart":"朝","menu":"Menu A","cells":{"qty.regular_x":"102","qty.staff_x":"2","qty.no_meat_x":"2"}},{"date":"2026-05-01","daypart":"朝","menu":"Menu C","cells":{"qty.regular_x":"107","qty.staff_x":"3"}}], unlisted_cells="")
    payload = {"tables": [
        {"page_index": 1, "table_id": "unowned-april",
         "rows": [["4/26", "朝", "", "Menu A", "102", "2", "2"]]},
        {"page_index": 2, "table_id": "unowned-may",
         "rows": [["5/1", "朝", "", "Menu C", "107", "3"]]}],
        "column_mapping": {"blocked": True, "requires_user_choice": True, "candidates": [{"value": "4:qty.regular_x|5:qty.staff_x"}, {"value": "4:qty.staff_x|5:qty.regular_x"}]}}
    _assert_unowned_projection_is_not_adopted(case, payload)
    current = order_service.get_current_sheet_context(case.id)
    assert current["base_evidence_run_id"] == case.evidence["id"]
    fields = current["fields"]
    for source in case.source_rows:
        matching = [row for row in current["rows"]
                    if row[fields.index("menu")] == source["menu"]]
        assert len(matching) == 1
        for field, value in source["cells"].items():
            assert matching[0][fields.index(field)] == value


def test_owned_regular_quantities_are_not_reassigned_by_noisy_unowned_headers(selected_projection_case):
    case = selected_projection_case(layout="repository-specimen", facility_id="FAC00002",
        period="2026-03@2026-03-22~2026-03-28",
        rows=[{"date":"2026-03-22","daypart":"朝","menu":"Menu A","cells":{"qty.regular_x":"23"}},{"date":"2026-03-22","daypart":"昼","menu":"Menu B","cells":{"qty.regular_x":"27"}}], unlisted_cells="")
    _assert_unowned_projection_is_not_adopted(case, {
        "tables": [{"page_index": 1, "table_id": "unowned-noisy-table",
                    "rows": [["日 付","区 分","","献立","常食","","事故","","変更の","変更の","備考欄"],["3/22","\"","VF","Menu A","23"],["","","48","Menu B","27"]]}]})


def test_owned_regular_blanks_and_forty_four_keep_physical_menu_positions(selected_projection_case):
    # Separate physical cells are explicitly authored for independent blank/44 values.
    case = selected_projection_case(layout="repository-specimen", facility_id="FAC00007",
        body_merge_policy={"mode": "none"},
        period="2026-03@2026-03-22~2026-03-28",
        rows=[{"date":"2026-03-22","daypart":"朝","menu":"Menu A","cells":{"qty.regular_x":""}},{"date":"2026-03-22","daypart":"朝","menu":"Menu B","cells":{"qty.regular_x":""}},{"date":"2026-03-22","daypart":"朝","menu":"Menu C","cells":{"qty.regular_x":"44"}},{"date":"2026-03-22","daypart":"朝","menu":"Menu D","cells":{"qty.regular_x":""}},{"date":"2026-03-22","daypart":"朝","menu":"Menu E","cells":{"qty.regular_x":""}},{"date":"2026-03-22","daypart":"朝","menu":"Menu F","cells":{"qty.regular_x":"44"}}], unlisted_cells="")
    _assert_unowned_projection_is_not_adopted(case, {
        "tables": [{"page_index": 1, "table_id": "unowned-noisy-table",
                    "rows": [["4 日","区 分","","献立","茶食","","禁食"],["3/22","明","WO","Menu A",""],["","","(2)","Menu B",""],["","&","¥¥","Menu C","44"],["","","金属","Menu D",""],["","","福","Menu E",""],["","$","¥","Menu F","44"]]}]})


def test_merged_authored_cell_cannot_hold_both_blank_and_forty_four(selected_projection_case):
    with pytest.raises(AssertionError, match="a merged authored region must have one explicit value"):
        selected_projection_case(
            layout="repository-specimen", facility_id="FAC00007",
            period="2026-03@2026-03-22~2026-03-28", unlisted_cells="",
            rows=[
                {"date": "2026-03-22", "daypart": "朝", "menu": "Menu A", "cells": {"qty.regular_x": ""}},
                {"date": "2026-03-22", "daypart": "朝", "menu": "Menu B", "cells": {"qty.regular_x": "44"}},
            ],
        )


def test_owned_soft_and_bag_quantities_do_not_depend_on_partial_legacy_mapping(selected_projection_case):
    case = selected_projection_case(layout="repository-specimen", facility_id="FAC00005",
        period="2026-03@2026-03-22~2026-03-28",
        rows=[{"date":"2026-03-22","daypart":"朝","menu":"Menu A","cells":{"qty.soft_x":"57","qty.regular_bag_x":"2"}},{"date":"2026-03-22","daypart":"昼","menu":"Menu B","cells":{"qty.soft_x":"58","qty.regular_bag_x":"4"}}], unlisted_cells="")
    _assert_unowned_projection_is_not_adopted(case, {"tables": [{"rows": [["3/22", "朝", "", "Menu A", "57", "2"], ["", "昼", "", "Menu B", "58", "4"]]}], "column_mapping": {"mapped_fields": ["qty.soft_x"], "unmapped_fields": ["qty.regular_bag_x"], "blocked": True}})


def test_owned_blank_and_lunch_quantities_cannot_be_replaced_by_unowned_rotation_receipt(selected_projection_case):
    case = selected_projection_case(layout="repository-specimen", facility_id="FAC00002",
        period="2026-03@2026-03-22~2026-03-28",
        rows=[{"date":"2026-03-22","daypart":"朝","menu":"Menu A","cells":{"qty.regular_x":""}},{"date":"2026-03-22","daypart":"昼","menu":"Menu B","cells":{"qty.regular_x":"23"}},{"date":"2026-03-22","daypart":"昼","menu":"Menu C","cells":{"qty.regular_x":"27"}}], unlisted_cells="")
    _assert_unowned_projection_is_not_adopted(case, {"tables": [{"rows": [["3/22", "IN", "HKD", "Menu A", ""], ["", "VF", "", "Menu B", "23"], ["", "", "48", "Menu C", "27"]]}], "page_correction": {"applied": True, "document_rotation_deg": 90}, "page_correction_artifacts": {"template_warp_page_indexes": [1], "position_normalized": True}})


def test_owned_canonical_columns_do_not_invent_aux_totals_from_unowned_headers(selected_projection_case):
    case = selected_projection_case(layout="repository-specimen", facility_id="FAC00004",
        period="2026-04@2026-04-26~2026-04-30", unlisted_cells="", rows=[
            {"date": "2026-04-26", "daypart": "昼", "menu": "Menu A",
             "cells": {"qty.regular_x": "10", "qty.daycare_x": "1", "qty.staff_x": "1"}},
            {"date": "2026-04-26", "daypart": "昼", "menu": "Menu B",
             "cells": {"qty.regular_x": "8", "qty.daycare_x": "", "qty.staff_x": "1"}}])
    assert "aux.col_4" not in case.sheet["fields"]
    _assert_unowned_projection_is_not_adopted(case, {"tables": [{"rows": [
        ["日付", "区分", "副区分", "メニュー", "合計", "常食", "通所", "職員"],
        ["4/26", "昼", "主", "Menu A", "12", "10", "1", "1"],
        ["", "昼", "副", "Menu B", "9", "8", "", "1"]]}]})


def test_extract_sheet_rows_from_payload_rejects_position_fallback_for_noisy_fac00004_table(repository_layout_source):
    template = (config_service.get_facility_config("FAC00004") or {}).get("fax_template") or {}
    rows = _fac00004_aux_rows()
    payload = {
        "table_raw": "|日付|区分|副区分|献立|合計|#☆|通所|職員|平森|肉蒸|魚禁|揚げ物|備考|\n|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        "tables": [
            {
                "page_index": 1,
                "table_id": "page1_table1",
                "row_count": len(rows),
                "col_count": len(rows[0]),
                "rows": rows,
                "cells": _structured_cells(rows),
            }
        ],
        "column_mapping_resolution": {
            "resolved_value": (
                "5:qty.regular_x|6:qty.daycare_x|7:qty.staff_x|8:qty.no_meat_x|"
                "9:qty.no_fish_x|10:qty.no_fried_x|11:qty.change_1_x"
            ),
            "resolved_column_mapping_id": (
                "5:qty.regular_x|6:qty.daycare_x|7:qty.staff_x|8:qty.no_meat_x|"
                "9:qty.no_fish_x|10:qty.no_fried_x|11:qty.change_1_x"
            ),
            "blocked": False,
            "blocked_reasons": [],
            "requires_user_choice": False,
            "decision_source": "position_fallback",
            "partial_quantity_mapping": False,
            "confidence": 0.99,
        },
    }

    original = deepcopy(payload)
    with pytest.raises(fax_extractor.CanonicalOcrProjectionError,
                       match="^sheet_quantity_column_unmapped$"):
        order_service._extract_sheet_rows_from_payload(payload, template)
    assert payload == original


def test_extract_sheet_rows_from_resolved_mapping_overlays_explicit_aux_columns_when_mapping_meta_omits_them(monkeypatch):
    template = _authored_aux_component_template()
    rows = _fac00004_aux_rows()
    payload = {
        "table_raw": "|日付|区分|副区分|献立|合計|#☆|通所|職員|平森|肉蒸|魚禁|揚げ物|備考|\n|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        "tables": [
            {
                "page_index": 1,
                "table_id": "page1_table1",
                "row_count": len(rows),
                "col_count": len(rows[0]),
                "rows": rows,
                "cells": _structured_cells(rows),
            }
        ],
        "column_mapping_resolution": {
            "resolved_value": (
                "5:qty.regular_x|6:qty.daycare_x|7:qty.staff_x|8:qty.no_meat_x|"
                "9:qty.no_fish_x|10:qty.no_fried_x|11:qty.change_1_x"
            ),
            "resolved_column_mapping_id": (
                "5:qty.regular_x|6:qty.daycare_x|7:qty.staff_x|8:qty.no_meat_x|"
                "9:qty.no_fish_x|10:qty.no_fried_x|11:qty.change_1_x"
            ),
            "blocked": False,
            "blocked_reasons": [],
            "requires_user_choice": False,
            "decision_source": "resolved_column_mapping",
            "partial_quantity_mapping": False,
            "confidence": 0.99,
        },
    }

    original_resolve_structured_table_mapping = fax_extractor._resolve_structured_table_mapping

    def _fake_resolve_structured_table_mapping(_matrix, _template):
        return (
            {
                "fields": order_service._get_row_fields(template),
                "mapped_indexes": {
                    0: 0,
                    1: 1,
                    3: 3,
                    5: 5,
                    6: 6,
                    7: 7,
                    8: 8,
                    9: 9,
                    10: 10,
                    11: 11,
                },
                "row_map": {4: 0, 5: 1},
            },
            [],
        )

    monkeypatch.setattr(
        fax_extractor,
        "_resolve_structured_table_mapping",
        _fake_resolve_structured_table_mapping,
    )

    try:
        extracted = order_service._extract_sheet_rows_from_resolved_column_mapping(payload, template)
    finally:
        monkeypatch.setattr(
            fax_extractor,
            "_resolve_structured_table_mapping",
            original_resolve_structured_table_mapping,
        )

    assert extracted[0][:7] == ["4/26 (日)", "朝", "主", "鶏じゃが", "67", "66", ""]
    assert extracted[1][:7] == ["", "夕", "主", "麻婆豆腐", "59", "58", ""]


def test_helper_diagnostics_do_not_fill_blank_totals_from_live_like_sequences():
    template = _authored_aux_component_template()
    fields = order_service._get_row_fields(template)
    header = order_service._sheet_header_from_template(fields, template)
    header[4] = "合計"
    rows = [
        ["04/26", "朝", "", "大豆のトマト煮", "", "70", "", "", "", "", "", "", ""],
        ["04/26", "朝", "", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "", "67", "", "", "", "", "", "", ""],
        ["04/26", "昼", "", "サワラの揚げ浸し", "", "67", "", "", "", "", "", "", ""],
        ["04/26", "夕", "", "豚肉のピリ辛炒め", "", "67", "", "", "", "", "", "", ""],
        ["04/27", "朝", "", "じゃが芋の煮物", "", "70", "", "", "", "", "", "", ""],
        ["04/27", "昼", "", "オムレツミートソース", "", "67", "", "", "", "", "", "", ""],
        ["04/27", "夕", "", "鶏肉の和風あんかけ", "", "67", "", "", "", "", "", "", ""],
        ["04/28", "朝", "", "野菜の卵とじ", "", "70", "", "", "", "", "", "", ""],
        ["04/28", "昼", "", "豆腐ﾊﾝﾊﾞｰｸﾞ", "", "67", "", "", "", "", "", "", ""],
        ["04/28", "夕", "", "豚肉とじゃが芋の醤油炒め", "", "67", "", "", "", "", "", "", ""],
        ["04/29", "朝", "", "厚揚げと里芋の煮物", "", "70", "", "", "", "", "", "", ""],
        ["04/29", "昼", "", "タラの野菜あんかけ", "", "58", "34", "", "", "", "", "", ""],
        ["04/29", "夕", "", "鶏肉と根菜の煮込み", "", "65", "", "", "2", "", "", "", ""],
        ["04/30", "朝", "", "ジャーマンポテト", "", "70", "", "", "", "", "", "", ""],
        ["04/30", "昼", "", "豚肉と大根の生姜煮", "", "67", "34", "", "", "", "", "", ""],
        ["04/30", "夕", "", "メンチカツ", "", "61", "", "5", "", "", "6", "", ""],
    ]
    payload = {
        "table_raw": "|日付|区分|副区分|献立|合計|#☆|通所|職員|平森|肉蒸|魚禁|揚げ物|備考|\n|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        "tables": [
            {
                "page_index": 1,
                "table_id": "page1_table1",
                "row_count": len(_fac00004_live_like_block_total_rows()),
                "col_count": len(_fac00004_live_like_block_total_rows()[0]),
                "rows": _fac00004_live_like_block_total_rows(),
                "cells": _structured_cells(_fac00004_live_like_block_total_rows()),
            }
        ],
    }

    original_rows, original_payload = deepcopy(rows), deepcopy(payload)
    projected, stats = order_service._canonicalize_block_helper_display_rows(
        rows=rows,
        fields=fields,
        header=header,
    )

    assert projected == original_rows
    assert rows == original_rows and payload == original_payload
    assert all(row[4] == "" for row in projected)
    assert stats["helper_reanchor_count"] == 0


def test_extract_sheet_rows_from_resolved_column_mapping_uses_template_columns_for_aux_markdown_tables():
    template = _authored_aux_component_template(hiramori=True)
    payload = {
        "table_raw": "\n".join(
            [
                "|日付|区分|副区分|献立|合計|#☆|通所|職員|平森|肉蒸|魚禁|揚げ物|備考|",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
                "|04/26|朝|主|大豆のトマト煮|70|70|70|||||||",
                "|04/26|朝|副①|胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ|70|67|3||||6||",
            ]
        ),
        "column_mapping_resolution": {
            "resolved_value": (
                "5:qty.regular_x|6:qty.daycare_x|7:qty.staff_x|8:qty.hiramori_x|"
                "9:qty.no_meat_x|10:qty.no_fish_x|11:qty.no_fried_x"
            ),
            "decision_source": "resolved_column_mapping",
        },
    }

    extracted = order_service._extract_sheet_rows_from_resolved_column_mapping(payload, template)

    assert extracted[0][:7] == ["04/26", "朝", "主", "大豆のトマト煮", "70", "70", "70"]
    assert extracted[1][:7] == ["04/26", "朝", "副①", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "70", "67", "3"]


def _authored_single_floor_template():
    return {"columns": [
        {"index": 0, "source_index": 0, "name": "date_mmdd", "role": "date"},
        {"index": 1, "source_index": 1, "name": "daypart", "role": "daypart"},
        {"index": 2, "source_index": 2, "name": "menu", "role": "menu_name"},
        {"index": 3, "source_index": 3, "name": "qty.regular_2f", "role": "quantity",
         "diet_type": "regular", "area_id": "2F"},
    ]}


def test_project_payload_quantities_onto_weekly_menu_shell_maps_explicit_identities():
    template = _authored_single_floor_template()
    fields, field_index = order_service._build_sheet_fields_and_indexes(template)

    def _shell_row(*, row_id: str, menu_date: date, mmdd: str, daypart: str, menu_name: str) -> dict[str, object]:
        values = [""] * len(fields)
        values[field_index["date_mmdd"]] = mmdd
        values[field_index["daypart"]] = daypart
        values[field_index["menu"]] = menu_name
        return {
            "row_id": row_id,
            "values": values,
            "identity": order_service._sheet_row_identity(menu_date, daypart, menu_name),
        }

    def _payload_row(*, date_text: str, daypart: str, menu_name: str, regular: str) -> list[str]:
        values = [""] * len(fields)
        values[field_index["date_mmdd"]] = date_text
        values[field_index["daypart"]] = daypart
        values[field_index["menu"]] = menu_name
        values[field_index["qty.regular_2f"]] = regular
        return values

    rows = [
        _shell_row(row_id="r1", menu_date=date(2026, 4, 29), mmdd="04/29", daypart="朝", menu_name="Menu A"),
        _shell_row(row_id="r2", menu_date=date(2026, 4, 29), mmdd="04/29", daypart="朝", menu_name="Menu B"),
        _shell_row(row_id="r3", menu_date=date(2026, 4, 29), mmdd="04/29", daypart="昼", menu_name="Menu C"),
        _shell_row(row_id="r4", menu_date=date(2026, 4, 29), mmdd="04/29", daypart="昼", menu_name="Menu D"),
        _shell_row(row_id="r5", menu_date=date(2026, 4, 29), mmdd="04/29", daypart="昼", menu_name="Menu E"),
    ]
    payload_rows = [
        _payload_row(date_text="04/29", daypart="朝", menu_name="Menu A", regular="70"),
        _payload_row(date_text="04/29", daypart="朝", menu_name="Menu B", regular="70"),
        _payload_row(date_text="04/29", daypart="昼", menu_name="Menu C", regular="58"),
        _payload_row(date_text="04/29", daypart="昼", menu_name="Menu D", regular="59"),
        _payload_row(date_text="04/29", daypart="昼", menu_name="Menu E", regular="60"),
    ]

    projected, stats = order_service._project_payload_quantities_onto_weekly_menu_shell(
        rows=rows,
        fields=fields,
        payload_rows=payload_rows,
        template=template,
    )

    qty_idx = field_index["qty.regular_2f"]
    assert [row["values"][qty_idx] for row in projected] == ["70", "70", "58", "59", "60"]
    assert stats["mapped_date_group_count"] == 1
    assert stats["exact_row_count"] == 5
    assert stats["ordered_row_count"] == 0
    assert stats["unresolved_date_group_count"] == 0
    assert stats["overflow_row_count"] == 0


def test_project_payload_quantities_onto_weekly_menu_shell_rejects_unresolved_date_group_without_mutation():
    template = _authored_single_floor_template()
    fields, field_index = order_service._build_sheet_fields_and_indexes(template)

    def _shell_row(*, row_id: str, menu_date: date, mmdd: str, daypart: str, menu_name: str) -> dict[str, object]:
        values = [""] * len(fields)
        values[field_index["date_mmdd"]] = mmdd
        values[field_index["daypart"]] = daypart
        values[field_index["menu"]] = menu_name
        return {
            "row_id": row_id,
            "values": values,
            "identity": order_service._sheet_row_identity(menu_date, daypart, menu_name),
        }

    def _payload_row(*, date_text: str, daypart: str, menu_name: str, regular: str) -> list[str]:
        values = [""] * len(fields)
        values[field_index["date_mmdd"]] = date_text
        values[field_index["daypart"]] = daypart
        values[field_index["menu"]] = menu_name
        values[field_index["qty.regular_2f"]] = regular
        return values

    rows = [
        _shell_row(row_id="r1", menu_date=date(2026, 4, 29), mmdd="04/29", daypart="朝", menu_name="Menu A"),
        _shell_row(row_id="r2", menu_date=date(2026, 4, 29), mmdd="04/29", daypart="朝", menu_name="Menu B"),
    ]
    payload_rows = [
        _payload_row(date_text="04/20", daypart="朝", menu_name="Unknown A", regular="91"),
        _payload_row(date_text="", daypart="朝", menu_name="Unknown B", regular="92"),
    ]

    original_rows, original_payload = deepcopy(rows), deepcopy(payload_rows)
    with pytest.raises(order_service._CanonicalOcrProjectionError, match="sheet_canonical_mismatch"):
        order_service._project_payload_quantities_onto_weekly_menu_shell(
            rows=rows, fields=fields, payload_rows=payload_rows, template=template,
        )
    assert rows == original_rows and payload_rows == original_payload


def test_extract_first_pass_rows_from_payload_rejects_fragmented_position_fallback(repository_layout_source):
    template = (config_service.get_facility_config("FAC00005") or {}).get("fax_template") or {}
    fields, field_index = order_service._build_sheet_fields_and_indexes(template)
    payload = {
        "column_mapping_resolution": {
            "resolved_value": "4:qty.regular_bag_x|5:qty.no_meat_x|6:qty.soft_x|7:qty.no_fish_x|8:qty.change_1_x",
            "decision_source": "position_fallback",
        },
        "tables": [
            {
                "page_index": 1,
                "table_id": "page1_table1",
                "row_count": 8,
                "col_count": 11,
                "rows": [
                    ["月 付", "", "区 分", "献立", "秋葉", "優 #±", "", "", "", "", ""],
                    ["", "", "", "", "", "", "奈食 【 軟菜 】", "", "変更の", "空辺2", "備考欄"],
                    ["", "", "", "", "", "", "肉薬", "魚禁", "", "", ""],
                    ["4/26\n(日)", "", "", "", "0", "0", "", "", "", "", ""],
                    ["", "", "ない", "胡瓜のフレンチサラダ", "0", "0", "", "", "", "", ""],
                    ["", "", "V7", "", "58", "2", "", "", "", "", ""],
                    ["", "", "<</d", "里芋の味噌かけ", "58", "2", "", "", "", "", ""],
                    ["", "", "", "", "58", "2", "", "", "", "", ""],
                ],
                "cells": _structured_cells(
                    [
                        ["月 付", "", "区 分", "献立", "秋葉", "優 #±", "", "", "", "", ""],
                        ["", "", "", "", "", "", "奈食 【 軟菜 】", "", "変更の", "空辺2", "備考欄"],
                        ["", "", "", "", "", "", "肉薬", "魚禁", "", "", ""],
                        ["4/26\n(日)", "", "", "", "0", "0", "", "", "", "", ""],
                        ["", "", "ない", "胡瓜のフレンチサラダ", "0", "0", "", "", "", "", ""],
                        ["", "", "V7", "", "58", "2", "", "", "", "", ""],
                        ["", "", "<</d", "里芋の味噌かけ", "58", "2", "", "", "", "", ""],
                        ["", "", "", "", "58", "2", "", "", "", "", ""],
                    ]
                ),
            }
        ],
    }

    original = deepcopy(payload)
    with pytest.raises(fax_extractor.CanonicalOcrProjectionError,
                       match="^sheet_quantity_column_unmapped$"):
        order_service._extract_first_pass_rows_from_payload(payload, template)
    assert payload == original


def test_extract_first_pass_rows_from_payload_does_not_promote_fragmented_position_identity(repository_layout_source):
    template = (config_service.get_facility_config("FAC00005") or {}).get("fax_template") or {}
    fields, field_index = order_service._build_sheet_fields_and_indexes(template)
    payload = {
        "column_mapping_resolution": {
            "resolved_value": "5:qty.soft_x|6:qty.regular_bag_x|7:qty.no_meat_x|8:qty.no_fish_x|9:qty.change_1_x",
            "decision_source": "position_fallback",
        },
        "tables": [
            {
                "page_index": 1,
                "table_id": "page1_table1",
                "row_count": 4,
                "col_count": 12,
                "rows": [
                    ["日付", "", "区 分", "", "献立", "軟菜", "* # は", "熱食 【 軟菜 】", "", "変更1", "変更2", "備考欄"],
                    ["", "", "", "", "", "", "", "茶室", "魚袋", "", "", ""],
                    ["", "3/22\n(日)", "体", "学歴\nEND", "Menu A", "57", "2", "", "", "", "", ""],
                    ["", "", "", "▼¥", "Menu B", "58", "4", "", "", "", "", ""],
                ],
                "cells": _structured_cells(
                    [
                        ["日付", "", "区 分", "", "献立", "軟菜", "* # は", "熱食 【 軟菜 】", "", "変更1", "変更2", "備考欄"],
                        ["", "", "", "", "", "", "", "茶室", "魚袋", "", "", ""],
                        ["", "3/22\n(日)", "体", "学歴\nEND", "Menu A", "57", "2", "", "", "", "", ""],
                        ["", "", "", "▼¥", "Menu B", "58", "4", "", "", "", "", ""],
                    ]
                ),
            }
        ],
    }

    original = deepcopy(payload)
    with pytest.raises(fax_extractor.CanonicalOcrProjectionError,
                       match="^sheet_quantity_column_unmapped$"):
        order_service._extract_first_pass_rows_from_payload(payload, template)
    assert payload == original


def test_extract_sheet_rows_from_payload_does_not_use_position_fallback_pseudo_menu_tokens(repository_layout_source):
    template = (config_service.get_facility_config("FAC00005") or {}).get("fax_template") or {}
    fields, field_index = order_service._build_sheet_fields_and_indexes(template)
    payload = {
        "column_mapping_resolution": {
            "resolved_value": "4:qty.regular_bag_x|5:qty.no_meat_x|6:qty.soft_x|7:qty.no_fish_x|8:qty.change_1_x",
            "decision_source": "position_fallback",
        },
        "tables": [
            {
                "page_index": 1,
                "table_id": "page1_table1",
                "row_count": 6,
                "col_count": 11,
                "rows": [
                    ["月 付", "", "区 分", "献立", "秋葉", "優 #±", "", "", "", "", ""],
                    ["", "", "", "", "", "", "奈食 【 軟菜 】", "", "変更の", "空辺2", "備考欄"],
                    ["", "", "", "", "", "", "肉薬", "魚禁", "", "", ""],
                    ["4/29\n(水)", "", "", "", "0", "0", "", "", "", "", ""],
                    ["", "", "41 4", "鯖の塩焼き", "58", "2", "", "", "", "", ""],
                    ["", "", "MD", "", "89", "2", "", "", "", "", ""],
                ],
            }
        ],
    }

    original = deepcopy(payload)
    with pytest.raises(fax_extractor.CanonicalOcrProjectionError,
                       match="^sheet_quantity_column_unmapped$"):
        order_service._extract_sheet_rows_from_payload(payload, template)
    assert payload == original
    # Neither pseudo-menu tokens nor adjacent numeric cells acquire authority.
    assert payload["tables"][0]["rows"][4][2:6] == ["41 4", "鯖の塩焼き", "58", "2"]
    assert payload["tables"][0]["rows"][5][2:6] == ["MD", "", "89", "2"]


def test_project_payload_quantities_onto_weekly_menu_shell_rejects_global_anchor_guessing(repository_layout_source):
    template = (config_service.get_facility_config("FAC00005") or {}).get("fax_template") or {}
    fields, field_index = order_service._build_sheet_fields_and_indexes(template)

    def _shell_row(*, row_id: str, menu_date: date, mmdd: str, daypart: str, menu_name: str) -> dict[str, object]:
        values = [""] * len(fields)
        values[field_index["date_mmdd"]] = mmdd
        values[field_index["daypart"]] = daypart
        values[field_index["menu"]] = menu_name
        return {
            "row_id": row_id,
            "values": values,
            "identity": order_service._sheet_row_identity(menu_date, daypart, menu_name),
        }

    def _payload_row(*, date_text: str, menu_name: str, bag: str, meat: str) -> list[str]:
        values = [""] * len(fields)
        values[field_index["date_mmdd"]] = date_text
        values[field_index["menu"]] = menu_name
        values[field_index["qty.regular_bag_x"]] = bag
        values[field_index["qty.no_meat_x"]] = meat
        return values

    rows = [
        _shell_row(row_id="r1", menu_date=date(2026, 4, 26), mmdd="04/26", daypart="朝", menu_name="大豆のトマト煮"),
        _shell_row(row_id="r2", menu_date=date(2026, 4, 26), mmdd="04/26", daypart="朝", menu_name="胡瓜のフレンチサラダ"),
        _shell_row(row_id="r3", menu_date=date(2026, 4, 26), mmdd="04/26", daypart="昼", menu_name="サワラの揚げ浸し"),
        _shell_row(row_id="r4", menu_date=date(2026, 4, 26), mmdd="04/26", daypart="昼", menu_name="里芋の味噌かけ"),
        _shell_row(row_id="r5", menu_date=date(2026, 4, 26), mmdd="04/26", daypart="昼", menu_name="オクラのおかか和え"),
        _shell_row(row_id="r6", menu_date=date(2026, 4, 26), mmdd="04/26", daypart="夕", menu_name="豚肉のピリ辛炒め"),
        _shell_row(row_id="r7", menu_date=date(2026, 4, 26), mmdd="04/26", daypart="夕", menu_name="玉子焼き"),
        _shell_row(row_id="r8", menu_date=date(2026, 4, 26), mmdd="04/26", daypart="夕", menu_name="キャベツと竹輪の酢の物"),
    ]
    payload_rows = [
        _payload_row(date_text="04/26", menu_name="胡瓜のフレンチサラダ", bag="0", meat="0"),
        _payload_row(date_text="", menu_name="", bag="58", meat="2"),
        _payload_row(date_text="", menu_name="里芋の味噌かけ", bag="58", meat="2"),
        _payload_row(date_text="", menu_name="", bag="58", meat="2"),
        _payload_row(date_text="", menu_name="豚肉のピリ辛炒め", bag="0", meat="0"),
        _payload_row(date_text="", menu_name="玉子焼き", bag="0", meat="0"),
        _payload_row(date_text="", menu_name="キャベツと竹輪の酢の物", bag="0", meat="0"),
    ]

    original_rows, original_payload = deepcopy(rows), deepcopy(payload_rows)
    with pytest.raises(order_service._CanonicalOcrProjectionError, match="sheet_canonical_mismatch"):
        order_service._project_payload_quantities_onto_weekly_menu_shell(
            rows=rows, fields=fields, payload_rows=payload_rows, template=template,
        )
    assert rows == original_rows and payload_rows == original_payload


def test_project_payload_quantities_onto_weekly_menu_shell_blocks_blank_rows_without_any_exact_anchor(repository_layout_source):
    template = (config_service.get_facility_config("FAC00005") or {}).get("fax_template") or {}
    fields, field_index = order_service._build_sheet_fields_and_indexes(template)

    rows = [
        {
            "row_id": "r1",
            "values": ["04/26", "朝", "Menu A", "", "", "", "", "", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 4, 26), "朝", "Menu A"),
        },
        {
            "row_id": "r2",
            "values": ["04/26", "朝", "Menu B", "", "", "", "", "", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 4, 26), "朝", "Menu B"),
        },
    ]
    payload_rows = [
        ["04/26", "", "", "", "58", "2", "", "", "", ""],
        ["", "", "", "", "59", "3", "", "", "", ""],
    ]

    original_rows, original_payload = deepcopy(rows), deepcopy(payload_rows)
    with pytest.raises(order_service._CanonicalOcrProjectionError, match="sheet_canonical_mismatch"):
        order_service._project_payload_quantities_onto_weekly_menu_shell(
            rows=rows, fields=fields, payload_rows=payload_rows, template=template,
        )
    assert rows == original_rows and payload_rows == original_payload


def test_normalize_menu_text_collapses_halfwidth_katakana_and_spacing():
    assert order_service._normalize_menu_text("豆腐ﾊﾝﾊﾞｰｸﾞ　添)おろしｿｰｽ") == order_service._normalize_menu_text(
        "豆腐ハンバーグ 添)おろしソース"
    )


def test_helper_diagnostics_do_not_adopt_extra_unmapped_totals():
    template = _authored_aux_component_template()
    fields = order_service._get_row_fields(template)
    header = order_service._sheet_header_from_template(fields, template)
    header[4] = "合計"
    rows = [
        ["04/26", "朝", "", "Menu A", "", "", "", "", "", "", "", "", ""],
        ["04/26", "昼", "", "Menu B", "", "", "", "", "", "", "", "", ""],
        ["04/26", "夕", "", "Menu C", "", "", "", "", "", "", "", "", ""],
    ]
    raw_rows = [
        ["", "", "", "", "70", "", "", "", "", "", "", "", ""],
        ["4/26\n(日)", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "朝", "", "", "70", "", "", "", "", "", "", "", ""],
        ["", "昼", "", "", "80", "", "", "", "", "", "", "", ""],
        ["", "夕", "", "", "90", "", "", "", "", "", "", "", ""],
        ["", "夜", "", "", "100", "", "", "", "", "", "", "", ""],
    ]
    payload = {
        "tables": [
            {
                "page_index": 1,
                "table_id": "page1_table1",
                "row_count": len(raw_rows),
                "col_count": len(raw_rows[0]),
                "rows": raw_rows,
                "cells": _structured_cells(raw_rows),
            }
        ]
    }

    original_rows, original_payload = deepcopy(rows), deepcopy(payload)
    projected, stats = order_service._canonicalize_block_helper_display_rows(
        rows=rows,
        fields=fields,
        header=header,
    )

    assert projected == original_rows
    assert rows == original_rows and payload == original_payload
    assert stats["helper_reanchor_count"] == 0


def test_helper_diagnostics_preserve_blank_totals_instead_of_deriving_them():
    template = _authored_aux_component_template()
    fields = order_service._get_row_fields(template)
    header = order_service._sheet_header_from_template(fields, template)
    header[4] = "合計"
    rows = [
        ["04/26", "夕", "", "豚肉のピリ辛炒め", "", "67", "", "5", "", "", "", "", ""],
        ["", "", "", "春雨サラダ", "", "67", "", "5", "", "", "", "", ""],
        ["", "", "", "味噌汁", "", "67", "", "5", "", "", "", "", ""],
        ["04/30", "夕", "", "メンチカツ", "", "61", "", "5", "", "", "6", "", ""],
        ["", "", "", "蓮根と鶏肉の炒め煮", "", "65", "", "5", "2", "", "", "", ""],
        ["", "", "", "ほうれん草のお浸し", "", "67", "", "5", "", "", "", "", ""],
    ]

    original_rows = deepcopy(rows)
    projected, stats = order_service._canonicalize_block_helper_display_rows(
        rows=rows,
        fields=fields,
        header=header,
    )

    assert projected == original_rows and rows == original_rows
    assert all(row[4] == "" for row in projected)
    assert stats["helper_reanchor_count"] == 0


def test_helper_diagnostics_keep_blank_without_explicit_total():
    template = _authored_aux_component_template()
    fields = order_service._get_row_fields(template)
    header = order_service._sheet_header_from_template(fields, template)
    header[4] = "合計"
    rows = [
        ["04/26", "夕", "", "豚肉のピリ辛炒め", "", "", "", "", "", "", "", "", ""],
        ["", "", "", "春雨サラダ", "", "", "", "", "", "", "", "", ""],
    ]

    original_rows = deepcopy(rows)
    projected, stats = order_service._canonicalize_block_helper_display_rows(
        rows=rows,
        fields=fields,
        header=header,
    )

    assert [row[4] for row in projected] == ["", ""]
    assert projected == original_rows and rows == original_rows
    assert stats["helper_reanchor_count"] == 0


def test_owned_menu_identity_keeps_breakfast_blanks_and_lunch_quantities(selected_projection_case):
    case = selected_projection_case(layout="repository-specimen", facility_id="FAC00002",
        period="2026-03@2026-03-22~2026-03-28",
        rows=[{"date":"2026-03-22","daypart":"朝","menu":"Breakfast 1","cells":{"qty.regular_x":""}},{"date":"2026-03-22","daypart":"朝","menu":"Breakfast 2","cells":{"qty.regular_x":""}},{"date":"2026-03-22","daypart":"昼","menu":"Menu A","cells":{"qty.regular_x":"23"}},{"date":"2026-03-22","daypart":"昼","menu":"Menu B","cells":{"qty.regular_x":"27"}},{"date":"2026-03-22","daypart":"昼","menu":"Menu C","cells":{"qty.regular_x":"23"}}], unlisted_cells="")
    _assert_unowned_projection_is_not_adopted(case, {
        "tables": [{"rows": [
            ["3/22", "ロ", "VF", "Menu A", "23"],
            ["", "", "48", "Menu B", "27"],
            ["", "", "PX", "Menu C", "23"]]}]})


def test_unselected_diagnostic_draft_does_not_override_owned_cells_or_add_stale_warnings(selected_projection_case):
    from sqlalchemy import MetaData, Table, select
    from src.db import get_engine, session_scope
    from src.models.order_workflow_state import OrderWorkflowState
    from src.services.canonical_facility_store import read_snapshot
    from tests.unit.test_disabled_facility_columns import _snapshot
    case = selected_projection_case(layout="repository-specimen", facility_id="FAC00002",
        period="2026-03@2026-03-22~2026-03-28", unlisted_cells="", rows=[
            {"date": "2026-03-22", "daypart": "朝", "menu": "Menu A", "cells": {"qty.regular_x": "23"}},
            {"date": "2026-03-22", "daypart": "昼", "menu": "Menu B", "cells": {"qty.regular_x": "27"}}])
    diagnostic = deepcopy(case.sheet)
    diagnostic["source"] = "draft_sheet"
    diagnostic["rows"][0][diagnostic["fields"].index("qty.regular_x")] = "999"
    stale = ["template_unresolved", "sheet_quantity_column_unmapped",
             "ocr_evidence_recovery_required", "sheet_ocr_review_required"]
    captured = order_service._capture_synchronous_cache_write_input(case.id)
    assert captured is not None and captured["evidence_run_id"] == case.evidence["id"]
    before = _snapshot(get_engine())
    latest_before = order_service.get_latest_sheet_draft(case.id)
    original_diagnostic = deepcopy(diagnostic)
    original_capture = deepcopy(captured)
    original_pdf = case.pdf.read_bytes()
    source_revision = read_snapshot().revision
    with session_scope() as session:
        selected_draft_id = session.get(OrderWorkflowState, case.id).draft_id
    started = datetime.utcnow()
    saved = order_service.persist_sheet_draft(order_id=case.id,
        draft_sheet_json=diagnostic, draft_state="draft_ready",
        blockers=["template_unresolved"], warnings=stale,
        synchronous_write_input=captured)
    assert saved is not None
    assert saved["order_id"] == case.id
    assert saved["base_evidence_run_id"] == captured["evidence_run_id"] == case.evidence["id"]
    assert saved["template_version_id"] == case.evidence["template_version_id"]
    assert "template_resolution" not in case.evidence["payload_json"]
    assert saved["base_template_resolution_id"] is None
    assert saved["base_menu_snapshot_id"] is None
    assert saved["draft_sheet_json"] == original_diagnostic
    assert saved["draft_sheet_json"]["rows"][0][diagnostic["fields"].index("qty.regular_x")] == "999"
    assert saved["draft_state"] == "draft_ready"
    assert saved["blockers_json"] == ["template_unresolved"]
    assert saved["warnings_json"] == stale
    assert saved["latest_patch_candidate_id"] is None
    assert saved["edited_by"] is None
    created = datetime.fromisoformat(saved["created_at"])
    assert started <= created <= datetime.utcnow()
    assert saved["edited_at"] == saved["created_at"]
    with session_scope() as session:
        assert session.get(OrderWorkflowState, case.id).draft_id == selected_draft_id
    sheet, error = order_service.get_ocr_sheet(case.id)
    assert error is None and sheet is not None
    assert sheet["rows"] == case.sheet["rows"]
    quantity_index = sheet["fields"].index("qty.regular_x")
    assert [row[quantity_index] for row in sheet["rows"]] == ["23", "27"]
    assert sheet["base_evidence_run_id"] == case.evidence["id"]
    assert sheet.get("warnings") == case.sheet.get("warnings")
    assert sheet.get("apply_blockers") == case.sheet.get("apply_blockers")
    assert sheet.get("can_apply") == case.sheet.get("can_apply")
    assert order_service.get_latest_sheet_draft(case.id) == latest_before
    assert diagnostic == original_diagnostic
    assert captured == original_capture
    assert case.pdf.read_bytes() == original_pdf
    assert read_snapshot().revision == source_revision
    engine = get_engine()
    table = Table("order_sheet_drafts", MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        actual = connection.execute(select(table).where(table.c.id == saved["id"])).one()
    assert dict(actual._mapping) == {
        "id": saved["id"], "order_id": case.id,
        "template_version_id": case.evidence["template_version_id"],
        "base_evidence_run_id": case.evidence["id"],
        "base_template_resolution_id": None,
        "base_menu_snapshot_id": None, "draft_sheet_json": original_diagnostic,
        "draft_state": "draft_ready", "blockers_json": ["template_unresolved"],
        "warnings_json": stale, "latest_patch_candidate_id": None, "edited_by": None,
        "edited_at": created, "created_at": created,
    }
    expected = deepcopy(before)
    assert tuple(actual) not in expected["order_sheet_drafts"]
    expected["order_sheet_drafts"] = sorted(
        [*expected["order_sheet_drafts"], tuple(actual)], key=repr,
    )
    assert _snapshot(engine) == expected


def test_foreign_facility_candidates_cannot_rebind_owned_six_column_source(selected_projection_case):
    case = selected_projection_case(layout="authored-split", facility_id="FAC00001",
        period="2026-03@2026-03-21~2026-03-28", unlisted_cells="", rows=[
            {"date": "2026-03-21", "daypart": "朝", "menu": "Menu A",
             "cells": {"qty.regular_2f": "9", "qty.regular_3f": "8",
                       "qty.soft_2f": "7", "qty.soft_3f": "6",
                       "qty.mixer_2f": "5", "qty.mixer_3f": "4"}}])
    _assert_unowned_projection_is_not_adopted(case, {"facility_candidates": [{"facility_id": "FAC99999", "score": 0.96}, {"facility_id": "FAC88888", "score": 0.52}], "tables": [{"rows": [["03/21", "朝", "Menu A", "9", "8", "7", "6", "5", "4"]]}]})


def test_ambiguous_unowned_table_offsets_cannot_blank_owned_six_columns(selected_projection_case):
    case = selected_projection_case(layout="authored-split", facility_id="FAC00001",
        period="2026-03@2026-03-21~2026-03-28", unlisted_cells="", rows=[
            {"date": "2026-03-21", "daypart": "朝", "menu": "Menu A",
             "cells": {"qty.regular_2f": "9", "qty.regular_3f": "8",
                       "qty.soft_2f": "7", "qty.soft_3f": "6",
                       "qty.mixer_2f": "5", "qty.mixer_3f": "4"}}])
    _assert_unowned_projection_is_not_adopted(case, {"tables": [{"table_id": "unowned-offset-a", "rows": [["03/21", "朝", "Menu A", "9", "8", "7", "6", "5", "4"]]}, {"table_id": "unowned-offset-b", "rows": [["03/21", "朝", "Menu A", "", "9", "8", "7", "6", "5", "4"]]}]})


def test_owned_nine_does_not_require_legacy_registry_rescue(selected_projection_case):
    case = selected_projection_case(layout="authored-split", facility_id="FAC00001",
        period="2026-03@2026-03-21~2026-03-28", unlisted_cells="", rows=[
            {"date": "2026-03-21", "daypart": "朝", "menu": "Menu A",
             "cells": {"qty.regular_2f":"9"}}])
    _assert_unowned_projection_is_not_adopted(case, {"template_id": "fax_layout_regular_soft_mixer_forbidden_v1", "table_rows": [["03/21", "朝", "Menu A", "9"]], "quantity_subgrid_passes": [{"normalized_rows": [["03/21", "朝", "Menu A", "9"]]}], "template_resolution": {"blocked": True, "blocked_reasons": ["template_mismatch"]}})


def test_owned_canonical_fields_do_not_need_partial_header_template_guess(selected_projection_case):
    case = selected_projection_case(layout="repository-specimen", facility_id="FAC00016",
        period="2026-03@2026-03-21~2026-03-28", unlisted_cells="", rows=[
            {"date": "2026-03-21", "daypart": "朝", "menu": "Menu A",
             "cells": {"qty.regular_x":"9","qty.no_meat_x":"4","qty.no_fish_x":"2"}}])
    _assert_unowned_projection_is_not_adopted(case, {"table_rows": [["03/21", "朝", "Menu A", "9", "4", "2"]], "template_resolution": {"resolved_template_id": "", "candidate_template_ids": ["fax_layout_regular_diabetes_v1", "fax_layout_regular_forbidden_v1"], "confidence": 0.41}})


def test_owned_regular_and_diabetes_cells_ignore_ambiguous_header_names(selected_projection_case):
    case = selected_projection_case(layout="repository-specimen", facility_id="FAC00016",
        period="2026-03@2026-03-21~2026-03-28", unlisted_cells="", rows=[
            {"date": "2026-03-21", "daypart": "朝", "menu": "Menu A",
             "cells": {"qty.regular_x":"9","qty.diabetes_x":"4"}}])
    _assert_unowned_projection_is_not_adopted(case, {"table_rows": [["日付", "区分", "メニュー", "数量A", "数量B"], ["03/21", "朝", "Menu A", "9", "4"]], "template_resolution": {"resolved_template_id": "", "candidate_template_ids": ["fax_layout_regular_diabetes_v1", "fax_layout_regular_forbidden_v1"], "confidence": 0.41}})


def test_fac00013_uses_current_json_template_as_single_canonical_config(repository_layout_source):
    facility_config = config_service.get_facility_config("FAC00013")

    assert isinstance(facility_config, dict)
    assert facility_config["fax_template_id"] == "いこいの森"
    assert facility_config["fax_template_ids"] == ["いこいの森"]


def test_get_ocr_sheet_projects_fac00006_repeated_regular_round_columns_from_source_indexes(history_case):
    # Same source indexes and quantities as the original table, including the
    # skipped code column and blank second round. Only recognition is doubled.
    raw = ["04/26", "朝", "CHE", "大豆のトマト煮", "11", "", "13", "2", "3", "4", "5", "6", ""]
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.change_1_x",
              "qty.change_2_x", "qty.regular_bag_x", "qty.soft_x", "qty.mixer_x",
              "qty.no_meat_x", "qty.no_fish_x", "remarks"]
    headers = ["日付", "区分", "メニュー", "常食1回目", "常食2回目", "常食3回目",
               "常食袋分け", "軟菜", "ミキサー", "禁食肉禁", "禁食魚禁", "備考欄"]
    indexes = [0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    columns = []
    values = {}
    for index, (field, header, source_index) in enumerate(zip(fields, headers, indexes, strict=True)):
        role = "quantity" if field.startswith("qty.") else {
            "date_mmdd": "date", "daypart": "daypart", "menu": "menu_name", "remarks": "note"}[field]
        column = {"index": index, "source_index": source_index, "name": field, "header": header, "role": role}
        if role == "quantity":
            column.update(diet_type=field[4:].rsplit("_", 1)[0], area_id="X")
        columns.append(column)
        values[field] = raw[source_index]
    period = "2026-04@2026-04-26~2026-04-30"
    f = history_case(facility="FAC00006", period=period, week=period,
        received=datetime(2026, 4, 25, 9), columns=columns, lines=[],
        menu_entries=[{"id": "fac6-april26", "monthly_menu_id": "2026-04",
            "menu_date": date(2026, 4, 26), "daypart": "朝", "name": raw[3], "slot_index": 0, "diet_type": "regular"}])

    def recognize(region):
        from tests.helpers.authored_cell_values import declared_field
        declared = {values[declared_field(target, fields)] for target in region["logical_targets"]}
        assert len(declared) == 1, "one physical target cannot silently merge different declared values"
        (value,) = declared
        return value

    selected = f.produce(cell_values=recognize)
    assert selected["selected_ocr_result_id"] == f.evidence
    with f.readonly():
        sheet, sheet_error = order_service.get_ocr_sheet(f.id)
        current = order_service.get_current_sheet_context(f.id)
        from src.services.workflow_state_service import project_workflow_state
        workflow = project_workflow_state(f.id)
    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "workflow_v2_selected_ocr_projection"
    assert sheet["header"] == [
        "日付",
        "区分",
        "メニュー",
        "常食1回目",
        "常食2回目",
        "常食3回目",
        "常食袋分け",
        "軟菜",
        "ミキサー",
        "禁食肉禁",
        "禁食魚禁",
        "備考欄",
    ]
    assert sheet["rows"][0] == ["04/26", "朝", "大豆のトマト煮", "11", "", "13", "2", "3", "4", "5", "6", ""]
    assert "template_unresolved" not in (sheet.get("warnings") or [])
    assert "sheet_quantity_column_unmapped" not in (sheet.get("warnings") or [])
    assert "template_unresolved" not in (current.get("warnings") or [])
    assert "sheet_quantity_column_unmapped" not in (current.get("warnings") or [])
    assert "template_unresolved" not in workflow["apply_gate"]["blockers"]
    assert "sheet_quantity_column_unmapped" not in workflow["apply_gate"]["blockers"]

    # Same original partial mapping specimen; it cannot override the owned source indexes.
    from src.db import session_scope
    from src.models.order_ocr_cache import OrderOcrCache

    rows = [
        ["日付", "区分", "コード", "メニュー", "常食1回目", "常食2回目", "常食3回目", "常食袋分け", "軟菜", "ミキサー", "禁食肉禁", "禁食魚禁", "備考欄"],
        raw,
    ]
    partial = {
                "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                "table_raw": "\n".join(
                    [
                        "|日付|区分|コード|メニュー|常食1回目|常食2回目|常食3回目|常食袋分け|軟菜|ミキサー|禁食肉禁|禁食魚禁|備考欄|",
                        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
                        "|04/26|朝|CHE|大豆のトマト煮|11||13|2|3|4|5|6||",
                    ]
                ),
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "page_index": 1,
                        "rows": rows,
                        "cells": _structured_cells(rows),
                        "row_count": len(rows),
                        "col_count": len(rows[0]),
                    }
                ],
                "column_mapping_resolution": {
                    "resolved_value": "4:qty.regular_x|7:qty.regular_bag_x",
                    "resolved_column_mapping_id": "4:qty.regular_x|7:qty.regular_bag_x",
                    "blocked": False,
                    "blocked_reasons": [],
                    "requires_user_choice": False,
                    "decision_source": "position_fallback",
                    "partial_quantity_mapping": True,
                    "confidence": 0.86,
                    "evidence_ref": {
                        "page_index": 1,
                        "table_id": "p1_t1",
                        "source_col_indexes": [4, 7],
                        "mapped_fields": ["qty.regular_x", "qty.regular_bag_x"],
                    },
                    "mapped_quantity_fields": ["qty.regular_x", "qty.regular_bag_x"],
                    "expected_quantity_fields": [
                        "qty.regular_x",
                        "qty.change_1_x",
                        "qty.change_2_x",
                        "qty.regular_bag_x",
                        "qty.soft_x",
                        "qty.mixer_x",
                        "qty.no_meat_x",
                        "qty.no_fish_x",
                    ],
                },
            }
    with session_scope() as session:
        cache = session.get(OrderOcrCache, f.id)
        assert cache is not None
        cache.payload = partial
    with f.readonly():
        resolution = candidate_resolution_service.build_column_mapping_resolution(partial)
        assert resolution["resolved_value"] is None
        assert resolution["partial_quantity_mapping"] is True
        assert "position_fallback_removed" in resolution["blocked_reasons"]
        sheet, error = order_service.get_ocr_sheet(f.id)
        state = project_workflow_state(f.id)
        assert error is None and isinstance(sheet, dict)
        assert sheet["source"] == "workflow_v2_selected_ocr_projection"
        assert sheet["rows"][0] == ["04/26", "朝", "大豆のトマト煮", "11", "", "13", "2", "3", "4", "5", "6", ""]
        assert "sheet_quantity_column_unmapped" not in sheet.get("warnings", [])
        assert "sheet_quantity_column_unmapped" not in state["apply_gate"]["blockers"]
        with session_scope() as session:
            assert session.get(OrderOcrCache, f.id).payload == partial


def test_low_confidence_owned_twenty_one_remains_available_for_operator_editing(selected_projection_case):
    case = selected_projection_case(layout="authored-split", facility_id="FAC00001",
        period="2026-03@2026-03-21~2026-03-28", unlisted_cells="", recognizer_score=0.09, rows=[
            {"date": "2026-03-21", "daypart": "朝", "menu": "Menu A",
             "cells": {"qty.regular_2f":"21"}}])
    _assert_unowned_projection_is_not_adopted(case, {"table_rows": [["03/21", "朝", "Menu A", "21"]], "cell_issues": [{"issue_code": "merged_numeric_cell"}], "template_resolution": {"blocked": False}})


def test_owned_blank_recognition_does_not_borrow_stored_order_quantity(selected_projection_case):
    line = {"date": "2026-03-21", "daypart": "朝", "menu_name": "Menu A",
            "diet_type": "regular", "area_id": "2F", "bag_type": "standard",
            "quantity_original": 2}
    case = selected_projection_case(layout="authored-split", facility_id="FAC00001",
        period="2026-03@2026-03-21~2026-03-28", unlisted_cells="",
        stored_lines=[line], rows=[
            {"date": "2026-03-21", "daypart": "朝", "menu": "Menu A",
             "cells": {"qty.regular_2f": "", "qty.regular_3f": "",
                       "qty.soft_2f": "", "qty.soft_3f": "",
                       "qty.mixer_2f": "", "qty.mixer_3f": ""}}])
    stored = order_service.get_order_by_id(case.id)["lines"]
    assert stored == []
    quantity_indexes = [index for index, field in enumerate(case.sheet["fields"]) if field.startswith("qty.")]
    assert quantity_indexes
    assert all(row[index] == "" for row in case.sheet["rows"] for index in quantity_indexes)
    _assert_unowned_projection_is_not_adopted(case, {
        "tables": [{"rows": [["日付", "区分", "メニュー", "備考欄"],
                            ["03/21", "朝", "Menu A", ""]]}]})
    assert order_service.get_order_by_id(case.id)["lines"] == stored


def test_actual_blank_rerun_cannot_borrow_existing_two_or_replace_saved_two(authored_sheet_case):
    from src.db import session_scope
    from src.models.order import OrderLine
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    from src.models.order_sheet_draft import OrderSheetDraft
    from src.models.order_workflow_state import OrderWorkflowState

    case = authored_sheet_case(quantity=2, include_remarks=False, cell_values="2")
    case.save(case.selected_sheet)
    assert order_service.update_lines(case.id, [{
        "line_id": "operator-two", "date": "2026-03-22", "daypart": "朝",
        "menu_name": "Menu A", "diet_type": "regular", "area_id": "2F",
        "bag_type": "standard", "quantity_original": 2, "quantity_corrected": 2,
        "change_note": "explicit operator value",
    }]) is True

    def line_snapshot():
        with session_scope() as session:
            return [{column.key: deepcopy(getattr(line, column.key)) for column in OrderLine.__table__.columns}
                    for line in session.query(OrderLine).filter_by(order_id=case.id).all()]

    original_lines = line_snapshot()
    assert len(original_lines) == 1
    assert original_lines[0]["quantity_original"] == original_lines[0]["quantity_corrected"] == 2
    with session_scope() as session:
        state = session.get(OrderWorkflowState, case.id)
        draft_id, selected_id = state.draft_id, state.evidence_run_id
        original_draft = deepcopy(session.get(OrderSheetDraft, draft_id).draft_sheet_json)
        original_evidence = deepcopy(session.get(OrderOcrEvidenceRun, selected_id).payload_json)
    observed = []

    def recognize_blank(region):
        assert region["role"] == "quantity"
        assert line_snapshot() == original_lines
        observed.append(region["sheet_cell"])
        return ""

    case.prepare_provider(cell_values=recognize_blank)
    with case.execution():
        evidence, error = order_service.rerun_ocr_evidence_only(
            case.id, job_id=case.job, project_sheet=False, refresh_workflow=False)
    assert error is None and evidence is not None, error
    assert len(observed) == 1
    assert evidence["id"] != selected_id
    with session_scope() as session:
        new_payload = session.get(OrderOcrEvidenceRun, evidence["id"]).payload_json
        records = new_payload["hakodate_ocr_evidence_records"]
        assert len(records) == 1 and records[0]["raw_text"] == ""
        state = session.get(OrderWorkflowState, case.id)
        assert state.evidence_run_id == selected_id and state.draft_id == draft_id
        assert session.get(OrderSheetDraft, draft_id).draft_sheet_json == original_draft
        assert session.get(OrderOcrEvidenceRun, selected_id).payload_json == original_evidence
    assert line_snapshot() == original_lines


def test_build_confirm_materialization_candidate_prefers_latest_draft_rows(owned_outputs):
    order_service.clear_all()
    order = _owned_order(owned_outputs)

    saved, error = owned_outputs.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食2F", "備考"],
        rows=[["03/21", "朝", "Menu A", "7", "draft"]],
        fields=["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )
    assert saved is not None

    second_saved, second_error = owned_outputs.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食2F", "備考"],
        rows=[["03/21", "朝", "Menu A", "8", "latest"]],
        fields=["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        row_ids=["draft-row-2"],
        ui_mode="sheet",
    )

    assert second_error is None
    assert second_saved is not None
    assert error is None
    latest_draft = order_service.get_latest_sheet_draft(order["id"])
    assert isinstance(latest_draft, dict)

    candidate = order_service.build_confirm_materialization_candidate(order["id"])

    assert isinstance(candidate, dict)
    assert candidate["source"] == "draft_sheet"
    assert candidate["draft_id"] == latest_draft["id"]
    assert candidate["line_count"] == 1
    assert candidate["error"] is None
    assert candidate["lines"][0]["menu_name"] == "Menu A"
    assert candidate["lines"][0]["quantity_original"] == 8


def test_confirmed_snapshot_includes_materialization_candidate_from_latest_draft(owned_outputs):
    order_service.clear_all()
    order = _owned_order(owned_outputs)

    saved, error = owned_outputs.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食2F", "備考"],
        rows=[["03/21", "朝", "Menu A", "9", "draft"]],
        fields=["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )

    assert error is None
    assert saved is not None
    latest_draft = order_service.get_latest_sheet_draft(order["id"])
    assert isinstance(latest_draft, dict)

    confirmed = owned_outputs.confirm_order(order["id"])
    assert confirmed is not None

    snapshot = order_service.get_latest_confirmed_snapshot(order["id"])
    assert isinstance(snapshot, dict)
    snapshot_json = snapshot["snapshot_json"]
    materialization_candidate = snapshot_json.get("materialization_candidate")
    assert isinstance(materialization_candidate, dict)
    assert materialization_candidate["source"] == "draft_sheet"
    assert materialization_candidate["draft_id"] == latest_draft["id"]
    assert materialization_candidate["lines"][0]["quantity_original"] == 9
    assert snapshot["draft_id"] == latest_draft["id"]
    refreshed = order_service.get_order_by_id(order["id"]) or confirmed
    assert isinstance(refreshed, dict)
    assert refreshed["lines"][0]["quantity_original"] == 9
    from src.services import output_builder

    output_ctx = output_builder._prepare_output_context(order["id"])
    assert output_ctx["order_lines"][0]["quantity_original"] == 9


def test_owned_bagging_rejects_unparseable_identity_without_restoring_invalidated_lines(authored_sheet_case):
    from src.db import session_scope
    from src.models.order import Order, OrderLine
    from src.services import order_workflow_v2_service as workflow
    from tests.contract.test_saved_sheet_quantity_schema import unchanged_after_refusal

    case = authored_sheet_case(quantity=2,
        cell_values=lambda region: "2" if region["role"] == "quantity" else "",
        menu_date=date(2026, 3, 21), period="2026-03@2026-03-21~2026-03-28")
    broken = {"header": ["日付", "区分", "メニュー", "常食2F", "備考"],
        "rows": [["", "", "", "", "broken"]],
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "row_ids": ["draft-row-broken-1"]}
    saved = case.save(broken)
    assert saved is not None
    revision = saved["workflow"]["revision"]
    with unchanged_after_refusal(case, revision):
        result, error = workflow.run_bagging(case.id, expected_revision=revision)
        assert result is None
        assert error == "sheet_identity_review_required"
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id)
        assert current["rows"] == [["", "", "", "", "broken"]]
        assert current["base_evidence_run_id"] == case.evidence
        with session_scope() as session:
            assert session.get(Order, case.id).status == "要確認"
            lines = session.query(OrderLine).filter_by(order_id=case.id).all()
            assert lines == []


def test_owned_bagging_and_confirm_require_formally_saved_source(authored_sheet_case):
    from src.db import session_scope
    from src.models.order import Order, OrderLine
    from src.services import order_workflow_v2_service as workflow
    from tests.contract.test_saved_sheet_quantity_schema import unchanged_after_refusal

    case = authored_sheet_case(quantity=2, include_remarks=False, produce=False,
        menu_date=date(2026, 3, 21), period="2026-03@2026-03-21~2026-03-28")
    case.confirm()
    revision = case.revision()
    with unchanged_after_refusal(case, revision):
        bagging, bagging_error = workflow.run_bagging(case.id, expected_revision=revision)
        assert bagging is None and bagging_error == "saved_sheet_required"
        confirmed, confirm_error = workflow.final_confirm(case.id, expected_revision=revision)
        assert confirmed is None and confirm_error == "output_review_required"
    with case.readonly():
        assert order_service.get_latest_ocr_evidence_run(case.id, backfill_from_cache=False) is None
        assert order_service.get_latest_sheet_draft(case.id) is None
        with session_scope() as session:
            assert session.get(Order, case.id).status == "要確認"
            lines = session.query(OrderLine).filter_by(order_id=case.id).all()
            assert len(lines) == 1 and lines[0].quantity_original == 2


def test_confirm_materialization_uses_sheet_fields_for_fac00004_multi_quantity_rows(owned_outputs, monkeypatch, tmp_path):
    order_service.clear_all()
    authored_lines = [
        {"date": "2026-03-24", "daypart": "昼", "menu_name": "ホイコーロー", "diet_type": "regular",
         "area_id": "X", "bag_type": "standard", "quantity_original": 65},
        {"date": "2026-03-24", "daypart": "昼", "menu_name": "ホイコーロー", "diet_type": "daycare",
         "area_id": "X", "bag_type": "standard", "quantity_original": 45},
        {"date": "2026-03-24", "daypart": "昼", "menu_name": "ホイコーロー", "diet_type": "no_meat",
         "area_id": "X", "bag_type": "standard", "quantity_original": 2},
    ]
    order, _source_sheet = selected_bagging_order(monkeypatch, tmp_path, authored_lines,
        facility_id="FAC00004", menu_qty_per_serving=1, menu_unit_type="g",
        week_start="2026-03-21", week_end="2026-03-28",
        declared_quantity_pairs=[("regular", "X"), ("daycare", "X"), ("staff", "X"), ("no_meat", "X"), ("no_fish", "X"), ("no_fried", "X"), ("change_1", "X")])

    saved, error = owned_outputs.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "65", "45", "", "2", "", "", "", "豚肉2"]],
        fields=[
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.daycare_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.no_fried_x",
            "qty.change_1_x",
            "remarks",
        ],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )

    assert error is None
    assert saved is not None

    confirmed = owned_outputs.confirm_order(order["id"])
    assert confirmed is not None

    refreshed = order_service.get_order_by_id(order["id"])
    assert isinstance(refreshed, dict)
    hits = [
        line
        for line in (refreshed.get("lines") or [])
        if line.get("date") == "2026-03-24"
        and line.get("daypart") == "昼"
        and line.get("menu_name") == "ホイコーロー"
    ]
    assert {(line.get("diet_type"), line.get("quantity_original")) for line in hits} == {
        ("regular", 65),
        ("daycare", 45),
    }
    sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    assert sheet_error is None
    assert sheet["rows"][0][sheet["fields"].index("qty.no_meat_x")] == "2"


def test_confirm_materialization_uses_sheet_fields_for_fac00014_multi_quantity_rows(owned_outputs, monkeypatch, tmp_path):
    order_service.clear_all()
    authored_lines = [
        {"date": "2026-03-24", "daypart": "昼", "menu_name": "ホイコーロー", "diet_type": "regular",
         "area_id": "X", "bag_type": "standard", "quantity_original": 102},
        {"date": "2026-03-24", "daypart": "昼", "menu_name": "ホイコーロー", "diet_type": "staff",
         "area_id": "X", "bag_type": "standard", "quantity_original": 2},
        {"date": "2026-03-24", "daypart": "昼", "menu_name": "ホイコーロー", "diet_type": "no_meat",
         "area_id": "X", "bag_type": "standard", "quantity_original": 2},
        {"date": "2026-03-24", "daypart": "昼", "menu_name": "ホイコーロー", "diet_type": "no_fish",
         "area_id": "X", "bag_type": "standard", "quantity_original": 1},
    ]
    order, _source_sheet = selected_bagging_order(monkeypatch, tmp_path, authored_lines,
        facility_id="FAC00014", menu_qty_per_serving=1, menu_unit_type="g",
        week_start="2026-03-21", week_end="2026-03-28",
        declared_quantity_pairs=[("regular", "X"), ("staff", "X"), ("no_meat", "X"), ("no_fish", "X"), ("sesame_allergy", "X"), ("change_1", "X")])

    saved, error = owned_outputs.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食", "職員", "肉禁", "魚禁", "ゴマアレルギー", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "102", "2", "2", "1", "", "", ""]],
        fields=[
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "qty.change_1_x",
            "remarks",
        ],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )

    assert error is None
    assert saved is not None

    confirmed = owned_outputs.confirm_order(order["id"])
    assert confirmed is not None

    refreshed = order_service.get_order_by_id(order["id"])
    assert isinstance(refreshed, dict)
    hits = [
        line
        for line in (refreshed.get("lines") or [])
        if line.get("date") == "2026-03-24"
        and line.get("daypart") == "昼"
        and line.get("menu_name") == "ホイコーロー"
    ]
    assert {(line.get("diet_type"), line.get("quantity_original")) for line in hits} == {
        ("regular", 102),
        ("staff", 2),
    }
    sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    assert sheet_error is None
    assert [sheet["rows"][0][sheet["fields"].index(field)]
            for field in ("qty.no_meat_x", "qty.no_fish_x")] == ["2", "1"]


def test_owned_staff_and_forbidden_columns_ignore_missing_legacy_template_hint(selected_projection_case):
    case = selected_projection_case(layout="repository-specimen", facility_id="FAC00014",
        period="2026-04@2026-04-06~2026-04-12", unlisted_cells="", rows=[
            {"date": "2026-04-06", "daypart": "朝", "menu": "Menu A",
             "cells": {"qty.regular_x":"44","qty.staff_x":"2","qty.no_meat_x":"1"}}])
    _assert_unowned_projection_is_not_adopted(case, {"table_rows": [["04/06", "朝", "Menu A", "44", "2", "1"]], "template_resolution": {"resolved_template_id": "", "candidate_template_ids": [], "confidence": 0.2, "blocked": True, "blocked_reasons": ["template_resolution_missing"]}})


def _selected_materialization_order(monkeypatch, tmp_path, *, facility_id):
    specimens = {
        "FAC00004": [("regular", 65), ("daycare", 45), ("staff", 0),
                     ("no_meat", 2), ("no_fish", 0), ("no_fried", 0), ("change_1", 0)],
        "FAC00014": [("regular", 102), ("staff", 2), ("no_meat", 2),
                     ("no_fish", 1), ("sesame_allergy", 0), ("change_1", 0)],
    }
    declared = specimens[facility_id]
    lines = [{"date": "2026-03-24", "daypart": "昼", "menu_name": "ホイコーロー",
              "diet_type": diet, "area_id": "X", "bag_type": "standard",
              "quantity_original": quantity}
             for diet, quantity in declared if quantity]
    order, source = selected_bagging_order(monkeypatch, tmp_path, lines,
        facility_id=facility_id, menu_qty_per_serving=1, menu_unit_type="g",
        week_start="2026-03-21", week_end="2026-03-28",
        declared_quantity_pairs=[(diet, "X") for diet, _ in declared])
    assert source["rows"][0][3:] == [quantity if quantity else None for _, quantity in declared]
    return order


def _refused_owned_bagging(order_id, *, expected_error, engine, root):
    from types import SimpleNamespace
    from src.services import order_workflow_v2_service as workflow
    from tests.contract.test_orders_ocr_sheet_history_api import _snapshot
    from tests.contract.test_saved_sheet_quantity_schema import source_files

    current, error = workflow.get_workflow_readonly(order_id)
    assert error is None
    before, files = _snapshot(engine), source_files(SimpleNamespace(root=root))
    result, error = workflow.run_bagging(order_id, expected_revision=current["revision"])
    assert result is None
    assert error == expected_error
    assert _snapshot(engine) == before
    assert source_files(SimpleNamespace(root=root)) == files


def test_owned_confirm_does_not_run_legacy_position_mapping_for_semantic_sheet(monkeypatch, owned_outputs, tmp_path, isolated_formal_schema):
    order = _selected_materialization_order(monkeypatch, tmp_path,
        facility_id="FAC00014")

    saved, error = owned_outputs.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食", "職員", "肉禁", "魚禁", "ゴマアレルギー", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "102", "2", "2", "1", "", "", ""]],
        fields=[
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "qty.change_1_x",
            "remarks",
        ],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )

    assert error is None
    assert saved is not None

    from src.services import order_workflow_v2_service as workflow

    current, error = workflow.get_workflow_readonly(order["id"])
    assert error is None
    bagging, apply_error = workflow.run_bagging(order["id"], expected_revision=current["revision"])
    assert apply_error is None
    assert bagging is not None
    confirmed, error = workflow.final_confirm(order["id"],
        expected_revision=bagging["workflow"]["revision"])
    assert error is None
    assert confirmed is not None
    applied = order_service.get_order_by_id(order["id"])
    assert applied is not None
    line = applied["lines"][0]
    assert line["date"] == "2026-03-24"
    assert line["daypart"] == "昼"
    assert line["menu_name"] == "ホイコーロー"
    assert line["quantity_original"] == 102


def test_owned_bagging_rejects_empty_semantic_materialization_without_fallback(monkeypatch, owned_outputs, tmp_path, isolated_formal_schema):
    order = _selected_materialization_order(monkeypatch, tmp_path,
        facility_id="FAC00004")

    saved, error = owned_outputs.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "65", "45", "", "2", "", "", "", "豚肉2"]],
        fields=[
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.daycare_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.no_fried_x",
            "qty.change_1_x",
            "remarks",
        ],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )

    assert error is None
    assert saved is not None

    monkeypatch.setattr(order_service, "_build_materialization_lines_from_sheet_rows", lambda **_kwargs: [])

    _refused_owned_bagging(order["id"], expected_error="draft_lines_empty",
        engine=isolated_formal_schema, root=tmp_path)


def test_legacy_position_mapping_wrapper_is_not_executable():
    with pytest.raises(order_service.LegacyPositionMappingDisabledError):
        order_service._apply_menu_position_mapping(  # noqa: SLF001
            [
                {
                    "date": "2026-03-24",
                    "daypart": "昼",
                    "menu_name": "ホイコーロー",
                    "source_row_index": 0,
                    "diet_type": "regular",
                    "quantity_original": 102,
                }
            ],
            "2026-03",
        )


def test_owned_bagging_blocks_when_sheet_quantities_diverge_for_fac00004(monkeypatch, owned_outputs, tmp_path, isolated_formal_schema):
    order = _selected_materialization_order(monkeypatch, tmp_path,
        facility_id="FAC00004")

    saved, error = owned_outputs.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "65", "45", "", "2", "", "", "", "豚肉2"]],
        fields=[
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.daycare_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.no_fried_x",
            "qty.change_1_x",
            "remarks",
        ],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )
    assert error is None
    assert saved is not None

    original_builder = order_service._build_materialization_lines_from_sheet_rows

    def _corrupt_builder(**kwargs):
        lines = original_builder(**kwargs)
        for line in lines:
            if line.get("diet_type") == "regular":
                line["quantity_original"] = 2
                break
        return lines

    monkeypatch.setattr(order_service, "_build_materialization_lines_from_sheet_rows", _corrupt_builder)

    _refused_owned_bagging(order["id"], expected_error="draft_materialization_mismatch",
        engine=isolated_formal_schema, root=tmp_path)


def test_owned_bagging_blocks_when_sheet_quantities_diverge_for_fac00014(monkeypatch, owned_outputs, tmp_path, isolated_formal_schema):
    order = _selected_materialization_order(monkeypatch, tmp_path,
        facility_id="FAC00014")

    saved, error = owned_outputs.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食", "職員", "肉禁", "魚禁", "ゴマアレルギー", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "102", "2", "2", "1", "", "", ""]],
        fields=[
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "qty.change_1_x",
            "remarks",
        ],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )
    assert error is None
    assert saved is not None

    original_builder = order_service._build_materialization_lines_from_sheet_rows

    def _corrupt_builder(**kwargs):
        lines = original_builder(**kwargs)
        for line in lines:
            if line.get("diet_type") == "regular":
                line["quantity_original"] = 2
                break
        return lines

    monkeypatch.setattr(order_service, "_build_materialization_lines_from_sheet_rows", _corrupt_builder)

    _refused_owned_bagging(order["id"], expected_error="draft_materialization_mismatch",
        engine=isolated_formal_schema, root=tmp_path)


def _owned_order(inputs):
    payload = IngestEmailPayload(message_id="authored-latest-draft", pdf_uri="component:registration-input",
        received_at=datetime(2026, 3, 21, 9), facility_hint="FAC00001",
        week_hint="2026-03@2026-03-21~2026-03-28")
    return inputs.create_order_from_ingest(payload, physical_slot=0,
        recipe={"qty_per_serving": 1, "unit_type": "g", "category": "主菜"},
        lines=[{"date": "2026-03-21", "daypart": "朝", "menu_name": "Menu A",
                "diet_type": "regular", "area_id": "2F", "bag_type": "standard", "quantity_original": 2}])
