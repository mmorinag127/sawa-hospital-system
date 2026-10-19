"""Synthetic metadata on owned formal databases; no OCR or live identity proof."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
from hashlib import sha256
import json
import time
from types import SimpleNamespace

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa, ed25519
from cryptography.x509.oid import NameOID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.auth import exceptions, crypt, jwt
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from test_portal_admin_write_authority import owned, owned_pg
from test_ingest_delivery_contract import payload
from test_native_ingest_sql_component import native_sql as canonical_native_sql
from src import db
from src.api import worker
from src.models.ingest_delivery import IngestDelivery
from src.models.ingest_job import IngestJob
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.document import OrderDocument
from src.models.uploaded_pdf import UploadedPdf
from src.models.order_workflow_state import OrderWorkflowState
from src.services import ingest_delivery_auth_service as auth
from src.services import ingest_delivery_service as delivery
from src.services import ingest_delivery_dispatch_service as dispatch
from src.services import ingest_job_service as ingest
from src.services import ocr_job_service
from src.services import workflow_attempt_guard_service as guard
from src.workers.ingest_mail_adapter import parse_ingest_payload


def service_handoff(engine, payload, monkeypatch, name, *, native=False):
    for key, value in {"PROJECT_ID":"fixture-project", "LOCATION":"fixture-location", "QUEUE":"fixture-queue",
        "TARGET_URL":"https://fixture.invalid", "SERVICE_ACCOUNT_EMAIL":"fixture@fixture.iam.gserviceaccount.com",
        "AUDIENCE":"fixture-audience", "DISPATCH_DEADLINE_SECONDS":"15"}.items():
        monkeypatch.setenv(dispatch.PREFIX + key, value)
    for role in ('WORKER_', 'DRAIN_'):
        for key, value in {'ISSUER':'https://accounts.google.com', 'AUDIENCE':'fixture-audience',
            'EMAIL':'fixture@fixture.iam.gserviceaccount.com', 'SUBJECT':'fixture-subject'}.items():
            monkeypatch.setenv(dispatch.PREFIX + role + key, value)
    with Session(engine) as session:
        template = session.get(Order, session.execute(sa.select(Order.id).where(
            Order.message_id == 'sql-component-message')).scalar_one())
        document_uri, projection = template.document_uri, template.template_version_id
        configured_source = deepcopy(session.get(IngestJob, 'sql-component-message').payload)
    data = dict(configured_source, message_id=name, intake_protocol='native_ingest_v2' if native else None, uploaded_pdf_id=None,
        order_id=None, order_document_id=None)
    ingest.create_ingest_receipt(data)
    with Session(engine) as session:
        row = session.execute(sa.select(IngestDelivery).where(IngestDelivery.job_id == name)).scalar_one()
        identity, generation = row.id, row.generation
    claim = delivery.claim_delivery(identity, generation)
    with Session(engine) as session, session.begin():
        session.add(UploadedPdf(id=name + '-upload', message_id=name, storage_uri=document_uri,
            content_sha256=data['content_sha256'], original_filename='authored.pdf',
            received_at=datetime.fromisoformat(data['received_at'])))
        session.add(OrderDocument(id=name + '-document', storage_uri=document_uri, source_email_id=name,
            received_at=datetime.fromisoformat(data['received_at']), status='processing'))
        session.flush()
        session.add(Order(id=name + '-order', facility_code=data['facility_hint'], week_code=data['week_hint'],
            template_version_id=projection, current_document_id=name + '-document',
            document_uri=document_uri, message_id=name))
    parsed = parse_ingest_payload(claim['lease']['payload'])
    parsed.order_id, parsed.order_document_id = name + '-order', name + '-document'
    with delivery.claim_context(claim), ingest.ingest_execution_context(claim['lease']):
        ingest.prepare_ingest_ocr_job(claim['lease'], name + '-ocr', parsed)
        if native:
            from src.services import order_workflow_v2_service as workflow
            with Session(engine) as session, session.begin():
                workflow.initialize_ingest_context(session, session.get(Order, parsed.order_id),
                    data['facility_selection'], name + '-ocr')
        capture = guard.capture_nonworkflow_execution(name + '-ocr', order_id=parsed.order_id,
            input_reference=data['pdf_uri'], expected_document_id=parsed.order_document_id,
            ingest_ocr_mode='hakodate' if native else None)
    configured = capture['orders'][capture['order_id']]['configured_input']
    selected = capture['selected_pdf_receipt']
    pipeline = {"job_id":capture["job_id"], "order_id":capture["order_id"], "execution_id":capture["execution_id"],
        "workflow_attempt_id":None, "workflow_revision":None, "selected_input_reference":data["pdf_uri"],
        "input_reference":"gs://fixture/input.pdf", "output_reference":"gs://fixture/output.json",
        "generation":"1", "pipeline_job_id":"fixture:input.pdf:1", "selected_pdf_receipt":selected,
        "input_sha256":selected["sha256"], "configured_input_sha256":sha256(json.dumps(configured,
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    monkeypatch.setattr(ocr_job_service, 'validate_own_pipeline_result', lambda *args, **kwargs: None)
    with delivery.claim_context(claim), Session(engine) as session, session.begin():
        job = session.get(OcrJob, capture['job_id'])
        job.status, job.output_reference = 'awaiting_output', pipeline['output_reference']
        job.metrics = {**job.metrics, 'pipeline_delivery_receipt':pipeline}
    assert delivery.acknowledge_claim(claim) == 'durable_handoff'
    with engine.begin() as connection:
        connection.execute(sa.update(OcrJob).where(OcrJob.id == capture['job_id']).values(
            updated_at=datetime.utcnow() - timedelta(days=2 if name == 'A' else 1)))
    return identity, capture


@pytest.fixture(params=['owned', 'owned_pg'], ids=['formal-sqlite', 'formal-pg16'])
def database(request, payload, tmp_path, monkeypatch):
    engine, _ = request.getfixturevalue(request.param)
    for key, value in {"PROJECT_ID":"fixture-project", "LOCATION":"fixture-location", "QUEUE":"fixture-queue",
        "TARGET_URL":"https://fixture.invalid", "SERVICE_ACCOUNT_EMAIL":"fixture@fixture.iam.gserviceaccount.com",
        "AUDIENCE":"fixture-audience", "DISPATCH_DEADLINE_SECONDS":"15"}.items():
        monkeypatch.setenv(dispatch.PREFIX + key, value)
    for role in ('WORKER_', 'DRAIN_'):
        for key, value in {'ISSUER':'https://accounts.google.com', 'AUDIENCE':'fixture-audience',
            'EMAIL':'fixture@fixture.iam.gserviceaccount.com', 'SUBJECT':'fixture-subject'}.items():
            monkeypatch.setenv(dispatch.PREFIX + role + key, value)
    canonical_native_sql.__wrapped__(sessionmaker(engine, autoflush=False), tmp_path, monkeypatch,
                                    SimpleNamespace(param=None))
    # The setup claim is not a recovery scenario. Keep only its source rows;
    # each case creates its own actual delivery and claim.
    with engine.begin() as connection:
        connection.execute(sa.delete(IngestDelivery).where(IngestDelivery.job_id == 'sql-component-message'))
    return engine


def capture(identity):
    with db.session_scope() as session:
        order = session.execute(sa.select(Order).where(Order.message_id == 'sql-component-message')).scalar_one()
        order_id, uri = order.id, order.document_uri
        if session.get(OcrJob, identity) is None:
            session.add(OcrJob(id=identity, status='pending', input_reference=uri))
    return guard.capture_nonworkflow_execution(identity, order_id=order_id, input_reference=uri)


def seed(engine, identity, *, age=1, status='awaiting_output', metrics=None, stamp=None):
    if metrics is None:
        metrics = {'nonworkflow_execution': capture(identity)}
    with engine.begin() as connection:
        exists = connection.execute(sa.select(OcrJob.input_reference).where(OcrJob.id == identity)).scalar_one_or_none()
        statement = OcrJob.__table__.update().where(OcrJob.id == identity) if exists else OcrJob.__table__.insert().values(id=identity)
        connection.execute(statement.values(status=status,
            input_reference=exists if exists else 'fixture://' + identity,
            updated_at=stamp if stamp is not None else datetime.utcnow() - timedelta(days=age),
            metrics=metrics))


def snapshot(engine):
    with engine.connect() as connection:
        return {name: sorted(repr(tuple(row)) for row in connection.exec_driver_sql('SELECT * FROM "' + name + '"'))
                for name in sa.inspect(connection).get_table_names()}


@pytest.mark.parametrize('tie', [False, True])
@pytest.mark.parametrize('change', ['accepted', 'not-ack', 'missing-handoff', 'stale-owner',
    'missing-delivery', 'changed-generation', 'changed-digest', 'changed-claim', 'old-business-generation'])
def test_service_delivery_admission_matches_actual_recovery_owner(database, payload, monkeypatch, tie, change):
    a, boundary_a = service_handoff(database, payload, monkeypatch, 'A')
    _, boundary_b = service_handoff(database, payload, monkeypatch, 'B')
    with database.begin() as connection:
        if tie:
            connection.execute(sa.update(OcrJob).values(updated_at=datetime.utcnow() - timedelta(days=2)))
        if change == 'missing-delivery':
            connection.execute(sa.delete(IngestDelivery).where(IngestDelivery.id == a))
        elif change in {'not-ack', 'missing-handoff', 'stale-owner', 'old-business-generation'}:
            values = {'not-ack':{'state':'CLAIMED'}, 'missing-handoff':{'handoff_receipt':sa.null()},
                'stale-owner':{'owner_lease':dict(boundary_a['ingest_lease'], claim_id='foreign')},
                'old-business-generation':{'business_generation':'foreign'}}[change]
            connection.execute(sa.update(IngestDelivery).where(IngestDelivery.id == a).values(**values))
        elif change.startswith('changed-'):
            field = {'changed-generation':'generation', 'changed-digest':'input_digest', 'changed-claim':'claim_id'}[change]
            connection.execute(sa.update(IngestJob).where(IngestJob.id == 'A').values(**{field:'foreign'}))
    before = snapshot(database)
    candidates = ingest.list_ocr_recovery_candidates(1)
    assert [row['id'] for row in candidates] == (['A-ocr'] if change == 'accepted' else ['B-ocr'])
    if change in {'accepted', 'not-ack', 'missing-handoff', 'stale-owner', 'missing-delivery', 'old-business-generation'}:
        with delivery.recovery_context(boundary_a) as eligible:
            assert eligible is (change == 'accepted')
    with delivery.recovery_context(boundary_b) as eligible:
        assert eligible
    assert candidates[0]['metrics']['nonworkflow_execution'] == (boundary_a if change == 'accepted' else boundary_b)
    assert snapshot(database) == before


@pytest.mark.parametrize('all_invalid', [False, True])
def test_multiple_service_delivery_rejections_advance_same_time_pages(database, payload, monkeypatch, all_invalid):
    stamp = datetime.utcnow() - timedelta(days=2)
    for index in range(5):
        identity, _ = service_handoff(database, payload, monkeypatch, f'bad-{index}')
        with database.begin() as connection:
            connection.execute(sa.update(IngestDelivery).where(IngestDelivery.id == identity).values(state='SENT'))
    if not all_invalid:
        service_handoff(database, payload, monkeypatch, 'valid')
    with database.begin() as connection:
        connection.execute(sa.update(OcrJob).values(updated_at=stamp))
    before = snapshot(database)
    statements = []
    def observe(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
        assert len(statements) < 30
    sa.event.listen(database, 'before_cursor_execute', observe)
    try:
        assert [row['id'] for row in ingest.list_ocr_recovery_candidates(1)] == ([] if all_invalid else ['valid-ocr'])
    finally:
        sa.event.remove(database, 'before_cursor_execute', observe)
    assert all(not query.lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for query in statements)
    assert snapshot(database) == before


def test_real_recovery_loop_skips_unacknowledged_delivery(database, payload, monkeypatch):
    from src.workers import ingest_worker
    identity, _ = service_handoff(database, payload, monkeypatch, 'A')
    service_handoff(database, payload, monkeypatch, 'B')
    with database.begin() as connection:
        connection.execute(sa.update(IngestDelivery).where(IngestDelivery.id == identity).values(state='CLAIMED'))
    before = snapshot(database)
    visited = []
    monkeypatch.setattr(ingest_worker, '_recover_captured_ocr_job', lambda job: visited.append(job['id']) or False)
    assert ingest_worker.run_ocr_job_recovery_once(1) == 0
    assert visited == ['B-ocr']
    assert snapshot(database) == before


@pytest.fixture
def store(database, monkeypatch):
    from src.services import workflow_ocr_delivery_execution_service as exclusion
    monkeypatch.setattr(exclusion, 'engine', database)
    for key, value in {"PROJECT_ID":"fixture-project", "LOCATION":"fixture-location", "QUEUE":"fixture-queue",
        "TARGET_URL":"https://fixture.invalid", "SERVICE_ACCOUNT_EMAIL":"fixture@fixture.iam.gserviceaccount.com",
        "AUDIENCE":"fixture-audience", "DISPATCH_DEADLINE_SECONDS":"15"}.items():
        monkeypatch.setenv(dispatch.PREFIX + key, value)
    for role in ('WORKER_', 'DRAIN_'):
        for key, value in {'ISSUER':'https://accounts.google.com', 'AUDIENCE':'fixture-audience',
            'EMAIL':'fixture@fixture.iam.gserviceaccount.com', 'SUBJECT':'fixture-subject'}.items():
            monkeypatch.setenv(dispatch.PREFIX + role + key, value)
    return sessionmaker(database, autoflush=False, expire_on_commit=True)


@pytest.fixture
def successor_producer(store, payload, monkeypatch):
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    from src.services import order_service, ingest_delivery_successor_service as successor
    monkeypatch.setenv('OCR_AUTO_LLM_REPARSE_ON_INGEST', '1')
    monkeypatch.setenv('OCR_AUTO_LLM_REPARSE_PROVIDER', 'gemini')
    with store() as session:
        template = session.execute(sa.select(Order).where(Order.message_id == 'sql-component-message')).scalar_one()
        uri, projection = template.document_uri, template.template_version_id
        payload = dict(session.get(IngestJob, 'sql-component-message').payload,
            message_id=payload['message_id'], intake_protocol=None, uploaded_pdf_id=None)
    ingest.create_ingest_receipt(payload)
    with store() as session:
        row = session.execute(sa.select(IngestDelivery).where(IngestDelivery.job_id == payload['message_id'])).scalar_one()
        identity, generation = row.id, row.generation
    claim = delivery.claim_delivery(identity, generation)
    with delivery.claim_context(claim), store.begin() as session:
        session.add(OrderDocument(id='producer-document', storage_uri=uri,
            source_email_id=payload['message_id'], received_at=datetime.fromisoformat(payload['received_at']),
            status='success'))
        session.add(UploadedPdf(id='producer-upload', message_id=payload['message_id'],
            storage_uri=uri, content_sha256=payload['content_sha256'],
            original_filename='authored.pdf', received_at=datetime.fromisoformat(payload['received_at'])))
        session.flush()
        session.add(Order(id='producer-order', facility_code=payload['facility_hint'], week_code=payload['week_hint'],
            template_version_id=projection, current_document_id='producer-document', document_uri=uri,
            message_id=payload['message_id']))
        # No ORM relationship orders these mappers; persist the FK parent first.
        session.flush()
        session.add(OcrJob(id='producer-parent-job', order_id='producer-order', status='running',
            input_reference=uri, metrics={'ingest_lease':deepcopy(claim['lease'])}))
    with delivery.claim_context(claim), ingest.ingest_execution_context(claim['lease']):
        boundary = guard.capture_nonworkflow_execution('producer-parent-job', order_id='producer-order',
            input_reference=uri, expected_document_id='producer-document')
    own_payload = {'table_raw':'authored metadata only', 'unknown':{'zero':0, 'null':None},
        '_ocr_execution':{'job_id':boundary['job_id'], 'nonworkflow_execution_id':boundary['execution_id'],
            'evidence_run_id':'producer-evidence', 'workflow_attempt_id':None, 'workflow_revision':None}}
    with store.begin() as session:
        session.add(OrderOcrEvidenceRun(id='producer-evidence', order_id='producer-order', schema_version='v1_legacy',
            payload_json=own_payload, artifact_digest='recorded-authored-digest', status='ready'))
    # Retain the original fixture's terminal-CAS isolation, not a new authority bypass.
    monkeypatch.setattr(order_service, '_workflow_attempt_write_fence',
        lambda session, job_id, order_id, **kwargs: delivery.fence_original_write(session, claim['lease']))

    def produce(*, aborted=False):
        with delivery.claim_context(claim), store.begin() as session:
            receipt = order_service._complete_first_pass_job_in_session(session,
                session.get(Order, 'producer-order'), session.get(OrderDocument, 'producer-document'), boundary,
                {'id':'producer-evidence', 'selectable':True, 'payload_json':own_payload}, applied=True)
            if aborted:
                raise RuntimeError('authored_transaction_abort')
            return receipt

    return {'produce':produce, 'capture':boundary, 'claim':claim, 'payload':own_payload,
        'service':successor, 'parent_id':identity, 'generation':generation}


@pytest.mark.parametrize('change', ['accepted', 'not-ack', 'missing-handoff', 'refused', 'missing-successor',
                                  'changed-owner', 'changed-generation', 'changed-digest'])
def test_service_authored_successor_admission(database, store, successor_producer, monkeypatch, change):
    from src.models.ingest_delivery_successor import IngestDeliverySuccessor
    from src.workers import ingest_worker
    source = successor_producer
    source['produce']()
    claim = source['claim']
    with delivery.claim_context(claim):
        assert ingest.complete_ingest_job(claim['lease']['id'], lease=claim['lease'])
        assert delivery.acknowledge_claim(claim) == 'business_done'
    with store() as session:
        row = session.execute(sa.select(IngestDeliverySuccessor)).scalar_one()
        identity, child, original = row.id, row.job_id, deepcopy(row.original)
    boundary = original['execution_input']
    configured = boundary['orders'][boundary['order_id']]['configured_input']
    selected = boundary['selected_pdf_receipt']
    pipeline = {'job_id':child, 'order_id':boundary['order_id'], 'execution_id':boundary['execution_id'],
        'workflow_attempt_id':None, 'workflow_revision':None, 'selected_input_reference':boundary['input_reference'],
        'input_reference':'gs://fixture/successor.pdf', 'output_reference':'gs://fixture/successor.json',
        'generation':'2', 'pipeline_job_id':'fixture:successor.pdf:2', 'selected_pdf_receipt':selected,
        'input_sha256':selected['sha256'], 'configured_input_sha256':sha256(json.dumps(configured,
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    waiting = {'job_id':child, 'order_id':boundary['order_id'], 'execution_id':boundary['execution_id'],
        'workflow_attempt_id':None, 'workflow_revision':None, 'output_reference':pipeline['output_reference'],
        'request_kwargs':deepcopy(original['request'])}
    def pending(*args, **kwargs):
        with store.begin() as session:
            job = session.get(OcrJob, child)
            job.status, job.output_reference = 'awaiting_output', pipeline['output_reference']
            job.metrics = {**job.metrics, 'pipeline_delivery_receipt':pipeline, 'pending_receipt':waiting}
    monkeypatch.setattr(ingest_worker, '_run_auto_llm_reparse', pending)
    monkeypatch.setattr(ocr_job_service, 'validate_own_pipeline_result', lambda *args, **kwargs: None)
    assert source['service'].process(identity, 1)['result'] == 'durable_handoff'
    with source['service'].recovery_context(boundary) as eligible:
        assert eligible
    with database.begin() as connection:
        connection.execute(sa.update(OcrJob).where(OcrJob.id == child).values(
            updated_at=datetime.utcnow() - timedelta(days=2)))
        if change == 'missing-successor':
            connection.execute(sa.delete(IngestDeliverySuccessor).where(IngestDeliverySuccessor.id == identity))
        elif change in {'not-ack', 'missing-handoff', 'refused', 'changed-owner'}:
            changed_original = deepcopy(original)
            changed_original['execution_input']['ingest_lease']['claim_id'] = 'foreign'
            values = {'not-ack':{'state':'CLAIMED'}, 'missing-handoff':{'handoff_receipt':sa.null()},
                'refused':{'state':'REFUSED', 'terminal_receipt':{'kind':'owned_refusal'}},
                'changed-owner':{'original':changed_original}}[change]
            connection.execute(sa.update(IngestDeliverySuccessor).where(IngestDeliverySuccessor.id == identity).values(**values))
        elif change in {'changed-generation', 'changed-digest'}:
            field = 'generation' if change == 'changed-generation' else 'input_digest'
            connection.execute(sa.update(IngestJob).where(IngestJob.id == claim['lease']['id']).values(**{field:'foreign'}))
    seed(database, 'Z-good')
    before = snapshot(database)
    result = ingest.list_ocr_recovery_candidates(1)
    assert [row['id'] for row in result] == ([child] if change == 'accepted' else ['Z-good'])
    if change == 'accepted':
        assert result[0]['metrics']['nonworkflow_execution'] == boundary
    assert snapshot(database) == before


@pytest.mark.parametrize('failure', ['successor-insert', 'caller-abort'])
def test_successor_parent_insert_and_receipt_are_one_rollback(database, successor_producer, failure):
    before = snapshot(database)
    statements = []
    def observe(connection, cursor, statement, parameters, context, many):
        if statement.startswith('INSERT INTO ocr_jobs '):
            statements.append('ocr')
        if statement.startswith('INSERT INTO ingest_delivery_successors '):
            statements.append('successor')
            if failure == 'successor-insert':
                raise RuntimeError('synthetic successor persistence failure')
    sa.event.listen(database, 'before_cursor_execute', observe)
    try:
        with pytest.raises(RuntimeError, match='synthetic successor persistence failure|authored_transaction_abort'):
            successor_producer['produce'](aborted=failure == 'caller-abort')
    finally:
        sa.event.remove(database, 'before_cursor_execute', observe)
    assert statements == (['ocr', 'successor'] if failure == 'successor-insert' else ['ocr'])
    assert snapshot(database) == before


@pytest.mark.parametrize('tie', [False, True])
def test_bad_source_and_nonrecoverable_pages_do_not_consume_limit(database, tie):
    stamp = datetime.utcnow() - timedelta(days=2) if tie else None
    seed(database, 'A-missing', age=5, metrics={}, stamp=stamp)
    seed(database, 'B-owner-empty', age=4, metrics={'nonworkflow_execution': {}}, stamp=stamp)
    seed(database, 'C-not-stale', age=3, status='running', metrics={
        'nonworkflow_execution': capture('C-not-stale'), 'result_state': 'processing',
        'stage_updated_at': datetime.utcnow().isoformat()}, stamp=stamp)
    seed(database, 'D-good', age=2, stamp=stamp)
    seed(database, 'E-good', age=1, stamp=stamp)
    before = snapshot(database)
    assert [row['id'] for row in ingest.list_ocr_recovery_candidates(1)] == ['D-good']
    assert [row['id'] for row in ingest.list_ocr_recovery_candidates(2)] == ['D-good', 'E-good']
    assert snapshot(database) == before


def test_all_invalid_is_finite_and_retains_warning_and_original(database):
    for index in range(9):
        seed(database, f'bad-{index}', metrics={}, age=10-index)
    before = snapshot(database)
    messages, statements = [], []
    sink = ingest.logger.add(lambda message: messages.append(str(message)))
    def observe(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
        assert len(statements) < 30, 'keyset must advance rather than loop over the same rejected row'
    sa.event.listen(database, 'before_cursor_execute', observe)
    try:
        assert ingest.list_ocr_recovery_candidates(2) == []
    finally:
        ingest.logger.remove(sink)
        sa.event.remove(database, 'before_cursor_execute', observe)
    assert len(messages) == 9
    assert all('processing-start input missing' in message for message in messages)
    assert all(not statement.lstrip().upper().startswith(('UPDATE ', 'DELETE ', 'INSERT ')) for statement in statements)
    assert snapshot(database) == before


@pytest.mark.parametrize('status', ['done', 'failed', 'cancelled'])
def test_terminal_jobs_never_displace_live_capture(database, status):
    seed(database, 'A-terminal', age=5, status=status)
    seed(database, 'B-good')
    before = snapshot(database)
    assert [row['id'] for row in ingest.list_ocr_recovery_candidates(1)] == ['B-good']
    assert snapshot(database) == before


def test_formal_schema_refuses_unknown_timestamp_without_date_guess(database):
    seed(database, 'A-null', metrics={})
    seed(database, 'B-good')
    before = snapshot(database)
    with pytest.raises(sa.exc.IntegrityError), database.begin() as connection:
        connection.execute(OcrJob.__table__.update().where(OcrJob.id == 'A-null').values(updated_at=None))
    assert [row['id'] for row in ingest.list_ocr_recovery_candidates(1)] == ['B-good']
    assert snapshot(database) == before


@pytest.mark.parametrize('field,value', [('job_id','foreign'), ('job_order_id','foreign'),
                                        ('input_reference','fixture://foreign'), ('execution_id',None),
                                        ('ingest_lease',True), ('ingest_lease',{}), ('orders',{})])
def test_uncaptured_or_mismatched_owner_does_not_displace_good_job(database, field, value):
    source = dict(capture('A-invalid'), **{field:value})
    seed(database, 'A-invalid', age=5, metrics={'nonworkflow_execution':source})
    seed(database, 'B-good')
    before = snapshot(database)
    assert [row['id'] for row in ingest.list_ocr_recovery_candidates(1)] == ['B-good']
    assert snapshot(database) == before


def test_stale_ingest_owner_excluded_and_write_cas_stays_strict(database):
    lease = {'id': 'original', 'generation': 'g1', 'claim_id': 'claim', 'attempts': 1, 'input_digest': 'digest'}
    with database.begin() as connection:
        connection.execute(IngestJob.__table__.insert().values(id='original', status='processing', payload={},
            generation='g2', claim_id='new-claim', input_digest='new-digest'))
        connection.execute(IngestDelivery.__table__.insert().values(id='D', job_id='original',
            business_generation='g1', input_digest='digest', original_payload={}, generation=2,
            state='CLAIMED', claim_token='current', owner_lease=lease, task_contract={}))
    source = capture('A-stale')
    source['ingest_lease'] = lease
    seed(database, 'A-stale', age=5, metrics={'nonworkflow_execution': source})
    seed(database, 'B-good')
    before = snapshot(database)
    assert [row['id'] for row in ingest.list_ocr_recovery_candidates(1)] == ['B-good']
    with delivery.claim_context({'delivery_id':'D', 'generation':1, 'claim_token':'old'}), db.session_scope() as session:
        assert delivery.fence_original_write(session, lease, read_only=True) is False
    assert snapshot(database) == before


@pytest.mark.parametrize('revision', [1, None, True, 'not-an-integer', {'invalid':0}])
def test_stale_or_invalid_workflow_revision_is_not_admitted(database, revision):
    with database.begin() as connection:
        connection.execute(Order.__table__.insert().values(id='workflow-order', document_uri='fixture://doc',
            message_id='synthetic', received_at=datetime.utcnow()))
        connection.execute(OrderWorkflowState.__table__.insert().values(order_id='workflow-order', revision=2))
    seed(database, 'A-stale-workflow', age=5, metrics={
        'workflow_attempt_id':'old', 'workflow_completion_revision':revision,
        'pending_receipt':{'workflow_attempt_id':'old'}})
    with database.begin() as connection:
        connection.execute(OcrJob.__table__.update().where(OcrJob.id == 'A-stale-workflow').values(order_id='workflow-order'))
    seed(database, 'B-good')
    before = snapshot(database)
    assert [row['id'] for row in ingest.list_ocr_recovery_candidates(1)] == ['B-good']
    assert snapshot(database) == before


def test_recorded_workflow_pending_receipt_remains_admissible(database):
    request = {'ocr_prompt':None, 'prompt_preset':None, 'ocr_provider':None, 'ocr_model':None, 'llm_assist':False}
    receipt = {'job_id':'workflow-job', 'order_id':'workflow-order', 'workflow_attempt_id':'current',
               'workflow_revision':2, 'execution_id':None, 'output_reference':'fixture://output', 'request_kwargs':request}
    metrics = {'workflow_attempt_id':'current', 'workflow_completion_revision':2, 'request_mode':'ocr_reparse',
               'pending_receipt':receipt, 'reparse_ocr_prompt':None, 'reparse_prompt_preset':None,
               'reparse_requested_provider':None, 'reparse_ocr_model':None, 'reparse_llm_assist':False}
    with database.begin() as connection:
        connection.execute(Order.__table__.insert().values(id='workflow-order', document_uri='fixture://doc',
            message_id='synthetic', received_at=datetime.utcnow()))
        connection.execute(OrderWorkflowState.__table__.insert().values(order_id='workflow-order', revision=2))
    seed(database, 'workflow-job', metrics=metrics)
    with database.begin() as connection:
        connection.execute(OcrJob.__table__.update().where(OcrJob.id == 'workflow-job').values(
            order_id='workflow-order', output_reference='fixture://output'))
    before = snapshot(database)
    candidate = ingest.list_ocr_recovery_candidates(1)
    assert [row['id'] for row in candidate] == ['workflow-job']
    assert candidate[0]['metrics'] == metrics
    assert candidate[0]['metrics']['reparse_llm_assist'] is False
    assert snapshot(database) == before


def test_real_recovery_loop_preserves_missing_source_and_visits_valid_job(database, monkeypatch):
    from src.workers import ingest_worker
    seed(database, 'A-missing', age=5, metrics={})
    seed(database, 'B-good')
    before = snapshot(database)
    visited = []
    def synthetic_recovery(job):
        # Admission proof only: no PDF, OCR, quantity or domain adoption is simulated.
        visited.append(job['id'])
        return False
    monkeypatch.setattr(ingest_worker, '_recover_captured_ocr_job', synthetic_recovery)
    assert ingest_worker.run_ocr_job_recovery_once(1) == 0
    assert visited == ['B-good']
    assert snapshot(database) == before


@pytest.mark.parametrize('limit', [None, True, False, 0, -1, '1'])
def test_reader_invalid_limit_stops_before_session(monkeypatch, limit):
    calls = []
    def forbidden():
        calls.append(True)
        raise AssertionError('no database for invalid admission limit')
    monkeypatch.setattr(ingest, 'session_scope', forbidden)
    with pytest.raises(ValueError, match='ocr_recovery_limit_required'):
        ingest.list_ocr_recovery_candidates(limit)
    assert calls == []


@pytest.fixture
def identity_config(monkeypatch):
    claims = {'iss':'https://accounts.google.com', 'aud':'synthetic-audience',
              'email':'fixture@fixture.iam.gserviceaccount.com', 'sub':'synthetic-subject', 'email_verified':True}
    for base in ('HOSPITAL_INGEST_DELIVERY_', 'HOSPITAL_WORKFLOW_OCR_DELIVERY_'):
        for role in ('WORKER_', 'DRAIN_'):
            for key, value in {'EMAIL':claims['email'], 'SUBJECT':claims['sub'],
                               'AUDIENCE':claims['aud'], 'ISSUER':claims['iss']}.items():
                monkeypatch.setenv(base + role + key, value)
    return claims


ENDPOINTS = [('/worker/ingest-deliveries/process', {'delivery_id':'D','generation':1}),
             ('/worker/ingest-deliveries/auto-llm', {'delivery_id':'D','generation':1}),
             ('/worker/ingest-deliveries/drain?limit=1', None),
             ('/worker/workflow-ocr-deliveries/D/1', None),
             ('/worker/workflow-ocr-deliveries/drain?limit=1', None)]


def http_client():
    app = FastAPI()
    app.include_router(worker.router)
    assert not app.dependency_overrides
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize('path,body', ENDPOINTS)
@pytest.mark.parametrize('fault,status', [
    (exceptions.TransportError('synthetic-private-transport'), 503),
    (exceptions.DefaultCredentialsError('synthetic-private-config'), 503),
    (exceptions.TimeoutError('synthetic-private-timeout'), 503),
    (RuntimeError('synthetic-private-unknown'), 503),
    (json.JSONDecodeError('synthetic-private-certs', '', 0), 503),
    (ValueError('synthetic-private-invalid-token'), 401),
    (exceptions.MalformedError('synthetic-private-malformed-token'), 401),
    (exceptions.GoogleAuthError('synthetic-private-invalid-issuer'), 401),
])
def test_actual_http_verification_classifies_failures_without_auth_override(monkeypatch, identity_config, path, body, fault, status):
    def reject(*args):
        raise fault
    monkeypatch.setattr(auth.id_token, 'verify_oauth2_token', reject)
    def no_database(*args, **kwargs):
        pytest.fail('denied/unavailable authentication must not reach database')
    monkeypatch.setattr(db, 'SessionLocal', no_database)
    with http_client() as client:
        response = client.post(path, json=body, headers={'Authorization':'Bearer synthetic'})
    assert response.status_code == status
    assert 'synthetic-private' not in response.text


@pytest.mark.parametrize('path,body', ENDPOINTS)
@pytest.mark.parametrize('field,value', [('sub','foreign'), ('email','foreign@example.invalid'),
                                        ('aud','foreign'), ('email_verified',False), ('iss','foreign')])
def test_foreign_identity_remains_403(monkeypatch, identity_config, path, body, field, value):
    claims = dict(identity_config, **{field:value})
    # The real verifier rejects foreign issuers. Ingest also has its configured issuer guard.
    if field == 'iss' and 'workflow-ocr' in path:
        def invalid_issuer(*args):
            raise exceptions.GoogleAuthError('invalid issuer')
        monkeypatch.setattr(auth.id_token, 'verify_oauth2_token', invalid_issuer)
        expected = 401
    else:
        monkeypatch.setattr(auth.id_token, 'verify_oauth2_token', lambda *a: claims)
        expected = 403
    with http_client() as client:
        assert client.post(path, json=body, headers={'Authorization':'Bearer synthetic'}).status_code == expected


@pytest.mark.parametrize('limit', [0, -1])
@pytest.mark.parametrize('denial', [None, 401, 403, 503])
def test_ingest_limit_guard_after_auth_before_any_database_or_dispatch(monkeypatch, identity_config, limit, denial):
    calls = []
    def verify(*args):
        if denial == 503:
            raise RuntimeError('synthetic unavailable')
        return dict(identity_config, sub='foreign') if denial == 403 else identity_config
    monkeypatch.setattr(auth.id_token, 'verify_oauth2_token', verify)
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('invalid limit must not dispatch or connect')
    monkeypatch.setattr(dispatch, 'drain', forbidden)
    monkeypatch.setattr(db, 'SessionLocal', forbidden)
    with http_client() as client:
        headers = {} if denial == 401 else {'Authorization':'Bearer synthetic'}
        response = client.post(f'/worker/ingest-deliveries/drain?limit={limit}', headers=headers)
    assert response.status_code == (denial or 422)
    assert calls == []


@pytest.mark.parametrize('fault', [RuntimeError('database unavailable'), ValueError('unexpected domain failure')])
def test_valid_limit_does_not_mask_dispatch_failure_as_422(monkeypatch, identity_config, fault):
    monkeypatch.setattr(auth.id_token, 'verify_oauth2_token', lambda *a: identity_config)
    def fail(*args):
        raise fault
    monkeypatch.setattr(dispatch, 'drain', fail)
    with http_client() as client:
        assert client.post('/worker/ingest-deliveries/drain?limit=1', headers={'Authorization':'Bearer synthetic'}).status_code == 500


@pytest.mark.parametrize('claims', [None, [], 'not-authority', 0])
def test_malformed_verifier_result_is_unavailable(monkeypatch, identity_config, claims):
    monkeypatch.setattr(auth.id_token, 'verify_oauth2_token', lambda *a: claims)
    with http_client() as client:
        assert client.post('/worker/ingest-deliveries/drain?limit=0', headers={'Authorization':'Bearer synthetic'}).status_code == 503


@pytest.mark.parametrize('path,body', ENDPOINTS)
def test_missing_bearer_still_401_with_configured_authority(identity_config, path, body):
    with http_client() as client:
        assert client.post(path, json=body).status_code == 401


@pytest.mark.parametrize('path,body', ENDPOINTS)
def test_missing_config_stops_503_before_verification(monkeypatch, identity_config, path, body):
    for base in ('HOSPITAL_INGEST_DELIVERY_', 'HOSPITAL_WORKFLOW_OCR_DELIVERY_'):
        for role in ('WORKER_', 'DRAIN_'):
            monkeypatch.delenv(base + role + 'SUBJECT')
    attempts = []
    monkeypatch.setattr(auth.id_token, 'verify_oauth2_token', lambda *a: attempts.append(True))
    with http_client() as client:
        assert client.post(path, json=body, headers={'Authorization':'Bearer synthetic'}).status_code == 503
    assert attempts == []


@pytest.mark.parametrize('lane', ['ingest', 'workflow'])
def test_valid_principal_reaches_only_its_original_business_boundary(monkeypatch, identity_config, lane):
    monkeypatch.setattr(auth.id_token, 'verify_oauth2_token', lambda *a: identity_config)
    seen = []
    if lane == 'ingest':
        def process(identity, generation):
            seen.append((identity,generation))
            return {'acknowledged':True}
        monkeypatch.setattr(delivery, 'process', process)
        path, body = ENDPOINTS[0]
    else:
        def inspect(identity, generation):
            seen.append((identity,generation))
            return {'duplicate':True}
        monkeypatch.setattr(worker.delivery, 'inspect_generation', inspect)
        path, body = ENDPOINTS[3]
    with http_client() as client:
        response = client.post(path, json=body, headers={'Authorization':'Bearer synthetic'})
    assert response.status_code == 200 and response.json()['acknowledged'] is True
    assert seen == [('D',1)]


@pytest.fixture(scope='module')
def signing_material():
    """Ephemeral synthetic signing keys only; no token is cached or returned."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    elliptic = ec.generate_private_key(ec.SECP256R1())
    elliptic384 = ec.generate_private_key(ec.SECP384R1())
    def signer(private):
        return crypt.RSASigner.from_string(private.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()), key_id='synthetic-key')
    def certificate(private):
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'synthetic.invalid')])
        now = datetime.now(timezone.utc)
        return x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key()).serial_number(
            x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(
            now+timedelta(days=1)).sign(private, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()
    def public(private):
        return private.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return {'signer':signer(key), 'other':signer(other), 'public':public(key), 'certificate':certificate(key),
            'ec-public':public(elliptic), 'ec-certificate':certificate(elliptic),
            'ec-signer':crypt.EsSigner.from_string(elliptic.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()), key_id='synthetic-key'),
            'ec384-public':public(elliptic384), 'ec384-certificate':certificate(elliptic384),
            'ec384-signer':crypt.EsSigner.from_string(elliptic384.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()), key_id='synthetic-key'),
            'unsupported-key':public(ed25519.Ed25519PrivateKey.generate())}


