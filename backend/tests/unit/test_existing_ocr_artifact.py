import importlib.util
from pathlib import Path
import os
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('existing_artifact', ROOT / 'scripts/verify_existing_ocr_artifact.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def fixture():
    run = dict(id=123, repository={'full_name': gate.REPOSITORY}, head_repository={'full_name': gate.REPOSITORY},
               path='.github/workflows/deploy-stg.yml', head_branch='develop', event='workflow_dispatch',
               head_sha='a' * 40, conclusion='failure')
    jobs = [dict(name='deploy-backend', run_id=123, status='completed', conclusion='success',
                 started_at='2026-10-16T00:00:00Z', completed_at='2026-10-16T00:20:00Z',
                 steps=[dict(name=gate.OCR_STEP, conclusion='success')])]
    artifacts = [dict(name='live-ocr-stg', expired=False, created_at='2026-10-16T00:19:00Z',
                      workflow_run=dict(id=123, head_sha='a' * 40))]
    return run, jobs, artifacts


def test_ui_failed_run_with_successful_real_ocr_is_accepted():
    gate.validate('123', *fixture())


@pytest.mark.parametrize('case', ['id', 'repo', 'head_repo', 'workflow', 'branch', 'event',
    'failed_backend', 'skipped_ocr', 'absent_ocr', 'expired', 'missing', 'duplicate', 'sha', 'old_attempt'])
def test_invalid_provenance_stops(case):
    run, jobs, artifacts = fixture()
    run_id = '123'
    if case == 'id': run_id = '../123'
    elif case == 'repo': run['repository']['full_name'] = 'other/repo'
    elif case == 'head_repo': run['head_repository']['full_name'] = 'other/repo'
    elif case == 'workflow': run['path'] = '.github/workflows/other.yml'
    elif case == 'branch': run['head_branch'] = 'main'
    elif case == 'event': run['event'] = 'pull_request'
    elif case == 'failed_backend': jobs[0]['conclusion'] = 'failure'
    elif case == 'skipped_ocr': jobs[0]['steps'][0]['conclusion'] = 'skipped'
    elif case == 'absent_ocr': jobs[0]['steps'] = []
    elif case == 'expired': artifacts[0]['expired'] = True
    elif case == 'missing': artifacts = []
    elif case == 'duplicate': artifacts *= 2
    elif case == 'sha': artifacts[0]['workflow_run']['head_sha'] = 'b' * 40
    elif case == 'old_attempt': artifacts[0]['created_at'] = '2026-10-15T00:00:00Z'
    with pytest.raises(ValueError): gate.validate(run_id, run, jobs, artifacts)


def workflow():
    return yaml.safe_load((ROOT / 'local-checks/legacy/deploy-stg.yml').read_text())


@pytest.mark.parametrize('run_id,manifest,expected', [('', '', 0), ('123', '', 0),
    ('abc', '', 1), ('123\n', '', 1), ('123', 'gs://manifest', 1), ('', 'gs://manifest', 0)])
def test_source_gate_exclusive_and_numeric(run_id, manifest, expected):
    env = dict(PATH=os.defpath, GITHUB_REPOSITORY=gate.REPOSITORY, GITHUB_REF='refs/heads/develop',
               GITHUB_EVENT_NAME='workflow_dispatch', DEPLOY_MODE='normal', RECOVERY_CONFIRMATION='',
               EXPECTED_REVISION='', EXPECTED_IMAGE='', OCR_MANIFEST=manifest, OCR_EXISTING_RUN=run_id)
    result = subprocess.run(['bash', '-c', workflow()['jobs']['source-gate']['steps'][0]['run']],
                            env=env, capture_output=True)
    assert (result.returncode != 0) == bool(expected)


def test_normal_deploy_and_verifier_commands_unchanged():
    from scripts.tls_workflow_test_contract import before_ca_upgrade
    approved_base = "c504c11cb49fcc6372f7fd11798e9cbfbd43ac56"
    subprocess.run(["git", "merge-base", "--is-ancestor", approved_base, "HEAD"], cwd=ROOT, check=True)
    baseline = yaml.safe_load(subprocess.check_output(
        ["git", "show", f"{approved_base}:.github/workflows/deploy-stg.yml"], cwd=ROOT))
    assert workflow() == baseline
