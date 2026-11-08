"""Synthetic label bytes only; no live requests or production data."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
import verify_live_breakfast_labels as subject


@pytest.mark.parametrize('target', ['stg', 'prod'])
@pytest.mark.parametrize('hosted', [None, ('GITHUB_ACTIONS', 'true'), ('GITHUB_ACTIONS', 'false'), ('CI', 'true')])
def test_hosted_verification_keeps_only_public_attestation(monkeypatch, tmp_path, capsys, target, hosted):
    data = '時間,メニュー,商品名１\r\n朝,副菜①,PRIVATE_MENU\r\n昼,主菜,PRIVATE_MENU\r\n'.encode('cp932')
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    monkeypatch.delenv('CI', raising=False)
    if hosted: monkeypatch.setenv(*hosted)
    monkeypatch.setenv('GITHUB_SHA', 'a' * 40)
    monkeypatch.setenv('VERIFICATION_TOKEN', 'synthetic-not-a-credential')
    monkeypatch.setattr(sys, 'argv', ['verify', '--target', target, '--order-id', 'ORD1234abcd',
                                     '--output-dir', str(tmp_path / 'verification')])
    def download(request, **kwargs):
        assert request.get_header('Authorization') == 'Bearer synthetic-not-a-credential'
        return BytesIO(data)
    monkeypatch.setattr(subject.urllib.request, 'urlopen', download)
    subject.main()
    stdout = capsys.readouterr().out
    expected = {'verification_commit': 'a' * 40, 'csv_sha256': hashlib.sha256(data).hexdigest(), 'verified': True}
    assert json.loads(stdout) == expected
    root = tmp_path / 'verification'
    assert json.loads((root / 'public-attestation.json').read_text()) == expected
    assert 'PRIVATE_MENU' not in stdout and 'ORD1234abcd' not in stdout and 'synthetic-not-a-credential' not in stdout
    if hosted:
        assert {path.name for path in root.iterdir()} == {'public-attestation.json'}
    else:
        assert (root / 'labels.csv').read_bytes() == data
        assert json.loads((root / 'summary.json').read_text())['rows'] == 2
        assert (root / 'labels.csv').stat().st_mode & 0o777 == 0o600
        assert (root / 'summary.json').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('rows', ['昼,主菜,PRIVATE_MENU', '朝,主菜,PRIVATE_MENU', '朝,副菜②,PRIVATE_MENU'])
def test_existing_breakfast_failures_still_stop_without_payload_files(monkeypatch, tmp_path, rows):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('VERIFICATION_TOKEN', 'synthetic-not-a-credential')
    monkeypatch.setattr(sys, 'argv', ['verify', '--target', 'prod', '--order-id', 'ORD1234abcd',
                                     '--output-dir', str(tmp_path / 'verification')])
    data = ('時間,メニュー,商品名１\r\n' + rows + '\r\n').encode('cp932')
    monkeypatch.setattr(subject.urllib.request, 'urlopen', lambda *args, **kwargs: BytesIO(data))
    with pytest.raises(AssertionError): subject.main()
    assert not (tmp_path / 'verification').exists()


@pytest.mark.parametrize('target', ['stg', 'prod'])
def test_live_label_artifacts_publish_only_allowlisted_metadata(target):
    root = Path(__file__).resolve().parents[3]
    workflow = yaml.safe_load((root / f'.github/workflows/deploy-{target}.yml').read_text())
    artifacts = [step['with'] for job in workflow['jobs'].values() for step in job.get('steps', [])
                 if step.get('uses', '').startswith('actions/upload-artifact@')
                 and step.get('with', {}).get('name') == f'live-label-display-{target}-${{{{ github.run_attempt }}}}']
    assert len(artifacts) == 1
    assert set(artifacts[0]['path'].splitlines()) == {'verification/public-attestation.json', 'verification/worker.json'}
