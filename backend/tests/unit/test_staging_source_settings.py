import base64
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api import staging_source_settings as source

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('K_SERVICE','worker-prod')
    monkeypatch.setenv('K_REVISION','worker-prod-fixture')
    monkeypatch.setattr(source.id_token,'verify_oauth2_token',lambda *args:{'email':source.COORDINATOR,'email_verified':True})
    app=FastAPI();app.include_router(source.router)
    return TestClient(app)

@pytest.mark.parametrize('header',[None,'Basic synthetic','Bearer invalid'])
def test_other_or_invalid_identity_cannot_read_any_source_file(client,monkeypatch,header):
    monkeypatch.setattr(source.id_token,'verify_oauth2_token',lambda *args:(_ for _ in ()).throw(ValueError()))
    monkeypatch.setattr(source,'_file',lambda *args:pytest.fail('unauthorized file read'))
    result=client.get('/internal/staging-source-settings',params={'snapshot':'00000001-00000002-1'},headers={'Authorization':header} if header else {})
    assert result.status_code==403

@pytest.mark.parametrize('claims',[{'email':'other@fixture.iam.gserviceaccount.com','email_verified':True},{'email':source.COORDINATOR,'email_verified':False}])
def test_only_exact_verified_coordinator_is_allowed(client,monkeypatch,claims):
    monkeypatch.setattr(source.id_token,'verify_oauth2_token',lambda *args:claims)
    assert client.get('/internal/staging-source-settings?snapshot=00000001-00000002-1',headers={'Authorization':'Bearer synthetic'}).status_code==403

@pytest.mark.parametrize('query',['snapshot=bad','snapshot=00000001-00000002-1&path=/etc/passwd','snapshot=00000001-00000002-0'])
def test_arbitrary_paths_or_invalid_snapshots_rejected_before_file_or_db_read(client,monkeypatch,query):
    monkeypatch.setattr(source,'_file',lambda *args:pytest.fail('invalid file read'))
    assert client.get('/internal/staging-source-settings?'+query,headers={'Authorization':'Bearer synthetic'}).status_code==422


def test_export_is_forbidden_outside_exact_prod_worker(client,monkeypatch):
    monkeypatch.setenv('K_SERVICE','worker-stg')
    assert client.get('/internal/staging-source-settings?snapshot=00000001-00000002-1',headers={'Authorization':'Bearer synthetic'}).status_code==403


def test_file_export_keeps_raw_bytes_and_digest(tmp_path):
    import hashlib
    payload=b'{"fixture": "source bytes", "value": 2}\n';path=tmp_path/'setting.json';path.write_bytes(payload)
    raw,value=source._file(path)
    exported=source._document(raw,path)
    assert raw==payload and value=={'fixture':'source bytes','value':2}
    assert base64.b64decode(exported['content_base64'])==payload
    assert exported['sha256']==hashlib.sha256(payload).hexdigest()



@pytest.mark.parametrize('current_name',['source name','changed concurrently'])
def test_startup_uses_readonly_checks_and_blocks_raced_facility_names(monkeypatch,tmp_path,current_name):
    from src.services import staging_source_boot_readonly as boot
    path=tmp_path/'master.json';path.write_text('{"facilities":[{"facility_id":"synthetic","facility_name":"source name"}]}')
    monkeypatch.setattr(boot.config_service,'FACILITY_MASTER_PATH',path)
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execution_options(self,**options):
            assert options=={'isolation_level':'REPEATABLE READ','postgresql_readonly':True};return self
        def execute(self,statement):
            assert str(statement).startswith('SELECT')
            return SimpleNamespace(all=lambda:[('synthetic',current_name)])
        def rollback(self):pass
    class Inspector:
        def get_table_names(self):return ['menu_masters','menu_facility_overrides','monthly_menu_items','monthly_menu_entries','facility_template_versions']
        def get_columns(self,table):return [{'name':n} for n in {'condiments','menu_master_id','facility_id','master_resolution_mode','bag_max_qty','bag_max_unit','facility_override','config_json','valid_from','valid_to'}]
        def get_indexes(self,table):return [{'name':n} for n in {'uq_monthly_menu_items_scope_identity','ix_facility_template_versions_valid_from','ix_facility_template_versions_valid_to'}]
    monkeypatch.setattr(boot,'inspect',lambda connection:Inspector())
    monkeypatch.setattr(boot,'engine',SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),connect=lambda:Connection()))
    if current_name=='source name':boot.require_initialized_runtime()
    else:
        with pytest.raises(RuntimeError,match='automatic name synchronization is forbidden'):boot.require_initialized_runtime()


