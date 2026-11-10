"""Owner-dispatched rolling release; never displays maintenance or disables writers."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, build_opener

import yaml
from hospital_production_preflight import cloud, revision_images, require_runtime_act_as
from hospital_release_context import PROJECT, REGION, require_context
from registry_image_digest import resolve_image_digest
from staging_db_target import NoRedirect

ROOT = Path(__file__).resolve().parents[1]
BASE = '9a2b8a63c07aeeef9407ca93069812d5d6664323'
BASE_RUN = '42546485239'
SERVICES = ('worker-prod', 'web-prod', 'ocr-pipeline-prod')


def require(value, message):
    if not value:
        raise RuntimeError(message)


def context():
    require(require_context(os.environ) == 'prod', 'production owner context required')
    require(os.environ.get('ONLINE_RELEASE') == 'true', 'explicit online release required')
    require(not any(os.environ.get(key) for key in
        ('ROLLBACK_RUN', 'MAINTENANCE_RESUME_RUN', 'MIGRATED_CONTINUATION')),
        'online release cannot combine with recovery modes')
    require([v.get('account') for v in cloud('auth','list','--filter=status:ACTIVE')] ==
            [f'sawa-github-deploy-prod@{PROJECT}.iam.gserviceaccount.com'], 'production identity required')
    sha = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    require(sha == os.environ['GITHUB_SHA'], 'checkout source mismatch')
    subprocess.run(['git','merge-base','--is-ancestor',BASE,sha],cwd=ROOT,check=True)
    subprocess.run(['git','diff','--quiet',BASE,sha,'--','agent_runtime','ocr_pipeline',
                    'backend/pyproject.toml','backend/uv.lock','backend/Dockerfile'],cwd=ROOT,check=True)
    original = json.loads(subprocess.check_output(['gh','api',
        f'repos/mmorinag127/sawa-hospital-system/actions/runs/{BASE_RUN}'],text=True))
    require(original.get('head_sha') == BASE and original.get('conclusion') == 'success'
            and original.get('event') == 'workflow_dispatch', 'reviewed production baseline required')


def serving(service):
    traffic = service['status'].get('traffic', [])
    require(not any(v.get('tag') for v in traffic), 'existing tagged routes require separate review')
    active = [v for v in traffic if v.get('percent')]
    require(len(active) == 1 and active[0].get('percent') == 100
            and active[0].get('revisionName'), 'single explicit serving revision required')
    return active[0]['revisionName']


def identity(service):
    revision = cloud('run','revisions','describe',serving(service),'--region='+REGION)
    require(any(v.get('type') == 'Ready' and v.get('status') == 'True'
                for v in revision['status']['conditions']), 'original revision must be ready')
    return dict(revision=revision['metadata']['name'], uid=revision['metadata']['uid'],
                images=[resolve_image_digest(v) for v in revision_images(revision)])


def replacement(service, image, name, tag, source_sha):
    spec = copy.deepcopy(service['spec'])
    template = spec['template']
    template['metadata']['name'] = name
    template['metadata'].setdefault('labels', {})['sawa-source-sha'] = source_sha
    containers = template['spec']['containers']
    require(containers and (len(containers) == 1 or
            containers[0].get('ports') and not any(c.get('ports') for c in containers[1:])),
            'unambiguous primary container required')
    for container in containers:
        require(not any(v.get('name') == 'SAWA_RELEASE_READ_ONLY' and v.get('value') == '1'
                        for v in container.get('env', [])), 'unfinished read-only release rejected')
    containers[0]['image'] = image
    spec['traffic'] = [dict(revisionName=serving(service), percent=100),
                       dict(revisionName=name, percent=0, tag=tag)]
    metadata = {k:copy.deepcopy(v) for k,v in service['metadata'].items()
                if k in ('name','labels','annotations','resourceVersion')}
    require(not metadata.get('labels', {}).get('sawa-maintenance-run'), 'unfinished maintenance rejected')
    require(metadata.get('annotations', {}).get('run.googleapis.com/scalingMode','automatic') == 'automatic',
            'service scaling must remain automatic')
    return dict(apiVersion=service['apiVersion'], kind=service['kind'], metadata=metadata, spec=spec)


def request(url, token=None, expected=200):
    headers = {'Authorization':'Bearer '+token} if token else {}
    try:
        response = build_opener(NoRedirect()).open(Request(url,headers=headers),timeout=120)
    except HTTPError as error:
        response = error
    with response:
        require(response.status == expected, 'live verification status mismatch: '+str(response.status))
        body = response.read(2_000_001)
        require(len(body) <= 2_000_000, 'live verification response too large')
    return body


def verify_worker(url, token, order):
    request(url+'/orders?include_ocr=false',expected=401)
    request(url+'/portal/auth/me?system=hospital',token)
    request(url+'/orders/'+order+'/workflow-v2',token)


def verify_web(url, token, order):
    request(url+'/api/auth/me',token)
    request(url+'/api/orders/'+order+'/workflow-v2',token)
    body = request(url+'/')
    require('アップデート作業中'.encode() not in body, 'maintenance page forbidden')


def run():
    context()
    expected = json.loads(os.environ['ONLINE_EXPECTED_LIVE'])
    require(set(expected) == set(SERVICES), 'exact original production identities required')
    images = {key:resolve_image_digest(os.environ[key]) for key in
              ('ONLINE_BACKEND_IMAGE','ONLINE_FRONTEND_IMAGE','ONLINE_RECOVERY_IMAGE')}
    token = os.environ['VERIFICATION_TOKEN']
    order = os.environ['ORDER_ID']
    require(re.fullmatch(r'ORD[0-9a-fA-F]+',order), 'exact smoke order required')
    directory = Path(os.environ['RUNNER_TEMP'])
    record_path = directory/'online-release.json'
    require(not record_path.exists(), 'never overwrite original online release receipt')
    record = dict(source_sha=os.environ['GITHUB_SHA'],base_source_sha=BASE,
                  run=os.environ['GITHUB_RUN_ID'],services={},candidates=[],promoted=[],complete=False)
    def save():
        record_path.write_text(json.dumps(record,indent=2)+'\n')
        record_path.chmod(0o600)
    descriptors = {}
    for service in SERVICES:
        descriptor = cloud('run','services','describe',service,'--region='+REGION)
        attached = descriptor['spec']['template']['metadata'].get('annotations', {}).get('run.googleapis.com/cloudsql-instances','')
        require(PROJECT+':'+REGION+':orders-prod' in attached.split(',')
                and '-stg' not in attached, 'production attachment mismatch')
        current = identity(descriptor)
        require(current == expected[service], 'production changed since owner preflight: '+service)
        record['services'][service] = current
        descriptors[service] = descriptor
    require_runtime_act_as(descriptors)
    from run_prod_schema_adoption import production_database
    production_database()
    verify_web(descriptors['web-prod']['status']['url'],token,order)
    save()

    def candidate(service, image, role):
        current = cloud('run','services','describe',service,'--region='+REGION)
        # Original public traffic must still be intact when preparing a candidate.
        active = [v for v in current['status']['traffic'] if v.get('percent')]
        original = record.get('active_revisions', {}).get(service, record['services'][service]['revision'])
        require(len(active) == 1 and active[0].get('percent') == 100 and active[0].get('revisionName') == original,
                'serving traffic changed before candidate')
        tag = 'online-'+role+'-'+os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']
        name = service+'-r'+os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']+'-'+role
        # Remove only this run's zero-traffic tags from the prepared descriptor.
        prepared_source = copy.deepcopy(current)
        tags = [v.get('tag') for v in current['status']['traffic'] if v.get('tag')]
        require(all(v['tag'] in tags for v in record['candidates'] if v['service']==service),
                'recorded candidate tag changed')
        require(set(tags) <= {v['tag'] for v in record['candidates'] if v['service']==service},
                'unreviewed tagged route appeared')
        prepared_source['status']['traffic'] = [dict(revisionName=original,percent=100)]
        replacement_data = replacement(prepared_source,image,name,tag,os.environ['GITHUB_SHA'])
        # Preserve prior recovery routes while adding the next verified candidate.
        replacement_data['spec']['traffic'].extend(dict(revisionName=v['revision'],percent=0,tag=v['tag'])
            for v in record['candidates'] if v['service']==service)
        path = directory/(name+'.yaml')
        path.write_text(yaml.safe_dump(replacement_data,sort_keys=False)); path.chmod(0o600)
        entry = dict(service=service,revision=name,tag=tag,image=image,ready=False)
        record['candidates'].append(entry); save()
        cloud('run','services','replace',str(path),'--region='+REGION,'--quiet')
        revision = cloud('run','revisions','describe',name,'--region='+REGION)
        require(any(v.get('type')=='Ready' and v.get('status')=='True' for v in revision['status']['conditions']),
                'candidate is not ready')
        require(resolve_image_digest(revision_images(revision)[0]) == image, 'candidate image mismatch')
        require([c.get('env',[]) for c in revision['spec']['containers']] ==
                [c.get('env',[]) for c in current['spec']['template']['spec']['containers']],
                'candidate changed runtime environment')
        updated = cloud('run','services','describe',service,'--region='+REGION)
        route = next(v for v in updated['status']['traffic'] if v.get('tag')==tag)
        require(route['revisionName']==name and not route.get('percent'), 'candidate must receive no public traffic')
        entry.update(ready=True,url=route['url']); save()
        return entry

    def promote(entry):
        cloud('run','services','update-traffic',entry['service'],'--region='+REGION,
              '--to-revisions='+entry['revision']+'=100','--quiet')
        record['promoted'].append(entry['service']); save()
        record.setdefault('active_revisions', {})[entry['service']] = entry['revision']; save()

    recovery = None
    try:
        # A cold-started old-business bridge exists before any production DDL.
        recovery = candidate('worker-prod',images['ONLINE_RECOVERY_IMAGE'],'recovery38')
        verify_worker(recovery['url'],token,order)
        # The public old-business runtime must tolerate either side of the DDL,
        # including a cold restart between migration and the new app promotion.
        promote(recovery)
        description = 'online-'+os.environ['GITHUB_SHA']+'-'+os.environ['GITHUB_RUN_ID']
        cloud('sql','backups','create','--instance=orders-prod','--description='+description)
        backups = cloud('sql','backups','list','--instance=orders-prod')
        backups = [v for v in backups if v.get('description')==description and v.get('status')=='SUCCESSFUL']
        require(len(backups)==1, 'successful pre-migration backup required')
        record['backup_id']=backups[0]['id']; save()
        from run_prod_schema_adoption import run as migrate
        record['projection_parity'] = migrate(online_projection_only=True)
        record['schema']='0039'; save()
        # Prove cold-start rollback against the migrated DB before new app traffic.
        recovery = candidate('worker-prod',images['ONLINE_RECOVERY_IMAGE'],'recovery39')
        verify_worker(recovery['url'],token,order)
        backend = candidate('worker-prod',images['ONLINE_BACKEND_IMAGE'],'candidate')
        verify_worker(backend['url'],token,order)
        promote(backend)
        frontend = candidate('web-prod',images['ONLINE_FRONTEND_IMAGE'],'candidate')
        verify_web(frontend['url'],token,order)
        promote(frontend)
        verify_web(descriptors['web-prod']['status']['url'],token,order)
        record['complete']=True; save()
    except Exception:
        failures=[]
        for name in ('web-prod','worker-prod'):
            target = recovery['revision'] if name=='worker-prod' and recovery and recovery.get('ready') else record['services'][name]['revision']
            try:
                cloud('run','services','update-traffic',name,'--region='+REGION,
                      '--to-revisions='+target+'=100','--quiet')
            except Exception:
                failures.append(name)
        record['rollback_failures']=failures; save()
        raise
    finally:
        cleanup_failures=[]
        for name in ('web-prod','worker-prod'):
            try:
                current=cloud('run','services','describe',name,'--region='+REGION)
                live_tags={v.get('tag') for v in current['status'].get('traffic',[])}
                tags=[v['tag'] for v in record['candidates'] if v['service']==name and v['tag'] in live_tags]
                if tags:
                    cloud('run','services','update-traffic',name,'--region='+REGION,
                          '--remove-tags='+','.join(tags),'--quiet')
            except Exception:
                cleanup_failures.append(name)
        record['tag_cleanup_failures']=cleanup_failures; save()
        require(not cleanup_failures, 'candidate tag cleanup failed')
    print('Online production release verified; no maintenance display or quantity mutation')


if __name__ == '__main__':
    run()
