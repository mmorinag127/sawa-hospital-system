"""Real producer artifacts consumed by the history service and mounted HTTP route.

The unchanged fixture manually seeds synthetic OCR/saved quantities. SQLite and
AUTH_DISABLED are component boundaries, not native OCR, production auth, formal
PG or live proof. Direct DB corruption below is not a public operator action.
History 'validated' means existing artifact metadata/hash/source validation,
not an additional full bagging proof contract. No producer or validator is mocked.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from hashlib import sha256

import pytest
from sqlalchemy import event, select

from test_modernization_real_bagging_confirmation import actual_bagging, owned_bagging, revision
from test_modernization_workflow_surface_parity import database_snapshot, surface as surface
from src.models.order_output_artifact import OrderOutputBundle
from src.services import order_output_artifact_service as artifacts
from src.services import output_history_service as history


@pytest.fixture
def real_history(owned_bagging, surface, monkeypatch):
    fixture = owned_bagging
    same_fixture, client = surface
    assert same_fixture is fixture
    from src.api import output_history

    # Imported SessionLocal is a connection binding, not a successful result stub.
    # Keep the actual route/service/queries on the fixture's real owned database.
    monkeypatch.setattr(output_history, "SessionLocal", fixture.sessions)
    return fixture, client


def capture_inputs(fixture):
    files = (fixture.pdf, fixture.source, fixture.master_path)
    from src.services import master_order_form_template_service as master

    files = (*files, master.MASTER_TEMPLATE_PATH)
    return fixture.saved(), {path: sha256(path.read_bytes()).hexdigest() for path in files}


def assert_inputs(fixture, inputs):
    saved, hashes = inputs
    assert fixture.saved() == saved
    assert [row[4] for row in saved["rows"]] == ["7", "", "", "0"]
    assert {path: sha256(path.read_bytes()).hexdigest() for path in hashes} == hashes


@contextmanager
def readonly_history(fixture):
    before = database_snapshot()
    statements = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        sql = statement.strip()
        statements.append(sql)
        assert sql == "BEGIN" or sql.upper().startswith(("SELECT", "PRAGMA")), sql

    event.listen(fixture.engine, "before_cursor_execute", observe)
    try:
        yield
    finally:
        event.remove(fixture.engine, "before_cursor_execute", observe)
        assert statements
        assert database_snapshot() == before


def service_page(fixture, *, expected=None, page=1, limit=100):
    expected = revision(fixture) if expected is None else expected
    with fixture.session_scope() as session:
        return history.list_output_history(
            session, order_id=fixture.order_id, expected_workflow_revision=expected,
            page=page, limit=limit,
        )


def http_page(fixture, client, *, expected=None, page=1, limit=100):
    return client.get("/outputs/history", params={
        "order_id": fixture.order_id,
        "expected_workflow_revision": revision(fixture) if expected is None else expected,
        "page": page, "limit": limit,
    })


def assert_entry(entry, fixture, produced, *, current, validation="validated"):
    bundle = produced["output_bundle"]
    assert set(entry) == {
        "output_bundle_id", "order_id", "source_saved_sheet_id",
        "source_bagging_result_id", "template_version_id", "created_at",
        "current", "validation", "blocked_reasons",
    }
    assert entry["output_bundle_id"] == bundle["output_bundle_id"]
    assert entry["order_id"] == fixture.order_id
    assert entry["source_saved_sheet_id"] == fixture.draft_id
    assert entry["source_bagging_result_id"] == produced["bagging_result"]["bagging_result_id"]
    assert entry["template_version_id"] == fixture.projection_id
    assert datetime.fromisoformat(entry["created_at"]).utcoffset() is not None
    assert entry["current"] is current
    assert entry["validation"] == validation
    assert entry["blocked_reasons"] == ([] if validation == "validated" else ["output_bundle_artifact_invalid"])


def assert_history_error(fixture, client, error, *, expected=None):
    with readonly_history(fixture):
        with pytest.raises(ValueError, match=f"^{error}$"):
            service_page(fixture, expected=expected)
        response = http_page(fixture, client, expected=expected)
        assert response.status_code == 409, response.text
        assert response.json() == {"detail": error}


def test_real_producer_history_http_and_service_preserve_all_inputs(real_history):
    fixture, client = real_history
    inputs = capture_inputs(fixture)
    produced = actual_bagging(fixture)
    produced_before = deepcopy(produced)
    with readonly_history(fixture):
        result = service_page(fixture)
        response = http_page(fixture, client)
        assert response.status_code == 200, response.text
        assert response.json() == result
        assert set(result) == {"order_id", "workflow_revision", "current_output_bundle_id",
                               "page", "limit", "total", "entries"}
        assert result["order_id"] == fixture.order_id
        assert result["workflow_revision"] == revision(fixture)
        assert result["current_output_bundle_id"] == produced["output_bundle"]["output_bundle_id"]
        assert (result["total"], result["page"], result["limit"]) == (1, 1, 100)
        assert len(result["entries"]) == 1
        assert_entry(result["entries"][0], fixture, produced, current=True)
    assert produced == produced_before
    assert_inputs(fixture, inputs)


def test_real_regeneration_preserves_prior_and_current_pagination(real_history):
    fixture, client = real_history
    inputs = capture_inputs(fixture)
    first = actual_bagging(fixture)
    first_revision = revision(fixture)
    second = actual_bagging(fixture)
    assert revision(fixture) == first_revision + 1
    first_id, second_id = (item["output_bundle"]["output_bundle_id"] for item in (first, second))
    assert first_id != second_id
    with fixture.session_scope() as session:
        assert artifacts.load_output_bundle_payload(session, first_id, expected_order_id=fixture.order_id) == first["output_bundle"]
        assert artifacts.load_output_bundle_payload(session, second_id, expected_order_id=fixture.order_id) == second["output_bundle"]
        ordered_ids = session.scalars(select(OrderOutputBundle.id).where(
            OrderOutputBundle.order_id == fixture.order_id,
        ).order_by(OrderOutputBundle.created_at.desc(), OrderOutputBundle.id.desc())).all()
    with readonly_history(fixture):
        full = service_page(fixture)
        assert full["total"] == 2
        assert full["current_output_bundle_id"] == second_id
        assert [entry["output_bundle_id"] for entry in full["entries"]] == ordered_ids
        by_id = {entry["output_bundle_id"]: entry for entry in full["entries"]}
        assert_entry(by_id[first_id], fixture, first, current=False)
        assert_entry(by_id[second_id], fixture, second, current=True)
        for page in (1, 2, 3):
            result = service_page(fixture, page=page, limit=1)
            response = http_page(fixture, client, page=page, limit=1)
            assert response.status_code == 200, response.text
            assert response.json() == result
            assert (result["page"], result["limit"], result["total"]) == (page, 1, 2)
            assert result["current_output_bundle_id"] == second_id
            assert result["entries"] == full["entries"][page - 1:page]
    assert_inputs(fixture, inputs)


def test_real_history_rejects_stale_revision_without_writes(real_history):
    fixture, client = real_history
    inputs = capture_inputs(fixture)
    actual_bagging(fixture)
    stale = revision(fixture)
    actual_bagging(fixture)
    assert_history_error(fixture, client, "output_history_workflow_revision_mismatch", expected=stale)
    assert_inputs(fixture, inputs)


def corrupt_bundle(fixture, bundle_id, fault):
    """Component-only persisted fault; never an operator save/registration."""
    with fixture.session_scope() as session:
        row = session.get(OrderOutputBundle, bundle_id)
        payload = deepcopy(row.payload_json)
        if fault == "missing-materialization-digest":
            payload.pop("materialization_digest")
            row.materialization_digest = None
        elif fault == "payload-hash":
            row.payload_digest = "0" * 64
            return
        elif fault == "foreign-owner":
            # Existing FK target from the unchanged owned fixture, not a new order.
            row.order_id = "ORD-FOREIGN"
            payload["order_id"] = "ORD-FOREIGN"
        elif fault == "source-sheet":
            payload["source_saved_sheet_id"] = "DRAFT-LATEST-UNSELECTED"
        else:
            raise AssertionError(fault)
        row.payload_json = payload
        row.payload_digest = artifacts.payload_digest(payload)


@pytest.mark.parametrize("fault", [
    "missing-materialization-digest", "payload-hash", "foreign-owner", "source-sheet",
])
def test_invalid_current_never_falls_back_to_prior_valid_artifact(real_history, fault):
    fixture, client = real_history
    inputs = capture_inputs(fixture)
    first = actual_bagging(fixture)
    second = actual_bagging(fixture)
    corrupt_bundle(fixture, second["output_bundle"]["output_bundle_id"], fault)
    with fixture.session_scope() as session:
        assert artifacts.load_output_bundle_payload(
            session, first["output_bundle"]["output_bundle_id"], expected_order_id=fixture.order_id,
        ) == first["output_bundle"]
    assert_history_error(fixture, client, "output_history_current_artifact_invalid")
    assert_inputs(fixture, inputs)


@pytest.mark.parametrize("fault", ["missing-materialization-digest", "payload-hash"])
def test_corrupt_historical_artifact_remains_blocked_row(real_history, fault):
    fixture, client = real_history
    inputs = capture_inputs(fixture)
    first = actual_bagging(fixture)
    second = actual_bagging(fixture)
    first_id = first["output_bundle"]["output_bundle_id"]
    second_id = second["output_bundle"]["output_bundle_id"]
    corrupt_bundle(fixture, first_id, fault)
    with readonly_history(fixture):
        result = service_page(fixture)
        response = http_page(fixture, client)
        assert response.status_code == 200, response.text
        assert response.json() == result
        assert result["total"] == 2
        assert result["current_output_bundle_id"] == second_id
        assert len(result["entries"]) == 2
        by_id = {entry["output_bundle_id"]: entry for entry in result["entries"]}
        assert_entry(by_id[first_id], fixture, first, current=False, validation="blocked")
        assert_entry(by_id[second_id], fixture, second, current=True)
    assert_inputs(fixture, inputs)
