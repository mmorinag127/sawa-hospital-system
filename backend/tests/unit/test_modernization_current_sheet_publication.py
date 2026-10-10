"""Owned migrated SQLite publication components, not PG concurrency or live proof.

Synthetic external OCR comes from selected_repair. Interleaving hooks resave
immediately before the real boundary; raising-only hooks inject rejection after
real decision synchronization. No successful producer/persist/guard doubles.
"""

from copy import deepcopy
from hashlib import sha256
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
import pytest
from sqlalchemy import event

from test_modernization_current_revision_authority import (
    selected_repair as selected_repair,
    repair_order as repair_order,
    no_writes,
    save,
)
from test_modernization_workflow_surface_parity import database_snapshot
from src import db
from src.models.ingest_delivery import IngestDelivery
from src.models.ingest_job import IngestJob
from src.models.order import Order
from src.models.order_current_state import OrderCurrentState
from src.models.order_critical_decision import OrderCriticalDecision
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.uploaded_pdf import UploadedPdf
from src.services import critical_decision_service
from src.services import ingest_job_service, order_current_state_service, order_service
from src.services import uploaded_pdf_service, workflow_state_service
from src.services import workflow_attempt_guard_service as guard


@pytest.fixture
def saved(selected_repair):
    order_id, sheet, evidence = selected_repair
    sheet = deepcopy(sheet)
    index = sheet["fields"].index("qty.regular_x")
    assert len(sheet["rows"]) >= 2
    sheet["rows"][0][index] = 7
    sheet["rows"][1][index] = 0
    identity = save(order_id, sheet)
    context = order_service.get_current_sheet_context(order_id)
    assert context["draft_id"] == identity
    assert context["draft_payload"]["rows"] == sheet["rows"]
    return order_id, sheet, evidence, context


def persist(order_id, context):
    return order_current_state_service.persist_current_state(
        order_id=order_id, state_json=context, draft_id=context["draft_id"],
        evidence_run_id=context["base_evidence_run_id"],
        template_version_id=context["template_version_id"],
    )


def projection(order_id, context):
    return workflow_state_service._build_workflow_state_projection(
        order_id, current_sheet_context_override=context,
        include_candidate_preview=False, persist=True,
    )


def assert_sheet(order_id, sheet):
    from src.services import order_workflow_v2_service
    stored, error = order_workflow_v2_service.get_saved_sheet(order_id)
    assert error is None
    assert stored["sheet"] == sheet


def test_normal_saved_context_persist_cache_read_and_workflow(saved):
    order_id, sheet, _evidence, context = saved
    original = deepcopy(context)
    expected = sha256(guard.canonical_snapshot_json(dict(context)).encode()).hexdigest()
    assert context.input_receipt["context_sha256"] == expected
    assert persist(order_id, context) is not None
    before = database_snapshot()
    with no_writes():
        cached = order_current_state_service.get_current_state_payload(order_id)
        assert cached == context
        assert cached.input_receipt == context.input_receipt
        assert order_service.get_current_sheet_context(order_id) == context
    assert database_snapshot() == before
    public = jsonable_encoder(context)
    assert public == jsonable_encoder(dict(context))
    assert "input_receipt" not in public
    assert "_current_sheet_input_receipt" not in public
    result = projection(order_id, context)
    assert result["current_sheet_revision_id"] == context["draft_id"]
    assert "input_receipt" not in jsonable_encoder(result)
    assert context == original
    assert_sheet(order_id, sheet)


def test_generation_then_resave_rejects_real_persist_without_stale_json(saved):
    order_id, sheet, _evidence, old = saved
    latest = save(order_id, sheet)
    before = database_snapshot()
    with pytest.raises(guard.CurrentSheetInputChanged):
        persist(order_id, old)
    assert database_snapshot() == before
    with db.session_scope() as session:
        row = session.get(OrderCurrentState, order_id)
        assert row is None or row.draft_id != old["draft_id"]
    with no_writes():
        assert order_service.get_current_sheet_context(order_id)["draft_id"] == latest
    assert_sheet(order_id, sheet)


