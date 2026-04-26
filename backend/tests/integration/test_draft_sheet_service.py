import pathlib
import sys
from datetime import date, datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.models.order import OrderLine  # noqa: E402
from src.models.order_sheet_draft import OrderSheetDraft  # noqa: E402
from src.services import draft_sheet_service, ocr_evidence_service, order_current_state_service, order_service, workflow_state_service  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _seed_order(message_id: str) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 3, 22, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-03",
    )
    return order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-03-22",
                "daypart": "朝",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 3,
            }
        ],
    )


def _seed_order_for_facility(
    message_id: str,
    *,
    facility_id: str,
    received_at: datetime,
    week_hint: str,
    lines: list[dict] | None,
) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=received_at,
        facility_hint=facility_id,
        week_hint=week_hint,
    )
    return order_service.create_order_from_ingest(payload, lines=lines)


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
    )


def test_build_initial_sheet_draft_prefers_latest_saved_draft() -> None:
    order_service.clear_all()
    order = _seed_order("msg-draft-saved")

    draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "manual_draft",
            "fields": ["col1"],
            "header": ["数量"],
            "rows": [["9"]],
            "row_ids": ["row-1"],
        },
        draft_state="draft_ready",
        edited_by="tester",
    )

    built = draft_sheet_service.build_initial_sheet_draft(order["id"])

    assert isinstance(built, dict)
    assert built["source"] == "manual_draft"
    assert built["rows"] == [["9"]]


