"""Owned synthetic SQLite/PDF/dated JSON -> mounted override commands.

No lifespan/native OCR/auth/provider/live proof; SQLite is not PG phantom-lock
proof. Capture, ownership fence and rebuild stay real. The rollback hook runs
the actual rebuild before raising, never supplies a successful result.
"""

from copy import copy, deepcopy
from datetime import date

import pytest
from sqlalchemy import select

from test_modernization_real_bagging_confirmation import owned_bagging
from test_modernization_real_daily_delivery_html import confirm
from test_modernization_workflow_surface_parity import database_snapshot, surface
from src.models.document import OrderDocument
from src.models.order import Order, OrderLine
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.output import Bag, DailyOutputPortionOverride
from src.models.uploaded_pdf import UploadedPdf
from src.services import order_service as orders
from src.services import output_builder as output


DAY = date(2026, 3, 1)
QUERY = {"date": DAY.isoformat(), "daypart": "朝", "menu_name": "選択献立0", "menu_category": "主菜"}
ROOT = "/orders/daily-output-overrides"


def clone_selected_input(fixture):
    """Explicit second synthetic order, same verified F/week/PDF, new identities.

    Copy only pre-confirmation inputs; no confirmed artifacts or fabricated
    output receipts. The real version recorder and confirmation create those.
    """
    other = copy(fixture)
    other.order_id = "ORD-SECOND-SYNTHETIC"
    other.evidence_id = "OCR-SECOND-SYNTHETIC"
    other.draft_id = "DRAFT-SECOND-SYNTHETIC"
    identities = {fixture.order_id: other.order_id, fixture.evidence_id: other.evidence_id,
                  fixture.draft_id: other.draft_id, "UNIT-DOC": "SECOND-DOC", "UNIT-PDF": "SECOND-PDF"}

    def replace(value):
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        return identities.get(value, value) if isinstance(value, str) else deepcopy(value)

    with fixture.session_scope() as session:
        for model, identifier in ((Order, fixture.order_id), (OrderDocument, "UNIT-DOC"),
                                  (UploadedPdf, "UNIT-PDF"), (OrderOcrEvidenceRun, fixture.evidence_id),
                                  (OrderSheetDraft, fixture.draft_id), (OrderWorkflowState, fixture.order_id)):
            original = session.get(model, identifier)
            assert original is not None
            values = {column.name: replace(getattr(original, column.name)) for column in model.__table__.columns}
            session.execute(model.__table__.insert().values(**values))
        session.expire_all()
        orders._record_current_order_version(session, session.get(Order, other.order_id),
                                             session.get(OrderDocument, "SECOND-DOC"))
    original_saved = fixture.saved()
    cloned_saved = other.saved()
    assert (fixture.order_id, fixture.evidence_id, fixture.draft_id) == (
        "ORD-SAVED-SELECTED", "OCR-SELECTED", "DRAFT-SELECTED")
    assert (other.order_id, other.evidence_id, other.draft_id) == (
        "ORD-SECOND-SYNTHETIC", "OCR-SECOND-SYNTHETIC", "DRAFT-SECOND-SYNTHETIC")
    assert cloned_saved["base_evidence_run_id"] == other.evidence_id
    assert original_saved["base_evidence_run_id"] == fixture.evidence_id
    assert cloned_saved["fields"] == original_saved["fields"]
    assert cloned_saved["rows"] == original_saved["rows"]
    assert [row[4] for row in cloned_saved["rows"]] == ["7", "", "", "0"]
    assert cloned_saved == replace(original_saved)
    return other


@pytest.fixture
def paired(owned_bagging, surface):
    first = owned_bagging
    second = clone_selected_input(first)
    confirm(first)
    confirm(second)
    return first, second, surface[1]


def editor(client):
    response = client.get(ROOT, params=QUERY)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["rows"] and value["original_receipt"]["order_ids"]
    return value


def body(value, *, bulk=False):
    result = {**QUERY, "unit_type": "g", "qty_per_serving": 200,
              "original_receipt": deepcopy(value["original_receipt"])}
    if not bulk:
        result.update(facility_id="F", diet_type="regular")
    return result


