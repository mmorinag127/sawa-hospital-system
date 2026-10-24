"""Explicit out-of-band fault injection; never an authorized terminal writer."""
from sqlalchemy import select

from src.models.ocr_job import OcrJob
from src.models.order_workflow_state import OrderWorkflowState


def inject_stale_job_diagnostics(case, **values):
    assert set(values) <= {"output_reference", "error_message", "metrics"}
    table = OcrJob.__table__
    with case.engine.begin() as connection:
        row = connection.execute(select(table).where(
            table.c.id == case.job, table.c.order_id == case.id,
        )).mappings().one()
        if "metrics" in values:
            assert values["metrics"]["rerun_terminal_receipt"] == row["metrics"]["rerun_terminal_receipt"]
            for key in ("workflow_attempt_id", "request_identity", "nonworkflow_execution", "ingest_lease"):
                assert values["metrics"].get(key) == row["metrics"].get(key)
        result = connection.execute(table.update().where(
            table.c.id == case.job, table.c.order_id == case.id,
        ).values(**values))
        assert result.rowcount == 1


def inject_unowned_job_diagnostics(engine, job_id, **values):
    assert set(values) <= {"output_reference", "error_message", "metrics"}
    table = OcrJob.__table__
    workflow = OrderWorkflowState.__table__
    with engine.begin() as connection:
        assert connection.execute(select(workflow.c.order_id).where(
            workflow.c.secondary_actions_json["workflow_v2"]["ocr_job_id"].as_string() == job_id,
        )).all() == []
        row = connection.execute(select(table).where(table.c.id == job_id)).mappings().one()
        old = row["metrics"] or {}
        new = values.get("metrics", old)
        for key in ("rerun_terminal_receipt", "workflow_attempt_id", "request_identity",
                    "nonworkflow_execution", "ingest_lease"):
            assert old.get(key) is None and new.get(key) is None
        result = connection.execute(table.update().where(table.c.id == job_id).values(**values))
        assert result.rowcount == 1
