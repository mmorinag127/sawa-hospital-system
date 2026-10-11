"""Synthetic component public identity parity, not native OCR/auth/live proof."""

from test_modernization_daily_override_atomic import (
    DAY, QUERY, ROOT, paired as paired, clone_selected_input,
)
from test_modernization_real_bagging_confirmation import owned_bagging as owned_bagging
from test_modernization_workflow_surface_parity import surface as surface, database_snapshot
from src.services import order_service as orders, output_builder as output


def test_public_daily_group_selects_same_accepted_category_and_override_receipt(paired):
    first, second, client = paired
    before = database_snapshot()
    saved = [fixture.saved() for fixture in (first, second)]
    sources = {path: path.read_bytes() for path in (first.pdf, first.source, first.master_path)}
    accepted = []
    for fixture in (first, second):
        with fixture.session_scope() as session:
            order = orders.get_order_by_id(fixture.order_id, read_session=session)
            candidate = output._accepted_output_candidate(order, write_session=session)
            assert [line["quantity_original"] for line in candidate["lines"]] == [7, 0]
            lines = output.build_order_lines_for_outputs(order, accepted_candidate=candidate)
            selected = [line for line in lines if line["menu_name"] == "選択献立0"]
            assert len(selected) == 1
            assert selected[0]["menu_category"] == "副菜1"
            accepted.append(selected[0])
    response = client.get("/orders/daily-bags", params={"date": DAY.isoformat()})
    assert response.status_code == 200, response.text
    groups = response.json()["groups"]
    assert len(groups) == 1
    group = groups[0]
    # The UI sends these public group keys, not the raw monthly category.
    query = {"date": DAY.isoformat(), **{
        key: group[key] for key in ("daypart", "menu_name", "menu_category")
    }}
    assert query == QUERY
    assert all((line["daypart"], line["menu_name"], line["menu_category"]) ==
               (query["daypart"], query["menu_name"], query["menu_category"]) for line in accepted)
    response = client.get(ROOT, params=query)
    assert response.status_code == 200, response.text
    editor = response.json()
    assert len(editor["rows"]) == 1
    assert editor["rows"][0]["total_quantity"] == 14
    receipt = editor["original_receipt"]
    assert set(receipt["order_ids"]) == {first.order_id, second.order_id}
    assert {key: receipt[key] for key in query} == query
    stale = client.get(ROOT, params={**query, "menu_category": "主菜"})
    assert stale.status_code == 200, stale.text
    assert stale.json()["rows"] == []
    assert database_snapshot() == before
    assert [fixture.saved() for fixture in (first, second)] == saved
    assert all(path.read_bytes() == value for path, value in sources.items())
