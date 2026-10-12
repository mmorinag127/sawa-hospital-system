import sys
import pathlib
import base64
from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.main import app  # noqa: E402
from src.models.menu import MonthlyMenu, MonthlyMenuEntry  # noqa: E402
from src.models.ocr_job import OcrJob  # noqa: E402
from src.services import config_service  # noqa: E402
from src.services import facility_service  # noqa: E402
from src.services import order_service  # noqa: E402
from src.services.ocr_job_service import create_job, get_job, update_job  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _basic_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


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
                )
            )


def _seed_monthly_menu_daypart_order_2099_11() -> None:
    with session_scope() as session:
        session.query(MonthlyMenuEntry).filter(MonthlyMenuEntry.monthly_menu_id == "2099-11").delete()
        session.query(MonthlyMenu).filter(MonthlyMenu.id == "2099-11").delete()
        session.add(
            MonthlyMenu(
                id="2099-11",
                month_start=date(2099, 11, 1),
                filename="seed-2099-11.xlsx",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="seed-entry-2099-11-15-breakfast-contract",
                    monthly_menu_id="2099-11",
                    menu_date=date(2099, 11, 15),
                    daypart="朝食",
                    name="朝メニュー",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-11-15-lunch-contract",
                    monthly_menu_id="2099-11",
                    menu_date=date(2099, 11, 15),
                    daypart="昼食",
                    name="昼メニュー",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-11-15-dinner-contract",
                    monthly_menu_id="2099-11",
                    menu_date=date(2099, 11, 15),
                    daypart="夕食",
                    name="夕メニュー",
                    slot_index=0,
                ),
            ]
        )


def _clear_monthly_menu(month_id: str) -> None:
    with session_scope() as session:
        session.query(MonthlyMenuEntry).filter(MonthlyMenuEntry.monthly_menu_id == month_id).delete()
        session.query(MonthlyMenu).filter(MonthlyMenu.id == month_id).delete()


def _create_seed_order(message_id: str) -> dict:
    _seed_monthly_menu_2026_01()
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 1, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    lines = [
        {
            "date": "2026-01-08",
            "daypart": "昼",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 2,
        }
    ]
    return order_service.create_order_from_ingest(payload, lines=lines)


def _create_weekly_menu_seed_order_2099_11(message_id: str) -> dict:
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    lines = [
        {
            "line_id": "line-contract-lunch-1",
            "date": "2099-11-15",
            "daypart": "昼",
            "menu_name": "昼メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 6,
        }
    ]
    return order_service.create_order_from_ingest(payload, lines=lines)


def _create_seed_order_without_facility(message_id: str) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 2, 13, 9, 0, 0),
        facility_hint=None,
        week_hint=None,
    )
    lines = [
        {
            "date": "2026-02-15",
            "daypart": "朝",
            "menu_name": "Menu B",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 4,
        }
    ]
    return order_service.create_order_from_ingest(payload, lines=lines)


def _seed_basic_ocr_cache(order_id: str) -> None:
    order_service._save_order_ocr_cache(
        order_id,
        {
            "status": "done",
            "rows": [["01/08", "昼", "Menu A", "2"]],
            "table_rows": [["01/08", "昼", "Menu A", "2"]],
            "tables": [{"rows": [["01/08", "昼", "Menu A", "2"]]}],
            "pages": [{"page_index": 1, "tables": [{"rows": [["01/08", "昼", "Menu A", "2"]]}]}],
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|01/08|昼|Menu A|2|",
        },
    )


def test_orders_ocr_sheet_and_history_api_flow():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-sheet-001")
    _seed_basic_ocr_cache(order["id"])

    sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
    assert sheet_res.status_code == 200
    sheet = sheet_res.json()
    assert sheet["order_id"] == order["id"]
    assert isinstance(sheet.get("fields"), list)
    assert isinstance(sheet.get("rows"), list)
    assert sheet.get("legacy_available") is True

    history_before = client.get(f"/orders/{order['id']}/ocr-history")
    assert history_before.status_code == 200
    history_before_json = history_before.json()
    assert isinstance(history_before_json.get("latest"), dict)
    assert len(history_before_json.get("revisions") or []) >= 1

    apply_payload = {
        "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
        "rows": [["01/08", "昼", "Menu A", "3", "api"]],
        "ui_mode": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "row_ids": ["row-api-1"],
    }
    apply_res = client.post(f"/orders/{order['id']}/ocr-apply", json=apply_payload)
    assert apply_res.status_code == 200

    history_after = client.get(f"/orders/{order['id']}/ocr-history")
    assert history_after.status_code == 200
    history_after_json = history_after.json()
    latest = history_after_json.get("latest")
    assert isinstance(latest, dict)
    assert latest.get("ui_mode") == "sheet"
    assert latest.get("row_ids") == ["row-api-1"]
    assert len(history_after_json.get("revisions") or []) >= 1


