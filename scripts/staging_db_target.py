"""Staging-only config gate shared by migration and live-test cleanup; no DML."""
from dataclasses import dataclass, field
import hashlib
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy import text
from scripts import portal_prod_db_bootstrap as transport

PROJECT = 'sawahospitalsystem'
REGION = 'asia-northeast2'
INSTANCE = PROJECT + ':' + REGION + ':orders-stg'
DATABASE = 'orders'
ROLE = 'orders_app'
WORKER_URL = 'https://worker-stg-avlnzjjrca-dt.a.run.app'
SERVICE_URLS = {'web-stg': 'https://web-stg-avlnzjjrca-dt.a.run.app', 'worker-stg': WORKER_URL}
REGISTRY = 'asia-northeast2-docker.pkg.dev'
KEYS = {'DB_URI', 'DB_HOST', 'DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_DRIVER', 'DB_PORT', 'API_PROXY_TARGET'}


class StagingConfigBlocked(RuntimeError):
    """Only static, credential-free diagnostics cross this boundary."""


def require(ok, code):
    if not ok:
        raise StagingConfigBlocked('staging-db-' + code)


@dataclass(frozen=True)
class StagingDbConfig:
    instance_connection_name: str
    db_name: str
    db_user: str
    db_password: str = field(repr=False)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_http(url, token=None, *, accept='application/json', blob_redirect=False):
    headers = {'Accept': accept}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        response = urllib.request.build_opener(NoRedirect()).open(urllib.request.Request(url, headers=headers), timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        if blob_redirect and response.status in (301, 302, 303, 307, 308):
            target = urllib.parse.urlsplit(response.headers.get('Location', ''))
            require(target.scheme == 'https' and target.hostname == 'storage.googleapis.com'
                    and target.port in (None, 443) and not target.username and not target.password
                    and target.path.startswith('/') and not target.fragment, 'registry-blob-redirect-target-rejected')
            return read_http(target.geturl(), accept=accept)  # Never forward registry Authorization.
        body = response.read(2_000_001)
        require(len(body) <= 2_000_000, 'response-size-unexpected')
        return response.status, body


def image_config(image_digest, component, access_token):
    prefix = REGISTRY + '/sawahospitalsystem/backend/' + component + '@'
    require(image_digest.startswith(prefix), 'image-repository-mismatch')
    digest = image_digest[len(prefix):]
    require(bool(re.fullmatch(r'sha256:[0-9a-f]{64}', digest)), 'immutable-image-digest-required')
    base = 'https://' + REGISTRY + '/v2/sawahospitalsystem/backend/' + component
    status, raw = read_http(base + '/manifests/' + digest, access_token,
        accept='application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json')
    require(status == 200 and 'sha256:' + hashlib.sha256(raw).hexdigest() == digest, 'image-manifest-digest-mismatch')
    config = json.loads(raw).get('config', {}).get('digest', '')
    require(bool(re.fullmatch(r'sha256:[0-9a-f]{64}', config)), 'single-image-config-required')
    status, raw = read_http(base + '/blobs/' + config, access_token, blob_redirect=True)
    require(status == 200 and 'sha256:' + hashlib.sha256(raw).hexdigest() == config, 'image-config-digest-mismatch')
    return json.loads(raw)['config']


def _access_token():
    result = subprocess.run(['gcloud', 'auth', 'print-access-token'], capture_output=True, text=True, timeout=60)
    require(result.returncode == 0 and bool(result.stdout.strip()), 'registry-read-identity-unavailable')
    return result.stdout.strip()


def _container(descriptor):
    containers = descriptor.get('spec', {}).get('containers', [])
    require(len(containers) == 1, 'single-runtime-container-required')
    return containers[0]


def _entries(descriptor):
    entries = {}
    for entry in _container(descriptor).get('env', []):
        name = entry.get('name')
        if name in KEYS:
            require(name not in entries, 'duplicate-runtime-env')
            entries[name] = entry
    return entries


def resolve_runtime(descriptor, image, service):
    require(descriptor.get('metadata', {}).get('annotations', {}).get('run.googleapis.com/cloudsql-instances') == INSTANCE,
            'instance-annotation-mismatch')
    values = {}
    for item in image.get('Env', []):
        require(isinstance(item, str) and '=' in item, 'image-env-shape-unknown')
        name, value = item.split('=', 1)
        if name in KEYS:
            require(name not in values, 'duplicate-image-env')
            values[name] = value
    for name, entry in _entries(descriptor).items():
        if 'value' in entry:
            require(set(entry) == {'name', 'value'} and isinstance(entry['value'], str), 'ambiguous-runtime-env-source')
            values[name] = entry['value']
        else:
            # Only the credential may use Secret Manager. Routing-secret/URI shapes are unsupported.
            require(name == 'DB_PASSWORD', 'secret-routing-env-unsupported')
            require(set(entry) in ({'name', 'valueFrom'}, {'name', 'valueSource'}), 'ambiguous-runtime-env-source')
            ref = transport._extract_secret_ref(entry)
            require(ref is not None and bool(re.fullmatch(r'[A-Za-z0-9_-]+', ref[0])), 'password-secret-source-unknown')
            require(ref[1] == 'latest' or (ref[1] is not None and bool(re.fullmatch(r'[1-9][0-9]*', ref[1]))), 'password-secret-version-unknown')
            values[name] = transport._load_secret(PROJECT, *ref)
    # Product DB_URI wins even when other components look valid. Reject rather than reinterpret it.
    require(not values.get('DB_URI'), 'DB_URI-unsupported-no-component-fallback')
    require(values.get('DB_HOST') == '/cloudsql/' + INSTANCE, 'runtime-host-mismatch')
    require(values.get('DB_NAME') == DATABASE and values.get('DB_USER') == ROLE, 'runtime-database-or-role-mismatch')
    require(values.get('DB_DRIVER', 'postgresql+psycopg2') == 'postgresql+psycopg2', 'runtime-driver-unsupported')
    require(values.get('DB_PORT', '') in ('', '5432'), 'runtime-port-ambiguous')
    require(bool(values.get('DB_PASSWORD')), 'runtime-password-missing')
    if service == 'web-stg':
        require(values.get('API_PROXY_TARGET') == WORKER_URL, 'web-API-proxy-target-mismatch')
    return StagingDbConfig(INSTANCE, DATABASE, ROLE, values['DB_PASSWORD'])


def load_service_db_config(project, region, service, *, expected_revision=None):
    require((project, region) == (PROJECT, REGION) and service in ('web-stg', 'worker-stg'), 'target-context-rejected')
    def describe(kind, name):
        return transport._run_gcloud_json('run', kind, 'describe', name, '--project=' + PROJECT, '--region=' + REGION)
    data = describe('services', service)
    status = data.get('status', {})
    require(status.get('url') == SERVICE_URLS[service], 'service-URL-mismatch')
    require(data.get('metadata', {}).get('generation') is not None
            and str(data['metadata']['generation']) == str(status.get('observedGeneration')), 'service-config-not-observed')
    traffic = [t for t in status.get('traffic', []) if t.get('percent', 0) > 0]
    require(len(traffic) == 1 and traffic[0]['percent'] == 100, 'split-or-unknown-traffic')
    active = traffic[0].get('revisionName', '')
    intended = status.get('latestCreatedRevisionName', '')
    require(active.startswith(service + '-') and intended.startswith(service + '-'), 'revision-unresolved')
    require(intended == status.get('latestReadyRevisionName'), 'intended-revision-not-ready')
    require(expected_revision is None or active == expected_revision, 'API-source-revision-changed')
    template = data.get('spec', {}).get('template', {})
    access = _access_token()
    configs = {}
    for name in dict.fromkeys((intended, active)):
        revision = describe('revisions', name)
        require(revision.get('metadata', {}).get('name') == name, 'revision-descriptor-mismatch')
        require(any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in revision.get('status', {}).get('conditions', [])), 'revision-not-ready')
        image = image_config(revision.get('status', {}).get('imageDigest', ''), 'frontend' if service == 'web-stg' else 'backend', access)
        configs[name] = resolve_runtime(revision, image, service)
        if name == intended:
            require(_entries(template) == _entries(revision), 'intended-revision-env-mismatch')
            require(resolve_runtime(template, image, service) == configs[name], 'intended-runtime-mismatch')
    return configs[active]


def verify_connection_target(connection, *, database=DATABASE, role=ROLE):
    require(connection.dialect.name == 'postgresql', 'postgres-required')
    require(tuple(connection.execute(text('SELECT current_database(), current_user')).one()) == (database, role),
            'connected-database-or-role-mismatch')
