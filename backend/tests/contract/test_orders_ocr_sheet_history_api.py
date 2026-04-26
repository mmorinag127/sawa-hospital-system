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

    order = _create_seed_order("msg-api-week-001")
    try:
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


def test_orders_week_save_api_rejects_invalid_week():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-week-invalid-001")

    res = client.post(f"/orders/{order['id']}/week", json={"week": "2026-01@2026-02-01~2026-02-07"})
    assert res.status_code == 400
    assert res.json().get("detail") == "week invalid"


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

    order = _create_seed_order("msg-api-week-display-001")
    try:
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


def test_orders_facility_template_columns_save_api_flow():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-facility-template-001")
    previous_config = config_service.get_facility_config(order["facility"]) or {}

    try:
        columns = [
            {"index": 0, "role": "date", "header": "日付", "name": "date"},
            {
                "index": 1,
                "role": "quantity",
                "header": "常食2F",
                "name": "qty.regular_2f",
                "diet_type": "regular",
                "area_id": "2F",
            },
        ]
        save_res = client.put(f"/orders/{order['id']}/facility-template-columns", json={"columns": columns})
        assert save_res.status_code == 200
        payload = save_res.json()
        assert payload.get("updated") is True
        resolved_columns = (((payload.get("resolved_config") or {}).get("fax_template") or {}).get("columns") or [])
        quantity_column = next(item for item in resolved_columns if item.get("index") == 1)
        assert quantity_column.get("header") == "常食2F"
        assert quantity_column.get("diet_type") == "regular"
        assert quantity_column.get("area_id") == "2F"

        facility_res = client.get(f"/facilities/{order['facility']}")
        assert facility_res.status_code == 200
        facility_columns = (
            (((facility_res.json().get("resolved_config") or {}).get("fax_template") or {}).get("columns"))
            or []
        )
        assert any(
            item.get("index") == 1
            and item.get("header") == "常食2F"
            and item.get("diet_type") == "regular"
            and item.get("area_id") == "2F"
            for item in facility_columns
        )
    finally:
        assert facility_service.update_config(order["facility"], previous_config)


def test_orders_facility_template_columns_save_prefers_explicit_quantity_mapping_over_header_inference():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-facility-template-explicit-001")
    previous_config = config_service.get_facility_config(order["facility"]) or {}

    try:
        columns = [
            {"index": 0, "role": "date", "header": "日付", "name": "date"},
            {
                "index": 1,
                "role": "quantity",
                "header": "常食2F",
                "name": "qty.regular_2f",
                "diet_type": "no_fish",
                "area_id": "3F",
                "diet_type_locked": True,
                "area_id_locked": True,
            },
        ]
        save_res = client.put(f"/orders/{order['id']}/facility-template-columns", json={"columns": columns})
        assert save_res.status_code == 200
        payload = save_res.json()
        resolved_columns = (((payload.get("resolved_config") or {}).get("fax_template") or {}).get("columns") or [])
        quantity_column = next(item for item in resolved_columns if item.get("index") == 1)
        assert quantity_column.get("header") == "常食2F"
        assert quantity_column.get("diet_type") == "no_fish"
        assert quantity_column.get("area_id") == "3F"
        assert quantity_column.get("name") == "qty.no_fish_3f"
    finally:
        assert facility_service.update_config(order["facility"], previous_config)


def test_orders_facility_template_columns_save_preserves_locked_custom_quantity_name():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-facility-template-explicit-002")
    previous_config = config_service.get_facility_config(order["facility"]) or {}

    try:
        columns = [
            {"index": 0, "role": "date", "header": "日付", "name": "date_mmdd"},
            {
                "index": 1,
                "role": "quantity",
                "header": "特食3F",
                "name": "qty.special_diet_3f",
                "diet_type": "special_diet",
                "area_id": "3F",
                "diet_type_locked": True,
                "area_id_locked": True,
                "name_locked": True,
            },
        ]
        save_res = client.put(f"/orders/{order['id']}/facility-template-columns", json={"columns": columns})
        assert save_res.status_code == 200
        payload = save_res.json()
        resolved_columns = (((payload.get("resolved_config") or {}).get("fax_template") or {}).get("columns") or [])
        quantity_column = next(item for item in resolved_columns if item.get("index") == 1)
        assert quantity_column.get("header") == "特食3F"
        assert quantity_column.get("diet_type") == "special_diet"
        assert quantity_column.get("area_id") == "3F"
        assert quantity_column.get("name") == "qty.special_diet_3f"
        assert quantity_column.get("name_locked") is True
    finally:
        assert facility_service.update_config(order["facility"], previous_config)


