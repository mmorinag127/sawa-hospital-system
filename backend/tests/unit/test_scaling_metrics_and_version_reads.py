"""Owned SQLite component checks; not PostgreSQL or live-flow evidence."""
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from src.models.ocr_job import OcrJob
from src.models.order_version import OrderVersion
from src.services import order_serialization_service as serialization


@pytest.fixture
def database():
    engine = create_engine('sqlite:///:memory:')
    OcrJob.__table__.create(engine)
    OrderVersion.__table__.create(engine)
    yield engine
    engine.dispose()


def test_progress_updates_preserve_evidence_and_explicit_null_without_rewriting_large_json(database):
    value = {'workflow_attempt_id': 'attempt-1', 'proof': {'cells': ['original'] * 100},
             'processing_stage': 'queued', 'progress_label': None}
    with Session(database) as session:
        session.add(OcrJob(id='job', status='running', input_reference='owned.pdf', metrics=value))
        session.commit()
    statements = []

    def observe(_connection, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith('UPDATE '):
            statements.append(statement)

    event.listen(database, 'before_cursor_execute', observe)
    try:
        with Session(database) as session:
            job = session.get(OcrJob, 'job')
            assert job.metrics == value
            job.metrics = {**job.metrics, 'processing_stage': 'recognizing'}
            session.commit()
        assert statements and all('SET metrics=' not in statement and ', metrics=' not in statement
                                  for statement in statements)
        with Session(database) as session:
            expected = {**value, 'processing_stage': 'recognizing'}
            assert session.get(OcrJob, 'job').metrics == expected
            assert session.scalar(select(OcrJob.metrics).where(OcrJob.id == 'job')) == expected
            assert session.scalar(select(OcrJob.metrics['workflow_attempt_id'].as_string())) == 'attempt-1'
            assert session.get(OcrJob, 'job').evidence_metrics['proof'] == value['proof']
    finally:
        event.remove(database, 'before_cursor_execute', observe)


def test_legacy_core_metrics_remain_readable_and_orm_bulk_updates_split_fields(database):
    from sqlalchemy import update

    with database.begin() as connection:
        connection.execute(OcrJob.__table__.insert().values(id='legacy', status='running',
            input_reference='owned.pdf', metrics={'proof': 'original', 'processing_stage': 'queued'}))
    with Session(database) as session:
        assert session.get(OcrJob, 'legacy').metrics['processing_stage'] == 'queued'
        session.execute(update(OcrJob).where(OcrJob.id == 'legacy').values(
            metrics={'proof': 'original', 'processing_stage': None}))
        session.commit()
    with Session(database) as session:
        row = session.get(OcrJob, 'legacy')
        assert row.metrics == {'proof': 'original', 'processing_stage': None}
        assert row.evidence_metrics == {'proof': 'original'}
        assert row.progress_metrics == {'processing_stage': None}


def test_version_metadata_pages_preserve_old_selected_fax_without_loading_snapshot(database, monkeypatch):
    @contextmanager
    def scope():
        with Session(database) as session:
            yield session

    monkeypatch.setattr(serialization, 'session_scope', scope)
    with Session(database) as session:
        for number in range(1, 33):
            session.add(OrderVersion(id=f'version-{number:02}', order_id='owned', version_no=number,
                document_id=f'document-{number:02}', message_id=f'message-{number:02}', storage_uri=f'{number}.pdf',
                received_at=datetime(2026, 1, 1) + timedelta(days=number), is_current=number == 32,
                line_snapshot=[{'quantity': number}]))
        session.commit()
    statements = []

    def observe(_connection, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(database, 'before_cursor_execute', observe)
    try:
        with Session(database) as session:
            first = serialization.order_version_metadata_page(session, 'owned', selected_document_id='document-01')
            assert first['total'] == 32 and len(first['items']) == 26
            assert first['current_version']['document_id'] == 'document-32'
            assert any(row['document_id'] == 'document-01' for row in first['items'])
            assert all(row['detail_state'] == 'not_loaded' and row['line_snapshot'] is None for row in first['items'])
            second = serialization.order_version_metadata_page(session, 'owned', before_id=first['next_before_id'])
            assert [row['version_no'] for row in second['items']] == list(range(7, 0, -1))
            assert second['next_before_id'] is None
            with pytest.raises(ValueError, match='order_version_cursor_invalid'):
                serialization.order_version_metadata_page(session, 'foreign', before_id=first['next_before_id'])
        assert not any('line_snapshot' in statement or 'selected_version_input' in statement for statement in statements)
    finally:
        event.remove(database, 'before_cursor_execute', observe)
    selected = serialization.read_order_document_version('owned', 'document-01')
    assert selected['line_snapshot'] == [{'quantity': 1}]
    assert selected['selected_version_input']['id'] == 'version-01'
    assert selected['selected_version_input']['line_snapshot'] == [{'quantity': 1}]
    assert serialization.read_order_document_version('foreign', 'document-01') is None


@pytest.mark.parametrize('failure', ['begin', 'body', 'transaction-change'])
def test_read_snapshot_restores_sqlite_connection_after_failures(database, failure):
    from src.services.order_output_artifact_service import consistent_artifact_reads

    def reject_begin(_connection, _cursor, statement, *_args):
        if failure == 'begin' and statement == 'BEGIN':
            raise RuntimeError('owned begin failure')

    event.listen(database, 'before_cursor_execute', reject_begin)
    try:
        with Session(database) as session:
            with pytest.raises((RuntimeError, ValueError)):
                with consistent_artifact_reads(session):
                    if failure == 'transaction-change':
                        session.commit()
                    else:
                        raise RuntimeError('owned body failure')
            session.rollback()
    finally:
        event.remove(database, 'before_cursor_execute', reject_begin)
    with Session(database) as session:
        assert session.connection().exec_driver_sql('PRAGMA query_only').scalar_one() == 0
        session.add(OcrJob(id='after-failure', status='running', input_reference='owned.pdf'))
        session.commit()
