"""Read-only proof of the production API candidate from the exact coordinator identity."""
import os
from contextlib import closing
from urllib.parse import urlsplit
from fastapi import APIRouter, Header, HTTPException
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from src.services.staging_copy_refresh_engine import _cloud_session, _database, _proxy, _connect, runtime_source
from src.services.staging_prod_settings_transport import export_runtime_settings

router = APIRouter()
SERVICE = 'projects/sawahospitalsystem/locations/asia-northeast2/services/worker-prod'
AUDIENCE = 'https://worker-stg-avlnzjjrca-dt.a.run.app'
RELEASE_IDENTITY = 'sawa-github-deploy-prod@sawahospitalsystem.iam.gserviceaccount.com'


def _authorize(authorization):
    if os.getenv('K_SERVICE') != 'worker-stg' or not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(403, 'Forbidden')
    try:
        claims = id_token.verify_oauth2_token(authorization[7:], Request(), AUDIENCE)
        if claims.get('email') != RELEASE_IDENTITY or claims.get('email_verified') is not True:
            raise ValueError('identity mismatch')
    except Exception:
        raise HTTPException(403, 'Forbidden') from None


def probe_candidate():
    response = _cloud_session().get('https://run.googleapis.com/v2/' + SERVICE, timeout=30)
    if response.status_code != 200:
        raise ValueError('production runtime unavailable')
    candidates = [row for row in response.json().get('trafficStatuses', []) if row.get('tag') == 'staging-source-api']
    if len(candidates) != 1 or candidates[0].get('percent', 0) != 0:
        raise ValueError('production export candidate ambiguous')
    candidate = candidates[0]
    revision = candidate.get('revision', '')
    if '/' not in revision:
        revision = SERVICE + '/revisions/' + revision
    if not revision.startswith(SERVICE + '/revisions/'):
        raise ValueError('production export candidate invalid')
    endpoint = candidate.get('uri', '')
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.a.run.app'):
        raise ValueError('production export candidate invalid')
    source_uri, _, serving_revision = runtime_source(SERVICE)
    config = _database(source_uri, 'prod')
    with _proxy(config) as port, closing(_connect(config, port)) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SHOW transaction_read_only')
            if cursor.fetchone() != ('on',):raise ValueError('production read-only snapshot required')
            cursor.execute('SELECT pg_export_snapshot()')
            snapshot = cursor.fetchone()[0]
        value = export_runtime_settings(revision, snapshot, endpoint=endpoint)
        connection.rollback()
    return {'verified': True, 'coordinator': 'worker-exec-stg@sawahospitalsystem.iam.gserviceaccount.com',
            'candidate_revision': value['revision'], 'serving_revision': serving_revision,
            'database_snapshot': snapshot,
            'settings': {key: {'sha256': setting['sha256'], 'raw_file_sha256': setting['raw_file_sha256']}
                if isinstance(setting, dict) else {'uri': setting} for key, setting in value['settings'].items()}}


@router.get('/worker/staging-source-probe')
def production_source_probe(authorization: str | None = Header(default=None)):
    _authorize(authorization)
    try:
        return probe_candidate()
    except Exception:
        raise HTTPException(409, 'production_source_probe_failed') from None