def test_build_initial_sheet_draft_from_latest_evidence_run() -> None:
    order_service.clear_all()
    order = _seed_order("msg-draft-evidence")

    evidence = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("5"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )

    built = draft_sheet_service.build_initial_sheet_draft(order["id"])

    assert isinstance(evidence, dict)
    assert isinstance(built, dict)
    assert built["source"] == "review_blocked"
    assert built["rows"] == []
    assert built["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"
    assert "monthly_menu_object_missing" in (built.get("warnings") or [])
    assert "monthly_menu_object_missing" in (built.get("apply_blockers") or [])


def test_build_initial_sheet_draft_falls_back_to_legacy_cache_revision() -> None:
    order_service.clear_all()
    order = _seed_order("msg-draft-legacy")

    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|03/22|朝|Menu A|3|",
            "_edited_ocr": {
                "latest": {
                    "ui_mode": "sheet",
                    "fields": ["date_mmdd", "daypart", "menu", "qty"],
                    "header": ["日付", "区分", "メニュー", "常食2F"],
                    "rows": [["03/22", "朝", "Menu A", "8"]],
                    "row_ids": ["row-a"],
                }
            },
        },
    )

    built = draft_sheet_service.build_initial_sheet_draft(order["id"])

    assert isinstance(built, dict)
    assert built["source"] == "review_blocked"
    assert built["rows"] == []
    assert built["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"
    assert "monthly_menu_object_missing" in (built.get("warnings") or [])
    assert "monthly_menu_object_missing" in (built.get("apply_blockers") or [])


def test_order_service_build_initial_sheet_draft_prefers_semantic_sheet(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-draft-semantic-sheet")

    built = order_service.build_initial_sheet_draft(order["id"])

    assert isinstance(built, dict)
    assert built["source"] == "review_blocked"
    assert built["rows"] == []
    assert built["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"
    assert "monthly_menu_object_missing" in (built.get("warnings") or [])
    assert "monthly_menu_object_missing" in (built.get("apply_blockers") or [])


def test_order_service_build_initial_sheet_draft_keeps_semantic_sheet_when_only_evidence_recovery_warning_remains(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-draft-semantic-shell-over-raw-evidence")

    built = order_service.build_initial_sheet_draft(order["id"])

    assert isinstance(built, dict)
    assert built["source"] == "review_blocked"
    assert built["rows"] == []
    assert built["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"


def test_get_latest_sheet_draft_upgrades_generic_cols_from_semantic_sheet(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-generic-draft-upgrade")

    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "ocr_evidence",
            "fields": ["col1", "col2", "col3", "col4"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
        draft_state="draft_ready",
        edited_by="tester",
    )
    assert isinstance(saved, dict)

    current = order_service.get_current_sheet_context(
        order["id"],
        refresh_draft_from_semantic=True,
        upgrade_generic_from_sheet=True,
        backfill_from_revision=False,
    )

    assert isinstance(current, dict)
    assert current["source"] == "review_blocked"
    assert current["rows"] == []
    assert current["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"


def test_build_initial_sheet_draft_uses_recoverable_semantic_payload(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-recoverable-semantic")

    built = order_service.build_initial_sheet_draft(order["id"])

    assert isinstance(built, dict)
    assert built["rows"] == []
    assert built["source"] == "review_blocked"
    assert built["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"


def test_build_initial_sheet_draft_keeps_semantic_shell_when_weekly_menu_warning_remains(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-weekly-warning-semantic-shell")

    built = order_service.build_initial_sheet_draft(order["id"])

    assert isinstance(built, dict)
    assert built["source"] == "review_blocked"
    assert built["rows"] == []
    assert built["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"


def test_build_initial_sheet_draft_keeps_blocked_empty_semantic_shell(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-blocked-empty-semantic-shell")

    built = order_service.build_initial_sheet_draft(order["id"])

    assert isinstance(built, dict)
    assert built["source"] == "review_blocked"
    assert built["rows"] == []
    assert built["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"
    assert "monthly_menu_object_missing" in (built.get("warnings") or [])
    assert "monthly_menu_object_missing" in (built.get("apply_blockers") or [])


def test_build_initial_sheet_draft_keeps_blocked_shell_when_menu_missing_even_if_structured_rows_exist(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-menu-missing-ocr-seed")

    ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("7"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )

    monkeypatch.setattr(order_service, "_build_position_menu_entries_safe", lambda week_id, facility_id=None: [])
    monkeypatch.setattr(
        order_service,
        "_build_monthly_menu_diagnostics",
        lambda *, week_id, facility_id: {
            "month_id": "2026-03",
            "resolved_week_id": week_id,
            "order_codes": ["monthly_menu_object_missing"],
            "row_codes": [],
            "global_entries_count": 0,
            "facility_entries_count": 0,
        },
    )
    built = order_service.build_initial_sheet_draft(order["id"])

    assert isinstance(built, dict)
    assert built["source"] == "review_blocked"
    assert built["rows"] == []


def test_get_current_sheet_context_keeps_blocked_shell_when_menu_missing_even_if_structured_rows_exist(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-current-sheet-menu-missing-ocr-seed")

    ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("9"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )

    monkeypatch.setattr(order_service, "_build_position_menu_entries_safe", lambda week_id, facility_id=None: [])
    monkeypatch.setattr(
        order_service,
        "_build_monthly_menu_diagnostics",
        lambda *, week_id, facility_id: {
            "month_id": "2026-03",
            "resolved_week_id": week_id,
            "order_codes": ["monthly_menu_object_missing"],
            "row_codes": [],
            "global_entries_count": 0,
            "facility_entries_count": 0,
        },
    )
    current = order_service.get_current_sheet_context(order["id"])

    assert isinstance(current, dict)
    assert current["source"] == "review_blocked"
    assert current["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"
    assert "monthly_menu_object_missing" in (current.get("warnings") or [])
    assert current["rows"] == []


def test_get_current_sheet_context_keeps_blocked_shell_for_wrapped_raw_output_when_menu_missing(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-current-sheet-menu-missing-wrapped-ocr-seed")

    ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload={
            **_sample_payload("9"),
            "raw_output": _sample_payload("9"),
            "capabilities": {
                "step2_view_ready": True,
                "step2_edit_ready": True,
                "semantic_shell_only": True,
            },
        },
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )

    monkeypatch.setattr(order_service, "_build_position_menu_entries_safe", lambda week_id, facility_id=None: [])
    monkeypatch.setattr(
        order_service,
        "_build_monthly_menu_diagnostics",
        lambda *, week_id, facility_id: {
            "month_id": "2026-03",
            "resolved_week_id": week_id,
            "order_codes": ["monthly_menu_object_missing"],
            "row_codes": [],
            "global_entries_count": 0,
            "facility_entries_count": 0,
        },
    )
    current = order_service.get_current_sheet_context(order["id"])

    assert isinstance(current, dict)
    assert current["source"] == "review_blocked"
    assert current["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"
    assert "monthly_menu_object_missing" in (current.get("warnings") or [])
    assert current["rows"] == []


def test_get_current_sheet_context_rebases_stale_saved_draft_to_authoritative_aux_schema(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order_for_facility(
        "msg-current-sheet-fac00004-aux-schema-rebase",
        facility_id="FAC00004",
        received_at=datetime(2026, 4, 26, 9, 0, 0),
        week_hint="2026-04",
        lines=None,
    )

    monkeypatch.setattr(
        order_service,
        "_build_position_menu_entries_safe",
        lambda week_id, facility_id=None: [
            {
                "menu_name": "鶏じゃが",
                "menu_date": date(2026, 4, 26),
                "daypart_key": "breakfast",
                "slot_index": 0,
                "order": 0,
            }
        ],
    )
    monkeypatch.setattr(
        order_service,
        "_build_monthly_menu_diagnostics",
        lambda *, week_id, facility_id: {
            "month_id": "2026-04",
            "resolved_week_id": week_id,
            "order_codes": [],
            "row_codes": [],
            "global_entries_count": 1,
            "facility_entries_count": 1,
        },
    )

    ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload={
            "template_id": "fax_layout_regular_staff_daycare_other_forbidden_v1",
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "rows": [
                        ["日付", "区分", "副区分", "メニュー", "合計", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"],
                        ["04/26", "朝", "主", "鶏じゃが", "67", "66", "", "", "", "", "", "", ""],
                    ],
                }
            ],
            "table_raw": (
                "|日付|区分|副区分|メニュー|合計|常食|通所|職員|肉禁|魚禁|揚げ物禁|変更1|備考欄|\n"
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
                "|04/26|朝|主|鶏じゃが|67|66|||||||"
            ),
        },
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )

    saved_draft = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "facility_id": "FAC00004",
            "resolved_week_id": "2026-04@2026-04-26~2026-04-30",
            "fields": [
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
            "header": ["日付", "区分", "メニュー", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"],
            "rows": [["04/26", "朝", "鶏じゃが", "9", "4", "", "", "", "", "", ""]],
            "row_ids": ["row-1"],
            "warnings": ["sheet_quantity_column_unmapped"],
        },
        draft_state="draft_ready",
        warnings=["sheet_quantity_column_unmapped"],
        edited_by="tester",
    )
    assert isinstance(saved_draft, dict)

    order_current_state_service.persist_current_state(
        order_id=order["id"],
        draft_id=str(saved_draft.get("id") or "").strip() or None,
        evidence_run_id=None,
        state_json={
            "order_id": order["id"],
            "draft_id": str(saved_draft.get("id") or "").strip(),
            "source": "weekly_menu+ocr_payload",
            "facility_id": "FAC00004",
            "resolved_week_id": "2026-04@2026-04-26~2026-04-30",
            "fields": [
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
            "header": ["日付", "区分", "メニュー", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"],
            "rows": [["04/26", "朝", "鶏じゃが", "9", "4", "", "", "", "", "", ""]],
            "row_ids": ["row-1"],
        },
    )

    latest = draft_sheet_service.get_latest_sheet_draft(order["id"])
    assert isinstance(latest, dict)
    rebase_required, rebuilt = order_service._draft_record_requires_current_sheet_semantic_rebase(order["id"], latest)
    assert rebase_required is True
    assert isinstance(rebuilt, dict)
    assert rebuilt["fields"] == [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "aux.col_4",
        "qty.regular_x",
        "qty.daycare_x",
        "qty.staff_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.no_fried_x",
        "qty.change_1_x",
        "remarks",
    ]
    assert rebuilt["header"] == [
        "日付",
        "区分",
        "副区分",
        "メニュー",
        "合計",
        "常食",
        "通所",
        "職員",
        "肉禁",
        "魚禁",
        "揚げ物禁",
        "変更1",
        "備考欄",
    ]
    assert rebuilt["rows"] == [["04/26", "朝", "主", "鶏じゃが", "67", "66", "", "", "", "", "", "", ""]]

    refreshed = order_service._rebase_draft_record_to_facility_schema(
        order["id"],
        latest,
        edited_by="current-sheet-refresh",
    )
    refreshed_payload = refreshed.get("draft_sheet_json") if isinstance(refreshed, dict) else None
    assert isinstance(refreshed_payload, dict)
    assert refreshed_payload["fields"] == rebuilt["fields"]
    assert refreshed_payload["header"] == rebuilt["header"]
    assert refreshed_payload["rows"] == rebuilt["rows"]


def test_get_current_sheet_context_rebases_stale_aux_values_even_when_schema_already_matches(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order_for_facility(
        "msg-current-sheet-fac00004-aux-values-rebase",
        facility_id="FAC00004",
        received_at=datetime(2026, 4, 26, 9, 0, 0),
        week_hint="2026-04@2026-04-26~2026-04-30",
        lines=[
            {
                "date": "2026-04-26",
                "daypart": "朝",
                "menu_name": "鶏じゃが",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 3,
            }
        ],
    )

    monkeypatch.setattr(
        order_service,
        "_build_position_menu_entries_safe",
        lambda week_id, facility_id=None: [
            {
                "menu_name": "鶏じゃが",
                "menu_date": date(2026, 4, 26),
                "daypart_key": "breakfast",
                "slot_index": 0,
                "order": 0,
            }
        ],
    )
    monkeypatch.setattr(
        order_service,
        "_build_monthly_menu_diagnostics",
        lambda *, week_id, facility_id: {
            "month_id": "2026-04",
            "resolved_week_id": week_id,
            "order_codes": [],
            "row_codes": [],
            "global_entries_count": 1,
            "facility_entries_count": 1,
        },
    )

    ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload={
            "template_id": "fax_layout_regular_staff_daycare_other_forbidden_v1",
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "rows": [
                        ["日付", "区分", "副区分", "メニュー", "合計", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"],
                        ["04/26", "朝", "主", "鶏じゃが", "67", "66", "", "", "", "", "", "", ""],
                    ],
                }
            ],
            "table_raw": (
                "|日付|区分|副区分|メニュー|合計|常食|通所|職員|肉禁|魚禁|揚げ物禁|変更1|備考欄|\n"
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
                "|04/26|朝|主|鶏じゃが|67|66|||||||"
            ),
        },
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )

    fields = [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "aux.col_4",
        "qty.regular_x",
        "qty.daycare_x",
        "qty.staff_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.no_fried_x",
        "qty.change_1_x",
        "remarks",
    ]
    header = [
        "日付",
        "区分",
        "副区分",
        "メニュー",
        "合計",
        "常食",
        "通所",
        "職員",
        "肉禁",
        "魚禁",
        "揚げ物禁",
        "変更1",
        "備考欄",
    ]
    stale_rows = [["04/26", "朝", "", "鶏じゃが", "", "9", "4", "", "", "", "", "", ""]]

    saved_draft = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "facility_id": "FAC00004",
            "resolved_week_id": "2026-04@2026-04-26~2026-04-30",
            "fields": fields,
            "header": header,
            "rows": stale_rows,
            "row_ids": ["row-1"],
            "warnings": ["sheet_quantity_column_unmapped"],
        },
        draft_state="draft_ready",
        warnings=["sheet_quantity_column_unmapped"],
        edited_by="tester",
    )
    assert isinstance(saved_draft, dict)

    order_current_state_service.persist_current_state(
        order_id=order["id"],
        draft_id=str(saved_draft.get("id") or "").strip() or None,
        evidence_run_id=None,
        state_json={
            "order_id": order["id"],
            "draft_id": str(saved_draft.get("id") or "").strip(),
            "source": "weekly_menu+ocr_payload",
            "facility_id": "FAC00004",
            "resolved_week_id": "2026-04@2026-04-26~2026-04-30",
            "fields": fields,
            "header": header,
            "rows": stale_rows,
            "row_ids": ["row-1"],
        },
    )

    latest = draft_sheet_service.get_latest_sheet_draft(order["id"])
    assert isinstance(latest, dict)
    rebase_required, rebuilt = order_service._draft_record_requires_current_sheet_semantic_rebase(order["id"], latest)
    assert rebase_required is True
    assert isinstance(rebuilt, dict)
    assert rebuilt["fields"] == fields
    assert rebuilt["header"] == header
    assert rebuilt["rows"] == [["04/26", "朝", "主", "鶏じゃが", "67", "66", "", "", "", "", "", "", ""]]

    refreshed = order_service._rebase_draft_record_to_facility_schema(
        order["id"],
        latest,
        edited_by="current-sheet-refresh",
    )
    refreshed_payload = refreshed.get("draft_sheet_json") if isinstance(refreshed, dict) else None
    assert isinstance(refreshed_payload, dict)
    assert refreshed_payload["fields"] == fields
    assert refreshed_payload["header"] == header
    assert refreshed_payload["rows"][0][:7] == ["04/26", "朝", "主", "鶏じゃが", "67", "66", ""]


def test_get_ocr_sheet_prefers_applied_reparse_revision_quantities_without_erasing_aux_display(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order_for_facility(
        "msg-current-sheet-fac00004-applied-reparse-qty-overlay",
        facility_id="FAC00004",
        received_at=datetime(2026, 4, 26, 9, 0, 0),
        week_hint="2026-04",
        lines=None,
    )

    monkeypatch.setattr(
        order_service,
        "_build_position_menu_entries_safe",
        lambda week_id, facility_id=None: [
            {
                "menu_name": "大豆のトマト煮",
                "menu_date": date(2026, 4, 26),
                "daypart_key": "breakfast",
                "slot_index": 0,
                "order": 0,
            },
            {
                "menu_name": "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ",
                "menu_date": date(2026, 4, 26),
                "daypart_key": "breakfast",
                "slot_index": 1,
                "order": 1,
            },
        ],
    )
    monkeypatch.setattr(
        order_service,
        "_build_monthly_menu_diagnostics",
        lambda *, week_id, facility_id: {
            "month_id": "2026-04",
            "resolved_week_id": week_id,
            "order_codes": [],
            "row_codes": [],
            "global_entries_count": 2,
            "facility_entries_count": 2,
        },
    )

    fields = [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "aux.col_4",
        "qty.regular_x",
        "qty.daycare_x",
        "qty.staff_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.no_fried_x",
        "qty.change_1_x",
        "remarks",
    ]
    header = [
        "日付",
        "区分",
        "副区分",
        "メニュー",
        "合計",
        "常食",
        "通所",
        "職員",
        "肉禁",
        "魚禁",
        "揚げ物禁",
        "変更1",
        "備考欄",
    ]

    ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload={
            "template_id": "fax_layout_regular_staff_daycare_other_forbidden_v1",
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "rows": [
                        header,
                        ["04/26", "朝", "主", "大豆のトマト煮", "70", "", "", "", "", "", "", "", ""],
                        ["04/26", "朝", "副①", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "70", "", "", "", "", "", "", "", ""],
                    ],
                }
            ],
            "table_raw": (
                "|日付|区分|副区分|メニュー|合計|常食|通所|職員|肉禁|魚禁|揚げ物禁|変更1|備考欄|\n"
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
                "|04/26|朝|主|大豆のトマト煮|70||||||||\n"
                "|04/26|朝|副①|胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ|70||||||||"
            ),
        },
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )

    _append_sheet_revision(
        order_id=order["id"],
        fields=fields,
        header=header,
        rows=[
            ["04/26", "朝", "", "大豆のトマト煮", "", "70", "", "", "", "", "", "", ""],
            ["04/26", "朝", "", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "", "70", "", "", "", "", "", "", ""],
        ],
        row_ids=["ocr-row-1", "ocr-row-2"],
        revision_meta={
            "sheet_save_mode": "applied",
            "reparse_applied": True,
            "provider": "gemini",
            "llm_assist": True,
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"], use_saved_draft=False)

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


def test_get_current_sheet_context_uses_applied_reparse_revision_without_erasing_aux_display(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order_for_facility(
        "msg-current-context-fac00004-applied-reparse-qty-overlay",
        facility_id="FAC00004",
        received_at=datetime(2026, 4, 26, 9, 0, 0),
        week_hint="2026-04",
        lines=None,
    )

    monkeypatch.setattr(
        order_service,
        "_build_position_menu_entries_safe",
        lambda week_id, facility_id=None: [
            {
                "menu_name": "大豆のトマト煮",
                "menu_date": date(2026, 4, 26),
                "daypart_key": "breakfast",
                "slot_index": 0,
                "order": 0,
            }
        ],
    )
    monkeypatch.setattr(
        order_service,
        "_build_monthly_menu_diagnostics",
        lambda *, week_id, facility_id: {
            "month_id": "2026-04",
            "resolved_week_id": week_id,
            "order_codes": [],
            "row_codes": [],
            "global_entries_count": 1,
            "facility_entries_count": 1,
        },
    )

    fields = [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "aux.col_4",
        "qty.regular_x",
        "qty.daycare_x",
        "qty.staff_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.no_fried_x",
        "qty.change_1_x",
        "remarks",
    ]
    header = [
        "日付",
        "区分",
        "副区分",
        "メニュー",
        "合計",
        "常食",
        "通所",
        "職員",
        "肉禁",
        "魚禁",
        "揚げ物禁",
        "変更1",
        "備考欄",
    ]

    ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload={
            "template_id": "fax_layout_regular_staff_daycare_other_forbidden_v1",
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "rows": [
                        header,
                        ["04/26", "朝", "主", "大豆のトマト煮", "70", "", "", "", "", "", "", "", ""],
                    ],
                }
            ],
            "table_raw": (
                "|日付|区分|副区分|メニュー|合計|常食|通所|職員|肉禁|魚禁|揚げ物禁|変更1|備考欄|\n"
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
                "|04/26|朝|主|大豆のトマト煮|70||||||||"
            ),
        },
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )

    _append_sheet_revision(
        order_id=order["id"],
        fields=fields,
        header=header,
        rows=[["04/26", "朝", "", "大豆のトマト煮", "", "70", "", "", "", "", "", "", ""]],
        row_ids=["ocr-row-1"],
        revision_meta={
            "sheet_save_mode": "applied",
            "reparse_applied": True,
            "provider": "gemini",
            "llm_assist": True,
        },
    )

    current = order_service.get_current_sheet_context(order["id"])

    assert isinstance(current, dict)
    assert current["rows"] == [["04/26", "朝", "主", "大豆のトマト煮", "70", "70", "", "", "", "", "", "", ""]]


def test_get_ocr_sheet_ignores_non_authoritative_reparse_candidate_revision(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order_for_facility(
        "msg-current-sheet-fac00004-reparse-candidate-not-current",
        facility_id="FAC00004",
        received_at=datetime(2026, 4, 26, 9, 0, 0),
        week_hint="2026-04",
        lines=None,
    )

    monkeypatch.setattr(
        order_service,
        "_build_position_menu_entries_safe",
        lambda week_id, facility_id=None: [
            {
                "menu_name": "大豆のトマト煮",
                "menu_date": date(2026, 4, 26),
                "daypart_key": "breakfast",
                "slot_index": 0,
                "order": 0,
            }
        ],
    )
    monkeypatch.setattr(
        order_service,
        "_build_monthly_menu_diagnostics",
        lambda *, week_id, facility_id: {
            "month_id": "2026-04",
            "resolved_week_id": week_id,
            "order_codes": [],
            "row_codes": [],
            "global_entries_count": 1,
            "facility_entries_count": 1,
        },
    )

    fields = [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "aux.col_4",
        "qty.regular_x",
        "qty.daycare_x",
        "qty.staff_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.no_fried_x",
        "qty.change_1_x",
        "remarks",
    ]
    header = [
        "日付",
        "区分",
        "副区分",
        "メニュー",
        "合計",
        "常食",
        "通所",
        "職員",
        "肉禁",
        "魚禁",
        "揚げ物禁",
        "変更1",
        "備考欄",
    ]

    ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload={
            "template_id": "fax_layout_regular_staff_daycare_other_forbidden_v1",
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "rows": [
                        header,
                        ["04/26", "朝", "主", "大豆のトマト煮", "70", "", "", "", "", "", "", "", ""],
                    ],
                }
            ],
            "table_raw": (
                "|日付|区分|副区分|メニュー|合計|常食|通所|職員|肉禁|魚禁|揚げ物禁|変更1|備考欄|\n"
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
                "|04/26|朝|主|大豆のトマト煮|70||||||||"
            ),
        },
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )

    _append_sheet_revision(
        order_id=order["id"],
        fields=fields,
        header=header,
        rows=[["04/26", "朝", "", "大豆のトマト煮", "", "99", "", "", "", "", "", "", ""]],
        row_ids=["ocr-row-1"],
        revision_meta={
            "sheet_save_mode": "draft_candidate",
            "draft_from_reparse_reject": True,
            "provider": "gemini",
            "llm_assist": True,
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"], use_saved_draft=False)

    assert error is None
    assert isinstance(sheet, dict)
    assert sheet["rows"] == [["04/26", "朝", "主", "大豆のトマト煮", "70", "", "", "", "", "", "", "", ""]]


def test_get_current_sheet_context_does_not_rebase_auto_apply_blocked_draft_on_read(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-current-sheet-read-does-not-rebase-blocked-draft")

    original_rows = [
        ["03/22", "朝", "Menu A", "5", ""],
        ["01/01", "\"", "Ghost Menu", "23", ""],
    ]
    saved_draft = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "draft_sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食", "備考"],
            "rows": original_rows,
            "row_ids": ["draft-2", "draft-1"],
            "warnings": ["stale-current-warning-should-drop"],
        },
        draft_state="auto_apply_blocked",
        blockers=["sheet_canonical_mismatch"],
        warnings=["sheet_ocr_review_required"],
        edited_by="tester",
    )
    assert isinstance(saved_draft, dict)

    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft",
        lambda _order_id, use_saved_draft=False: {
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食", "備考"],
            "rows": [["03/22", "朝", "Menu A", "", ""]],
            "row_ids": ["fresh-1"],
            "warnings": [],
        },
    )

    current = order_service.get_current_sheet_context(order["id"])
    refreshed_draft = draft_sheet_service.get_latest_sheet_draft(order["id"])

    assert isinstance(current, dict)
    assert current["source"] == "draft_sheet"
    assert current["rows"][0] == original_rows[0]
    assert current["rows"][1] == ["01/01", "朝", "Ghost Menu", "23", ""]
    assert refreshed_draft is not None
    assert refreshed_draft["id"] == saved_draft["id"]
    assert refreshed_draft["blockers_json"] == ["sheet_canonical_mismatch"]
    assert refreshed_draft["warnings_json"] == ["sheet_ocr_review_required"]
    assert refreshed_draft["draft_sheet_json"]["rows"] == original_rows


def test_get_current_sheet_context_uses_weekly_menu_with_payload_overlay_when_payload_rows_are_unparseable(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-current-sheet-menu-missing-structured-fallback")

    ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload={
            "column_mapping_resolution": {
                "resolved_value": (
                    "4:qty.regular_2f|5:qty.regular_3f|6:qty.soft_2f|"
                    "7:qty.soft_3f|8:qty.mixer_2f|9:qty.mixer_3f"
                ),
                "decision_source": "position_fallback",
            },
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "rows": [
                        ["日付", "区分", "", "献立", "常食", "", "軟菜", "", "ミキサー", "", "備考欄"],
                        ["", "", "", "", "2F", "3F", "2F", "3F", "2F", "3F", ""],
                        ["03/22", "朝", "主A", "Menu A", "9", "", "", "", "", "", ""],
                        ["", "昼", "主A", "Menu B", "8", "", "", "", "", "", ""],
                    ],
                }
            ],
            "table_raw": (
                "|日付|区分||献立|常食| |軟菜| |ミキサー| |備考欄|\n"
                "|---|---|---|---|---|---|---|---|---|---|---|\n"
                "|03/22|朝|主A|Menu A|9|||||||\n"
                "||昼|主A|Menu B|8||||||"
            ),
        },
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )

    monkeypatch.setattr(
        order_service,
        "_build_position_menu_entries_safe",
        lambda week_id, facility_id=None: [
            {
                "menu_name": "Menu A",
                "menu_date": date(2026, 3, 22),
                "daypart_key": "breakfast",
                "slot_index": 0,
                "order": 0,
            },
            {
                "menu_name": "Menu B",
                "menu_date": date(2026, 3, 22),
                "daypart_key": "lunch",
                "slot_index": 1,
                "order": 1,
            },
        ],
    )
    monkeypatch.setattr(
        order_service,
        "_build_monthly_menu_diagnostics",
        lambda *, week_id, facility_id: {
            "month_id": "2026-03",
            "resolved_week_id": week_id,
            "order_codes": [],
            "row_codes": [],
            "global_entries_count": 2,
            "facility_entries_count": 2,
        },
    )
    monkeypatch.setattr(order_service, "_load_sheet_order_lines", lambda order_id: [])
    monkeypatch.setattr(
        order_service,
        "_extract_sheet_rows_from_payload_uncanonicalized",
        lambda payload, template: [],
    )

    current = order_service.get_current_sheet_context(order["id"])

    assert isinstance(current, dict)
    assert current["source"] == "weekly_menu"
    assert "sheet_payload_mapping_blocked_unresolved_template" in (current.get("warnings") or [])
    assert current["rows"] == [
        ["03/22", "朝", "Menu A", "", "", "", "", "", "", ""],
        ["03/22", "昼", "Menu B", "", "", "", "", "", "", ""],
    ]


def test_get_current_sheet_context_uses_candidate_column_mapping_for_quantity_overlay(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-current-sheet-candidate-column-mapping")

    ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload={
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "rows": [
                        ["日付", "区分", "", "献立", "常食", "", "軟菜", "", "ミキサー", "", "備考欄"],
                        ["", "", "", "", "2F", "3F", "2F", "3F", "2F", "3F", ""],
                        ["03/22", "朝", "主A", "Menu A", "9", "", "", "", "", "", ""],
                        ["", "昼", "主A", "Menu B", "8", "", "", "", "", "", ""],
                    ],
                }
            ],
            "table_raw": (
                "|日付|区分||献立|常食| |軟菜| |ミキサー| |備考欄|\n"
                "|---|---|---|---|---|---|---|---|---|---|---|\n"
                "|03/22|朝|主A|Menu A|9|||||||\n"
                "||昼|主A|Menu B|8||||||"
            ),
        },
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )

    monkeypatch.setattr(
        order_service,
        "_build_position_menu_entries_safe",
        lambda week_id, facility_id=None: [
            {
                "menu_name": "Menu A",
                "menu_date": date(2026, 3, 22),
                "daypart_key": "breakfast",
                "slot_index": 0,
                "order": 0,
            },
            {
                "menu_name": "Menu B",
                "menu_date": date(2026, 3, 22),
                "daypart_key": "lunch",
                "slot_index": 1,
                "order": 1,
            },
        ],
    )
    monkeypatch.setattr(
        order_service,
        "_build_monthly_menu_diagnostics",
        lambda *, week_id, facility_id: {
            "month_id": "2026-03",
            "resolved_week_id": week_id,
            "order_codes": [],
            "row_codes": [],
            "global_entries_count": 2,
            "facility_entries_count": 2,
        },
    )
    monkeypatch.setattr(order_service, "_load_sheet_order_lines", lambda order_id: [])
    monkeypatch.setattr(
        order_service,
        "get_order_candidate_resolution",
        lambda order_id: {
            "resolutions": {
                "template": {
                    "decision_type": "template",
                    "resolved_value": "fax_layout_regular_soft_mixer_forbidden_v1",
                    "confidence": "high",
                    "blocked": False,
                    "blocked_reasons": [],
                    "requires_user_choice": False,
                    "candidates": [],
                },
                "column_mapping": {
                    "decision_type": "column_mapping",
                    "resolved_value": (
                        "4:qty.regular_2f|5:qty.regular_3f|6:qty.soft_2f|"
                        "7:qty.soft_3f|8:qty.mixer_2f|9:qty.mixer_3f"
                    ),
                    "decision_source": "position_fallback",
                    "confidence": "high",
                    "blocked": False,
                    "blocked_reasons": [],
                    "requires_user_choice": False,
                    "candidates": [],
                    "partial_quantity_mapping": False,
                    "mapped_quantity_fields": [
                        "qty.regular_2f",
                        "qty.regular_3f",
                        "qty.soft_2f",
                        "qty.soft_3f",
                        "qty.mixer_2f",
                        "qty.mixer_3f",
                    ],
                    "expected_quantity_fields": [
                        "qty.regular_2f",
                        "qty.regular_3f",
                        "qty.soft_2f",
                        "qty.soft_3f",
                        "qty.mixer_2f",
                        "qty.mixer_3f",
                    ],
                }
            }
        },
    )

    current = order_service.get_current_sheet_context(order["id"])

    assert isinstance(current, dict)
    assert current["source"] == "weekly_menu+ocr_payload"
    assert "sheet_quantity_column_unmapped" in (current.get("warnings") or [])
    assert "ocr_evidence_recovery_required" in (current.get("warnings") or [])
    assert current["rows"] == [
        ["03/22", "朝", "Menu A", "", "", "", "", "", "", ""],
        ["03/22", "昼", "Menu B", "", "", "", "", "", "", ""],
    ]


def test_get_current_sheet_context_does_not_append_ocr_only_menu_rows_to_weekly_menu(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-current-sheet-no-ocr-menu-append")

    ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("4"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )

    monkeypatch.setattr(
        order_service,
        "_build_position_menu_entries_safe",
        lambda week_id, facility_id=None: [
            {
                "menu_name": "Menu A",
                "menu_date": date(2026, 3, 22),
                "daypart_key": "breakfast",
                "slot_index": 0,
                "order": 0,
            }
        ],
    )
    monkeypatch.setattr(
        order_service,
        "_build_monthly_menu_diagnostics",
        lambda *, week_id, facility_id: {
            "month_id": "2026-03",
            "resolved_week_id": week_id,
            "order_codes": [],
            "row_codes": [],
            "global_entries_count": 1,
            "facility_entries_count": 1,
        },
    )
    monkeypatch.setattr(order_service, "_load_sheet_order_lines", lambda order_id: [])
    monkeypatch.setattr(
        order_service,
        "_build_position_menu_entries_from_ocr_payload",
        lambda *, payload, template, received_at: [
            {
                "menu_name": "Menu A",
                "menu_date": date(2026, 3, 22),
                "daypart_key": "breakfast",
                "slot_index": 0,
                "order": 0,
            },
            {
                "menu_name": "OCR Extra",
                "menu_date": date(2026, 3, 23),
                "daypart_key": "lunch",
                "slot_index": 1,
                "order": 1,
            },
        ],
    )

    current = order_service.get_current_sheet_context(order["id"])

    assert isinstance(current, dict)
    assert current["source"] == "weekly_menu+ocr_payload"
    assert current["rows"] == [["03/22", "朝", "Menu A", "4", "", "", "", "", "", ""]]


def test_get_current_sheet_context_does_not_persist_semantic_refresh_on_read(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-current-sheet-context-read-only")

    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "draft_sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["03/22", "朝", "Menu A", "3", "saved"]],
            "row_ids": ["row-1"],
            "warnings": [],
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
        edited_by="tester",
    )
    assert isinstance(saved, dict)

    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft",
        lambda _order_id, **_kwargs: {
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["03/22", "朝", "Menu A", "9", "semantic-refresh"]],
            "row_ids": ["row-1"],
            "warnings": [],
        },
    )

    with session_scope() as session:
        before_count = (
            session.query(OrderSheetDraft)
            .filter(OrderSheetDraft.order_id == order["id"])
            .count()
        )

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


def test_get_current_sheet_context_ignores_non_authoritative_auto_blocked_draft_and_uses_canonical_skeleton() -> None:
    order_service.clear_all()
    order = _seed_order("msg-current-sheet-non-authoritative-auto-draft")

    blocked = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "review_blocked",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [],
            "row_ids": [],
            "warnings": ["menu_entries_missing", "rows_empty"],
        },
        draft_state="draft_ready",
        blockers=["rows_empty"],
        warnings=["menu_entries_missing", "rows_empty"],
        edited_by="rerun-ocr-evidence",
    )
    assert isinstance(blocked, dict)

    current = order_service.get_current_sheet_context(
        order["id"],
        refresh_draft_from_semantic=True,
        upgrade_generic_from_sheet=True,
        backfill_from_revision=False,
    )

    assert isinstance(current, dict)
    assert current["draft_id"] is None
    assert current["source"] == "review_blocked"
    assert current["fields"][:3] == ["date_mmdd", "daypart", "menu"]
    assert current["rows"] == []


def test_get_current_sheet_context_keeps_operator_forced_repair_draft_as_authoritative_current_truth() -> None:
    order_service.clear_all()
    order = _seed_order("msg-current-sheet-forced-repair-authoritative")

    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
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
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=["forced_weekly_menu_overwrite"],
        edited_by="manual-weekly-menu-overwrite",
    )
    assert isinstance(saved, dict)

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


def test_resolve_active_ocr_evidence_run_ignores_non_authoritative_auto_draft_base_evidence() -> None:
    order_service.clear_all()
    order = _seed_order("msg-active-evidence-ignores-auto-draft")

    first = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("3"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )
    second = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("9"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )
    assert isinstance(first, dict)
    assert isinstance(second, dict)

    blocked = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "review_blocked",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [],
            "row_ids": [],
            "warnings": ["menu_entries_missing", "rows_empty"],
        },
        base_evidence_run_id=first["id"],
        draft_state="draft_ready",
        blockers=["rows_empty"],
        warnings=["menu_entries_missing", "rows_empty"],
        edited_by="rerun-ocr-evidence",
    )
    assert isinstance(blocked, dict)

    resolved = order_service._resolve_active_ocr_evidence_run(order["id"])

    assert isinstance(resolved, dict)
    assert resolved["id"] == second["id"]


