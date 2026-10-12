"""Owned SQLite receipts, not native OCR/auth/live or legacy HTTP-route proof.

Normal bagging, readers, writers, confirmation and ownership fences are real.
Persisted damage below is deliberate DB fault injection, not a normal-save bug.
Only the named digest/builder exception tests inject component-boundary faults;
they do not replace the real producer baseline. Legacy review routes are 410.
"""

from copy import deepcopy

import pytest
from sqlalchemy import select

from test_modernization_real_bagging_confirmation import (
    actual_bagging, assert_no_confirmation, owned_bagging, revision,
)
from test_modernization_workflow_surface_parity import database_snapshot
from test_modernization_workflow_surface_parity import surface as surface
from src.models.order import Order, OrderLine
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_output_artifact import OrderBaggingResult, OrderOutputBundle
from src.models.order_workflow_state import OrderWorkflowState
from src.services import order_output_artifact_service as artifacts
from src.services import order_workflow_v2_service as workflow
from src.services import output_builder


def _originals(fixture):
    snapshot = database_snapshot()
    return {
        "files": {path: path.read_bytes() for path in (fixture.master_path, fixture.source, fixture.pdf)},
        "sheet": fixture.saved(),
        "tables": {name: snapshot[name] for name in ("order_sheet_drafts", "order_ocr_evidence_runs")},
    }


def _assert_originals(fixture, original):
    assert {path: path.read_bytes() for path in original["files"]} == original["files"]
    assert fixture.saved() == original["sheet"]
    assert [row[4] for row in fixture.saved()["rows"]] == ["7", "", "", "0"]
    snapshot = database_snapshot()
    assert {name: snapshot[name] for name in original["tables"]} == original["tables"]


@pytest.fixture
def bagged_receipt(owned_bagging):
    fixture = owned_bagging
    original = _originals(fixture)
    result = actual_bagging(fixture)
    _assert_originals(fixture, original)
    return fixture, result, original


def _bundle(fixture, payload):
    with fixture.session_scope() as session:
        return workflow._build_output_bundle_payload(
            order=session.get(Order, fixture.order_id), bagging_result=payload,
        )


def _writer_rejects(fixture, payload, *, output=False):
    before = database_snapshot()
    submitted = deepcopy(payload)
    key = "output_bundle_id" if output else "bagging_result_id"
    submitted[key] += "-invalid-new"
    original = deepcopy(submitted)
    writer = artifacts.save_output_bundle_artifact if output else artifacts.save_bagging_result_artifact
    with pytest.raises(ValueError) as caught:
        with fixture.session_scope() as session:
            writer(session, payload=submitted)
    assert str(caught.value) == ("output_bundle_artifact_invalid" if output else "bagging_result_artifact_invalid")
    assert submitted == original
    assert database_snapshot() == before


def _persist_fault(fixture, model, artifact_id, payload, *, bad_hash=False):
    # Preserve row identity and every other table. Only the explicit fault is
    # persisted; the outer hash is valid except in the hash-guard case.
    before = database_snapshot()
    with fixture.session_scope() as session:
        row = session.get(model, artifact_id)
        row.payload_json = deepcopy(payload)
        row.payload_digest = "wrong-outer-hash" if bad_hash else artifacts.payload_digest(payload)
    after = database_snapshot()
    table = model.__tablename__
    assert after[table] != before[table]
    assert len(after[table]) == len(before[table])
    assert {key: value for key, value in after.items() if key != table} == {
        key: value for key, value in before.items() if key != table
    }