def test_orders_facility_template_columns_save_repairs_qty_named_non_quantity_column():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-facility-template-repair-qty-note-001")
    previous_config = config_service.get_facility_config(order["facility"]) or {}

    try:
        columns = [
            {"index": 0, "role": "date", "header": "日付"},
            {"index": 1, "role": "daypart", "header": "区分"},
            {"index": 2, "role": "menu_name", "header": "メニュー"},
            {
                "index": 3,
                "role": "quantity",
                "header": "常食",
                "name": "qty.regular_x",
                "diet_type": "regular",
                "area_id": "X",
                "diet_type_locked": True,
                "area_id_locked": True,
            },
            {
                "index": 4,
                "role": "note",
                "header": "不明",
                "name": "qty.unknown_x",
                "name_locked": True,
            },
            {"index": 5, "role": "note", "header": "備考"},
        ]
        save_res = client.put(f"/orders/{order['id']}/facility-template-columns", json={"columns": columns})
        assert save_res.status_code == 200

        payload = save_res.json()
        resolved_columns = (((payload.get("resolved_config") or {}).get("fax_template") or {}).get("columns") or [])
        repaired_column = next(item for item in resolved_columns if item.get("index") == 4)
        assert repaired_column.get("role") == "quantity"
        assert repaired_column.get("diet_type") == "unknown"
        assert repaired_column.get("area_id") == "X"
        assert repaired_column.get("name") == "qty.unknown_x"

        sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
        assert sheet_res.status_code == 200
        sheet = sheet_res.json()
        assert sheet["fields"] == [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.unknown_x",
            "remarks",
        ]
    finally:
        assert facility_service.update_config(order["facility"], previous_config)


def test_orders_facility_template_columns_allows_operator_auth(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-secret")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "operator-secret")

    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-facility-template-admin-001")
    columns = [
        {"index": 0, "role": "date", "header": "日付", "name": "date"},
        {
            "index": 1,
            "role": "quantity",
            "header": "常食2F",
            "name": "qty.regular_2f",
            "diet_type": "regular",
            "area_id": "2F",
        },
    ]

    operator_res = client.put(
        f"/orders/{order['id']}/facility-template-columns",
        json={"columns": columns},
        headers=_basic_header("operator", "operator-secret"),
    )
    assert operator_res.status_code == 200

    admin_res = client.put(
        f"/orders/{order['id']}/facility-template-columns",
        json={"columns": columns},
        headers=_basic_header("admin", "admin-secret"),
    )
    assert admin_res.status_code == 200


def test_orders_facility_template_columns_save_updates_ocr_sheet_header():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-facility-template-sheet-001")
    previous_config = config_service.get_facility_config(order["facility"]) or {}

    try:
        resolved_columns = (
            (((previous_config.get("fax_template") or {}).get("columns")) or [])
        )
        columns = []
        for item in resolved_columns:
            if not isinstance(item, dict):
                continue
            column = dict(item)
            if (
                str(column.get("role") or "").strip().lower() == "quantity"
                and str(column.get("diet_type") or "").strip().lower() == "regular"
                and str(column.get("area_id") or "").strip().lower() == "2f"
            ):
                column["header"] = "新常食2F"
            columns.append(column)

        save_res = client.put(f"/orders/{order['id']}/facility-template-columns", json={"columns": columns})
        assert save_res.status_code == 200

        sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
        assert sheet_res.status_code == 200
        sheet = sheet_res.json()
        field_idx = sheet["fields"].index("qty.regular_2f")
        assert sheet["header"][field_idx] == "新常食2F"
    finally:
        assert facility_service.update_config(order["facility"], previous_config)


def test_orders_facility_template_columns_save_refreshes_current_draft(monkeypatch):
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-facility-template-draft-refresh-001")

    seeded = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "旧常食2F", "備考"],
            "rows": [["01/08", "昼", "Menu A", "2", ""]],
            "row_ids": ["row-refresh-1"],
            "source": "draft_ready",
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
        edited_by="test-seed",
    )
    assert seeded is not None

    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft",
        lambda order_id, use_saved_draft=False, evidence_run_override=None: {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "新常食花", "備考"],
            "rows": [["01/08", "昼", "Menu A", "9", "refreshed"]],
            "row_ids": ["row-refresh-1"],
            "source": "weekly_menu+ocr_payload",
            "warnings": ["quantity_review_required"],
        },
    )

    save_res = client.put(
        f"/orders/{order['id']}/facility-template-columns",
        json={
            "columns": [
                {"index": 0, "role": "date", "header": "日付"},
                {"index": 1, "role": "daypart", "header": "区分"},
                {"index": 2, "role": "menu_name", "header": "メニュー"},
                {
                    "index": 3,
                    "role": "quantity",
                    "header": "新常食花",
                    "name": "qty.regular_hana",
                    "diet_type": "regular",
                    "area_id": "X",
                },
                {"index": 4, "role": "note", "header": "備考"},
            ]
        },
    )
    assert save_res.status_code == 200
    payload = save_res.json()
    assert payload.get("draft_refreshed") is True
    resolved_columns = ((payload.get("resolved_config") or {}).get("fax_template") or {}).get("columns") or []
    quantity_columns = [col for col in resolved_columns if isinstance(col, dict) and col.get("role") == "quantity"]
    assert quantity_columns[0].get("area_id") == "2F"
    assert quantity_columns[0].get("name") == "qty.regular_2f"

    draft_res = client.get(f"/orders/{order['id']}/draft-sheet")
    assert draft_res.status_code == 200
    draft = draft_res.json()
    assert draft["header"][3] == "新常食花"
    assert draft["rows"][0][3] == "9"
    assert isinstance(payload.get("draft_payload"), dict)
    assert payload["draft_payload"]["header"][3] == "新常食花"
    assert payload["draft_payload"]["rows"][0][3] == "9"


