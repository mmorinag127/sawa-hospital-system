"""Synthetic, local-only tests for the Actions diagnostic safety boundary."""
from contextlib import contextmanager
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("demand_parity", ROOT / "scripts/verify_workflow_demand_parity.py")
parity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parity)


def native_fixture():
    owned = dict.fromkeys(parity.FIELDS)
    owned.update(order={"id": "O"}, document={"id": "D"}, configured_input={"private": "secret"},
        physical_menu_entries=[{"quantity": 7}], workflow={}, workflow_revision=3)
    captured = {"order_id": "O", "orders": {"O": owned}}
    current = deepcopy(owned)
    start = deepcopy(owned)
    calls = []
    @contextmanager
    def execution(value):
        assert value is captured
        yield
    def read(session, order, **kwargs):
        calls.append(kwargs)
        return current
    guard = SimpleNamespace(nonworkflow_execution_context=execution,
        processing_start_read_input=lambda: start, _order_execution_input=read)
    return owned, current, start, SimpleNamespace(id="O"), SimpleNamespace(order_id="O",
        metrics={"nonworkflow_execution": captured}), guard, calls


def test_native_all_14_and_start_two_equal_without_locks():
    owned, current, start, order, job, guard, calls = native_fixture()
    result = parity.capture_native(object(), order, job, guard)
    assert result["status"] == "captured"
    assert result["compared_start_fields"] == 2 and result["compared_current_fields"] == 14
    assert result["owned_current"] == result["start_owned"] == []
    assert result["native_input_changed_predicate"] is False
    assert calls == [{"lock": False, "original_input": owned}]


@pytest.mark.parametrize("key", parity.FIELDS)
def test_native_each_mismatch_reports_only_key_and_hash(key):
    owned, current, start, order, job, guard, _ = native_fixture()
    current[key] = {"secret-current-value": 99}
    result = parity.capture_native(None, order, job, guard)
    assert result["stop"] == "_native_owner.owned_current"
    assert [v["key"] for v in result["owned_current"]] == [key]
    assert set(result["owned_current"][0]) == {"key", "left_sha256", "right_sha256"}
    assert "secret-current-value" not in json.dumps(result) and '"private"' not in json.dumps(result)


@pytest.mark.parametrize("key", parity.START_FIELDS)
def test_start_stop_precedes_current_mismatch(key):
    _, current, start, order, job, guard, _ = native_fixture()
    start[key] = "different"
    current["lines"] = [1]
    result = parity.capture_native(None, order, job, guard)
    assert result["stop"] == "_native_owner.start_owned"
    assert result["start_owned"][0]["key"] == key
    assert result["owned_current"][0]["key"] == "lines"


def test_guard_failure_is_unverified_not_native_success():
    _, _, _, order, job, guard, _ = native_fixture()
    def fail(*a, **kw):
        raise ValueError("raw private error")
    guard._order_execution_input = fail
    result = parity.capture_native(None, order, job, guard)
    assert result["status"] == "unverified" and result["stop"] == "_order_execution_input"
    assert "raw private" not in json.dumps(result)


@pytest.mark.parametrize("mutation", ["missing", "extra", "foreign", "nojob"])
def test_native_ambiguous_shape_fails_closed(mutation):
    owned, _, _, order, job, guard, _ = native_fixture()
    if mutation == "missing":
        owned.pop("lines")
    elif mutation == "extra":
        owned["initial_saved_import"] = {}
    elif mutation == "foreign":
        job.order_id = "other"
    else:
        job = None
    assert parity.capture_native(None, order, job, guard)["status"] == "unverified"


@pytest.mark.parametrize("sql", ["UPDATE orders SET id='x'", "DELETE FROM orders", "CREATE TEMP TABLE t(a int)",
    "SELECT * FROM orders FOR UPDATE", "SELECT * FROM orders FOR NO KEY UPDATE", "SELECT * FROM orders FOR SHARE",
    "SELECT * FROM orders FOR KEY SHARE", "WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x",
    "SELECT 1; SELECT 2", "SELECT * INTO TEMP t FROM orders", "COMMIT", "SET transaction_read_only=off"])
