"""Actual mounted daily-bags reader on real confirmed synthetic receipts.

Owned SQLite/AUTH_DISABLED/no lifespan/manual OCR: not native/auth/PG/live.
No read, grouping or accepted-candidate success doubles.
"""

from datetime import date, timedelta

from test_modernization_real_bagging_confirmation import owned_bagging as owned_bagging
from test_modernization_workflow_surface_parity import surface as surface
from test_modernization_real_daily_delivery_html import confirm, preservation, assert_preserved


def test_real_daily_bag_groups_preserve_accepted_amounts_and_source_references(owned_bagging, surface):
    fixture = owned_bagging
    candidate = confirm(fixture)
    assert [line["quantity_original"] for line in candidate["lines"]] == [7, 0]
    day = date.fromisoformat(str(candidate["lines"][0]["date"])[:10])
    before = preservation(fixture)
    response = surface[1].get("/orders/daily-bags", params={"date": day.isoformat()})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["date"] == day.isoformat() and payload["order_count"] == 1
    assert payload["status"] is None and payload["facility_id"] is None
    assert len(payload["groups"]) == 1
    group = payload["groups"][0]
    assert group["menu_name"] == "選択献立0" and group["daypart_key"] == "朝"
    assert len(group["diet_groups"]) == 1
    diet = group["diet_groups"][0]
    assert diet["diet_type"] == "regular" and diet["total_quantity"] == 7
    assert diet["total_amounts"] == {"g": 700}
    assert len(diet["bag_type_groups"]) == 1
    bags = diet["bag_type_groups"][0]
    assert bags["bag_count"] == 1 and bags["total_quantity"] == 7
    assert bags["total_amounts"] == {"g": 700}
    assert len(bags["breakdowns"]) == 1
    breakdown = bags["breakdowns"][0]
    assert breakdown["count"] == 1 and len(breakdown["order_refs"]) == 1
    ref = breakdown["order_refs"][0]
    assert ref["order_id"] == fixture.order_id and ref["area_id"] == "X" and ref["quantity"] == 7
    facility = candidate["configured_input"]["facility"]
    assert ref["facility_label"] == f"{facility['facility_name']} ({facility['facility_id']})"
    assert ref["source_refs"]
    assert sum(item["quantity"] for item in ref["source_refs"]) == 7
    for source in ref["source_refs"]:
        assert source["source_row_index"] is not None and source["source_field"]
    assert_preserved(fixture, before)
    for query in ({"date": (day + timedelta(days=7)).isoformat()},
                  {"date": day.isoformat(), "facility": "FOREIGN-SYNTHETIC-FACILITY"}):
        empty = surface[1].get("/orders/daily-bags", params=query)
        assert empty.status_code == 200, empty.text
        assert empty.json()["order_count"] == 0 and empty.json()["groups"] == []
        assert_preserved(fixture, before)