def test_orders_force_weekly_menu_api_returns_flattened_repair_draft(monkeypatch):
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-force-weekly-001")

    seeded = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["01/08", "昼", "Menu A", "5", ""]],
            "row_ids": ["row-force-weekly-api-1"],
            "source": "forced_weekly_menu",
            "repair_mode": "forced_weekly_menu_overwrite",
            "repair_metadata": {"mode": "forced_weekly_menu_overwrite", "origin": "operator"},
            "warnings": ["forced_weekly_menu_overwrite"],
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=["forced_weekly_menu_overwrite"],
        edited_by="manual-weekly-menu-overwrite",
    )
    assert seeded is not None

    monkeypatch.setattr(
        order_service,
        "force_overwrite_current_sheet_with_weekly_menu",
        lambda order_id, blank_quantities=False: (seeded, None),
    )

    res = client.post(f"/orders/{order['id']}/draft-sheet/force-weekly-menu")
    assert res.status_code == 200
    payload = res.json()
    assert payload.get("updated") is True
    draft_payload = payload.get("draft_payload") or {}
    assert draft_payload.get("repair_mode") == "forced_weekly_menu_overwrite"
    assert "forced_weekly_menu_overwrite" in (draft_payload.get("warnings") or [])


def test_orders_force_weekly_menu_api_accepts_blank_quantities(monkeypatch):
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-force-weekly-blank-001")

    seeded = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["01/08", "昼", "Menu A", "", ""]],
            "row_ids": ["row-force-weekly-api-blank-1"],
            "source": "forced_weekly_menu",
            "repair_mode": "forced_weekly_menu_overwrite",
            "repair_metadata": {
                "mode": "forced_weekly_menu_overwrite",
                "blank_quantities": True,
                "origin": "operator",
            },
            "warnings": [
                "forced_weekly_menu_overwrite",
                "forced_quantity_manual_entry_required",
            ],
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[
            "forced_weekly_menu_overwrite",
            "forced_quantity_manual_entry_required",
        ],
        edited_by="manual-weekly-menu-overwrite",
    )
    assert seeded is not None

    monkeypatch.setattr(
        order_service,
        "force_overwrite_current_sheet_with_weekly_menu",
        lambda order_id, blank_quantities=False: (seeded, None),
    )

    res = client.post(
        f"/orders/{order['id']}/draft-sheet/force-weekly-menu",
        json={"blank_quantities": True},
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload.get("updated") is True
    draft_payload = payload.get("draft_payload") or {}
    assert draft_payload.get("repair_mode") == "forced_weekly_menu_overwrite"
    assert "forced_quantity_manual_entry_required" in (draft_payload.get("warnings") or [])


def test_orders_force_facility_schema_api_returns_flattened_repair_draft(monkeypatch):
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-force-facility-001")

    seeded = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["01/08", "昼", "Menu A", "", ""]],
            "row_ids": ["row-force-facility-api-1"],
            "source": "forced_facility_schema",
            "repair_mode": "forced_facility_schema_overwrite",
            "repair_metadata": {
                "mode": "forced_facility_schema_overwrite",
                "blank_quantities": True,
                "origin": "operator",
            },
            "warnings": [
                "forced_facility_schema_overwrite",
                "forced_quantity_manual_entry_required",
            ],
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[
            "forced_facility_schema_overwrite",
            "forced_quantity_manual_entry_required",
        ],
        edited_by="manual-facility-schema-overwrite",
    )
    assert seeded is not None

    monkeypatch.setattr(
        order_service,
        "force_overwrite_current_sheet_with_facility_schema",
        lambda order_id, blank_quantities=True: (seeded, None),
    )

    res = client.post(
        f"/orders/{order['id']}/draft-sheet/force-facility-schema",
        json={"blank_quantities": True},
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload.get("updated") is True
    draft_payload = payload.get("draft_payload") or {}
    assert draft_payload.get("repair_mode") == "forced_facility_schema_overwrite"
    assert "forced_quantity_manual_entry_required" in (draft_payload.get("warnings") or [])


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


def test_orders_facility_template_columns_save_preserves_existing_quantity_values_when_adding_column(monkeypatch):
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-facility-template-preserve-qty-001")

    seeded = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": [
                "date_mmdd",
                "daypart",
                "menu",
                "qty.regular_2f",
                "qty.soft_2f",
                "qty.mixer_2f",
                "remarks",
            ],
            "header": ["日付", "区分", "メニュー", "常食2F", "軟菜2F", "ミキサー2F", "備考"],
            "rows": [["01/08", "昼", "Menu A", "1", "2", "3", "seeded"]],
            "row_ids": ["row-preserve-1"],
            "source": "draft_ready",
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
        edited_by="test-seed",
    )
    assert seeded is not None

    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft",
        lambda order_id, use_saved_draft=False, evidence_run_override=None: {
            "fields": [
                "date_mmdd",
                "daypart",
                "menu",
                "qty.regular_2f",
                "qty.no_fish_2f",
                "qty.soft_2f",
                "qty.mixer_2f",
                "remarks",
            ],
            "header": ["日付", "区分", "メニュー", "常食2F", "魚禁2F", "軟菜2F", "ミキサー2F", "備考"],
            "rows": [["01/08", "昼", "Menu A", "1", "2", "3", "", "rebuilt"]],
            "row_ids": ["row-preserve-1"],
            "source": "weekly_menu+ocr_payload",
            "warnings": [],
        },
    )

    save_res = client.put(
        f"/orders/{order['id']}/facility-template-columns",
        json={
            "columns": [
                {"index": 0, "role": "date", "header": "日付"},
                {"index": 1, "role": "daypart", "header": "区分"},
                {"index": 2, "role": "menu_name", "header": "メニュー"},
                {"index": 3, "role": "quantity", "header": "常食2F", "diet_type": "regular", "area_id": "2F"},
                {"index": 4, "role": "quantity", "header": "魚禁2F", "diet_type": "no_fish", "area_id": "2F"},
                {"index": 5, "role": "quantity", "header": "軟菜2F", "diet_type": "soft", "area_id": "2F"},
                {"index": 6, "role": "quantity", "header": "ミキサー2F", "diet_type": "mixer", "area_id": "2F"},
                {"index": 7, "role": "note", "header": "備考"},
            ]
        },
    )
    assert save_res.status_code == 200
    payload = save_res.json()
    assert payload.get("draft_refreshed") is True

    draft_res = client.get(f"/orders/{order['id']}/draft-sheet")
    assert draft_res.status_code == 200
    draft = draft_res.json()
    assert draft["fields"][3:7] == [
        "qty.regular_2f",
        "qty.no_fish_2f",
        "qty.soft_2f",
        "qty.mixer_2f",
    ]
    assert draft["rows"][0][3:7] == ["1", "", "2", "3"]
    assert isinstance(payload.get("draft_payload"), dict)
    assert payload["draft_payload"]["rows"][0][3:7] == ["1", "", "2", "3"]


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


