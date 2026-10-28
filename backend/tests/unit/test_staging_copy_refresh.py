"""Refresh lifecycle/ownership proofs; synthetic data never represents live production."""
from copy import deepcopy
from datetime import timedelta
import json
import uuid
import struct
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import staging_data as data
from src.services import staging_copy_refresh_service as service
from src.services import staging_copy_refresh_engine as engine

OLD = "replay-original"
RID = "a" * 32
DIGEST = "b" * 64


@pytest.mark.parametrize('rows', [[], ['{}'], ['{"raw":"Ⅲ\\n\\t\\\\","empty":""}', '{"raw":null}']])
def test_copy_stream_keeps_exact_json_bytes(rows, monkeypatch, tmp_path):
    payload = b'PGCOPY\n\xff\r\n\x00' + b'\x00' * 8
    for row in rows:
        raw = row.encode('utf-8')
        payload += struct.pack('!hi', 1, len(raw)) + raw
    payload += struct.pack('!h', -1)
    monkeypatch.setattr(engine.sql.Composed, 'as_string', lambda self, connection: 'COPY fixture')
    cursor = SimpleNamespace(copy_expert=lambda statement, output: output.write(payload))
    connection = SimpleNamespace(cursor=lambda: nullcontext(cursor), encoding='UTF8')
    assert list(engine._copy_raw_rows(connection, 'public', 'fixture', tmp_path / 'stream.copy')) == rows


@pytest.mark.parametrize('payload', [
    b'', b'not-a-copy-stream',
    b'PGCOPY\n\xff\r\n\x00' + struct.pack('!ii', 1, 0),
    b'PGCOPY\n\xff\r\n\x00' + b'\x00' * 8 + struct.pack('!h', 2),
    b'PGCOPY\n\xff\r\n\x00' + b'\x00' * 8 + struct.pack('!hi', 1, -1),
    b'PGCOPY\n\xff\r\n\x00' + b'\x00' * 8 + struct.pack('!hi', 1, 10) + b'{}',
    b'PGCOPY\n\xff\r\n\x00' + b'\x00' * 8 + struct.pack('!h', -1) + b'extra',
])
def test_invalid_copy_stream_blocks_and_removes_temporary_stream(payload, monkeypatch, tmp_path):
    monkeypatch.setattr(engine.sql.Composed, 'as_string', lambda self, connection: 'COPY fixture')
    cursor = SimpleNamespace(copy_expert=lambda statement, output: output.write(payload))
    connection = SimpleNamespace(cursor=lambda: nullcontext(cursor), encoding='UTF8')
    target = tmp_path / 'stream.copy'
    with pytest.raises(service.RefreshError, match='copy_stream_invalid'):
        list(engine._copy_raw_rows(connection, 'public', 'fixture', target))
    assert not target.exists()


@pytest.fixture
def control(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "worker-stg")
    monkeypatch.setenv("APP_ENV", "stg")
    monkeypatch.setenv("SAWA_STG_REPLAY_MANIFEST_URI", "gs://fixture-stg-raw/stg-replay/replay-original/manifest.json")
    initial = {"schema": 1, "active": OLD, "snapshots": {OLD: {
        "uri": "gs://fixture-stg-raw/stg-replay/" + OLD + "/manifest.json", "sha256": DIGEST,
        "snapshot_at": "2026-10-23T00:00:00Z"}}, "refresh": None}
    state = {"value": initial, "generation": 1, "dispatched": []}
    def read():
        return deepcopy(state["value"]), state["generation"]
    def write(value, generation):
        from google.api_core.exceptions import PreconditionFailed
        if generation != state["generation"]:
            raise PreconditionFailed("synthetic CAS conflict")
        data.validate_registry(value)
        state["value"] = deepcopy(value)
        state["generation"] += 1
    monkeypatch.setattr(data, "read_registry", read)
    monkeypatch.setattr(service, "write_registry", write)
    monkeypatch.setattr(service, "source_configuration", lambda: ({"components": {}}, DIGEST))
    monkeypatch.setattr(service, "dispatch", lambda job: state["dispatched"].append(deepcopy(job)))
    return state


def publication(job):
    return {"id": job["dataset"], "uri": "gs://fixture-stg-raw/stg-replay/" + job["dataset"] + "/manifest.json",
            "sha256": "c" * 64, "snapshot_at": "2026-10-23T01:00:00Z"}


def test_same_request_is_idempotent_and_other_request_cannot_replace_active_copy(control):
    first = service.request_refresh(RID)
    second = service.request_refresh(RID)
    assert first == second
    assert control["value"]["active"] == OLD
    assert len(control["value"]["snapshots"]) == 1
    with pytest.raises(service.RefreshError, match="already_running"):
        service.request_refresh("d" * 32)
    assert control["value"]["refresh"]["id"] == RID


def test_expired_execution_fails_without_publishing_and_allows_new_ui_refresh(control):
    service.request_refresh(RID)
    claimed = service.claim(RID, 1)
    control['value']['refresh']['lease_until'] = (service.now() - timedelta(seconds=1)).isoformat()
    result = service.status()
    assert result['job']['phase'] == 'failed'
    assert result['job']['error'] == 'refresh_execution_timed_out'
    assert control['value']['active'] == OLD
    assert list(control['value']['snapshots']) == [OLD]
    with pytest.raises(service.RefreshError, match='execution_fenced'):
        service.checkpoint(claimed, 'succeeded', published=publication(claimed))
    assert service.claim(RID, 1) is None
    assert service.request_refresh('d' * 32)['phase'] == 'queued'


def test_status_preserves_queued_and_live_execution(control):
    service.request_refresh(RID)
    generation = control['generation']
    assert service.status()['job']['phase'] == 'queued'
    assert control['generation'] == generation
    service.claim(RID, 1)
    generation = control['generation']
    assert service.status()['job']['phase'] == 'starting'
    assert control['generation'] == generation


