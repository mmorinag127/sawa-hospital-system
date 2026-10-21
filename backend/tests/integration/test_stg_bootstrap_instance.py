"""The real jq resolver must never pass multiple Cloud SQL names as one target."""
import copy
import json
from pathlib import Path
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[3]
ORDERS='sawahospitalsystem:asia-northeast2:orders-stg'
SCHOOL='sawahospitalsystem:asia-northeast2:school-lunch-stg'


def service():
    return {'spec': {'template': {'metadata': {'annotations': {
        'run.googleapis.com/cloudsql-instances': ORDERS+','+SCHOOL}}, 'spec': {'containers': [
        {'ports':[{'containerPort':8080}], 'env':[{'name':k,'value':v} for k,v in {
            'DB_HOST':'/cloudsql/'+ORDERS,'DB_NAME':'orders','DB_USER':'orders_app'}.items()]},
        {'name':'agent-runtime','image':'asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/agent-runtime@sha256:'+'a'*64}
    ]}}}}


def resolve(data, *, project='sawahospitalsystem', region='asia-northeast2', name='web-stg'):
    return subprocess.run(['jq','-er','--arg','project',project,'--arg','region',region,
        '--arg','service',name,'-f',str(ROOT/'scripts/staging_bootstrap_instance.jq')],
        input=json.dumps(data),text=True,capture_output=True)


@pytest.mark.parametrize('attachments',[ORDERS,ORDERS+','+SCHOOL,SCHOOL+','+ORDERS])
def test_bootstrap_uses_explicit_portal_host_not_attachment_list(attachments):
    data=service();data['spec']['template']['metadata']['annotations']['run.googleapis.com/cloudsql-instances']=attachments
    result=resolve(data)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()==ORDERS


@pytest.mark.parametrize('case',['prod','foreign','duplicate','missing_attachment','foreign_host','secret_host','duplicate_host','wrong_db','wrong_role','unknown_sidecar','missing_ingress','missing_sidecar','sidecar_ingress'])
def test_bootstrap_rejects_ambiguous_or_nonstaging_target(case):
    data=copy.deepcopy(service());t=data['spec']['template'];env=t['spec']['containers'][0]['env']
    if case=='prod':t['metadata']['annotations']['run.googleapis.com/cloudsql-instances']=ORDERS.replace('orders-stg','orders-prod')
    if case=='foreign':t['metadata']['annotations']['run.googleapis.com/cloudsql-instances']=ORDERS+',other:region:database'
    if case=='duplicate':t['metadata']['annotations']['run.googleapis.com/cloudsql-instances']=ORDERS+','+ORDERS
    if case=='missing_attachment':t['metadata']['annotations'].clear()
    if case=='foreign_host':env[0]['value']='/cloudsql/'+SCHOOL
    if case=='secret_host':env[0]={'name':'DB_HOST','valueFrom':{'secretKeyRef':{'name':'unknown','key':'latest'}}}
    if case=='duplicate_host':env.append(dict(env[0]))
    if case=='wrong_db':env[1]['value']='school_lunch'
    if case=='wrong_role':env[2]['value']='school_lunch_app'
    if case=='unknown_sidecar':t['spec']['containers'][1]['name']='other'
    if case=='missing_ingress':t['spec']['containers'][0].pop('ports')
    if case=='missing_sidecar':t['spec']['containers'].pop()
    if case=='sidecar_ingress':t['spec']['containers'][1]['ports']=[{'containerPort':8128}]
    assert resolve(data).returncode!=0


@pytest.mark.parametrize('kwargs',[{'project':'other'},{'region':'other'},{'name':'web-prod'}])
def test_bootstrap_rejects_wrong_execution_context(kwargs):
    assert resolve(service(),**kwargs).returncode!=0


def test_both_bootstrap_domains_share_resolver_before_proxy_start():
    script=(ROOT/'scripts/bootstrap_automation_user.sh').read_text()
    assert '-f scripts/staging_bootstrap_instance.jq' in script
    assert script.index('-f scripts/staging_bootstrap_instance.jq') < script.index('curl -fsSL')
    assert '"$proxy" --address 127.0.0.1 --port 5432 "$instance"' in script
    assert 'refs/heads/develop' in script and '${GITHUB_ACTIONS:-}' in script
