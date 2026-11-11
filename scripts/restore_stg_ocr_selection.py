"""One explicitly approved incident recovery through the authenticated public API."""
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

ORDER = 'ORD37344b72'
AUDIT = 'AUDd87f48731479454692f4d907f2256326'
CONFIRMATION = 'RESTORE_STG_ORD37344B72_'+AUDIT
BASE = 'https://worker-stg-avlnzjjrca-dt.a.run.app'
EXPECTED = {'selected_ocr_result_id':'OEVe5e04e6111de',
    'saved_sheet_id':'ODS5466389f7ab048d2',
    'confirmed_snapshot_id':'OCS17b4bbc59f1a45aa','state':'confirmed'}


def require_context(environment):
    required = {'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':'mmorinag127/sawa-hospital-system',
        'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REF':'refs/heads/develop',
        'GITHUB_ACTOR':'mmorinag127','GITHUB_TRIGGERING_ACTOR':'mmorinag127',
        'PROJECT_ID':'sawahospitalsystem','WORKER_URL':BASE,'WORKER_SERVICE':'worker-stg',
        'RESTORE_CONFIRMATION':CONFIRMATION}
    if any(environment.get(key)!=value for key,value in required.items()):
        raise ValueError('exact_approved_staging_recovery_context_required')
    if not environment.get('STG_ID_TOKEN'):
        raise ValueError('existing_staging_actions_identity_required')


def restore(request):
    path='/orders/'+ORDER+'/workflow-v2'
    before=request('GET',path)
    if before.get('order_id')!=ORDER:
        raise ValueError('approved_order_identity_mismatch')
    if all(before.get(key)==value for key,value in EXPECTED.items()):
        return {'order_id':ORDER,'state':'already_restored','revision':before['revision']}
    if (before.get('order_id')!=ORDER or before.get('revision')!=2
            or (before.get('ocr_reset') or {}).get('audit_id')!=AUDIT
            or (before.get('ocr_reset') or {}).get('retained_sheet_id')!=EXPECTED['saved_sheet_id']
            or any(before.get(key) for key in ('selected_ocr_result_id','saved_sheet_id','confirmed_snapshot_id'))):
        raise ValueError('approved_reset_is_no_longer_current')
    after=request('POST',path+'/ocr-reset/restore',revision=2)
    if not all(after.get(key)==value for key,value in EXPECTED.items()) or after.get('revision')!=3:
        raise ValueError('restored_references_do_not_match_audit')
    read=request('GET',path)
    if not all(read.get(key)==value for key,value in EXPECTED.items()) or read.get('revision')!=3:
        raise ValueError('restored_selection_readback_mismatch')
    request('GET',path+'/inspection')
    request('GET',path+'/sheet')
    return {'order_id':ORDER,'reset_audit_id':AUDIT,'revision':read['revision'],
        'restored':EXPECTED,'inspection':'HTTP 200','sheet':'HTTP 200'}


def main():
    require_context(os.environ)
    token=os.environ['STG_ID_TOKEN']
    def request(method,path,revision=None):
        headers={'Authorization':'Bearer '+token,'Accept':'application/json'}
        if revision is not None:
            headers['If-Match']=str(revision)
        with urlopen(Request(BASE+path,method=method,headers=headers),timeout=60) as response:
            return json.load(response)
    result=restore(request)
    (Path(os.environ['RUNNER_TEMP'])/'stg-selection-restore.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


if __name__=='__main__':
    main()
