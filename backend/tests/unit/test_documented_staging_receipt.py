"""Synthetic rejection cases, not runtime verification or production evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'scripts'))
import documented_staging_receipt as subject


@pytest.mark.parametrize('path', [
    'backend/src/services/order_service.py', '.github/workflows/deploy-prod.yml',
    'backend/migrations/0040.py', 'backend/uv.lock', 'frontend/src/pages/assistant.tsx',
    'agent_runtime/Dockerfile', 'docs/release-evidence/rollback-db-compatibility.json',
    'docs/arbitrary.md', 'AGENTS.md', '../docs/numeric-quantity-production-20261117.md',
])
def test_no_runtime_security_migration_or_arbitrary_document_delta(path):
    with pytest.raises(ValueError, match='Only approved'):
        subject.require_paths([path])


@pytest.mark.parametrize('paths', [[], [subject.RUNNER, subject.RUNNER]])
def test_empty_or_duplicate_inventory_rejected(paths):
    with pytest.raises(ValueError):
        subject.require_paths(paths)


def test_exact_dispatcher_insertion_preserves_existing_checks():
    original = subprocess.check_output(['git','show',subject.STAGED+':'+subject.RUNNER],cwd=subject.ROOT)
    released = 'dc10e71ae44453bd98713e9c991413eefd64d25d'
    current = subprocess.check_output(['git', 'show', f'{released}:{subject.RUNNER}'], cwd=subject.ROOT)
    subject.require_dispatcher(original,current)
    for changed in (original, current+b'\n', current.replace(b'require_staging=require_full',b'require_staging=False'),
                    current.replace(b'receipt_version',b'unchecked_version',1)):
        with pytest.raises(ValueError):
            subject.require_dispatcher(original,changed)


@pytest.mark.parametrize('mode', ['100755 blob', '120000 blob', '160000 commit', 'missing'])
def test_executable_symlink_submodule_and_deletion_are_not_document_only(monkeypatch, mode):
    def fake(*args):
        if args[0]=='diff':return subject.RUNNER.encode()
        if args[0]=='ls-tree':return b'' if mode=='missing' else (mode+' '+subject.RUNNER+'\0').encode()
        return b''
    monkeypatch.setattr(subject,'git',fake)
    with pytest.raises(ValueError,match='Regular non-executable'):
        subject.checked_paths('f'*40)


@pytest.mark.parametrize('change', [None,'source','definition','version','bool_version','dirty',
    'full_rerun','child_source','child_version','child_run','child_no_run','inventory','command',
    'failed','bool_exit','log','record_extra','skipped','failures','errors','empty','bool_count','junit_hash'])
def test_signed_staging_and_complete_gate_execution_required(monkeypatch, change):
    import local_hospital_test_runner as runner
    paths=[subject.RUNNER]
    calls=[]
    monkeypatch.setattr(subject,'verify_signed',lambda value:value['payload'])
    monkeypatch.setattr(subject,'checked_paths',lambda source:paths)
    monkeypatch.setattr(runner,'validate_receipt',lambda *args,**kwargs:calls.append((args,kwargs)))
    proof=dict(receipt_version=12,source_sha='f'*40,definition_sha256='d'*64,
        staging_receipt={'payload':dict(receipt_version=11,source_sha=subject.STAGED,staging_run=subject.STAGING_RUN)},
        changed_files=paths.copy(),clean_before=True,clean_after=True,full_rerun=False,
        regression=dict(command_sha256=hashlib.sha256(json.dumps(subject.COMMAND).encode()).hexdigest(),
            log_sha256='e'*64,exit_code=0,
            junit=dict(tests=40,failures=0,errors=0,skipped=0,sha256='e'*64)))
    child=proof['staging_receipt']['payload'];record=proof['regression'];junit=record['junit']
    if change=='source':proof['source_sha']='a'*40
    if change=='definition':proof['definition_sha256']='a'*64
    if change=='version':proof['receipt_version']=11
    if change=='bool_version':proof['receipt_version']=True
    if change=='dirty':proof['clean_after']=False
    if change=='full_rerun':proof['full_rerun']=True
    if change=='child_source':child['source_sha']='a'*40
    if change=='child_version':child['receipt_version']=12
    if change=='child_run':child['staging_run']=43907946814
    if change=='child_no_run':child['staging_run']=None
    if change=='inventory':proof['changed_files']=[]
    if change=='command':record['command_sha256']='a'*64
    if change=='failed':record['exit_code']=1
    if change=='bool_exit':record['exit_code']=False
    if change=='log':record['log_sha256']='unknown'
    if change=='record_extra':record['skipped_command']=True
    if change in ('skipped','failures','errors'):junit[change]=1
    if change=='empty':junit['tests']=0
    if change=='bool_count':junit['failures']=False
    if change=='junit_hash':junit['sha256']='unknown'
    before=deepcopy(proof)
    if change:
        with pytest.raises(ValueError):subject.validate(proof,'f'*40,'d'*64)
    else:
        subject.validate(proof,'f'*40,'d'*64)
        assert calls[0][0][1:]==(subject.STAGED,'d'*64)
        assert calls[0][1]=={'require_full':True}
    assert proof==before


def test_new_dispatcher_cannot_weaken_old_receipt_or_approval_validation(monkeypatch):
    import local_hospital_test_runner as runner
    calls=[]
    monkeypatch.setattr(subject,'validate',lambda *args:calls.append(args))
    runner.validate_receipt({'receipt_version':12},'f'*40,'d'*64,require_full=True)
    assert calls==[({'receipt_version':12},'f'*40,'d'*64)]


def test_staging_signature_failure_is_not_reclassified_as_success(monkeypatch):
    def invalid(*args):raise ValueError('invalid signature')
    monkeypatch.setattr(subject,'verify_signed',invalid)
    proof=dict(receipt_version=12,source_sha='f'*40,definition_sha256='d'*64,
        staging_receipt={},changed_files=[],regression={},clean_before=True,clean_after=True,full_rerun=False)
    with pytest.raises(ValueError,match='invalid signature'):
        subject.validate(proof,'f'*40,'d'*64)