def test_real_producer_readers_and_confirmation_preserve_full_receipt_and_seven_zero(bagged_receipt):
    fixture, result, original = bagged_receipt
    bag = result["bagging_result"]
    output = result["output_bundle"]
    receipts = deepcopy(result)
    candidate = deepcopy(bag["materialization_candidate"])
    before = database_snapshot()
    assert artifacts.validate_bagging_receipt(bag) == bag["materialization_digest"]
    assert bag["materialization_digest"] == workflow._canonical_payload_digest(candidate)
    assert output["materialization_digest"] == bag["materialization_digest"]
    with fixture.session_scope() as session:
        assert artifacts.load_bagging_result_payload(session, bag["bagging_result_id"], expected_order_id=fixture.order_id) == bag
        assert artifacts.load_output_bundle_payload(session, output["output_bundle_id"], expected_order_id=fixture.order_id) == output
    assert output_builder._accepted_output_candidate({"id": fixture.order_id}) == candidate
    rebuilt = _bundle(fixture, bag)
    assert rebuilt["materialization_digest"] == bag["materialization_digest"]
    assert rebuilt["source_saved_sheet_id"] == fixture.draft_id
    assert rebuilt["source_ocr_result_id"] == fixture.evidence_id
    assert result == receipts
    assert bag["materialization_candidate"] == candidate
    assert database_snapshot() == before
    assert_no_confirmation(fixture)
    confirmed, error = workflow.final_confirm(fixture.order_id, expected_revision=revision(fixture))
    assert error is None, error
    assert confirmed is not None
    with fixture.session_scope() as session:
        state = session.get(OrderWorkflowState, fixture.order_id)
        snapshot = session.get(OrderConfirmedSnapshot, confirmed["confirmed_snapshot_id"])
        assert state.state == "confirmed" and state.confirmed_snapshot_id == snapshot.id
        assert snapshot.saved_sheet_id == fixture.draft_id
        assert snapshot.snapshot_json["saved_sheet"] == original["sheet"]
        assert snapshot.snapshot_json["bagging_result"] == bag
        assert snapshot.snapshot_json["bagging_result"]["materialization_candidate"] == candidate
        assert snapshot.snapshot_digest == artifacts.payload_digest(snapshot.snapshot_json)
        assert artifacts.confirmed_snapshot_artifact_payloads(session, snapshot, expected_order_id=fixture.order_id) is not None
        lines = session.execute(select(OrderLine).where(OrderLine.order_id == fixture.order_id).order_by(OrderLine.menu_name)).scalars().all()
        assert [(line.menu_name, line.quantity_original, line.quantity_corrected) for line in lines] == [
            ("選択献立0", 7, None), ("選択献立3", 0, None),
        ]
        assert all(line.confirmed_snapshot_id == snapshot.id for line in lines)
    after_confirmation = database_snapshot()
    assert output_builder._accepted_output_candidate({"id": fixture.order_id}) == candidate
    assert database_snapshot() == after_confirmation
    _assert_originals(fixture, original)


@pytest.mark.parametrize("fault", [
    "candidate-missing", "digest-missing", "digest-blank", "digest-mismatch",
    "configured-missing", "facility-missing", "policy-missing",
])
def test_persisted_inner_receipt_fault_stops_every_consumer_and_new_writer(bagged_receipt, fault):
    fixture, result, original = bagged_receipt
    payload = deepcopy(result["bagging_result"])
    if fault == "candidate-missing":
        del payload["materialization_candidate"]
    elif fault == "digest-missing":
        del payload["materialization_digest"]
    elif fault == "digest-blank":
        payload["materialization_digest"] = " \t "
    elif fault == "digest-mismatch":
        payload["materialization_digest"] = "0" * 64
    else:
        candidate = payload["materialization_candidate"]
        if fault == "configured-missing":
            del candidate["configured_input"]
        else:
            del candidate["configured_input"][fault.removesuffix("-missing")]
        payload["materialization_digest"] = workflow._canonical_payload_digest(candidate)
    _persist_fault(fixture, OrderBaggingResult, payload["bagging_result_id"], payload)
    damaged = database_snapshot()
    with fixture.session_scope() as session:
        assert artifacts.load_bagging_result_payload(session, payload["bagging_result_id"], expected_order_id=fixture.order_id) is None
    for command in (workflow.confirm_bagging, workflow.prepare_output_review):
        assert command(fixture.order_id, expected_revision=revision(fixture)) == (None, "bagging_result_required")
        assert database_snapshot() == damaged
    assert workflow.final_confirm(fixture.order_id, expected_revision=revision(fixture)) == (None, "bagging_result_template_mismatch")
    with pytest.raises(ValueError, match="^workflow-v2 output bundle source mismatch$"):
        output_builder._accepted_output_candidate({"id": fixture.order_id})
    _writer_rejects(fixture, payload)
    assert_no_confirmation(fixture)
    assert database_snapshot() == damaged
    _assert_originals(fixture, original)