def test_orders_ocr_sheet_save_api_flow():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-sheet-save-001")
    _seed_basic_ocr_cache(order["id"])

    save_payload = {
        "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
        "rows": [["01/08", "昼", "Menu A", "8", "exact-save"]],
        "ui_mode": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "row_ids": ["row-api-save-1"],
    }
    save_res = client.post(f"/orders/{order['id']}/ocr-sheet-save", json=save_payload)
    assert save_res.status_code == 200
    payload = save_res.json()
    revision = payload.get("revision")
    assert isinstance(revision, dict)
    assert revision.get("sheet_save_only") is True
    assert revision.get("sheet_save_mode") == "exact"
    assert revision.get("row_ids") == ["row-api-save-1"]
    assert revision.get("rows") == [["01/08", "昼", "Menu A", "8", "exact-save"]]

    history_res = client.get(f"/orders/{order['id']}/ocr-history")
    assert history_res.status_code == 200
    history_payload = history_res.json()
    latest = history_payload.get("latest")
    assert isinstance(latest, dict)
    assert latest.get("sheet_save_only") is True
    assert latest.get("rows") == [["01/08", "昼", "Menu A", "8", "exact-save"]]


def test_orders_draft_sheet_keeps_exact_saved_values_after_history_reload():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-draft-sheet-persists-exact-save-001")

    save_payload = {
        "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
        "rows": [["01/08", "昼", "Menu A", "11", "keep-me"]],
        "ui_mode": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "row_ids": ["row-api-save-keep-1"],
    }
    save_res = client.post(f"/orders/{order['id']}/ocr-sheet-save", json=save_payload)
    assert save_res.status_code == 200

    draft_res = client.get(f"/orders/{order['id']}/draft-sheet")
    assert draft_res.status_code == 200
    draft_payload = draft_res.json()
    assert draft_payload.get("rows") == [["01/08", "昼", "Menu A", "11", "keep-me"]]
    assert draft_payload.get("row_ids") == ["row-api-save-keep-1"]

    history_res = client.get(f"/orders/{order['id']}/ocr-history")
    assert history_res.status_code == 200

    draft_res_after_history = client.get(f"/orders/{order['id']}/draft-sheet")
    assert draft_res_after_history.status_code == 200
    draft_payload_after_history = draft_res_after_history.json()
    assert draft_payload_after_history.get("rows") == [["01/08", "昼", "Menu A", "11", "keep-me"]]
    assert draft_payload_after_history.get("row_ids") == ["row-api-save-keep-1"]


def test_orders_ocr_history_falls_back_to_latest_evidence_run_when_revision_history_empty():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-history-evidence-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "status": "done",
            "pages": [
                {
                    "page_index": 1,
                    "tables": [
                        {
                            "table_id": "p1_t1",
                            "rows": [["日付", "区分", "メニュー", "常食2F"], ["01/08", "昼", "Menu A", "3"]],
                        }
                    ],
                    "ocr_overlay_uri": "gs://dummy/overlay.png",
                }
            ],
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|01/08|昼|Menu A|3|",
            "template_resolution": {"resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1"},
            "table_box": [0.1, 0.1, 0.9, 0.9],
            "grid_column_edges": [0.1, 0.5, 0.9],
            "grid_row_edges": [0.1, 0.5, 0.9],
        },
    )

    history_res = client.get(f"/orders/{order['id']}/ocr-history")
    assert history_res.status_code == 200
    history_payload = history_res.json()
    latest = history_payload.get("latest")
    assert isinstance(latest, dict)
    assert latest.get("ui_mode") == "evidence"
    assert str(latest.get("revision_id") or "").startswith("OEV")
    assert latest.get("sheet_save_mode") == "evidence_run"
    assert len(history_payload.get("revisions") or []) == 1
    assert isinstance(history_payload.get("raw_output"), dict)


