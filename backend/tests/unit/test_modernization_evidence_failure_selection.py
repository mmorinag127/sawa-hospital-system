"""Failure-selection siblings using actual ORM/classifier and owned API reads.

OCR selection is manually seeded by the unchanged surface fixture. API cases
use its actual app/routes/read-only middleware, SQLite, dated JSON and local
PDF/version capture. Local AUTH_DISABLED without lifespan is not native OCR,
operator-flow, startup/formal schema, production-auth, PG or live proof.
No positive native artifact or execution receipt is fabricated.
"""

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from test_modernization_workflow_surface_parity import (
    assert_no_output_candidate, assert_saved_identity, database_snapshot,
    read_surfaces, surface,  # noqa: F401 -- unchanged pytest fixture registration
)
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.services import ocr_evidence_service as evidence


def make_row(*, status="ready", payload=None, **overrides):
    authored = {"tables": [{"rows": [["7", "0", ""]]}]} if payload is None else deepcopy(payload)
    values = {
        "id": "UNIT-SELECTION-EVIDENCE", "order_id": "UNIT-SELECTION-ORDER",
        "schema_version": "v2", "producer_version": "unit-fixture/v1",
        "source": "unit-fixture", "status": status, "payload_json": authored,
        "artifact_digest": sha256(json.dumps(authored).encode()).hexdigest(),
    }
    values.update(overrides)
    return OrderOcrEvidenceRun(**values)


def row_snapshot(row):
    return {column.key: deepcopy(getattr(row, column.key)) for column in row.__table__.columns}


@pytest.mark.parametrize("values,expected", [
    ((), False), (("ready", "done"), False), (("future-status", "pending"), False),
    ((None, "", "  "), False), (("failed",), True), ((" ERROR ",), True),
    (("ready", " FaIlEd "), True), (("done", "future-status", "eRrOr"), True),
])
def test_actual_common_failure_predicate_normalizes_only_explicit_failure_tokens(values, expected):
    original = deepcopy(values)
    assert evidence._has_failed_evidence_status(*values) is expected
    assert values == original


@pytest.mark.parametrize("status", ["ready", "done", " READY ", " Done ", "future-status", " pending ", ""])
def test_success_and_unknown_row_and_payload_statuses_keep_existing_semantics(status):
    row = make_row(status=status, payload={
        "status": status, "stage": "unknown-stage", "tables": [{"rows": [["7", "0", ""]]}],
    })
    before = row_snapshot(row)
    assert evidence.evidence_run_is_selectable(row) is True
    assert evidence.classify_evidence_payload(row.payload_json) == {
        "persistable": True, "status": status.strip().lower() or None, "stage": "unknown-stage",
    }
    assert row_snapshot(row) == before


@pytest.mark.parametrize("status", ["failed", "error", " FaIlEd ", " ERROR "])
def test_failed_row_status_cannot_adopt_an_otherwise_reusable_success_payload(status):
    row = make_row(status=status)
    before = row_snapshot(row)
    assert evidence.classify_evidence_payload(row.payload_json) == {
        "persistable": True, "status": None, "stage": None,
    }
    assert evidence.evidence_run_is_selectable(row) is False
    assert row_snapshot(row) == before


@pytest.mark.parametrize("field", ["status", "stage"])
@pytest.mark.parametrize("failure", ["failed", "error", " FaIlEd ", " ERROR "])
def test_payload_failure_overrides_ready_row_and_preserves_classifier_error_detail(field, failure):
    payload = {"tables": [{"rows": [["7", "0", ""]]}], field: failure, "error": "  upstream refusal  "}
    row = make_row(payload=payload)
    before = row_snapshot(row)
    assert evidence.evidence_run_is_selectable(row) is False
    assert evidence.classify_evidence_payload(row.payload_json) == {
        "persistable": False, "error": "ocr_pipeline_failed", "message": "upstream refusal",
        "status": failure.strip().lower() if field == "status" else None,
        "stage": failure.strip().lower() if field == "stage" else None,
    }
    assert row_snapshot(row) == before