def test_get_latest_sheet_draft_upgrades_generic_cols_from_recoverable_semantic_sheet(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-generic-draft-recoverable-upgrade")

    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "ocr_evidence",
            "fields": ["col1", "col2", "col3", "col4"],
            "header": ["日付", "区分", "メニュー", "常食"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
        draft_state="draft_ready",
        edited_by="tester",
    )
    assert isinstance(saved, dict)

    current = order_service.get_current_sheet_context(
        order["id"],
        refresh_draft_from_semantic=True,
        upgrade_generic_from_sheet=True,
        backfill_from_revision=False,
    )

    assert isinstance(current, dict)
    assert current["fields"][:3] == ["date_mmdd", "daypart", "menu"]
    assert "qty.regular_2f" in current["fields"]
    assert current["source"] == "review_blocked"
    assert current["rows"] == []
    assert current["draft_record"]["draft_sheet_json"]["fields"][:3] == [
        "date_mmdd",
        "daypart",
        "menu",
    ]
    assert current["draft_record"]["draft_sheet_json"]["rows"] == []


def test_get_current_sheet_context_projects_blocked_empty_semantic_shell_over_generic_saved_draft(
    monkeypatch,
) -> None:
    order_service.clear_all()
    order = _seed_order("msg-generic-draft-blocked-empty-upgrade")

    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "ocr_evidence",
            "fields": ["col1", "col2", "col3", "col4"],
            "header": ["日付", "区分", "メニュー", "常食"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
        draft_state="draft_ready",
        edited_by="tester",
    )
    assert isinstance(saved, dict)

    current = order_service.get_current_sheet_context(
        order["id"],
        refresh_draft_from_semantic=True,
        upgrade_generic_from_sheet=True,
        backfill_from_revision=False,
    )

    assert isinstance(current, dict)
    assert current["fields"][:3] == ["date_mmdd", "daypart", "menu"]
    assert current["rows"] == []
    blocker_tokens = set(current.get("blockers") or []) | set((current.get("menu_diagnostics") or {}).get("order_codes") or [])
    assert "monthly_menu_object_missing" in blocker_tokens or "menu_entries_missing" in blocker_tokens
    assert current["draft_record"]["draft_sheet_json"]["fields"][:3] == [
        "date_mmdd",
        "daypart",
        "menu",
    ]
    assert current["draft_record"]["draft_sheet_json"]["rows"] == []


def test_get_latest_sheet_draft_keeps_generic_cols_without_opt_in(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-generic-draft-always-upgrade")

    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "ocr_evidence",
            "fields": ["col1", "col2", "col3", "col4"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
        draft_state="draft_ready",
        edited_by="tester",
    )
    assert isinstance(saved, dict)

    monkeypatch.setattr(
        order_service,
        "get_ocr_sheet",
        lambda _order_id, **_kwargs: (
            {
                "source": "weekly_menu+ocr_payload",
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
                "header": ["日付", "区分", "メニュー", "常食2F"],
                "rows": [["03/22", "朝", "Menu A", "6"]],
                "row_ids": ["semantic-1"],
                "warnings": [],
            },
            None,
        ),
    )

    current = order_service.get_latest_sheet_draft(
        order["id"],
        backfill_from_revision=False,
    )

    assert isinstance(current, dict)
    draft_json = current["draft_sheet_json"]
    assert draft_json["fields"] == ["col1", "col2", "col3", "col4"]
    assert draft_json["rows"] == [["03/22", "朝", "Menu A", "5"]]


def test_rerun_ocr_evidence_only_persists_new_evidence_without_overwriting_current_draft(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-rerun-evidence-only")
    first = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("3"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )
    assert isinstance(first, dict)
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "3"]],
            "row_ids": ["row-1"],
        },
        edited_by="tester",
    )
    assert isinstance(saved, dict)
    assert saved["base_evidence_run_id"] == first["id"]

    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda _uri: b"%PDF-rerun%")
    monkeypatch.setattr(
        order_service,
        "run_ocr_pipeline",
        lambda **_kwargs: {
            **_sample_payload("9"),
            "status": "done",
            "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "output_reference": "gs://bucket/output.json",
        },
    )

    rerun, error = order_service.rerun_ocr_evidence_only(order["id"])

    assert error is None
    assert isinstance(rerun, dict)
    assert rerun["id"] != first["id"]

    current_draft = order_service.get_latest_sheet_draft(order["id"], backfill_from_revision=True)
    assert isinstance(current_draft, dict)
    draft_json = current_draft["draft_sheet_json"]
    assert "qty.regular_2f" in draft_json["fields"]
    assert all(not str(field or "").startswith("col") for field in draft_json["fields"])
    regular_index = draft_json["fields"].index("qty.regular_2f")
    assert draft_json["rows"][0][:3] == ["03/22", "朝", "Menu A"]
    assert draft_json["rows"][0][regular_index] == "3"
    assert current_draft["base_evidence_run_id"] == rerun["id"]

    latest_evidence = order_service.get_latest_ocr_evidence_run(order["id"], backfill_from_cache=True)
    assert isinstance(latest_evidence, dict)
    assert latest_evidence["id"] == rerun["id"]

    with session_scope() as session:
        lines = session.query(OrderLine).filter(OrderLine.order_id == order["id"]).all()
        assert len(lines) == 1
        assert lines[0].quantity_original == 3


