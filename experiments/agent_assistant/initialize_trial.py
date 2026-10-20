"""Prepare explicitly declared synthetic intake through the normal receipt service.

This runs only while creating a new isolated trial copy, before its before-image
is sealed. Saved delivery contracts are never rewritten or moved between runs.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3


def initialize(data):
    data=Path(data).resolve()
    manifest=json.loads((data/'trial-manifest.json').read_text())
    if manifest.get('purpose')!='sawa-agent-isolated-trial' or manifest.get('initialization')!='register-declared-pending-faxes-v1':
        raise ValueError('explicit_trial_initialization_required')
    sources=manifest.get('pending_sources')
    if not isinstance(sources,list) or not sources:
        raise ValueError('declared_trial_sources_required')
    with sqlite3.connect(data/'database.sqlite3') as db:
        for table in ('uploaded_pdfs','ingest_jobs','ingest_deliveries','orders'):
            if db.execute('SELECT count(*) FROM '+table).fetchone()[0]:
                raise ValueError('trial_initialization_requires_clean_fixture')
    import fitz
    from src.services.canonical_facility_store import read_snapshot
    from src.services.canonical_facility_lineage import resolve
    from src.services.ingest_facility_selection_service import capture_selection
    from src.services.manual_upload_service import ManualUploadSavedFile
    from src.services.uploaded_pdf_service import create_uploaded_pdf_from_upload
    receipts=[]
    for source in sources:
        name=source['path'];pdf=(data/name).resolve()
        if not pdf.is_relative_to(data) or pdf.is_symlink() or not pdf.is_file():
            raise ValueError('trial_original_path_invalid')
        content=pdf.read_bytes()
        if hashlib.sha256(content).hexdigest()!=source['sha256']:
            raise ValueError('trial_original_digest_changed')
        with fitz.open(pdf) as document:
            text='\n'.join(page.get_text() for page in document)
            pages=len(document)
        if source['facility_id'] not in text or source['week'] not in text:
            raise ValueError('trial_original_facility_week_mismatch')
        snapshot=read_snapshot()
        facility,_=resolve(None,source['facility_id'],source['service_date'])
        if not facility:
            raise ValueError('trial_declared_facility_unresolved')
        selection=capture_selection(source['facility_id'],source['service_date'],snapshot.revision,
            facility['canonical_lineage']['authored_version_id'],source['week'])
        saved=ManualUploadSavedFile(message_id=source['message_id'],pdf_uri=name,
            content_sha256=source['sha256'],original_filename=pdf.name,
            received_at=datetime.fromisoformat(source['received_at']))
        uploaded,duplicate=create_uploaded_pdf_from_upload(saved=saved,facility_hint=source['facility_id'],
            week_hint=source['week'],facility_name=facility['facility_name'],skip_ocr=False,
            source_kind='manual_upload',page_count=pages,facility_selection=selection,intake_protocol='native_ingest_v2')
        if duplicate or uploaded['status']!='pending':
            raise ValueError('trial_pending_registration_not_created')
        receipts.append({'uploaded_pdf_id':uploaded['id'],'message_id':saved.message_id,
            'sha256':source['sha256'],'facility_selection':selection})
    (data/'initialization-receipts.json').write_text(json.dumps(receipts,ensure_ascii=False,indent=2))