@pytest.mark.parametrize("status,stage,detail", [
    (" FaIlEd ", None, "failed"), (None, " ERROR ", "error"),
    (" ERROR ", " processing ", "processing"), ("done", " FaIlEd ", "failed"),
])
def test_classifier_without_upstream_error_keeps_existing_stage_then_status_detail(status, stage, detail):
    payload = {"status": status, "stage": stage, "tables": [{"rows": [["7", "0", ""]]}]}
    original = deepcopy(payload)
    assert evidence.classify_evidence_payload(payload) == {
        "persistable": False, "error": "ocr_pipeline_failed", "message": detail,
        "status": status.strip().lower() if status else None,
        "stage": stage.strip().lower() if stage else None,
    }
    assert payload == original


@pytest.mark.parametrize("changes,payload_changes", [
    ({"status": "diagnostic_only"}, {}), ({"status": "repair_blocked"}, {}),
    ({"source": "legacy-cache-backfill"}, {}), ({"source": "legacy-cache-mirror"}, {}),
    ({"producer_version": "legacy-cache-mirror/v1"}, {}),
    ({}, {"canonical_adoption_allowed": False}),
    ({}, {"_ocr_execution": "invalid-execution-shape"}), ({}, {"_ocr_execution": {}}),
])
def test_existing_diagnostic_legacy_adoption_and_malformed_execution_guards_still_reject(
    changes, payload_changes,
):
    payload = {"tables": [{"rows": [["7", "0", ""]]}], **deepcopy(payload_changes)}
    row = make_row(payload=payload, **changes)
    before = row_snapshot(row)
    assert evidence.evidence_run_is_selectable(row) is False
    assert row_snapshot(row) == before


@pytest.mark.parametrize("field", ["status", "stage"])
@pytest.mark.parametrize("failure", [" FaIlEd ", " ERROR "])
def test_ready_row_with_payload_failure_blocks_actual_selected_reads_and_output_without_writes(
    surface, field, failure,
):
    f, client = surface
    original = f.saved()
    selected = evidence.get_selected_evidence_run(f.order_id)
    assert selected["id"] == f.evidence_id and selected["selectable"] is True
    with f.session_scope() as session:
        row = session.get(OrderOcrEvidenceRun, f.evidence_id)
        assert row.status == "ready"
        payload = deepcopy(row.payload_json)
        payload[field] = failure
        row.payload_json = payload
        row.artifact_digest = sha256(json.dumps(payload).encode()).hexdigest()
    before = database_snapshot()
    assert evidence.get_selected_evidence_run(f.order_id) is None
    detail, workflow, sheet, results = read_surfaces(f, client)
    assert detail["id"] == workflow["order_id"] == results["order_id"] == f.order_id
    assert detail["lines"] == []
    assert workflow["selected_ocr_result_id"] == results["selected_ocr_result_id"] == f.evidence_id
    assert workflow["saved_sheet_id"] == f.draft_id
    assert workflow["confirmed_snapshot_id"] is None
    assert "ocr_result_execution_lineage_mismatch" in workflow["blockers"]
    assert "ocr_result_execution_lineage_mismatch" in results["blockers"]
    assert workflow["menu_amount_resolution"] == {
        "status": "unresolved", "blocker": "synchronous_input_conflict",
        "message": "synchronous_input_conflict", "affected_lines": [],
    }
    assert_saved_identity(f, sheet, original)
    # Failure history remains visible and selected identity remains recorded;
    # neither means it may be adopted as a successful current result.
    assert len(results["results"]) == 1
    public = results["results"][0]
    assert public["ocr_result_id"] == f.evidence_id
    assert public["order_id"] == f.order_id
    assert public["template_version_id"] == f.projection_id
    assert public["status"] == "ready"
    assert public["selected"] is True and public["selectable"] is False
    assert_no_output_candidate(f, "synchronous_input_missing")
    assert f.saved() == original
    assert [row[4] for row in f.saved()["rows"]] == ["7", "", "", "0"]
    assert database_snapshot() == before
