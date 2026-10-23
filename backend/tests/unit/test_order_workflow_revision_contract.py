from types import SimpleNamespace
from pathlib import Path
import ast

import pytest
from fastapi import HTTPException
from sqlalchemy.orm.exc import StaleDataError

from src.api import orders as orders_api
from src.api.orders import _require_workflow_if_match, _workflow_completion_revision
from src.services import order_service
from src.services import order_workflow_v2_service


def _source_function(path, name):
    return next(node for node in ast.parse(Path(path).read_text(encoding="utf-8")).body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)


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
        info = {}
        rolled_back = False

        def flush(self):
            raise StaleDataError("UPDATE matched zero rows")

        def rollback(self):
            self.rolled_back = True

    workflow = SimpleNamespace(revision=4, secondary_actions_json={})
    session = ConcurrentSession()

    assert (
        order_workflow_v2_service._finalize_workflow_mutation(session, workflow)
        == "workflow_revision_conflict"
    )
    assert session.rolled_back is True


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
    meta = {}
    status = order_workflow_v2_service._pre_save_status_for_sheet(meta, "sheet-digest")

    assert status["sheet_hash"] == "sheet-digest"
    assert status["anomaly_review_confirmed"] is False
    assert status["sheet_review_confirmed"] is False
    assert status["ready"] is False
    assert meta == {}


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
    if command_name in {"confirm_bagging", "prepare_output_review"}:
        command = _source_function(order_workflow_v2_service.__file__, command_name)
        assert [ast.unparse(node) for node in command.decorator_list] == ["workflow_command"]
        assert [ast.unparse(node) for node in command.body] == ["return _prepare_output_review(order_id)"]
        shared = _source_function(order_workflow_v2_service.__file__, "_prepare_output_review")
        assert shared.decorator_list == []
        command_source = ast.get_source_segment(source, shared)
        statements = shared.body[0].body
        finalizer = next(index for index, node in enumerate(statements)
                         if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                         and ast.unparse(node.value.func) == "_finalize_workflow_mutation")
        assert isinstance(statements[-1], ast.Return)
        assert ast.unparse(statements[-1].value.elts[-1]) == "None"
        assert finalizer < len(statements) - 1
        assert "_serialize_workflow(workflow)" in ast.unparse(statements[-1])
    else:
        command_source = source.split(f"def {command_name}(", 1)[1]
        next_command = command_source.find("\n@workflow_command")
        if next_command >= 0:
            command_source = command_source[:next_command]

    assert "_finalize_workflow_mutation(session," in command_source