REAL_CERT_CASES = [('valid-public', 200), ('valid-certificate', 200), ('valid-es256-public', 200),
    ('valid-es256-certificate', 200), ('bad-signature', 401),
    ('valid-es384-public', 200), ('valid-es384-certificate', 200), ('unsupported-keytype', 503),
    ('expired', 401), ('bad-audience', 401), ('bad-issuer', 401), ('foreign-subject', 403),
    ('unverified-email', 403), ('malformed-token', 401), ('missing-expiry', 401), ('missing-issued', 401),
    ('future-issued', 401), ('unknown-key-id', 401), ('unsupported-algorithm', 401),
    ('malformed-pem', 503), ('malformed-x509', 503), ('ec-public', 503), ('ec-certificate', 503),
    ('empty-mapping', 503), ('list-payload', 503), ('null-payload', 503), ('string-payload', 503),
    ('invalid-value', 503), ('invalid-json', 503), ('invalid-utf8', 503), ('http-unavailable', 503),
    ('transport', 503), ('unknown-transport', 503), ('transport-value-error', 503), ('unexpected-jwks', 503),
    ('reserved-jwks-key', 503)]


@pytest.mark.parametrize('path,body', ENDPOINTS)
@pytest.mark.parametrize('case,status', REAL_CERT_CASES)
def test_actual_google_verifier_with_isolated_certificate_http(monkeypatch, identity_config, signing_material,
                                                               path, body, case, status):
    from src.services import ingest_delivery_successor_service as successor
    # Retain the actual verifier/crypto; only the HTTP request to its fixed cert endpoint is replaced.
    assert auth.id_token.verify_oauth2_token.__module__ == 'google.oauth2.id_token'
    claims = dict(identity_config, iat=int(time.time())-10, exp=int(time.time())+300)
    if case == 'expired':
        claims.update(iat=int(time.time())-600, exp=int(time.time())-300)
    elif case == 'bad-audience':
        claims['aud'] = 'foreign-audience'
    elif case == 'bad-issuer':
        claims['iss'] = 'https://foreign.invalid'
    elif case == 'foreign-subject':
        claims['sub'] = 'foreign-subject'
    elif case == 'unverified-email':
        claims['email_verified'] = False
    elif case == 'missing-expiry':
        del claims['exp']
    elif case == 'missing-issued':
        del claims['iat']
    elif case == 'future-issued':
        claims.update(iat=int(time.time())+600, exp=int(time.time())+1200)
    header = {'kid':'unknown'} if case == 'unknown-key-id' else (
        {'alg':'unsupported'} if case == 'unsupported-algorithm' else None)
    signer = signing_material['other'] if case == 'bad-signature' else signing_material['signer']
    if case.startswith('valid-es256-'):
        signer = signing_material['ec-signer']
    elif case.startswith('valid-es384-'):
        signer = signing_material['ec384-signer']
    token = jwt.encode(signer, claims, header=header, key_id='unknown' if case == 'unknown-key-id' else None).decode()
    if case == 'malformed-token':
        token = 'not-a-jwt'
    certs = {'synthetic-key':signing_material['certificate' if case == 'valid-certificate' else 'public']}
    if case in {'ec-public', 'ec-certificate'}:
        certs = {'synthetic-key':signing_material[case]}
    elif case.startswith('valid-es256-'):
        certs = {'synthetic-key':signing_material['ec-' + case.removeprefix('valid-es256-')]}
    elif case.startswith('valid-es384-'):
        certs = {'synthetic-key':signing_material['ec384-' + case.removeprefix('valid-es384-')]}
    elif case == 'unsupported-keytype':
        certs = {'synthetic-key':signing_material['unsupported-key']}
    elif case == 'malformed-pem':
        certs = {'synthetic-key':'synthetic malformed PEM'}
    elif case == 'malformed-x509':
        certs = {'synthetic-key':'-----BEGIN CERTIFICATE-----\ninvalid\n-----END CERTIFICATE-----'}
    elif case == 'empty-mapping':
        certs = {}
    elif case == 'list-payload':
        certs = []
    elif case == 'null-payload':
        certs = None
    elif case == 'string-payload':
        certs = 'not a certificate map'
    elif case == 'invalid-value':
        certs = {'synthetic-key':{'not':'PEM'}}
    elif case == 'unexpected-jwks':
        certs = {'keys':[]}
    elif case == 'reserved-jwks-key':
        certs = {'keys':signing_material['public']}
    original_data = b'not JSON' if case == 'invalid-json' else (
        b'\xff' if case == 'invalid-utf8' else json.dumps(certs).encode())
    response = SimpleNamespace(status=503 if case == 'http-unavailable' else 200, data=original_data)
    calls, business = [], []
    def certificate_http(request, url, method='GET', **kwargs):
        calls.append((url, method))
        if case == 'transport':
            raise exceptions.TransportError('synthetic certificate transport failed')
        if case == 'unknown-transport':
            raise RuntimeError('synthetic certificate transport failed')
        if case == 'transport-value-error':
            raise ValueError('synthetic certificate transport failed')
        return response
    monkeypatch.setattr(auth.requests.Request, '__call__', certificate_http)
    def process(identity, generation):
        business.append((identity,generation))
        return {'acknowledged':True}
    def inspect(identity, generation):
        business.append((identity,generation))
        return {'duplicate':True}
    monkeypatch.setattr(delivery, 'process', process)
    monkeypatch.setattr(successor, 'process', process)
    monkeypatch.setattr(worker.delivery, 'inspect_generation', inspect)
    def no_database(*args, **kwargs):
        pytest.fail('certificate/authentication tests must not connect to a database')
    monkeypatch.setattr(db, 'SessionLocal', no_database)
    is_drain = '/drain?' in path
    with http_client() as client:
        result = client.post(path.replace('limit=1', 'limit=0'), json=body,
            headers={'Authorization':'Bearer '+token})
    assert calls == [('https://www.googleapis.com/oauth2/v1/certs','GET')]
    assert response.data is original_data
    assert result.status_code == (422 if status == 200 and is_drain else status)
    assert business == ([('D',1)] if status == 200 and not is_drain else [])
    if status == 503:
        assert result.json() == {'detail':'delivery_auth_verification_unavailable'}
    elif status == 401:
        assert result.json() == {'detail':'Unauthorized'}


