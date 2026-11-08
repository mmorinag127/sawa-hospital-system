import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
import production_deploy_supplement as supplement
import local_hospital_test_runner as runner


@pytest.mark.parametrize('failure', [None, 'runtime', 'dependency', 'migration', 'files', 'source', 'regression', 'chained', 'signature'])
def test_only_verified_deployment_delta_can_supplement_full_result(monkeypatch, failure):
    old, new = 'a' * 40, 'b' * 40
    base = {'receipt_version': 4 if failure == 'chained' else 2, 'source_sha_end': old}
    full = Mock()
    monkeypatch.setattr(runner, 'validate_receipt', full)
    def verify(envelope):
        if failure == 'signature': raise ValueError('invalid signature')
        return base
    monkeypatch.setattr(supplement, 'verify_signed', verify)
    changed = ['scripts/deploy_worker_prod_with_checks.sh']
    if failure in {'runtime', 'dependency', 'migration'}:
        changed = [{'runtime': 'backend/src/db.py', 'dependency': 'backend/uv.lock', 'migration': 'backend/alembic/versions/new.py'}[failure]]
    monkeypatch.setattr(supplement, 'git', lambda *args: '\n'.join(changed))
    monkeypatch.setattr(supplement.subprocess, 'run', Mock())
    proof = {'receipt_version': 4, 'source_sha': old if failure == 'source' else new,
             'clean_before': True, 'clean_after': True, 'base_receipt': {'signed': True},
             'changed_files': [] if failure == 'files' else changed,
             'regression': {'command_sha256': supplement.COMMAND_HASH, 'log_sha256': 'c' * 64,
                            'exit_code': 1 if failure == 'regression' else 0}}
    if failure:
        with pytest.raises(ValueError): supplement.validate(proof, new, 'd' * 64)
    else:
        supplement.validate(proof, new, 'd' * 64)
        full.assert_called_once_with(base, old, 'd' * 64, require_full=True)


def test_supplement_does_not_allow_business_or_dependency_paths():
    for name in ('backend/src/db.py', 'backend/uv.lock', 'frontend/package-lock.json',
                 'backend/alembic/versions/migration.py', 'agent_runtime/main.py'):
        assert name not in supplement.ALLOWED
