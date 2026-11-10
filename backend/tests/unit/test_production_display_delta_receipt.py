from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
from production_display_delta_receipt import GATE_ONLY, require_gate_only_delta, validate


@pytest.mark.parametrize('path', ['backend/src/services/workflow_display_read_service.py',
    'frontend/src/pages/shipping.tsx','backend/migrations/0040.py','backend/uv.lock',
    '.github/workflows/deploy-prod.yml'])
def test_runtime_schema_dependency_or_workflow_changes_require_new_verification(path):
    with pytest.raises(ValueError,match='exactly equal'):
        require_gate_only_delta([*GATE_ONLY,path])


def test_only_verified_gate_changes_are_accepted():
    require_gate_only_delta(sorted(GATE_ONLY))
    with pytest.raises(ValueError):
        require_gate_only_delta([])


@pytest.mark.parametrize('field,value', [('source_sha','stale'),('definition_sha256','stale'),
    ('full_rerun',True),('clean_before',False),('clean_after',False)])
def test_dirty_stale_or_fabricated_full_proof_is_rejected(field,value):
    proof = dict(receipt_version=9,source_sha='a'*40,definition_sha256='b'*64,staging_receipt={},
        changed_files=[],regression={},clean_before=True,clean_after=True,full_rerun=False)
    proof[field] = value
    with pytest.raises(ValueError,match='Exact staged-runtime'):
        validate(proof,'a'*40,'b'*64)