def test_rerun_ocr_evidence_only_maps_failed_partial_output_to_pipeline_failure(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-rerun-evidence-failed-partial")

    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda _uri: b"%PDF-rerun%")
    monkeypatch.setattr(
        order_service,
        "run_ocr_pipeline",
        lambda **_kwargs: {
            "status": "failed",
            "stage": "error",
            "error": "template resolution failed",
            "input_reference": "gs://bucket/input.pdf",
            "output_reference": "gs://bucket/output.json",
        },
    )

    rerun, error = order_service.rerun_ocr_evidence_only(order["id"])

    assert rerun is None
    assert error == "ocr_pipeline_failed"
    job = order_service.get_ocr_job(f"OCR-{order['id']}")
    assert isinstance(job, dict)
    assert job["status"] == "failed"
    assert str(job.get("error_message") or "").startswith("ocr_pipeline_failed:")
    latest_evidence = order_service.get_latest_ocr_evidence_run(order["id"], backfill_from_cache=False)
    assert latest_evidence is None


def test_rerun_ocr_evidence_only_maps_empty_done_output_to_evidence_unusable(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-rerun-evidence-empty-output")

    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda _uri: b"%PDF-rerun%")
    monkeypatch.setattr(
        order_service,
        "run_ocr_pipeline",
        lambda **_kwargs: {
            "status": "done",
            "stage": "done",
            "input_reference": "gs://bucket/input.pdf",
            "output_reference": "gs://bucket/output.json",
        },
    )

    rerun, error = order_service.rerun_ocr_evidence_only(order["id"])

    assert rerun is None
    assert error == "evidence_unusable"
    job = order_service.get_ocr_job(f"OCR-{order['id']}")
    assert isinstance(job, dict)
    assert job["status"] == "failed"
    assert str(job.get("error_message") or "").startswith("evidence_unusable")


