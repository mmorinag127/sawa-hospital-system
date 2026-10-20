"""One fixed staging evidence replay; read-only capture, then disconnected validation."""
import argparse
from collections import Counter
from copy import deepcopy
from contextlib import ExitStack
from datetime import datetime
from functools import wraps
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

PRODUCT_SHA = "e5282354017b43d17f0d4dbd58e73972f37a716f"
REPOSITORY = "mmorinag127/sawa-hospital-system"
PROJECT, REGION = "sawahospitalsystem", "asia-northeast2"
INSTANCE = PROJECT + ":" + REGION + ":orders-stg"
ORDER, EVIDENCE, DOCUMENT = "ORD581f55d6", "OEV9fef6b9e2672", "DOC0d4cb340"
PAYLOAD_SHA = "0b3584824e281e14001672f49867bba31c89fa08e6f45e4993a002680374632f"
MANIFEST_SHA = "dd003264d2a414e71976086c74366a1123901e09f6c2f3ef82dc23fe323e5e7e"
ARTIFACT_SHA = "88ce8ae392d33ed95f72820aa571e4d6b835e7d08a8d323f397401745ef1fc66"
PDF_SHA = "acafa4eacc0fe0d82aff29c078b2ad8bf56dd3700c19a13bf26f69a484678a31"
PDF_URI = ("gs://sawahospitalsystem-stg-raw/manual-uploads/2026/10/15/"
           "acafa4eacc0fe0d82aff29c0-ORD3d234696.pdf?generation=1792028285165557")
KEY = "operator_recognition_acceptance"


class Blocked(ValueError):
    pass


def require(value, code):
    if not value:
        raise Blocked(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_private(path, data):
    path.write_bytes(data)
    path.chmod(0o600)


def write_json(path, data):
    write_private(path, (json.dumps(data, sort_keys=True, indent=2) + "\n").encode())


def command(args):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120)
    require(result.returncode == 0, "diagnostic_read_command_failed")
    return result.stdout