def test_orders_ocr_history_exposes_latest_llm_reparse_attempt_without_revision():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-history-evidence-001-llm-failed")

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="failed")
    update_job(
        job_id,
        status="failed",
        error_message="main_ocr_failed:gemini",
        metrics={
            "request_mode": "llm_reparse",
            "requested_provider": "gemini",
            "llm_assist": True,
            "processing_stage": "inference",
            "result_state": "hard_failed",
        },
    )

    history_res = client.get(f"/orders/{order['id']}/ocr-history")
    assert history_res.status_code == 200
    history_payload = history_res.json()
    attempt = history_payload.get("latest_reparse_attempt") or {}
    assert attempt.get("job_id") == job_id
    assert attempt.get("request_mode") == "llm_reparse"
    assert attempt.get("requested_provider") == "gemini"
    assert attempt.get("processing_stage") == "inference"
    assert attempt.get("result_state") == "hard_failed"


def test_failed_llm_reparse_truth_stays_aligned_across_surfaces_without_revision(tmp_path):
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-history-evidence-001-llm-lines-empty")
    output_path = tmp_path / "ocr_output_done_lines_empty.json"
    output_path.write_text(
        '{"status":"done","table_raw":"|日付|区分|メニュー|常食2F|\\n|---|---|---|---|\\n|01/08|昼|Menu A|2|"}',
        encoding="utf-8",
    )
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "status": "done",
            "rows": [["01/08", "昼", "Menu A", "2"]],
            "table_rows": [["01/08", "昼", "Menu A", "2"]],
            "tables": [{"rows": [["01/08", "昼", "Menu A", "2"]]}],
            "pages": [{"page_index": 1, "tables": [{"rows": [["01/08", "昼", "Menu A", "2"]]}]}],
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|01/08|昼|Menu A|2|",
        },
    )

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="failed")
    update_job(
        job_id,
        status="failed",
        output_reference=f"file://{output_path}",
        error_message="lines_empty",
        metrics={
            "request_mode": "llm_reparse",
            "requested_provider": "gemini",
            "provider": "gemini",
            "llm_assist": True,
            "processing_stage": "line_parse",
            "result_state": "hard_failed",
            "error": "lines_empty",
            "confirmed_lines_retained": True,
        },
    )

    detail_res = client.get(f"/orders/{order['id']}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail.get("ocr_status") == "failed"
    assert detail.get("ocr_error") == "lines_empty"
    assert (detail.get("ocr_metrics") or {}).get("request_mode") == "llm_reparse"
    assert detail.get("ocr_result_state") == "hard_failed"

    draft_res = client.get(f"/orders/{order['id']}/draft-sheet")
    assert draft_res.status_code == 200
    draft = draft_res.json()
    assert draft.get("reparse_status") == "failed"

    sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
    assert sheet_res.status_code == 200
    sheet = sheet_res.json()
    assert sheet.get("reparse_status") == "failed"
    assert sheet.get("reparse_health") == "hard_failed"

    workflow_res = client.get(f"/orders/{order['id']}/workflow-state")
    assert workflow_res.status_code == 200
    workflow = workflow_res.json()
    reparse_state = workflow.get("reparse_state") or {}
    assert reparse_state.get("status") == "hard_failed"
    assert reparse_state.get("request_mode") == "llm_reparse"

    history_res = client.get(f"/orders/{order['id']}/ocr-history")
    assert history_res.status_code == 200
    history = history_res.json()
    assert (history.get("reparse_state") or {}).get("status") == "hard_failed"
    attempt = history.get("latest_reparse_attempt") or {}
    assert attempt.get("job_id") == job_id
    assert attempt.get("request_mode") == "llm_reparse"
    assert attempt.get("processing_stage") == "line_parse"
    assert attempt.get("result_state") == "hard_failed"
    assert attempt.get("error_message") == "lines_empty"


def test_failed_llm_reparse_keeps_exact_terminal_error_across_surfaces_when_job_is_old(tmp_path):
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-history-evidence-001-llm-http400")
    output_path = tmp_path / "ocr_output_done_http400.json"
    output_path.write_text(
        '{"status":"done","table_raw":"|日付|区分|メニュー|常食2F|\\n|---|---|---|---|\\n|01/08|昼|Menu A|2|"}',
        encoding="utf-8",
    )
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "status": "done",
            "rows": [["01/08", "昼", "Menu A", "2"]],
            "table_rows": [["01/08", "昼", "Menu A", "2"]],
            "tables": [{"rows": [["01/08", "昼", "Menu A", "2"]]}],
            "pages": [{"page_index": 1, "tables": [{"rows": [["01/08", "昼", "Menu A", "2"]]}]}],
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|01/08|昼|Menu A|2|",
        },
    )
    exact_error = (
        "main_ocr_failed:gemini:"
        "Gemini OCR HTTP 400 INVALID_ARGUMENT: Budget 0 is invalid. "
        "This model only works in thinking mode."
    )

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="failed")
    update_job(
        job_id,
        status="failed",
        output_reference=f"file://{output_path}",
        error_message=exact_error,
        metrics={
            "request_mode": "llm_reparse",
            "requested_provider": "gemini",
            "provider": "gemini",
            "llm_assist": True,
            "processing_stage": "inference",
            "result_state": "hard_failed",
            "error": exact_error,
            "confirmed_lines_retained": False,
        },
    )
    with session_scope() as session:
        job = session.get(OcrJob, job_id)
        assert job is not None
        job.updated_at = datetime.utcnow() - timedelta(minutes=45)
        session.add(job)

    detail_res = client.get(f"/orders/{order['id']}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail.get("ocr_status") == "failed"
    assert detail.get("ocr_error") == exact_error
    assert detail.get("ocr_processing_stage") == "inference"
    assert detail.get("ocr_result_state") == "hard_failed"

    draft_res = client.get(f"/orders/{order['id']}/draft-sheet")
    assert draft_res.status_code == 200
    draft = draft_res.json()
    assert draft.get("reparse_status") == "failed"
    assert draft.get("reparse_health") == "hard_failed"
    assert draft.get("reparse_error") == exact_error

    sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
    assert sheet_res.status_code == 200
    sheet = sheet_res.json()
    assert sheet.get("reparse_status") == "failed"
    assert sheet.get("reparse_health") == "hard_failed"
    assert sheet.get("reparse_error") == exact_error

    workflow_res = client.get(f"/orders/{order['id']}/workflow-state")
    assert workflow_res.status_code == 200
    workflow = workflow_res.json()
    reparse_state = workflow.get("reparse_state") or {}
    assert reparse_state.get("status") == "hard_failed"
    assert reparse_state.get("request_mode") == "llm_reparse"
    assert workflow.get("ocr_last_reparse_error") == exact_error

    history_res = client.get(f"/orders/{order['id']}/ocr-history")
    assert history_res.status_code == 200
    history = history_res.json()
    assert (history.get("reparse_state") or {}).get("status") == "hard_failed"
    attempt = history.get("latest_reparse_attempt") or {}
    assert attempt.get("job_id") == job_id
    assert attempt.get("error_message") == exact_error

    stale_job = get_job(job_id)
    assert stale_job is not None
    assert stale_job.get("error_message") == exact_error
    assert (stale_job.get("metrics") or {}).get("processing_stage") == "inference"


def test_orders_week_options_and_save_api_flow():
    order_service.clear_all()
    client = TestClient(app)
    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 3, 15, 9, 0, 0)

    original_datetime = order_service.datetime
    order_service.datetime = _FrozenDateTime

    try:
        order = _create_seed_order("msg-api-week-001")
        options_res = client.get(f"/orders/{order['id']}/week-options")
        assert options_res.status_code == 200
        options = options_res.json().get("options") or []
        assert any(str(item.get("week_id") or "").startswith("2026-01@") for item in options)
        selected = next(item for item in options if str(item.get("week_id") or "").startswith("2026-01@"))
        assert str(selected.get("label") or "").startswith("2026-01 (")
        assert selected.get("date_from") == "2026-01-04"
        assert selected.get("date_to") == "2026-01-10"

        save_res = client.post(f"/orders/{order['id']}/week", json={"week": selected.get("week_id")})
        assert save_res.status_code == 200
        assert save_res.json().get("updated") is True

        order_res = client.get(f"/orders/{order['id']}")
        assert order_res.status_code == 200
        assert order_res.json().get("week") == "2026-01"
        assert order_res.json().get("week_value") == selected.get("week_id")
        assert order_res.json().get("week_label") == selected.get("label")
    finally:
        order_service.datetime = original_datetime