def test_rerun_ocr_evidence_only_preserves_partial_pipeline_output_as_awaiting_output(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-rerun-evidence-partial")
    first = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("3"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )
    assert isinstance(first, dict)
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "3"]],
            "row_ids": ["row-1"],
        },
        edited_by="tester",
    )
    assert isinstance(saved, dict)

    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda _uri: b"%PDF-rerun%")
    monkeypatch.setattr(
        order_service,
        "run_ocr_pipeline",
        lambda **_kwargs: {
            "status": "running",
            "stage": "ocr",
            "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "output_reference": "gs://bucket/output.json",
        },
    )

    rerun, error = order_service.rerun_ocr_evidence_only(order["id"])

    assert error is None
    assert rerun == {"status": "running", "output_reference": "gs://bucket/output.json"}
    job = order_service.get_ocr_job(f"OCR-{order['id']}")
    assert isinstance(job, dict)
    assert job["status"] == "awaiting_output"
    current_draft = order_service.get_latest_sheet_draft(order["id"], backfill_from_revision=True)
    assert isinstance(current_draft, dict)
    draft_json = current_draft["draft_sheet_json"]
    assert "qty.regular_2f" in draft_json["fields"]
    assert all(not str(field or "").startswith("col") for field in draft_json["fields"])
    regular_index = draft_json["fields"].index("qty.regular_2f")
    assert draft_json["rows"][0][:3] == ["03/22", "朝", "Menu A"]
    assert draft_json["rows"][0][regular_index] == "3"
    assert current_draft["base_evidence_run_id"] == first["id"]
    latest_evidence = order_service.get_latest_ocr_evidence_run(order["id"], backfill_from_cache=True)
    assert isinstance(latest_evidence, dict)
    assert latest_evidence["id"] == first["id"]