@pytest.mark.parametrize("fault", ["resave", "selected_evidence"])
def test_projection_boundary_rejects_changed_source_before_decision_writes(saved, monkeypatch, fault):
    order_id, sheet, _evidence, context = saved
    real = workflow_state_service.validate_current_sheet_input
    seen = []
    snapshots = []

    def interleave(session, identity, payload, **kwargs):
        assert kwargs.get("writing") is True
        assert not seen
        seen.append(identity)
        if fault == "resave":
            save(order_id, sheet)
        else:
            # Persisted selection fault, not a successful public selection proof.
            with db.session_scope() as changed:
                changed.get(OrderWorkflowState, order_id).evidence_run_id = None
        snapshots.append(database_snapshot())
        return real(session, identity, payload, **kwargs)

    monkeypatch.setattr(workflow_state_service, "validate_current_sheet_input", interleave)
    with pytest.raises(guard.CurrentSheetInputChanged):
        projection(order_id, context)
    assert seen == [order_id]
    assert database_snapshot() == snapshots[0]


def test_real_decision_sync_then_projection_failure_rolls_back_every_table(saved, monkeypatch):
    order_id, sheet, _evidence, context = saved
    residue_id = str(uuid4())
    # Obsolete synthetic decision is intentionally deleted by real synchronization.
    # It supplies a measurable rollback target, not an authored business choice.
    with db.session_scope() as session:
        session.add(OrderCriticalDecision(
            id=residue_id, order_id=order_id, decision_type="synthetic-obsolete-publication-decision",
            candidate_set_json={"synthetic": True},
        ))
    context = order_service.get_current_sheet_context(order_id)
    before = database_snapshot()
    real_sync = critical_decision_service.sync_pending_decisions
    synchronized = []
    failure = RuntimeError("synthetic post-sync projection failure")

    def observe_sync(*args, **kwargs):
        assert kwargs.get("write_session") is not None
        result = real_sync(*args, **kwargs)
        assert kwargs["write_session"].get(OrderCriticalDecision, residue_id) is None
        synchronized.append(kwargs["write_session"])
        return result

    def fail_after_sync(**kwargs):
        assert synchronized
        raise failure

    monkeypatch.setattr(critical_decision_service, "sync_pending_decisions", observe_sync)
    monkeypatch.setattr(workflow_state_service, "_derive_state", fail_after_sync)
    with pytest.raises(RuntimeError) as caught:
        projection(order_id, context)
    assert caught.value is failure
    assert len(synchronized) == 1
    assert database_snapshot() == before
    assert_sheet(order_id, sheet)


@pytest.mark.parametrize("ref", ["draft_id", "evidence_run_id", "template_version_id"])
def test_real_persist_rejects_reference_mismatch_without_writes(saved, ref):
    order_id, _sheet, _evidence, context = saved
    arguments = dict(order_id=order_id, state_json=context, draft_id=context["draft_id"],
                     evidence_run_id=context["base_evidence_run_id"],
                     template_version_id=context["template_version_id"])
    arguments[ref] = "synthetic-foreign-reference"
    before = database_snapshot()
    with pytest.raises(guard.CurrentSheetInputChanged, match="current_sheet_persist_reference_mismatch"):
        order_current_state_service.persist_current_state(**arguments)
    assert database_snapshot() == before


@pytest.mark.parametrize("fault", ["owner", "persist"])
def test_refresh_never_returns_old_context_after_boundary_rejection(saved, monkeypatch, fault):
    order_id, _sheet, _evidence, _context = saved
    before = database_snapshot()
    failure = guard.CurrentSheetInputChanged("synthetic publication rejection")
    seen = []
    if fault == "owner":
        real = guard.lock_current_execution_write

        def reject_write(session, identity, **kwargs):
            if not kwargs.get("read_only", False):
                seen.append(identity)
                raise failure
            return real(session, identity, **kwargs)

        monkeypatch.setattr(guard, "lock_current_execution_write", reject_write)
    else:
        def reject_persist(**kwargs):
            seen.append(kwargs["order_id"])
            raise failure

        monkeypatch.setattr(order_current_state_service, "persist_current_state", reject_persist)
    with pytest.raises(guard.CurrentSheetInputChanged) as caught:
        order_service.refresh_current_sheet_context(order_id)
    assert caught.value is failure
    assert seen == [order_id]
    assert database_snapshot() == before


