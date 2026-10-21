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


def configure(web,worker,config,frontend_image,agent_image,token,*,school,portal_origin=None):
    if '@sha256:' not in frontend_image or '@sha256:' not in agent_image:
        raise RuntimeError('immutable image digests are required')
    spec=web['spec']['template']['spec']; containers=spec['containers']
    ingress=[c for c in containers if c.get('ports')]
    if len(ingress)!=1 or containers[0] is not ingress[0]:
        raise RuntimeError('canonical ingress container is unresolved')
    front=ingress[0]; front.setdefault('name','web')
    front['image']=frontend_image
    front_env=envmap(front); backend_env=envmap(worker['spec']['template']['spec']['containers'][0])
    if portal_origin is not None:
        if portal_origin != 'https://web-stg-avlnzjjrca-dt.a.run.app':
            raise RuntimeError('staging portal origin is unresolved')
        front_env['PORTAL_PUBLIC_ORIGIN']=value('PORTAL_PUBLIC_ORIGIN',portal_origin)
    required=('DB_HOST','DB_NAME','DB_USER','DB_PASSWORD','GEMINI_API_KEY','GOOGLE_OAUTH_CLIENT_ID','OCR_PIPELINE_BUCKET')
    if any(name not in backend_env for name in required):
        raise RuntimeError('existing staging database/model settings are incomplete')
    for name,text in {'SAWA_ASSISTANT_TRIAL_ENABLED':'true','SAWA_ASSISTANT_TRIAL_URL':'http://127.0.0.1:8128',
        'SAWA_TRIAL_WEB_TOKEN':token,'SAWA_TRIAL_OPERATOR_ACCOUNT':config['operator_account']}.items():
        front_env[name]=value(name,text)
    front['env']=list(front_env.values())
    env=[backend_env[k] for k in required]
    school_spec=school['spec']['template']
    school_instance='sawahospitalsystem:asia-northeast2:school-lunch-stg'
    if school_spec.get('metadata',{}).get('annotations',{}).get('run.googleapis.com/cloudsql-instances') != school_instance:
        raise RuntimeError('registered school staging database source is unresolved')
    school_containers=school_spec['spec']['containers']
    if len(school_containers)!=1:
        raise RuntimeError('registered school API container is unresolved')
    school_database=envmap(school_containers[0]).get('DATABASE_URL',{})
    ref=school_database.get('valueFrom',{}).get('secretKeyRef',{})
    if ref.get('name')!='school-lunch-stg-database-url' or set(school_database)!={'name','valueFrom'}:
        raise RuntimeError('registered school database secret source is unresolved')
    env.append({**school_database,'name':'SAWA_TRIAL_SCHOOL_DATABASE_URL'})
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
    order_instance='sawahospitalsystem:asia-northeast2:orders-stg'
    if annotations.get('run.googleapis.com/cloudsql-instances') not in (order_instance,order_instance+','+school_instance):
        raise RuntimeError('existing staging Cloud SQL attachment is unresolved')
    annotations['run.googleapis.com/cloudsql-instances']=order_instance+','+school_instance
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
                       os.environ['SAWA_AGENT_RUNTIME_IMAGE'],token,school=service('price-api-stg'),
                       portal_origin=os.environ['PORTAL_PUBLIC_ORIGIN'])
    path=Path(os.environ['RUNNER_TEMP'])/'sawa-agent-sidecar.json'
    path.write_text(json.dumps(document));path.chmod(0o600)
    try:
        subprocess.run(['gcloud','run','services','replace',str(path),'--project=sawahospitalsystem',
            '--region=asia-northeast2','--quiet'],check=True)
    finally:
        path.unlink(missing_ok=True)


if __name__=='__main__':main()
