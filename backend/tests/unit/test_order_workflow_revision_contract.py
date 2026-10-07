from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.orm.exc import StaleDataError

from src.api import orders as orders_api
from src.api.orders import _require_workflow_if_match, _workflow_completion_revision
from src.services import order_service
from src.services import order_workflow_v2_service


def test_if_match_requires_a_quoted_integer_revision():
    assert _require_workflow_if_match('"7"') == 7

    with pytest.raises(HTTPException) as exc_info:
        _require_workflow_if_match(None)

    assert exc_info.value.status_code == 428
    assert exc_info.value.detail["error"] == "workflow_revision_required"


def test_revision_guard_accepts_current_and_rejects_old_or_double_command():
    workflow = SimpleNamespace(revision=4)

    assert order_workflow_v2_service._require_workflow_revision(None, workflow, 4) is None
    assert (
        order_workflow_v2_service._require_workflow_revision(None, workflow, 3)
        == "workflow_revision_conflict"
    )
    assert (
        order_workflow_v2_service._require_workflow_revision(None, workflow, None)
        == "workflow_revision_required"
    )
    assert (
        order_workflow_v2_service._require_workflow_revision(None, workflow, True)
        == "workflow_revision_invalid"
    )
    assert (
        order_workflow_v2_service._require_workflow_revision(None, workflow, -1)
        == "workflow_revision_invalid"
    )


def test_finalize_turns_a_simultaneous_sql_update_into_a_conflict():
    class ConcurrentSession:
        def flush(self):
            raise StaleDataError("UPDATE matched zero rows")

        def rollback(self):
            pass

    workflow = SimpleNamespace(revision=4)

    assert (
        order_workflow_v2_service._finalize_workflow_mutation(ConcurrentSession(), workflow)
        == "workflow_revision_conflict"
    )


def test_read_serialization_keeps_revision_without_mutating_workflow():
    workflow = SimpleNamespace(
        order_id="ORD-revision-readonly",
        revision=9,
        state="sheet_saved",
        headline=None,
        primary_action=None,
        evidence_run_id=None,
        draft_id=None,
        confirmed_snapshot_id=None,
        template_version_id=None,
        secondary_actions_json={},
        blockers_json=[],
        warnings_json=[],
        last_transition_at=None,
    )

    serialized = order_workflow_v2_service._serialize_workflow(workflow)

    assert serialized["revision"] == 9
    assert workflow.revision == 9


def test_ocr_completion_accepts_only_the_revision_pinned_in_job_metadata():
    assert _workflow_completion_revision({"metrics": {"workflow_completion_revision": 6}}) == 6
    assert _workflow_completion_revision({"metrics": {"workflow_completion_revision": "6"}}) is None
    assert _workflow_completion_revision({"metrics": {}}) is None


def test_missing_persisted_workflow_revision_is_a_stop_not_an_initial_value_fallback():
    workflow = SimpleNamespace(revision=None)

    with pytest.raises(RuntimeError, match="workflow_revision_missing"):
        order_workflow_v2_service._workflow_revision(workflow)


def test_nested_same_revision_resets_each_context_token_once():
    @order_workflow_v2_service.workflow_command
    def inner(order_id):
        assert order_workflow_v2_service._expected_workflow_revision.get() == 4
        return {"order_id": order_id}, None

    @order_workflow_v2_service.workflow_command
    def outer(order_id):
        return inner(order_id, expected_revision=4)

    payload, error = outer("ORD-nested", expected_revision=4)

    assert error is None
    assert payload["order_id"] == "ORD-nested"
    assert order_workflow_v2_service._expected_workflow_revision.get() is None


def test_nested_different_revision_returns_conflict_and_cleans_up_context():
    @order_workflow_v2_service.workflow_command
    def inner(order_id):
        raise order_workflow_v2_service._WorkflowRevisionError("workflow_revision_conflict")

    @order_workflow_v2_service.workflow_command
    def outer(order_id):
        return inner(order_id, expected_revision=3)

    _payload, error = outer("ORD-nested-conflict", expected_revision=4)

    assert error == "workflow_revision_conflict"
    assert order_workflow_v2_service._expected_workflow_revision.get() is None


def test_manual_command_rejects_missing_revision_before_workflow_creation():
    result, error = order_workflow_v2_service.record_context_suggestion(
        order_id="ORD-missing-revision",
        expected_revision=None,
        suggestion={"source": "test"},
    )

    assert result is None
    assert error == "workflow_revision_required"


