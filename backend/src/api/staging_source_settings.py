"""Fixed production settings export for the staging snapshot coordinator."""
import base64
import hashlib
import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from src.db import engine
from src.models.facility import Facility
from src.services import config_service
from src.services.ocr_pipeline_state_store import _state_uri

router = APIRouter()
AUDIENCE = 'https://worker-prod-avlnzjjrca-dt.a.run.app'
COORDINATOR = 'worker-exec-stg@sawahospitalsystem.iam.gserviceaccount.com'


def _authenticate(authorization):
    if os.environ.get('K_SERVICE') != 'worker-prod' or not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(403, 'Forbidden')
    try:
        claims = id_token.verify_oauth2_token(authorization[7:], GoogleRequest(), AUDIENCE)
        if claims.get('email') != COORDINATOR or claims.get('email_verified') is not True:
            raise ValueError('identity mismatch')
    except Exception:
        raise HTTPException(403, 'Forbidden') from None


def _file(path):
    raw = Path(path).read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('settings object required')
    return raw, value


def _effective_master(session, base):
    # Same explicit DB overlay as the currently deployed facility-master GET.
    # Empty DB is blocked here instead of selecting another source.
    facilities = []
    for facility in session.execute(select(Facility).order_by(Facility.id.asc())).scalars():
        item = {'facility_id': facility.id, 'facility_name': facility.name,
                'areas': [{'id': area.id, 'name': area.name} for area in facility.areas]}
        if facility.config is not None and isinstance(facility.config.config_json, dict):
            config = dict(facility.config.config_json)
            config.pop('facility_template_source', None)
            item.update(config)
        facilities.append(item)
    if not facilities:
        raise ValueError('production facility master empty')
    return {**base, 'facilities': facilities}


def _document(raw, path, *, raw_file_sha256=None):
    return {'content_base64': base64.b64encode(raw).decode('ascii'),
            'sha256': hashlib.sha256(raw).hexdigest(), 'path': str(path),
            'raw_file_sha256': raw_file_sha256 or hashlib.sha256(raw).hexdigest()}


@router.get('/internal/staging-source-settings')
def export_settings(request: Request, snapshot: str, authorization: str | None = Header(default=None)):
    _authenticate(authorization)
    if (set(request.query_params) != {'snapshot'}
            or not re.fullmatch(r'[0-9A-Fa-f]{8}-[0-9A-Fa-f]{8}-[1-9][0-9]*', snapshot)):
        raise HTTPException(422, 'Invalid snapshot')
    try:
        facility_uri = os.environ.get('FACILITY_MASTER_URI', '').strip()
        ingest_uri = os.environ.get('INGEST_POLICY_URI', '').strip()
        for uri in (facility_uri, ingest_uri):
            if uri and not uri.startswith(('gs://sawahospitalsystem-prod-raw/', 'gs://sawahospitalsystem-prod-templates/')):
                raise ValueError('production settings source invalid')
        if not facility_uri:
            facility_raw, facility_base = _file(config_service.FACILITY_MASTER_PATH)
        if not ingest_uri:
            ingest_raw, _ = _file(config_service.INGEST_POLICY_PATH)
        if engine.dialect.name != 'postgresql':
            raise ValueError('production PostgreSQL required')
        with engine.connect().execution_options(isolation_level='REPEATABLE READ', postgresql_readonly=True) as connection:
            connection.exec_driver_sql('SET TRANSACTION SNAPSHOT %s', (snapshot,))
            if connection.execute(text('SHOW transaction_read_only')).scalar_one() != 'on':
                raise ValueError('read only snapshot required')
            if connection.execute(text('SELECT current_database()')).scalar_one() != 'orders':
                raise ValueError('production database mismatch')
            if not facility_uri:
                with Session(bind=connection, autoflush=False) as session:
                    master = _effective_master(session, facility_base)
            connection.rollback()
        if not facility_uri:
            effective = json.dumps(master, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        state_uri = _state_uri()
        if not state_uri or not state_uri.startswith('gs://sawahospitalsystem-prod-raw/'):
            raise ValueError('production pipeline state unresolved')
        return {'schema': 1, 'revision': os.environ['K_REVISION'], 'database_snapshot': snapshot,
                'settings': {'FACILITY_MASTER_URI': facility_uri or _document(effective, config_service.FACILITY_MASTER_PATH,
                    raw_file_sha256=hashlib.sha256(facility_raw).hexdigest()),
                    'INGEST_POLICY_URI': ingest_uri or _document(ingest_raw, config_service.INGEST_POLICY_PATH),
                    'OCR_PIPELINE_STATE_URI': state_uri}}
    except Exception:
        raise HTTPException(409, 'production_settings_export_unavailable') from None