def test_dispatch_failure_retains_queued_intent_for_same_id_retry(control, monkeypatch):
    monkeypatch.setattr(service, "dispatch", lambda job: (_ for _ in ()).throw(RuntimeError("synthetic network failure")))
    with pytest.raises(RuntimeError):
        service.request_refresh(RID)
    assert control["value"]["refresh"]["phase"] == "queued"
    assert control["value"]["active"] == OLD
    monkeypatch.setattr(service, "dispatch", lambda job: None)
    assert service.request_refresh(RID)["id"] == RID


def test_only_verified_completion_changes_active_and_retains_previous_manifest(control, monkeypatch):
    service.request_refresh(RID)
    def build(job, config, progress):
        for phase in ("copying_database", "copying_files_and_settings", "verifying_manifest"):
            assert control["value"]["active"] == OLD
            progress(phase)
        return publication(job)
    monkeypatch.setattr(engine, "build_snapshot", build)
    assert service.execute(RID, 1)["succeeded"] is True
    registry = control["value"]
    assert registry["active"] == "replay-" + RID
    assert registry["snapshots"][OLD]["sha256"] == DIGEST
    assert len(registry["snapshots"]) == 2
    assert registry["refresh"]["phase"] == "succeeded"
    assert service.execute(RID, 1) == {"terminal": True}


@pytest.mark.parametrize("phase", ["copying_database", "copying_files_and_settings", "verifying_manifest"])
def test_failure_at_each_stage_keeps_old_copy_and_never_records_exception_secrets(control, monkeypatch, phase):
    service.request_refresh(RID)
    def build(job, config, progress):
        progress(phase, "order")
        raise RuntimeError("synthetic-password-do-not-persist")
    monkeypatch.setattr(engine, "build_snapshot", build)
    assert service.execute(RID, 1)["succeeded"] is False
    assert control["value"]["active"] == OLD
    assert len(control["value"]["snapshots"]) == 1
    assert "synthetic-password" not in json.dumps(control["value"])
    assert control["value"]["refresh"]["phase"] == "failed"


def test_late_worker_cannot_publish_or_change_successor(control):
    service.request_refresh(RID)
    old_claim = service.claim(RID, 1)
    with pytest.raises(service.RefreshError, match="in_progress"):
        service.claim(RID, 1)
    control["value"]["refresh"]["lease_until"] = (service.now() - timedelta(seconds=1)).isoformat()
    new_claim = service.claim(RID, 1)
    assert new_claim["attempt"] != old_claim["attempt"]
    with pytest.raises(service.RefreshError, match="fenced"):
        service.checkpoint(old_claim, "succeeded", published=publication(old_claim))
    assert control["value"]["active"] == OLD
    service.checkpoint(new_claim, "succeeded", published=publication(new_claim))


def test_old_delivery_generation_cannot_execute_after_tombstone_renewal(control):
    service.request_refresh(RID)
    control["value"]["refresh"]["delivery_generation"] = 2
    with pytest.raises(service.RefreshError, match="not_current"):
        service.claim(RID, 1)
    assert service.claim(RID, 2)["phase"] == "starting"


def test_configuration_change_after_submission_fails_without_publication(control, monkeypatch):
    service.request_refresh(RID)
    monkeypatch.setattr(service, "source_configuration", lambda: ({"components": {}}, "d" * 64))
    assert service.execute(RID, 1)["succeeded"] is False
    assert control["value"]["refresh"]["error"] == "refresh_source_configuration_changed"
    assert control["value"]["active"] == OLD


@pytest.mark.parametrize("uri,role", [
    ("sqlite:///test.sqlite", "prod"),
    ("postgresql://u:p@/business?host=/cloudsql/p:r:orders-stg", "prod"),
    ("postgresql://u:p@/business?host=/cloudsql/p:r:orders-prod", "stg"),
    ("postgresql://u:p@localhost/business", "prod"),
    ("postgresql://u:p@/sawa_replay_old?host=/cloudsql/p:r:orders-prod", "prod"),
])
def test_invalid_or_test_or_copy_source_is_never_accepted(uri, role):
    with pytest.raises(service.RefreshError):
        engine._database(uri, role)


def test_production_runtime_cannot_enable_status_submit_or_worker(control, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "worker-prod")
    for operation in (service.status, lambda: service.request_refresh(RID), lambda: service.execute(RID, 1)):
        with pytest.raises(service.RefreshError, match="disabled"):
            operation()


def test_captured_request_keeps_old_manifest_after_another_generation_is_activated(control, monkeypatch):
    manifests = {identity: {"id": identity, "components": {feature: {"runtime_settings": {key: None for key in keys}, **({"producer_runtime_settings": {key: None for key in data.RUNTIME_PRODUCER_KEYS}} if feature == "order" else {})} for feature, keys in data.RUNTIME_SETTING_KEYS.items()}, "runtime_settings_provenance": "production_environment"} for identity in (OLD, "replay-new")}
    monkeypatch.setattr(data, "_read_manifest", lambda uri, digest: manifests["replay-new" if "replay-new/" in uri else OLD])
    with data.dataset_context(OLD):
        control["value"]["active"] = "replay-new"
        control["value"]["snapshots"]["replay-new"] = {"uri": "gs://fixture-stg-raw/stg-replay/replay-new/manifest.json", "sha256": "e" * 64, "snapshot_at": "2026-10-23T02:00:00Z"}
        assert data.manifest()["id"] == OLD
        with data.dataset_context("test"):
            assert data.manifest()["id"] == "replay-new"
        assert data.manifest()["id"] == OLD
    with data.dataset_context(OLD):
        assert data.manifest()["id"] == OLD


