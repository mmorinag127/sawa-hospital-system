import asyncio
import hashlib
import ast
from uuid import uuid4
from unittest.mock import Mock
import importlib.util
import json
from pathlib import Path

import pytest

from experiments.agent_assistant.runtime import source_manifest,verify_source
from experiments.agent_assistant.store import Conflict

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('sidecar',ROOT/'scripts/configure_agent_sidecar.py')
sidecar=importlib.util.module_from_spec(spec);spec.loader.exec_module(sidecar)


def test_lookup_does_not_copy_models_or_persist_orphan_before_images(tmp_path):
    async def scenario():
        from experiments.agent_assistant.tests.test_trial import prepared
        _, sandbox, _, _ = prepared(tmp_path, ('shift',))
        worktree = tmp_path / 'source'; worktree.mkdir(); (worktree / '.git').touch()
        models = tmp_path / 'models'; models.mkdir(); (models / 'weights').write_bytes(b'fixed-model')
        sandbox.configs['shift'].update(worktree=str(worktree), python='unused', model_cache=str(models))
        durable = Mock(); sandbox.durable_store = durable
        lookup = {'id': uuid4().hex, 'domains': ['shift'], 'purpose': 'target_lookup'}
        sandbox.prepare(lookup)
        durable.persist_files.assert_not_called()
        async def launch(*args): pass
        sandbox.launch = launch
        await sandbox.start(lookup)
        assert not list(sandbox.path(lookup['id']).rglob('model-cache'))
    asyncio.run(scenario())


def test_stop_reclaims_only_models_and_retains_business_and_before_data(tmp_path):
    async def scenario():
        from experiments.agent_assistant.tests.test_trial import prepared
        _, sandbox, _, run = prepared(tmp_path, ('shift',))
        directory = sandbox.path(run['id'])
        originals = {p.relative_to(directory): p.read_bytes() for p in directory.rglob('*') if p.is_file()}
        cache = directory / 'data/shift/home/model-cache'
        cache.mkdir(parents=True); (cache / 'weights').write_bytes(b'rebuildable-model')
        await sandbox.stop(run['id'])
        assert not cache.exists()
        assert {p.relative_to(directory): p.read_bytes() for p in directory.rglob('*') if p.is_file()} == originals
        await sandbox.stop(run['id'])
    asyncio.run(scenario())


def test_native_absolute_output_defaults_are_confined_per_request(tmp_path):
    from experiments.agent_assistant.tests.test_trial import prepared
    _, sandbox, _, run = prepared(tmp_path)
    data = sandbox.path(run['id']) / 'data' / 'hospital'
    (data / 'facility-master.json').write_text('{}')
    cfg = {'facility_master_file': 'facility-master.json'}
    env = sandbox.environment(run, 'hospital', cfg, data, ROOT, 'synthetic-token')
    # Discover settings from the actual native services so a newly added global
    # output default cannot silently escape the request's storage configuration.
    settings = set()
    for source in (ROOT / 'backend/src/services').glob('*.py'):
        for node in ast.walk(ast.parse(source.read_text())):
            if (isinstance(node, ast.Call) and len(node.args) >= 2
                    and ast.unparse(node.func) in {'os.getenv', 'os.environ.get'}
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)
                    and node.args[1].value.startswith('/tmp/')):
                settings.add(node.args[0].value)
    assert settings
    for setting in settings:
        assert Path(env[setting]).is_relative_to(data), setting
    assert len({env[key] for key in settings}) == len(settings)


def test_pinned_source_rejects_modified_or_added_code(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'app.py').write_text('original')
    manifest=tmp_path/'source.json';manifest.write_text(json.dumps({'source_revision':'abc','files':source_manifest(source)}))
    verify_source(source,manifest)
    (source/'extra.py').write_text('unexpected')
    with pytest.raises(Conflict):verify_source(source,manifest)
    (source/'extra.py').unlink();(source/'app.py').write_text('changed')
    with pytest.raises(Conflict):verify_source(source,manifest)