def test_saved_output_digest_is_required_by_reader_and_writer(bagged_receipt):
    fixture, result, original = bagged_receipt
    payload = deepcopy(result["output_bundle"])
    del payload["materialization_digest"]
    # Match the nullable DB column too, isolating the new nonempty requirement.
    with fixture.session_scope() as session:
        row = session.get(OrderOutputBundle, payload["output_bundle_id"])
        row.materialization_digest = None
        row.payload_json = deepcopy(payload)
        row.payload_digest = artifacts.payload_digest(payload)
    damaged = database_snapshot()
    with fixture.session_scope() as session:
        assert artifacts.load_output_bundle_payload(session, payload["output_bundle_id"], expected_order_id=fixture.order_id) is None
    _writer_rejects(fixture, payload, output=True)
    assert database_snapshot() == damaged
    _assert_originals(fixture, original)


@pytest.mark.parametrize("command_name", ["confirm_bagging", "prepare_output_review"])
@pytest.mark.parametrize("outcome", ["success", "stale_revision", "writer_failure"])
def test_output_review_commands_share_owned_receipt_and_atomic_revision(bagged_receipt, monkeypatch, command_name, outcome):
    fixture, bagged, original = bagged_receipt
    before = database_snapshot()
    current_revision = revision(fixture)
    command = getattr(workflow, command_name)
    if outcome == "writer_failure":
        writer = artifacts.save_output_bundle_artifact
        def fail_after_write(session, *, payload):
            writer(session, payload=payload)
            raise RuntimeError("synthetic output writer failure")
        monkeypatch.setattr(artifacts, "save_output_bundle_artifact", fail_after_write)
        with pytest.raises(RuntimeError, match="^synthetic output writer failure$"):
            command(fixture.order_id, expected_revision=current_revision)
    elif outcome == "stale_revision":
        _, error = command(fixture.order_id, expected_revision=current_revision - 1)
        assert error == workflow.WORKFLOW_REVISION_CONFLICT
    else:
        result, error = command(fixture.order_id, expected_revision=current_revision)
        assert error is None, error
        assert result["workflow"]["state"] == "output_review"
        assert result["workflow"]["revision"] == current_revision + 1
        assert result["bagging_result"] == bagged["bagging_result"]
        assert result["output_bundle"]["materialization_digest"] == bagged["output_bundle"]["materialization_digest"]
        with fixture.session_scope() as session:
            assert artifacts.load_output_bundle_payload(session, result["output_bundle"]["output_bundle_id"], expected_order_id=fixture.order_id) == result["output_bundle"]
    if outcome != "success":
        assert database_snapshot() == before
        assert revision(fixture) == current_revision
    _assert_originals(fixture, original)


@pytest.mark.parametrize("output", [False, True], ids=["bagging", "output"])
@pytest.mark.parametrize("fault", ["owner", "source", "outer-hash"])
def test_real_sql_owner_source_and_outer_hash_guards_remain(bagged_receipt, output, fault):
    fixture, result, original = bagged_receipt
    payload = deepcopy(result["output_bundle" if output else "bagging_result"])
    model = OrderOutputBundle if output else OrderBaggingResult
    reader = artifacts.load_output_bundle_payload if output else artifacts.load_bagging_result_payload
    artifact_id = payload["output_bundle_id" if output else "bagging_result_id"]
    if fault == "owner":
        payload["order_id"] = "foreign-order"
    elif fault == "source":
        payload["source_saved_sheet_id"] = "foreign-sheet"
    _persist_fault(fixture, model, artifact_id, payload, bad_hash=fault == "outer-hash")
    damaged = database_snapshot()
    with fixture.session_scope() as session:
        assert reader(session, artifact_id, expected_order_id=fixture.order_id) is None
    assert database_snapshot() == damaged
    _assert_originals(fixture, original)


