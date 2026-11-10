"""Bundle boundaries use synthetic bytes; this is not real OCR acceptance."""
import hashlib
from io import BytesIO
import json
import sqlite3
import zipfile

import pytest

from experiments.agent_assistant import hospital_source as source
from experiments.agent_assistant.store import Conflict, Store


def bundle(tmp_path,monkeypatch,*,extra_row=False):
    monkeypatch.setenv('OCR_PIPELINE_BUCKET','synthetic-trial-bucket')
    database=tmp_path/'db.sqlite3'
    with sqlite3.connect(database) as db:
        db.executescript('CREATE TABLE alembic_version(version_num TEXT); INSERT INTO alembic_version VALUES ("test"); CREATE TABLE orders(id TEXT);')
        if extra_row:
            db.execute('INSERT INTO orders VALUES ("forbidden")')
    files={'database.sqlite3':database.read_bytes(),'facility-master.json':b'{}','originals/ORDabcdef.pdf':b'synthetic PDF only'}
    sha=lambda body:hashlib.sha256(body).hexdigest()
    manifest={'purpose':'sawa-agent-isolated-trial','initialization':source.INITIALIZATION,
        'authority':{'read_only':True,'source':'sawahospitalsystem:asia-northeast2:orders-prod/orders',
            'order_ids':['ORDabcdef'],'captured_at':'2026-11-11T00:00:00Z'},
        'pending_sources':[{'source_order_id':'ORDabcdef','source_document_id':'document',
            'source_template_version_id':'template','authored_version_id':'authored','facility_id':'facility',
            'week':'2026-11@2026-11-15~2026-11-21','path':'originals/ORDabcdef.pdf',
            'sha256':sha(files['originals/ORDabcdef.pdf']),'fax_rotation_degrees':180}],
        'files':{name:sha(body) for name,body in files.items()}}
    stream=BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        for name,body in files.items():
            archive.writestr(name,body)
        archive.writestr('trial-manifest.json',json.dumps(manifest))
    body=stream.getvalue()
    value={'sha256':sha(body),'uri':'gs://synthetic-trial-bucket/agent-trial-sources/'+sha(body)+'.zip'}
    return body,value,files


def test_bundle_keeps_original_bytes_and_orientation(tmp_path,monkeypatch):
    body,value,files=bundle(tmp_path,monkeypatch)
    target=tmp_path/'private'
    manifest=source.unpack(body,target,value)
    assert (target/'originals/ORDabcdef.pdf').read_bytes()==files['originals/ORDabcdef.pdf']
    assert manifest['pending_sources'][0]['fax_rotation_degrees']==180
    assert source.inspect(value,tmp_path,fetch=lambda _:body)['orders'][0]['order_id']=='ORDabcdef'


def test_original_history_cannot_enter_trial_bundle(tmp_path,monkeypatch):
    body,value,_=bundle(tmp_path,monkeypatch,extra_row=True)
    with pytest.raises(Conflict,match='注文・履歴・認証'):
        source.unpack(body,tmp_path/'private',value)


@pytest.mark.parametrize('damage',['digest','bucket','path','existing'])
def test_invalid_bundle_has_no_fallback(tmp_path,monkeypatch,damage):
    body,value,_=bundle(tmp_path,monkeypatch)
    target=tmp_path/'private'
    if damage=='digest':
        body+=b'changed'
    elif damage=='bucket':
        value['uri']=value['uri'].replace('synthetic-trial-bucket','other-bucket')
    elif damage=='path':
        stream=BytesIO(body)
        with zipfile.ZipFile(stream,'a') as archive:
            archive.writestr('../outside',b'no')
        body=stream.getvalue()
        value['sha256']=hashlib.sha256(body).hexdigest()
        value['uri']='gs://synthetic-trial-bucket/agent-trial-sources/'+value['sha256']+'.zip'
    else:
        target.mkdir()
        (target/'existing').write_text('keep')
    with pytest.raises(Conflict):
        source.unpack(body,target,value)
    assert not (tmp_path/'outside').exists()
    if damage=='existing':
        assert (target/'existing').read_text()=='keep'


def test_registered_source_is_owner_scoped_and_persisted(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3')
    descriptor={'uri':'fixed','sha256':'a'*64}
    store.hospital_source('one',descriptor)
    assert store.hospital_source('two') is None
    assert Store(tmp_path/'ledger.sqlite3').hospital_source('one')==descriptor