def test_orders_facility_template_columns_save_accepts_hana_tsuki_headers():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-facility-template-hanatsuki-001")
    previous_config = config_service.get_facility_config(order["facility"]) or {}

    try:
        save_res = client.put(
            f"/orders/{order['id']}/facility-template-columns",
            json={
                "columns": [
                    {"index": 0, "role": "date", "header": "日付"},
                    {"index": 1, "role": "daypart", "header": "区分"},
                    {"index": 2, "role": "menu_name", "header": "メニュー"},
                    {"index": 3, "role": "quantity", "header": "常食花", "diet_type": "regular", "area_id": "花"},
                    {"index": 4, "role": "quantity", "header": "常食月", "diet_type": "regular", "area_id": "月"},
                    {"index": 5, "role": "note", "header": "備考"},
                ]
            },
        )
        assert save_res.status_code == 200
        payload = save_res.json()
        resolved_columns = ((payload.get("resolved_config") or {}).get("fax_template") or {}).get("columns") or []
        quantity_columns = [col for col in resolved_columns if isinstance(col, dict) and col.get("role") == "quantity"]
        assert [col.get("header") for col in quantity_columns] == ["常食花", "常食月"]
        assert [col.get("area_id") for col in quantity_columns] == ["2F", "3F"]
        assert [col.get("name") for col in quantity_columns] == ["qty.regular_2f", "qty.regular_3f"]
    finally:
        assert facility_service.update_config(order["facility"], previous_config)