@pytest.mark.parametrize("fault,error", [
    ("missing", "bagging_result_materialization_digest_missing"),
    ("mismatch", "bagging_result_materialization_digest_mismatch"),
])
def test_shared_bundle_builder_never_repairs_a_saved_digest(bagged_receipt, fault, error):
    fixture, result, original = bagged_receipt
    payload = deepcopy(result["bagging_result"])
    if fault == "missing":
        del payload["materialization_digest"]
    else:
        payload["materialization_digest"] = "0" * 64
    unchanged = deepcopy(payload)
    before = database_snapshot()
    with pytest.raises(artifacts.BaggingReceiptInvalid) as caught:
        _bundle(fixture, payload)
    assert str(caught.value) == error
    assert payload == unchanged
    assert database_snapshot() == before
    _assert_originals(fixture, original)


@pytest.mark.parametrize("value", [object(), float("nan")], ids=["unsupported-type", "nonfinite"])
def test_real_canonical_serialization_failure_is_typed(bagged_receipt, value):
    fixture, result, original = bagged_receipt
    payload = deepcopy(result["bagging_result"])
    payload["materialization_candidate"]["unknown_serialization_fault"] = value
    before = database_snapshot()
    for call in (lambda: artifacts.validate_bagging_receipt(payload), lambda: _bundle(fixture, payload)):
        with pytest.raises(artifacts.BaggingReceiptInvalid) as caught:
            call()
        assert str(caught.value) == "bagging_result_materialization_invalid"
        assert isinstance(caught.value.__cause__, (TypeError, ValueError))
    assert database_snapshot() == before
    _assert_originals(fixture, original)


def test_unknown_canonical_digest_error_propagates_from_reader_writer_and_builder(bagged_receipt, monkeypatch):
    fixture, result, original = bagged_receipt
    payload = deepcopy(result["bagging_result"])
    failure = RuntimeError("synthetic canonical digest failure")

    def fail_digest(_candidate):
        raise failure

    before = database_snapshot()
    # Exception boundary only: no successful validator or completion is stubbed.
    with monkeypatch.context() as patch:
        patch.setattr(workflow, "_canonical_payload_digest", fail_digest)
        with pytest.raises(RuntimeError) as caught:
            with fixture.session_scope() as session:
                artifacts.load_bagging_result_payload(session, payload["bagging_result_id"], expected_order_id=fixture.order_id)
        assert caught.value is failure
        new_payload = deepcopy(payload)
        new_payload["bagging_result_id"] += "-unknown-failure"
        with pytest.raises(RuntimeError) as caught:
            with fixture.session_scope() as session:
                artifacts.save_bagging_result_artifact(session, payload=new_payload)
        assert caught.value is failure
        with pytest.raises(RuntimeError) as caught:
            _bundle(fixture, payload)
        assert caught.value is failure
    assert database_snapshot() == before
    _assert_originals(fixture, original)


@pytest.mark.parametrize("known", [True, False], ids=["typed-receipt-error", "unknown-runtime-error"])
def test_run_bagging_builder_exception_boundary_has_no_partial_artifacts(owned_bagging, monkeypatch, known):
    fixture = owned_bagging
    original = _originals(fixture)
    before = database_snapshot()
    failure = artifacts.BaggingReceiptInvalid("bagging_result_materialization_digest_missing") if known else RuntimeError("synthetic bundle failure")
    calls = []

    def fail_builder(*, order, bagging_result):
        # Actual producer/candidate has already run. Only this failure boundary
        # is synthetic; it is not a substitute for the normal SQL baseline.
        assert order.id == fixture.order_id
        assert artifacts.validate_bagging_receipt(bagging_result) == bagging_result["materialization_digest"]
        assert [line["quantity_original"] for line in bagging_result["materialization_candidate"]["lines"]] == [7, 0]
        calls.append(bagging_result["bagging_result_id"])
        raise failure

    with monkeypatch.context() as patch:
        patch.setattr(workflow, "_build_output_bundle_payload", fail_builder)
        if known:
            assert workflow.run_bagging(fixture.order_id, expected_revision=revision(fixture)) == (None, str(failure))
        else:
            with pytest.raises(RuntimeError) as caught:
                workflow.run_bagging(fixture.order_id, expected_revision=revision(fixture))
            assert caught.value is failure
    assert len(calls) == 1
    assert database_snapshot() == before
    assert_no_confirmation(fixture)
    _assert_originals(fixture, original)
