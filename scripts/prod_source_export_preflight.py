"""Actions-only read-only preflight for the approved production settings API hotfix."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import requests

BASE = '7ae61ee1033776e2c413a621a2013c4c93002df4'
IMAGE = 'asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/backend@sha256:6c4bc8df1ca25b7eb0793a4b28aa89622cfc98356f1479247d5ed407e935a2f7'
ALLOWED = {'.github/workflows/deploy-prod.yml', '.github/workflows/deploy-prod-source-export.yml', 'backend/src/main.py', 'backend/src/api/staging_source_settings.py',
           'backend/tests/unit/test_staging_source_settings.py', 'backend/src/services/staging_source_boot_readonly.py', 'scripts/prod_source_export_preflight.py',
           'docs/prod_source_export_approval_20261023.md'}


def require_actions():
    if (os.getenv('GITHUB_ACTIONS') != 'true' or os.getenv('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or os.getenv('GITHUB_REPOSITORY') != 'mmorinag127/sawa-hospital-system'
            or os.getenv('GITHUB_ACTOR') != 'mmorinag127'
            or os.getenv('GITHUB_REF') != 'refs/heads/release/prod-stg-source-export-20261023'
            or os.getenv('PRODUCTION_CONFIRMATION') != 'DEPLOY_PRODUCTION'):
        raise RuntimeError('Owner-approved exact production source export workflow is required')
    subprocess.run(['git','merge-base','--is-ancestor',BASE,'HEAD'],check=True)
    paths=set(subprocess.check_output(['git','diff','--name-only',BASE,'HEAD'],text=True).splitlines())
    if paths - ALLOWED:
        raise RuntimeError('Production source export diff exceeds approval')


def live_service():
    return json.loads(subprocess.check_output(['gcloud','run','services','describe','worker-prod',
        '--project=sawahospitalsystem','--region=asia-northeast2','--format=json'],text=True))


def preflight(output):
    require_actions()
    live=live_service();traffic=[r for r in live['status']['traffic'] if r.get('percent',0)>0]
    if len(traffic)!=1 or traffic[0]['percent']!=100 or traffic[0]['revisionName']!='worker-prod-00590-d2l':
        raise RuntimeError('Serving production source changed')
    revision=json.loads(subprocess.check_output(['gcloud','run','revisions','describe',traffic[0]['revisionName'],
        '--project=sawahospitalsystem','--region=asia-northeast2','--format=json'],text=True))
    if revision['status']['imageDigest']!=IMAGE:
        raise RuntimeError('Serving production image changed')
    environment=revision['spec']['containers'][0].get('env',[])
    if any(item['name']=='UPLOADED_PDF_RECOVERY_ENABLED' for item in environment):
        raise RuntimeError('Candidate recovery override would not be isolated to its image')
    token=os.environ['DEPLOY_ID_TOKEN']
    response=requests.get(live['status']['url']+'/facility-master',headers={'Authorization':'Bearer '+token},timeout=60)
    if response.status_code!=200:
        raise RuntimeError('Existing canonical production master could not be read')
    actual=response.json()['facility_master']
    candidate=json.loads(Path('backend/src/data/facility_master.template.json').read_text())
    if {k:v for k,v in actual.items() if k!='facilities'} != {k:v for k,v in candidate.items() if k!='facilities'}:
        raise RuntimeError('Production runtime configuration differs from immutable build source')
    # Full content stays only in this ephemeral runner; upload hashes/counts only.
    result={'base_sha':BASE,'before_revision':traffic[0]['revisionName'],'before_image':IMAGE,
        'effective_master_sha256':hashlib.sha256(json.dumps(actual,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
        'facility_count':len(actual['facilities']),'immutable_globals_match':True,
        'ingest_blob_sha256':hashlib.sha256(Path('backend/src/data/ingest_policy.template.json').read_bytes()).hexdigest()}
    Path(output).write_text(json.dumps(result,indent=2)+'\n')


def probe(output):
    require_actions()
    response=requests.get('https://worker-stg-avlnzjjrca-dt.a.run.app/worker/staging-source-probe',
        headers={'Authorization':'Bearer '+os.environ['COORDINATOR_PROBE_ID_TOKEN']},timeout=240)
    if response.status_code!=200:
        raise RuntimeError('Exact staging coordinator production snapshot export proof failed')
    proof=response.json()
    if (proof.get('verified') is not True
            or proof.get('coordinator')!='worker-exec-stg@sawahospitalsystem.iam.gserviceaccount.com'
            or proof.get('candidate_revision')!=os.environ['CANDIDATE_REVISION']
            or not proof.get('database_snapshot')
            or set(proof.get('settings',{}))!={'FACILITY_MASTER_URI','INGEST_POLICY_URI','OCR_PIPELINE_STATE_URI'}):
        raise RuntimeError('Production snapshot export proof mismatch')
    Path(output).write_text(json.dumps(proof,indent=2)+'\n')


def verify(output):
    require_actions()
    before=json.loads(Path(output).read_text());live=live_service()
    endpoint=os.environ.get('CANDIDATE_URL') or live['status']['url']
    if not endpoint.startswith('https://') or not endpoint.endswith('.a.run.app'):
        raise RuntimeError('Unexpected production verification origin')
    response=requests.get(endpoint+'/facility-master' ,headers={'Authorization':'Bearer '+os.environ['DEPLOY_ID_TOKEN']},timeout=60)
    if response.status_code!=200:raise RuntimeError('Production master verification failed')
    master=response.json()['facility_master']
    current=hashlib.sha256(json.dumps(master,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    if current!=before['effective_master_sha256']:
        raise RuntimeError('Production effective master changed during API-only rollout')
    traffic=[x for x in live['status']['traffic'] if x.get('percent',0)>0]
    if len(traffic)!=1 or traffic[0]['percent']!=100:
        raise RuntimeError('Production serving revision is ambiguous')
    before.update(after_revision=traffic[0]['revisionName'],effective_master_unchanged=True)
    Path(output).write_text(json.dumps(before,indent=2)+'\n')


if __name__=='__main__':
    if sys.argv[1]=='preflight':preflight(sys.argv[2])
    elif sys.argv[1]=='verify':verify(sys.argv[2])
    elif sys.argv[1]=='probe':probe(sys.argv[2])
    elif sys.argv[1]=='source-gate':require_actions()
    elif sys.argv[1]=='candidate':
        value=json.loads(Path(sys.argv[2]).read_text())
        candidates=[row for row in value['status']['traffic'] if row.get('tag')=='staging-source-api']
        if len(candidates)!=1:raise RuntimeError('Candidate revision is ambiguous')
        with open(os.environ['GITHUB_ENV'],'a') as output:
            output.write('CANDIDATE_URL='+candidates[0]['url']+'\n')
            output.write('CANDIDATE_REVISION='+candidates[0]['revisionName']+'\n')
    else:raise SystemExit(2)