def test_forbidden_sql(sql):
    with pytest.raises(parity.Blocked, match="parity_sql_forbidden"):
        parity.reject_sql(None, None, sql, None, None, False)


@pytest.mark.parametrize("sql", ["SELECT orders.id FROM orders WHERE orders.id = %(id)s", "SHOW transaction_read_only"])
def test_read_sql_allowed(sql):
    parity.reject_sql(None, None, sql, None, None, False)


def test_actions_gate_before_command(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(parity.diagnostic, "command", lambda *a: pytest.fail("command before context gate"))
    with pytest.raises(parity.Blocked, match="context_invalid"):
        parity.context(ROOT)


def test_extracts_old_candidates_into_same_namespace():
    source = '''
def _project_ocr_candidates(): return "old"
def get_inspection(): return _project_ocr_candidates()
def get_workflow_readonly(): return "workflow"
'''
    old = parity.old_readers(source, {"_project_ocr_candidates": lambda: "new"})
    assert old["get_inspection"]() == "old"
    with pytest.raises(parity.Blocked):
        parity.old_readers("def get_inspection(): pass", {})


@pytest.mark.parametrize("left,right", [(1, True), (1, 1.0), ({}, {"new": None}), ([1, 2], [2, 1]),
    ({"blockers": []}, {"blockers": ["menu_amount_unresolved"]})])
def test_no_value_normalization(left, right):
    assert parity.compare_values(left, right)["equal"] is False


def test_detail_materialization_preserves_candidate_blockers():
    summary = {"order_id": "O", "workflow": {"revision": 2}, "ocr_results": [
        {"order_id": "O", "ocr_result_id": "E", "detail_state": "not_loaded"}],
        "ocr_candidates": {"blockers": ["menu"], "results": [{"ocr_result_id": "E"}]}}
    detail = {"order_id": "O", "ocr_result_id": "E", "workflow_revision": 2,
        "quantity": 7, "warnings": ["w"], "selectable": False}
    result = parity.materialize(summary, lambda *a: (detail, None))
    assert result["ocr_results"][0]["quantity"] == 7
    assert result["ocr_candidates"]["blockers"] == ["menu"]
    assert "detail_state" in summary["ocr_results"][0]
    detail["workflow_revision"] = 3
    with pytest.raises(parity.Blocked, match="revision_changed"):
        parity.materialize(summary, lambda *a: (detail, None))


def test_readonly_requires_postgres():
    connection = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    with pytest.raises(parity.Blocked, match="postgres_required"):
        with parity.readonly_session(connection):
            pytest.fail("must not yield")


@pytest.mark.parametrize("values,code", [(["off"], "db_not_readonly"),
    (["on", "read committed"], "isolation_changed"), (["on", "repeatable read", ("prod", "orders_app")], "database_target_changed")])
def test_readonly_transaction_gates(values, code):
    answers = iter(values)
    def execute(statement):
        answer = next(answers)
        return SimpleNamespace(scalar_one=lambda: answer, one=lambda: answer)
    connection = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"), execute=execute)
    with pytest.raises(parity.Blocked, match=code):
        with parity.readonly_session(connection):
            pytest.fail("must not yield")


def test_candidate_detail_mismatch_not_overwritten():
    summary = {"order_id": "O", "workflow": {"revision": 2}, "ocr_results": [
        {"order_id": "O", "ocr_result_id": "E", "detail_state": "not_loaded"}],
        "ocr_candidates": {"results": [{"ocr_result_id": "E", "quantity": 8}]}}
    detail = {"order_id": "O", "ocr_result_id": "E", "workflow_revision": 2, "quantity": 7}
    with pytest.raises(parity.Blocked, match="candidate_detail_changed"):
        parity.materialize(summary, lambda *a: (detail, None))


def test_function_comparison_uses_old_workflow_and_retains_candidate_difference():
    workflow = {"revision": 2, "state": "ocr_selected", "ocr_job": None, "common_job_status": None}
    old = {"order_id": "O", "workflow": {"legacy": True}, "ocr_results": [],
        "ocr_candidates": {"results": [], "blockers": []}}
    new = deepcopy(old)
    new["workflow"] = workflow
    progress = {"order_id": "O", "workflow_revision": 2, "state": "ocr_selected", "ocr_job": None,
        "common_job_status": None}
    baseline = {"get_inspection": lambda *a, **kw: (old, None),
        "get_workflow_readonly": lambda *a: (workflow, None)}
    service = SimpleNamespace(get_inspection=lambda *a, **kw: (new, None),
        get_inspection_detail=lambda *a: pytest.fail("no detail required"), get_ocr_progress=lambda *a: (progress, None))
    assert parity.compare_reads(service, baseline, "O")["equal"] is True
    new["ocr_candidates"]["blockers"] = ["menu_amount_unresolved"]
    report = parity.compare_reads(service, baseline, "O")
    assert report["equal"] is False
    assert report["components"]["ocr_candidates"]["equal"] is False
    assert old["workflow"] == {"legacy": True}


def test_equal_errors_not_counted_as_success():
    baseline = {"get_inspection": lambda *a, **kw: (None, "blocked"),
        "get_workflow_readonly": lambda *a: (None, "blocked")}
    service = SimpleNamespace(get_inspection=lambda *a, **kw: (None, "blocked"))
    assert parity.compare_reads(service, baseline, "O")["status"] == "unverified"


def actions_env(monkeypatch):
    values = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": parity.diagnostic.REPOSITORY,
        "GITHUB_REPOSITORY_OWNER": "mmorinag127", "GITHUB_ACTOR": "mmorinag127",
        "GITHUB_TRIGGERING_ACTOR": "mmorinag127", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/develop", "DIAGNOSTIC_ENVIRONMENT": "staging",
        "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "a" * 40}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT", raising=False)
    return values