def test_pre_save_status_for_uninitialized_workflow_is_a_read_projection():
    status = order_workflow_v2_service._pre_save_status_for_sheet({}, "sheet-digest")

    assert status["sheet_hash"] == "sheet-digest"
    assert status["checks"] == {}


def test_migration_0029_only_initializes_when_adding_the_revision_column():
    source = (
        Path(__file__).resolve().parents[2] / "migrations" / "0029_order_workflow_state_revision.py"
    ).read_text(encoding="utf-8")

    assert "added_revision_column = column is None" in source
    assert "if not added_revision_column:" in source
    assert "WHERE revision IS NULL OR revision < 1" in source
    assert "UPDATE order_workflow_states SET revision = 1" not in source


@pytest.mark.parametrize(
    "command_name",
    [
        "ensure_ocr_prerequisites",
        "save_quad_review_decision",
        "save_header_axis_review_decision",
        "save_row_axis_review_decision",
        "record_context_suggestion",
        "confirm_context",
        "mark_ocr_run_queued",
        "mark_ocr_run_completed",
        "set_expanded_cell_copy_mode",
        "select_ocr_result",
        "delete_ocr_result",
        "save_facility_template_columns",
        "save_sheet",
        "confirm_sheet_review",
        "start_sheet_auto_edit_job",
        "run_bagging",
        "run_sheet_anomaly_review",
        "dismiss_sheet_anomaly_warning",
        "confirm_bagging",
        "prepare_output_review",
        "final_confirm",
    ],
)
def test_every_public_workflow_mutator_finalizes_before_its_success_snapshot(command_name):
    source = Path(order_workflow_v2_service.__file__).read_text(encoding="utf-8")
    command_source = source.split(f"def {command_name}(", 1)[1]
    next_command = command_source.find("\n@workflow_command")
    if next_command >= 0:
        command_source = command_source[:next_command]

    assert "_finalize_workflow_mutation(session," in command_source


def test_queue_callers_dispatch_the_queue_captured_revision_and_attempt():
    api_source = (
        Path(order_workflow_v2_service.__file__).parents[1] / "api" / "orders.py"
    ).read_text(encoding="utf-8")

    assert "_dispatch_workflow_ocr_task(" in api_source
    assert 'expected_revision=queued_workflow["revision"]' in api_source
    assert 'workflow_attempt_id=queued_job["workflow_attempt_id"]' in api_source


def test_reparse_blocks_implicit_cross_provider_fallback_but_keeps_same_provider_retry():
    source = Path(order_workflow_v2_service.__file__).parents[1].joinpath(
        "services", "order_service.py"
    ).read_text(encoding="utf-8")

    assert "provider_fallback_blocked = True" in source
    assert "provider_fallback_blocked" in source
    assert "ocr_provider=auto_provider" not in source
    assert "ocr_provider=main_provider" in source


def test_workflow_progress_strips_terminal_updates_before_attempt_write():
    source = Path(order_workflow_v2_service.__file__).parents[1].joinpath(
        "services", "order_service.py"
    ).read_text(encoding="utf-8")
    job_source = Path(order_workflow_v2_service.__file__).parents[1].joinpath(
        "services", "ocr_job_service.py"
    ).read_text(encoding="utf-8")

    assert 'updates.pop("status", None)' in source
    assert 'updates.pop("error_message", None)' in source
    assert "allow_terminal_metadata: bool = False" in job_source
    assert "if not allow_terminal_metadata:" in job_source


def test_ocr_completion_success_requires_the_shared_captured_revision_and_attempt_guard():
    source = Path(order_workflow_v2_service.__file__).read_text(encoding="utf-8")
    completion = source.split("def mark_ocr_run_completed(", 1)[1]

    assert "lock_current_workflow_attempt(" in completion
    assert "expected_workflow_revision=_expected_workflow_revision.get()" in completion
    assert "return _serialize_workflow(workflow, ocr_job=ocr_job), WORKFLOW_REVISION_CONFLICT" in completion


def test_ocr_completion_blockers_terminalize_the_current_attempt_with_revision():
    source = Path(order_workflow_v2_service.__file__).read_text(encoding="utf-8")
    completion = source.split("def mark_ocr_run_completed(", 1)[1]

    assert "def stop_with_blocker(blocker: str)" in completion
    assert 'completion_metrics["workflow_blocker"] = blocker' in completion
    assert 'ocr_job.status = "failed"' in completion
    assert 'completion_metrics["workflow_result_revision"] = _workflow_revision(workflow)' in completion


