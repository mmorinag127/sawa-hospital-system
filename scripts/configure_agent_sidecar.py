#!/usr/bin/env python3
"""GitHub Actions-only staging service spec, preserving current env/auth/IAM."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys


def require_context(env):
    expected={'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':'mmorinag127/sawa-hospital-system',
        'GITHUB_REF':'refs/heads/develop','PROJECT_ID':'sawahospitalsystem','REGION':'asia-northeast2',
        'WEB_SERVICE':'web-stg','WORKER_SERVICE':'worker-stg'}
    if any(env.get(k)!=v for k,v in expected.items()):
        raise RuntimeError('agent sidecar requires the regular staging Actions deployment')
    if env.get('GITHUB_EVENT_NAME') not in {'push','workflow_dispatch'}:
        raise RuntimeError('unsupported deployment event')


def service(name):
    result=subprocess.run(['gcloud','run','services','describe',name,'--project=sawahospitalsystem',
        '--region=asia-northeast2','--format=json'],capture_output=True,text=True,check=True)
    return json.loads(result.stdout)


def envmap(container): return {item['name']:item for item in container.get('env',[])}
def value(name,text): return {'name':name,'value':text}


def configure(web,worker,config,frontend_image,agent_image,token):
    if '@sha256:' not in frontend_image or '@sha256:' not in agent_image:
        raise RuntimeError('immutable image digests are required')
    spec=web['spec']['template']['spec']; containers=spec['containers']
    ingress=[c for c in containers if c.get('ports')]
    if len(ingress)!=1 or containers[0] is not ingress[0]:
        raise RuntimeError('canonical ingress container is unresolved')
    front=ingress[0]; front.setdefault('name','web')
    front['image']=frontend_image
    front_env=envmap(front); backend_env=envmap(worker['spec']['template']['spec']['containers'][0])
    required=('DB_HOST','DB_NAME','DB_USER','DB_PASSWORD','GEMINI_API_KEY','GOOGLE_OAUTH_CLIENT_ID','OCR_PIPELINE_BUCKET')
    if any(name not in backend_env for name in required):
        raise RuntimeError('existing staging database/model settings are incomplete')
    for name,text in {'SAWA_ASSISTANT_TRIAL_ENABLED':'true','SAWA_ASSISTANT_TRIAL_URL':'http://127.0.0.1:8128',
        'SAWA_TRIAL_WEB_TOKEN':token,'SAWA_TRIAL_OPERATOR_ACCOUNT':config['operator_account']}.items():
        front_env[name]=value(name,text)
    front['env']=list(front_env.values())
    env=[backend_env[k] for k in required]
    env.extend(backend_env[k] for k in ('OCR_PIPELINE_URL','OCR_PIPELINE_TARGET_AUDIENCE') if k in backend_env)
    env.extend(value(k,v) for k,v in {
        'SAWA_TRIAL_WEB_TOKEN':token,'SAWA_TRIAL_OPERATOR_ACCOUNT':config['operator_account'],
        'PORTAL_AUTH_ME_URL':'https://worker-stg-avlnzjjrca-dt.a.run.app/portal/auth/me',
        'GEMINI_AGENT_MODEL':config['gemini_model'],
        'GEMINI_AGENT_INPUT_USD_PER_MILLION':config['input_usd_per_million'],
        'GEMINI_AGENT_OUTPUT_USD_PER_MILLION':config['output_usd_per_million'],
        'GEMINI_AGENT_MAX_OUTPUT_TOKENS':config['max_output_tokens']}.items())
    # No additional identity, IAM grant, public endpoint, or business DB migration.
    agent={'name':'agent-runtime','image':agent_image,'env':env,
        'resources':{'limits':{'cpu':'4','memory':'8Gi'}},
        'startupProbe':{'tcpSocket':{'port':8128},'initialDelaySeconds':5,'periodSeconds':5,'failureThreshold':48}}
    others=[c for c in containers[1:] if c.get('name')!='agent-runtime']
    spec['containers']=[front,*others,agent]
    annotations=web['spec']['template'].setdefault('metadata',{}).setdefault('annotations',{})
    annotations['run.googleapis.com/cpu-throttling']='false'
    annotations['autoscaling.knative.dev/minScale']='0'
    annotations['autoscaling.knative.dev/maxScale']='1'
    annotations['run.googleapis.com/container-dependencies']=json.dumps({front['name']:['agent-runtime']})
    web['spec']['template']['metadata'].pop('name',None)
    # Preserve existing service identity, mounts, secret refs, ingress, and traffic.
    clean={'apiVersion':'serving.knative.dev/v1','kind':'Service',
        'metadata':{'name':'web-stg','namespace':web['metadata']['namespace'],
                    'annotations':web['metadata'].get('annotations',{})},'spec':web['spec']}
    for key in ('run.googleapis.com/operation-id','run.googleapis.com/urls'):
        clean['metadata']['annotations'].pop(key,None)
    return clean


def main():
    require_context(os.environ)
    token=secrets.token_urlsafe(48)
    print('::add-mask::'+token)
    config=json.loads(Path('agent_runtime/staging.json').read_text())
    document=configure(service('web-stg'),service('worker-stg'),config,os.environ['IMAGE'],
                       os.environ['SAWA_AGENT_RUNTIME_IMAGE'],token)
    path=Path(os.environ['RUNNER_TEMP'])/'sawa-agent-sidecar.json'
    path.write_text(json.dumps(document));path.chmod(0o600)
    try:
        subprocess.run(['gcloud','run','services','replace',str(path),'--project=sawahospitalsystem',
            '--region=asia-northeast2','--quiet'],check=True)
    finally:
        path.unlink(missing_ok=True)


if __name__=='__main__':main()