def test_orders_facility_template_columns_save_canonicalizes_invalid_quantity_tokens():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-facility-template-canonical-001")
    previous_config = config_service.get_facility_config(order["facility"]) or {}

    try:
        columns = [
            {"index": 0, "role": "date", "header": "日付"},
            {"index": 1, "role": "daypart", "header": "区分"},
            {"index": 2, "role": "menu_name", "header": "メニュー"},
            {"index": 3, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "regular"},
            {"index": 4, "role": "quantity", "header": "None", "diet_type": "regular", "area_id": "none"},
            {"index": 5, "role": "quantity", "header": "肉禁", "diet_type": "reglur", "area_id": "niku-kin"},
            {"index": 6, "role": "quantity", "header": "魚禁", "diet_type": "reglur", "area_id": "sakana-kin"},
            {"index": 7, "role": "quantity", "header": "変更1", "diet_type": "regular", "area_id": "henkou1"},
            {"index": 8, "role": "quantity", "header": "変更2", "diet_type": "regular", "area_id": "henkou2"},
            {"index": 9, "role": "note", "header": "備考"},
        ]

        save_res = client.put(f"/orders/{order['id']}/facility-template-columns", json={"columns": columns})
        assert save_res.status_code == 200

        sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
        assert sheet_res.status_code == 200
        sheet = sheet_res.json()
        assert sheet["header"][3:9] == ["常食", "None", "肉禁", "魚禁", "変更1", "変更2"]
        assert sheet["fields"][3:9] == [
            "qty.regular_x",
            "qty.unknown_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.change_1_x",
            "qty.change_2_x",
        ]
    finally:
        assert facility_service.update_config(order["facility"], previous_config)


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


def test_orders_ocr_sheet_and_apply_api_without_facility():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order_without_facility("msg-api-sheet-no-fac-001")

    sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
    assert sheet_res.status_code == 400
    assert sheet_res.json().get("detail") == "facility_missing"

    apply_payload = {
        "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
        "rows": [["02/15", "朝", "Menu B", "6", "api"]],
        "ui_mode": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "row_ids": ["row-api-no-fac-1"],
    }
    apply_res = client.post(f"/orders/{order['id']}/ocr-apply", json=apply_payload)
    assert apply_res.status_code == 400
    assert apply_res.json().get("detail") == "facility missing"


def test_orders_ocr_review_api_passes_provider_and_prompt(monkeypatch):
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-review-001")
    captured: dict[str, str | None] = {}

    def _fake_review(
        order_id: str,
        *,
        provider: str | None = None,
        prompt: str | None = None,
        pdf_variant: str | None = None,
    ):
        captured["order_id"] = order_id
        captured["provider"] = provider
        captured["prompt"] = prompt
        captured["pdf_variant"] = pdf_variant
        return {
            "id": order_id,
            "llm_review": {
                "provider": provider,
                "needs_more_review": True,
                "issues": [
                    {
                        "row_id": "row-1",
                        "field": "qty.regular_2f",
                        "issue_code": "review_required",
                    }
                ],
            },
        }, None

    monkeypatch.setattr(order_service, "review_ocr_table_with_llm", _fake_review)

    review_res = client.post(
        f"/orders/{order['id']}/ocr-review",
        json={
            "ocr_provider": "gemini",
            "ocr_prompt": "verify baseline against pdf",
            "pdf_variant": "corrected",
        },
    )

    assert review_res.status_code == 200
    assert captured == {
        "order_id": order["id"],
        "provider": "gemini",
        "prompt": "verify baseline against pdf",
        "pdf_variant": "corrected",
    }
    payload = review_res.json()
    assert payload["id"] == order["id"]
    assert payload["llm_review"]["provider"] == "gemini"
    assert payload["llm_review"]["needs_more_review"] is True