def test_rerun_ocr_evidence_only_marks_output_wait_as_running(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-rerun-evidence-awaiting-output")

    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda _uri: b"%PDF-rerun%")

    def _raise_pending(**_kwargs):
        raise order_service.OCRPipelineOutputPendingError(
            input_reference="gs://bucket/input.pdf",
            output_reference="gs://bucket/output-awaiting.json",
            timeout_seconds=600,
        )

    monkeypatch.setattr(order_service, "run_ocr_pipeline", _raise_pending)

    rerun, error = order_service.rerun_ocr_evidence_only(order["id"])

    assert error is None
    assert rerun == {"status": "running", "output_reference": "gs://bucket/output-awaiting.json"}
    job = order_service.get_ocr_job(f"OCR-{order['id']}")
    assert isinstance(job, dict)
    assert job["status"] == "awaiting_output"
    metrics = job.get("metrics") or {}
    assert metrics.get("result_state") == "awaiting_output"
    workflow = workflow_state_service.refresh_workflow_state(order["id"])
    assert workflow["state"] == "rerun_in_progress"


def test_rerun_ocr_evidence_only_async_trigger_marks_job_awaiting_output(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-rerun-evidence-async-trigger")

    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda _uri: b"%PDF-rerun%")
    captured: dict[str, object] = {}

    def _fake_run_ocr_pipeline(**kwargs):
        captured.update(kwargs)
        return {
            "status": "running",
            "input_reference": "gs://bucket/input-async.pdf",
            "output_reference": "gs://bucket/output-async.json",
            "trigger_error": "OCR pipeline request timeout: timed out",
        }

    monkeypatch.setattr(order_service, "run_ocr_pipeline", _fake_run_ocr_pipeline)

    rerun, error = order_service.rerun_ocr_evidence_only(order["id"])

    assert error is None
    assert rerun == {"status": "running", "output_reference": "gs://bucket/output-async.json"}
    assert captured["wait_for_output"] is False
    job = order_service.get_ocr_job(f"OCR-{order['id']}")
    assert isinstance(job, dict)
    assert job["status"] == "awaiting_output"
    assert job["output_reference"] == "gs://bucket/output-async.json"
    metrics = job.get("metrics") or {}
    assert metrics.get("result_state") == "awaiting_output"
    assert metrics.get("trigger_error") == "OCR pipeline request timeout: timed out"
    workflow = workflow_state_service.refresh_workflow_state(order["id"])
    assert workflow["state"] == "rerun_in_progress"