def test_ocr_completion_superseded_attempt_returns_before_any_blocker_mutation():
    source = Path(order_workflow_v2_service.__file__).read_text(encoding="utf-8")
    completion = source.split("def mark_ocr_run_completed(", 1)[1]

    guard_index = completion.index("if not lock_current_workflow_attempt(")
    blocker_index = completion.index("def stop_with_blocker(blocker: str)")
    assert guard_index < blocker_index


def test_actual_write_guard_rejects_an_absent_pg_job_instead_of_treating_it_as_no_attempt():
    guard_source = Path(order_workflow_v2_service.__file__).with_name(
        "workflow_attempt_guard_service.py"
    ).read_text(encoding="utf-8")

    assert 'select(OcrJob.id, OcrJob.metrics["workflow_attempt_id"].as_string())' in guard_source
    assert ").one_or_none()" in guard_source
    assert "if current_owner is None:\n            return False" in guard_source


def test_workflow_bootstrap_maps_only_the_exact_order_id_primary_key_duplicate_to_conflict():
    source = Path(order_workflow_v2_service.__file__).read_text(encoding="utf-8")
    bootstrap = source.split("def _get_or_create_workflow(", 1)[1].split(
        "def _get_workflow(", 1
    )[0]

    assert '"unique constraint failed: order_workflow_states.order_id"' in bootstrap
    assert 'unique constraint "order_workflow_states_pkey"' in bootstrap
    assert '"key (order_id)="' in bootstrap
    assert '"unique" in str(exc).lower()' not in bootstrap


def test_queue_claims_workflow_before_reserving_the_reusable_job_and_finalizes_blockers():
    source = Path(order_workflow_v2_service.__file__).read_text(encoding="utf-8")
    queue = source.split("def mark_ocr_run_queued(", 1)[1].split(
        "def mark_ocr_run_completed(", 1
    )[0]

    assert queue.index("lock_current_workflow_revision(") < queue.index(
        "ocr_job_service.reserve_workflow_reparse_job("
    )
    assert "def finalize_queue_blocker(blocker: str" in queue
    assert "terminal_metrics[\"workflow_blocker\"] = blocker" in queue
    assert "return finalize_queue_blocker(lineage_error, ocr_job)" in queue


def test_shared_attempt_guard_claims_workflow_before_job_on_both_database_backends():
    guard_source = Path(order_workflow_v2_service.__file__).with_name(
        "workflow_attempt_guard_service.py"
    ).read_text(encoding="utf-8")
    attempt_guard = guard_source.split("def lock_current_workflow_attempt(", 1)[1].split(
        "def lock_actual_write_ownership(", 1
    )[0]

    assert attempt_guard.index("lock_current_workflow_revision(") < attempt_guard.index(
        "job_claim = session.execute("
    )
    assert ".with_for_update()" in attempt_guard


def test_neighboring_select_and_sheet_blocker_paths_finalize_their_own_revision():
    source = Path(order_workflow_v2_service.__file__).read_text(encoding="utf-8")
    select_command = source.split("def select_ocr_result(", 1)[1].split(
        "def delete_ocr_result(", 1
    )[0]
    save_sheet_command = source.split("def save_sheet(", 1)[1].split(
        "def confirm_sheet_review(", 1
    )[0]

    assert "_finalize_workflow_blocker(session, workflow" in select_command
    assert "_finalize_workflow_blocker(session, workflow" in save_sheet_command


def test_reparse_callback_completes_success_failure_and_crash_from_captured_result_only():
    api_source = (
        Path(order_workflow_v2_service.__file__).parents[1] / "api" / "orders.py"
    ).read_text(encoding="utf-8")
    callback = api_source.split("def _run_reparse_background(", 1)[1].split(
        "def _run_ocr_rerun_background(", 1
    )[0]

    assert 'result.get("reparse_evidence_run_id")' in callback
    assert "reparse_evidence_missing" in callback
    assert callback.count("_complete_workflow_ocr_run(") == 2
    assert "get_latest_ocr_evidence_run" not in callback
    assert "expected_revision=expected_revision" in callback
    assert "workflow_attempt_id=workflow_attempt_id" in callback


def test_reparse_result_contract_exposes_only_its_persisted_evidence_id():
    source = Path(order_workflow_v2_service.__file__).parents[1].joinpath(
        "services", "order_service.py"
    ).read_text(encoding="utf-8")

    assert 'serialized["reparse_evidence_run_id"] = reparse_evidence_run_id' in source
    assert 'source="reparse-pipeline-result"' in source
    assert "reparse_evidence_persist_failed" in source
    assert "reparse_cache_persist_failed" in source
    assert "preserve_existing_artifacts=False" in source


