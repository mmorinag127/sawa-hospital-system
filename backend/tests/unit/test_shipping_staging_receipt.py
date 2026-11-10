from pathlib import Path
import sys
from unittest.mock import Mock
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
import run_local_shipping_stg_proof as proof
import local_hospital_test_runner as runner

@pytest.mark.parametrize('bad',[None,'source','fields','runtime','failed','signature','deployment','chain'])
def test_staging_frontend_proof_is_exact_signed_and_scope_limited(monkeypatch,bad):
    new='b'*40
    base=dict(receipt_version=6 if bad=='chain' else 4,source_sha=proof.BASE)
    def verify(value):
        if bad=='signature': raise ValueError('signature invalid')
        return base
    monkeypatch.setattr(proof,'verify_signed',verify)
    validate=Mock()
    monkeypatch.setattr(runner,'validate_receipt',validate)
    changed=['backend/src/db.py'] if bad=='runtime' else ['frontend/src/pages/shipping-history.tsx']
    monkeypatch.setattr(proof,'git',lambda *args:'\n'.join(changed))
    import json
    monkeypatch.setattr(proof.subprocess,'check_output',lambda *args,**kwargs:json.dumps(dict(
        head_sha=proof.BASE,conclusion='failure' if bad=='deployment' else 'success',event='workflow_dispatch',
        actor=dict(login='mmorinag127'),path='.github/workflows/deploy-prod.yml')))
    value=dict(receipt_version=7,source_sha='c'*40 if bad=='source' else new,definition_sha256='d'*64,
        baseline={},changed_files=changed,commands=[dict(name=name,command_sha256=proof.command_hash(argv,directory),
        log_sha256='a'*64,exit_code=1 if bad=='failed' else 0) for name,argv,directory in proof.COMMANDS],
        clean_before=True,clean_after=True,staging_only=True)
    if bad=='fields': value['invented']=True
    if bad:
        with pytest.raises(ValueError): proof.validate(value,new,'d'*64)
    else:
        proof.validate(value,new,'d'*64)
        validate.assert_called_once_with(base,proof.BASE,'d'*64,require_full=True)

def test_shipping_proof_never_authorizes_production():
    with pytest.raises(ValueError,match='staging only'):
        runner.validate_receipt(dict(receipt_version=7),'a'*40,'b'*64,require_full=True)