@pytest.mark.parametrize("path", ["cached", "uncached"])
def test_read_interleaving_rejects_old_context_and_read_phase_has_no_sql_writes(saved, monkeypatch, path):
    order_id, sheet, _evidence, context = saved
    external_write = [False]
    after_resave = []
    writes = []
    with db.session_scope() as session:
        engine = session.get_bind()

    def observe(_conn, _cursor, statement, _params, execution, _many):
        if not external_write[0] and (execution.isinsert or execution.isupdate or execution.isdelete):
            writes.append(statement)

    def resave():
        external_write[0] = True
        try:
            save(order_id, sheet)
        finally:
            external_write[0] = False
        after_resave.append(database_snapshot())

    if path == "cached":
        assert persist(order_id, context) is not None
        real = order_service._persisted_current_state_is_reusable

        def interleave(*args, **kwargs):
            reusable = real(*args, **kwargs)
            assert reusable is True
            resave()
            return reusable

        monkeypatch.setattr(order_service, "_persisted_current_state_is_reusable", interleave)
    else:
        order_current_state_service.delete_current_state(order_id)
        real = order_service._build_current_sheet_context_uncached

        def interleave(*args, **kwargs):
            generated = real(*args, **kwargs)
            resave()
            return generated

        monkeypatch.setattr(order_service, "_build_current_sheet_context_uncached", interleave)
    # The hook is an explicit external writer; the subsequent read is measured
    # separately below rather than mislabelling that writer as a readonly query.
    event.listen(engine, "before_cursor_execute", observe)
    try:
        with pytest.raises(guard.CurrentSheetInputChanged):
            order_service.get_current_sheet_context(order_id)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert len(after_resave) == 1
    assert database_snapshot() == after_resave[0]
    assert writes == []
    attribute = "_persisted_current_state_is_reusable" if path == "cached" else "_build_current_sheet_context_uncached"
    monkeypatch.setattr(order_service, attribute, real)
    with no_writes():
        latest = order_service.get_current_sheet_context(order_id)
        assert latest["draft_id"] != context["draft_id"]
        assert latest["draft_payload"]["rows"] == sheet["rows"]


@pytest.mark.parametrize("fault", ["body", "receipt_missing"])
def test_context_tamper_or_plain_override_cannot_publish(saved, fault):
    order_id, _sheet, _evidence, context = saved
    changed = deepcopy(context)
    if fault == "body":
        changed["rows"][0][-1] = "synthetic invalid context content"
    else:
        changed = dict(changed)
    before = database_snapshot()
    with pytest.raises(guard.CurrentSheetInputChanged):
        projection(order_id, changed)
    assert database_snapshot() == before


def test_uploaded_without_selected_sheet_remains_non_sheet_workflow(repair_order):
    order_id, _revision, _configured = repair_order
    # Explicit pre-OCR state setup in the owned database, not a valid-sheet stub.
    with db.session_scope() as session:
        state = session.get(OrderWorkflowState, order_id)
        state.evidence_run_id = None
        state.draft_id = None
        state.state = "uploaded"
    result = workflow_state_service.refresh_workflow_state(order_id, include_candidate_preview=False)
    assert result is not None
    assert result["draft_id"] is None
    assert result["current_sheet_revision_id"] is None


def test_blocked_week_keeps_blocker_and_readonly_database(repair_order):
    order_id, _revision, _configured = repair_order
    with db.session_scope() as session:
        session.get(Order, order_id).week_code = None
    with no_writes():
        context = order_service.get_current_sheet_context(order_id)
        assert context["blockers"] == ["week_unresolved"]
        assert context["draft_payload"] is None
        assert context["rows"] == []
        assert isinstance(context.input_receipt, dict)


def test_notfound_workflow_preserves_absence_without_database_writes(repair_order):
    with no_writes():
        assert workflow_state_service.project_workflow_state("synthetic-missing-order") is None