def test_full_reference_walk_preserves_embedded_json_and_local_identity():
    value = {"meta": json.dumps({"uri": "gs://fixture-prod/docs/original.pdf?generation=12"}),
             "files": ["/app/uploads/original.pdf", "file:///app/config.json"], "quantity": "--", "memo": "document.pdf"}
    assert set(engine._references(value)) == {"gs://fixture-prod/docs/original.pdf?generation=12", "/app/uploads/original.pdf", "file:///app/config.json"}


class MemoryStorage:
    """Generation/CAS-faithful storage fixture, containing synthetic bytes only."""
    def __init__(self):
        self.values = {}
        self.generation = 10
        self.corrupt_copies = False
        self.fail_replica = None
    def bucket(self, name):
        owner = self
        class Bucket:
            def __init__(self): self.name = name
            def blob(self, key, generation=None): return MemoryBlob(owner, self, key, generation)
        return Bucket()
    def put(self, bucket, key, content):
        self.generation += 1
        self.values[(bucket,key)] = (self.generation,content)


class MemoryBlob:
    def __init__(self, owner, bucket, name, generation):
        self.owner,self.bucket,self.name,self.generation=owner,bucket,name,generation
    def reload(self, **kwargs):
        from google.api_core.exceptions import NotFound
        value=self.owner.values.get((self.bucket.name,self.name))
        if value is None or (self.generation is not None and self.generation != value[0]): raise NotFound('synthetic missing generation')
        self.generation=value[0]
    def download_as_bytes(self, if_generation_match=None, **kwargs):
        from google.api_core.exceptions import PreconditionFailed,NotFound
        value=self.owner.values.get((self.bucket.name,self.name))
        if value is None: raise NotFound('synthetic missing object')
        if if_generation_match is not None and if_generation_match!=value[0]: raise PreconditionFailed('synthetic stale generation')
        if self.owner.corrupt_copies and '/objects/' in self.name:return b'synthetic corrupt copy'
        return value[1]
    def upload_from_string(self, content, if_generation_match, **kwargs):
        from google.api_core.exceptions import PreconditionFailed
        if self.owner.fail_replica == self.bucket.name and '/manifests/' in self.name:raise RuntimeError('synthetic replica failure')
        value=self.owner.values.get((self.bucket.name,self.name))
        if (value[0] if value else 0)!=if_generation_match:raise PreconditionFailed('synthetic CAS conflict')
        if isinstance(content,str):content=content.encode()
        self.owner.put(self.bucket.name,self.name,content)
        self.generation=self.owner.values[(self.bucket.name,self.name)][0]

    def upload_from_filename(self, filename, if_generation_match, **kwargs):
        self.upload_from_string(Path(filename).read_bytes(), if_generation_match, **kwargs)

    def download_to_file(self, output, if_generation_match=None, **kwargs):
        output.write(self.download_as_bytes(if_generation_match=if_generation_match, **kwargs))


def test_disk_receipts_keep_exact_manifest_values_and_batch_writes(tmp_path):
    mapping = engine._SnapshotMap(tmp_path)
    expected = {'gs://original/1': {'uri': 'gs://copied/1', 'generation': '1'},
                'gs://original/2': {'uri': 'gs://copied/2', 'generation': '2'}}
    with mapping.collecting():
        for key, value in expected.items():
            mapping[key] = value
            assert key in mapping and mapping[key] == value
    assert len(mapping) == len(expected)
    assert dict(mapping.items()) == expected
    encoded = b''.join(engine._manifest_chunks({'objects': mapping, 'missing': engine._SnapshotKeys(mapping)}))
    assert json.loads(encoded) == {'objects': expected, 'missing': sorted(expected)}
    with pytest.raises(RuntimeError):
        with mapping.collecting():
            mapping['rollback'] = {'uri': 'never-published'}
            raise RuntimeError('owned interrupted builder')
    assert 'rollback' not in mapping


@pytest.fixture
def storage(monkeypatch):
    from google.cloud import storage as sdk
    store=MemoryStorage()
    monkeypatch.setattr(sdk,'Client',lambda:store)
    return store


def test_pdf_settings_and_nested_refs_are_generation_pinned_copied_and_hash_verified(storage):
    from hashlib import sha256
    storage.put('fixture-prod','original.pdf',b'synthetic PDF bytes; not an operator input')
    storage.put('fixture-prod','master.json',json.dumps({'original':'gs://fixture-prod/original.pdf?generation=11'}).encode())
    copier=engine.ObjectCopier(storage,storage.bucket('fixture-stg-raw'),'stg-replay/replay-case/order', ['fixture-prod'],{})
    master=copier.copy('gs://fixture-prod/master.json')
    pdf=copier.objects['gs://fixture-prod/original.pdf?generation=11']
    assert pdf['sha256']==sha256(b'synthetic PDF bytes; not an operator input').hexdigest()
    assert copier.objects[master]['uri']==master
    assert len([name for bucket,name in storage.values if '/objects/' in name])==2
    copier.copy('gs://fixture-prod/master.json')
    assert len([name for bucket,name in storage.values if '/objects/' in name])==2
    assert storage.values[('fixture-prod','original.pdf')][0]==11


@pytest.mark.parametrize('reference',['/app/uploads/missing.pdf','gs://unconfigured-prod/file.pdf'])
def test_missing_or_unconfigured_original_never_uses_test_or_old_copy(storage,reference):
    copier=engine.ObjectCopier(storage,storage.bucket('fixture-stg-raw'),'stg-replay/replay-case/order',['fixture-prod'],{})
    with pytest.raises(Exception):copier.copy(reference)
    assert not copier.objects