def test_orders_week_options_include_future_calendar_ranges_without_menu():
    order_service.clear_all()
    _clear_monthly_menu("2026-03")
    client = TestClient(app)
    payload = IngestEmailPayload(
        message_id="msg-api-week-future-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 3, 11, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-03",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-03-22",
                "daypart": "昼",
                "menu_name": "Future Menu",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 3,
            }
        ],
    )

    res = client.get(f"/orders/{order['id']}/week-options")
    assert res.status_code == 200
    options = res.json().get("options") or []
    target = next((item for item in options if item.get("week_id") == "2026-03@2026-03-22~2026-03-28"), None)
    assert isinstance(target, dict)
    assert target.get("date_from") == "2026-03-22"
    assert target.get("date_to") == "2026-03-28"
    assert target.get("selected") is False
    selected = next((item for item in options if item.get("selected") is True), None)
    assert isinstance(selected, dict)
    assert selected.get("week_id") == "2026-03@2026-03-08~2026-03-14"


def test_order_candidate_resolution_week_candidates_match_week_options_calendar_fallback():
    order_service.clear_all()
    _clear_monthly_menu("2026-04")
    client = TestClient(app)
    payload = IngestEmailPayload(
        message_id="msg-api-week-match-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 4, 6, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-04",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-04-06",
                "daypart": "昼",
                "menu_name": "Week Match Menu",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 3,
            }
        ],
    )

    order_res = client.get(f"/orders/{order['id']}")
    assert order_res.status_code == 200
    week_resolution = (((order_res.json().get("candidate_resolution") or {}).get("resolutions")) or {}).get("week") or {}
    candidate_values = [str(item.get("value") or "") for item in week_resolution.get("candidates") or []]

    options_res = client.get(f"/orders/{order['id']}/week-options")
    assert options_res.status_code == 200
    option_values = [str(item.get("week_id") or "") for item in options_res.json().get("options") or [] if str(item.get("week_id") or "").startswith("2026-04@")]

    assert "2026-04@2026-04-05~2026-04-11" in candidate_values
    assert "2026-04@2026-04-05~2026-04-11" in option_values
    assert "2026-04@2026-04-01~2026-04-07" not in candidate_values


