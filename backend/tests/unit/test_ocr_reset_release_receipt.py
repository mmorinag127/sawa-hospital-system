"""Synthetic validator records are rejection tests, never release evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'scripts'))
import ocr_reset_release_receipt as subject


@pytest.mark.parametrize('change', [None, 'source', 'definition', 'dirty', 'baseline',
    'extra_file', 'missing_command', 'command', 'failed', 'bool_exit', 'log',
    'skipped', 'empty', 'junit', 'staging'])
def test_exact_source_and_complete_related_execution_required(monkeypatch, change):
    import local_hospital_test_runner
    paths = ['backend/src/services/order_workflow_v2_service.py']
    records = []
    for name, argv, directory in subject.COMMANDS:
        junit = None if name not in ('backend', 'agent') else dict(
            tests=100, failures=0, errors=0, skipped=0, sha256='e'*64)
        records.append(dict(name=name, command_sha256=hashlib.sha256(json.dumps([argv,directory]).encode()).hexdigest(),
            log_sha256='e'*64, exit_code=0, junit=junit))
    proof = dict(receipt_version=11,source_sha='f'*40,definition_sha256='d'*64,
        baseline={'payload':dict(receipt_version=10,source_sha=subject.BASE)},
        changed_files=paths,commands=records,clean_before=True,clean_after=True,staging_run=None)
    monkeypatch.setattr(subject,'verify_signed',lambda value:value['payload'])
    monkeypatch.setattr(local_hospital_test_runner,'validate_receipt',lambda *a,**kw:None)
    monkeypatch.setattr(subject,'successful_run',lambda *a:None)
    monkeypatch.setattr(subject,'require_staging_deployments',lambda *a:None)
    monkeypatch.setattr(subject,'git',lambda *a:'\n'.join(paths) if a[0]=='diff' else '')
    if change=='source':proof['source_sha']='a'*40
    if change=='definition':proof['definition_sha256']='a'*64
    if change=='dirty':proof['clean_after']=False
    if change=='baseline':proof['baseline']['payload']['receipt_version']=11
    if change=='extra_file':paths.append('backend/src/services/ocr_service.py')
    if change=='missing_command':records.pop()
    if change=='command':records[0]['command_sha256']='a'*64
    if change=='failed':records[0]['exit_code']=1
    if change=='bool_exit':records[0]['exit_code']=False
    if change=='log':records[0]['log_sha256']='invalid'
    if change=='skipped':records[1]['junit']['skipped']=1
    if change=='empty':records[1]['junit']['tests']=0
    if change=='junit':records[1]['junit']=None
    if change=='staging':proof['staging_run']=True
    before=deepcopy(proof)
    if change:
        with pytest.raises(ValueError):subject.validate(proof,'f'*40,'d'*64)
    else:
        subject.validate(proof,'f'*40,'d'*64)
        with pytest.raises(ValueError,match='successful staging'):
            subject.validate(proof,'f'*40,'d'*64,require_staging=True)
        proof['staging_run']=123
        subject.validate(proof,'f'*40,'d'*64,require_staging=True)
        proof['staging_run']=None
    assert proof==before


def test_production_requires_the_same_source_staging_receipt(monkeypatch):
    import local_hospital_test_runner
    calls=[]
    monkeypatch.setattr(subject,'validate',lambda *a,**kw:calls.append(kw))
    local_hospital_test_runner.validate_receipt({'receipt_version':11},'f'*40,'d'*64,require_full=True)
    assert calls==[{'require_staging':True}]


@pytest.mark.parametrize('dsn', [
    'host=remote.invalid dbname=agent_trial', 'host=127.0.0.1 dbname=orders',
    'host=/cloudsql/project:region:orders-prod dbname=agent_trial',
    'host=127.0.0.1 hostaddr=192.0.2.1 dbname=agent_trial',
])
def test_disposable_database_cannot_resolve_to_business_or_remote_target(dsn):
    with pytest.raises(ValueError):subject.require_local_postgres(dsn)


def test_explicit_local_disposable_database_is_accepted():
    subject.require_local_postgres('host=127.0.0.1 port=55439 dbname=agent_trial_ocr_reset')


@pytest.mark.parametrize('jobs',[
    [{'name':'restore-ocr-selection','conclusion':'success'}],
    [{'name':'deploy-backend','conclusion':'success'},{'name':'deploy-frontend','conclusion':'skipped'}],
    [{'name':'deploy-backend','conclusion':'failure'},{'name':'deploy-frontend','conclusion':'success'}],
])
def test_restore_or_partial_runs_cannot_be_used_as_production_staging_proof(jobs):
    with pytest.raises(ValueError,match='deployment jobs required'):
        subject.validate_staging_jobs(jobs)


def test_both_real_deployment_jobs_are_required():
    subject.validate_staging_jobs([{'name':name,'conclusion':'success'} for name in ('deploy-backend','deploy-frontend')])
