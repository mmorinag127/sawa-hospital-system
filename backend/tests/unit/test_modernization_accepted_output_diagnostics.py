"""Owned SQLite diagnostic boundaries; no PG/native/auth/live proof.

Real bagging and persisted artifact damage exercise the lower guards. The
exception injection checks propagation only, never supplies a candidate.
"""

from copy import deepcopy

import pytest

from test_modernization_real_bagging_confirmation import actual_bagging, owned_bagging
from test_modernization_workflow_surface_parity import database_snapshot, surface
from src.models.order_output_artifact import OrderBaggingResult
from src.services import order_output_artifact_service as artifacts
from src.services import order_workflow_v2_service as workflow
from src.services import output_builder as output


@pytest.mark.parametrize("fault", [
    "candidate-missing", "digest-missing", "digest-blank", "digest-mismatch",
    "configured-missing", "facility-missing", "policy-missing",
])
def test_invalid_artifact_keeps_source_guard_diagnostic(owned_bagging, fault):
    fixture = owned_bagging
    result = actual_bagging(fixture)
    original_files = {path: path.read_bytes() for path in
                      (fixture.master_path, fixture.source, fixture.pdf)}
    saved = fixture.saved()
    assert [row[4] for row in saved["rows"]] == ["7", "", "", "0"]
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
    with fixture.session_scope() as session:
        artifact = session.get(OrderBaggingResult, payload["bagging_result_id"])
        artifact.payload_json = payload
        artifact.payload_digest = artifacts.payload_digest(payload)
    damaged = database_snapshot()
    with pytest.raises(ValueError) as caught:
        output._accepted_output_candidate({"id": fixture.order_id})
    assert str(caught.value) == "workflow-v2 output bundle source mismatch"
    assert caught.value.__cause__ is None
    assert database_snapshot() == damaged
    assert fixture.saved() == saved
    assert all(path.read_bytes() == content for path, content in original_files.items())


def test_construction_exception_is_not_reclassified(owned_bagging, monkeypatch):
    fixture = owned_bagging
    actual_bagging(fixture)
    before = database_snapshot()
    cause = ValueError("diagnostic-boundary-fault")

    def reject(*_args, **_kwargs):
        # Fault injection immediately at construction, not a successful mock.
        raise cause

    monkeypatch.setattr(output, "_workflow_v2_materialization_candidate", reject)
    with pytest.raises(ValueError) as caught:
        output._accepted_output_candidate({"id": fixture.order_id})
    assert caught.value is cause
    assert caught.value.__cause__ is None
    assert database_snapshot() == before