def test_mismatched_copied_pdf_bytes_stop_before_any_manifest_can_publish(storage):
    storage.put('fixture-prod','original.pdf',b'synthetic PDF')
    storage.corrupt_copies=True
    copier=engine.ObjectCopier(storage,storage.bucket('fixture-stg-raw'),'stg-replay/replay-case/order',['fixture-prod'],{})
    with pytest.raises(service.RefreshError,match='verification_failed'):copier.copy('gs://fixture-prod/original.pdf')
    assert not copier.objects


@pytest.fixture
def build_environment(control,storage,monkeypatch):
    import hashlib
    old={'schema':1,'id':OLD,'state':'ready','snapshot_at':'2026-10-23T00:00:00Z','components':{name:{'database':'sawa_replay_old','verified':True,'settings':{},'objects':{},'table_digests':{'public.probe':'b'*64}} for name in ('order','shift','school-lunch')}}
    raw=json.dumps(old,separators=(',',':')).encode();digest=hashlib.sha256(raw).hexdigest()
    control['value']['snapshots'][OLD]['sha256']=digest
    storage.put('fixture-stg-raw','stg-replay/'+OLD+'/manifest.json',raw)
    storage.put('fixture-prod','original.pdf',b'synthetic PDF')
    storage.put('fixture-prod','master.json',b'{"configured":true}')
    monkeypatch.setattr(data,'refresh_control_uri',lambda:'gs://fixture-stg-raw/stg-replay/control/')
    data._read_manifest.cache_clear()
    config={'schema':1,'components':{}}
    for name,bucket in [('order','fixture-stg-raw'),('shift','fixture-shift-stg'),('school-lunch','fixture-school-stg')]:
        config['components'][name]={'source_service':name+'-prod','destination_service':name+'-stg','source_buckets':['fixture-prod'],'destination_bucket':bucket,'settings_keys':['FACILITY_MASTER_URI','INGEST_POLICY_URI','OCR_PIPELINE_STATE_URI'] if name=='order' else ['GCS_BUCKET_NAME'] if name=='shift' else [],'expected_migration_heads':['head'],'local_objects':{},**({'producer_service':'producer-prod'} if name=='order' else {})}
    def runtime(name, settings_keys=()):
        feature=name.rsplit('-',1)[0];entry=config['components'][feature]
        role='prod' if name.endswith('-prod') else 'stg'
        env={key:('fixture-prod' if key=='GCS_BUCKET_NAME' else 'gs://fixture-prod/master.json') for key in entry['settings_keys']}
        env['SAWA_STG_REPLAY_MANIFEST_URI']='gs://'+entry['destination_bucket']+'/stg-replay/'+OLD+'/manifest.json'
        return 'postgresql://u:p@/business?host=/cloudsql/fixture:region:'+feature+'-'+role,env,'revision-pinned'
    monkeypatch.setattr(engine,'runtime_source',runtime)
    monkeypatch.setattr(engine,'runtime_environment',lambda *args: ({}, 'producer-revision-pinned'))
    targets=[]
    def clone(source,destination,target,directory,heads):
        targets.append((source['instance'],destination['database'],target))
        assert target.startswith('sawa_replay_') and target!='business'
        return {'public.quantities':{'rows':2,'sha256':'a'*64}},{'gs://fixture-prod/original.pdf'},{}
    monkeypatch.setattr(engine,'clone_database',clone)
    service.request_refresh(RID)
    job=service.claim(RID,1)
    return config,job,targets


def test_all_manifest_mirrors_verified_then_one_commit_updates_every_runtime(control,storage,build_environment,monkeypatch):
    config,job,targets=build_environment
    published=engine.build_snapshot(job,config,lambda *args:None)
    assert len(targets)==3
    assert control['value']['active']==OLD
    for bucket in ('fixture-shift-stg','fixture-school-stg'):
        assert not (bucket,'stg-replay/control/registry.json') in storage.values
        assert any(b==bucket and '/manifests/' in key for b,key in storage.values)
    service.checkpoint(job,'succeeded',published=published)
    assert control['value']['active']==job['dataset']
    for bucket in ('fixture-stg-raw','fixture-shift-stg','fixture-school-stg'):
        monkeypatch.setenv('SAWA_STG_REPLAY_MANIFEST_URI','gs://'+bucket+'/stg-replay/'+OLD+'/manifest.json')
        assert data.manifest()['id']==job['dataset']
    manifest=data._read_manifest(published['uri'],published['sha256'])
    assert set(manifest['components'])=={'order','shift','school-lunch'}
    assert all(c['source_revision']=='revision-pinned' for c in manifest['components'].values())


def test_replica_write_failure_never_commits_central_active_or_deletes_old_generation(control,storage,build_environment):
    config,job,_=build_environment
    storage.fail_replica='fixture-shift-stg'
    with pytest.raises(RuntimeError,match='replica failure'):
        engine.build_snapshot(job,config,lambda *args:None)
    assert control['value']['active']==OLD
    assert list(control['value']['snapshots'])==[OLD]
    assert ('fixture-stg-raw','stg-replay/'+OLD+'/manifest.json') in storage.values


