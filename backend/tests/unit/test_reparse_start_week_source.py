from datetime import date, datetime

from src.services import order_service


def test_reparse_week_missing_stops_without_active_or_cache(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("late source read")
    monkeypatch.setattr(order_service, "_get_ocr_output_without_legacy_edits", forbidden)
    monkeypatch.setattr(order_service, "_load_order_ocr_cache", forbidden)
    assert order_service._resolve_reparse_own_week(
        captured_week=None, received_at=datetime(2026, 10, 7),
        lines=[], own_payload=None,
    ) is None


def test_explicit_start_week_wins_without_altering_zero():
    lines = [{"date": date(2026, 9, 7), "quantity_original": 0, "quantity_corrected": None}]
    week = order_service._resolve_reparse_own_week(
        captured_week="2026-10", received_at=datetime(2026, 10, 7),
        lines=lines, own_payload=None,
    )
    assert week == "2026-10"
    assert lines[0]["quantity_original"] == 0
    assert lines[0]["quantity_corrected"] is None


def test_own_recognized_dates_resolve_without_received_month_default():
    assert order_service._resolve_reparse_own_week(
        captured_week=None, received_at=datetime(2026, 10, 7),
        lines=[{"date": date(2026, 9, 7), "quantity_original": 0}], own_payload=None,
    ) == "2026-09"


def test_llm_baseline_receives_only_captured_draft_and_own_payload(monkeypatch):
    captured = {"draft": {"id": "operator-selected-draft", "draft_sheet_json": {"rows": [[0]]}}}
    own = {"tables": [{"rows": [[0]]}]}
    seen = []
    monkeypatch.setattr(order_service, "_captured_reparse_read_input", lambda order_id: captured)
    def baseline(**kwargs):
        seen.append(kwargs)
        return {"rows": [[0]]}
    monkeypatch.setattr(order_service, "_resolve_llm_review_baseline", baseline)
    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("late sheet")))
    assert order_service._resolve_reparse_llm_baseline(order_id="order", template={}, fallback_payload=own) == {"rows": [[0]]}
    assert seen[0]["synchronous_write_input"] is captured
    assert seen[0]["payload"] is own
    assert order_service._resolve_reparse_llm_baseline(order_id="order", template={}, fallback_payload=None) is None