@pytest.mark.parametrize("key", ["GITHUB_ACTIONS", "GITHUB_REPOSITORY", "GITHUB_REPOSITORY_OWNER", "GITHUB_ACTOR",
    "GITHUB_TRIGGERING_ACTOR", "GITHUB_EVENT_NAME", "GITHUB_REF", "DIAGNOSTIC_ENVIRONMENT", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"])
def test_context_rejects_each_gate(monkeypatch, key):
    actions_env(monkeypatch)
    monkeypatch.setenv(key, "invalid")
    with pytest.raises(parity.Blocked):
        parity.context(ROOT)


def test_context_pins_both_checkout_sources(monkeypatch):
    actions_env(monkeypatch)
    monkeypatch.setattr(parity.diagnostic, "command", lambda args:
        ("a" * 40).encode() if "rev-parse" in args else b"")
    assert parity.context(ROOT) == "a" * 40
    monkeypatch.setattr(parity.diagnostic, "command", lambda args: ("b" * 40).encode())
    with pytest.raises(parity.Blocked, match="source_changed"):
        parity.context(ROOT)


def test_cli_cleanup_uses_existing_owner_guard(monkeypatch, tmp_path):
    actions_env(monkeypatch)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(parity, "context", lambda product: "a" * 40)
    private = tmp_path / "demand-parity-123-1"
    private.mkdir(mode=0o700)
    parity.diagnostic.write_json(private / "owner.json", parity.diagnostic.marker())
    (private / "raw").write_text("private")
    monkeypatch.setattr("sys.argv", ["verify", "--product", str(ROOT), "--cleanup"])
    assert parity.main() == 0
    assert not private.exists()


def test_private_cleanup_rejects_foreign_owner(monkeypatch, tmp_path):
    actions_env(monkeypatch)
    parity.diagnostic.write_json(tmp_path / "owner.json", {"GITHUB_RUN_ID": "different"})
    with pytest.raises(parity.Blocked, match="cleanup_owner_changed"):
        parity.diagnostic.cleanup(tmp_path)
    assert tmp_path.exists()


def test_external_input_attempt_retained_even_when_caller_swallows():
    from src.services import storage_service
    service = SimpleNamespace(session_scope=None,
        _get_order_service_module=lambda: SimpleNamespace(_signed_url_from_uri=lambda uri: "unused"))
    with parity.isolated_reads(service, object()) as blocked:
        try:
            storage_service.load_bytes_from_uri("gs://private/input", verification=True)
        except parity.Blocked:
            pass
        assert blocked == ["external_input_capture_failed"]


@pytest.mark.parametrize("failure", [False, True])
def test_cli_cleans_private_logs_on_success_and_failure(monkeypatch, tmp_path, failure):
    actions_env(monkeypatch)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(parity, "context", lambda product: "a" * 40)
    monkeypatch.setattr(parity.diagnostic, "command", lambda args: b"")
    @contextmanager
    def fake_scope(*args):
        yield object()
    monkeypatch.setattr(parity, "staging_connection", fake_scope)
    monkeypatch.setattr(parity, "readonly_session", fake_scope)
    def capture(*args):
        print("private raw data")
        if failure:
            raise RuntimeError("secret password")
        return [{"function_parity": {"status": "unverified"}, "native_input": {"status": "captured"}}]
    monkeypatch.setattr(parity, "capture", capture)
    proxy = tmp_path / "proxy"
    proxy.touch()
    summary = tmp_path / "summary"
    monkeypatch.setattr("sys.argv", ["verify", "--product", str(ROOT), "--proxy", str(proxy), "--summary", str(summary)])
    assert parity.main() == 2
    assert not (tmp_path / "demand-parity-123-1").exists()
    if not failure:
        assert "private raw data" not in (summary / "comparison.json").read_text()


def test_real_baseline_extraction_uses_all_three_fixed_functions():
    source = parity.diagnostic.command(["git", "-C", str(ROOT), "show", parity.BASE + ":" + parity.SOURCE]).decode()
    from src.services import order_workflow_v2_service as service
    baseline = parity.old_readers(source, vars(service))
    assert baseline["get_inspection"].__globals__["_project_ocr_candidates"] is baseline["_project_ocr_candidates"]
    assert baseline["_project_ocr_candidates"] is not service._project_ocr_candidates


@pytest.mark.parametrize("uri", ["gs://production/a", "file:///tmp/a", "/tmp/a", "https://storage.googleapis.com/a",
    "gs://sawahospitalsystem-stg-raw.evil/a", "gs://sawahospitalsystem-stg-raw@evil/a", "gs://sawahospitalsystem-stg-raw/"])
def test_storage_targets_rejected_before_read(uri):
    failures = []
    reader = parity.captured_read("bytes", lambda *a, **kw: pytest.fail("invalid target read"), {}, [], failures)
    with pytest.raises(parity.Blocked):
        reader(uri)
    assert failures == ["external_input_capture_failed"]


@pytest.mark.parametrize("kind,value", [("bytes", b"real private bytes"), ("signed_url", "https://signed/private?secret=x")])
def test_exact_read_reused_and_only_hashes_published(kind, value):
    calls, receipts, failures = [], [], []
    def real(uri, **kwargs):
        calls.append((uri, kwargs))
        return value
    reader = parity.captured_read(kind, real, {}, receipts, failures)
    uri = "gs://sawahospitalsystem-stg-raw/private?generation=1"
    assert reader(uri, verification=True) == value
    assert reader(uri, verification=True) == value
    assert len(calls) == len(receipts) == 1
    assert failures == []
    assert "private" not in json.dumps(receipts)


def test_read_failures_are_never_cached_as_success():
    def fail(*a, **kw):
        raise RuntimeError("private")
    cache, receipts, failures = {}, [], []
    reader = parity.captured_read("bytes", fail, cache, receipts, failures)
    with pytest.raises(RuntimeError):
        reader("gs://sawahospitalsystem-stg-raw/file")
    assert not cache and not receipts and failures


def test_storage_writes_remain_forbidden():
    from src.services import storage_service
    from google.cloud.storage.blob import Blob
    service = SimpleNamespace(session_scope=None,
        _get_order_service_module=lambda: SimpleNamespace(_signed_url_from_uri=lambda uri: "unused"))
    with parity.isolated_reads(service, object()) as blocked:
        with pytest.raises(parity.Blocked):
            storage_service.save_bytes_to_gcs("bucket", "object", b"write")
        with pytest.raises(parity.Blocked):
            Blob("x", None).upload_from_string(b"write")
        assert len(blocked) == 2


def runtime_descriptor():
    values = {"FACILITY_MASTER_URI": "gs://sawahospitalsystem-stg-templates/canonical.json",
        "RAW_BUCKET": "sawahospitalsystem-stg-raw", "OCR_PIPELINE_BUCKET": "sawahospitalsystem-stg-raw",
        "OCR_OUTPUT_BUCKET": "sawahospitalsystem-stg-raw", "TEMPLATE_BUCKET": "sawahospitalsystem-stg-templates"}
    return values, {"spec": {"containers": [{"env": [{"name": k, "value": v} for k, v in values.items()]}]}}


def test_canonical_config_is_live_env_not_local_default(monkeypatch):
    values, descriptor = runtime_descriptor()
    for key in values:
        monkeypatch.setenv(key, "old-test-value")
    monkeypatch.setenv("K_SERVICE", "old-test-service")
    answers = iter([{"status": {"traffic": [{"percent": 100, "revisionName": "worker-stg-001"}]}}, descriptor])
    monkeypatch.setattr(parity.diagnostic, "command", lambda args: json.dumps(next(answers)).encode())
    monkeypatch.setenv("FACILITY_MASTER_PATH", "/tmp/fake")
    monkeypatch.setenv("FACILITY_MASTER_URI", "gs://wrong/default")
    revision, hashes = parity.canonical_environment()
    import os
    assert revision == "worker-stg-001" and set(hashes) == set(values)
    assert os.environ["FACILITY_MASTER_URI"] == values["FACILITY_MASTER_URI"]
    assert "FACILITY_MASTER_PATH" not in os.environ
    assert "canonical.json" not in json.dumps(hashes)


@pytest.mark.parametrize("change", ["missing", "prod", "duplicate", "secret"])
def test_canonical_config_rejects_ambiguous_or_prod(monkeypatch, change):
    _, descriptor = runtime_descriptor()
    entries = descriptor["spec"]["containers"][0]["env"]
    if change == "missing":
        entries.pop(0)
    elif change == "prod":
        entries[0]["value"] = "gs://production/canonical.json"
    elif change == "duplicate":
        entries.append(entries[0])
    else:
        entries[0] = {"name": "FACILITY_MASTER_URI", "valueFrom": {"secret": "ref"}}
    answers = iter([{"status": {"traffic": [{"percent": 100, "revisionName": "worker-stg-001"}]}}, descriptor])
    monkeypatch.setattr(parity.diagnostic, "command", lambda args: json.dumps(next(answers)).encode())
    with pytest.raises(parity.Blocked):
        parity.canonical_environment()


@pytest.mark.parametrize("present", [False, True])
def test_added_deleted_dependency_hashes(monkeypatch, present):
    calls = []
    def command(args):
        calls.append(args)
        return (b"entry" if present else b"") if "ls-tree" in args else b"source"
    monkeypatch.setattr(parity.diagnostic, "command", command)
    result = parity.source_hash(ROOT, parity.BASE, "backend/src/new.py")
    assert result == {"present": present, "sha256": parity.diagnostic.sha(b"source") if present else None}
    assert len(calls) == (2 if present else 1)


def test_sanitized_error_stage_class_and_static_code_only():
    assert parity.failure(parity.Blocked("parity_sql_forbidden"), "capture")["blocked_code"] == "parity_sql_forbidden"
    error = parity.failure(RuntimeError("password=secret gs://raw/path"), "baseline_inspection")
    assert error == {"status": "unverified", "stop": "baseline_inspection", "exception_class": "RuntimeError"}
    assert "blocked_code" not in parity.failure(parity.Blocked("parity_secret_password"), "capture")


def test_read_failure_retains_exact_stage_without_exception_text():
    def fail(*a, **kw):
        raise ValueError("raw secret")
    with pytest.raises(parity.ReadFailure) as raised:
        parity.compare_reads(SimpleNamespace(), {"get_inspection": fail}, "O")
    assert raised.value.summary == {"status": "unverified", "stop": "baseline_inspection", "exception_class": "ValueError"}


def test_import_or_nested_db_connections_cannot_escape():
    from sqlalchemy import create_engine
    engine = create_engine("sqlite:///:memory:")
    with pytest.raises(parity.Blocked, match="extra_database_connection"):
        with parity.no_extra_database_connections():
            try:
                engine.connect()
            except parity.Blocked:
                pass
    engine.dispose()


@pytest.mark.parametrize("code,expected", [("57014", "57014"), ("25P02", "25P02"),
    (None, None), ("57014 secret SQL", None)])
def test_first_db_error_survives_aborted_transaction_and_listener_is_removed(monkeypatch, code, expected):
    import sqlite3
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError
    class QueryCanceled(sqlite3.OperationalError):
        pgcode = code
    class InFailedSqlTransaction(sqlite3.OperationalError):
        pgcode = "25P02"
    engine = create_engine("sqlite:///:memory:")
    report = {}
    with engine.connect() as connection, connection.begin():
        errors = iter([QueryCanceled("secret original SQL"), InFailedSqlTransaction("secret subsequent SQL")])
        def execute(*args, **kwargs):
            raise next(errors)
        monkeypatch.setattr(engine.dialect, "do_execute", execute)
        with parity.first_db_failure(engine, report, lambda: "capture"):
            for stage in ("baseline_inspection", "current_inspection"):
                with pytest.raises(parity.ReadFailure):
                    parity.read_phase(stage, connection.execute, text("SELECT 'private value'"))
            assert report == {"sqlstate": expected, "exception_class": "QueryCanceled", "stop": "baseline_inspection"}
            assert connection.in_transaction()
            assert parity.READ_STAGE.get() is None
        report.clear()
        monkeypatch.setattr(engine.dialect, "do_execute", lambda *a, **kw: (_ for _ in ()).throw(QueryCanceled("secret")))
        with pytest.raises(DBAPIError):
            connection.execute(text("SELECT 1"))
        assert report == {}
    engine.dispose()


def test_diagnostic_timeout_preserves_transaction_options():
    import ast
    tree = ast.parse(Path(parity.__file__).read_text())
    create = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "create_engine")
    options = {item.arg: ast.literal_eval(item.value) for item in create.keywords}
    assert options == {"isolation_level": "REPEATABLE READ",
        "execution_options": {"postgresql_readonly": True},
        "connect_args": {"options": "-c default_transaction_read_only=on -c statement_timeout=60000 -c lock_timeout=1000"}}


@pytest.mark.parametrize("outer_failure", [False, True])
def test_cli_preserves_first_db_failure_without_false_success(monkeypatch, tmp_path, outer_failure):
    actions_env(monkeypatch)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(parity, "context", lambda product: "a" * 40)
    monkeypatch.setattr(parity.diagnostic, "command", lambda args: b"")
    first = {"sqlstate": "57014", "exception_class": "QueryCanceled", "stop": "baseline_inspection"}
    @contextmanager
    def connection(proxy, proof, db_failure, stage):
        assert stage() == "staging_connection"
        db_failure.update(first)
        yield object()
    @contextmanager
    def session(*args):
        yield object()
    def capture(*args):
        if outer_failure:
            raise RuntimeError("25P02 secret SQL and values")
        return [{"function_parity": {"equal": True}, "native_input": {"status": "captured"}}]
    monkeypatch.setattr(parity, "staging_connection", connection)
    monkeypatch.setattr(parity, "readonly_session", session)
    monkeypatch.setattr(parity, "capture", capture)
    proxy = tmp_path / "proxy"
    proxy.touch()
    summary = tmp_path / "summary"
    monkeypatch.setattr("sys.argv", ["verify", "--product", str(ROOT), "--proxy", str(proxy), "--summary", str(summary)])
    assert parity.main() == 2
    output = (summary / ("failure.json" if outer_failure else "comparison.json")).read_text()
    assert json.loads(output)["first_db_failure"] == first
    assert "secret" not in output and "25P02" not in output
    assert not (tmp_path / "demand-parity-123-1").exists()


def test_readonly_session_keeps_external_transaction_and_blocks_commit(monkeypatch):
    from sqlalchemy import create_engine, text
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection, connection.begin():
        original = connection.execute
        def execute(statement, *a, **kw):
            answers = {"SHOW transaction_read_only": "on", "SHOW transaction_isolation": "repeatable read",
                "SELECT current_database(), current_user": ("orders", "orders_app")}
            if str(statement) in answers:
                value = answers[str(statement)]
                return SimpleNamespace(scalar_one=lambda: value, one=lambda: value)
            return original(statement, *a, **kw)
        monkeypatch.setattr(connection, "execute", execute)
        monkeypatch.setattr(connection.dialect, "name", "postgresql")
        with pytest.raises(parity.Blocked, match="readonly_violation"):
            with parity.readonly_session(connection) as session:
                assert session.connection() is connection
                assert session.execute(text("SELECT 1")).scalar_one() == 1
                with pytest.raises(parity.Blocked, match="orm_write_forbidden"):
                    session.commit()
        assert connection.in_transaction()
    engine.dispose()


def test_master_and_artifacts_are_shared_across_comparison_scopes(monkeypatch):
    from src.services import storage_service, canonical_facility_store
    calls = {"bytes": 0, "sign": 0, "master": 0}
    def load(uri, **kwargs):
        calls["bytes"] += 1
        return b"actual captured bytes"
    def sign(uri):
        calls["sign"] += 1
        return "https://signed/actual"
    def master(uri):
        calls["master"] += 1
        return canonical_facility_store.MasterSnapshot({"x": [1]}, "gcs:source:123", uri)
    monkeypatch.setattr(storage_service, "load_bytes_from_uri", load)
    monkeypatch.setattr(canonical_facility_store, "_read_gcs", master)
    order_service = SimpleNamespace(_signed_url_from_uri=sign, load_bytes_from_uri=load)
    service = SimpleNamespace(session_scope=None, _get_order_service_module=lambda: order_service)
    cache, receipts = {}, []
    for _ in range(2):
        with parity.isolated_reads(service, object(), cache, receipts) as blocked:
            assert storage_service.load_bytes_from_uri("gs://sawahospitalsystem-stg-raw/a") == b"actual captured bytes"
            assert order_service._signed_url_from_uri("gs://sawahospitalsystem-stg-raw/a") == "https://signed/actual"
            snapshot = canonical_facility_store._read_gcs("gs://sawahospitalsystem-stg-templates/master")
            assert snapshot.master == {"x": [1]}
            snapshot.master["x"].append(2)
            assert not blocked
    assert calls == {"bytes": 1, "sign": 1, "master": 1}
    assert len(receipts) == 3
    assert "actual" not in json.dumps(receipts)
