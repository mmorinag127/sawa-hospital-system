"""Display-only reads of owned synthetic SQL records, not live OCR proof."""
from copy import deepcopy
from unittest.mock import patch
import pytest

from sqlalchemy import event
from sqlalchemy.sql import Select, visitors

from test_current_ocr_operations import store  # noqa: F401
from test_native_ingest_sql_component import (  # noqa: F401
    native_sql, finish_authored_native, snapshot, OrderWorkflowState, OcrJob,
)
from src.services import ocr_operations_display_service as display


def read_without_work(fixture, call):
    before = snapshot(fixture)
    statements = []
    engine = fixture["factory"].kw["bind"]
    def observe(_connection, _cursor, sql, *_args):
        statements.append(sql)
    def reject_full_payload(_connection, statement, *_args):
        from src.models.order_version import OrderVersion
        for query in visitors.iterate(statement):
            if isinstance(query, Select):
                for selected in query.selected_columns:
                    assert not selected.compare(OcrJob.metrics.__clause_element__()), "full job metrics selected"
                    assert not selected.compare(OrderVersion.line_snapshot.__clause_element__()), "full version lines selected"
    event.listen(engine, "before_cursor_execute", observe)
    event.listen(engine, "before_execute", reject_full_payload)
    try:
        with patch("socket.socket", side_effect=AssertionError("display performed network I/O")):
            result = call()
    finally:
        event.remove(engine, "before_cursor_execute", observe)
        event.remove(engine, "before_execute", reject_full_payload)
    assert snapshot(fixture) == before
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    return result


def test_display_native_owner_and_generation_without_execution_proof(native_sql):
    f = native_sql
    boundary = finish_authored_native(f, "1")
    result = read_without_work(f, display.get_operations_display)
    assert result["source"] == display.SOURCE
    assert result["validation_state"] == "references_only"
    assert result["history"] == [] and result["history_loaded"] is False
    current = next(item for item in result["items"] if item["job_id"] == f["job_id"])
    assert current["status"] == "succeeded"
    assert current["execution_id"] == boundary["execution_id"]
    assert current["generation"] == boundary["ingest_lease"]["generation"]
    assert "orders" not in current["job"]
    assert "claim_token" not in str(current)


def test_scoped_order_display_does_not_replace_an_unregistered_job_with_error(native_sql):
    f = native_sql
    with f["factory"]() as session:
        state = session.get(OrderWorkflowState, f["order_id"])
        state.state = "uploaded"
        state.secondary_actions_json = {}
        session.commit()
    values = read_without_work(f, lambda: display.get_order_job_displays([f["order_id"]]))
    assert list(values) == [f["order_id"]]
    assert values[f["order_id"]]["job"] is None
    assert values[f["order_id"]]["error"] is None


def test_changed_reference_is_visible_not_silent_success(native_sql):
    f = native_sql
    finish_authored_native(f, "1")
    with f["factory"]() as session:
        state = session.get(OrderWorkflowState, f["order_id"])
        meta = deepcopy(state.secondary_actions_json)
        meta["workflow_v2"]["ocr_job_id"] = "missing-recorded-job"
        state.secondary_actions_json = meta
        session.commit()
    result = read_without_work(f, display.get_operations_display)
    item = next(item for item in result["items"] if item["job_id"] == "missing-recorded-job")
    assert item["status"] == "blocked"
    assert item["blockers"] == ["ocr_recorded_job_missing"]


def test_history_is_explicit_keyset_paged_and_does_not_read_metrics(native_sql):
    f = native_sql
    finish_authored_native(f, "1")
    with f["factory"]() as session:
        session.add_all([OcrJob(id=f"display-history-{i}", status="failed",
            input_reference="synthetic-history.pdf", metrics={"huge": "x" * 100000}) for i in range(3)])
        session.commit()
    first = read_without_work(f, lambda: display.get_operations_history(limit=2))
    assert [row["job_id"] for row in first["history"]] == ["display-history-0", "display-history-1"]
    assert first["next_cursor"] == "display-history-1"
    second = read_without_work(f, lambda: display.get_operations_history(limit=2, cursor=first["next_cursor"]))
    assert [row["job_id"] for row in second["history"]] == ["display-history-2"]
    assert second["next_cursor"] is None


@pytest.mark.parametrize("field", ["captured", "job_column"])
def test_native_template_mismatch_still_blocks_display(native_sql, field):
    f = native_sql
    finish_authored_native(f, "1")
    with f["factory"]() as session:
        job = session.get(OcrJob, f["job_id"])
        if field == "captured":
            metrics = deepcopy(job.metrics)
            metrics["nonworkflow_execution"]["orders"][f["order_id"]]["order"]["template_version_id"] = "foreign-template"
            # Explicit out-of-band corruption in the owned fixture, not a writer command.
            session.execute(OcrJob.__table__.update().where(OcrJob.id == f["job_id"]).values(metrics=metrics))
        else:
            from src.models.facility_template_version import FacilityTemplateVersion
            original = session.get(FacilityTemplateVersion, f["projection"])
            values = {column.key: deepcopy(getattr(original, column.key)) for column in original.__table__.columns}
            values.update(id="foreign-template", version="foreign-component")
            session.add(FacilityTemplateVersion(**values))
            session.flush()
            session.execute(OcrJob.__table__.update().where(OcrJob.id == f["job_id"]).values(template_version_id="foreign-template"))
        session.commit()
    value = read_without_work(f, lambda: display.get_order_job_displays([f["order_id"]]))[f["order_id"]]
    assert value["job"] is None
    assert value["error"] == "template_version_mismatch"
