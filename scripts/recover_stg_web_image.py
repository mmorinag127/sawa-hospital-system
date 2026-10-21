"""Explicit web-stg image-only recovery with pre/post runtime DB verification."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'backend')]

from stg_image_protection import (require, require_context, immutable_image, ensure_policy,
                                 protect_image, protect_current, PREFIX)
from stg_ocr_producer_deploy import Blocked, cloud, PROJECT, REGION, comparable, ready_revision
from staging_db_target import (StagingConfigBlocked, _container, image_config, _access_token,
                               resolve_runtime, load_service_db_config, revision_image_digest)
from run_stg_runtime_schema_preflight import validate_catalog, run as runtime_preflight
from run_stg_menu_master_migration import cloud_sql_proxy
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

SERVICE = 'web-stg'
CONFIRMATION = 'RECOVER_WEB_STG_IMAGE_AND_KEEP_STG_IMAGES'
PHASE = 'context'


def service():
    return cloud('run', 'services', 'describe', SERVICE, f'--region={REGION}')


def exported():
    return cloud('run', 'services', 'describe', SERVICE, f'--region={REGION}', output='export')


def iam():
    return cloud('run', 'services', 'get-iam-policy', SERVICE, f'--region={REGION}')


def web_container(descriptor):
    try:
        return _container(descriptor, SERVICE)
    except StagingConfigBlocked as error:
        raise Blocked(str(error)) from None


def web_revision_image(descriptor):
    try:
        return revision_image_digest(descriptor, SERVICE)
    except StagingConfigBlocked as error:
        raise Blocked(str(error)) from None


def replacement(before, image, namespace):
    immutable_image(image, 'frontend')
    require(before.get('apiVersion') == 'serving.knative.dev/v1' and before.get('kind') == 'Service'
            and before.get('metadata', {}).get('name') == SERVICE
            and str(before['metadata'].get('namespace')) == str(namespace), 'service_target_unknown')
    desired = comparable(before)
    spec = desired['spec']['template']['spec']
    require(bool(re.fullmatch(r'[a-z0-9-]+@sawahospitalsystem\.iam\.gserviceaccount\.com',
                             spec.get('serviceAccountName', ''))), 'runtime_identity_unknown')
    web_container({'spec': spec})
    require(desired['spec'].get('traffic') == [{'latestRevision': True, 'percent': 100}],
            'explicit_latest_traffic_required')
    spec['containers'][0]['image'] = image
    return desired


def verify_candidate_database(template, config):
    web = resolve_runtime(template, config, SERVICE)
    worker = load_service_db_config(PROJECT, REGION, 'worker-stg')
    require((web.instance_connection_name, web.db_name, web.db_user) ==
            (worker.instance_connection_name, worker.db_name, worker.db_user), 'candidate_worker_DB_mismatch')
    with cloud_sql_proxy(web.instance_connection_name):
        engine = create_engine(URL.create('postgresql+psycopg2', username=web.db_user,
            password=web.db_password, host='127.0.0.1', port=5432, database=web.db_name),
            hide_parameters=True, connect_args={'connect_timeout': 15})
        try:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    validate_catalog(connection)
                finally:
                    transaction.rollback()
        finally:
            engine.dispose()


def verify_original_runtime(before, revision, expected_revision, expected_image):
    template = before['spec']['template']
    require(revision.get('metadata', {}).get('name') == expected_revision
            and template.get('metadata', {}).get('name', expected_revision) == expected_revision,
            'exported_revision_mismatch')
    requested = template['spec']
    observed = deepcopy(revision.get('spec', {}))
    containers = observed.get('containers', [])
    web_container({'spec': requested})
    require(web_revision_image(revision) == expected_image,
            'original_image_changed')
    source = requested['containers'][0]
    require(bool(re.fullmatch(re.escape(PREFIX + 'frontend') +
            r'(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}|@sha256:[0-9a-f]{64})',
            source.get('image', ''))), 'original_image_reference_rejected')
    if '@' in source['image']:
        require(source['image'] == expected_image, 'original_image_changed')
    # The named active revision binds the requested tag to its resolved digest.
    containers[0]['image'] = source['image']
    if 'name' not in source:
        require(containers[0].get('name') in (None, 'frontend-1'),
                'unrecognized_generated_container_name')
        containers[0].pop('name', None)
    require(requested == observed, 'original_runtime_mismatch')


def recover(image, expected_revision, expected_image):
    global PHASE
    require_context()
    require(os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and os.environ.get('RECOVERY_CONFIRMATION') == CONFIRMATION, 'explicit_dispatch_confirmation_required')
    immutable_image(image, 'frontend')
    immutable_image(expected_image, 'frontend')
    require(bool(re.fullmatch(r'web-stg-[a-z0-9-]+', expected_revision)), 'expected_revision_required')
    PHASE = 'candidate_source'
    config = image_config(image, 'frontend', _access_token())
    require(config.get('Labels', {}).get('sawa.git_sha') == os.environ['GITHUB_SHA'], 'candidate_source_mismatch')
    PHASE = 'capture_settings_identity_traffic'
    initial = service()
    require(ready_revision(initial) == expected_revision, 'original_revision_changed')
    require(initial.get('status', {}).get('url') == 'https://web-stg-avlnzjjrca-dt.a.run.app', 'service_url_mismatch')
    require(str(initial.get('metadata', {}).get('generation')) ==
            str(initial.get('status', {}).get('observedGeneration'))
            and initial.get('metadata', {}).get('generation') is not None, 'service_not_observed')
    revision = cloud('run', 'revisions', 'describe', expected_revision, f'--region={REGION}')
    require(web_revision_image(revision) == expected_image, 'original_image_changed')
    before = exported()
    policy = iam()
    require(isinstance(policy.get('bindings'), list) and bool(policy.get('etag')), 'original_IAM_unknown')
    project = cloud('projects', 'describe', PROJECT)
    desired = replacement(before, image, project['projectNumber'])
    # Compare the original active revision with the complete exported runtime settings.
    verify_original_runtime(before, revision, expected_revision, expected_image)
    require(resolve_runtime(revision, config, SERVICE) ==
            resolve_runtime(desired['spec']['template'], config, SERVICE), 'original_runtime_annotation_mismatch')
    PHASE = 'candidate_database_read_only'
    verify_candidate_database(desired['spec']['template'], config)
    PHASE = 'protect_candidate'
    ensure_policy()
    protect_image(image, 'frontend')
    PHASE = 'pre_replace_race_check'
    require(ready_revision(service()) == expected_revision and exported() == before and iam() == policy,
            'service_changed_during_capture')
    PHASE = 'replace_image_only'
    with tempfile.TemporaryDirectory(prefix='stg-web-recovery-', dir=os.environ.get('RUNNER_TEMP')) as folder:
        path = Path(folder) / 'service.json'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            json.dump(desired, handle)
        cloud('run', 'services', 'replace', str(path), f'--region={REGION}')
    PHASE = 'verify_settings_identity_traffic_digest'
    ready = ready_revision(service())
    require(ready != expected_revision, 'new_revision_required')
    require(comparable(exported()) == desired and iam() == policy, 'runtime_settings_or_IAM_changed')
    observed = cloud('run', 'revisions', 'describe', ready, f'--region={REGION}')
    require(web_revision_image(observed) == image, 'ready_image_mismatch')
    PHASE = 'post_recovery_database_read_only'
    runtime_preflight()
    PHASE = 'protect_current_stg_images'
    protect_current()
    return {'status': 'verified', 'source_sha': os.environ['GITHUB_SHA'],
            'previous_revision': expected_revision, 'ready_revision': ready, 'image': image}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--expected-image', required=True)
    args = parser.parse_args()
    try:
        result = recover(args.image, args.expected_revision, args.expected_image)
    except Exception as error:
        result = {'status': 'blocked', 'phase': PHASE,
                  'reason': str(error) if isinstance(error, Blocked) else 'details_suppressed',
                  'returncode': error.returncode if isinstance(error, Blocked) else None}
        print(json.dumps(result))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