def bags(fixture, identifier):
    with fixture.session_scope() as session:
        return [(row.menu_name, row.quantity) for row in session.scalars(
            select(Bag).where(Bag.order_id == identifier).order_by(Bag.menu_name, Bag.id))]


@pytest.mark.parametrize("bulk", [False, True])
def test_real_single_bulk_and_delete_restore_original_bag_quantities(paired, bulk):
    first, second, client = paired
    originals = {}
    for fixture in (first, second):
        with fixture.session_scope() as session:
            order = orders.get_order_by_id(fixture.order_id, read_session=session)
            candidate = output._accepted_output_candidate(order, write_session=session)
            assert [line["quantity_original"] for line in candidate["lines"]] == [7, 0]
            original_rows = output.build_bag_rows_for_outputs(order, accepted_candidate=candidate)
        assert len(original_rows) == 1
        original = original_rows[0]
        assert original["menu_name"] == "選択献立0"
        assert original["quantity"] == 7
        assert original["menu_qty_per_serving"] == 100
        assert original["menu_unit_type"] == "g"
        assert original["quantity"] * original["menu_qty_per_serving"] == 700
        originals[fixture.order_id] = [(row["menu_name"], row["quantity"]) for row in original_rows]

    def public_original_amounts():
        response = client.get("/orders/daily-bags", params={"date": DAY.isoformat()})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["order_count"] == 2
        assert len(payload["groups"]) == 1
        group = payload["groups"][0]
        assert group["menu_name"] == "選択献立0"
        assert len(group["diet_groups"]) == 1
        diet = group["diet_groups"][0]
        assert diet["diet_type"] == "regular"
        assert diet["total_quantity"] == 14
        assert diet["total_amounts"] == {"g": 1400}
        refs = [ref for bag_type in diet["bag_type_groups"]
                for breakdown in bag_type["breakdowns"] for ref in breakdown["order_refs"]]
        assert sorted((ref["order_id"], ref["quantity"]) for ref in refs) == sorted(
            (fixture.order_id, 7) for fixture in (first, second))
        return diet["total_quantity"], diet["total_amounts"]

    original_public_amounts = public_original_amounts()
    saved = {f.order_id: f.saved() for f in (first, second)}
    before = database_snapshot()
    original_files = {path: path.read_bytes() for path in (first.pdf, first.source, first.master_path)}
    response = client.post(ROOT + ("/upsert-bulk" if bulk else "/upsert"), json=body(editor(client), bulk=bulk))
    assert response.status_code == 200, response.text
    assert set(response.json()["affected_order_ids"]) == {first.order_id, second.order_id}
    for fixture in (first, second):
        assert sum(qty for name, qty in bags(fixture, fixture.order_id) if name == "選択献立0") == 7
        assert sorted(qty for name, qty in bags(fixture, fixture.order_id) if name == "選択献立0") == [2, 5]
        assert fixture.saved() == saved[fixture.order_id]
    after = database_snapshot()
    assert after["order_lines"] == before["order_lines"]
    for table, rows in before.items():
        foreign = lambda row: row.get("order_id") == "ORD-FOREIGN" or row.get("id") == "ORD-FOREIGN"
        assert [row for row in after[table] if foreign(row)] == [row for row in rows if foreign(row)]
    assert all(path.read_bytes() == content for path, content in original_files.items())
    # The zero-menu scope is not the overridden menu.
    assert [row for row in after["bags"] if row["menu_name"] != QUERY["menu_name"]] == [
        row for row in before["bags"] if row["menu_name"] != QUERY["menu_name"]]
    with first.session_scope() as session:
        overrides = session.scalars(select(DailyOutputPortionOverride)).all()
        assert len(overrides) == 1 and overrides[0].qty_per_serving == 200
        identity = overrides[0].id
    fresh = editor(client)
    deleted = client.request("DELETE", ROOT + "/" + identity,
                             json={"original_receipt": fresh["original_receipt"]})
    assert deleted.status_code == 200, deleted.text
    for fixture in (first, second):
        assert bags(fixture, fixture.order_id) == originals[fixture.order_id]
        assert fixture.saved() == saved[fixture.order_id]
    assert public_original_amounts() == original_public_amounts
    with first.session_scope() as session:
        assert session.scalars(select(DailyOutputPortionOverride)).all() == []


