"""Offline workflow routing checks; never invoke cloud or deployment commands."""
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
BASE = '1088dc633e7203e0bce2203d45c165da50d38ba5'
PATH = '.github/workflows/deploy-stg.yml'


def baseline(path):
    return yaml.safe_load(subprocess.check_output(
        ['git', 'show', f'{BASE}:{path}'], cwd=ROOT, text=True))


def workflow():
    return yaml.safe_load((ROOT / PATH).read_text())


def test_normal_jobs_dependencies_and_checks_unchanged():
    old, new = baseline(PATH), workflow()
    assert new['concurrency'] == old['concurrency']
    assert new['env'] == old['env']
    for name, job in old['jobs'].items():
        if name != 'source-gate':
            assert new['jobs'][name] == job
    old_steps = old['jobs']['source-gate']['steps']
    new_steps = new['jobs']['source-gate']['steps'][1:]
    for old_step, new_step in zip(old_steps, new_steps, strict=True):
        if old_step.get('id') != 'changes':
            assert new_step == old_step
        else:
            # The existing normal change detection body stays byte-for-byte intact.
            normal_body = old_step['run'].split('set -euo pipefail\n', 1)[1]
            assert new_step['run'].endswith(normal_body)


def test_recovery_is_in_authorized_workflow_with_original_steps():
    current = workflow()
    recovery = current['jobs']['recover-web']
    old = baseline('.github/workflows/recover-stg-web-image.yml')
    assert recovery['steps'] == old['jobs']['recover-web']['steps']
    assert recovery['needs'] == 'source-gate'
    assert recovery['permissions'] == old['permissions']
    assert recovery['if'] == ("github.ref == 'refs/heads/develop' && "
                             "github.event_name == 'workflow_dispatch' && "
                             "inputs.mode == 'recover-web-image'")
    assert not (ROOT / '.github/workflows/recover-stg-web-image.yml').exists()
    assert 'uses' not in recovery
    inputs = current[True]['workflow_dispatch']['inputs']
    assert inputs['mode']['default'] == 'normal'
    assert inputs['mode']['options'] == ['normal', 'recover-web-image']


def environment():
    return {
        'PATH': os.defpath,
        'GITHUB_REPOSITORY': 'mmorinag127/sawa-hospital-system',
        'GITHUB_REF': 'refs/heads/develop',
        'GITHUB_EVENT_NAME': 'workflow_dispatch',
        'DEPLOY_MODE': 'recover-web-image',
        'RECOVERY_CONFIRMATION': 'RECOVER_WEB_STG_IMAGE_AND_KEEP_STG_IMAGES',
        'EXPECTED_REVISION': 'web-stg-00365-n99',
        'EXPECTED_IMAGE': 'asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/frontend@sha256:' + 'a' * 64,
        'OCR_MANIFEST': '', 'MENU_MANIFEST': '', 'VERIFY_MENU': 'false',
        'OUTPUT_ORDER': '', 'OUTPUT_DATE': '',
    }


def validate(env):
    step = workflow()['jobs']['source-gate']['steps'][0]
    assert step['name'] == 'Validate deployment mode and recovery inputs'
    return subprocess.run(['bash', '-c', step['run']], env=env, capture_output=True).returncode


@pytest.mark.parametrize('key,value', [
    ('GITHUB_REPOSITORY', 'other/repo'), ('GITHUB_REF', 'refs/heads/main'),
    ('GITHUB_EVENT_NAME', 'push'), ('DEPLOY_MODE', 'unknown'),
    ('RECOVERY_CONFIRMATION', ''), ('EXPECTED_REVISION', ''),
    ('EXPECTED_REVISION', 'web-prod-00365-n99'), ('EXPECTED_IMAGE', ''),
    ('EXPECTED_IMAGE', 'frontend:latest'), ('OCR_MANIFEST', 'gs://unexpected'),
    ('MENU_MANIFEST', 'gs://unexpected'), ('VERIFY_MENU', 'true'),
    ('OUTPUT_ORDER', 'ORD1'), ('OUTPUT_DATE', '2026-10-15'),
])
def test_invalid_recovery_stops_before_checkout_or_auth(key, value):
    env = environment()
    env[key] = value
    assert validate(env) != 0


def test_valid_recovery_disables_all_normal_deploy_surfaces(tmp_path):
    env = environment()
    assert validate(env) == 0
    output = tmp_path / 'output'
    env['GITHUB_OUTPUT'] = str(output)
    step = next(s for s in workflow()['jobs']['source-gate']['steps'] if s.get('id') == 'changes')
    subprocess.run(['bash', '-c', step['run']], env=env, check=True)
    assert output.read_text() == 'backend_changed=false\nfrontend_changed=false\n'


@pytest.mark.parametrize('event,mode', [('push', ''), ('workflow_dispatch', ''), ('workflow_dispatch', 'normal')])
def test_normal_default_preserved(event, mode):
    env = environment()
    env.update(GITHUB_EVENT_NAME=event, DEPLOY_MODE=mode,
               RECOVERY_CONFIRMATION='', EXPECTED_REVISION='', EXPECTED_IMAGE='')
    assert validate(env) == 0
    env['EXPECTED_REVISION'] = 'web-stg-00365-n99'
    assert validate(env) != 0