def test_rerun_ocr_evidence_only_uses_pipeline_wait_timeout_plus_grace(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-rerun-evidence-timeout-override")

    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda _uri: b"%PDF-rerun%")
    monkeypatch.setenv("OCR_PIPELINE_TIMEOUT_SECONDS", "1200")
    monkeypatch.setenv("OCR_RERUN_PIPELINE_STAGE_GRACE_SECONDS", "45")

    captured: dict[str, object] = {}

    def _fake_heartbeat(job_id, **kwargs):
        captured["job_id"] = job_id
        captured["timeout_seconds_override"] = kwargs.get("timeout_seconds_override")
        return {
            **_sample_payload("9"),
            "status": "done",
            "stage": "done",
            "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "output_reference": "gs://bucket/output.json",
        }

    monkeypatch.setattr(order_service, "_run_reparse_with_heartbeat", _fake_heartbeat)

    rerun, error = order_service.rerun_ocr_evidence_only(order["id"])

    assert error is None
    assert isinstance(rerun, dict)
    assert captured["job_id"] == f"OCR-{order['id']}"
    assert captured["timeout_seconds_override"] == 1245.0


def test_switch_draft_to_latest_evidence_explicitly_adopts_new_candidate(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-switch-evidence-adopt")
    first = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("3"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )
    assert isinstance(first, dict)
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "3"]],
            "row_ids": ["row-1"],
        },
        edited_by="tester",
    )
    assert isinstance(saved, dict)
    second = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("8"),
        schema_version="v2_evidence_rerun",
        producer_version="rerun",
        source="ocr-rerun",
    )
    assert isinstance(second, dict)

    monkeypatch.setattr(
        order_service,
        "get_ocr_sheet",
        lambda _order_id, use_saved_draft=True, evidence_run_override=None: (
            {
                "source": "weekly_menu+ocr_payload",
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
                "header": ["日付", "区分", "メニュー", "常食2F"],
                "rows": [["03/22", "朝", "Menu A", "8"]],
                "row_ids": ["row-1"],
                "base_evidence_run_id": second["id"],
            },
            None,
        ),
    )

    switched, error = order_service.switch_draft_to_latest_evidence(order["id"], edited_by="switch-test")

    assert error is None
    assert isinstance(switched, dict)
    assert switched["base_evidence_run_id"] == second["id"]
    assert switched["draft_sheet_json"]["rows"] == [["03/22", "朝", "Menu A", "8"]]