@pytest.mark.parametrize("fault", [None, "changed", "deleted"])
def test_real_unselected_materialized_draft_receipt_is_verified(saved, fault):
    order_id, sheet, evidence, context = saved
    captured = guard.capture_current_sheet_input(order_id)
    synchronous = order_service._capture_synchronous_cache_write_input(order_id)
    assert synchronous is not None
    # Real writer boundary used by semantic rebase. This bounded component does
    # not claim that this clean saved fixture requires a semantic schema rebase.
    generated = order_service._persist_sheet_draft_with_base_evidence(
        order_id=order_id, draft_sheet_json=deepcopy(sheet),
        evidence_run_override=evidence, edited_by="synthetic-publication-materialization",
        synchronous_write_input=synchronous,
    )
    assert generated is not None
    assert generated["id"] != context["draft_id"]
    derived = deepcopy(dict(context))
    derived["source_draft_record"] = generated
    bound = guard.bind_current_sheet_input(derived, captured)
    with db.session_scope() as session:
        assert session.get(OrderWorkflowState, order_id).draft_id == context["draft_id"]
        if fault == "changed":
            row = session.get(OrderSheetDraft, generated["id"])
            row.draft_sheet_json = {**row.draft_sheet_json, "synthetic_fault": True}
        elif fault == "deleted":
            session.delete(session.get(OrderSheetDraft, generated["id"]))
    with no_writes():
        with db.session_scope() as session:
            if fault is None:
                assert guard.validate_current_sheet_input(session, order_id, bound).id == order_id
            else:
                with pytest.raises(guard.CurrentSheetInputChanged, match="current_sheet_generated_draft_"):
                    guard.validate_current_sheet_input(session, order_id, bound)
    assert_sheet(order_id, sheet)


@pytest.mark.parametrize("fault", [None, "revoked", "identity", "digest"])
def test_real_ingest_uploaded_leaf_read_mode_shared_predicates(repair_order, fault):
    order_id, _revision, _configured = repair_order
    with db.session_scope() as session:
        order = session.get(Order, order_id)
        uploaded = session.query(UploadedPdf).filter_by(message_id=order.message_id).one()
        uploaded.status = "processing"
        uploaded.lease_token = "synthetic-owned-token"
        uploaded.lease_owner = "synthetic-owned-worker"
        uploaded.attempt_count = 1
        session.flush()
        keys = ("id", "lease_token", "lease_owner", "attempt_count", "message_id", "storage_uri",
                "content_sha256", "received_at", "facility_hint", "week_hint", "facility_name",
                "skip_ocr", "source_kind", "original_filename", "page_count")
        uploaded_lease = {key: getattr(uploaded, key) for key in keys}
        uploaded_lease["received_at"] = uploaded.received_at.isoformat()
        payload = {"synthetic": True, "order_id": order_id}
        lease = dict(id=str(uuid4()), generation="synthetic-generation", claim_id="synthetic-claim",
                     attempts=1, input_digest=ingest_job_service.payload_digest(payload),
                     uploaded_lease=uploaded_lease)
        session.add(IngestJob(id=lease["id"], generation=lease["generation"], claim_id=lease["claim_id"],
                              attempts=1, payload=payload, input_digest=lease["input_digest"], status="processing"))
        session.flush()
        session.add(IngestDelivery(id=str(uuid4()), job_id=lease["id"],
                                  business_generation=lease["generation"], input_digest=lease["input_digest"],
                                  original_payload=payload, generation=1, state="ACKNOWLEDGED",
                                  task_contract={}, owner_lease=deepcopy(lease), handoff_receipt={"synthetic": True}))
    # Component ownership rows are explicit seeds, not dispatch/live receipts.
    with db.session_scope() as session:
        if fault == "revoked":
            session.get(UploadedPdf, uploaded_lease["id"]).status = "failed"
        elif fault == "identity":
            session.get(UploadedPdf, uploaded_lease["id"]).message_id = "synthetic-other-message"
        elif fault == "digest":
            session.get(IngestJob, lease["id"]).payload = {"synthetic": "changed"}
    with no_writes():
        with db.session_scope() as session:
            assert ingest_job_service.lock_ingest_lease(session, lease, read_only=True) is (fault is None)
        with db.session_scope() as session:
            expected_upload = fault in (None, "digest")
            assert uploaded_pdf_service.lock_uploaded_pdf_lease(session, uploaded_lease, read_only=True) is expected_upload
        with ingest_job_service.ingest_execution_context(lease):
            with db.session_scope() as session:
                locked = guard.lock_order_execution_input(session, order_id, read_only=True)
                assert (locked is not None) is (fault is None)
