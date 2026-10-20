"""Registered-FAX dispatch contracts; no upload, facility binding or OCR calls."""
from unittest.mock import Mock

from fastapi import HTTPException
import pytest

from src.api import ingest


@pytest.mark.parametrize('state,expected', [('pending', True), ('processing', False)])
def test_start_preserves_current_dispatch_and_never_requeues(monkeypatch, state, expected):
    row = {'id': 'component-fax', 'status': state, 'content_sha256': 'captured-hash', 'skip_ocr': False}
    monkeypatch.setattr(ingest, 'get_uploaded_pdf', lambda ident: row)
    captured = Mock(); dispatch = Mock(); restart = Mock(side_effect=AssertionError('must not reset a lease'))
    monkeypatch.setattr(ingest, 'build_ingest_payload', captured)
    monkeypatch.setattr(ingest, 'enqueue_uploaded_pdf_async', dispatch)
    monkeypatch.setattr(ingest, 'requeue_uploaded_pdf', restart)
    result = ingest.start_pending_fax('component-fax', {'sha256': 'captured-hash'})
    assert result['accepted'] == expected
    assert dispatch.call_count == int(expected)
    assert captured.call_count == int(expected)
    restart.assert_not_called()


@pytest.mark.parametrize('body,skip,state', [({'sha256': 'wrong'}, False, 'pending'), ({'sha256': 'captured-hash'}, True, 'pending'), ({'sha256': 'captured-hash'}, False, 'failed'), ({'sha256': 'captured-hash', 'force': True}, False, 'pending')])
def test_changed_source_skipped_ocr_or_nonpending_fax_never_dispatches(monkeypatch, body, skip, state):
    monkeypatch.setattr(ingest, 'get_uploaded_pdf', lambda ident: {'status': state, 'content_sha256': 'captured-hash', 'skip_ocr': skip})
    dispatch = Mock(); monkeypatch.setattr(ingest, 'enqueue_uploaded_pdf_async', dispatch)
    with pytest.raises(HTTPException) as exc:
        ingest.start_pending_fax('component-fax', body)
    assert exc.value.status_code == 409
    dispatch.assert_not_called()


def test_missing_original_selection_is_explicit_conflict(monkeypatch):
    monkeypatch.setattr(ingest, 'get_uploaded_pdf', lambda ident: {'status': 'pending', 'content_sha256': 'captured-hash', 'skip_ocr': False})
    monkeypatch.setattr(ingest, 'build_ingest_payload', Mock(side_effect=ValueError('uploaded_pdf_original_selection_missing')))
    dispatch = Mock(); monkeypatch.setattr(ingest, 'enqueue_uploaded_pdf_async', dispatch)
    with pytest.raises(HTTPException) as exc:
        ingest.start_pending_fax('component-fax', {'sha256': 'captured-hash'})
    assert exc.value.status_code == 409
    dispatch.assert_not_called()


def test_pending_queue_has_no_first_hundred_cutoff_and_hides_secrets(isolated_formal_schema):
    from datetime import datetime
    from src.db import session_scope
    from src.models.uploaded_pdf import UploadedPdf
    # Synthetic intake metadata only: no upload or OCR is performed.
    with session_scope() as session:
        for index in range(105):
            session.add(UploadedPdf(id=f'component-{index:03}', message_id=f'component-message-{index}',
                content_sha256='component-hash', original_filename='synthetic.pdf', storage_uri='not-a-real-source',
                received_at=datetime(2026, 10, 19), status='pending', lease_token='private-dispatch-token'))
        session.add(UploadedPdf(id='failed-component', message_id='failed-message', content_sha256='component-hash',
            original_filename='synthetic.pdf', storage_uri='not-a-real-source', received_at=datetime(2026, 10, 19), status='failed'))
    queue = ingest.pending_faxes()
    assert queue['total'] == 105
    assert queue['items'][-1]['id'] == 'component-104'
    assert all('lease_token' not in item and 'storage_uri' not in item for item in queue['items'])


def test_review_queue_preserves_failed_manual_and_retry_intents(isolated_formal_schema):
    from datetime import datetime
    from src.db import session_scope
    from src.models.uploaded_pdf import UploadedPdf
    with session_scope() as session:
        for state in ('pending','processing','completed','failed','manual_review','retry_wait'):
            session.add(UploadedPdf(id='review-component-'+state,message_id='review-message-'+state,content_sha256='synthetic-hash',
                original_filename='synthetic.pdf',storage_uri='synthetic-non-ocr-source',received_at=datetime(2026,10,19),status=state))
    response=ingest.review_faxes()
    assert response['total']==3
    assert {x['status'] for x in response['items']}=={'failed','manual_review','retry_wait'}
    assert all('lease_token' not in x and 'storage_uri' not in x for x in response['items'])