def test_get_candidate_draft_preview_returns_latest_candidate_without_mutating_current_draft(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-candidate-preview-readonly")
    first = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("3"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )
    assert isinstance(first, dict)
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "3"]],
            "row_ids": ["row-1"],
            "base_evidence_run_id": first["id"],
        },
        edited_by="tester",
    )
    assert isinstance(saved, dict)
    second = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("8"),
        schema_version="v2_evidence_rerun",
        producer_version="rerun",
        source="ocr-rerun",
    )
    assert isinstance(second, dict)

    monkeypatch.setattr(
        order_service,
        "get_order_workflow_state",
        lambda *_args, **_kwargs: {"candidate_evidence_run_id": second["id"]},
    )
    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft_with_error",
        lambda *_args, **_kwargs: (
            {
                "source": "weekly_menu+ocr_payload",
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
                "header": ["日付", "区分", "メニュー", "常食2F"],
                "rows": [["03/22", "朝", "Menu A", "8"]],
                "row_ids": ["row-1"],
                "base_evidence_run_id": second["id"],
            },
            None,
        ),
    )

    preview, error = order_service.get_candidate_draft_preview(order["id"])

    assert error is None
    assert isinstance(preview, dict)
    assert preview["base_evidence_run_id"] == second["id"]
    assert preview["draft_sheet_json"]["rows"] == [["03/22", "朝", "Menu A", "8"]]
    latest = draft_sheet_service.get_latest_sheet_draft(order["id"])
    assert isinstance(latest, dict)
    assert latest["base_evidence_run_id"] == first["id"]


def test_candidate_sheet_state_marks_real_candidate_diff_previewable(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-candidate-sheet-state-diff")
    first = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("3"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )
    assert isinstance(first, dict)
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "3"]],
            "row_ids": ["row-1"],
            "base_evidence_run_id": first["id"],
        },
        edited_by="tester",
    )
    assert isinstance(saved, dict)
    second = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("8"),
        schema_version="v2_evidence_rerun",
        producer_version="rerun",
        source="ocr-rerun",
    )
    assert isinstance(second, dict)

    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft_with_error",
        lambda *_args, **_kwargs: (
            {
                "order_id": order["id"],
                "source": "weekly_menu+ocr_payload",
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
                "header": ["日付", "区分", "メニュー", "常食2F"],
                "rows": [["03/22", "朝", "Menu A", "8"]],
                "row_ids": ["row-1"],
                "base_evidence_run_id": second["id"],
            },
            None,
        ),
    )
    state = order_service.candidate_sheet_state(
        order["id"],
        candidate_evidence_run_id=second["id"],
    )

    assert state["candidate_preview_available"] is True
    assert state["candidate_has_meaningful_diff"] is True
    assert state["candidate_preview_error"] is None
    assert state["current_sheet_revision_id"] == saved["id"]
    preview = state["candidate_preview_draft"]
    assert isinstance(preview, dict)
    assert preview["base_evidence_run_id"] == second["id"]
    assert preview["draft_sheet_json"]["rows"] == [["03/22", "朝", "Menu A", "8"]]


def test_get_current_sheet_context_returns_transient_semantic_initial_with_menu_diagnostics(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-current-sheet-context-transient")

    monkeypatch.setattr(
        draft_sheet_service,
        "get_latest_sheet_draft",
        lambda _order_id: None,
    )
    monkeypatch.setattr(
        order_service,
        "build_initial_sheet_draft",
        lambda _order_id: {
            "order_id": _order_id,
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "6"]],
            "row_ids": ["semantic-1"],
            "resolved_week_id": "2026-03@2026-03-22~2026-03-28",
            "menu_diagnostics": {
                "month_id": "2026-03",
                "resolved_week_id": "2026-03@2026-03-22~2026-03-28",
                "order_codes": ["menu_entries_missing"],
                "row_codes": [],
            },
            "warnings": ["sheet_weekly_menu_missing"],
        },
    )
    order_current_state_service.delete_current_state(order["id"])

    context = order_service.get_current_sheet_context(
        order["id"],
        refresh_draft_from_semantic=True,
        upgrade_generic_from_sheet=True,
        backfill_from_revision=False,
    )

    assert isinstance(context, dict)
    assert context["draft_id"] is None
    assert context["source"] == "weekly_menu+ocr_payload"
    assert context["fields"] == ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    assert context["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"
    assert context["menu_diagnostics"]["order_codes"] == ["menu_entries_missing"]
    assert context["has_semantic_fields"] is True
    assert str(context["current_sheet_revision_id"]).startswith("current:")


def test_current_sheet_context_prefers_canonical_order_week_over_stale_draft_week() -> None:
    order_service.clear_all()
    order = _seed_order("msg-current-sheet-context-week-parity")

    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "manual_draft",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
            "resolved_week_id": "2026-03@2026-03-01~2026-03-07",
            "week_id": "2026-03@2026-03-01~2026-03-07",
        },
        draft_state="draft_ready",
        edited_by="tester",
    )
    assert isinstance(saved, dict)

    current = order_service.get_current_sheet_context(
        order["id"],
        refresh_draft_from_semantic=False,
        upgrade_generic_from_sheet=False,
        backfill_from_revision=False,
    )

    assert isinstance(current, dict)
    assert current["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"
    flattened = order_service.flatten_current_sheet_payload(order["id"], current)
    assert flattened["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"
