from __future__ import annotations

from datetime import date

from src.services.ocr_week_rerun_service import (
    ApiJsonResult,
    build_reparse_request_body,
    compare_backup_bundle_to_live,
    collect_week_orders,
    compose_api_root,
    fetch_order_backup_bundle,
    load_order_backup_bundle,
    summarize_artifact_for_compare,
    wait_for_ocr_terminal,
    write_order_backup_bundle,
)


class _FakeClient:
    def __init__(self) -> None:
        self.get_calls: list[tuple[str, dict | None]] = []
        self.post_calls: list[tuple[str, dict | None]] = []
        self.responses: dict[tuple[str, str], list[ApiJsonResult]] = {}

    def queue_get(self, path: str, result: ApiJsonResult, params: dict | None = None) -> None:
        self.responses.setdefault(("GET", self._key(path, params)), []).append(result)

    def queue_post(self, path: str, result: ApiJsonResult, body: dict | None = None) -> None:
        self.responses.setdefault(("POST", self._key(path, body)), []).append(result)

    def get_json(self, path: str, params: dict | None = None) -> ApiJsonResult:
        self.get_calls.append((path, params))
        key = ("GET", self._key(path, params))
        return self.responses[key].pop(0)

    def post_json(self, path: str, body: dict | None = None) -> ApiJsonResult:
        self.post_calls.append((path, body))
        key = ("POST", self._key(path, body))
        return self.responses[key].pop(0)

    @staticmethod
    def _key(path: str, payload: dict | None) -> str:
        normalized = tuple(sorted((payload or {}).items()))
        return f"{path}|{normalized}"


def test_compose_api_root_adds_api_prefix_only_once():
    assert compose_api_root("https://example.test", api_prefix="/api") == "https://example.test/api"
    assert compose_api_root("https://example.test/api", api_prefix="/api") == "https://example.test/api"
    assert compose_api_root("https://worker.test", api_prefix="") == "https://worker.test"


def test_collect_week_orders_dedupes_order_ids_and_keeps_latest_received_at():
    client = _FakeClient()
    client.queue_get(
        "/orders/by-line-date",
        ApiJsonResult(
            status_code=200,
            ok=True,
            data={
                "orders": [
                    {"id": "ORD1", "received_at": "2026-02-15T10:00:00", "facility": "FAC1"},
                    {"id": "ORD2", "received_at": "2026-02-15T09:00:00", "facility": "FAC2"},
                ]
            },
        ),
        params={"date": "2026-02-15"},
    )
    client.queue_get(
        "/orders/by-line-date",
        ApiJsonResult(
            status_code=200,
            ok=True,
            data={
                "orders": [
                    {"id": "ORD1", "received_at": "2026-02-16T11:00:00", "facility": "FAC1"},
                ]
            },
        ),
        params={"date": "2026-02-16"},
    )
    orders, summaries = collect_week_orders(
        client,
        start_date=date(2026, 2, 15),
        end_date=date(2026, 2, 16),
    )
    assert [item["id"] for item in orders] == ["ORD2", "ORD1"]
    assert orders[-1]["received_at"] == "2026-02-16T11:00:00"
    assert summaries == [
        {"date": "2026-02-15", "status_code": 200, "ok": True, "count": 2, "error": None},
        {"date": "2026-02-16", "status_code": 200, "ok": True, "count": 1, "error": None},
    ]


def test_fetch_order_backup_bundle_captures_success_and_error_artifacts(tmp_path):
    client = _FakeClient()
    client.queue_get("/orders/ORD1", ApiJsonResult(status_code=200, ok=True, data={"id": "ORD1"}))
    client.queue_get("/orders/ORD1/ocr-output", ApiJsonResult(status_code=404, ok=False, data={"detail": "ocr output not found"}, error="ocr output not found"))
    client.queue_get("/orders/ORD1/ocr-pages", ApiJsonResult(status_code=202, ok=False, data={"pending": True}, error="pending"))
    client.queue_get("/orders/ORD1/ocr-sheet", ApiJsonResult(status_code=200, ok=True, data={"rows": []}))
    client.queue_get("/orders/ORD1/ocr-history", ApiJsonResult(status_code=200, ok=True, data={"revisions": []}))
    client.queue_get("/orders/ORD1/history", ApiJsonResult(status_code=200, ok=True, data={"items": []}), params={"limit": 123})
    client.queue_get("/orders/ORD1/ocr-raw", ApiJsonResult(status_code=404, ok=False, data={"detail": "ocr raw not found"}, error="ocr raw not found"))

    bundle = fetch_order_backup_bundle(client, order_id="ORD1", history_limit=123)
    files = write_order_backup_bundle(output_root=tmp_path, bundle=bundle)

    assert bundle["artifacts"]["order"]["ok"] is True
    assert bundle["artifacts"]["ocr_output"]["status_code"] == 404
    assert bundle["artifacts"]["ocr_pages"]["status_code"] == 202
    assert bundle["artifacts"]["order_history"]["data"] == {"items": []}
    assert set(files) >= {"bundle", "order", "ocr_output", "ocr_pages", "ocr_sheet", "ocr_history", "order_history", "ocr_raw"}