def test_orders_week_options_select_inferred_cross_month_week():
    order_service.clear_all()
    client = TestClient(app)
    payload = IngestEmailPayload(
        message_id="msg-api-week-cross-month-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 4, 30, 9, 0, 0),
        facility_hint="FAC00001",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-04-29",
                "daypart": "朝",
                "menu_name": "A",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 1,
            },
            {
                "date": "2026-04-30",
                "daypart": "朝",
                "menu_name": "B",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 1,
            },
            {
                "date": "2026-05-01",
                "daypart": "朝",
                "menu_name": "C",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 1,
            },
            {
                "date": "2026-05-02",
                "daypart": "朝",
                "menu_name": "D",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 1,
            },
        ],
    )

    res = client.get(f"/orders/{order['id']}/week-options")
    assert res.status_code == 200
    options = res.json().get("options") or []
    selected = next((item for item in options if item.get("selected") is True), None)

    assert isinstance(selected, dict)
    assert selected.get("week_id") == "2026-04@2026-04-26~2026-05-02"
    assert selected.get("date_from") == "2026-04-26"
    assert selected.get("date_to") == "2026-05-02"


def test_orders_week_options_promote_stale_explicit_week_to_cross_month_selection():
    order_service.clear_all()
    client = TestClient(app)
    payload = IngestEmailPayload(
        message_id="msg-api-week-cross-month-stale-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 4, 30, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-04@2026-04-26~2026-04-30",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-04-26",
                "daypart": "朝",
                "menu_name": "A",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 1,
            },
            {
                "date": "2026-04-27",
                "daypart": "朝",
                "menu_name": "A2",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 1,
            },
            {
                "date": "2026-04-28",
                "daypart": "朝",
                "menu_name": "A3",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 1,
            },
            {
                "date": "2026-04-29",
                "daypart": "朝",
                "menu_name": "A4",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 1,
            },
            {
                "date": "2026-04-30",
                "daypart": "朝",
                "menu_name": "A5",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 1,
            },
            {
                "date": "2026-05-01",
                "daypart": "朝",
                "menu_name": "B",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 1,
            },
            {
                "date": "2026-05-02",
                "daypart": "朝",
                "menu_name": "C",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 1,
            },
        ],
    )

    res = client.get(f"/orders/{order['id']}/week-options")
    assert res.status_code == 200
    options = res.json().get("options") or []
    selected = next((item for item in options if item.get("selected") is True), None)

    assert isinstance(selected, dict)
    assert selected.get("week_id") == "2026-04@2026-04-26~2026-04-30"
    assert selected.get("date_from") == "2026-04-26"
    assert selected.get("date_to") == "2026-04-30"


