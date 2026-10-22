"""Synthetic isolation tests; these are not production-copy acceptance."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from hashlib import sha256
import json

import pytest
from sqlalchemy import create_engine, text
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src import staging_data as data


@pytest.fixture
def snapshot(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "worker-stg")
    monkeypatch.delenv("APP_ENV", raising=False)
    item = {"database": "sawa_replay_synthetic", "settings": {}, "objects": {},
            "verified": True, "table_digests": {"probe": "a" * 64}}
    value = {"schema": 1, "id": "replay-synthetic", "state": "ready", "snapshot_at": "2026-10-22T00:00:00Z",
             "components": {name: json.loads(json.dumps(item)) for name in ("order", "shift", "school-lunch")}}
    monkeypatch.setattr(data, "manifest", lambda: value)
    return value


@pytest.mark.parametrize("feature", ["order", "shift", "school-lunch"])
def test_only_database_name_changes_on_existing_staging_host(snapshot, feature):
    from sqlalchemy.engine import make_url
    original = "postgresql+psycopg2://test_user:test_password@/orders?host=/cloudsql/sawahospitalsystem:asia-northeast2:orders-stg"
    with data.dataset_context(snapshot["id"]):
        observed = make_url(data.database_uri(original, feature))
    assert observed == make_url(original).set(database="sawa_replay_synthetic")
    assert data.database_uri(original, feature) == original


@pytest.mark.parametrize("uri", [
    "postgresql://user:password@prod/orders", "postgresql://user:password@localhost/orders",
    "postgresql://user:password@/orders?host=/cloudsql/p:r:orders-prod", "sqlite:///test.sqlite",
])
def test_replay_never_accepts_production_or_another_database_host(snapshot, uri):
    with data.dataset_context(snapshot["id"]), pytest.raises(data.DatasetUnavailable):
        data.database_uri(uri, "order")


@pytest.mark.parametrize("identity", ["prod", "test ", "", "replay-missing", "postgresql://prod/orders"])
def test_unknown_selection_blocks_without_changing_current_target(snapshot, identity):
    with pytest.raises(data.DatasetUnavailable):
        with data.dataset_context(identity):
            pytest.fail("Invalid selection entered")
    assert data.current_dataset() == "test"


def test_production_does_not_enable_replay_even_with_manifest(snapshot, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "worker-prod")
    with pytest.raises(data.DatasetUnavailable):
        with data.dataset_context(snapshot["id"]):
            pytest.fail("Production accepted replay")


def test_nested_control_context_and_captured_job_keep_their_database(snapshot):
    with data.dataset_context(snapshot["id"]):
        job = copy_context()
        with data.dataset_context("test"):
            assert data.current_dataset() == "test"
        assert data.current_dataset() == snapshot["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert pool.submit(job.run, data.current_dataset).result() == snapshot["id"]
        assert pool.submit(data.current_dataset).result() == "test"
    assert data.current_dataset() == "test"


def test_copied_original_retains_logical_reference_and_exact_generation(snapshot):
    original = "gs://source-prod-raw/original.pdf?generation=11"
    physical = "gs://sawahospitalsystem-stg-raw/stg-replay/replay-synthetic/original.pdf"
    payload = b"synthetic isolation fixture, not an operator PDF"
    entry = {"uri": physical, "generation": "99", "sha256": sha256(payload).hexdigest()}
    snapshot["components"]["order"]["objects"][original] = entry
    with data.dataset_context(snapshot["id"]):
        assert data.read_object(original, feature="order") == (physical, "99", entry["sha256"])
        assert data.verify_copy(payload, entry["sha256"]) == payload
        with pytest.raises(data.DatasetUnavailable):
            data.verify_copy(b"changed", entry["sha256"])
        for ref in (original.replace("11", "12"), original.split("?")[0], "gs://source-prod-raw/other.pdf"):
            with pytest.raises(data.DatasetUnavailable):
                data.read_object(ref, feature="order")
    assert list(snapshot["components"]["order"]["objects"]) == [original]


def test_writes_and_generated_reads_are_namespaced_and_cross_snapshot_is_blocked(snapshot, tmp_path):
    with data.dataset_context(snapshot["id"]):
        assert data.write_object("sawahospitalsystem-stg-raw", "upload.pdf") == (
            "sawahospitalsystem-stg-raw", "stg-replay/replay-synthetic/upload.pdf")
        for bucket, path in (("source-prod-raw", "upload.pdf"), ("sawahospitalsystem-stg-raw", "stg-replay/replay-other/file")):
            with pytest.raises(data.DatasetUnavailable):
                data.write_object(bucket, path)
        own = "gs://sawahospitalsystem-stg-raw/output/stg-replay/replay-synthetic/result.json"
        assert data.read_object(own, feature="order")[0] == own
        with pytest.raises(data.DatasetUnavailable):
            data.read_object(own.replace("replay-synthetic", "replay-other"), feature="order")
        selected = data.local_directory(tmp_path)
        assert selected == tmp_path / "stg-replay" / snapshot["id"]
    assert data.local_directory(tmp_path) == tmp_path


@pytest.mark.parametrize("bucket", ["sawahospitalsystem-stg-raw", "sawahospitalsystem-shift-stg", "sawahospitalsystem-school-lunch-stg-ocr"])
def test_all_existing_staging_bucket_formats_accept_only_snapshot_writes(snapshot, bucket):
    with data.dataset_context(snapshot["id"]):
        target, name = data.write_object(bucket, "new-output.json")
        assert target == bucket
        assert name == "stg-replay/replay-synthetic/new-output.json"
        uri = "gs://" + target + "/" + name
        assert data.read_object(uri, feature="shift")[0] == uri


@pytest.mark.parametrize("bucket", ["sawahospitalsystem-prod-raw", "sawahospitalsystem-shift-prod", "sawahospitalsystem-stgfake-raw"])
def test_non_staging_bucket_names_are_rejected(snapshot, bucket):
    with data.dataset_context(snapshot["id"]), pytest.raises(data.DatasetUnavailable):
        data.write_object(bucket, "new-output.json")


def test_http_read_write_and_control_scope_are_isolated(snapshot, tmp_path):
    engines = {key: create_engine("sqlite:///" + str(tmp_path / (key + ".sqlite")), connect_args={"check_same_thread": False})
               for key in ("test", snapshot["id"])}
    for key, engine in engines.items():
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE probe (value TEXT)"))
            connection.execute(text("INSERT INTO probe VALUES (:value)"), {"value": key})

    async def probe(request: Request):
        with engines[data.current_dataset()].begin() as connection:
            if request.method == "POST":
                connection.execute(text("UPDATE probe SET value=:value"), {"value": "changed-replay"})
            result = connection.execute(text("SELECT value FROM probe")).scalar_one()
        return JSONResponse({"dataset": data.current_dataset(), "value": result})

    application = Starlette(routes=[Route("/probe", probe, methods=["GET", "POST"]), Route("/portal/me", probe)])
    application.add_middleware(data.DatasetMiddleware)
    with TestClient(application) as client:
        assert client.get("/probe").json()["value"] == "test"
        client.cookies.set(data.COOKIE, snapshot["id"])
        replay = client.post("/probe")
        assert replay.json()["dataset"] == snapshot["id"]
        assert replay.headers[data.HEADER] == snapshot["id"]
        assert "no-store" in replay.headers["cache-control"]
        assert client.get("/portal/me").json()["value"] == "test"
        assert client.get("/probe", headers={data.HEADER: "test"}).json()["value"] == "test"
        assert client.post("/probe", headers={data.HEADER: "test"}).status_code == 409
        assert client.get("/probe", headers={data.HEADER: "replay-unknown"}).status_code == 503
    for engine in engines.values():
        engine.dispose()


def test_manifest_requires_all_systems_and_verified_file_proofs(monkeypatch):
    from google.cloud import storage
    monkeypatch.setenv("K_SERVICE", "worker-stg")
    content = json.dumps({"schema": 1, "id": "replay-synthetic", "state": "ready", "snapshot_at": "now", "components": {}}).encode()
    class Blob:
        def download_as_bytes(self):
            return content
    class Bucket:
        def blob(self, _path):
            return Blob()
    class Client:
        def bucket(self, _name):
            return Bucket()
    monkeypatch.setattr(storage, "Client", Client)
    with pytest.raises(data.DatasetUnavailable, match="incomplete"):
        data._read_manifest("gs://sawahospitalsystem-stg-raw/stg-replay/fixture/manifest.json", sha256(content).hexdigest())
    with pytest.raises(data.DatasetUnavailable, match="changed"):
        data._read_manifest("gs://sawahospitalsystem-stg-raw/stg-replay/fixture/manifest.json", "0" * 64)


def test_original_and_retry_task_contracts_keep_selected_snapshot(snapshot, monkeypatch):
    from src.services import ingest_delivery_dispatch_service as ingest
    from src.services import workflow_ocr_delivery_dispatch_service as workflow
    config = {"PROJECT_ID": "synthetic", "LOCATION": "asia-northeast2", "QUEUE": "synthetic",
              "TARGET_URL": "https://worker-stg-synthetic.a.run.app", "SERVICE_ACCOUNT_EMAIL": "worker@synthetic.iam.gserviceaccount.com",
              "AUDIENCE": "https://worker-stg-synthetic.a.run.app", "DISPATCH_DEADLINE_SECONDS": "60"}
    monkeypatch.setattr(workflow, "required_config", lambda: config)
    test_contract = ingest.task_contract("same-id", 1, config)
    with data.dataset_context(snapshot["id"]):
        selected = ingest.task_contract("same-id", 1, config)
        selected_workflow = workflow.task_contract("same-id", 1)
    assert test_contract["name"] != selected["name"]
    assert test_contract["http_request"]["body"] == selected["http_request"]["body"]
    # A retry executes after the original request context has ended.
    for renewed in (ingest.renew_contract(selected, "successor-id", 2), workflow.next_task_contract(selected_workflow, "same-id", 2)):
        assert renewed["http_request"]["headers"][data.HEADER] == snapshot["id"]
        assert "/tasks/" + snapshot["id"] + "-" in renewed["name"]
    assert selected["name"].endswith("same-id-g1")
    assert data.HEADER not in test_contract["http_request"]["headers"]


def test_replay_refuses_frozen_production_delivery_before_transport(snapshot, monkeypatch):
    from src.services import workflow_ocr_delivery_dispatch_service as workflow
    from google.cloud import tasks_v2
    monkeypatch.setattr(workflow.delivery, "prepare_dispatch", lambda *_: {"task_contract": {
        "http_request": {"url": "https://worker-prod-synthetic.a.run.app/worker/frozen"}}})
    def forbidden_client():
        pytest.fail("Frozen original reached Cloud Tasks transport")
    monkeypatch.setattr(tasks_v2, "CloudTasksClient", forbidden_client)
    with data.dataset_context(snapshot["id"]), pytest.raises(data.DatasetUnavailable, match="original_delivery_not_replayable"):
        workflow.dispatch("copied-delivery", 1)


@pytest.mark.parametrize("headers,identity,valid", [
    ({"X-Sawa-Dataset": "replay-case"}, "replay-case", True),
    ({"x-sawa-dataset": "replay-case"}, "replay-case", True),
    ({"X-SAWA-DATASET": "replay-case", "Content-Type": "application/json"}, "replay-case", True),
    ({"X-Sawa-Dataset": "replay-case", "x-sawa-dataset": "replay-other"}, "replay-case", False),
    ({"X-Sawa-Dataset": "replay-case", "x-sawa-dataset": "replay-case"}, "replay-case", False),
    ({"x-sawa-dataset": "replay-other"}, "replay-case", False),
    ({}, "replay-case", False), ({}, None, True),
    ({"x-sawa-dataset": "replay-case"}, None, False),
])
def test_provider_header_case_does_not_change_snapshot_and_duplicate_aliases_stop(headers, identity, valid):
    assert data.delivery_dataset_matches(headers, identity) is valid