def test_queue_callers_dispatch_the_queue_captured_revision_and_attempt():
    api_source = (
        Path(order_workflow_v2_service.__file__).parents[1] / "api" / "orders.py"
    ).read_text(encoding="utf-8")

    api_tree = ast.parse(api_source)
    dispatches = [node for node in ast.walk(api_tree) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name) and node.func.id == "_dispatch_workflow_ocr_delivery"]
    assert len(dispatches) == 2
    assert all(ast.unparse(node.args[0]) == "queued.get('delivery')" for node in dispatches)
    queue = _source_function(order_workflow_v2_service.__file__, "mark_ocr_run_queued")
    intent = next(node for node in ast.walk(queue) if isinstance(node, ast.Call)
                  and ast.unparse(node.func) == "workflow_ocr_delivery_service.create_intent_in_session")
    keywords = {keyword.arg: ast.unparse(keyword.value) for keyword in intent.keywords}
    assert keywords["order_id"] == "order.id"
    assert keywords["job_id"] == "normalized_job_id"
    assert keywords["workflow_revision"] == "_workflow_revision(workflow)"
    assert keywords["workflow_attempt_id"] == "workflow_attempt_id or ''"
    assert keywords["selected_pdf_receipt"] == "queued_metrics.get('selected_pdf_receipt') or {}"
    assert keywords["request_identity"] == "delivery_request_identity or {}"
    identity = next(node.value for node in ast.walk(queue) if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "delivery_request_identity" for target in node.targets))
    fields = {key.value: ast.unparse(value) for key, value in zip(identity.keys, identity.values)}
    assert fields["selected_document_id"] == "delivery_selected_document_id"
    assert fields["selected_version_input"] == "selected_input"
    missing = next(node for node in queue.body if isinstance(node, ast.If)
                   and "input_reference" in ast.unparse(node.test))
    assert isinstance(missing.body[0], ast.Return)
    assert "workflow_ocr_delivery_input_reference_required" in ast.unparse(missing.body[0])
    assert queue.body.index(missing) < next(i for i, node in enumerate(queue.body) if isinstance(node, ast.With))
    dispatch = _source_function(Path(order_workflow_v2_service.__file__).parents[1] / "api" / "orders.py", "_dispatch_workflow_ocr_delivery")
    invalid = next(node for node in dispatch.body if isinstance(node, ast.If))
    assert ast.unparse(invalid.test) == "not delivery_id or type(generation) is not int"
    assert isinstance(invalid.body[0], ast.Raise)
    assert "workflow_ocr_delivery_missing" in ast.unparse(invalid.body[0])
    assert dispatch.body.index(invalid) < next(i for i, node in enumerate(dispatch.body) if isinstance(node, ast.Try))


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
    assert "select(OcrJob.id).where(*job_conditions).with_for_update(read=read_only)" in attempt_guard
    assert 'if session.bind.dialect.name == "sqlite" and not read_only:' in attempt_guard
    assert "update(OcrJob)\n            .where(*job_conditions)\n            .values(status=OcrJob.status)" in attempt_guard
    assert "if job_claim.rowcount != 1:\n            return False" in attempt_guard


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

    tree = ast.parse("def _run_reparse_background(" + callback)
    function = tree.body[0]
    completions = [node for node in ast.walk(function) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Name) and node.func.id == "_complete_workflow_ocr_run"]
    assert len(completions) == 1
    keywords = {keyword.arg: ast.unparse(keyword.value) for keyword in completions[0].keywords}
    assert keywords["evidence_run_id"] == "(result or {}).get('reparse_evidence_run_id')"
    assert keywords["expected_revision"] == "expected_revision"
    assert keywords["workflow_attempt_id"] == "workflow_attempt_id"
    assert keywords["error"] == "error"
    receipt_branch = next(node for node in function.body if isinstance(node, ast.If)
                          and ast.unparse(node.test) == "isinstance(receipt, dict)")
    assert isinstance(receipt_branch.body[-1], ast.Return)
    assert ast.unparse(receipt_branch.body[-1].value) == "{'terminal_receipt': receipt}"
    receipt_guard = receipt_branch.body[0]
    assert isinstance(receipt_guard, ast.If)
    for field, owner in (
        ("job_id", "ocr_job_id"), ("order_id", "order_id"),
        ("workflow_attempt_id", "workflow_attempt_id"), ("workflow_revision", "expected_revision"),
    ):
        assert f"receipt.get('{field}') != {owner}" in ast.unparse(receipt_guard.test)
    assert "receipt.get('status') not in {'done', 'failed'}" in ast.unparse(receipt_guard.test)
    assert isinstance(receipt_guard.body[0], ast.Raise)
    assert "reparse_terminal_receipt_mismatch" in ast.unparse(receipt_guard)
    missing_branch = next(node for node in function.body if isinstance(node, ast.If)
                          and ast.unparse(node.test) == "not error")
    assert isinstance(missing_branch.body[0], ast.Raise)
    assert "reparse_persisted_result_missing" in ast.unparse(missing_branch)
    assert function.body.index(receipt_branch) < function.body.index(missing_branch)
    refusal = next(node for node in function.body if isinstance(node, ast.If)
                   and ast.unparse(node.test) == "receipt is None")
    assert isinstance(refusal.body[0], ast.Raise)
    assert "completion_error or 'reparse_terminal_persist_refused'" in ast.unparse(refusal)
    assert not any(isinstance(node, ast.Try) for node in ast.walk(function))
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
    assert "preserve_existing_artifacts" not in source
    assert "_preserve_hakodate_artifacts_if_missing" not in source


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
        ({}, None, None, None, None, "reparse_persisted_result_missing"),
        (None, None, KeyboardInterrupt("crash"), None, None, "crash"),
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
    reparse_calls = []
    receipt = {
        "order_id": order_id, "job_id": ocr_job_id,
        "workflow_revision": expected_revision, "workflow_attempt_id": workflow_attempt_id,
        "evidence_run_id": expected_evidence_id,
        "status": "failed" if reparse_error else "done", "error": reparse_error,
    }

    def fake_reparse(actual_order_id, **kwargs):
        assert actual_order_id == order_id
        assert kwargs["workflow_attempt_id"] == workflow_attempt_id
        assert kwargs["expected_workflow_revision"] == expected_revision
        assert kwargs["ocr_job_id"] == ocr_job_id
        reparse_calls.append((actual_order_id, kwargs))
        if crash is not None:
            raise crash
        if completion_error is not None:
            return result, completion_error
        if expected_evidence_id is not None:
            return {**result, "terminal_receipt": receipt}, reparse_error
        return result, reparse_error

    def fake_complete(actual_order_id, job_id, **kwargs):
        completion_calls.append((actual_order_id, job_id, kwargs))
        return None, completion_error

    monkeypatch.setattr(orders_api.order_service, "reparse_order", fake_reparse)
    monkeypatch.setattr(orders_api, "_complete_workflow_ocr_run", fake_complete)

    def run():
        return orders_api._run_reparse_background(
            order_id, "prompt", expected_revision=expected_revision,
            workflow_attempt_id=workflow_attempt_id, ocr_job_id=ocr_job_id,
        )

    if crash is not None:
        with pytest.raises(KeyboardInterrupt, match=expected_error) as error:
            run()
        assert error.value is crash
        assert completion_calls == []
    elif completion_error is not None:
        with pytest.raises(ValueError, match=completion_error):
            run()
        assert len(completion_calls) == 1
        actual_order_id, job_id, kwargs = completion_calls[0]
        assert actual_order_id == order_id
        assert job_id == ocr_job_id
        assert kwargs["expected_revision"] == expected_revision
        assert kwargs["workflow_attempt_id"] == workflow_attempt_id
        assert kwargs.get("evidence_run_id") == expected_evidence_id
        assert kwargs["error"] == completion_error
    elif expected_evidence_id is None:
        with pytest.raises(ValueError, match=expected_error):
            run()
        assert completion_calls == []
    else:
        returned = run()
        assert returned == {"terminal_receipt": receipt}
        assert returned["terminal_receipt"] is receipt
        assert receipt["order_id"] == order_id
        assert receipt["job_id"] == ocr_job_id
        assert receipt["workflow_revision"] == expected_revision
        assert receipt["workflow_attempt_id"] == workflow_attempt_id
        assert receipt["evidence_run_id"] == expected_evidence_id
        assert receipt["error"] == expected_error
        assert completion_calls == []
    assert len(reparse_calls) == 1