@pytest.mark.parametrize('mutation',[
    'source-stg','destination-prod','source-bucket-stg','missing-component','unexpected-field','heads-string','settings-string','duplicate-setting',
])
def test_source_configuration_rejects_untrusted_or_incomplete_sources(storage,monkeypatch,mutation):
    from copy import deepcopy
    entry={'source_service':'projects/fixture/locations/region/services/source-prod','destination_service':'projects/fixture/locations/region/services/destination-stg','settings_keys':[],'expected_migration_heads':['head'],'source_buckets':['fixture-prod'],'local_objects':{},'destination_bucket':'fixture-stg-raw'}
    config={'schema':1,'components':{name:deepcopy(entry) for name in ('order','shift','school-lunch')}}
    for name,component in config['components'].items():component['settings_keys']=sorted(service.SETTING_KEYS[name])
    config['components']['order']['producer_service']='projects/fixture/locations/region/services/producer-prod'
    chosen=config['components']['order']
    if mutation=='source-stg':chosen['source_service']=chosen['source_service'].replace('-prod','-stg')
    elif mutation=='destination-prod':chosen['destination_service']=chosen['destination_service'].replace('-stg','-prod')
    elif mutation=='source-bucket-stg':chosen['source_buckets']=['fixture-stg-raw']
    elif mutation=='missing-component':del config['components']['school-lunch']
    elif mutation=='unexpected-field':chosen['password']='synthetic-never-accepted'
    elif mutation=='heads-string':chosen['expected_migration_heads']='head'
    elif mutation=='settings-string':chosen['settings_keys']='FACILITY_MASTER_URI'
    elif mutation=='duplicate-setting':chosen['settings_keys']=['FACILITY_MASTER_URI','FACILITY_MASTER_URI']
    storage.put('fixture-stg-raw','sources.json',json.dumps(config).encode())
    monkeypatch.setattr(service,'_blob',lambda name:storage.bucket('fixture-stg-raw').blob(name))
    # Use real validation, rather than the lifecycle fixture's configuration stub.
    with pytest.raises(service.RefreshError,match='configuration_invalid'):REAL_SOURCE_CONFIGURATION()


REAL_SOURCE_CONFIGURATION=service.source_configuration
REAL_DISPATCH=service.dispatch


def test_cloud_task_tombstone_renews_only_queued_generation_and_old_delivery_is_fenced(control,monkeypatch):
    from google.api_core.exceptions import AlreadyExists
    from google.cloud import tasks_v2
    from src.services import workflow_ocr_delivery_dispatch_service as dispatch_module
    service.request_refresh(RID)
    contracts=[]
    class Client:
        def create_task(self,request):
            contracts.append(request['task'])
            if len(contracts)==1:raise AlreadyExists('synthetic tombstone')
    monkeypatch.setattr(tasks_v2,'CloudTasksClient',Client)
    monkeypatch.setattr(dispatch_module,'required_config',lambda:{'PROJECT_ID':'fixture','LOCATION':'region','QUEUE':'workflow-stg','TARGET_URL':'https://worker-stg-fixture.a.run.app','SERVICE_ACCOUNT_EMAIL':'task@fixture.iam.gserviceaccount.com','AUDIENCE':'https://worker-stg-fixture.a.run.app'})
    monkeypatch.setattr(dispatch_module,'inspect_task',lambda client,contract:'missing')
    monkeypatch.setattr(service,'dispatch',REAL_DISPATCH)
    REAL_DISPATCH(control['value']['refresh'])
    assert len(contracts)==2
    assert contracts[0]['name'].endswith('-g1') and contracts[1]['name'].endswith('-g2')
    assert contracts[1]['http_request']['url'].endswith('/'+RID+'/2')
    with pytest.raises(service.RefreshError,match='not_current'):service.claim(RID,1)
    assert service.claim(RID,2)['phase']=='starting'


def test_original_gcs_absence_is_preserved_without_using_old_or_alternate_bytes(storage):
    copier=engine.ObjectCopier(storage,storage.bucket('fixture-stg-raw'),'stg-replay/replay-case/order',['fixture-prod'],{})
    assert copier.copy('gs://fixture-prod/original-already-missing.pdf') is None
    assert dict(copier.missing_at_source)=={'gs://fixture-prod/original-already-missing.pdf': True}
    assert not copier.objects
    with pytest.raises(service.RefreshError,match='required'):
        copier.copy('gs://fixture-prod/required-master-missing.json',required=True)


def test_local_saved_storage_receipt_is_kept_and_alias_bytes_must_match(storage):
    import hashlib
    content=b'synthetic local original PDF'
    reference='file:///app/uploads/original.pdf'
    receipt={'storage_version':{'mode':'local','device':3,'inode':9,'size':len(content),'mtime_ns':17},'pdf_sha256':hashlib.sha256(content).hexdigest()}
    original={'pdf_reference':reference,**receipt}
    receipts={};engine._collect_local_receipts({'source_contract':original},receipts)
    assert receipts=={reference:receipt}
    storage.put('fixture-prod','original.pdf',content)
    uri = 'gs://fixture-prod/original.pdf?generation=' + str(storage.values[('fixture-prod', 'original.pdf')][0])
    unpinned = engine.ObjectCopier(storage, storage.bucket('fixture-stg-raw'), 'stg-replay/replay-unpinned/order', ['fixture-prod'], {reference: 'gs://fixture-prod/original.pdf'}, receipts)
    with pytest.raises(service.RefreshError, match='local_source_generation_required'):
        unpinned.copy(reference)
    assert not unpinned.objects
    copier=engine.ObjectCopier(storage,storage.bucket('fixture-stg-raw'),'stg-replay/replay-case/order',['fixture-prod'],{reference:uri},receipts)
    copier.copy(reference)
    assert copier.objects[reference]['source_identity']==receipt['storage_version']
    wrong=engine.ObjectCopier(storage,storage.bucket('fixture-stg-raw'),'stg-replay/replay-neighbor/order',['fixture-prod'],{reference:uri}, {})
    with pytest.raises(service.RefreshError,match='proof_mismatch'):wrong.copy(reference)
    assert not wrong.objects


def test_queued_source_change_can_fail_cleanly_and_new_request_can_start(control, monkeypatch):
    service.request_refresh(RID)
    monkeypatch.setattr(service, 'source_configuration', lambda: ({'components': {}}, 'd' * 64))
    assert service.request_refresh(RID)['phase'] == 'failed'
    assert control['value']['active'] == OLD
    assert service.request_refresh('e' * 32)['phase'] == 'queued'