def test_orders_ocr_review_api_maps_errors(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(
        order_service,
        "review_ocr_table_with_llm",
        lambda _order_id, *, provider=None, prompt=None, pdf_variant=None: (None, "order_not_found"),
    )
    not_found = client.post("/orders/ORDmissing/ocr-review", json={})
    assert not_found.status_code == 404
    assert not_found.json().get("detail") == "order not found"

    monkeypatch.setattr(
        order_service,
        "review_ocr_table_with_llm",
        lambda _order_id, *, provider=None, prompt=None, pdf_variant=None: (None, "ocr_payload_missing"),
    )
    bad_request = client.post("/orders/ORDdummy/ocr-review", json={})
    assert bad_request.status_code == 400
    assert bad_request.json().get("detail") == "ocr_payload_missing"

    invalid_provider = client.post("/orders/ORDdummy/ocr-review", json={"ocr_provider": "pipeline"})
    assert invalid_provider.status_code == 400
    assert invalid_provider.json().get("detail") == "ocr_provider must be one of openai|gemini"

    invalid_pdf_variant = client.post("/orders/ORDdummy/ocr-review", json={"pdf_variant": "auto"})
    assert invalid_pdf_variant.status_code == 400
    assert invalid_pdf_variant.json().get("detail") == "pdf_variant must be one of raw|corrected"


def test_orders_apply_patch_candidate_api_flow(monkeypatch):
    client = TestClient(app)
    captured: dict[str, str | None] = {}

    def _fake_apply(order_id: str, *, candidate_id: str | None = None, applied_by: str | None = None):
        captured["order_id"] = order_id
        captured["candidate_id"] = candidate_id
        captured["applied_by"] = applied_by
        return ({
            "candidate": {"id": "OPCtest", "candidate_state": "applied"},
            "draft": {"id": "ODRtest", "latest_patch_candidate_id": "OPCtest"},
        }, None)

    monkeypatch.setattr(order_service, "apply_patch_candidate_to_draft", _fake_apply)

    res = client.post(
        "/orders/ORDpatch/draft-sheet/apply-patch-candidate",
        json={"candidate_id": "OPCtest"},
    )

    assert res.status_code == 200
    assert captured == {
        "order_id": "ORDpatch",
        "candidate_id": "OPCtest",
        "applied_by": "operator",
    }
    payload = res.json()
    assert payload["candidate"]["id"] == "OPCtest"
    assert payload["candidate"]["candidate_state"] == "applied"
    assert payload["draft"]["latest_patch_candidate_id"] == "OPCtest"


def test_orders_apply_patch_candidate_api_returns_not_found(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(
        order_service,
        "apply_patch_candidate_to_draft",
        lambda order_id, *, candidate_id=None, applied_by=None: (None, "patch_candidate_not_found"),
    )

    res = client.post("/orders/ORDmissing/draft-sheet/apply-patch-candidate", json={"candidate_id": "OPCmissing"})

    assert res.status_code == 404
    assert res.json().get("detail") == "patch candidate not found"


def test_orders_ocr_sheet_api_returns_template_validation_error():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-sheet-invalid-template-001")
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
        sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
        assert sheet_res.status_code == 400
        assert sheet_res.json().get("detail") == "sheet_template_field_invalid"
    finally:
        config_service.get_facility_config = original_get


def test_orders_ocr_sheet_api_uses_weekly_menu_shell_and_current_order_line_quantities():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_weekly_menu_seed_order_2099_11("msg-api-sheet-priority-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "昼", "OCRノイズ", "99", "", "", "", "", "", ""],
            ]
        },
    )

    first_sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
    assert first_sheet_res.status_code == 200
    first_sheet = first_sheet_res.json()
    assert str(first_sheet.get("source") or "").startswith("weekly_menu")
    fields = first_sheet.get("fields") or []
    qty_idx = next(
        idx
        for idx, field in enumerate(fields)
        if field in {"qty.regular_2f", "qty.regular_x"}
    )
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)
    first_lunch = next(
        row
        for row in (first_sheet.get("rows") or [])
        if row[date_idx] == "11/15" and row[daypart_idx] == "昼" and row[menu_idx] == "昼メニュー"
    )
    assert first_lunch[qty_idx] == "6"

    update_payload = {
        "lines": [
            {
                "line_id": "line-contract-lunch-updated",
                "date": "2099-11-15",
                "daypart": "昼",
                "menu_name": "昼メニュー",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 20,
            }
        ]
    }
    update_res = client.put(f"/orders/{order['id']}/lines", json=update_payload)
    assert update_res.status_code == 200
    assert update_res.json().get("updated") is True

    second_sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
    assert second_sheet_res.status_code == 200
    second_sheet = second_sheet_res.json()
    assert str(second_sheet.get("source") or "").startswith("weekly_menu")
    second_lunch = next(
        row
        for row in (second_sheet.get("rows") or [])
        if row[date_idx] == "11/15" and row[daypart_idx] == "昼" and row[menu_idx] == "昼メニュー"
    )
    assert second_lunch[qty_idx] == "20"


