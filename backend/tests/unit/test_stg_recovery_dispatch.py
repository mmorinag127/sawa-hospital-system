"""Offline workflow routing checks; never invoke cloud or deployment commands."""
import os
from pathlib import Path
import subprocess

import pytest
import yaml
from scripts.tls_workflow_test_contract import before_ca_upgrade

ROOT = Path(__file__).resolve().parents[3]
BASE = '1088dc633e7203e0bce2203d45c165da50d38ba5'
PATH = '.github/workflows/deploy-stg.yml'


def baseline(path):
    return yaml.safe_load(subprocess.check_output(
        ['git', 'show', f'{BASE}:{path}'], cwd=ROOT, text=True))


def workflow():
    return yaml.safe_load((ROOT / PATH).read_text())


def test_normal_jobs_dependencies_and_checks_unchanged():
    from scripts.tls_workflow_test_contract import before_ca_upgrade
    approved_base = "c504c11cb49fcc6372f7fd11798e9cbfbd43ac56"
    subprocess.run(["git", "merge-base", "--is-ancestor", approved_base, "HEAD"], cwd=ROOT, check=True)
    baseline = yaml.safe_load(subprocess.check_output(
        ["git", "show", f"{approved_base}:.github/workflows/deploy-stg.yml"], cwd=ROOT))
    current = before_ca_upgrade(workflow())
    assert set(current[True]) == {'workflow_dispatch'}
    for name in ('build-backend', 'build-frontend', 'deploy-backend', 'deploy-frontend'):
        assert current['jobs'][name]['permissions'] == baseline['jobs'][name]['permissions']
        for step in baseline['jobs'][name]['steps']:
            if step.get('id') in ('auth-backend', 'auth-frontend') or step.get('name', '').startswith('Deploy '):
                assert step in current['jobs'][name]['steps']
    assert current['jobs']['recover-web']['needs'] == 'source-gate'
    assert {'expected_source_sha', 'local_test_receipt'} <= set(current[True]['workflow_dispatch']['inputs'])


def test_recovery_is_in_authorized_workflow_with_original_steps():
    current = before_ca_upgrade(workflow())
    recovery = current['jobs']['recover-web']
    old = baseline('.github/workflows/recover-stg-web-image.yml')
    expected_steps = yaml.safe_load(subprocess.check_output(
        ['git', 'show', '6b3ee83058c72bd7c2857fd3034191f575495a05:.github/workflows/deploy-stg.yml'],
        cwd=ROOT))['jobs']['recover-web']['steps']
    for actual, expected in zip(recovery['steps'], expected_steps, strict=True):
        if expected.get('name') == 'Require explicit staging source and recovery approval':
            assert expected['env'].items() <= actual['env'].items()
            for line in expected['run'].splitlines():
                assert line in actual['run'].splitlines()
            assert 'local_hospital_test_runner.py --validate' in actual['run']
        else:
            assert actual == expected
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
    ('EXPECTED_IMAGE', 'frontend:latest'),
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
    if event == 'push':
        assert validate(env) != 0
        return
    assert validate(env) == 0
    env['EXPECTED_REVISION'] = 'web-stg-00365-n99'
    assert validate(env) != 0


def test_diagnostic_and_ui_dispatch_inputs_are_removed():
    current = workflow()
    inputs = current[True]['workflow_dispatch']['inputs']
    assert not any(name.startswith(('diagnose_', 'verify_', 'ocr_', 'menu_')) for name in inputs)
    assert not any(name.startswith('diagnose-') for name in current['jobs'])
