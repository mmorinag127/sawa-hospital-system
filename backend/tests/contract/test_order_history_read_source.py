"""Stored-state HTTP reads, not ingestion, OCR, or historical-retention proof."""
from datetime import datetime
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect

from tests.contract.auth_support import hospital_operator  # noqa: F401
from src.db import session_scope
from src.main import app
from src.models.ocr_job import OcrJob
from src.models.order import Order, OrderLine
from src.models.order_ocr_cache import OrderOcrCache
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_ocr_revision import OrderOcrRevision
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.user import AuditLog
from src.services.ocr_week_rerun_service import summarize_artifact_for_compare


STAMP = datetime(2026, 1, 1)
ROWS = [[0], [3], [8], [9]]
CACHED = {"_edited_ocr": {
    "latest": {"revision_id": "cache-only", "edited_at": STAMP.isoformat(), "rows": [[9]]},
    "revisions": [{"revision_id": "cache-only", "edited_at": STAMP.isoformat(), "rows": [[9]]}],
    "raw_output": {"rows": [[3]], "provider": "gemini"},
}}
EVIDENCE = {"tables": [{"fields": ["qty.regular"], "rows": [[3]]}], "provider": "gemini"}


@pytest.fixture
def stored_history(isolated_formal_schema):
    # No canonical facility/week, ready flag, receipt, or fabricated writer ownership.
    with session_scope() as session:
        session.add_all([Order(id=key, document_uri=f"stored:{key}", message_id=key, received_at=STAMP)
                         for key in ("history-order", "other-order")])
        session.flush()
        session.add(OrderOcrRevision(id="other-revision", order_id="other-order", rows=[[9]],
                                     row_count=1, created_at=STAMP))
        session.add_all([OrderLine(id=f"line-{i}", order_id="history-order", quantity_original=value)
                         for i, value in enumerate((0, 3, 8, 9))])
        session.add(OrderSheetDraft(id="current-draft", order_id="history-order", draft_state="saved",
                                   draft_sheet_json={"rows": ROWS}, edited_at=STAMP, created_at=STAMP))
        session.flush()
        session.add(OrderWorkflowState(order_id="history-order", draft_id="current-draft",
                                       state="sheet_saved", last_transition_at=STAMP))
        session.add(AuditLog(id="real-audit", actor="operator", action="stored-audit", target="history-order",
                             metadata_json={"original": 8}, created_at=STAMP))


@pytest.fixture
def read_history(stored_history, hospital_operator, tmp_path, monkeypatch):
    data = Path(__file__).resolve().parents[2] / "src/data"
    source = tmp_path / "facility.json"
    source.write_bytes((data / "facility_master.template.json").read_bytes())
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(source))
    monkeypatch.delenv("FACILITY_MASTER_URI", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    protected = (source, data / "facility_master.template.json",
                 data / "order_form_master_templates/master_layout_template.xlsx")
    with session_scope() as session:
        engine = session.get_bind()
    database = Path(engine.url.database)
    tables = inspect(engine).get_table_names()

    def snapshot():
        with engine.connect() as connection:
            rows = {name: sorted(map(repr, connection.exec_driver_sql(f'SELECT * FROM "{name}"').all()))
                    for name in tables}
        hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (*protected, database)}
        return rows, hashes

    observations = []

    def read(endpoint, *, order_id="history-order", sql_fault=False, authenticated=True):
        before = snapshot()
        statements = []

        def observe(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)
            if sql_fault and "FROM order_ocr_revisions" in statement:
                raise RuntimeError("owned revision SELECT failure")

        event.listen(engine, "before_cursor_execute", observe)
        try:
            response = TestClient(app).get(f"/orders/{order_id}/{endpoint}",
                                          headers=hospital_operator.headers if authenticated else {})
        finally:
            event.remove(engine, "before_cursor_execute", observe)
        assert snapshot() == before
        assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
        if sql_fault:
            assert any("FROM order_ocr_revisions" in sql for sql in statements)
        observations.append({"endpoint": endpoint, "order_id": order_id, "status": response.status_code,
                             "body": response.json(), "hashes": before[1], "unchanged": True})
        return response

    yield read
    (tmp_path / "history-read-observations.json").write_text(json.dumps(observations, indent=2))


