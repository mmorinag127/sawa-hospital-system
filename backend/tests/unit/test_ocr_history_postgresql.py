"""Run against an explicitly supplied disposable PostgreSQL instance."""
from copy import deepcopy
from datetime import datetime
import json
import os
import gc
import tracemalloc
from statistics import median
from time import perf_counter

import pytest
from sqlalchemy import Column, Computed, MetaData, Table, create_engine, event, select
from sqlalchemy.orm import Session

from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.services.ocr_history_read import read_history


def test_postgresql_job_display_extracts_small_native_and_workflow_references():
    from types import SimpleNamespace
    from src.models.ocr_job import OcrJob
    from src.models.document import OrderDocument
    from src.models.uploaded_pdf import UploadedPdf
    from src.models.order_version import OrderVersion
    from src.services.workflow_display_read_service import read_recorded_job_values, _job_summary
    url = os.environ.get('OCR_HISTORY_PROJECTION_POSTGRES_URL')
    if not url:
        pytest.skip('Requires explicit disposable PostgreSQL URL')
    engine = create_engine(url)
    metadata = MetaData()
    tables = {model: Table(model.__table__.name, metadata,
        *[Column(column.name, column.type, *([Computed(column.computed.sqltext, persisted=column.computed.persisted)]
            if column.computed is not None else [])) for column in model.__table__.columns], prefixes=['TEMPORARY'])
        for model in (OcrJob, OrderDocument, UploadedPdf, OrderVersion)}
    pdf = {'order_id': 'display-order', 'document_id': 'display-doc', 'uploaded_pdf_id': 'display-upload',
        'message_id': 'display-message', 'storage_uri': 'owned-component.pdf', 'sha256': 'a' * 64}
    captured = {'order': {'current_document_id': 'display-doc', 'template_version_id': 'display-template'},
        'line_snapshot': ['unreturned-large-input'] * 10000}
    native = {'ingest_ocr_mode': 'hakodate', 'intake_protocol': 'native_ingest_v2', 'job_id': 'native-job',
        'order_id': 'display-order', 'execution_id': 'display-execution', 'ingest_lease': {'generation': 3},
        'selected_pdf_receipt': pdf, 'orders': {'display-order': captured}}
    try:
        with engine.connect() as connection:
            for table in tables.values():
                table.create(connection)
            connection.execute(tables[OrderDocument].insert().values(id='display-doc', storage_uri=pdf['storage_uri'],
                source_email_id=pdf['message_id']))
            connection.execute(tables[UploadedPdf].insert().values(id='display-upload', storage_uri=pdf['storage_uri'],
                message_id=pdf['message_id'], content_sha256=pdf['sha256'], current_order_id='display-order',
                current_document_id='display-doc'))
            for identity, metrics, template in [
                ('native-job', {'nonworkflow_execution': native}, None),
                ('workflow-job', {'workflow_attempt_id': 'display-attempt', 'reparse_read_input': captured,
                    'selected_pdf_receipt': pdf}, 'display-template')]:
                connection.execute(tables[OcrJob].insert().values(id=identity, order_id='display-order',
                    status='done', input_reference=pdf['storage_uri'], order_document_id='display-doc',
                    uploaded_pdf_id='display-upload', input_artifact_digest=pdf['sha256'],
                    template_version_id=template, metrics=metrics))
            with Session(bind=connection) as session:
                reads = []
                def observe(_connection, _cursor, sql, *_args):
                    reads.append(sql)
                event.listen(connection, 'before_cursor_execute', observe)
                try:
                    rows = read_recorded_job_values(session, ['native-job', 'workflow-job'])
                finally:
                    event.remove(connection, 'before_cursor_execute', observe)
                assert len(reads) == 1
                assert all(name in reads[0] for name in ('order_documents', 'uploaded_pdfs', 'order_versions'))
                assert rows['native-job']['native_template_version'] == 'display-template'
                assert rows['native-job']['_current_document'] == 'display-doc'
                assert rows['native-job']['native_generation'] == 3
                assert 'line_snapshot' not in str(rows)
                state = SimpleNamespace(state='ocr_completed', template_version_id='display-template')
                order = SimpleNamespace(id='display-order', current_document_id='display-doc')
                for identity, attempt in [('native-job', None), ('workflow-job', 'display-attempt')]:
                    job, error, common = _job_summary(session, order, state,
                        {'ocr_job_id': identity, 'ocr_job_attempt_id': attempt}, _rows=rows)
                    assert error is None
                    assert job['recorded_state'] == 'done'
                    assert common['jobId'] == identity
                assert not session.new and not session.dirty
            for index, malformed in enumerate((None, [], 'text', False, 9,
                    {'selected_pdf_receipt': {**pdf, 'document_id': 9}},
                    {'selected_version_input': []})):
                identity = f'malformed-job-{index}'
                connection.execute(tables[OcrJob].insert().values(id=identity, order_id='display-order',
                    status='done', input_reference=pdf['storage_uri'], template_version_id='display-template',
                    metrics=malformed))
                with Session(bind=connection) as session:
                    job, error, common = _job_summary(session, order, state, {'ocr_job_id': identity})
                    assert job is None and error and common is None
            connection.rollback()
    finally:
        engine.dispose()