def test_orders_ocr_sheet_api_blocks_apply_when_quantity_mapping_is_unmapped():
    order_service.clear_all()
    client = TestClient(app)
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-api-sheet-unmapped-warning-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "line_id": "line-contract-unmapped-1",
                "date": "2099-11-15",
                "daypart": "朝",
                "menu_name": "朝メニュー",
                "diet_type": "regular",
                "area_id": None,
                "bag_type": "standard",
                "quantity_original": 11,
            }
        ],
    )
    original_get = config_service.get_facility_config

    def _mock_get(facility_id: str):
        current = original_get(facility_id)
        if not current or facility_id != "FAC00001":
            return current
        payload_cfg = dict(current)
        template = dict(payload_cfg.get("fax_template") or {})
        template["main_ocr_row_fields"] = [
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
        payload_cfg["fax_template"] = template
        return payload_cfg

    config_service.get_facility_config = _mock_get
    try:
        sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
        assert sheet_res.status_code == 200
        sheet = sheet_res.json()
        assert str(sheet.get("source") or "").startswith("weekly_menu")
        assert sheet.get("can_apply") is False
        assert "sheet_quantity_column_unmapped" in (sheet.get("apply_blockers") or [])
        assert "sheet_quantity_column_unmapped" in (sheet.get("warnings") or [])
    finally:
        config_service.get_facility_config = original_get


def test_orders_ocr_sheet_api_maps_sheet_errors_to_400(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id: (None, "sheet_week_dates_incomplete"))
    res_week = client.get("/orders/ORDdummy/ocr-sheet")
    assert res_week.status_code == 400
    assert res_week.json().get("detail") == "sheet_week_dates_incomplete"

    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id: (None, "sheet_quantity_column_unmapped"))
    res_qty = client.get("/orders/ORDdummy/ocr-sheet")
    assert res_qty.status_code == 400
    assert res_qty.json().get("detail") == "sheet_quantity_column_unmapped"

    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id: (None, "sheet_canonical_mismatch"))
    res_canonical = client.get("/orders/ORDdummy/ocr-sheet")
    assert res_canonical.status_code == 400
    assert res_canonical.json().get("detail") == "sheet_canonical_mismatch"

    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id: (None, "sheet_suspicious_blank_row"))
    res_blank = client.get("/orders/ORDdummy/ocr-sheet")
    assert res_blank.status_code == 400
    assert res_blank.json().get("detail") == "sheet_suspicious_blank_row"


def test_orders_ocr_sheet_api_recovery_prefers_saved_draft_monthly_menu_blocker(monkeypatch):
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-monthly-menu-missing-block")
    _clear_monthly_menu("2026-01")
    order_service.draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "source": "review_blocked",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [],
            "row_ids": [],
            "warnings": ["monthly_menu_object_missing"],
            "menu_diagnostics": {
                "month_id": "2026-01",
                "resolved_week_id": "2026-01",
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
                ["01/08", "昼", "Menu A", "3", "", "", "", "", "", ""],
            ],
            "date_strings": ["01/08"],
        },
    )

    original_get_ocr_sheet = order_service.get_ocr_sheet
    monkeypatch.setattr(
        order_service,
        "get_ocr_sheet",
        lambda requested_order_id, **_kwargs: (
            (None, "menu_entries_missing")
            if requested_order_id == order["id"]
            else original_get_ocr_sheet(requested_order_id, **_kwargs)
        ),
    )

    sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
    assert sheet_res.status_code == 200
    sheet = sheet_res.json()
    assert "monthly_menu_object_missing" in (sheet.get("warnings") or [])
    assert sheet.get("can_apply") is False
    assert "monthly_menu_object_missing" in (sheet.get("apply_blockers") or [])
    assert "monthly_menu_object_missing" in (sheet.get("confirm_blockers") or [])
    assert (sheet.get("menu_diagnostics") or {}).get("order_codes") == ["monthly_menu_object_missing"]


