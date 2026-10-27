"""Owned component production and API reads; not native OCR or live proof."""
from copy import deepcopy
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from tests.contract.test_orders_ocr_sheet_history_api import history_case  # noqa: F401
from tests.contract.test_orders_ocr_status_api import _owned_status_case
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.document import OrderDocument
from src.models.uploaded_pdf import UploadedPdf
from src.models.order_version import OrderVersion
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import ocr_job_service, order_service


@pytest.mark.parametrize("corrupt", [False, True])
def test_loaded_workflow_binding_matches_fresh_read_without_reloading_metadata(history_case, corrupt):
    from src.db import session_scope

    case = _owned_status_case(history_case)
    job_id = str(uuid4())
    case.produce(cell_values="5", job_id=job_id)
    if corrupt:
        with case.engine.begin() as connection:
            table = OcrJob.__table__
            metrics = deepcopy(connection.execute(select(table.c.metrics).where(table.c.id == job_id)).scalar_one())
            metrics["workflow_attempt_id"] = "foreign-attempt"
            connection.execute(table.update().where(table.c.id == job_id).values(metrics=metrics))
    with case.readonly():
        expected = ocr_job_service.get_recorded_order_job_bindings([case.id])
        with session_scope() as session:
            order = session.get(Order, case.id)
            state = session.get(OrderWorkflowState, case.id)
            statements = []

            def observe(connection, cursor, statement, *args):
                statements.append(statement)

            event.listen(case.engine, "before_cursor_execute", observe)
            try:
                actual = ocr_job_service.get_recorded_order_job_bindings(
                    [case.id], read_session=session, _read_context=(order, state),
                )
            finally:
                event.remove(case.engine, "before_cursor_execute", observe)
            assert actual == expected
            assert not any("FROM orders LEFT OUTER JOIN" in " ".join(sql.split()) for sql in statements)
            assert not session.dirty and not session.new and not session.deleted
        assert expected[case.id]["error"] == ("ocr_recorded_job_attempt_mismatch" if corrupt else None)


@pytest.mark.parametrize("age", [-1, 1])
def test_actual_uuid_binding_wins_over_unowned_neighbor_on_list_detail_and_progress(history_case, age):
    case = _owned_status_case(history_case)
    actual_id = str(uuid4())
    source = case.produce(cell_values="5", job_id=actual_id)
    case.save(source["sheet"])
    # An intentionally unowned row is not a second successful execution.
    with case.engine.begin() as connection:
        connection.execute(OcrJob.__table__.insert().values(
            id=str(uuid4()), order_id=case.id, status="done", input_reference=str(case.pdf),
            created_at=datetime.utcnow() + timedelta(days=age), metrics={"request_mode": "ingest_first_pass"},
        ))
    before_job = deepcopy(ocr_job_service.get_job(actual_id))
    with case.readonly():
        detail = case.get()
        response = case.client.get("/orders?include_ocr=true", headers=case.actor.headers)
        assert response.status_code == 200, response.text
        (listed,) = [row for row in response.json()["orders"] if row["id"] == case.id]
        workflow = case.get("workflow-v2")
        progress = case.get("workflow-v2/ocr-progress")
        current = order_service.get_current_sheet_context(case.id)
    for row in (detail, listed):
        assert row["ocr_job_id"] == actual_id
        assert row["ocr_job_binding_error"] is None
        assert row["ocr_status"] == "done"
    assert workflow["ocr_job"]["ocr_job_id"] == progress["ocr_job"]["ocr_job_id"] == actual_id
    assert current["rows"] == source["sheet"]["rows"]
    assert ocr_job_service.get_job(actual_id) == before_job


