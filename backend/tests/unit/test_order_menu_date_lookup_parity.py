"""Compare the collector with its frozen 4abd7a8 nested-loop implementation."""
from copy import deepcopy

import pytest

from src.services import order_serialization_service as service


def original(week_id, facility_id=None):
    items = []
    for month_id in service.sheet_week_service.sheet_week_month_ids(week_id):
        payload = (
            service.menu_service.get_menu_for_facility(month_id, facility_id, allow_unresolved=True)
            if facility_id else service.menu_service.get_menu(month_id, allow_unresolved=True)
        ) or {}
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            payload_item = dict(item)
            payload_item["menu_dates"] = [
                entry.get("menu_date") for entry in payload.get("entries", [])
                if service.menu_service._monthly_item_identity_key_from_payload(entry)[:4]
                == service.menu_service._monthly_item_identity_key_from_payload(item)[:4]
            ]
            items.append(payload_item)
    return items


def install(monkeypatch, payloads):
    calls = []
    monkeypatch.setattr(service.sheet_week_service, "sheet_week_month_ids", lambda week: list(payloads))
    def load(month, *args, **kwargs):
        calls.append((month, args, kwargs))
        return payloads[month]
    monkeypatch.setattr(service.menu_service, "get_menu", load)
    monkeypatch.setattr(service.menu_service, "get_menu_for_facility", load)
    return calls


def item(name="food", **kwargs):
    return dict(name=name, daypart="昼", category="主菜", diet_type="regular", **kwargs)


@pytest.mark.parametrize("facility", [None, "FAC00015"])
def test_complete_payload_parity_across_months(monkeypatch, facility):
    base = item(qty_per_serving=0, menu_dates=["old"], extra={"keep": None})
    variants = [base, dict(base), item("other"), {**base, "daypart": "朝"},
                {**base, "diet_type": "mixer"}, {**base, "category": "副菜"}]
    entries = [{**row, "menu_date": date} for row, date in zip(variants, ["2026-09-30", "2026-09-30", None, "2026-10-01", "", "2026-10-02"])]
    payloads = {"2026-09": {"items": variants + [None, 5, {}, {"name": " "}], "entries": entries},
                "2026-10": {"items": [base], "entries": [{**base, "menu_date": "2026-10-01"}]}}
    before = deepcopy(payloads)
    calls = install(monkeypatch, payloads)
    expected = original("week", facility)
    old_calls = list(calls)
    calls.clear()
    actual = service._collect_menu_items_for_week("week", facility)
    assert actual == expected
    assert calls == old_calls
    assert payloads == before
    assert actual[0]["menu_dates"] == ["2026-09-30", "2026-09-30"]
    actual[0]["menu_dates"].append("changed")
    assert actual[1]["menu_dates"] == expected[1]["menu_dates"]
    assert payloads == before


@pytest.mark.parametrize("raw_items", [None, {}, [], [None, 0, {}, {"name": " "}]])
def test_no_valid_items_does_not_parse_entries(monkeypatch, raw_items):
    install(monkeypatch, {"month": {"items": raw_items, "entries": None}})
    assert service._collect_menu_items_for_week("week") == original("week") == []


@pytest.mark.parametrize("entries", [None, 7, [None], [7], [item(), None], "bad"])
def test_malformed_entries_preserve_exception(monkeypatch, entries):
    install(monkeypatch, {"month": {"items": [item()], "entries": entries}})
    outcomes = []
    for algorithm in (original, service._collect_menu_items_for_week):
        try:
            algorithm("week")
        except Exception as exc:
            outcomes.append((type(exc), str(exc)))
        else:
            pytest.fail("malformed input must still fail")
    assert outcomes[0] == outcomes[1]


class GuardedEntry(dict):
    def get(self, key, *args):
        if key == "menu_date":
            raise RuntimeError("date-access")
        return super().get(key, *args)


def test_unmatched_date_not_read_and_matching_error_order_preserved(monkeypatch):
    install(monkeypatch, {"month": {"items": [item(), item()], "entries": [GuardedEntry(item("other"))]}})
    assert service._collect_menu_items_for_week("week") == original("week")
    install(monkeypatch, {"month": {"items": [item()], "entries": [GuardedEntry(item()), None]}})
    for algorithm in (original, service._collect_menu_items_for_week):
        with pytest.raises(RuntimeError, match="^date-access$"):
            algorithm("week")


def test_empty_entries_do_not_normalize_item(monkeypatch):
    install(monkeypatch, {"month": {"items": [item(), item()], "entries": []}})
    def forbidden(*args):
        pytest.fail("empty entries must not evaluate item identity")
    monkeypatch.setattr(service.menu_service, "_monthly_item_identity_key_from_payload", forbidden)
    assert service._collect_menu_items_for_week("week") == original("week")


def test_identity_calls_linear_and_next_call_reads_again(monkeypatch):
    payloads = {"month": {"items": [item() for _ in range(50)],
                          "entries": [{**item(), "menu_date": str(i)} for i in range(80)]}}
    install(monkeypatch, payloads)
    identity = service.menu_service._monthly_item_identity_key_from_payload
    calls = []
    def counted(payload):
        calls.append(payload)
        return identity(payload)
    monkeypatch.setattr(service.menu_service, "_monthly_item_identity_key_from_payload", counted)
    expected = original("week")
    assert len(calls) == 8000
    calls.clear()
    assert service._collect_menu_items_for_week("week") == expected
    assert len(calls) == 130
    payloads["month"]["entries"][0]["menu_date"] = "updated"
    assert service._collect_menu_items_for_week("week")[0]["menu_dates"][0] == "updated"