def test_sidecar_preserves_existing_identity_secret_refs_and_other_settings():
    required=('DB_HOST','DB_NAME','DB_USER','DB_PASSWORD','GEMINI_API_KEY','GOOGLE_OAUTH_CLIENT_ID','OCR_PIPELINE_BUCKET','OCR_PIPELINE_URL','OCR_PIPELINE_TARGET_AUDIENCE')
    values=[{'name':k,'valueFrom':{'secretKeyRef':{'name':k,'key':'latest'}}} for k in required]
    web={'metadata':{'namespace':'123','annotations':{'run.googleapis.com/ingress':'all'}},'spec':{'template':{'metadata':{'annotations':{'run.googleapis.com/cloudsql-instances':'sawahospitalsystem:asia-northeast2:orders-stg'}},'spec':{'serviceAccountName':'existing-web','containers':[{'name':'frontend','ports':[{'containerPort':8080}],'env':[{'name':'OPERATOR_PASSWORD','valueFrom':{'secretKeyRef':{'name':'operator','key':'latest'}}}]}]}},'traffic':[{'latestRevision':True,'percent':100}]}}
    worker={'spec':{'template':{'spec':{'containers':[{'env':values}]}}}}
    config={'operator_account':'existing@example.com','gemini_model':'gemini-test','input_usd_per_million':'1','output_usd_per_million':'2','max_output_tokens':'8192'}
    school={'spec':{'template':{'metadata':{'annotations':{'run.googleapis.com/cloudsql-instances':'sawahospitalsystem:asia-northeast2:school-lunch-stg'}},'spec':{'containers':[{'env':[{'name':'DATABASE_URL','valueFrom':{'secretKeyRef':{'name':'school-lunch-stg-database-url','key':'latest'}}}]}]}}}}
    result=sidecar.configure(web,worker,config,'registry/web@sha256:abc','registry/agent@sha256:def','test-token',school=school)
    rs=result['spec']['template']['spec'];assert rs['serviceAccountName']=='existing-web'
    assert len(rs['containers'])==2
    assert rs['containers'][0]['env'][0]['valueFrom']['secretKeyRef']['name']=='operator'
    assert rs['containers'][1]['env'][:7]==values[:7]
    assert all(entry in rs['containers'][1]['env'] for entry in values)
    school_env=next(x for x in rs['containers'][1]['env'] if x['name']=='SAWA_TRIAL_SCHOOL_DATABASE_URL')
    assert school_env['valueFrom']['secretKeyRef']['name']=='school-lunch-stg-database-url'
    assert not any(x['name']=='SAWA_TRIAL_SCHOOL_DATABASE_URL' for x in rs['containers'][0]['env'])
    assert result['spec']['template']['metadata']['annotations']['run.googleapis.com/cloudsql-instances']=='sawahospitalsystem:asia-northeast2:orders-stg,sawahospitalsystem:asia-northeast2:school-lunch-stg'
    assert result['spec']['traffic']==[{'latestRevision':True,'percent':100}]
    with pytest.raises(RuntimeError):sidecar.require_context({'GITHUB_ACTIONS':'false'})


def test_runtime_passes_existing_pipeline_settings_only_to_owned_hospital(tmp_path,monkeypatch):
    from experiments.agent_assistant.runtime import configuration
    domains={}
    for domain in ('hospital','shift','school-lunch'):
        source=tmp_path/'native'/domain;source.mkdir(parents=True)
        (source/'app.py').write_text('synthetic source')
        baseline=tmp_path/'baselines'/domain;baseline.mkdir(parents=True)
        (baseline/'fixture.txt').write_text('synthetic baseline')
        manifest=tmp_path/'manifests'/(domain+'.json');manifest.parent.mkdir(exist_ok=True)
        manifest.write_text(json.dumps({'source_revision':'synthetic','files':source_manifest(source)}))
        domains[domain]={'baseline_files':source_manifest(baseline)}
    (tmp_path/'release.json').write_text(json.dumps({'domains':domains}))
    settings={'SAWA_TRIAL_WEB_TOKEN':'x'*48,'SAWA_TRIAL_OPERATOR_ACCOUNT':'existing@example.com',
        'PORTAL_AUTH_ME_URL':'https://example.com/portal/auth/me','GEMINI_AGENT_MODEL':'synthetic-model',
        'OCR_PIPELINE_BUCKET':'existing-staging-bucket','OCR_PIPELINE_URL':'https://existing-pipeline.example.com',
        'OCR_PIPELINE_TARGET_AUDIENCE':'https://existing-pipeline.example.com'}
    for name,value in settings.items():monkeypatch.setenv(name,value)
    config=configuration(tmp_path)
    pipeline={k:v for k,v in settings.items() if k.startswith('OCR_PIPELINE_')}
    assert all(config['domains']['hospital']['env'][k]==v for k,v in pipeline.items())
    for domain in ('shift','school-lunch'):
        assert not pipeline.keys() & config['domains'][domain]['env'].keys()