@pytest.mark.parametrize("damage", ["missing", "attempt", "input", "malformed", "foreign", "document", "pdf_hash"])
def test_corrupted_recorded_job_is_not_replaced_or_repaired_on_read(history_case, damage):
    case = _owned_status_case(history_case)
    actual_id = str(uuid4())
    source = case.produce(cell_values="5", job_id=actual_id)
    case.save(source["sheet"])
    # Deliberate out-of-band corruption; never disable the legitimate writer guard.
    table = OcrJob.__table__
    with case.engine.begin() as connection:
        row = connection.execute(select(table).where(table.c.id == actual_id)).mappings().one()
        if damage == "missing":
            states = OrderWorkflowState.__table__
            state = connection.execute(select(states).where(states.c.order_id == case.id)).mappings().one()
            actions = deepcopy(state["secondary_actions_json"])
            actions["workflow_v2"]["ocr_job_id"] = "missing-recorded-job"
            connection.execute(states.update().where(states.c.order_id == case.id).values(secondary_actions_json=actions))
        elif damage == "attempt":
            metrics = deepcopy(row["metrics"])
            metrics["workflow_attempt_id"] = "unowned-attempt"
            connection.execute(table.update().where(table.c.id == actual_id).values(metrics=metrics))
        elif damage == "foreign":
            orders = Order.__table__
            original = dict(connection.execute(select(orders).where(orders.c.id == case.id)).mappings().one())
            foreign_id = "unowned-" + uuid4().hex
            original.update(id=foreign_id, message_id="unowned-message-" + uuid4().hex)
            connection.execute(orders.insert().values(**original))
            connection.execute(table.update().where(table.c.id == actual_id).values(order_id=foreign_id))
        elif damage == "document":
            documents = OrderDocument.__table__
            original = dict(connection.execute(select(documents).where(documents.c.id == "history-document")).mappings().one())
            replacement = "replacement-" + uuid4().hex
            original["id"] = replacement
            connection.execute(documents.insert().values(**original))
            connection.execute(Order.__table__.update().where(Order.id == case.id).values(current_document_id=replacement))
        elif damage == "pdf_hash":
            uploads = UploadedPdf.__table__
            connection.execute(uploads.update().where(uploads.c.id == "history-pdf").values(content_sha256="0" * 64))
        else:
            metrics = deepcopy(row["metrics"])
            if damage == "malformed":
                metrics["reparse_read_input"] = None
            else:
                metrics["reparse_read_input"]["order"]["document_uri"] = "unowned://replacement"
            connection.execute(table.update().where(table.c.id == actual_id).values(metrics=metrics))
    expected = {
        "missing": "ocr_recorded_job_missing",
        "attempt": "ocr_recorded_job_attempt_mismatch",
        "input": "ocr_recorded_job_input_changed",
        "malformed": "ocr_recorded_job_input_changed",
        "foreign": "ocr_recorded_job_order_mismatch",
        "document": "ocr_recorded_job_document_changed",
        "pdf_hash": "ocr_recorded_job_pdf_mismatch",
    }[damage]
    with case.readonly():
        response = case.client.get("/orders?include_ocr=true", headers=case.actor.headers)
        assert response.status_code == 200, response.text
        (listed,) = [row for row in response.json()["orders"] if row["id"] == case.id]
        assert listed["ocr_job_id"] is None
        assert listed["ocr_job_binding_error"] == expected
        assert listed["ocr_status"] == "failed"
        progress = case.client.get(f"/orders/{case.id}/workflow-v2/ocr-progress", headers=case.actor.headers)
        assert progress.status_code == 400
        assert progress.json()["detail"] == expected
        if damage not in {"document", "pdf_hash"}:
            assert order_service.get_current_sheet_context(case.id)["rows"] == source["sheet"]["rows"]
        with case.engine.connect() as connection:
            saved_id = connection.execute(select(OrderWorkflowState.draft_id).where(
                OrderWorkflowState.order_id == case.id)).scalar_one()
            saved = connection.execute(select(OrderSheetDraft.draft_sheet_json).where(
                OrderSheetDraft.id == saved_id)).scalar_one()
            assert saved["rows"] == source["sheet"]["rows"]


def test_explicit_past_version_binding_is_valid_without_replacing_current_document(history_case):
    import fitz
    from hashlib import sha256
    from src.db import session_scope
    from src.services import order_workflow_v2_service as workflow
    from src.services.workflow_attempt_guard_service import selected_version_snapshot

    case = _owned_status_case(history_case)
    case.produce(cell_values="5")
    original_document_id = "history-document"
    replacement = case.root / "explicit-newer-component.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((40, 40), f"{case.facility} / {case.period} / newer component revision")
        body = pdf.tobytes()
    replacement.write_bytes(body)
    case.protected += (replacement,)
    replacement_id, message = "document-" + uuid4().hex, "message-" + uuid4().hex
    with session_scope() as session:
        order = session.get(Order, case.id)
        old_version = session.execute(select(OrderVersion).where(
            OrderVersion.order_id == case.id, OrderVersion.document_id == original_document_id)).scalar_one()
        document = OrderDocument(id=replacement_id, storage_uri=str(replacement), source_email_id=message,
            received_at=order.received_at, facility_code=order.facility_code, week_code=order.week_code)
        session.add(document)
        session.add(UploadedPdf(id="upload-" + uuid4().hex, message_id=message, storage_uri=str(replacement),
            original_filename=replacement.name, content_sha256=sha256(body).hexdigest(), received_at=order.received_at))
        session.flush()
        order.current_document_id, order.document_uri, order.message_id = replacement_id, str(replacement), message
        order_service._record_current_order_version(session, order, document)
        session.refresh(old_version)
        selected = selected_version_snapshot(old_version)
        assert selected["is_current"] is False
    current, error = workflow.get_workflow_readonly(case.id)
    assert error is None
    job_id = str(uuid4())
    queued, error = workflow.mark_ocr_run_queued(case.id, job_id, input_reference=selected["storage_uri"],
        request_mode="ocr_rerun", delivery_selected_document_id=original_document_id,
        job_metrics={"selected_version_input": selected}, expected_revision=current["revision"])
    assert error is None, error
    assert queued["ocr_job"]["ocr_job_id"] == job_id
    with case.readonly():
        binding = ocr_job_service.get_recorded_order_job_bindings([case.id])[case.id]
        assert binding["error"] is None
        assert binding["job"]["id"] == job_id
        assert binding["job"]["metrics"]["selected_version_input"] == selected
        assert binding["job"]["metrics"]["selected_pdf_receipt"]["document_id"] == original_document_id
        with session_scope() as session:
            order = session.get(Order, case.id)
            assert order.current_document_id == replacement_id
            assert order.document_uri == str(replacement)