@pytest.mark.parametrize("endpoint", ["ocr-history", "history"])
@pytest.mark.parametrize("scenario", ["sql", "candidate", "cache", "evidence", "empty", "failed", "sql-error"])
def test_history_uses_only_sql_revisions_and_preserves_diagnostics(read_history, endpoint, scenario):
    has_sql = scenario in {"sql", "candidate"}
    has_cache = scenario in {"cache", "failed", "sql-error"}
    has_evidence = scenario in {"evidence", "failed"}
    metadata = {"source": "original-producer", "review_state": "draft_ready"}
    if scenario == "candidate":
        metadata.update(workflow_job_id="original-job", workflow_attempt_id="original-attempt",
                        workflow_revision=7, adopted=False, result_disposition="rejected",
                        execution_lineage={"disposition": "rejected"})
    with session_scope() as session:
        if has_sql:
            session.add(OrderOcrRevision(id="sql-revision-8", order_id="history-order", created_at=STAMP,
                rows=ROWS, fields=["qty.regular"], header=["Quantity"], row_ids=["zero", "three", "eight", "nine"],
                row_count=4, changed=True, ui_mode="table", metadata_json=metadata))
        if has_cache:
            session.add(OrderOcrCache(order_id="history-order", payload=CACHED, updated_at=STAMP))
        if has_evidence:
            session.add(OrderOcrEvidenceRun(id="evidence-3", order_id="history-order", schema_version="v1_legacy",
                status="diagnostic_only", source="original-producer", producer_version="original-version",
                artifact_digest="stored-diagnostic-digest", payload_json=EVIDENCE, created_at=STAMP))
        if scenario == "failed":
            session.add(OcrJob(id="failed-job", order_id="history-order", input_reference="stored:history-order",
                status="failed", error_message="main_ocr_failed:gemini", created_at=STAMP, updated_at=STAMP,
                metrics={"request_mode": "llm_reparse", "requested_provider": "gemini", "provider": "gemini",
                         "processing_stage": "inference", "result_state": "hard_failed"}))
    response = read_history(endpoint, sql_fault=scenario == "sql-error")
    if scenario == "sql-error":
        assert response.status_code == 500
        assert response.json() == {"detail": "ocr history load failed" if endpoint == "ocr-history" else "order history load failed"}
        return
    assert response.status_code == 200
    body = response.json()
    if endpoint == "ocr-history":
        assert [row["revision_id"] for row in body["revisions"]] == (["sql-revision-8"] if has_sql else [])
        assert body["latest"] == (body["revisions"][-1] if has_sql else None)
        if has_sql:
            assert body["latest"]["rows"] == ROWS
            assert body["latest"]["row_ids"] == ["zero", "three", "eight", "nine"]
            assert body["latest"]["fields"] == ["qty.regular"]
            assert all(body["latest"][key] == value for key, value in metadata.items())
        assert body["raw_output"] == (CACHED["_edited_ocr"]["raw_output"] if has_cache else EVIDENCE if has_evidence else None)
        assert body["raw_output_source"] == ("order_ocr_cache._edited_ocr.raw_output" if has_cache else
                                              "order_ocr_evidence_runs.payload_json" if has_evidence else None)
        if scenario == "failed":
            assert body["latest_reparse_attempt"] is None
            assert body["reparse_state"] is None
            with session_scope() as session:
                job = session.get(OcrJob, "failed-job")
                assert job.status == "failed" and job.error_message == "main_ocr_failed:gemini"
                assert job.metrics == {
                    "request_mode": "llm_reparse", "requested_provider": "gemini", "provider": "gemini",
                    "processing_stage": "inference", "result_state": "hard_failed",
                }
    else:
        revisions = [item for item in body["items"] if item["action"] in {"ocr_sheet_save", "ocr_candidate_review"}]
        assert [item["id"] for item in revisions] == (["sql-revision-8"] if has_sql else [])
        if has_sql:
            assert revisions[0]["metadata"]["source"] == "order_ocr_revisions"
            assert revisions[0]["action"] == ("ocr_candidate_review" if scenario == "candidate" else "ocr_sheet_save")
        assert next(item for item in body["items"] if item["id"] == "real-audit")["metadata"] == {"original": 8}
    diagnostics = body["diagnostics"]
    assert diagnostics["cache"] == {"source": "order_ocr_cache", "payload": CACHED if has_cache else None}
    assert diagnostics["latest_evidence"]["source"] == "order_ocr_evidence_runs"
    evidence = diagnostics["latest_evidence"]["record"]
    if has_evidence:
        assert evidence["id"] == "evidence-3"
        assert evidence["payload_json"] == EVIDENCE
        assert evidence["source"] == "original-producer"
    else:
        assert evidence is None
    summary = summarize_artifact_for_compare("ocr_history" if endpoint == "ocr-history" else "order_history",
                                             {"status_code": 200, "ok": True, "data": body})
    assert summary["entry_count"] == len(body["revisions"] if endpoint == "ocr-history" else body["items"])


@pytest.mark.parametrize("endpoint", ["ocr-history", "history"])
def test_history_order_identity_and_auth_do_not_materialize_or_write(read_history, endpoint, monkeypatch):
    # Missing orders must be rejected before consulting legacy diagnostic data.
    from src.services import order_service
    original = order_service._load_ocr_history_diagnostics
    def diagnostics(order_id):
        assert order_id != "missing-order", "orphan diagnostics cannot establish order identity"
        return original(order_id)
    monkeypatch.setattr(order_service, "_load_ocr_history_diagnostics", diagnostics)
    assert read_history(endpoint, authenticated=False).status_code == 401
    missing = read_history(endpoint, order_id="missing-order")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "order not found"}
    other = read_history(endpoint, order_id="other-order")
    assert other.status_code == 200
    body = other.json()
    if endpoint == "ocr-history":
        assert [row["revision_id"] for row in body["revisions"]] == ["other-revision"]
        assert body["latest"]["rows"] == [[9]]
    else:
        assert "other-revision" in {item["id"] for item in body["items"]}
        assert "real-audit" not in {item["id"] for item in body["items"]}
