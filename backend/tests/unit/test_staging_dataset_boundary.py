"""Synthetic isolation tests; these are not production-copy acceptance."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from hashlib import sha256
import json
import io

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
    monkeypatch.setattr(data, "manifest_for", lambda identity: _selected_fixture(data.manifest(), identity))
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
        generation = 7
        def reload(self):
            pass
        def download_to_file(self, target, *, if_generation_match):
            assert if_generation_match == 7
            target.write(content)
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


def test_manifest_stream_indexes_exact_objects_without_unbounded_reads(snapshot):
    proof={"uri":"gs://sawahospitalsystem-stg-raw/stg-replay/replay-synthetic/objects/source", "sha256":"b"*64, "generation":"9", "raw":[0,None,False,"原本",1.25]}
    snapshot['components']['order']['objects']={f'gs://source/{i}':proof for i in range(3000)}
    class Bounded(io.BytesIO):
        def read(self, size=-1):
            assert 0 <= size <= 65536
            return super().read(size)
    value=data._parse_manifest(Bounded(json.dumps(snapshot,ensure_ascii=False).encode()))
    objects=value['components']['order']['objects']
    assert isinstance(objects,data._ManifestObjects)
    assert len(objects)==3000
    assert objects['gs://source/2999']==proof
    assert objects==snapshot['components']['order']['objects']
    second=data._parse_manifest(Bounded(json.dumps(snapshot).encode()))
    assert value==second
    assert objects.path.is_file()
    with pytest.raises(KeyError):
        objects['gs://source/not-present']


@pytest.mark.parametrize('duplicate', ['objects','reference','nested'])
def test_manifest_stream_rejects_duplicate_keys_at_every_index_level(snapshot,duplicate):
    proof='{"uri":"gs://sawahospitalsystem-stg-raw/stg-replay/replay-synthetic/x","sha256":"'+'a'*64+'","generation":"2"}'
    content=json.dumps(snapshot)
    if duplicate=='objects':
        content=content.replace('"objects": {}','"objects": {}, "objects": {}',1)
    elif duplicate=='reference':
        content=content.replace('"objects": {}','"objects": {"same":'+proof+',"same":'+proof+'}',1)
    else:
        content=content.replace('"objects": {}','"objects": {"one":'+proof[:-1]+',"generation":"3"}}',1)
    with pytest.raises(data.DatasetUnavailable,match='duplicate_key'):
        data._parse_manifest(io.BytesIO(content.encode()))


@pytest.mark.parametrize('suffix',[b' {}',b' trailing',b'\xff'])
def test_manifest_stream_never_accepts_a_valid_prefix(snapshot,suffix):
    import ijson
    with pytest.raises((ValueError,ijson.JSONError,UnicodeDecodeError)):
        data._parse_manifest(io.BytesIO(json.dumps(snapshot).encode()+suffix))


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


def _workflow_identity_row(contract, generation=1):
    from types import SimpleNamespace
    from src.services import workflow_ocr_delivery_service as delivery
    row = SimpleNamespace(id="synthetic-delivery", order_id="synthetic-order", job_id="synthetic-job",
        workflow_attempt_id="synthetic-attempt", workflow_revision=1, delivery_generation=generation,
        task_name=contract["name"], task_contract=contract, request_mode="ocr_rerun",
        selected_pdf_receipt={"uploaded_pdf_id":"synthetic-upload", "document_id":"synthetic-document",
            "order_id":"synthetic-order", "message_id":"synthetic-message", "storage_uri":"fixture://no-pdf-upload",
            "sha256":"a"*64},
        request_identity={"reparse_read_input":{"order":{"id":"synthetic-order"},
            "configured_input":{"synthetic":True}, "physical_menu_entries":[]},
            "selected_version_input":None, "selected_document_id":None, "first_pass_source":None,
            "request_parameters":{key:"ocr_rerun" if key=="request_mode" else None for key in delivery.REQUEST_KEYS}})
    row.identity_sha256=delivery._digest(delivery._identity(row))
    return row


@pytest.fixture
def workflow_transport(monkeypatch):
    from src.services import workflow_ocr_delivery_dispatch_service as transport
    config={"PROJECT_ID":"synthetic", "LOCATION":"asia-northeast2", "QUEUE":"synthetic",
        "TARGET_URL":"https://worker-stg-synthetic.a.run.app", "SERVICE_ACCOUNT_EMAIL":"worker@synthetic.iam.gserviceaccount.com",
        "AUDIENCE":"https://worker-stg-synthetic.a.run.app", "DISPATCH_DEADLINE_SECONDS":"60"}
    monkeypatch.setattr(transport,"required_config",lambda:config)
    return transport


@pytest.mark.parametrize("selected", ["test", "replay-synthetic"])
def test_workflow_identity_accepts_generated_contract_and_context_free_retry(snapshot,workflow_transport,selected):
    from src.services import workflow_ocr_delivery_service as delivery
    with data.dataset_context(selected):
        row=_workflow_identity_row(workflow_transport.task_contract("synthetic-delivery",1))
        delivery._validate_identity(row)
    # The original browser context has ended. Stored identity, not current Test,
    # determines the prefix and header for validation and successor generation.
    assert data.current_dataset()=="test"
    for generation in (2,3):
        contract=workflow_transport.next_task_contract(row.task_contract,row.id,generation)
        row.task_contract=contract;row.task_name=contract["name"];row.delivery_generation=generation
        delivery._validate_identity(row)
        assert contract["http_request"].get("headers",{})==({data.HEADER:selected} if selected!="test" else {})


@pytest.mark.parametrize("tamper", ["missing-header","foreign-header","foreign-name","duplicate-header","unknown-header"])
def test_workflow_identity_rejects_cross_dataset_task_components(snapshot,workflow_transport,tamper):
    from src.services import workflow_ocr_delivery_service as delivery
    with data.dataset_context(snapshot["id"]):
        contract=workflow_transport.task_contract("synthetic-delivery",1)
    if tamper=="missing-header":contract["http_request"]["headers"]={}
    if tamper=="foreign-header":contract["http_request"]["headers"][data.HEADER]="replay-other"
    if tamper=="foreign-name":contract["name"]=contract["name"].replace(snapshot["id"],"replay-other")
    if tamper=="duplicate-header":contract["http_request"]["headers"][data.HEADER.lower()]=snapshot["id"]
    if tamper=="unknown-header":
        contract["http_request"]["headers"][data.HEADER]="prod"
        contract["name"]=contract["name"].replace(snapshot["id"],"prod")
    with pytest.raises(ValueError,match="^workflow_ocr_delivery_task_identity_invalid$"):
        delivery._validate_identity(_workflow_identity_row(contract))


def test_workflow_create_refuses_a_contract_for_another_selected_context(snapshot,workflow_transport,monkeypatch):
    from src.services import workflow_ocr_delivery_service as delivery
    with data.dataset_context(snapshot["id"]):
        captured=workflow_transport.task_contract("synthetic-delivery",1)
    monkeypatch.setattr(workflow_transport,"task_contract",lambda *_:captured)
    with pytest.raises(ValueError,match="^workflow_ocr_delivery_task_identity_invalid$"):
        delivery.create_intent_in_session(None,order_id="synthetic-order",job_id="synthetic-job",
            workflow_attempt_id="synthetic-attempt",workflow_revision=1,request_mode="ocr_rerun",
            selected_pdf_receipt={},request_identity={})


def test_workflow_persisted_renewal_retains_copy_after_request_context_ends(snapshot,workflow_transport,tmp_path,monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from src import db,models
    from src.models.ocr_job import OcrJob
    from src.models.order import Order
    from src.models.order_workflow_state import OrderWorkflowState
    from src.models.workflow_ocr_delivery import WorkflowOcrDelivery
    from src.services import workflow_ocr_delivery_service as delivery
    engine=create_engine("sqlite:///"+str(tmp_path/"copy-renewal.sqlite"))
    models.Base.metadata.create_all(engine)
    factory=sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(db,"SessionLocal",factory)
    monkeypatch.setattr(db,"engine",engine)
    try:
        with data.dataset_context(snapshot["id"]):
            original=_workflow_identity_row(workflow_transport.task_contract("synthetic-delivery",1))
        with factory() as session:
            session.add(Order(id=original.order_id,document_uri=original.selected_pdf_receipt["storage_uri"],message_id="synthetic-message"))
            session.flush()
            session.add(OrderWorkflowState(order_id=original.order_id,revision=1,state="ocr_running",
                secondary_actions_json={"workflow_v2":{"ocr_job_id":original.job_id,"ocr_job_attempt_id":original.workflow_attempt_id}}))
            session.add(OcrJob(id=original.job_id,order_id=original.order_id,status="running",
                input_reference=original.selected_pdf_receipt["storage_uri"],metrics={
                    "workflow_attempt_id":original.workflow_attempt_id,"workflow_completion_revision":1,
                    "workflow_ocr_delivery_id":original.id,"selected_pdf_receipt":original.selected_pdf_receipt,
                    **{key:original.request_identity[key] for key in ("reparse_read_input","selected_version_input","first_pass_source")},
                    **original.request_identity["request_parameters"]}))
            session.flush()
            row=WorkflowOcrDelivery(**vars(original),state=delivery.PENDING,visit_count=0)
            session.add(row);session.commit();observed=delivery.snapshot(row)
        assert data.current_dataset()=="test"
        assert delivery.renew_missing_delivery(original.id,observed) is True
        with factory() as session:
            row=session.get(WorkflowOcrDelivery,original.id)
            delivery._validate_identity(row)
            assert row.delivery_generation==2 and row.identity_sha256==original.identity_sha256
            assert row.request_identity==original.request_identity and row.selected_pdf_receipt==original.selected_pdf_receipt
            assert row.task_contract["http_request"]["headers"]=={data.HEADER:snapshot["id"]}
            assert row.task_name.endswith("/tasks/"+snapshot["id"]+"-"+original.id+"-g2")
    finally:
        engine.dispose()


def _selected_fixture(value, identity):
    if value["id"] != identity:
        raise data.DatasetUnavailable("staging_dataset_snapshot_unavailable")
    return value