@pytest.mark.parametrize('form', ['public', 'certificate'])
def test_certificate_boundary_returns_exact_original_response_once(monkeypatch, signing_material, form):
    response = SimpleNamespace(status=200, data=json.dumps({'synthetic-key':signing_material[form]}).encode())
    calls = []
    def certificate_http(request, url, **kwargs):
        calls.append(url)
        return response
    monkeypatch.setattr(auth.requests.Request, '__call__', certificate_http)
    assert auth._CertificateRequest()('https://www.googleapis.com/oauth2/v1/certs') is response
    assert calls == ['https://www.googleapis.com/oauth2/v1/certs']


@pytest.fixture(scope='module')
def header_signing_material():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signer = crypt.RSASigner.from_string(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return signer, public


def header_token(material, identity, *, kind='valid', malformed=None):
    signer, _ = material
    claims = dict(identity, iat=int(time.time())-10, exp=int(time.time())+300)
    header = {'kid':None} if kind == 'null' else None
    key_id = 'synthetic-key' if kind == 'valid' else None
    if malformed is not None:
        field, value = malformed
        if field == 'alg':
            header = {'alg':value}
        else:
            key_id = value
    return jwt.encode(signer, claims, header=header, key_id=key_id).decode()


def header_certificate_http(monkeypatch, material, *, fault=None):
    calls = []
    def certificate_http(request, url, method='GET', **kwargs):
        calls.append((url,method))
        if fault == 'transport':
            raise TypeError('synthetic unknown transport type failure')
        return SimpleNamespace(status=200, data=json.dumps({'synthetic-key':material}).encode())
    monkeypatch.setattr(auth.requests.Request, '__call__', certificate_http)
    return calls


@pytest.mark.parametrize('path,body', ENDPOINTS)
@pytest.mark.parametrize('field,value', [('alg', []), ('alg', {}), ('alg', None), ('alg', True),
    ('kid', ['x']), ('kid', {'x':'y'}), ('kid', 1), ('kid', True)])
def test_real_malformed_header_types_are_401(monkeypatch, identity_config, header_signing_material,
                                           path, body, field, value):
    assert auth.id_token.verify_oauth2_token.__module__ == 'google.oauth2.id_token'
    token = header_token(header_signing_material, identity_config, malformed=(field,value))
    calls = header_certificate_http(monkeypatch, header_signing_material[1])
    with http_client() as client:
        response = client.post(path, json=body, headers={'Authorization':'Bearer '+token})
    assert calls == [('https://www.googleapis.com/oauth2/v1/certs','GET')]
    assert response.status_code == 401
    assert response.json() == {'detail':'Unauthorized'}


@pytest.mark.parametrize('path,body', ENDPOINTS)
@pytest.mark.parametrize('kind', ['missing', 'null', 'valid'])
@pytest.mark.parametrize('fault', ['transport', 'verifier', 'wrong-key'])
def test_unknown_type_error_is_503_with_well_typed_header(monkeypatch, identity_config, header_signing_material,
                                                        signing_material, path, body, kind, fault):
    token = header_token(header_signing_material, identity_config, kind=kind)
    key = signing_material['ec-public'] if fault == 'wrong-key' else header_signing_material[1]
    calls = header_certificate_http(monkeypatch, key, fault=fault)
    if fault == 'verifier':
        # Injection checks unknown verifier errors separately, not real Google proof.
        def unknown(*args, **kwargs):
            raise TypeError('synthetic unknown verifier failure')
        monkeypatch.setattr(auth.id_token, 'verify_oauth2_token', unknown)
    else:
        assert auth.id_token.verify_oauth2_token.__module__ == 'google.oauth2.id_token'
    with http_client() as client:
        response = client.post(path, json=body, headers={'Authorization':'Bearer '+token})
    assert calls == ([] if fault == 'verifier' else [('https://www.googleapis.com/oauth2/v1/certs','GET')])
    assert response.status_code == 503
    assert response.json() == {'detail':'delivery_auth_verification_unavailable'}


@pytest.mark.parametrize('path,body', ENDPOINTS)
@pytest.mark.parametrize('kind', ['missing', 'null', 'valid'])
def test_actual_valid_token_preserves_missing_and_valid_key_id(monkeypatch, identity_config,
                                                              header_signing_material, path, body, kind):
    from src.services import ingest_delivery_successor_service as successor
    token = header_token(header_signing_material, identity_config, kind=kind)
    calls = header_certificate_http(monkeypatch, header_signing_material[1])
    business = []
    def process(identity, generation):
        business.append((identity,generation))
        return {'acknowledged':True}
    def inspect(identity, generation):
        business.append((identity,generation))
        return {'duplicate':True}
    monkeypatch.setattr(delivery, 'process', process)
    monkeypatch.setattr(successor, 'process', process)
    monkeypatch.setattr(worker.delivery, 'inspect_generation', inspect)
    with http_client() as client:
        response = client.post(path.replace('limit=1','limit=0'), json=body,
            headers={'Authorization':'Bearer '+token})
    assert calls == [('https://www.googleapis.com/oauth2/v1/certs','GET')]
    is_drain = '/drain?' in path
    assert response.status_code == (422 if is_drain else 200)
    assert business == ([] if is_drain else [('D',1)])


@pytest.mark.parametrize('path,body', ENDPOINTS)
@pytest.mark.parametrize('malformed', [('alg', []), ('kid', ['x'])])
@pytest.mark.parametrize('fault', ['transport', 'pem'])
def test_certificate_fault_remains_503_with_malformed_header(monkeypatch, identity_config,
                                                          header_signing_material, path, body, malformed, fault):
    token = header_token(header_signing_material, identity_config, malformed=malformed)
    calls = header_certificate_http(monkeypatch, 'synthetic invalid PEM', fault=fault)
    assert auth.id_token.verify_oauth2_token.__module__ == 'google.oauth2.id_token'
    with http_client() as client:
        response = client.post(path, json=body, headers={'Authorization':'Bearer '+token})
    assert calls == [('https://www.googleapis.com/oauth2/v1/certs','GET')]
    assert response.status_code == 503
    assert response.json() == {'detail':'delivery_auth_verification_unavailable'}


@pytest.mark.parametrize('path,body', ENDPOINTS)
def test_unknown_verifier_type_error_without_decodable_header_remains_503(monkeypatch, identity_config, path, body):
    def unknown(*args, **kwargs):
        raise TypeError('synthetic unknown verifier failure')
    monkeypatch.setattr(auth.id_token, 'verify_oauth2_token', unknown)
    with http_client() as client:
        response = client.post(path, json=body, headers={'Authorization':'Bearer synthetic-undecodable'})
    assert response.status_code == 503
    assert response.json() == {'detail':'delivery_auth_verification_unavailable'}


@pytest.mark.parametrize('damage', ['accepted', 'missing-start', 'null-start', 'missing-processing-start',
    'changed-start-hash', 'null-orders', 'extra-order', 'missing-order', 'missing-document',
    'missing-document-id', 'changed-document-source', 'missing-lease', 'malformed-lease',
    'missing-payload', 'malformed-payload', 'missing-received-at', 'invalid-received-at',
    'missing-mode', 'missing-protocol', 'missing-selected-proof', 'changed-selected-proof'])
@pytest.mark.parametrize('tie', [False, True])
def test_actual_native_capture_input_blocker_and_admission_agree(database, payload, monkeypatch, damage, tie):
    _, a = service_handoff(database, payload, monkeypatch, 'A', native=True)
    _, b = service_handoff(database, payload, monkeypatch, 'B', native=True)
    # Both valid boundaries are authored by capture_nonworkflow_execution, not a
    # hand-filled capsule or a test-computed replacement hash.
    assert ocr_job_service.describe_job_state(ocr_job_service.get_job('A-ocr'))['blockers'] == []
    assert ocr_job_service.describe_job_state(ocr_job_service.get_job('B-ocr'))['blockers'] == []
    changed = deepcopy(a)
    order_id = a['order_id']
    if damage == 'missing-start':
        changed.pop('start_orders')
    elif damage == 'null-start':
        changed['start_orders'] = None
    elif damage == 'missing-processing-start':
        changed['start_orders'][order_id].pop('processing_start_input')
    elif damage == 'changed-start-hash':
        changed['start_orders'][order_id]['input_sha256'] = '0' * 64
    elif damage == 'null-orders':
        changed['orders'] = None
    elif damage == 'extra-order':
        changed['orders']['foreign'] = {}
    elif damage == 'missing-order':
        changed['orders'][order_id].pop('order')
    elif damage == 'missing-document':
        changed['orders'][order_id].pop('document')
    elif damage == 'missing-document-id':
        changed['orders'][order_id]['document'].pop('id')
    elif damage == 'changed-document-source':
        changed['orders'][order_id]['document']['storage_uri'] = 'fixture://foreign'
    elif damage == 'missing-lease':
        changed['ingest_lease'] = None
    elif damage == 'malformed-lease':
        changed['ingest_lease'] = True
    elif damage == 'missing-payload':
        changed['ingest_lease'].pop('payload')
    elif damage == 'malformed-payload':
        changed['ingest_lease']['payload'] = []
    elif damage == 'missing-received-at':
        changed['ingest_lease']['payload'].pop('received_at')
    elif damage == 'invalid-received-at':
        changed['ingest_lease']['payload']['received_at'] = 'not-a-date'
    elif damage == 'missing-mode':
        changed.pop('ingest_ocr_mode')
    elif damage == 'missing-protocol':
        changed.pop('intake_protocol')
    elif damage == 'missing-selected-proof':
        changed.pop('selected_pdf_receipt')
    elif damage == 'changed-selected-proof':
        changed['selected_pdf_receipt']['sha256'] = '0' * 64
    with database.begin() as connection:
        metrics = deepcopy(ocr_job_service.get_job('A-ocr')['metrics'])
        metrics['nonworkflow_execution'] = changed
        connection.execute(sa.update(OcrJob).where(OcrJob.id == 'A-ocr').values(metrics=metrics))
        if tie:
            connection.execute(sa.update(OcrJob).where(OcrJob.id.in_(['A-ocr', 'B-ocr'])).values(
                updated_at=datetime.utcnow() - timedelta(days=2)))
    before = snapshot(database)
    state = ocr_job_service.describe_job_state(ocr_job_service.get_job('A-ocr'))
    assert state['blockers'] == ([] if damage == 'accepted' else ['ocr_execution_input_required'])
    assert ingest._recorded_recovery_owner(ocr_job_service.get_job('A-ocr')) is (damage == 'accepted')
    assert [job['id'] for job in ingest.list_ocr_recovery_candidates(1)] == (
        ['A-ocr'] if damage == 'accepted' else ['B-ocr'])
    assert ocr_job_service.get_job('B-ocr')['metrics']['nonworkflow_execution'] == b
    assert snapshot(database) == before


@pytest.mark.parametrize('all_invalid', [False, True])
def test_actual_native_invalid_capture_pages_finish_without_mutating_original(database, payload, monkeypatch, all_invalid):
    for index in range(4):
        _, captured = service_handoff(database, payload, monkeypatch, 'invalid-' + str(index), native=True)
        job = ocr_job_service.get_job(captured['job_id'])
        job['metrics']['nonworkflow_execution'].pop('start_orders')
        with database.begin() as connection:
            connection.execute(sa.update(OcrJob).where(OcrJob.id == job['id']).values(metrics=job['metrics']))
    if not all_invalid:
        service_handoff(database, payload, monkeypatch, 'Z-valid', native=True)
    with database.begin() as connection:
        connection.execute(sa.update(OcrJob).values(updated_at=datetime.utcnow() - timedelta(days=2)))
    before = snapshot(database)
    statements = []
    def observe(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
        assert len(statements) < 30
    sa.event.listen(database, 'before_cursor_execute', observe)
    try:
        assert [job['id'] for job in ingest.list_ocr_recovery_candidates(1)] == ([] if all_invalid else ['Z-valid-ocr'])
    finally:
        sa.event.remove(database, 'before_cursor_execute', observe)
    assert all(not sql.lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for sql in statements)
    assert snapshot(database) == before


@pytest.mark.parametrize('output', [False, True])
def test_actual_backoff_without_pollable_output_does_not_take_admission(database, payload, monkeypatch, output):
    service_handoff(database, payload, monkeypatch, 'A')
    service_handoff(database, payload, monkeypatch, 'B')
    a = ocr_job_service.get_job('A-ocr')
    a['metrics']['next_recovery_at'] = (datetime.utcnow() + timedelta(days=1)).isoformat()
    with database.begin() as connection:
        connection.execute(sa.update(OcrJob).where(OcrJob.id == 'A-ocr').values(
            metrics=a['metrics'], output_reference=a['output_reference'] if output else None))
    before = snapshot(database)
    assert not ocr_job_service.is_job_recovery_due(ocr_job_service.get_job('A-ocr'))
    assert [job['id'] for job in ingest.list_ocr_recovery_candidates(1)] == (['A-ocr'] if output else ['B-ocr'])
    assert snapshot(database) == before