@pytest.mark.skipif(not __import__('os').environ.get('SOURCE_EXPORT_NATIVE_DATABASE_URL'),reason='requires owned Actions PostgreSQL fixture')
def test_native_export_uses_exact_source_snapshot_and_never_rewrites_current_rows(client,monkeypatch,tmp_path):
    import json,os
    from sqlalchemy import create_engine,text,select
    from src.models.facility import Facility,FacilityArea,FacilityConfig
    database=create_engine(os.environ['SOURCE_EXPORT_NATIVE_DATABASE_URL'])
    monkeypatch.setattr(source,'engine',database)
    for model in (Facility,FacilityArea,FacilityConfig):model.__table__.create(database,checkfirst=True)
    with database.begin() as connection:
        connection.execute(Facility.__table__.insert(),{'id':'synthetic-export','name':'snapshot name'})
        connection.execute(FacilityConfig.__table__.insert(),{'facility_id':'synthetic-export','config_json':{'fax_template_id':'synthetic-only','operator_value':'unchanged'}})
    facility_path=tmp_path/'facility.json';facility_path.write_text('{"schema_version":"fixture","facilities":[]}')
    ingest_path=tmp_path/'ingest.json';ingest_path.write_text('{"fixture":"raw bytes"}\n')
    monkeypatch.setattr(source.config_service,'FACILITY_MASTER_PATH',facility_path)
    monkeypatch.setattr(source.config_service,'INGEST_POLICY_PATH',ingest_path)
    monkeypatch.delenv('FACILITY_MASTER_URI',raising=False);monkeypatch.delenv('INGEST_POLICY_URI',raising=False)
    monkeypatch.setattr(source,'_state_uri',lambda:'gs://sawahospitalsystem-prod-raw/ocr/pipeline_state.json')
    with database.connect().execution_options(isolation_level='REPEATABLE READ',postgresql_readonly=True) as original:
        snapshot=original.execute(text('SELECT pg_export_snapshot()')).scalar_one()
        with database.begin() as other:
            other.execute(Facility.__table__.update().where(Facility.id=='synthetic-export').values(name='concurrent current name'))
        response=client.get('/internal/staging-source-settings',params={'snapshot':snapshot},headers={'Authorization':'Bearer synthetic'})
        assert response.status_code==200,response.text
        payload=response.json();assert payload['database_snapshot']==snapshot
        master=json.loads(base64.b64decode(payload['settings']['FACILITY_MASTER_URI']['content_base64']))
        assert master['facilities'][0]['facility_name']=='snapshot name'
        assert master['facilities'][0]['operator_value']=='unchanged'
        assert base64.b64decode(payload['settings']['INGEST_POLICY_URI']['content_base64'])==ingest_path.read_bytes()
        original.rollback()
    with database.connect() as connection:
        assert connection.execute(select(Facility.name).where(Facility.id=='synthetic-export')).scalar_one()=='concurrent current name'
    database.dispose()


def _rollout_script():
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('export_rollout',Path(__file__).resolve().parents[3]/'scripts/prod_source_export_preflight.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_rollout_requires_exact_ready_digest_and_exact_serving_revision(monkeypatch):
    rollout=_rollout_script()
    monkeypatch.setattr(rollout,'require_actions',lambda:None)
    monkeypatch.setenv('EXACT_REVISION','worker-prod-normal')
    monkeypatch.setenv('EXACT_IMAGE','image@sha256:normal')
    monkeypatch.setenv('REQUIRE_SERVING','true')
    monkeypatch.setattr(rollout,'revision_image',lambda name:'image@sha256:normal')
    monkeypatch.setattr(rollout,'live_service',lambda:{'status':{'traffic':[{'revisionName':'worker-prod-candidate','percent':100}]}})
    with pytest.raises(RuntimeError,match='exact normal revision'):rollout.exact_revision()
    monkeypatch.setattr(rollout,'live_service',lambda:{'status':{'traffic':[{'revisionName':'worker-prod-normal','percent':100}]}})
    rollout.exact_revision()
    monkeypatch.setattr(rollout,'revision_image',lambda name:'image@sha256:candidate')
    with pytest.raises(RuntimeError,match='built digest'):rollout.exact_revision()


def test_rollout_rollback_preserves_unrelated_revision(monkeypatch):
    rollout=_rollout_script();calls=[]
    monkeypatch.setattr(rollout,'require_actions',lambda:None)
    monkeypatch.setattr(rollout,'revision_image',lambda name:rollout.IMAGE)
    monkeypatch.setattr(rollout.subprocess,'run',lambda *a,**k:calls.append(a))
    monkeypatch.setattr(rollout,'live_service',lambda:{'status':{'traffic':[{'revisionName':'external-new-revision','percent':100}]}})
    with pytest.raises(RuntimeError,match='Unrelated'):rollout.restore_original()
    assert calls==[]
    monkeypatch.setattr(rollout,'live_service',lambda:{'status':{'traffic':[{'revisionName':rollout.REPAIR_REVISION,'percent':100}]}})
    rollout.restore_original()
    assert '--to-revisions='+rollout.ORIGINAL_REVISION+'=100' in calls[0][0]