def test_orders_ocr_sheet_api_includes_trace():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_weekly_menu_seed_order_2099_11("msg-api-sheet-trace-001")
    sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
    assert sheet_res.status_code == 200
    sheet = sheet_res.json()
    trace = sheet.get("trace")
    assert isinstance(trace, dict)
    trace_rows = trace.get("rows")
    assert isinstance(trace_rows, list)
    assert len(trace_rows) == len(sheet.get("rows") or [])


def test_orders_draft_sheet_endpoint_exposes_current_sheet_context_metadata(monkeypatch):
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-draft-context-001")

    monkeypatch.setattr(
        order_service,
        "get_current_sheet_context",
        lambda _order_id, **_kwargs: {
            "order_id": _order_id,
            "draft_record": {
                "id": None,
                "order_id": _order_id,
                "base_evidence_run_id": "OEVtest",
                "draft_sheet_json": {
                    "source": "weekly_menu+ocr_payload",
                    "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
                    "header": ["日付", "区分", "メニュー", "常食2F"],
                    "rows": [["03/22", "朝", "Menu A", "6"]],
                    "row_ids": ["semantic-1"],
                    "resolved_week_id": "2026-03@2026-03-22~2026-03-28",
                    "seed_source": "weekly_menu",
                    "enrichment_source": "ocr_payload",
                    "menu_diagnostics": {
                        "month_id": "2026-03",
                        "resolved_week_id": "2026-03@2026-03-22~2026-03-28",
                        "order_codes": ["menu_entries_missing"],
                        "row_codes": [],
                    },
                },
                "draft_state": "draft_ready",
                "blockers_json": [],
                "warnings_json": [],
            },
            "draft_payload": {
                "source": "weekly_menu+ocr_payload",
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
                "header": ["日付", "区分", "メニュー", "常食2F"],
                "rows": [["03/22", "朝", "Menu A", "6"]],
                "row_ids": ["semantic-1"],
                "resolved_week_id": "2026-03@2026-03-22~2026-03-28",
                "seed_source": "weekly_menu",
                "enrichment_source": "ocr_payload",
                "menu_diagnostics": {
                    "month_id": "2026-03",
                    "resolved_week_id": "2026-03@2026-03-22~2026-03-28",
                    "order_codes": ["menu_entries_missing"],
                    "row_codes": [],
                },
            },
        },
    )

    res = client.get(f"/orders/{order['id']}/draft-sheet")

    assert res.status_code == 200
    body = res.json()
    assert body["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"
    assert body["seed_source"] == "weekly_menu"
    assert body["enrichment_source"] == "ocr_payload"
    assert body["menu_diagnostics"]["order_codes"] == ["menu_entries_missing"]


def test_ocr_sheet_and_pages_surface_recovery_required_without_wait(monkeypatch):
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-sheet-loading-root-fix")

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="failed")
    update_job(
        job_id,
        status="failed",
        output_reference="file://missing-output.json",
        error_message="ocr_output_missing",
        metrics={"order_id": order["id"]},
    )

    def _raise_missing(_uri: str):
        raise FileNotFoundError("missing object")

    def _retry_called(*_args, **_kwargs):
        raise AssertionError("sheet read path must not retry OCR output recovery")

    monkeypatch.setattr(order_service, "load_bytes_from_uri", _raise_missing)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _retry_called)

    draft_res = client.get(f"/orders/{order['id']}/draft-sheet")
    assert draft_res.status_code == 200
    draft = draft_res.json()
    assert "ocr_evidence_recovery_required" in (draft.get("warnings") or [])

    sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
    assert sheet_res.status_code == 200
    sheet = sheet_res.json()
    assert "ocr_evidence_recovery_required" in (sheet.get("warnings") or [])
    assert sheet.get("source") in {
        "weekly_menu_blocked",
        "ocr_sheet_blocked",
        "no_current_state_blocked",
        "review_blocked",
    }

    pages_res = client.get(f"/orders/{order['id']}/ocr-pages")
    assert pages_res.status_code == 409
    assert pages_res.json() == {
        "recovery_required": True,
        "detail": "ocr evidence recovery required",
    }