@pytest.mark.parametrize("field,value", [
    ("order_id", "ORD-foreign"), ("job_id", "OCR-foreign"),
    ("workflow_revision", 18), ("workflow_attempt_id", "attempt-foreign"),
    ("status", "running"),
])
def test_reparse_callback_rejects_foreign_terminal_receipt(monkeypatch, field, value):
    receipt = {
        "order_id": "ORD-callback-contract", "job_id": "OCR-captured-job",
        "workflow_revision": 17, "workflow_attempt_id": "attempt-captured",
        "evidence_run_id": "OEV-success", "status": "done", "error": None,
        field: value,
    }
    monkeypatch.setattr(orders_api.order_service, "reparse_order",
                        lambda *args, **kwargs: ({"terminal_receipt": receipt}, None))
    def forbidden(*args, **kwargs):
        pytest.fail("foreign receipt must not be repaired using other evidence")
    monkeypatch.setattr(orders_api, "_complete_workflow_ocr_run", forbidden)
    monkeypatch.setattr(orders_api.order_service, "get_latest_ocr_evidence_run", forbidden)
    with pytest.raises(ValueError, match="reparse_terminal_receipt_mismatch"):
        orders_api._run_reparse_background(
            "ORD-callback-contract", "prompt", expected_revision=17,
            workflow_attempt_id="attempt-captured", ocr_job_id="OCR-captured-job",
        )


def test_cache_actual_write_requires_the_callers_real_job_id_and_keeps_first_pass_explicit():
    source = Path(order_workflow_v2_service.__file__).parents[1].joinpath(
        "services", "order_service.py"
    ).read_text(encoding="utf-8")
    cache = source.split("def _save_order_ocr_cache(", 1)[1].split(
        "def _load_order_ocr_cache(", 1
    )[0]

    assert "normalized_job_id = str(job_id or \"\").strip()" in cache
    function = _source_function(order_service.__file__, "_save_order_ocr_cache")
    guards = {ast.unparse(node.test): node for node in function.body if isinstance(node, ast.If)}
    for condition in (
        "normalized_job_id and synchronous_write_input is not None",
        "not normalized_job_id and (not isinstance(synchronous_write_input, dict))",
    ):
        guard = guards[condition]
        assert isinstance(guard.body[0], ast.Return)
        assert isinstance(guard.body[0].value, ast.Constant) and guard.body[0].value.value is None
    synchronous = guards["not normalized_job_id"]
    assert ast.unparse(synchronous.body[0]) == "persist_evidence = False"
    fence = next(node for node in ast.walk(function) if isinstance(node, ast.If)
                 and ast.unparse(node.test) == "normalized_job_id"
                 and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                         and child.func.id == "_workflow_attempt_write_fence" for child in ast.walk(node)))
    assert ast.unparse(fence.body[0].value) == "_workflow_attempt_write_fence(session, normalized_job_id, order_id)"
    assert ast.unparse(fence.orelse[0].value) == "_synchronous_cache_write_fence(session, order_id, synchronous_write_input)"
    refusal = next(node for node in ast.walk(function) if isinstance(node, ast.If)
                   and ast.unparse(node.test) == "not write_allowed")
    assert isinstance(refusal.body[0], ast.Return) and refusal.body[0].value.value is None
    sync_fence = _source_function(order_service.__file__, "_synchronous_cache_write_fence")
    assert "captured_processing_config_matches" in ast.unparse(sync_fence)
    assert "lock_current_synchronous_cache_write" in ast.unparse(sync_fence)
    config_guard = next(node for node in sync_fence.body if isinstance(node, ast.If)
                        and "captured_processing_config_matches" in ast.unparse(node.test))
    assert isinstance(config_guard.body[0], ast.Return)
    assert config_guard.body[0].value.value is False
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