@pytest.mark.parametrize(
    ("result", "reparse_error", "crash", "completion_error", "expected_evidence_id", "expected_error"),
    [
        ({"reparse_evidence_run_id": "OEV-success"}, None, None, None, "OEV-success", None),
        (
            {"reparse_evidence_run_id": "OEV-current-error"},
            "current_reparse_error",
            None,
            None,
            "OEV-current-error",
            "current_reparse_error",
        ),
        (
            {"reparse_evidence_run_id": "OEV-superseded"},
            None,
            None,
            order_workflow_v2_service.WORKFLOW_REVISION_CONFLICT,
            "OEV-superseded",
            None,
        ),
        ({}, None, None, None, None, "reparse_evidence_missing"),
        (None, None, KeyboardInterrupt("crash"), None, None, "reparse_crashed:crash"),
    ],
    ids=["success", "current-error", "superseded", "missing-own-result", "crash"],
)
def test_reparse_callback_completes_with_only_its_returned_evidence_and_captured_tokens(
    monkeypatch,
    result,
    reparse_error,
    crash,
    completion_error,
    expected_evidence_id,
    expected_error,
):
    order_id = "ORD-callback-contract"
    ocr_job_id = "OCR-captured-job"
    expected_revision = 17
    workflow_attempt_id = "attempt-captured"
    completion_calls = []

    def fake_reparse(actual_order_id, **kwargs):
        assert actual_order_id == order_id
        assert kwargs["workflow_attempt_id"] == workflow_attempt_id
        assert kwargs["expected_workflow_revision"] == expected_revision
        assert kwargs["ocr_job_id"] == ocr_job_id
        if crash is not None:
            raise crash
        return result, reparse_error

    def fake_complete(actual_order_id, job_id, **kwargs):
        completion_calls.append((actual_order_id, job_id, kwargs))
        return None, completion_error

    monkeypatch.setattr(orders_api.order_service, "reparse_order", fake_reparse)
    monkeypatch.setattr(orders_api, "_complete_workflow_ocr_run", fake_complete)

    orders_api._run_reparse_background(
        order_id,
        "prompt",
        expected_revision=expected_revision,
        workflow_attempt_id=workflow_attempt_id,
        ocr_job_id=ocr_job_id,
    )

    assert len(completion_calls) == 1
    actual_order_id, job_id, kwargs = completion_calls[0]
    assert actual_order_id == order_id
    assert job_id == ocr_job_id
    assert kwargs["expected_revision"] == expected_revision
    assert kwargs["workflow_attempt_id"] == workflow_attempt_id
    assert kwargs.get("evidence_run_id") == expected_evidence_id
    if crash is not None:
        assert kwargs["error"] == expected_error
    else:
        assert kwargs.get("error") == expected_error


def test_cache_actual_write_requires_the_callers_real_job_id_and_keeps_first_pass_explicit():
    source = Path(order_workflow_v2_service.__file__).parents[1].joinpath(
        "services", "order_service.py"
    ).read_text(encoding="utf-8")
    cache = source.split("def _save_order_ocr_cache(", 1)[1].split(
        "def _load_order_ocr_cache(", 1
    )[0]

    assert "normalized_job_id = str(job_id or \"\").strip()" in cache
    assert "if not normalized_job_id:\n        return None" in cache
    assert "_workflow_attempt_write_fence(session, normalized_job_id, order_id)" in cache
    assert "workflow_job_id=normalized_job_id" in cache


def test_confirmed_snapshot_without_immutable_artifacts_is_not_rebuilt_from_current_state():
    snapshot = SimpleNamespace(
        id="OCS-missing-artifacts",
        order_id="ORD-confirmed",
        snapshot_json={},
    )

    class Session:
        def get(self, _model, identity):
            return snapshot if identity == snapshot.id else None

    workflow = SimpleNamespace(confirmed_snapshot_id=snapshot.id, draft_id="ODS-current")
    order = SimpleNamespace(id="ORD-confirmed")
    meta = {"bagging_result": None, "output_bundle": None}

    result = order_workflow_v2_service._recover_confirmed_artifacts_from_snapshot(
        order=order,
        workflow=workflow,
        meta=meta,
        session=Session(),
    )

    assert result == meta
    assert "bagging_result_id" not in result
    assert "output_bundle_id" not in result
