"""Build an immutable trial bundle from a server-enforced read-only snapshot."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import zipfile

from psycopg2 import sql

from .hospital_source import INITIALIZATION, MAX_BYTES, SUPPORT_TABLES
from .school_source import value


def digest(body):
    return hashlib.sha256(body).hexdigest()


def copy_support(cursor, baseline, destination, facilities, months):
    shutil.copyfile(baseline, destination)
    with sqlite3.connect(destination) as local:
        cursor.execute('SELECT version_num FROM alembic_version ORDER BY version_num')
        if cursor.fetchall() != local.execute('SELECT version_num FROM alembic_version ORDER BY version_num').fetchall():
            raise ValueError('source_and_trial_schema_version_differ')
        local.execute('PRAGMA foreign_keys=OFF')
        tables = [name for name, in local.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            local.execute('DELETE FROM '+sql.Identifier(table).as_string(cursor))
        counts = {}
        for table in sorted(SUPPORT_TABLES):
            columns = [row[1] for row in local.execute('PRAGMA table_info('+sql.Identifier(table).as_string(cursor)+')')]
            if not columns:
                raise ValueError('trial_support_table_missing:'+table)
            cursor.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (table,))
            types = dict(cursor.fetchall())
            if set(columns) != set(types):
                raise ValueError('source_support_columns_differ:'+table)
            where, parameters = sql.SQL(''), []
            if table == 'facilities':
                where, parameters = sql.SQL(' WHERE id=ANY(%s)'), [facilities]
            elif table in ('facility_areas','facility_configs','facility_template_versions','menu_facility_overrides'):
                where, parameters = sql.SQL(' WHERE facility_id=ANY(%s)'), [facilities]
            elif table == 'menu_rules':
                where, parameters = sql.SQL(' WHERE facility_id IS NULL OR facility_id=ANY(%s)'), [facilities]
            elif table == 'monthly_menus':
                where, parameters = sql.SQL(' WHERE month_start=ANY(%s::date[])'), [months]
            elif table in ('monthly_menu_items','monthly_menu_entries'):
                where, parameters = sql.SQL(' WHERE monthly_menu_id IN (SELECT id FROM monthly_menus WHERE month_start=ANY(%s::date[]))'), [months]
            projection = sql.SQL(',').join(sql.Identifier(name)+
                (sql.SQL('::text') if types[name] in ('json','jsonb') else sql.SQL('')) for name in columns)
            cursor.execute(sql.SQL('SELECT {} FROM {}').format(projection,sql.Identifier(table))+where, parameters)
            names = ','.join('"'+name+'"' for name in columns)
            insert = 'INSERT INTO "'+table+'" ('+names+') VALUES ('+','.join('?' for _ in columns)+')'
            count = 0
            while rows := cursor.fetchmany(200):
                local.executemany(insert,[tuple(value(item) for item in row) for row in rows])
                count += len(rows)
            counts[table] = count
        if local.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('source_support_foreign_key_mismatch')
        local.commit()
    return counts


def build(connection, baseline, master, sources, assets, output):
    """Caller supplies independently captured document/template receipts and bytes.

    The live connection is never used to update state. Receipts must have been
    selected from the same read-only transaction by the caller.
    """
    if connection.autocommit:
        raise ValueError('read_only_snapshot_transaction_required')
    with connection.cursor() as cursor:
        cursor.execute('SHOW transaction_read_only')
        if cursor.fetchone()[0] != 'on':
            raise ValueError('source_transaction_not_read_only')
        cursor.execute('SHOW transaction_isolation')
        if cursor.fetchone()[0] not in ('repeatable read','serializable'):
            raise ValueError('source_snapshot_isolation_required')
        identifiers = [source['source_order_id'] for source in sources]
        if not identifiers or len(identifiers) != len(set(identifiers)):
            raise ValueError('unique_original_orders_required')
        cursor.execute('SELECT id,current_document_id,facility_code,week_code,template_version_id FROM orders WHERE id=ANY(%s)',(identifiers,))
        current = {row[0]:row[1:] for row in cursor.fetchall()}
        if set(current) != set(identifiers):
            raise ValueError('source_order_missing')
        for source in sources:
            actual = current[source['source_order_id']]
            if actual != tuple(source[key] for key in ('source_document_id','facility_id','week','source_template_version_id')):
                raise ValueError('source_order_receipt_changed')
            cursor.execute('SELECT facility_code,week_code,storage_uri FROM order_documents WHERE id=%s',
                (source['source_document_id'],))
            document = cursor.fetchone()
            if not document or document != (source['facility_id'],source['week'],source['source_uri']):
                raise ValueError('source_document_receipt_changed')
            cursor.execute('SELECT content_sha256 FROM uploaded_pdfs WHERE current_document_id=%s AND current_order_id=%s',
                (source['source_document_id'],source['source_order_id']))
            if cursor.fetchall() != [(source['sha256'],)]:
                raise ValueError('source_original_digest_not_authoritative')
            cursor.execute('SELECT facility_id,validation_json FROM facility_template_versions WHERE id=%s',
                (source['source_template_version_id'],))
            template = cursor.fetchone()
            if (not template or template[0] != source['facility_id']
                    or (template[1] or {}).get('canonical_lineage',{}).get('authored_version_id') != source['authored_version_id']):
                raise ValueError('source_template_lineage_changed')
        with TemporaryDirectory(prefix='hospital-export-',dir=Path(output).parent) as temporary:
            root = Path(temporary)
            files, pending, captured_assets = {}, [], {}
            def save(name, body):
                if sum(len(item) for item in files.values())+len(body)>MAX_BYTES:
                    raise ValueError('source_bundle_too_large')
                path = root/name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(body)
                files[name] = body
            for source in sources:
                record = {key:item for key,item in source.items() if key!='content'}
                body = source['content']
                if digest(body)!=record['sha256']:
                    raise ValueError('original_pdf_digest_mismatch')
                record['path'] = 'originals/'+record['source_order_id']+'.pdf'
                save(record['path'],body)
                pending.append(record)
            for uri, asset in assets.items():
                body = asset['content']
                if digest(body)!=asset['sha256']:
                    raise ValueError('source_asset_digest_mismatch')
                name = 'assets/'+asset['sha256']
                save(name,body)
                captured_assets[uri] = {key:item for key,item in asset.items() if key!='content'}|{'path':name}
            private_master = json.loads(master)
            # Keep authored facility versions intact, but never import saved
            # production order bindings into a newly registered FAX trial.
            excluded = ('initial_saved_imports','initial_saved_configurations')
            for key in excluded:
                private_master.pop(key,None)
            save('facility-master.json',json.dumps(private_master,ensure_ascii=False).encode())
            counts = copy_support(cursor,baseline,root/'database.sqlite3',
                sorted({source['facility_id'] for source in sources}),
                sorted({source['week'][:7]+'-01' for source in sources}))
            files['database.sqlite3']=(root/'database.sqlite3').read_bytes()
            manifest = {'purpose':'sawa-agent-isolated-trial','initialization':INITIALIZATION,
                'data':'registered_production_order_snapshot','pending_sources':pending,
                'source_assets':captured_assets,'files':{name:digest(body) for name,body in files.items()},
                'authority':{'source':'sawahospitalsystem:asia-northeast2:orders-prod/orders',
                    'read_only':True,'order_ids':identifiers,'tables':counts,
                    'source_master_sha256':digest(master),'excluded_master_keys':list(excluded),
                    'captured_at':datetime.now(timezone.utc).isoformat()}}
            target = Path(output)
            if target.exists():
                raise ValueError('refuse_existing_bundle_overwrite')
            with target.open('xb') as stream, zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                for name,body in sorted(files.items()):
                    archive.writestr(name,body)
                archive.writestr('trial-manifest.json',json.dumps(manifest,ensure_ascii=False))
            target.chmod(0o600)
            return {'sha256':digest(target.read_bytes()),'order_ids':identifiers,'tables':counts}