def test_orders_week_options_include_selected_explicit_cross_month_week_when_payload_is_single_month():
    order_service.clear_all()
    client = TestClient(app)
    payload = IngestEmailPayload(
        message_id="msg-api-week-cross-month-selected-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 4, 30, 9, 0, 0),
        facility_hint="FAC00014",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-04-26",
                "daypart": "朝",
                "menu_name": "A",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 1,
            },
            {
                "date": "2026-04-30",
                "daypart": "夕",
                "menu_name": "B",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 1,
            },
        ],
    )
    client.post(
        f"/orders/{order['id']}/week",
        json={"week": "2026-04@2026-04-26~2026-05-02"},
    )

    res = client.get(f"/orders/{order['id']}/week-options")
    assert res.status_code == 200
    options = res.json().get("options") or []
    values = [str(item.get("week_id") or "") for item in options]
    selected = next((item for item in options if item.get("selected") is True), None)

    assert "2026-04@2026-04-26~2026-05-02" in values
    assert isinstance(selected, dict)
    assert selected.get("week_id") == "2026-04@2026-04-26~2026-05-02"
    assert selected.get("date_from") == "2026-04-26"
    assert selected.get("date_to") == "2026-05-02"


def test_orders_week_options_center_on_current_month_plus_minus_two(monkeypatch):
    order_service.clear_all()
    client = TestClient(app)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 3, 15, 9, 0, 0)

    monkeypatch.setattr(order_service, "datetime", _FrozenDateTime)

    order = _create_seed_order("msg-api-week-range-001")

    res = client.get(f"/orders/{order['id']}/week-options")
    assert res.status_code == 200
    options = res.json().get("options") or []
    week_ids = {str(item.get("week_id") or "") for item in options}

    assert any(week_id.startswith("2025-10@") or week_id.startswith("2025-11@") for week_id in week_ids)
    assert any(week_id.startswith("2026-03@") for week_id in week_ids)
    assert not any(week_id.startswith("2025-09@") for week_id in week_ids)
    assert not any(week_id.startswith("2026-04@") for week_id in week_ids)


def test_orders_week_save_api_preserves_operator_exception_range():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-week-exception-001")

    res = client.post(
        f"/orders/{order['id']}/week",
        json={"week": "2026-05@2026-05-01~2026-05-02"},
    )
    assert res.status_code == 200

    order_res = client.get(f"/orders/{order['id']}")
    assert order_res.status_code == 200
    payload = order_res.json()
    assert payload.get("week_value") == "2026-05@2026-05-01~2026-05-02"
    assert payload.get("persisted_week_value") == "2026-05@2026-05-01~2026-05-02"


def test_order_detail_promotes_plain_month_week_to_selected_weekly_range():
    order_service.clear_all()
    client = TestClient(app)
    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 3, 15, 9, 0, 0)

    original_datetime = order_service.datetime
    order_service.datetime = _FrozenDateTime

    try:
        order = _create_seed_order("msg-api-week-display-001")
        save_res = client.post(f"/orders/{order['id']}/week", json={"week": "2026-01"})
        assert save_res.status_code == 200

        order_res = client.get(f"/orders/{order['id']}")
        assert order_res.status_code == 200
        payload = order_res.json()
        assert payload.get("week") == "2026-01"
        assert payload.get("week_value") == "2026-01@2026-01-04~2026-01-10"
        assert payload.get("week_label") == "2026-01 (01/04-01/10)"
    finally:
        order_service.datetime = original_datetime


