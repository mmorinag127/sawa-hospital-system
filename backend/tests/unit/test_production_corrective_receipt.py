"""Synthetic verification records; never evidence of a production execution."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
import production_corrective_receipt as subject


def baseline():
    manifest = json.loads((subject.ROOT / 'scripts/local_hospital_test_manifest.json').read_text())['commands']
    records = [{'name': item['name'], 'command_sha256': subject.sha(item['command'].encode()),
                'log_sha256': 'c' * 64, 'exit_code': 1 if item['name'] == 'backend-tests' else 0}
               for item in manifest]
    run = {'receipt_version': 1, 'owner_attestation': 'manual', 'source_sha_start': 'a' * 40,
           'source_sha_end': 'a' * 40, 'clean_before': True, 'clean_after': True,
           'definition_sha256': 'b' * 64, 'commands': records}
    return {'failed_run': run, 'failed_run_sha256': 'd' * 64, 'full_run_success': False,
            'backend': {'source_sha': 'a' * 40, 'full': True, 'selected': 3, 'executed': 3,
                        'completed': 3, 'passed': 1, 'skipped': 1, 'reused': 0,
                        'failed': ['tests/unit/test_stg_web_image_recovery.py::test_bad_manifest'],
                        'report_sha256': 'e' * 64}}


@pytest.mark.parametrize('change', [None, 'success', 'business', 'incomplete', 'reused', 'focused',
    'source', 'definition', 'missing-command', 'other-command-failed', 'backend-zero', 'partition', 'boolean'])
def test_completed_failure_is_never_relabelled_as_full_success(change):
    value = baseline()
    if change == 'success': value['full_run_success'] = True
    if change == 'business': value['backend']['failed'] = ['tests/unit/test_saved_bag_read_parity.py::test_failed']
    if change == 'incomplete': value['backend']['completed'] = 2
    if change == 'reused': value['backend']['reused'] = 1
    if change == 'focused': value['backend']['full'] = False
    if change == 'source': value['backend']['source_sha'] = 'f' * 40
    if change == 'definition': value['failed_run']['definition_sha256'] = 'f' * 64
    if change == 'missing-command': value['failed_run']['commands'].pop()
    if change == 'other-command-failed': value['failed_run']['commands'][1]['exit_code'] = 1
    if change == 'backend-zero': value['failed_run']['commands'][0]['exit_code'] = 0
    if change == 'partition': value['backend']['passed'] = 2
    if change == 'boolean': value['backend']['executed'] = True
    if change:
        with pytest.raises(ValueError): subject.validate_baseline(value, 'b' * 64)
    else:
        before = deepcopy(value)
        assert subject.validate_baseline(value, 'b' * 64) == 'a' * 40
        assert value == before and value['failed_run']['commands'][0]['exit_code'] == 1


@pytest.mark.parametrize('change', [None, 'business-source', 'dirty', 'regression', 'command', 'source', 'signature'])
def test_corrective_proof_requires_unchanged_business_and_actual_related_success(monkeypatch, change):
    base = baseline()
    files = ['scripts/registry_image_digest.py']
    proof = {'receipt_version': 5, 'source_sha': 'f' * 40, 'clean_before': True, 'clean_after': True,
             'original_execution': {'payload': base, 'signature': 'synthetic'}, 'changed_files': files,
             'regression': {'command_sha256': subject.COMMAND_HASH, 'log_sha256': 'd' * 64, 'exit_code': 0}}
    if change == 'business-source': files[:] = ['backend/src/services/output_builder.py']
    if change == 'dirty': proof['clean_after'] = False
    if change == 'regression': proof['regression']['exit_code'] = 1
    if change == 'command': proof['regression']['command_sha256'] = '0' * 64
    if change == 'source': proof['source_sha'] = '0' * 40
    def verify(envelope):
        if change == 'signature': raise ValueError('invalid signature')
        return envelope['payload']
    monkeypatch.setattr(subject, 'verify_signed', verify)
    monkeypatch.setattr(subject, 'git', lambda *args: '\n'.join(files))
    monkeypatch.setattr(subject.subprocess, 'run', lambda *args, **kwargs: None)
    if change:
        with pytest.raises(ValueError): subject.validate(proof, 'f' * 40, 'b' * 64)
    else:
        subject.validate(proof, 'f' * 40, 'b' * 64)


@pytest.mark.parametrize('change', [None, 'unfinished', 'duplicate', 'overlap', 'log'])
def test_original_logs_and_complete_case_partition_are_checked(tmp_path, change):
    base = baseline()
    for index, record in enumerate(base['failed_run']['commands']):
        data = f'original log {index}\n'.encode()
        (tmp_path / f"{index:02d}-{record['name']}.log").write_bytes(data)
        record['log_sha256'] = subject.sha(data)
    (tmp_path / 'failed-run.json').write_text(json.dumps(base['failed_run']))
    failed = base['backend']['failed']
    report = {'source_sha': 'a' * 40, 'full': True, 'selected': ['passed', 'skipped', *failed],
              'executed': ['passed', 'skipped', *failed], 'completed': ['passed', 'skipped', *failed],
              'passed': ['passed'], 'skipped': ['skipped'], 'failed': failed, 'reused': [],
              'not_started': [], 'incomplete': [], 'exitstatus': 1}
    if change == 'unfinished': report['not_started'] = ['passed']
    if change == 'duplicate': report['selected'].append('passed')
    if change == 'overlap': report['passed'].append('skipped')
    if change == 'log': (tmp_path / '00-backend-tests.log').write_text('changed')
    (tmp_path / 'backend-tests').mkdir()
    (tmp_path / 'backend-tests/selection.json').write_text(json.dumps(report))
    if change:
        with pytest.raises(ValueError): subject.read_baseline(tmp_path, 'b' * 64)
    else:
        actual = subject.read_baseline(tmp_path, 'b' * 64)
        assert actual['full_run_success'] is False
        assert actual['backend']['completed'] == 3


def test_production_validator_delegates_without_faking_a_full_rerun(monkeypatch):
    import local_hospital_test_runner
    calls = []
    monkeypatch.setattr(subject, 'validate', lambda *args: calls.append(args))
    proof = {'receipt_version': 5}
    local_hospital_test_runner.validate_receipt(proof, 'f' * 40, 'b' * 64, require_full=True)
    assert calls == [(proof, 'f' * 40, 'b' * 64)]


def test_fixed_regression_suite_covers_resolver_consumer_tests():
    consumers = {str(path.relative_to(subject.ROOT))
                 for path in (subject.ROOT / 'backend/tests/unit').glob('test_*.py')
                 if 'registry_image_digest' in path.read_text() or 'resolve_image_digest' in path.read_text()}
    assert consumers <= set(subject.TESTS)