def context(product):
    env = os.environ
    require(env.get("GITHUB_ACTIONS") == "true" and env.get("GITHUB_REPOSITORY") == REPOSITORY
        and env.get("GITHUB_REPOSITORY_OWNER") == "mmorinag127"
        and env.get("GITHUB_ACTOR") == env.get("GITHUB_TRIGGERING_ACTOR") == "mmorinag127"
        and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and env.get("GITHUB_REF") == "refs/heads/develop"
        and env.get("DIAGNOSTIC_ENVIRONMENT") == "staging", "diagnostic_context_invalid")
    require(not env.get("CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT"), "diagnostic_impersonation_forbidden")
    require(command(["git", "-C", str(product), "rev-parse", "HEAD"]).decode().strip() == PRODUCT_SHA,
        "diagnostic_product_source_changed")
    require(not command(["git", "-C", str(product), "status", "--porcelain", "--untracked-files=no"]),
        "diagnostic_product_dirty")
    root = Path(__file__).resolve().parents[1]
    diagnostic_sha = command(["git", "-C", str(root), "rev-parse", "HEAD"]).decode().strip()
    require(diagnostic_sha == env.get("GITHUB_SHA"), "diagnostic_source_changed")
    require(all(re.fullmatch(r"[1-9][0-9]*", env.get(key, "")) for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")),
        "diagnostic_run_identity_invalid")
    return diagnostic_sha


def candidate_context(product, candidate):
    require(candidate is not None and candidate.resolve() != product.resolve(), "diagnostic_candidate_required")
    require(command(["git", "-C", str(candidate), "rev-parse", "HEAD"]).decode().strip() == os.environ["GITHUB_SHA"],
        "diagnostic_candidate_source_changed")
    require(not command(["git", "-C", str(candidate), "status", "--porcelain", "--untracked-files=no"]),
        "diagnostic_candidate_dirty")
    for name in ("uv.lock", "pyproject.toml"):
        require((product / "backend" / name).read_bytes() == (candidate / "backend" / name).read_bytes(),
            "diagnostic_candidate_runtime_changed")


def result_bytes(value):
    # Typed encoding compares complete values privately, including every byte,
    # without publishing raw evidence or conflating bytes with business strings.
    def encode(item):
        if isinstance(item, bytes):
            return ["bytes", item.hex()]
        if isinstance(item, dict):
            require(all(isinstance(key, str) for key in item), "diagnostic_result_type_invalid")
            return ["dict", [[key, encode(item[key])] for key in sorted(item)]]
        if isinstance(item, list):
            return ["list", [encode(entry) for entry in item]]
        require(item is None or type(item) in (str, int, float, bool), "diagnostic_result_type_invalid")
        return [type(item).__name__, item]
    return json.dumps(encode(value), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def compare_results(private, summary):
    comparisons = []
    for build in ("headless", "gui"):
        baseline = private / ("baseline-" + build + "-results.json")
        candidate = private / ("candidate-" + build + "-results.json")
        require(baseline.is_file() and candidate.is_file(), "diagnostic_comparison_missing")
        left, right = baseline.read_bytes(), candidate.read_bytes()
        comparisons.append({"build": build, "full_results_equal": left == right,
            "baseline_sha256": sha(left), "candidate_sha256": sha(right)})
    write_json(summary / "comparison.json", {"comparisons": comparisons,
        "scope": "full phase values and exception identities on identical captured inputs; not HTTP latency"})
    return all(item["full_results_equal"] for item in comparisons)


def owned_path():
    return Path(os.environ["RUNNER_TEMP"]).resolve() / (
        "reviewed-ocr-" + os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"])


def marker():
    return {key: os.environ[key] for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA")}


def cleanup(private):
    if private.exists():
        require(not private.is_symlink() and json.loads((private / "owner.json").read_bytes()) == marker(),
            "diagnostic_cleanup_owner_changed")
        shutil.rmtree(private)


def validate_row(row):
    payload_raw, manifest_raw = row["payload_raw"].encode(), row["manifest_raw"].encode()
    require(sha(payload_raw) == PAYLOAD_SHA and sha(manifest_raw) == MANIFEST_SHA, "diagnostic_input_hash_changed")
    require(row["id"] == EVIDENCE and row["order_id"] == ORDER and row["artifact_digest"] == ARTIFACT_SHA,
        "diagnostic_evidence_changed")
    payload, manifest = json.loads(payload_raw), json.loads(manifest_raw)
    pdf = payload["hakodate_repair_source_receipt"]["selected_pdf_receipt"]
    require(pdf == payload["_ocr_execution"]["selected_pdf_receipt"], "diagnostic_pdf_changed")
    require(pdf["document_id"] == DOCUMENT and pdf["order_id"] == ORDER
        and pdf["sha256"] == PDF_SHA and pdf["storage_uri"] == PDF_URI
        and pdf["generation"] == "1792028285165557", "diagnostic_pdf_changed")
    require(row["current_document_id"] == DOCUMENT and row["storage_uri"] == PDF_URI
        and row["source_email_id"] == pdf["message_id"] and row["uploaded_pdf_id"] == pdf["uploaded_pdf_id"]
        and row["content_sha256"] == PDF_SHA, "diagnostic_current_pdf_changed")
    acceptance = manifest.get(KEY)
    require(isinstance(acceptance, dict) and acceptance.get("evidence_run_id") == EVIDENCE
        and acceptance.get("artifact_digest") == ARTIFACT_SHA, "diagnostic_acceptance_changed")
    require(row["selected_evidence"] == EVIDENCE and row["workflow_state"] == "ocr_selected"
        and row["draft_id"] is None and row["workflow_blockers"] == [], "diagnostic_selected_state_changed")
    require(payload["hakodate_recognition_review"]["count"] == 10, "diagnostic_review_changed")
    return payload_raw, manifest_raw, payload


def read_row(proxy, private):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    service = json.loads(command(["gcloud", "run", "services", "describe", "worker-stg",
        "--project", PROJECT, "--region", REGION, "--format=json"]))
    template = service["spec"]["template"]
    require(template["metadata"]["annotations"]["run.googleapis.com/cloudsql-instances"] == INSTANCE,
        "diagnostic_instance_changed")
    entries = {entry["name"]: entry for entry in template["spec"]["containers"][0]["env"]}

    def value(name):
        entry = entries[name]
        if "value" in entry:
            require("valueFrom" not in entry, "diagnostic_db_config_invalid")
            return entry["value"]
        reference = entry["valueFrom"]["secretKeyRef"]
        return command(["gcloud", "secrets", "versions", "access", reference["key"],
            "--secret", reference["name"], "--project", PROJECT]).decode()

    require(value("DB_DRIVER").startswith("postgres"), "diagnostic_db_driver_invalid")
    credentials = {"dbname": value("DB_NAME"), "user": value("DB_USER"), "password": value("DB_PASSWORD")}
    with TemporaryDirectory(prefix="ocr-sql-", dir="/tmp") as socket_directory:
        socket_path = Path(socket_directory) / INSTANCE / ".s.PGSQL.5432"
        require(len(os.fsencode(socket_path)) < 104, "diagnostic_proxy_socket_path_invalid")
        process = None
        try:
            process = subprocess.Popen([str(proxy), "--gcloud-auth", "--unix-socket", socket_directory,
                "--max-connections", "1", INSTANCE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            started = time.monotonic()
            while True:
                require(process.poll() is None and time.monotonic() - started < 35, "diagnostic_proxy_start_failed")
                if socket_path.is_socket():
                    break
                time.sleep(0.1)
            connection = psycopg2.connect(**credentials, host=str(socket_path.parent), port=5432, connect_timeout=8,
                options="-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=1000")
            try:
                connection.set_session(isolation_level="REPEATABLE READ", readonly=True)
                with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("SHOW transaction_read_only")
                    require(cursor.fetchone()["transaction_read_only"] == "on", "diagnostic_db_not_readonly")
                    cursor.execute("SHOW transaction_isolation")
                    require(cursor.fetchone()["transaction_isolation"] == "repeatable read", "diagnostic_db_isolation_changed")
                    cursor.execute("""SELECT e.id, e.order_id, e.status, e.template_version_id, e.producer_version,
                        e.source, e.schema_version, e.artifact_digest, e.created_at,
                        e.payload_json::text AS payload_raw, e.artifact_manifest_json::text AS manifest_raw,
                        o.current_document_id, d.storage_uri, d.source_email_id,
                        u.id AS uploaded_pdf_id, u.content_sha256, w.evidence_run_id AS selected_evidence,
                        w.state AS workflow_state, w.draft_id, w.blockers_json AS workflow_blockers
                        FROM order_ocr_evidence_runs e JOIN orders o ON o.id=e.order_id
                        JOIN order_documents d ON d.id=o.current_document_id
                        JOIN uploaded_pdfs u ON u.message_id=d.source_email_id AND u.storage_uri=d.storage_uri
                        JOIN order_workflow_states w ON w.order_id=o.id WHERE e.order_id=%s AND e.id=%s""", (ORDER, EVIDENCE))
                    rows = cursor.fetchall()
                    require(len(rows) == 1, "diagnostic_row_count_changed")
                    row = dict(rows[0])
                connection.rollback()
                return row
            finally:
                connection.close()

        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def uri_parts(uri):
    require(isinstance(uri, str), "diagnostic_uri_invalid")
    parsed = urlsplit(uri)
    query = parse_qs(parsed.query, strict_parsing=True)
    require(parsed.scheme == "gs" and parsed.netloc == "sawahospitalsystem-stg-raw"
        and not parsed.fragment and set(query) == {"generation"} and len(query["generation"]) == 1
        and re.fullmatch(r"[1-9][0-9]*", query["generation"][0])
        and (parsed.path.startswith("/artifacts/") or parsed.path.startswith("/manual-uploads/")), "diagnostic_uri_invalid")
    return parsed, query["generation"][0]


def capture_assets(payload, private):
    assets = {}

    def fetch(receipt):
        uri = receipt["uri"]
        parsed, generation = uri_parts(uri)
        require(receipt.get("generation", generation) == generation, "diagnostic_generation_changed")
        if uri in assets:
            entry = assets[uri]
            require(entry["sha256"] == receipt["sha256"]
                and receipt.get("size_bytes", entry["bytes"]) == entry["bytes"], "diagnostic_alias_changed")
            return (private / entry["path"]).read_bytes()
        data = command(["gcloud", "storage", "cat", "gs://" + parsed.netloc + parsed.path + "#" + generation])
        require(sha(data) == receipt["sha256"] and len(data) == receipt.get("size_bytes", len(data)),
            "diagnostic_artifact_changed")
        path = "asset-" + str(len(assets)) + ".bin"
        write_private(private / path, data)
        assets[uri] = {"path": path, "sha256": sha(data), "bytes": len(data), "generation": generation}
        return data

    packet = payload["hakodate_canonical_pipeline"]["artifact_packet"]
    fetch(packet["metadata"])
    for receipt in packet["outputs"].values():
        fetch(receipt)
    observation = json.loads(fetch(packet["outputs"]["preprocess_observation"]))
    checkpoint = observation["checkpoint"]
    fetch(checkpoint["checkpoint"])
    fetch(checkpoint["image"])
    pdf = checkpoint["source"]["selected_pdf_receipt"]
    require(pdf == payload["hakodate_repair_source_receipt"]["selected_pdf_receipt"], "diagnostic_checkpoint_pdf_changed")
    while pdf is not None:
        uri = pdf["storage_uri"]
        parsed, generation = uri_parts(uri)
        require(pdf["generation"] == generation, "diagnostic_generation_changed")
        fetch({"uri": uri, "sha256": pdf["sha256"], "generation": generation})
        metadata = json.loads(command(["gcloud", "storage", "objects", "describe",
            "gs://" + parsed.netloc + parsed.path, "--format=json"]))
        require(str(metadata["generation"]) == generation, "diagnostic_latest_pdf_generation_changed")
        assets[uri]["latest_generation"] = generation
        pdf = pdf.get("parent")
    return assets


def safe_error(error):
    message = str(error)
    if re.fullmatch(r"(?:diagnostic|hakodate|canonical|recognition|evidence)_[a-z0-9_]{1,120}", message):
        return message
    return "unexpected_" + type(error).__name__


def block_external():
    def forbidden(*args, **kwargs):
        raise Blocked("diagnostic_external_io_forbidden")

    socket.socket.connect = socket.socket.connect_ex = forbidden
    socket.socket.sendto = forbidden
    if hasattr(socket.socket, "sendmsg"):
        socket.socket.sendmsg = forbidden
    socket.create_connection = socket.getaddrinfo = socket.gethostbyname = forbidden
    socket.gethostbyname_ex = socket.gethostbyaddr = forbidden
    from sqlalchemy.engine import Engine
    Engine.connect = Engine.raw_connection = forbidden
    import psycopg2
    psycopg2.connect = forbidden


def fixture_bytes(private, assets, uri, options):
    require(uri in assets and options == {"verification": True}, "diagnostic_unknown_uri")
    _, generation = uri_parts(uri)
    entry = assets[uri]
    require(entry["generation"] == generation and re.fullmatch(r"asset-[0-9]+\.bin", entry["path"]),
        "diagnostic_fixture_path_changed")
    path = private / entry["path"]
    require(not path.is_symlink() and path.resolve().parent == private.resolve(), "diagnostic_fixture_path_changed")
    data = path.read_bytes()
    require(len(data) == entry["bytes"] and sha(data) == entry["sha256"], "diagnostic_artifact_changed")
    return data


def offline(product, private, output, build, *, variant="baseline", runtime=None, original_only=False):
    block_external()
    sys.path.insert(0, str(product / "backend"))
    from src.services import canonical_ocr_artifact_service as artifacts, storage_service as storage
    from src.services import order_service as orders, order_workflow_v2_service as workflow
    from src.services import ocr_evidence_service as evidence, recognition_acceptance_service as acceptance
    from src.services import hakodate_preprocess_observation as observation
    from src.services import hakodate_ocr_checkpoint_service as checkpoint
    from src.services import hakodate_recognizer_contact_sheet as contact
    from src.services import hakodate_recognizer_input_evidence as pixels
    import cv2
    versions = {name: importlib.metadata.version(name) for name in ("Pillow", "numpy")}
    distribution = "opencv-python-headless" if build == "headless" else "opencv-python"
    require(versions == {"Pillow": "12.3.0", "numpy": "2.4.2"} and cv2.__version__ == "5.0.0"
        and importlib.metadata.version(distribution) == "5.0.0.93",
        "diagnostic_runtime_changed")
    module_root = (runtime or product / "backend/.venv") if build == "headless" else Path(os.environ["RUNNER_TEMP"]) / "reviewed-opencv-gui"
    require(Path(cv2.__file__).resolve().is_relative_to(module_root.resolve()), "diagnostic_runtime_changed")
    require(json.loads((private / "owner.json").read_bytes()) == marker(), "diagnostic_owner_changed")
    before = {path.name: sha(path.read_bytes()) for path in private.iterdir()
        if path.is_file() and not path.name.endswith("-results.json")}
    bundle = json.loads((private / "bundle.json").read_bytes())
    require(sha((private / "payload.json").read_bytes()) == PAYLOAD_SHA
        and sha((private / "manifest.json").read_bytes()) == MANIFEST_SHA, "diagnostic_input_hash_changed")
    row_data = json.loads((private / "header.json").read_bytes())
    row_data["created_at"] = datetime.fromisoformat(row_data["created_at"])
    row = SimpleNamespace(**row_data, payload_json=json.loads((private / "payload.json").read_bytes()),
        artifact_manifest_json=json.loads((private / "manifest.json").read_bytes()))
    assets = bundle["assets"]
    original_row = deepcopy(row)
    original_assets = deepcopy(assets)
    changed_uri = None
    changed_reads = 0
    change_kind = None
    events, failures = [], []

    def load(uri, **kwargs):
        nonlocal changed_reads
        wall, cpu = time.perf_counter(), time.process_time()
        data = fixture_bytes(private, assets, uri, kwargs)
        if uri == changed_uri:
            changed_reads += 1
            if change_kind == "unavailable" or (change_kind == "in_flight_delete" and changed_reads > 1):
                raise FileNotFoundError("synthetic deleted artifact")
            if change_kind == "bytes_changed":
                data += b"synthetic mutation"
        events.append({"name": "fixture_read", "bytes": len(data), "uri_hash": sha(uri.encode()),
            "wall": time.perf_counter() - wall, "cpu": time.process_time() - cpu})
        return data

    class Bucket:
        def __init__(self, name):
            self.name = name

        def blob(self, name):
            found = [(uri, entry) for uri, entry in assets.items()
                if urlsplit(uri).netloc == self.name and urlsplit(uri).path.lstrip("/") == name]
            require(len(found) == 1, "diagnostic_unknown_uri")
            uri, entry = found[0]

            class Blob:
                generation = None

                def reload(self, **kwargs):
                    require(kwargs == {"timeout": 30, "retry": None}, "diagnostic_metadata_options_changed")
                    load(uri, verification=True)
                    require(entry.get("latest_generation") is not None, "diagnostic_unknown_uri")
                    self.generation = entry["latest_generation"]
            return Blob()

    client = SimpleNamespace(bucket=Bucket)

    def spy(name, real):
        @wraps(real)
        def call(*args, **kwargs):
            event = {"name": name}
            events.append(event)
            wall, cpu = time.perf_counter(), time.process_time()
            try:
                return real(*args, **kwargs)
            except Exception as exc:
                event["error"] = safe_error(exc)
                failures.append(event["error"])
                raise
            finally:
                event.update(wall=time.perf_counter() - wall, cpu=time.process_time() - cpu)
        return call

    watched = [(acceptance, "payload_for_evidence"), (acceptance, "accepted_values"), (acceptance, "payload_digest"),
        (orders, "_validate_hakodate_original_execution"), (orders, "_validate_hakodate_owned_artifacts"),
        (orders, "_validate_hakodate_recognition_review"), (artifacts, "validate_payload"), (artifacts, "validate_packet"),
        (observation, "validate_artifact"), (checkpoint, "load_recognizer_checkpoint"),
        (contact, "build_recognizer_contact_sheet"), (pixels, "contact_crop_evidence")]
    results = []
    full_results = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(storage, "load_bytes_from_uri", side_effect=load))
        stack.enter_context(patch.object(storage, "_get_thread_gcs_client", return_value=client))
        for module, name in watched:
            stack.enter_context(patch.object(module, name, spy(name, getattr(module, name))))
        execution = original_row.payload_json["_ocr_execution"]
        captured = {**execution["processing_start_input"], "selected_pdf_receipt": execution["selected_pdf_receipt"]}
        phases = (
            ("canonical_payload", lambda: artifacts.validate_payload(row.payload_json)),
            ("selection_blocker", lambda: workflow._evidence_selection_blocker(row)),
            ("selected_validation", lambda: orders._validate_selected_hakodate_artifacts(vars(row), captured)),
            ("serialize_evidence", lambda: evidence._serialize_evidence_run(row, include_payload=True)),
        )
        cases = [("original", None, None), ("acceptance_changed", None, None),
            ("source_changed", None, None), ("wrong_order", None, None), ("wrong_revision", None, None),
            ("latest_pdf_generation", None, None), ("multiple_invalid", None, None)]
        for index, uri in enumerate(original_assets):
            cases.extend((kind + "_" + str(index), uri, kind)
                for kind in ("unavailable", "bytes_changed", "in_flight_delete"))
        if original_only:
            cases = cases[:1]
        for case, changed_uri, change_kind in cases:
            row = deepcopy(original_row)
            assets = deepcopy(original_assets)
            if case in ("acceptance_changed", "multiple_invalid"):
                row.artifact_manifest_json[KEY]["artifact_digest"] = "0" * 64
            if case in ("source_changed", "multiple_invalid"):
                row.payload_json["hakodate_repair_source_receipt"]["page"] = -1
            if case == "wrong_order":
                row.order_id = "synthetic-foreign-order"
                row.payload_json["_ocr_execution"]["evidence_run_id"] = "synthetic-foreign-evidence"
            if case == "wrong_revision":
                row.payload_json["_ocr_execution"]["workflow_revision"] = -1
            if case == "latest_pdf_generation":
                for entry in assets.values():
                    if "latest_generation" in entry:
                        entry["latest_generation"] = str(int(entry["generation"]) + 1)
            for phase, action in phases:
                if changed_uri is not None and phase not in (
                    ("selected_validation", "serialize_evidence") if change_kind == "in_flight_delete"
                    else ("canonical_payload", "selected_validation")
                ):
                    continue
                changed_reads = 0
                events.clear()
                failures.clear()
                wall, cpu = time.perf_counter(), time.process_time()
                phase_input = deepcopy(vars(row))
                captured_input = deepcopy(captured)
                try:
                    value = action()
                    outcome = value if isinstance(value, str) or value is None else type(value).__name__
                    full_results.append({"case": case, "phase": phase, "value": json.loads(result_bytes(value))})
                except Exception as exc:
                    outcome = safe_error(exc)
                    full_results.append({"case": case, "phase": phase,
                        "exception": [type(exc).__module__, type(exc).__name__, str(exc)]})
                require(vars(row) == phase_input and captured == captured_input, "diagnostic_input_mutated")
                full_results[-1]["first_guard_error"] = failures[0] if failures else None
                full_results[-1]["errors_in_order"] = list(failures)
                counts = Counter(event["name"] for event in events)
                loads = [event for event in events if event["name"] == "fixture_read"]
                results.append({"case": case, "phase": phase, "outcome": outcome, "wall": time.perf_counter() - wall,
                    "cpu": time.process_time() - cpu, "calls": dict(counts),
                    "bytes": sum(event["bytes"] for event in loads),
                    "unique_uris": len({event["uri_hash"] for event in loads}),
                    "errors_in_order": list(failures),
                    "returned_selectable": value.get("selectable") if outcome == "dict" and phase == "serialize_evidence" else None,
                    "inclusive_times": {name: {clock: sum(event[clock] for event in events if event["name"] == name)
                        for clock in ("wall", "cpu")} for name in counts}})
    require(before == {path.name: sha(path.read_bytes()) for path in private.iterdir()
        if path.is_file() and not path.name.endswith("-results.json")},
        "diagnostic_input_mutated")
    require(original_row.payload_json == json.loads((private / "payload.json").read_bytes())
        and original_row.artifact_manifest_json == json.loads((private / "manifest.json").read_bytes()), "diagnostic_input_mutated")
    original_valid = (results[0]["outcome"] == "dict" and not results[0]["errors_in_order"]
        and results[1]["outcome"] is None and results[2]["outcome"] is None
        and results[3]["returned_selectable"] is True)
    result = {"scope": "offline original execution input; not current DB capture or service latency", "build": build,
        "mode": "original-only" if original_only else "all", "original_validation_passed": original_valid,
        "product_sha": PRODUCT_SHA if variant == "baseline" else os.environ["GITHUB_SHA"],
        "variant": variant, "diagnostic_sha": os.environ["GITHUB_SHA"], "input_hashes": before,
        "script_sha256": sha(Path(__file__).read_bytes()), "lock_sha256": sha((product / "backend/uv.lock").read_bytes()),
        "versions": {**versions, "opencv": cv2.__version__, "python": sys.version.split()[0]},
        "opencv_gui": next((line.strip() for line in cv2.getBuildInformation().splitlines() if "GUI:" in line), "unknown"),
        "inputs_unchanged": True, "results": results}
    write_json(output, result)
    write_json(private / (variant + "-" + build + "-results.json"), full_results)
    # A completed comparison input is not a claim that the evidence is selectable.
    return original_only or original_valid


def run(product, proxy, summary, candidate=None, *, original_only=False):
    private = owned_path()
    private.mkdir(mode=0o700)
    write_json(private / "owner.json", marker())
    try:
        row = read_row(proxy, private)
        payload_raw, manifest_raw, payload = validate_row(row)
        write_private(private / "payload.json", payload_raw)
        write_private(private / "manifest.json", manifest_raw)
        header = {key: row[key] for key in ("id", "order_id", "status", "template_version_id", "producer_version",
            "source", "schema_version", "artifact_digest")}
        header["created_at"] = row["created_at"].isoformat()
        write_json(private / "header.json", header)
        write_json(private / "bundle.json", {"assets": capture_assets(payload, private)})
        statuses = []
        for variant, source, build in (
            (variant, source, build)
            for variant, source in (("baseline", product), ("candidate", candidate)) if source is not None
            for build in ("headless", "gui")
        ):
            env = {key: os.environ[key] for key in ("PATH", "RUNNER_TEMP", "GITHUB_ACTIONS", "GITHUB_REPOSITORY",
                "GITHUB_REPOSITORY_OWNER", "GITHUB_ACTOR", "GITHUB_TRIGGERING_ACTOR", "GITHUB_EVENT_NAME", "GITHUB_REF",
                "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "DIAGNOSTIC_ENVIRONMENT")}
            env.update(DATABASE_URL="sqlite:///:memory:", PYTHONDONTWRITEBYTECODE="1")
            if build == "gui":
                env["PYTHONPATH"] = str(Path(os.environ["RUNNER_TEMP"]) / "reviewed-opencv-gui")
            destination = summary / (variant + "-" + build + ".json")
            candidate_args = ["--candidate", str(candidate)] if candidate is not None else []
            mode_args = ["--original-only"] if original_only else []
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--product", str(product),
                "--offline", "--build", build, "--variant", variant, "--summary", str(summary), *candidate_args, *mode_args], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
            if result.returncode != 0:
                require(destination.is_file() or (summary / (variant + "-" + build + "-error.json")).is_file(),
                    "diagnostic_offline_process_failed")
            statuses.append(result.returncode == 0)
        equal = compare_results(private, summary) if candidate is not None else True
        return all(statuses) and equal
    finally:
        cleanup(private)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--variant", choices=("baseline", "candidate"), default="baseline")
    parser.add_argument("--proxy", type=Path)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--original-only", action="store_true",
        help="Compare only the four original-input phases; default retains all fault cases.")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--build", choices=("headless", "gui"), default="headless")
    args = parser.parse_args()
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    try:
        context(args.product)
        if args.candidate is not None:
            candidate_context(args.product, args.candidate)
        args.summary.mkdir(mode=0o700, parents=True, exist_ok=True)
        if args.cleanup:
            cleanup(owned_path())
        elif args.offline:
            require(args.variant == "baseline" or args.candidate is not None, "diagnostic_candidate_required")
            source = args.product if args.variant == "baseline" else args.candidate
            return 0 if offline(source, owned_path(), args.summary / (args.variant + "-" + args.build + ".json"),
                args.build, variant=args.variant, runtime=args.product / "backend/.venv",
                original_only=args.original_only) else 1
        else:
            require(args.proxy is not None, "diagnostic_proxy_required")
            require(args.candidate is not None, "diagnostic_candidate_required")
            return 0 if run(args.product, args.proxy, args.summary, args.candidate,
                original_only=args.original_only) else 1
        return 0
    except Exception as exc:
        # Never publish exception text, raw DB rows, credentials or subprocess output.
        args.summary.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_json(args.summary / ((args.variant + "-" + args.build if args.offline else "capture") + "-error.json"),
            {"status": "blocked", "error": safe_error(exc), "product_sha": PRODUCT_SHA})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