def test_wait_for_ocr_terminal_polls_until_non_running_status():
    client = _FakeClient()
    client.queue_get("/orders/ORD1", ApiJsonResult(status_code=200, ok=True, data={"id": "ORD1", "ocr_status": "running"}))
    client.queue_get("/orders/ORD1", ApiJsonResult(status_code=200, ok=True, data={"id": "ORD1", "ocr_status": "pending"}))
    client.queue_get("/orders/ORD1", ApiJsonResult(status_code=200, ok=True, data={"id": "ORD1", "ocr_status": "done", "ocr_error": None}))
    ticks = iter([0.0, 1.0, 2.0])
    sleeps: list[float] = []

    result = wait_for_ocr_terminal(
        client,
        order_id="ORD1",
        timeout_seconds=30,
        poll_seconds=3,
        monotonic_fn=lambda: next(ticks),
        sleep_fn=lambda seconds: sleeps.append(seconds),
    )

    assert result["terminal"] is True
    assert result["timeout"] is False
    assert result["order"]["ocr_status"] == "done"
    assert sleeps == [3, 3]


def test_build_reparse_request_body_keeps_default_llm_assist():
    assert build_reparse_request_body() == {"llm_assist": True}
    assert build_reparse_request_body(llm_assist=False, ocr_provider="gemini", ocr_prompt="  test  ") == {
        "llm_assist": False,
        "ocr_provider": "gemini",
        "ocr_prompt": "test",
    }


def test_compare_backup_bundle_to_live_detects_sheet_and_order_changes(tmp_path):
    bundle = {
        "order_id": "ORD1",
        "artifacts": {
            "order": {"status_code": 200, "ok": True, "data": {"ocr_status": "running", "week_value": "2026-02@2026-02-15~2026-02-21", "lines": []}, "error": None},
            "ocr_sheet": {"status_code": 200, "ok": True, "data": {"source": "weekly_menu", "warnings": [], "rows": [["02/15", "朝", "A", "1"]]}, "error": None},
            "ocr_output": {"status_code": 202, "ok": False, "data": {"pending": True}, "error": "pending"},
        },
    }
    write_order_backup_bundle(output_root=tmp_path, bundle=bundle)
    loaded = load_order_backup_bundle(output_root=tmp_path, order_id="ORD1")
    live = {
        "order_id": "ORD1",
        "artifacts": {
            "order": {"status_code": 200, "ok": True, "data": {"ocr_status": "done", "week_value": "2026-02@2026-02-15~2026-02-21", "lines": [{"date": "2026-02-15", "daypart": "朝", "menu_name": "A", "diet_type": "regular", "area_id": "X", "quantity_original": 1}]}, "error": None},
            "ocr_sheet": {"status_code": 200, "ok": True, "data": {"source": "weekly_menu+ocr_payload", "warnings": [], "rows": [["02/15", "朝", "A", "2"]]}, "error": None},
            "ocr_output": {"status_code": 200, "ok": True, "data": {"metrics": {"provider": "gemini", "row_count": 1, "line_count": 1}, "rows": [["02/15", "朝", "A", "2"]], "table_raw": "x"}, "error": None},
        },
    }

    comparison = compare_backup_bundle_to_live(backup_bundle=loaded, live_bundle=live)

    assert comparison["changed"] is True
    assert set(comparison["changed_artifacts"]) == {"order", "ocr_output", "ocr_sheet"}


def test_summarize_artifact_for_compare_compacts_relevant_fields():
    summary = summarize_artifact_for_compare(
        "order",
        {
            "status_code": 200,
            "ok": True,
            "data": {
                "ocr_status": "done",
                "ocr_error": None,
                "week_value": "2026-02@2026-02-15~2026-02-21",
                "lines": [
                    {
                        "date": "2026-02-15",
                        "daypart": "朝",
                        "menu_name": "A",
                        "diet_type": "regular",
                        "area_id": "X",
                        "quantity_original": 1,
                        "quantity_corrected": None,
                        "change_note": None,
                        "ignore_me": "volatile",
                    }
                ],
            },
            "error": None,
        },
    )

    assert summary["ocr_status"] == "done"
    assert summary["line_count"] == 1
    assert isinstance(summary["line_digest"], str) and len(summary["line_digest"]) == 64