def test_valid_source_configuration_and_credentials_setting_rejected(storage, monkeypatch):
    entry = {'source_service': 'projects/fixture/locations/region/services/source-prod',
             'destination_service': 'projects/fixture/locations/region/services/destination-stg',
             'settings_keys': [], 'expected_migration_heads': ['head'], 'source_buckets': ['fixture-prod'],
             'local_objects': {}, 'destination_bucket': 'fixture-stg-raw'}
    config = {'schema': 1, 'components': {name: {**deepcopy(entry), 'settings_keys': sorted(keys)} for name, keys in service.SETTING_KEYS.items()}}
    config['components']['order']['producer_service']='projects/fixture/locations/region/services/producer-prod'
    monkeypatch.setattr(service, '_blob', lambda name: storage.bucket('fixture-stg-raw').blob(name))
    storage.put('fixture-stg-raw', 'sources.json', json.dumps(config).encode())
    assert REAL_SOURCE_CONFIGURATION()[0] == config
    config['components']['order']['settings_keys'].append('OPERATOR_PASSWORD')
    storage.put('fixture-stg-raw', 'sources.json', json.dumps(config).encode())
    with pytest.raises(service.RefreshError, match='configuration_invalid'):
        REAL_SOURCE_CONFIGURATION()


@pytest.mark.parametrize('traffic', [[], [{'revision': 'source-prod-a', 'percent': 50}, {'revision': 'source-prod-b', 'percent': 50}]])
def test_runtime_source_rejects_ambiguous_serving_revision(monkeypatch, traffic):
    class Reply:
        status_code = 200
        def json(self): return {'trafficStatuses': traffic}
    class Session:
        def get(self, *args, **kwargs): return Reply()
    monkeypatch.setattr(engine, '_cloud_session', Session)
    with pytest.raises(service.RefreshError, match='revision_ambiguous'):
        engine.runtime_source('projects/fixture/locations/region/services/source-prod')


@pytest.mark.parametrize("full_revision", [False, True])
def test_runtime_source_pins_revision_resolves_only_needed_secret_and_rejects_duplicate_db(monkeypatch, full_revision):
    import base64
    service_name = 'projects/fixture/locations/region/services/source-prod'
    revision = service_name + '/revisions/source-prod-00001'
    uri = 'postgresql+psycopg2://owner:synthetic@/source?host=/cloudsql/fixture:region:db-prod'
    environment = [{'name': 'DB_URI', 'valueSource': {'secretKeyRef': {'secret': 'database', 'version': '1'}}},
                   {'name': 'OPERATOR_PASSWORD', 'valueSource': {'secretKeyRef': {'secret': 'unrelated', 'version': 'latest'}}}]
    calls = []
    class Reply:
        status_code = 200
        def __init__(self, value): self.value = value
        def json(self): return self.value
    class Session:
        def get(self, url, **kwargs):
            calls.append(url)
            if url.endswith('/' + service_name): return Reply({'trafficStatuses': [{'revision': revision if full_revision else revision.rsplit('/', 1)[-1], 'percent': 100}]})
            if url.endswith('/' + revision): return Reply({'containers': [{'env': environment}]})
            assert url.endswith('/secrets/database/versions/1:access')
            return Reply({'payload': {'data': base64.b64encode(uri.encode()).decode()}})
    monkeypatch.setattr(engine, '_cloud_session', Session)
    actual, values, source_revision = engine.runtime_source(service_name)
    assert engine.make_url(actual) == engine.make_url(uri) and source_revision == revision and 'OPERATOR_PASSWORD' not in values
    assert len(calls) == 3
    environment.append({'name': 'DB_URI', 'value': uri})
    with pytest.raises(service.RefreshError, match='setting_duplicate'):
        engine.runtime_source(service_name)


