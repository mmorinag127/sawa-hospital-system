"""Real accepted producer -> final confirmation -> mounted daily/child HTML.

AUTH_DISABLED, no TestClient lifespan, owned SQLite and manually seeded OCR:
not native recognition, startup, authentication, public UI, PG or live proof.
No producer/reader/renderer success doubles. Root-name divergence changes only
owned synthetic JSON through the unchanged fixture's write_master method.
"""

from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
from html.parser import HTMLParser
import json
import re

import pytest

from test_modernization_real_bagging_confirmation import actual_bagging, owned_bagging, revision
from test_modernization_workflow_surface_parity import database_snapshot, surface as surface
from src.models.document import OrderDocument
from src.models.order import Order
from src.services import master_order_form_template_service as master
from src.services import order_service as orders
from src.services import order_workflow_v2_service as workflow


class HtmlContents(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.scripts = []
        self.cells = {}
        self.active = None
        self.script = None
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.script = []
        if "data-edit" in attrs:
            self.active = attrs["data-edit"]
            assert self.active not in self.cells
            self.cells[self.active] = ""

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        if self.active is not None:
            self.cells[self.active] += data

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            self.scripts.append("".join(self.script))
            self.script = None
        if tag in {"td", "th", "div"}:
            self.active = None


def confirm(fixture):
    bagged = actual_bagging(fixture)
    result, error = workflow.final_confirm(
        fixture.order_id, expected_revision=revision(fixture),
        confirmed_by="synthetic-daily-delivery-operator",
    )
    assert error is None, error
    assert result is not None
    candidate = bagged["bagging_result"]["materialization_candidate"]
    assert [(line["quantity_original"]) for line in candidate["lines"]] == [7, 0]
    return candidate


@pytest.fixture
def invoice_order(owned_bagging, surface):
    fixture = owned_bagging
    fixture.authored["invoice_template"] = {
        "include_menu_name": True,
        "columns": [
            {"name": "Date", "source": "date"},
            {"name": "Menu", "source": "menu_name"},
            {"name": "Quantity", "source": "quantity", "diet_type": "regular", "area_id": "X"},
        ],
    }
    fixture.republish()
    with fixture.session_scope() as session:
        orders._record_current_order_version(
            session, session.get(Order, fixture.order_id), session.get(OrderDocument, "UNIT-DOC"),
        )
    candidate = confirm(fixture)
    assert candidate["configured_input"]["facility"]["invoice_template"] == fixture.authored["invoice_template"]
    return fixture, surface[1], candidate


def preservation(fixture):
    return database_snapshot(), deepcopy(fixture.saved()), {
        path: sha256(path.read_bytes()).hexdigest()
        for path in (fixture.pdf, fixture.source, fixture.master_path, master.MASTER_TEMPLATE_PATH)
    }


def assert_preserved(fixture, before):
    database, saved, hashes = before
    assert database_snapshot() == database
    assert fixture.saved() == saved
    assert [row[4] for row in saved["rows"]] == ["7", "", "", "0"]
    assert all(sha256(path.read_bytes()).hexdigest() == digest for path, digest in hashes.items())


def received_html(response, fixture, name, capsys):
    assert response.status_code == 200, response.text
    body = response.content
    assert body and int(response.headers["content-length"]) == len(body)
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.text == body.decode("utf-8")
    path = fixture.root / "http-received" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(body)
    assert path.read_bytes() == body
    with capsys.disabled():
        print(f"http-received artifact={path} sha256={sha256(body).hexdigest()}")
    return HtmlContents(response.text)


def test_real_daily_shell_and_child_table_preserve_accepted_quantities(invoice_order, capsys):
    fixture, client, candidate = invoice_order
    day = date.fromisoformat(str(candidate["lines"][0]["date"])[:10])
    before = preservation(fixture)
    response = client.get("/outputs/daily-delivery-notes/html", params={"date": day.isoformat()})
    parsed = received_html(response, fixture, "daily.html", capsys)
    scripts = "\n".join(parsed.scripts)
    matches = re.findall(r"const orderIds = (\[[^;]*\]);", scripts)
    assert len(matches) == 1
    assert json.loads(matches[0]) == [fixture.order_id]
    assert f"&date={day.isoformat()}" in scripts
    assert response.headers["x-daily-delivery-total-orders"] == "1"
    assert_preserved(fixture, before)
    child = received_html(client.get("/outputs/delivery-notes/html", params={
        "order_id": fixture.order_id, "date": day.isoformat(),
    }), fixture, "child.html", capsys)
    assert [child.cells[f"h-{index}"] for index in range(6)] == [
        "日付", "区分", "献立区分", "メニュー名", "常食", "備考欄",
    ]
    rows = {
        value: child.cells[key.replace("-c-3", "-c-4")]
        for key, value in child.cells.items() if key.startswith("r-") and key.endswith("-c-3")
    }
    assert rows["選択献立0"] == "7"
    # Existing zero_as_empty policy: explicit saved zero remains zero in the
    # receipt, but its template menu row has a blank rendered quantity.
    assert candidate["configured_input"]["policy"].get("quantity_rules", {}).get("zero_as_empty", True) is True
    assert rows["選択献立3"] == ""
    assert_preserved(fixture, before)
    empty = client.get("/outputs/daily-delivery-notes/html", params={"date": (day + timedelta(days=7)).isoformat()})
    assert empty.status_code == 400
    assert empty.json() == {"detail": "対象日の納品書出力対象がありません"}
    assert_preserved(fixture, before)


@pytest.mark.parametrize("invoice_fault", ["missing", "empty-columns"])
def test_invalid_accepted_invoice_stops_child_without_fabricating_template(owned_bagging, surface, invoice_fault):
    fixture = owned_bagging
    if invoice_fault == "empty-columns":
        fixture.authored["invoice_template"] = {"columns": []}
    else:
        fixture.authored.pop("invoice_template", None)
    fixture.republish()
    with fixture.session_scope() as session:
        orders._record_current_order_version(
            session, session.get(Order, fixture.order_id), session.get(OrderDocument, "UNIT-DOC"),
        )
    candidate = confirm(fixture)
    if invoice_fault == "missing":
        assert candidate["configured_input"]["facility"].get("invoice_template") is None
    else:
        assert candidate["configured_input"]["facility"]["invoice_template"] == {"columns": []}
    day = date.fromisoformat(str(candidate["lines"][0]["date"])[:10])
    before = preservation(fixture)
    response = surface[1].get("/outputs/delivery-notes/html", params={
        "order_id": fixture.order_id, "date": day.isoformat(),
    })
    assert response.status_code == 400
    assert response.json() == {"detail": "accepted_invoice_template_required"}
    assert_preserved(fixture, before)


def test_child_facility_name_remains_accepted_after_unversioned_root_rename(invoice_order, capsys):
    fixture, client, candidate = invoice_order
    original = fixture.master_path.read_bytes()
    versions = deepcopy(fixture.master["facilities"][0]["versions"])
    day = date.fromisoformat(str(candidate["lines"][0]["date"])[:10])
    try:
        fixture.master["facilities"][0]["facility_name"] = "Different unversioned root name"
        fixture.write_master()
        assert json.loads(fixture.master_path.read_bytes())["facilities"][0]["versions"] == versions
        before = preservation(fixture)
        parsed = received_html(client.get("/outputs/delivery-notes/html", params={
            "order_id": fixture.order_id, "date": day.isoformat(),
        }), fixture, "child-after-root-rename.html", capsys)
        assert_preserved(fixture, before)
        assert parsed.cells["facility-name"] == candidate["configured_input"]["facility"]["facility_name"]
    finally:
        fixture.master_path.write_bytes(original)
    assert fixture.master_path.read_bytes() == original
