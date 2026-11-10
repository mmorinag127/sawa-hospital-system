"""Read original orders into a private bundle; never deploy or mutate the source."""
import argparse
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import time
from tempfile import TemporaryDirectory

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts')]
from run_prod_schema_adoption import production_database
from experiments.agent_assistant.export_hospital_source import build, digest


def cloud_json(*arguments):
    return json.loads(subprocess.check_output(['gcloud',*arguments,'--format=json'],stderr=subprocess.DEVNULL))


def cloud_bytes(uri):
    if not uri.startswith('gs://') or any(char in uri for char in ('\n','\r')):
        raise ValueError('explicit_gcs_original_required')
    return subprocess.check_output(['gcloud','storage','cat',uri],stderr=subprocess.DEVNULL)


def source_assets(item, environment, result):
    if isinstance(item, dict):
        for child in item.values():
            source_assets(child,environment,result)
    elif isinstance(item, list):
        for child in item:
            source_assets(child,environment,result)
    elif isinstance(item, str) and (item.startswith('gs://') or '${' in item):
        uri = item
        for key, val in environment.items():
            uri = uri.replace('${'+key+'}',val)
        if '${' in uri:
            raise ValueError('source_asset_binding_unresolved')
        if item in result:
            return
        metadata = cloud_json('storage','objects','describe',uri)
        generation = str(metadata['generation'])
        body = cloud_bytes(uri+'#'+generation)
        result[item] = {'sha256':digest(body),'generation':generation,'source_uri':uri,'content':body}


def capture(args):
    if len(args.order)!=len(set(args.order)):
        raise ValueError('duplicate_source_order')
    config = production_database()
    service = cloud_json('run','services','describe','worker-prod','--project=sawahospitalsystem','--region=asia-northeast2')
    environment = {entry['name']:entry['value'] for entry in service['spec']['template']['spec']['containers'][0]['env'] if 'value' in entry}
    master = cloud_bytes(environment['FACILITY_MASTER_URI'])
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0))
        port = probe.getsockname()[1]
    proxy = subprocess.Popen([str(args.proxy),'--address=127.0.0.1','--port='+str(port),
        'sawahospitalsystem:asia-northeast2:orders-prod'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=1):
                    break
            except OSError:
                time.sleep(.5)
        with psycopg2.connect(host='127.0.0.1',port=port,user=config.db_user,password=config.db_password,
                dbname=config.db_name,connect_timeout=20) as connection:
            connection.set_session(readonly=True,isolation_level='REPEATABLE READ')
            sources, assets = [], {}
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout='30s'")
                for identity in args.order:
                    cursor.execute('SELECT o.current_document_id,o.facility_code,o.week_code,o.template_version_id,d.storage_uri,d.received_at,u.content_sha256,t.validation_json,t.config_json FROM orders o JOIN order_documents d ON d.id=o.current_document_id JOIN uploaded_pdfs u ON u.current_document_id=d.id AND u.current_order_id=o.id JOIN facility_template_versions t ON t.id=o.template_version_id WHERE o.id=%s',(identity,))
                    rows = cursor.fetchall()
                    if len(rows)!=1:
                        raise ValueError('unique_original_receipt_required:'+identity)
                    document, facility, week, template, uri, received, sha, validation, facility_config = rows[0]
                    cursor.execute('SELECT metrics FROM ocr_jobs WHERE order_id=%s AND order_document_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',(identity,document))
                    job = cursor.fetchone()
                    if not job or type((job[0] or {}).get('fax_rotation_degrees')) is not int:
                        raise ValueError('explicit_original_orientation_required:'+identity)
                    rotation = job[0]['fax_rotation_degrees']
                    if rotation not in (0,180):
                        raise ValueError('unsupported_original_orientation')
                    body = cloud_bytes(uri)
                    if digest(body)!=sha:
                        raise ValueError('original_pdf_digest_changed:'+identity)
                    sources.append({'source_order_id':identity,'source_document_id':document,
                        'source_template_version_id':template,'authored_version_id':validation['canonical_lineage']['authored_version_id'],
                        'facility_id':facility,'week':week,'source_uri':uri,'sha256':sha,'content':body,
                        'received_at':received.isoformat(),'service_date':week.split('@',1)[1].split('~',1)[0],
                        'message_id':'agent-copy:'+identity+':'+sha,'fax_rotation_degrees':rotation})
                    source_assets(facility_config,environment,assets)
            return build(connection,args.baseline,master,sources,assets,args.output)
    finally:
        proxy.terminate()
        proxy.wait(timeout=10)


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--order',action='append',required=True)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--proxy',type=Path,default=ROOT.parent.parent/'cloud-sql-proxy')
    options = parser.parse_args()
    options.output.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='hospital-copy-schema-',dir=options.output.parent) as temporary:
        baseline = Path(temporary)/'database.sqlite3'
        shutil.copyfile(options.baseline,baseline)
        environment = {key:os.environ[key] for key in ('PATH','LANG') if key in os.environ}
        environment.update(ALEMBIC_DB_URI='sqlite:///'+str(baseline.resolve()),PYTHONPATH=str(ROOT/'backend'))
        with (Path(temporary)/'migration.log').open('wb') as log:
            migrated = subprocess.run([sys.executable,'-m','alembic','-c',str(ROOT/'backend/alembic.ini'),'upgrade','head'],
                cwd=ROOT/'backend',env=environment,stdout=log,stderr=subprocess.STDOUT)
        if migrated.returncode:
            raise ValueError('private_copy_schema_preparation_failed')
        options.baseline = baseline
        print(json.dumps(capture(options)))