def test_orders_draft_and_ocr_sheet_rebase_clean_saved_draft_when_facility_schema_changes():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-facility-schema-rebase-001")
    previous_config = config_service.get_facility_config(order["facility"]) or {}

    try:
        seeded = order_service.persist_sheet_draft(
            order_id=order["id"],
            draft_sheet_json={
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
                "header": ["日付", "区分", "メニュー", "旧常食2F", "備考"],
                "rows": [["01/08", "昼", "Menu A", "8", "seeded"]],
                "row_ids": ["row-schema-rebase-1"],
                "source": "draft_ready",
            },
            draft_state="draft_ready",
            blockers=[],
            warnings=[],
            edited_by="test-seed",
        )
        assert seeded is not None

        next_config = dict(previous_config)
        override = dict(next_config.get("fax_template_override") or {})
        override["columns_authoritative"] = True
        override["columns"] = [
            {"index": 0, "role": "date", "header": "日付"},
            {"index": 1, "role": "daypart", "header": "区分"},
            {"index": 2, "role": "menu_name", "header": "メニュー"},
            {
                "index": 3,
                "role": "quantity",
                "header": "施設常食2F",
                "diet_type": "regular",
                "area_id": "2F",
            },
            {"index": 4, "role": "note", "header": "備考"},
        ]
        override["main_ocr_row_fields"] = [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
            "remarks",
        ]
        next_config["fax_template_override"] = override
        assert facility_service.update_config(order["facility"], next_config)

        draft_res = client.get(f"/orders/{order['id']}/draft-sheet")
        assert draft_res.status_code == 200
        draft = draft_res.json()
        assert draft["header"][3] == "施設常食2F"
        assert draft["rows"][0][3] == "8"

        sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
        assert sheet_res.status_code == 200
        sheet = sheet_res.json()
        assert sheet["header"][3] == "施設常食2F"
        assert sheet["rows"][0][3] == "8"
    finally:
        assert facility_service.update_config(order["facility"], previous_config)


def test_build_recoverable_ocr_sheet_payload_can_skip_saved_draft():
    order_service.clear_all()
    order = _create_seed_order("msg-api-facility-template-recoverable-001")
    seeded = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "旧常食2F", "備考"],
            "rows": [["01/08", "昼", "Menu A", "2", ""]],
            "row_ids": ["row-recoverable-1"],
            "source": "draft_ready",
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
        edited_by="test-seed",
    )
    assert seeded is not None

    with_saved, error = order_service.build_recoverable_ocr_sheet_payload(
        order["id"],
        "menu_entries_missing",
        use_saved_draft=True,
    )
    assert error is None
    assert isinstance(with_saved, dict)
    assert with_saved["source"] == "draft_sheet_blocked"
    assert with_saved["header"][3] == "常食2F"

    without_saved, error = order_service.build_recoverable_ocr_sheet_payload(
        order["id"],
        "menu_entries_missing",
        use_saved_draft=False,
    )
    assert error is None
    assert isinstance(without_saved, dict)
    assert without_saved["source"] == "review_blocked"
    assert without_saved["header"][3] != "旧常食2F"


def test_build_recoverable_ocr_sheet_payload_does_not_rehydrate_revision_without_saved_draft(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-api-facility-template-recoverable-no-revision-current-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [["01/08", "昼", "Menu A", "2"]],
            "date_strings": ["01/08"],
        },
    )

    calls = {"count": 0}

    def _unexpected_revision(**_kwargs):
        calls["count"] += 1
        return {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "旧常食2F", "備考"],
            "rows": [["01/01", "朝", "Revision Menu", "9", ""]],
            "row_ids": ["rev-1"],
            "revision_id": "REV-1",
        }

    monkeypatch.setattr(order_service, "_select_order_sheet_revision", _unexpected_revision)

    recovered, error = order_service.build_recoverable_ocr_sheet_payload(
        order["id"],
        "menu_entries_missing",
        use_saved_draft=True,
    )

    assert error is None
    assert isinstance(recovered, dict)
    assert recovered["source"] == "review_blocked"
    assert recovered["recovery_source"] == "none"
    assert recovered["rows"] == []
    assert recovered["row_ids"] == []


def test_orders_activity_history_api_flow():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-history-001")

    update_payload = {
        "lines": [
            {
                "line_id": "line-1",
                "date": "2026-01-08",
                "daypart": "昼",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 5,
            }
        ]
    }
    update_res = client.put(f"/orders/{order['id']}/lines", json=update_payload)
    assert update_res.status_code == 200

    history_res = client.get(f"/orders/{order['id']}/history")
    assert history_res.status_code == 200
    payload = history_res.json()
    assert payload.get("order_id") == order["id"]
    items = payload.get("items") or []
    assert isinstance(items, list)
    assert any(item.get("action") == "order_lines_update" for item in items)