@pytest.mark.parametrize("fault", ["missing", "foreign", "tampered", "added", "removed", "quantity", "version", "override"])
def test_displayed_receipt_conflicts_preserve_entire_database(paired, fault):
    first, second, client = paired
    payload = body(editor(client))
    if fault == "missing":
        payload.pop("original_receipt")
    elif fault == "foreign":
        payload["original_receipt"]["order_ids"] = ["ORD-FOREIGN"]
    elif fault == "tampered":
        payload["original_receipt"]["digest"] = "0" * 64
    elif fault == "added":
        # Existing synthetic foreign order gains real line-date membership.
        with first.session_scope() as session:
            session.add(OrderLine(id="ADDED-SCOPE-LINE", order_id="ORD-FOREIGN", date=DAY,
                                  daypart="朝", menu_name="選択献立0", diet_type="regular", area_id="X",
                                  quantity_original=7))
    elif fault == "removed":
        with first.session_scope() as session:
            for line in session.scalars(select(OrderLine).where(OrderLine.order_id == second.order_id)):
                session.delete(line)
    elif fault == "quantity":
        with first.session_scope() as session:
            draft = session.get(OrderSheetDraft, first.draft_id)
            value = deepcopy(draft.draft_sheet_json)
            value["rows"][0][4] = "9"
            draft.draft_sheet_json = value
    elif fault == "version":
        with first.session_scope() as session:
            session.get(Order, first.order_id).current_document_id = None
    else:
        changed = client.post(ROOT + "/upsert", json=deepcopy(payload))
        assert changed.status_code == 200, changed.text
    before = database_snapshot()
    response = client.post(ROOT + "/upsert", json=payload)
    assert response.status_code == 409, response.text
    assert database_snapshot() == before


@pytest.mark.parametrize("command", ["single", "bulk", "delete"])
def test_second_real_rebuild_failure_rolls_back_all_prior_writes(paired, monkeypatch, command):
    first, second, client = paired
    identity = None
    if command == "delete":
        initial = client.post(ROOT + "/upsert", json=body(editor(client)))
        assert initial.status_code == 200, initial.text
        identity = initial.json()["override"]["id"]
    value = editor(client)
    payload = body(value, bulk=command == "bulk")
    real = output.rebuild_bags
    visited = []

    def rebuild_then_fault(identifier, **kwargs):
        result = real(identifier, **kwargs)
        visited.append(identifier)
        if len(visited) == 2:
            raise ValueError("synthetic fault after second actual rebuild")
        return result

    monkeypatch.setattr(output, "rebuild_bags", rebuild_then_fault)
    before = database_snapshot()
    if command == "delete":
        response = client.request("DELETE", ROOT + "/" + identity,
                                  json={"original_receipt": value["original_receipt"]})
    else:
        response = client.post(ROOT + ("/upsert-bulk" if command == "bulk" else "/upsert"), json=payload)
    assert response.status_code == 409, response.text
    assert set(visited) == {first.order_id, second.order_id}
    assert database_snapshot() == before


@pytest.mark.parametrize("command", ["bulk", "delete"])
@pytest.mark.parametrize("fault", ["missing", "tampered", "foreign"])
def test_sibling_commands_require_their_displayed_receipt(paired, command, fault):
    first, second, client = paired
    initial = client.post(ROOT + "/upsert", json=body(editor(client)))
    assert initial.status_code == 200, initial.text
    identity = initial.json()["override"]["id"]
    value = editor(client)
    payload = body(value, bulk=True) if command == "bulk" else {"original_receipt": value["original_receipt"]}
    if fault == "missing":
        payload.pop("original_receipt")
    elif fault == "tampered":
        payload["original_receipt"]["digest"] = "0" * 64
    else:
        payload["original_receipt"]["order_ids"] = ["ORD-FOREIGN"]
    before = database_snapshot()
    response = (client.post(ROOT + "/upsert-bulk", json=payload) if command == "bulk" else
                client.request("DELETE", ROOT + "/" + identity, json=payload))
    assert response.status_code == 409, response.text
    assert database_snapshot() == before