def test_refresh_api_enforces_operator_dependency_production_guard_and_body_contract(control, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import staging_datasets as api
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    monkeypatch.setenv('AUTH_DISABLED', 'false')
    # Preserve the real operator dependency; uncredentialed callers cannot enqueue.
    assert client.post('/staging-datasets/refresh', json={'request_id': RID}).status_code in (401, 403)
    app.dependency_overrides[api.router.dependencies[0].dependency] = lambda: {'role': 'operator'}
    result = client.post('/staging-datasets/refresh', json={'request_id': RID})
    assert result.status_code == 202 and result.json()['id'] == RID
    assert client.post('/staging-datasets/refresh', json={'request_id': RID, 'database': 'test'}).status_code == 422
    monkeypatch.setattr(data, 'staging_runtime', lambda: False)
    assert client.get('/staging-datasets/refresh').status_code == 404
    assert client.post('/staging-datasets/refresh', json={'request_id': RID}).status_code == 404


REAL_READ_REGISTRY = data.read_registry


def test_registry_transport_missing_vs_denied_never_selects_test(storage, monkeypatch):
    from google.api_core.exceptions import Forbidden
    monkeypatch.setattr(data, 'refresh_control_uri', lambda: 'gs://fixture-stg-raw/stg-replay/control/')
    original = {'schema': 1, 'active': OLD, 'snapshots': {OLD: {'uri': 'gs://fixture-stg-raw/stg-replay/' + OLD + '/manifest.json', 'sha256': DIGEST, 'snapshot_at': 'original'}}, 'refresh': None}
    monkeypatch.setattr(data, 'initial_registry', lambda: deepcopy(original))
    assert REAL_READ_REGISTRY() == (original, 0)
    storage.put('fixture-stg-raw', 'stg-replay/control/registry.json', json.dumps(original).encode())
    actual, generation = REAL_READ_REGISTRY()
    assert actual == original and generation > 0
    def denied(*args, **kwargs): raise Forbidden('synthetic denial')
    monkeypatch.setattr(MemoryBlob, 'reload', denied)
    with pytest.raises(data.DatasetUnavailable, match='registry_unavailable'):
        REAL_READ_REGISTRY()
    with data.dataset_context('test'):
        assert data.current_dataset() == 'test'


@pytest.mark.parametrize('setting', ['https://unconfigured.invalid/master.json', 'unresolved-setting'])
def test_required_setting_must_resolve_to_verified_original_before_publication(control, storage, build_environment, monkeypatch, setting):
    config, job, _ = build_environment
    original = engine.runtime_source
    def runtime(name, settings_keys=()):
        uri, environment, revision = original(name, settings_keys)
        if name == 'order-prod': environment['FACILITY_MASTER_URI'] = setting
        return uri, environment, revision
    monkeypatch.setattr(engine, 'runtime_source', runtime)
    with pytest.raises(service.RefreshError, match='original_object_source_unresolved'):
        engine.build_snapshot(job, config, lambda *args: None)
    assert control['value']['active'] == OLD
    assert not any('/manifests/' in key for _, key in storage.values)



def test_runtime_settings_capture_backend_producer_school_and_exclude_secrets(control, storage, build_environment, monkeypatch):
    config, job, _ = build_environment
    original = engine.runtime_source
    def runtime(name, settings_keys=()):
        uri, environment, revision = original(name, settings_keys)
        environment.update({'OCR_TEMPLATE_MIN_CONFIDENCE': '0.7', 'GEMINI_OCR_MODEL': 'gemini-source', 'GEMINI_API_KEY': 'never-copy-synthetic-key',
                            'PROCESS_GEMINI_MODEL': 'gemini-school-source', 'IMPLEMENTATION_PRICE_OCR_DPI': '400'})
        return uri, environment, revision
    monkeypatch.setattr(engine, 'runtime_source', runtime)
    monkeypatch.setattr(engine, 'runtime_environment', lambda *args: ({'OCR_YOMITOKU_DPI': '500', 'OCR_TEMPLATE_MIN_CONFIDENCE': '0.8'}, 'producer-exact-revision'))
    published = engine.build_snapshot(job, config, lambda *args: None)
    value = data._read_manifest(published['uri'], published['sha256'])
    order = value['components']['order']
    assert order['runtime_settings']['GEMINI_OCR_MODEL'] == 'gemini-source'
    assert order['producer_runtime_settings']['OCR_YOMITOKU_DPI'] == '500'
    assert order['runtime_settings']['OPENAI_OCR_MODEL'] is None
    assert order['runtime_settings']['OCR_TEMPLATE_MIN_CONFIDENCE'] == '0.7'
    assert order['producer_runtime_settings']['OCR_TEMPLATE_MIN_CONFIDENCE'] == '0.8'
    assert order['producer_revision'] == 'producer-exact-revision'
    assert value['components']['school-lunch']['runtime_settings']['IMPLEMENTATION_PRICE_OCR_DPI'] == '400'
    serialized=b''.join(engine._manifest_chunks(value)).decode()
    assert 'GEMINI_API_KEY' not in serialized
    assert 'never-copy-synthetic-key' not in serialized


def test_business_endpoint_with_embedded_credentials_stops_before_copy_or_publication(control, storage, build_environment, monkeypatch):
    config, job, targets = build_environment
    original = engine.runtime_source
    def runtime(name, settings_keys=()):
        uri, environment, revision = original(name, settings_keys)
        if name == 'order-prod': environment['GEMINI_API_BASE'] = 'https://private:credential@example.invalid/v1?key=secret'
        return uri, environment, revision
    monkeypatch.setattr(engine, 'runtime_source', runtime)
    with pytest.raises(service.RefreshError, match='endpoint_contains_credentials'):
        engine.build_snapshot(job, config, lambda *args: None)
    assert not targets and control['value']['active'] == OLD



def test_business_secret_reference_is_never_resolved_or_published(monkeypatch):
    service_name = 'projects/fixture/locations/region/services/source-prod'
    revision = service_name + '/revisions/source-prod-00001'
    calls = []
    class Reply:
        status_code = 200
        def __init__(self, data): self.data = data
        def json(self): return self.data
    class Session:
        def get(self, url, **kwargs):
            calls.append(url)
            if url.endswith('/' + service_name): return Reply({'trafficStatuses': [{'revision': revision, 'percent': 100}]})
            assert url.endswith('/' + revision)
            return Reply({'containers': [{'env': [{'name': 'GEMINI_OCR_MODEL', 'valueSource': {'secretKeyRef': {'secret': 'never-access-this-secret', 'version': '1'}}}]}]})
    monkeypatch.setattr(engine, '_cloud_session', Session)
    with pytest.raises(service.RefreshError, match='business_setting_secret_reference_forbidden'):
        engine.runtime_environment(service_name, data.RUNTIME_BACKEND_KEYS)
    assert len(calls) == 2 and not any('secretmanager' in call for call in calls)



def test_local_original_requires_exact_receipt_for_every_feature_and_cannot_use_another_alias(storage):
    import hashlib
    reference = 'file:///production/templates/shipping.xlsx'
    content = b'authoritative-template-original'
    storage.put('fixture-prod', 'shipping.xlsx', content)
    generation = storage.values[('fixture-prod', 'shipping.xlsx')][0]
    source_uri = 'gs://fixture-prod/shipping.xlsx?generation=' + str(generation)
    identity = {'mode': 'local', 'device': 1, 'inode': 2, 'size': len(content), 'mtime_ns': 3}
    proof = {'uri': source_uri, 'source_identity': identity, 'sha256': hashlib.sha256(content).hexdigest()}
    for feature in ('order', 'shift', 'school-lunch'):
        prefix = 'stg-replay/replay-case/' + feature
        missing = engine.ObjectCopier(storage, storage.bucket('fixture-stg-raw'), prefix, ['fixture-prod'], {reference: source_uri})
        with pytest.raises(service.RefreshError, match='local_original_proof_mismatch'):
            missing.copy(reference)
        valid = engine.ObjectCopier(storage, storage.bucket('fixture-stg-raw'), prefix, ['fixture-prod'], {reference: proof})
        valid.copy(reference)
        assert valid.objects[reference]['source_identity'] == identity
    wrong = {**proof, 'sha256': 'a' * 64}
    copier = engine.ObjectCopier(storage, storage.bucket('fixture-stg-raw'), 'stg-replay/replay-wrong/order', ['fixture-prod'], {reference: wrong})
    with pytest.raises(service.RefreshError, match='local_original_proof_mismatch'):
        copier.copy(reference)



def test_alias_map_cannot_replace_a_gcs_original_with_another_file(storage):
    original = 'gs://fixture-prod/original.pdf'
    copier = engine.ObjectCopier(storage, storage.bucket('fixture-stg-raw'), 'stg-replay/replay-case/order', ['fixture-prod'], {original: 'gs://fixture-prod/another.pdf'})
    with pytest.raises(service.RefreshError, match='alias_original_not_local'):
        copier.copy(original)



def test_missing_pinned_generation_never_copies_the_latest_generation(storage):
    storage.put('fixture-prod', 'original.pdf', b'newer source bytes')
    reference = 'gs://fixture-prod/original.pdf?generation=1'
    copier = engine.ObjectCopier(storage, storage.bucket('fixture-stg-raw'), 'stg-replay/replay-case/order', ['fixture-prod'], {})
    assert copier.copy(reference) is None
    assert reference in copier.missing_at_source and not copier.objects
    assert not any('/objects/' in key for _, key in storage.values)



def test_source_configuration_changed_during_copy_cannot_publish(control, monkeypatch):
    service.request_refresh(RID)
    def build(job, config, progress):
        monkeypatch.setattr(service, 'source_configuration', lambda: ({'components': {}}, 'd' * 64))
        return publication(job)
    monkeypatch.setattr(engine, 'build_snapshot', build)
    assert service.execute(RID, 1)['succeeded'] is False
    assert control['value']['active'] == OLD
    assert control['value']['refresh']['error'] == 'refresh_source_configuration_changed'


def test_literal_empty_business_env_is_preserved_and_is_not_a_secret_reference(monkeypatch):
    service_name = 'projects/fixture/locations/region/services/source-prod'
    class Reply:
        status_code = 200
        def __init__(self, value): self.value = value
        def json(self): return self.value
    class Session:
        def get(self, url, **kwargs):
            if url.endswith('/' + service_name): return Reply({'trafficStatuses': [{'revision': 'source-prod-00001', 'percent': 100}]})
            assert url.endswith('/' + service_name + '/revisions/source-prod-00001')
            return Reply({'containers': [{'env': [{'name': 'GEMINI_OCR_MODEL'}]}]})
    monkeypatch.setattr(engine, '_cloud_session', Session)
    environment, _ = engine.runtime_environment(service_name, data.RUNTIME_BACKEND_KEYS)
    assert environment['GEMINI_OCR_MODEL'] == ''
    assert 'OPENAI_OCR_MODEL' not in environment


def test_partial_refresh_never_clones_or_copies_school_business_objects(control, storage, build_environment, monkeypatch):
    config, job, targets = build_environment
    parent = {**control['value']['snapshots'][OLD], 'id': OLD, 'runtime_settings_provenance': 'legacy_stg_runtime'}
    monkeypatch.setenv('SAWA_STG_REPLAY_MANIFEST_SHA256', parent['sha256'])
    config['components']['school-lunch'] = {'mode': 'retain', 'snapshot': parent, 'destination_bucket': 'fixture-school-stg'}
    initial = deepcopy(data._read_manifest(parent['uri'], parent['sha256'])['components']['school-lunch'])
    published = engine.build_snapshot(job, config, lambda *args: None)
    value = data._read_manifest(published['uri'], published['sha256'])
    assert len(targets) == 2
    assert {item[0] for item in targets} == {'fixture:region:order-prod', 'fixture:region:shift-prod'}
    assert value['components']['school-lunch'] == initial
    assert value['retained_components']['school-lunch'] == parent
    assert not any(bucket == 'fixture-school-stg' and '/objects/' in key for bucket, key in storage.values)
    assert control['value']['active'] == OLD
    service.checkpoint(job, 'succeeded', published=published)
    assert control['value']['active'] == job['dataset']
    assert control['value']['snapshots'][OLD]['sha256'] == parent['sha256']


def test_authored_template_variable_uses_only_explicit_production_bucket(storage, monkeypatch):
    monkeypatch.setenv('TEMPLATE_BUCKET', 'fixture-test-bucket')
    storage.put('fixture-prod', 'template.xlsx', b'original production template')
    reference = 'gs://${TEMPLATE_BUCKET}/template.xlsx'
    copier = engine.ObjectCopier(storage, storage.bucket('fixture-stg-raw'), 'stg-replay/replay-case/order',
        ['fixture-prod'], {}, source_environment={'TEMPLATE_BUCKET': 'fixture-prod'})
    copied = copier.copy(reference)
    assert copier.objects[reference]['uri'] == copied
    assert copier.objects['gs://fixture-prod/template.xlsx']['uri'] == copied
    blocked = engine.ObjectCopier(storage, storage.bucket('fixture-stg-raw'), 'stg-replay/replay-blocked/order', ['fixture-prod'], {})
    with pytest.raises(service.RefreshError, match='environment_unresolved'):
        blocked.copy(reference)
    with pytest.raises(service.RefreshError, match='environment_unresolved'):
        copier.copy('gs://${DB_PASSWORD}/template.xlsx')
