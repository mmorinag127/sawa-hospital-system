"""Owned migrated DB claim predicates; not PG concurrency or OCR/live proof."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from sqlalchemy import MetaData, event, select
from sqlalchemy.orm import Session

from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.workflow_ocr_delivery import WorkflowOcrDelivery
from src.services import workflow_ocr_delivery_service as delivery


def database_snapshot(engine):
    metadata = MetaData()
    metadata.reflect(bind=engine)
    with engine.connect() as connection:
        return {
            table.name: sorted(
                (deepcopy(dict(row)) for row in connection.execute(select(table)).mappings()),
                key=repr,
            )
            for table in metadata.sorted_tables
        }


@pytest.mark.parametrize("fault", [None, "expired", "token", "attempt", "revision", "generation"])
def test_real_claim_read_only_predicate_and_entire_database_unchanged(
    isolated_formal_schema, fault,
):
    engine = isolated_formal_schema
    order_id, job_id = "ORD-read-claim", "OCR-read-claim"
    delivery_id, attempt, revision = "delivery-read-claim", "attempt-read-claim", 17
    claim = {
        "delivery_id": delivery_id, "job_id": job_id, "order_id": order_id,
        "workflow_attempt_id": attempt, "workflow_revision": revision,
        "generation": 1, "claim_token": "owned-read-token",
    }
    with Session(engine) as setup:
        setup.add(Order(id=order_id, document_uri="fixture://synthetic", message_id="read-claim"))
        setup.flush()
        setup.add(OcrJob(
            id=job_id, order_id=order_id, status="running",
            input_reference="fixture://synthetic",
            metrics={"workflow_ocr_delivery_id": delivery_id,
                     "workflow_attempt_id": attempt, "workflow_completion_revision": revision},
        ))
        setup.flush()
        setup.add(WorkflowOcrDelivery(
            id=delivery_id, order_id=order_id, job_id=job_id,
            workflow_attempt_id=attempt, workflow_revision=revision,
            delivery_generation=1, task_name="owned-read-claim-task", task_contract={},
            identity_sha256="synthetic-claim-predicate-only", request_mode="ocr_reparse",
            selected_pdf_receipt={}, request_identity={}, state=delivery.CLAIMED,
            claim_token=claim["claim_token"],
            lease_expires_at=datetime.utcnow() + timedelta(days=-1 if fault == "expired" else 1),
        ))
        setup.commit()
    if fault == "token":
        claim["claim_token"] = "different-token"
    elif fault == "attempt":
        claim["workflow_attempt_id"] = "different-attempt"
    elif fault == "revision":
        claim["workflow_revision"] = revision + 1
    elif fault == "generation":
        claim["generation"] = 2

    before = database_snapshot(engine)
    statements = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
        assert statement.lstrip().split(None, 1)[0].upper() == "SELECT", statement

    prior_claim = delivery._claim_context.get()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        with delivery.claim_context(claim), Session(engine) as session:
            assert delivery.lock_claim_write(
                session, job_id=job_id, order_id=order_id,
                attempt=attempt, revision=revision, read_only=True,
            ) is (fault is None)
            assert not session.new and not session.dirty and not session.deleted
            assert "workflow_delivery_terminal_jobs" not in session.info
            session.commit()
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert statements
    assert delivery._claim_context.get() is prior_claim
    assert database_snapshot(engine) == before
