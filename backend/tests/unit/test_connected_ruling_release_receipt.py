"""Receipt rejection coverage; synthetic records are not release evidence."""
from copy import deepcopy
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'scripts'))
import connected_ruling_release_receipt as subject


@pytest.mark.parametrize('change', [None, 'source', 'definition', 'dirty', 'baseline',
    'files', 'extra', 'failed', 'skipped', 'missing', 'command', 'log', 'bool_exit', 'staging'])
def test_exact_scope_and_success_required(monkeypatch, change):
    import hashlib
    import json
    import local_hospital_test_runner
    paths = ['backend/src/services/hakodate_step_review_pipeline_service.py']
    proof = dict(receipt_version=10, source_sha='f'*40, definition_sha256='d'*64,
        baseline={'payload':dict(receipt_version=9,source_sha=subject.BASE)},
        changed_files=paths, clean_before=True, clean_after=True, staging_run=None,
        regression=dict(command_sha256=hashlib.sha256(json.dumps(subject.COMMAND).encode()).hexdigest(),
            log_sha256='e'*64,junit_sha256='e'*64, tests=397,failures=0,errors=0,skipped=0,exit_code=0))
    monkeypatch.setattr(subject,'verify_signed',lambda envelope:envelope['payload'])
    monkeypatch.setattr(local_hospital_test_runner,'validate_receipt',lambda *a,**kw:None)
    monkeypatch.setattr(subject,'successful_run',lambda *args:None)
    monkeypatch.setattr(subject,'git',lambda *args:'\n'.join(paths) if args[0]=='diff' else '')
    if change == 'source': proof['source_sha']='a'*40
    if change == 'definition': proof['definition_sha256']='a'*64
    if change == 'dirty': proof['clean_after']=False
    if change == 'baseline': proof['baseline']['payload']['receipt_version']=10
    if change == 'files': proof['changed_files']=[]
    if change == 'extra': paths.append('backend/src/services/storage_service.py')
    if change == 'failed': proof['regression']['exit_code']=1
    if change == 'skipped': proof['regression']['skipped']=1
    if change == 'missing': del proof['regression']['junit_sha256']
    if change == 'command': proof['regression']['command_sha256']='a'*64
    if change == 'log': proof['regression']['log_sha256']='not-a-hash'
    if change == 'bool_exit': proof['regression']['exit_code']=False
    if change == 'staging': proof['staging_run']=True
    before=deepcopy(proof)
    if change:
        with pytest.raises(ValueError): subject.validate(proof,'f'*40,'d'*64)
    else:
        subject.validate(proof,'f'*40,'d'*64)
        with pytest.raises(ValueError,match='successful staging'):
            subject.validate(proof,'f'*40,'d'*64,require_staging=True)
        proof['staging_run']=123
        subject.validate(proof,'f'*40,'d'*64,require_staging=True)
        proof['staging_run']=None
    assert proof==before


def test_production_cannot_reuse_staging_only_proof(monkeypatch):
    import local_hospital_test_runner
    calls=[]
    monkeypatch.setattr(subject,'validate',lambda *args,**kw:calls.append(kw))
    local_hospital_test_runner.validate_receipt({'receipt_version':10},'f'*40,'d'*64,require_full=True)
    assert calls==[{'require_staging':True}]
