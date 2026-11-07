#!/usr/bin/env python3
"""GitHub Actions-only service spec, preserving current env/auth/IAM."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from experiments.agent_assistant.runtime import SCHOOL_RUNTIME_SETTINGS
from hospital_release_context import require_context as release_context


def require_context(env):
    environment = release_context(env)
    expected={'WEB_SERVICE':'web-'+environment,'WORKER_SERVICE':'worker-'+environment}
    if any(env.get(k)!=v for k,v in expected.items()):
        raise RuntimeError('agent sidecar service/environment mismatch')
    return environment


def service(name):
    result=subprocess.run(['gcloud','run','services','describe',name,'--project=sawahospitalsystem',
        '--region=asia-northeast2','--format=json'],capture_output=True,text=True,check=True)
    return json.loads(result.stdout)


def envmap(container): return {item['name']:item for item in container.get('env',[])}
def value(name,text): return {'name':name,'value':text}


def configure(web,worker,config,frontend_image,agent_image,token,*,school,portal_origin=None,environment='stg'):
    if environment not in {'stg', 'prod'}:
        raise RuntimeError('unsupported deployment environment')
    if web.get('metadata',{}).get('name') != 'web-'+environment or worker.get('metadata',{}).get('name') != 'worker-'+environment:
        raise RuntimeError('service/environment mismatch')
    if '@sha256:' not in frontend_image or '@sha256:' not in agent_image:
        raise RuntimeError('immutable image digests are required')
    spec=web['spec']['template']['spec']; containers=spec['containers']
    ingress=[c for c in containers if c.get('ports')]
    if len(ingress)!=1 or containers[0] is not ingress[0]:
        raise RuntimeError('canonical ingress container is unresolved')
    front=ingress[0]; front.setdefault('name','web')
    front['image']=frontend_image
    front_env=envmap(front); backend_env=envmap(worker['spec']['template']['spec']['containers'][0])
    if environment == 'prod' and 'GEMINI_API_KEY' not in backend_env:
        backend_env['GEMINI_API_KEY'] = {
            'name': 'GEMINI_API_KEY',
            'valueFrom': {'secretKeyRef': {'name': 'gemini-api-key', 'key': 'latest'}},
        }
    if portal_origin is not None:
        if portal_origin != f'https://web-{environment}-avlnzjjrca-dt.a.run.app':
            raise RuntimeError('portal origin is unresolved')
        front_env['PORTAL_PUBLIC_ORIGIN']=value('PORTAL_PUBLIC_ORIGIN',portal_origin)
    required=('DB_HOST','DB_NAME','DB_USER','DB_PASSWORD','GEMINI_API_KEY','GOOGLE_OAUTH_CLIENT_ID','OCR_PIPELINE_BUCKET')
    if any(name not in backend_env for name in required):
        raise RuntimeError('existing database/model settings are incomplete')
    if backend_env['DB_HOST'].get('value') != f'/cloudsql/sawahospitalsystem:asia-northeast2:orders-{environment}':
        raise RuntimeError('agent hospital database environment mismatch')
    for name,text in {'SAWA_ASSISTANT_TRIAL_ENABLED':'true','SAWA_ASSISTANT_TRIAL_URL':'http://127.0.0.1:8128',
        'SAWA_TRIAL_WEB_TOKEN':token,'SAWA_TRIAL_OPERATOR_ACCOUNT':config['operator_account']}.items():
        front_env[name]=value(name,text)
    front['env']=list(front_env.values())
    env=[backend_env[k] for k in required]
    school_spec=school['spec']['template']
    school_instance=f'sawahospitalsystem:asia-northeast2:school-lunch-{environment}'
    if school_spec.get('metadata',{}).get('annotations',{}).get('run.googleapis.com/cloudsql-instances') != school_instance:
        raise RuntimeError('registered school database source is unresolved')
    school_containers=school_spec['spec']['containers']
    if len(school_containers)!=1:
        raise RuntimeError('registered school API container is unresolved')
    school_database=envmap(school_containers[0]).get('DATABASE_URL',{})
    ref=school_database.get('valueFrom',{}).get('secretKeyRef',{})
    if ref.get('name')!=f'school-lunch-{environment}-database-url' or set(school_database)!={'name','valueFrom'}:
        raise RuntimeError('registered school database secret source is unresolved')
    env.append({**school_database,'name':'SAWA_TRIAL_SCHOOL_DATABASE_URL'})
    school_env = envmap(school_containers[0])
    if not school_env.get('PROCESS_GEMINI_MODEL', {}).get('value', '').strip():
        raise RuntimeError('registered school process model source is unresolved')
    for name in SCHOOL_RUNTIME_SETTINGS:
        if name in school_env:
            setting = school_env[name]
            if set(setting) != {'name', 'value'}:
                raise RuntimeError('registered school runtime setting must be a public value: ' + name)
            env.append(dict(setting))
    env.extend(backend_env[k] for k in ('OCR_PIPELINE_URL','OCR_PIPELINE_TARGET_AUDIENCE') if k in backend_env)
    env.extend(value(k,v) for k,v in {
        'SAWA_TRIAL_WEB_TOKEN':token,'SAWA_TRIAL_OPERATOR_ACCOUNT':config['operator_account'],
        'PORTAL_AUTH_ME_URL':f'https://worker-{environment}-avlnzjjrca-dt.a.run.app/portal/auth/me',
        'GEMINI_AGENT_MODEL':config['gemini_model'],
        'GEMINI_AGENT_INPUT_USD_PER_MILLION':config['input_usd_per_million'],
        'GEMINI_AGENT_OUTPUT_USD_PER_MILLION':config['output_usd_per_million'],
        'GEMINI_AGENT_MAX_OUTPUT_TOKENS':config['max_output_tokens']}.items())
    # No additional identity, IAM grant, public endpoint, or business DB migration.
    agent={'name':'agent-runtime','image':agent_image,'env':env,
        'resources':{'limits':{'cpu':'4','memory':'8Gi'}},
        'startupProbe':{'tcpSocket':{'port':8128},'periodSeconds':1,'failureThreshold':240}}
    others=[c for c in containers[1:] if c.get('name')!='agent-runtime']
    spec['containers']=[front,*others,agent]
    annotations=web['spec']['template'].setdefault('metadata',{}).setdefault('annotations',{})
    order_instance=f'sawahospitalsystem:asia-northeast2:orders-{environment}'
    if annotations.get('run.googleapis.com/cloudsql-instances') not in (order_instance,order_instance+','+school_instance):
        raise RuntimeError('existing Cloud SQL attachment is unresolved')
    annotations['run.googleapis.com/cloudsql-instances']=order_instance+','+school_instance
    if environment == 'stg':
        annotations['run.googleapis.com/cpu-throttling']='false'
        annotations['autoscaling.knative.dev/minScale']='0'
        annotations['autoscaling.knative.dev/maxScale']='1'
    dependencies=json.loads(annotations.get('run.googleapis.com/container-dependencies','{}'))
    if front['name'] in dependencies:
        dependencies[front['name']]=[name for name in dependencies[front['name']] if name!='agent-runtime']
        if not dependencies[front['name']]:
            del dependencies[front['name']]
    if dependencies:
        annotations['run.googleapis.com/container-dependencies']=json.dumps(dependencies)
    else:
        annotations.pop('run.googleapis.com/container-dependencies',None)
    web['spec']['template']['metadata'].pop('name',None)
    # Preserve existing service identity, mounts, secret refs, ingress, and traffic.
    clean={'apiVersion':'serving.knative.dev/v1','kind':'Service',
        'metadata':{'name':'web-'+environment,'namespace':web['metadata']['namespace'],
                    'annotations':web['metadata'].get('annotations',{}),
                    'labels':web['metadata'].get('labels',{})},'spec':web['spec']}
    for key in ('run.googleapis.com/operation-id','run.googleapis.com/urls'):
        clean['metadata']['annotations'].pop(key,None)
    return clean


def main():
    environment = require_context(os.environ)
    token=secrets.token_urlsafe(48)
    print('::add-mask::'+token)
    config=(json.loads(Path('agent_runtime/staging.json').read_text()) if environment == 'stg'
            else json.loads(os.environ['PROD_AGENT_RUNTIME_CONFIG']))
    required = ('operator_account', 'gemini_model', 'input_usd_per_million',
                'output_usd_per_million', 'max_output_tokens')
    if any(not isinstance(config.get(key), str) or not config[key].strip() for key in required):
        raise RuntimeError('explicit agent runtime configuration required')
    web = service('web-'+environment)
    if environment == 'prod' and os.environ.get('PRODUCTION_MAINTENANCE') == '1':
        from production_maintenance import restore_frontend_entrypoint
        web = restore_frontend_entrypoint(web)
    document=configure(web,service('worker-'+environment),config,os.environ['IMAGE'],
                       os.environ['SAWA_AGENT_RUNTIME_IMAGE'],token,school=service('price-api-'+environment),
                       portal_origin=os.environ['PORTAL_PUBLIC_ORIGIN'],environment=environment)
    if environment == 'prod' and os.environ.get('PRODUCTION_MAINTENANCE') == '1':
        from production_maintenance import load
        _, record = load()
        document['spec']['traffic'] = [{'revisionName': record['maintenance_revision'], 'percent': 100}]
        document['metadata'].setdefault('labels', {})['sawa-maintenance-run'] = os.environ['GITHUB_RUN_ID']
        front = document['spec']['template']['spec']['containers'][0]
        front['env'] = [entry for entry in front.get('env', []) if entry['name'] != 'SAWA_RELEASE_READ_ONLY']
        front['env'].append(value('SAWA_RELEASE_READ_ONLY', '1'))
    path=Path(os.environ['RUNNER_TEMP'])/'sawa-agent-sidecar.json'
    path.write_text(json.dumps(document));path.chmod(0o600)
    try:
        subprocess.run(['gcloud','run','services','replace',str(path),'--project=sawahospitalsystem',
            '--region=asia-northeast2','--quiet'],check=True)
    finally:
        path.unlink(missing_ok=True)


if __name__=='__main__':main()
