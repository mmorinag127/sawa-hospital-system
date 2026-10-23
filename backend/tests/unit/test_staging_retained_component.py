"""Retained school data and delivery identity survive Hospital/Shift refresh."""
from copy import deepcopy
import pytest
from src import staging_data as data

OLD='replay-original'
NEW='replay-new'

@pytest.fixture
def retained(monkeypatch):
    monkeypatch.setenv('K_SERVICE','worker-stg')
    monkeypatch.setenv('APP_ENV','stg')
    monkeypatch.setenv('SAWA_STG_REPLAY_MANIFEST_URI',f'gs://fixture-stg-raw/stg-replay/{OLD}/manifest.json')
    monkeypatch.setenv('SAWA_STG_REPLAY_MANIFEST_SHA256','a'*64)
    old={'schema':1,'id':OLD,'snapshot_at':'2026-10-22T00:00:00Z','components':{k:{'database':'sawa_replay_old','settings':{},'objects':{}} for k in data.RUNTIME_SETTING_KEYS}}
    parent={'id':OLD,'uri':f'gs://fixture-stg-raw/stg-replay/{OLD}/manifest.json','sha256':'a'*64,'snapshot_at':old['snapshot_at'],'runtime_settings_provenance':'legacy_stg_runtime'}
    new={'schema':1,'id':NEW,'snapshot_at':'2026-10-23T00:00:00Z','runtime_settings_provenance':'production_environment','components':{k:{'database':'sawa_replay_new','settings':{},'objects':{},'runtime_settings':{x:None for x in v}} for k,v in data.RUNTIME_SETTING_KEYS.items()},'retained_components':{'school-lunch':parent}}
    new['components']['order']['producer_runtime_settings']={k:None for k in data.RUNTIME_PRODUCER_KEYS}
    new['components']['school-lunch']=deepcopy(old['components']['school-lunch'])
    monkeypatch.setattr(data,'legacy_manifest',lambda:old)
    monkeypatch.setattr(data,'_read_manifest',lambda uri,digest: old if digest=='a'*64 else new)
    entry={'uri':f'gs://fixture-stg-raw/stg-replay/{NEW}/manifest.json','sha256':'b'*64,'snapshot_at':new['snapshot_at']}
    return old,new,parent,entry

def test_school_provenance_is_derived_from_verified_parent(retained):
    old,new,parent,entry=retained
    value=data._runtime_manifest(entry)
    assert value['components']['school-lunch']==old['components']['school-lunch']
    assert value['_component_runtime_provenance']=={'order':'production_environment','shift':'production_environment','school-lunch':'legacy_stg_runtime'}

def test_altered_school_database_cannot_be_published_as_retained(retained):
    old,new,parent,entry=retained
    new['components']['school-lunch']['database']='sawa_replay_replaced'
    with pytest.raises(data.DatasetUnavailable,match='retained_component_changed:school-lunch'):
        data._runtime_manifest(entry)

def test_forged_serialized_provenance_cannot_override_validation(retained):
    old,new,parent,entry=retained
    new['_component_runtime_provenance']={'order':'legacy_stg_runtime'}
    new['components']['order']['runtime_settings']={}
    with pytest.raises(data.DatasetUnavailable,match='runtime_settings_incomplete:order'):
        data._runtime_manifest(entry)

def test_retained_parent_cycle_is_rejected(retained):
    old,new,parent,entry=retained
    new['retained_components']['school-lunch']={'id':NEW,**entry}
    with pytest.raises(data.DatasetUnavailable,match='retained_snapshot_cycle'):
        data._runtime_manifest(entry)

def test_school_business_namespace_and_delivery_keep_original_identity(retained,monkeypatch,tmp_path):
    old,new,parent,entry=retained
    value=data._runtime_manifest(entry)
    monkeypatch.setattr(data,'manifest_for',lambda identity:value)
    monkeypatch.setattr(data,'manifest',lambda:value)
    with data.dataset_context(NEW):
        assert data.current_dataset()==NEW
        assert data.current_dataset(feature='school-lunch')==OLD
        assert data.current_dataset(feature='order')==NEW
        assert data.local_directory(tmp_path,feature='school-lunch')==tmp_path/'stg-replay'/OLD
        assert data.write_object('fixture-stg-raw','result.pdf',feature='school-lunch')[1]==f'stg-replay/{OLD}/result.pdf'
        assert data.delivery_headers(feature='school-lunch')['X-Sawa-Dataset']==OLD
