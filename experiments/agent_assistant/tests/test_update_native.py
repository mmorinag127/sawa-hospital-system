import hashlib
import json

import pytest

from experiments.agent_assistant import update_native as upgrade
from experiments.agent_assistant.runtime import verify_source
from experiments.agent_assistant.store import Conflict


def source(tmp_path, monkeypatch, before, current, after, relative='backend/src/contract.py'):
    bundle=tmp_path/'bundle'; path=bundle/relative
    path.parent.mkdir(parents=True);path.write_bytes(current)
    manifest={'source_revision':'base','files':{relative:hashlib.sha256(current).hexdigest()}}
    manifest_path=tmp_path/'manifest.json';manifest_path.write_text(json.dumps(manifest))
    def git(repo,*args):
        if args[0]=='rev-parse':return b'target\n'
        if args[0]=='diff':
            if relative.startswith('ocr_pipeline/'):
                assert 'ocr_pipeline' in args
            return relative.encode()+b'\n' if '--name-only' in args else b'explicit delta'
        raise AssertionError(args)
    monkeypatch.setattr(upgrade,'git',git)
    monkeypatch.setattr(upgrade,'blob',lambda repo,revision,name: before if revision=='base' else after)
    return relative,bundle,path,manifest,manifest_path,tmp_path/'report.json'


@pytest.mark.parametrize('relative',['backend/src/contract.py','ocr_pipeline/app/__init__.py'])
def test_three_way_upgrade_retains_extension_and_pins_exact_hashes(tmp_path,monkeypatch,relative):
    before=b'a=1\nb=1\nc=1\nd=1\ne=1\nf=1\n'
    current=before+b'agent_extension=True\n'
    after=before.replace(b'a=1',b'a=2')
    relative,bundle,path,manifest,pin,report=source(tmp_path,monkeypatch,before,current,after,relative)
    result=upgrade.merge(tmp_path,bundle,manifest,'target',report)
    assert not result['conflicts']
    assert path.read_bytes()==after+b'agent_extension=True\n'
    upgrade.seal(tmp_path,bundle,pin,report)
    verify_source(bundle,pin)
    value=json.loads(pin.read_text())
    assert value['source_revision']=='target'
    assert value['retained_extensions']==[{'path':relative,'upstream_sha256':hashlib.sha256(after).hexdigest(),
                                         'bundled_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]
    path.write_bytes(after)
    with pytest.raises(Conflict):verify_source(bundle,pin)


def test_conflicting_extension_cannot_be_sealed(tmp_path,monkeypatch):
    relative,bundle,path,manifest,pin,report=source(tmp_path,monkeypatch,b'a=1\n',b'a=2\n',b'a=3\n')
    result=upgrade.merge(tmp_path,bundle,manifest,'target',report)
    assert result['conflicts']==[relative]
    assert b'<<<<<<< BUNDLED_EXTENSION' in path.read_bytes()
    with pytest.raises(RuntimeError,match='Unresolved source conflict'):
        upgrade.seal(tmp_path,bundle,pin,report)
    assert json.loads(pin.read_text())['source_revision']=='base'


@pytest.mark.parametrize('before,after',[(None,b'new upstream'),(b'old upstream',None)])
def test_add_delete_ambiguity_preserves_extension_and_blocks_pin(tmp_path,monkeypatch,before,after):
    relative,bundle,path,manifest,pin,report=source(tmp_path,monkeypatch,before,b'custom extension',after)
    result=upgrade.merge(tmp_path,bundle,manifest,'target',report)
    assert result['conflicts']==[relative]
    assert path.read_bytes()==b'custom extension'
    with pytest.raises(RuntimeError,match='add/delete conflict'):
        upgrade.seal(tmp_path,bundle,pin,report)


def test_owned_native_environment_keeps_auth_enabled(tmp_path,monkeypatch):
    from experiments.agent_assistant.tests.test_trial import prepared
    _,sandbox,_,run=prepared(tmp_path,('shift',))
    monkeypatch.setenv('SAWA_TEST_DISABLE_API_AUTH','true')
    monkeypatch.setenv('AUTH_DISABLED','true')
    monkeypatch.setenv('ORT_DISABLE_TELEMETRY','0')
    data=sandbox.path(run['id'])/'data/shift'
    env=sandbox.environment(run,'shift',{},data,tmp_path,'synthetic-token')
    assert env['SAWA_ENVIRONMENT']=='test'
    assert env['SAWA_REQUIRE_API_AUTH']=='true'
    assert env['ORT_DISABLE_TELEMETRY']=='1'
    assert 'SAWA_TEST_DISABLE_API_AUTH' not in env
    assert 'AUTH_DISABLED' not in env
    assert env['DATABASE_URL']=='sqlite:///'+str(data/'database.sqlite3')


def test_uncommitted_source_is_explicitly_recorded_and_cannot_change_before_seal(tmp_path, monkeypatch):
    relative, bundle, path, manifest, pin, report = source(
        tmp_path, monkeypatch, b'a=1\n', b'a=1\n', b'a=2\n')
    snapshot = {relative: b'a=3\n'}
    monkeypatch.setattr(upgrade, 'working_snapshot', lambda *_: dict(snapshot))
    result = upgrade.merge(tmp_path, bundle, manifest, 'target', report, working_tree=True)
    assert result['conflicts'] == [] and path.read_bytes() == b'a=3\n'
    assert result['working_source_files'] == {relative: hashlib.sha256(b'a=3\n').hexdigest()}
    snapshot[relative] = b'a=4\n'
    with pytest.raises(RuntimeError, match='changed after synchronization'):
        upgrade.seal(tmp_path, bundle, pin, report)
    assert json.loads(pin.read_text())['source_revision'] == 'base'
    snapshot[relative] = b'a=3\n'
    upgrade.seal(tmp_path, bundle, pin, report)
    verify_source(bundle, pin)
    sealed = json.loads(pin.read_text())
    assert sealed['source_revision'] == 'target'
    assert sealed['working_source_files'] == result['working_source_files']
    assert sealed['retained_extensions'][0]['bundled_sha256'] == hashlib.sha256(b'a=3\n').hexdigest()
