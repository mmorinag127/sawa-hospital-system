"""Actions-only staging snapshot diagnostics; no native-owner replay or OCR."""
import argparse
import ast
from contextlib import contextmanager, ExitStack, redirect_stdout, redirect_stderr
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
BASE = "5e30eefee4f5daddc0f97b6c29cbd1ddc5172496"
ORDERS = ("ORD581f55d6", "ORDc69f352e")
SOURCE = "backend/src/services/order_workflow_v2_service.py"
READERS = {"get_inspection", "get_workflow_readonly", "_project_ocr_candidates"}
FIELDS = (
    "order_exists", "configured_input", "original_template_projection",
    "original_template_lineage", "original_template_source_proof", "physical_menu_entries",
    "order", "lines", "workflow_exists", "evidence", "draft", "cache", "document", "decisions",
)
START_FIELDS = ("configured_input", "physical_menu_entries")
BUCKETS = {"sawahospitalsystem-stg-raw", "sawahospitalsystem-stg-templates"}


def load_helper(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diagnostic = load_helper("diagnose_reviewed_ocr")
require = diagnostic.require
Blocked = diagnostic.Blocked
STATIC_CODES = {node.value for path in (Path(__file__), Path(diagnostic.__file__))
    for node in ast.walk(ast.parse(path.read_text())) if isinstance(node, ast.Constant)
    and isinstance(node.value, str) and re.fullmatch(r"(?:parity|diagnostic)_[a-z_]+", node.value)}


def failure(error, stage):
    result = {"status": "unverified", "stop": stage, "exception_class": type(error).__name__}
    if isinstance(error, Blocked) and str(error) in STATIC_CODES:
        result["blocked_code"] = str(error)
    return result


class ReadFailure(Exception):
    def __init__(self, error, stage):
        self.summary = failure(error, stage)


def read_phase(stage, function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Exception as error:
        raise ReadFailure(error, stage) from None


def source_hash(product, ref, path):
    present = bool(diagnostic.command(["git", "-C", str(product), "ls-tree", ref, "--", path]))
    return {"present": present, "sha256": diagnostic.sha(diagnostic.command(
        ["git", "-C", str(product), "show", ref + ":" + path])) if present else None}


@contextmanager
def no_extra_database_connections():
    from sqlalchemy.engine import Engine
    import psycopg2
    attempts = []
    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise Blocked("parity_extra_database_connection")
    with patch.object(Engine, "connect", forbidden), patch.object(Engine, "raw_connection", forbidden), \
            patch.object(psycopg2, "connect", forbidden):
        yield
        require(not attempts, "parity_extra_database_connection")


def context(product):
    env = os.environ
    expected = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": diagnostic.REPOSITORY,
        "GITHUB_REPOSITORY_OWNER": "mmorinag127", "GITHUB_ACTOR": "mmorinag127",
        "GITHUB_TRIGGERING_ACTOR": "mmorinag127", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/develop", "DIAGNOSTIC_ENVIRONMENT": "staging"}
    require(all(env.get(k) == v for k, v in expected.items()), "parity_context_invalid")
    require(not env.get("CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT"), "parity_impersonation_forbidden")
    require(all(re.fullmatch(r"[1-9][0-9]*", env.get(k, "")) for k in
        ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")), "parity_run_identity_invalid")
    sha = env.get("GITHUB_SHA", "")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", sha)), "parity_source_invalid")
    for directory in (ROOT, product):
        require(diagnostic.command(["git", "-C", str(directory), "rev-parse", "HEAD"]).decode().strip() == sha,
            "parity_source_changed")
        require(not diagnostic.command(["git", "-C", str(directory), "status", "--porcelain", "--untracked-files=no"]),
            "parity_source_dirty")
    return sha


def old_readers(source, namespace):
    nodes = [node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name in READERS]
    require({node.name for node in nodes} == READERS, "parity_baseline_functions_missing")
    for node in nodes:
        node.decorator_list = []
    result = dict(namespace)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "fixed-baseline:workflow", "exec"), result)
    return result


def hashes(left, right):
    from src.services.snapshot_serialization import canonical_snapshot_json
    return {name: hashlib.sha256(canonical_snapshot_json(value).encode()).hexdigest()
        for name, value in (("left_sha256", left), ("right_sha256", right))}


def differences(left, right, keys):
    from src.services.snapshot_serialization import snapshots_equal
    return [{"key": key, **hashes(left.get(key), right.get(key))} for key in keys
        if not snapshots_equal(left.get(key), right.get(key))]


def capture_native(session, order, job, guard):
    """Evaluate input predicates only, never bypass or invoke _native_owner."""
    report = {"scope": "input predicates; not exact _native_owner replay", "status": "unverified"}
    metrics = job.metrics if job is not None and isinstance(job.metrics, dict) else {}
    captured = metrics.get("nonworkflow_execution")
    if not isinstance(captured, dict) or captured.get("order_id") != order.id or job.order_id != order.id:
        return {**report, "stop": "native_capture_owner"}
    owned_orders = captured.get("orders")
    owned = owned_orders.get(order.id) if isinstance(owned_orders, dict) else None
    if (not isinstance(owned, dict) or set(captured.get("orders", {})) != {order.id}
            or not isinstance(owned.get("order"), dict) or not isinstance(owned.get("document"), dict)):
        return {**report, "stop": "_native_owner.owned_shape"}
    if set(owned) - {"workflow", "workflow_revision"} != set(FIELDS):
        return {**report, "stop": "native_capture_14_field_contract"}
    try:
        with guard.nonworkflow_execution_context(captured):
            start = guard.processing_start_read_input()
    except Exception as error:
        return {**report, **failure(error, "processing_start_read_input")}
    report["start_owned"] = differences(start, owned, START_FIELDS)
    try:
        current = guard._order_execution_input(session, order, lock=False, original_input=owned)
    except Exception as error:
        return {**report, **failure(error, "_order_execution_input")}
    if set(current) - {"workflow", "workflow_revision"} != set(FIELDS):
        return {**report, "stop": "current_14_field_contract"}
    report["owned_current"] = differences(owned, current, FIELDS)
    report.update(status="captured", compared_start_fields=2, compared_current_fields=14,
        stop="_native_owner.start_owned" if report["start_owned"] else
        "_native_owner.owned_current" if report["owned_current"] else None,
        native_input_changed_predicate=bool(report["start_owned"] or report["owned_current"]))
    return report


def reject_sql(conn, cursor, statement, parameters, execution_context, executemany):
    # PostgreSQL READ ONLY is authoritative; this also rejects locks and temp-table DML.
    sql = statement.strip()
    require(bool(re.match(r"^(SELECT|SHOW)\b", sql, re.I)) and ";" not in sql
        and not re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|COPY|CALL|INTO)\b|\bFOR\s+(KEY\s+)?SHARE\b", sql, re.I),
        "parity_sql_forbidden")


@contextmanager
def readonly_session(connection):
    from sqlalchemy import event, text
    from sqlalchemy.orm import Session
    require(connection.dialect.name == "postgresql", "parity_postgres_required")
    require(connection.execute(text("SHOW transaction_read_only")).scalar_one() == "on", "parity_db_not_readonly")
    require(connection.execute(text("SHOW transaction_isolation")).scalar_one() == "repeatable read", "parity_isolation_changed")
    require(tuple(connection.execute(text("SELECT current_database(), current_user")).one()) == ("orders", "orders_app"),
        "parity_database_target_changed")
    violations = []
    def checked_sql(*args):
        try:
            reject_sql(*args)
        except Blocked:
            violations.append(True)
            raise
    event.listen(connection, "before_cursor_execute", checked_sql)
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    def no_write(*args, **kwargs):
        violations.append(True)
        raise Blocked("parity_orm_write_forbidden")
    event.listen(session, "before_flush", no_write)
    event.listen(session, "before_commit", no_write)
    try:
        yield session
        require(not (session.new or session.dirty or session.deleted), "parity_orm_mutated")
        require(not violations, "parity_readonly_violation")
    finally:
        session.close()
        event.remove(connection, "before_cursor_execute", checked_sql)


def compare_values(left, right):
    # Typed full-value equality, including additions, nulls, lists and numeric types.
    a, b = diagnostic.result_bytes(left), diagnostic.result_bytes(right)
    return {"equal": a == b, "left_sha256": diagnostic.sha(a), "right_sha256": diagnostic.sha(b)}


def materialize(inspection, detail):
    result = deepcopy(inspection)
    rows = {}
    for row in result["ocr_results"]:
        value, error = detail(result["order_id"], row["ocr_result_id"])
        require(error is None and value is not None, "parity_detail_unavailable")
        value = deepcopy(value)
        require(value.pop("workflow_revision") == result["workflow"]["revision"], "parity_detail_revision_changed")
        require(value["order_id"] == result["order_id"] and value["ocr_result_id"] == row["ocr_result_id"],
            "parity_detail_ownership_changed")
        for key, item in row.items():
            if key == "detail_state" and item == "not_loaded":
                continue
            require(key in value and diagnostic.result_bytes(item) == diagnostic.result_bytes(value[key]),
                "parity_summary_detail_changed")
        rows[row["ocr_result_id"]] = value
    result["ocr_results"] = list(rows.values())
    candidates = result.get("ocr_candidates")
    if candidates is not None:
        for row in candidates["results"]:
            value = rows[row["ocr_result_id"]]
            for key, item in row.items():
                if key == "detail_state" and item == "not_loaded":
                    continue
                require(key in value and diagnostic.result_bytes(item) == diagnostic.result_bytes(value[key]),
                    "parity_candidate_detail_changed")
        candidates["results"] = [deepcopy(rows[row["ocr_result_id"]]) for row in candidates["results"]]
    return result


def compare_reads(service, baseline, order_id):
    old, old_error = read_phase("baseline_inspection", baseline["get_inspection"], order_id, include_ocr_results=True)
    workflow, workflow_error = read_phase("baseline_workflow", baseline["get_workflow_readonly"], order_id)
    new, new_error = read_phase("current_inspection", service.get_inspection, order_id, include_ocr_results=True, history_details=False)
    if old_error or new_error or workflow_error or old is None or new is None or workflow is None:
        return {"status": "unverified", "stop": "inspection_or_workflow",
            "errors": compare_values([old_error, workflow_error], [new_error, workflow_error])}
    old = deepcopy(old)
    old["workflow"] = workflow  # The old UI merged the separate workflow GET.
    expanded = read_phase("deferred_detail", materialize, new, service.get_inspection_detail)
    inspection = compare_values(old, expanded)
    progress, error = read_phase("current_progress", service.get_ocr_progress, order_id)
    expected = {"order_id": order_id, "workflow_revision": workflow["revision"],
        "state": workflow["state"], "ocr_job": workflow["ocr_job"]}
    if "common_job_status" in workflow:
        expected["common_job_status"] = workflow["common_job_status"]
    progress_result = compare_values(expected, progress)
    return {"status": "compared", "inspection": inspection, "progress": progress_result,
        "components": {key: compare_values(old.get(key), expanded.get(key))
            for key in ("workflow", "ocr_results", "ocr_candidates", "saved_sheet", "artifact_lineage",
                "pre_save_checks", "pre_save_status", "bagging_result", "anomaly_review", "output_bundle")},
        "progress_error": error is not None,
        "equal": inspection["equal"] and progress_result["equal"] and error is None}


def staging_uri(uri):
    require(isinstance(uri, str), "parity_storage_uri_invalid")
    parsed = urlsplit(uri)
    require(parsed.scheme == "gs" and parsed.netloc in BUCKETS and bool(parsed.path.strip("/")),
        "parity_storage_target_forbidden")
    return uri


def captured_read(kind, reader, cache, receipts, failures):
    def read(uri, *args, **kwargs):
        try:
            staging_uri(uri)
            key = (kind, uri, json.dumps([args, kwargs], sort_keys=True))
            if key not in cache:
                value = reader(uri, *args, **kwargs)
                require(isinstance(value, (bytes, str)), "parity_storage_value_invalid")
                cache[key] = value
                receipts.append({"kind": kind, "input_sha256": diagnostic.sha(repr(key).encode()),
                    "value_sha256": diagnostic.sha(value if isinstance(value, bytes) else value.encode())})
            return cache[key]
        except Exception:
            failures.append("external_input_capture_failed")
            raise
    return read


@contextmanager
def isolated_reads(service, session, cache=None, receipts=None):
    """Freeze real staging reads in memory; block writes and additional DB connections."""
    from sqlalchemy.engine import Engine
    import psycopg2
    from google.cloud.storage.blob import Blob
    from src.services import storage_service, canonical_facility_store
    cache = {} if cache is None else cache
    receipts = [] if receipts is None else receipts
    blocked = []
    def forbidden(*args, **kwargs):
        blocked.append("external_input_not_fixed")
        raise Blocked("parity_external_input_not_fixed")
    @contextmanager
    def scope():
        yield session
    order_service = service._get_order_service_module()
    read_bytes = captured_read("bytes", storage_service.load_bytes_from_uri, cache, receipts, blocked)
    sign = captured_read("signed_url", order_service._signed_url_from_uri, cache, receipts, blocked)
    original_master = canonical_facility_store._read_gcs
    original_download = Blob.download_as_bytes
    def download(blob, *args, **kwargs):
        uri = "gs://" + blob.bucket.name + "/" + blob.name
        if blob.generation is not None:
            uri += "?generation=" + str(blob.generation)
        reader = captured_read("gcs_bytes", lambda _uri, *a, **kw: original_download(blob, *a, **kw),
            cache, receipts, blocked)
        return reader(uri, *args, **kwargs)
    def master(uri):
        try:
            staging_uri(uri)
            key = ("master", uri)
            if key not in cache:
                cache[key] = original_master(uri)
                receipts.append({"kind": "canonical_master", "input_sha256": diagnostic.sha(uri.encode()),
                    "value_sha256": diagnostic.sha(diagnostic.result_bytes(cache[key].master)),
                    "revision_sha256": diagnostic.sha(cache[key].revision.encode())})
            return deepcopy(cache[key])
        except Exception:
            blocked.append("canonical_input_capture_failed")
            raise
    with ExitStack() as stack:
        stack.enter_context(patch.object(service, "session_scope", scope))
        for name in ("connect", "raw_connection"):
            stack.enter_context(patch.object(Engine, name, forbidden))
        stack.enter_context(patch.object(psycopg2, "connect", forbidden))
        stack.enter_context(patch.object(storage_service, "load_bytes_from_uri", read_bytes))
        if hasattr(order_service, "load_bytes_from_uri"):
            stack.enter_context(patch.object(order_service, "load_bytes_from_uri", read_bytes))
        stack.enter_context(patch.object(order_service, "_signed_url_from_uri", sign))
        stack.enter_context(patch.object(canonical_facility_store, "_read_gcs", master))
        stack.enter_context(patch.object(canonical_facility_store, "_read_local", forbidden))
        stack.enter_context(patch.object(Blob, "download_as_bytes", download))
        for name in ("save_canonical_artifact_bytes", "save_bytes_to_gcs", "save_artifact_bytes_to_gcs", "save_output_bytes_to_gcs"):
            stack.enter_context(patch.object(storage_service, name, forbidden))
        stack.enter_context(patch.object(storage_service.StorageService, "save_bytes", forbidden))
        for name in ("upload_from_string", "upload_from_file", "upload_from_filename", "delete", "patch", "update", "compose", "rewrite"):
            stack.enter_context(patch.object(Blob, name, forbidden))
        yield blocked


def capture(session, source, receipts=None):
    from src.services import order_workflow_v2_service as service, workflow_attempt_guard_service as guard
    results = []
    cache = {}
    for order_id in ORDERS:
        order, error = service._get_order_or_error(session, order_id)
        require(error is None, "parity_order_missing")
        state = service._get_workflow_readonly(session, order_id)
        job_id = service._workflow_meta(state).get("ocr_job_id") if state is not None else None
        job = session.get(service.OcrJob, job_id) if job_id else None
        with isolated_reads(service, session, cache, receipts) as blocked:
            native = capture_native(session, order, job, guard)
            if blocked:
                native.update(status="unverified", stop="native_external_input_not_fixed")
            blocked.clear()
            baseline = old_readers(source, vars(service))
            try:
                parity = compare_reads(service, baseline, order_id)
            except ReadFailure as error:
                parity = error.summary
            except Exception as error:
                parity = failure(error, "inspection_candidate_progress")
            if blocked:
                parity = {**parity, "status": "unverified", "equal": False, "external_capture_failures": len(blocked)}
        results.append({"order_id": order_id, "native_input": native, "function_parity": parity})
    return results


def canonical_environment():
    service = json.loads(diagnostic.command(["gcloud", "run", "services", "describe", "worker-stg",
        "--project", diagnostic.PROJECT, "--region", diagnostic.REGION, "--format=json"]))
    traffic = [item for item in service["status"]["traffic"] if item.get("percent", 0) > 0]
    require(len(traffic) == 1 and traffic[0]["percent"] == 100, "parity_runtime_traffic_ambiguous")
    revision = traffic[0]["revisionName"]
    require(revision.startswith("worker-stg-"), "parity_runtime_revision_invalid")
    descriptor = json.loads(diagnostic.command(["gcloud", "run", "revisions", "describe", revision,
        "--project", diagnostic.PROJECT, "--region", diagnostic.REGION, "--format=json"]))
    containers = descriptor["spec"]["containers"]
    require(len(containers) == 1, "parity_runtime_container_ambiguous")
    names = ("FACILITY_MASTER_URI", "RAW_BUCKET", "OCR_PIPELINE_BUCKET", "OCR_OUTPUT_BUCKET", "TEMPLATE_BUCKET")
    values = {}
    for entry in containers[0].get("env", []):
        if entry.get("name") in names:
            require(set(entry) == {"name", "value"} and entry["name"] not in values, "parity_canonical_env_ambiguous")
            values[entry["name"]] = entry["value"]
    require(set(values) == set(names), "parity_canonical_env_missing")
    staging_uri(values["FACILITY_MASTER_URI"])
    require(urlsplit(values["FACILITY_MASTER_URI"]).netloc == "sawahospitalsystem-stg-templates"
        and all(values[k] == "sawahospitalsystem-stg-raw" for k in names[1:4])
        and values["TEMPLATE_BUCKET"] == "sawahospitalsystem-stg-templates", "parity_canonical_env_target_changed")
    os.environ.update(values)
    os.environ.pop("FACILITY_MASTER_PATH", None)
    os.environ["K_SERVICE"] = "worker-stg"
    return revision, {name: diagnostic.sha(value.encode()) for name, value in values.items()}


@contextmanager
def staging_connection(proxy, runtime_proof=None):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL
    from staging_db_target import load_service_db_config, PROJECT, REGION, INSTANCE
    from run_stg_menu_master_migration import PROXY_SHA256
    require(not proxy.is_symlink() and diagnostic.sha(proxy.read_bytes()) == PROXY_SHA256, "parity_proxy_hash_changed")
    revision, config_hashes = canonical_environment()
    if runtime_proof is not None:
        runtime_proof.update(revision=revision, canonical_env_sha256=config_hashes)
    config = load_service_db_config(PROJECT, REGION, "worker-stg", expected_revision=revision)
    with TemporaryDirectory(prefix="parity-sql-", dir="/tmp") as directory:
        path = Path(directory) / INSTANCE / ".s.PGSQL.5432"
        require(len(os.fsencode(path)) < 104, "parity_socket_path_invalid")
        process = subprocess.Popen([str(proxy), "--gcloud-auth", "--unix-socket", directory,
            "--max-connections", "1", INSTANCE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        engine = None
        try:
            started = time.monotonic()
            while not path.is_socket():
                require(process.poll() is None and time.monotonic() - started < 35, "parity_proxy_failed")
                time.sleep(0.1)
            engine = create_engine(URL.create("postgresql+psycopg2", username=config.db_user,
                password=config.db_password, database=config.db_name, query={"host": str(path.parent)}),
                isolation_level="REPEATABLE READ", execution_options={"postgresql_readonly": True},
                connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=1000"})
            with engine.connect() as connection, connection.begin():
                yield connection
                connection.rollback()
        finally:
            if engine is not None:
                engine.dispose()
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", type=Path, required=True)
    parser.add_argument("--proxy", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    private = None
    stage = "context"
    summary_ready = False
    try:
        current = context(args.product)
        private = diagnostic.owned_path().with_name("demand-parity-" + os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"])
        if args.cleanup:
            diagnostic.cleanup(private)
            return 0
        require(args.proxy is not None and args.proxy.is_file() and args.summary is not None, "parity_arguments_missing")
        stage = "private_area"
        require(not private.exists(), "parity_private_exists")
        private.mkdir(mode=0o700)
        diagnostic.write_json(private / "owner.json", diagnostic.marker())
        require(args.summary.resolve() != private.resolve() and private.resolve() not in args.summary.resolve().parents
            and args.summary.resolve() not in private.resolve().parents, "parity_summary_private_overlap")
        args.summary.mkdir(parents=True, exist_ok=True)
        summary_ready = True
        sys.path.insert(0, str(args.product / "backend"))
        sys.path.insert(0, str(ROOT / "scripts"))
        stage = "source_dependencies"
        source = diagnostic.command(["git", "-C", str(args.product), "show", BASE + ":" + SOURCE]).decode()
        changed = diagnostic.command(["git", "-C", str(args.product), "diff", "--name-only", BASE, current,
            "--", "backend/src", "backend/uv.lock", "backend/pyproject.toml", "backend/requirements.txt"]).decode().splitlines()
        dependencies = [{"path": path, "baseline": source_hash(args.product, BASE, path),
            "current": source_hash(args.product, current, path)} for path in changed]
        # Imported product code must not acquire a separate business DB connection.
        for name in ("ALEMBIC_DB_URI", "DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER"):
            os.environ.pop(name, None)
        os.environ["DB_URI"] = "sqlite:///:memory:"
        log = private / "runtime.log"
        diagnostic.write_private(log, b"")
        runtime_proof, receipts = {}, []
        with log.open("w") as stream, redirect_stdout(stream), redirect_stderr(stream):
            stage = "staging_connection"
            with staging_connection(args.proxy, runtime_proof) as connection:
                stage = "readonly_session"
                with readonly_session(connection) as session, no_extra_database_connections():
                    stage = "capture"
                    results = capture(session, source, receipts)
        report = {"baseline": BASE, "current": current, "dependencies": dependencies,
            "runtime": runtime_proof, "captured_inputs": receipts,
            "scope": "function-only comparison on common current dependencies; not full old runtime parity",
            "excluded_proofs": ["unknown_x source change", "Pillow image parity: separate 105-image proof"],
            "comparison": "full inspection after detail fetch; old workflow GET authoritative; no value normalization",
            "orders": results}
        diagnostic.write_json(args.summary / "comparison.json", report)
        return 0 if all(item["function_parity"].get("equal") is True and
            item["native_input"]["status"] == "captured" for item in results) else 2
    except Exception as error:
        sanitized = failure(error, stage)
        if summary_ready:
            diagnostic.write_json(args.summary / "failure.json", sanitized)
        print(json.dumps(sanitized), file=sys.stderr)
        return 2
    finally:
        if private is not None and private.exists():
            diagnostic.cleanup(private)


if __name__ == "__main__":
    raise SystemExit(main())
