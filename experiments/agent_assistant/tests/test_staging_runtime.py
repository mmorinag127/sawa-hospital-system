import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from experiments.agent_assistant.runtime import source_manifest,verify_source
from experiments.agent_assistant.store import Conflict

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('sidecar',ROOT/'scripts/configure_agent_sidecar.py')
sidecar=importlib.util.module_from_spec(spec);spec.loader.exec_module(sidecar)


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
    web={'metadata':{'namespace':'123','annotations':{'run.googleapis.com/ingress':'all'}},'spec':{'template':{'metadata':{'annotations':{'run.googleapis.com/cloudsql-instances':'project:region:stg'}},'spec':{'serviceAccountName':'existing-web','containers':[{'name':'frontend','ports':[{'containerPort':8080}],'env':[{'name':'OPERATOR_PASSWORD','valueFrom':{'secretKeyRef':{'name':'operator','key':'latest'}}}]}]}},'traffic':[{'latestRevision':True,'percent':100}]}}
    worker={'spec':{'template':{'spec':{'containers':[{'env':values}]}}}}
    config={'operator_account':'existing@example.com','gemini_model':'gemini-test','input_usd_per_million':'1','output_usd_per_million':'2','max_output_tokens':'8192'}
    result=sidecar.configure(web,worker,config,'registry/web@sha256:abc','registry/agent@sha256:def','test-token')
    rs=result['spec']['template']['spec'];assert rs['serviceAccountName']=='existing-web'
    assert len(rs['containers'])==2
    assert rs['containers'][0]['env'][0]['valueFrom']['secretKeyRef']['name']=='operator'
    assert rs['containers'][1]['env'][:len(values)]==values
    assert result['spec']['template']['metadata']['annotations']['run.googleapis.com/cloudsql-instances']=='project:region:stg'
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
