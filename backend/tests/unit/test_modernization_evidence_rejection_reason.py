"""Nonpersisted model/handler components, not native, live UI or rollback proof.

Real canonical packet corruption, source binding and database rollback remain
the responsibility of the separate numeric-review native scenario. No canonical
validator or ownership predicate is replaced here.
"""

import asyncio
from copy import deepcopy
import json

import pytest
from starlette.requests import Request

from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.services import ocr_evidence_service as evidence


@pytest.mark.parametrize("overrides,payload_additions,expected_reason", [
    pytest.param({}, {}, None, id="valid-noncanonical"),
    pytest.param({}, {"_ocr_execution": {"evidence_run_id": "component-evidence", "job_id": "component-job",
                                          "disposition": "pipeline_result"}},
                 None, id="valid-nonworkflow-execution"),
    pytest.param({}, {"_ocr_execution": {"evidence_run_id": "component-evidence", "job_id": "component-job",
                                          "disposition": "pipeline_result",
                                          "workflow_attempt_id": "component-attempt", "workflow_revision": 3}},
                 None, id="valid-workflow-execution"),
    pytest.param({"status": "failed"}, {}, "evidence_unusable", id="row-failed"),
    pytest.param({"status": "error"}, {}, "evidence_unusable", id="row-error"),
    pytest.param({}, {"status": "failed"}, "evidence_unusable", id="payload-failed"),
    pytest.param({}, {"stage": "error"}, "evidence_unusable", id="stage-error"),
    pytest.param({"status": "diagnostic_only"}, {}, "evidence_unusable", id="diagnostic-only"),
    pytest.param({"status": "repair_blocked"}, {}, "evidence_unusable", id="repair-blocked"),
    pytest.param({}, {"canonical_adoption_allowed": False}, "evidence_unusable", id="adoption-disallowed"),
    pytest.param({"source": "legacy-cache-backfill"}, {}, "evidence_unusable", id="legacy-backfill"),
    pytest.param({"source": "legacy-cache-mirror"}, {}, "evidence_unusable", id="legacy-mirror-source"),
    pytest.param({"producer_version": "legacy-cache-mirror/v1"}, {}, "evidence_unusable", id="legacy-mirror-producer"),
    pytest.param({}, {"_ocr_execution": []}, "evidence_unusable", id="execution-not-mapping"),
    pytest.param({}, {"_ocr_execution": {"evidence_run_id": "different-evidence", "job_id": "component-job"}},
                 "evidence_unusable", id="execution-wrong-evidence"),
    pytest.param({}, {"_ocr_execution": {"evidence_run_id": "component-evidence"}},
                 "evidence_unusable", id="execution-missing-job"),
    pytest.param({}, {"_ocr_execution": {"evidence_run_id": "component-evidence", "job_id": "component-job",
                                          "workflow_attempt_id": "component-attempt"}},
                 "evidence_unusable", id="execution-missing-revision"),
    pytest.param({}, {"_ocr_execution": {"evidence_run_id": "component-evidence", "job_id": "component-job",
                                          "workflow_revision": 3}},
                 "evidence_unusable", id="execution-missing-attempt"),
])
def test_real_model_selectability_reason_bool_parity_and_input_unchanged(
    overrides, payload_additions, expected_reason,
):
    values = {
        "id": "component-evidence", "order_id": "component-order",
        "schema_version": "component-noncanonical/v1", "producer_version": "component/v1",
        "source": "component", "status": "ready",
        "payload_json": {"table_raw": "explicit noncanonical component", **deepcopy(payload_additions)},
        "artifact_manifest_json": {}, "artifact_digest": "0" * 64,
    }
    values.update(deepcopy(overrides))
    row = OrderOcrEvidenceRun(**values)
    before = {column.key: deepcopy(getattr(row, column.key)) for column in row.__table__.columns}
    assert evidence.evidence_run_selectability_error(row) == expected_reason
    assert {column.key: getattr(row, column.key) for column in row.__table__.columns} == before
    assert evidence.evidence_run_is_selectable(row) is (expected_reason is None)
    assert {column.key: getattr(row, column.key) for column in row.__table__.columns} == before


@pytest.mark.parametrize("reason", [
    "canonical_artifact_packet_required",
    "canonical_artifact_bytes_changed",
    "canonical_artifact_bytes_unavailable",
    "canonical_artifact_source_changed",
])
def test_registered_dedicated_handler_returns_exact_400_detail(reason):
    from src.main import app

    handler = app.exception_handlers[evidence.SelectedEvidenceRejected]
    request = Request({"type": "http", "method": "GET", "path": "/component-only", "headers": []})
    exc = evidence.SelectedEvidenceRejected(reason)
    assert isinstance(exc, ValueError)
    response = asyncio.run(handler(request, exc))
    assert response.status_code == 400
    assert response.media_type == "application/json"
    assert json.loads(response.body) == {"detail": reason}
    assert str(exc) == reason


def test_plain_value_error_is_not_registered_as_a_business_rejection():
    from src.main import app

    assert evidence.SelectedEvidenceRejected in app.exception_handlers
    assert ValueError not in app.exception_handlers