def test_postgresql_projection_values_storage_and_memory(record_property):
    url = os.environ.get('OCR_HISTORY_PROJECTION_POSTGRES_URL')
    if not url:
        pytest.skip('Requires explicit disposable PostgreSQL URL')
    engine = create_engine(url)
    source = OrderOcrEvidenceRun.__table__
    table = Table(source.name, MetaData(), *[
        Column(column.name, column.type) for column in source.columns if column.computed is None
    ], prefixes=['TEMPORARY'])
    payloads = [
        {'diagnostic_pipeline_output': {'blob': 'x' * 1000000},
         'raw': ['1', '26', '0'], 'nested': {'diagnostic_pipeline_output': 'keep'},
         'unknown': None, 'enabled': False, 'text': '日本語'},
        {}, None, [], [{'diagnostic_pipeline_output': 'keep'}],
        'text', 123, 0, True, False,
        {'diagnostic_pipeline_output': {}, 'unknown': '\u0000'},
    ]
    payloads.extend({'diagnostic_pipeline_output': {'blob': 'x' * 250000}, 'raw': ['1', '26', '0']}
                    for _ in range(20))
    try:
        with engine.connect() as connection:
            table.create(connection)
            connection.execute(table.insert(), [
                {'id': f'E{i:02}', 'order_id': 'O', 'payload_json': deepcopy(payload),
                 'created_at': datetime(2026, 10, 20)}
                for i, payload in enumerate(payloads)
            ])
            with Session(bind=connection) as session:
                full_statement = select(table).where(table.c.order_id == 'O').order_by(
                    table.c.created_at.desc(), table.c.id.desc())
                before = session.execute(full_statement).mappings().all()
                projected = read_history(session, 'O')
                assert [row.id for row in projected] == [row['id'] for row in before]
                for old, new in zip(before, projected):
                    expected = dict(old)
                    payload = expected['payload_json']
                    if isinstance(payload, dict):
                        expected['payload_json'] = {k: v for k, v in payload.items()
                                                    if k != 'diagnostic_pipeline_output'}
                    assert vars(new) == expected
                assert read_history(session, 'missing') == []
                assert session.execute(full_statement).mappings().all() == before
                assert not session.dirty and not session.identity_map
                full_bytes = len(json.dumps([r['payload_json'] for r in before], ensure_ascii=False).encode())
                projected_bytes = len(json.dumps([r.payload_json for r in projected], ensure_ascii=False).encode())
                assert projected_bytes < full_bytes / 100
                durations = {'full': [], 'projected': []}
                for _ in range(3):
                    start = perf_counter()
                    session.execute(full_statement).mappings().all()
                    durations['full'].append(perf_counter() - start)
                    start = perf_counter()
                    read_history(session, 'O')
                    durations['projected'].append(perf_counter() - start)
                peaks = {}
                for name, read in [('full', lambda: session.execute(full_statement).mappings().all()),
                                   ('projected', lambda: read_history(session, 'O'))]:
                    gc.collect()
                    tracemalloc.start()
                    value = read()
                    peaks[name] = tracemalloc.get_traced_memory()[1]
                    tracemalloc.stop()
                    del value
                assert peaks['projected'] < peaks['full'] / 2
                result = {'full_retained_json_bytes': full_bytes, 'projected_retained_json_bytes': projected_bytes,
                          'full_peak_python_bytes': peaks['full'], 'projected_peak_python_bytes': peaks['projected'],
                          'full_median_ms': median(durations['full']) * 1000,
                          'projected_median_ms': median(durations['projected']) * 1000}
                for key, value in result.items():
                    record_property(key, value)
                print(json.dumps(result))
            connection.rollback()
    finally:
        engine.dispose()
