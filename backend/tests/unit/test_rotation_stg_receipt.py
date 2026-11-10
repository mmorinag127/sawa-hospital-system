from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'scripts'))
from local_hospital_test_runner import validate_receipt
from run_local_rotation_stg_proof import validate


def test_staging_delta_never_authorizes_production():
    with pytest.raises(ValueError, match='not a full production proof'):
        validate_receipt({'receipt_version': 8}, 'a'*40, 'b'*64, require_full=True)


@pytest.mark.parametrize('field,value', [('source_sha', 'stale'), ('definition_sha256', 'stale'),
    ('clean_before', False), ('clean_after', False), ('staging_only', False)])
def test_wrong_source_or_dirty_or_nonstaging_proof_is_rejected(field, value):
    proof = dict(receipt_version=8, source_sha='a'*40, definition_sha256='b'*64, baseline={},
        changed_files=[], commands=[], clean_before=True, clean_after=True, staging_only=True)
    proof[field] = value
    with pytest.raises(ValueError, match='Exact staging orientation proof required'):
        validate(proof, 'a'*40, 'b'*64)
