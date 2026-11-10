"""Pinned original-order bundles, copied only into request-owned hospital storage."""
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from urllib.parse import quote
import zipfile

import httpx

from .store import Conflict

INITIALIZATION = 'register-authoritative-pending-faxes-v1'
MAX_BYTES = 256 * 1024 * 1024
SUPPORT_TABLES = {'alembic_version', 'facilities', 'facility_areas', 'facility_configs',
    'facility_template_versions', 'menu_masters', 'menu_facility_overrides',
    'monthly_menus', 'monthly_menu_items', 'monthly_menu_entries', 'menu_rules',
    'base_menu_cycle_items'}


def descriptor(value):
    bucket = os.environ.get('OCR_PIPELINE_BUCKET', '').removeprefix('gs://').rstrip('/')
    if (not isinstance(value, dict) or set(value) != {'uri', 'sha256'}
            or not re.fullmatch(r'[a-z0-9][a-z0-9_.-]+', bucket)
            or not re.fullmatch(r'[0-9a-f]{64}', str(value.get('sha256', '')))
            or value.get('uri') != f"gs://{bucket}/agent-trial-sources/{value['sha256']}.zip"):
        raise Conflict('同じOCR保存先の内容固定済み原本セットを指定してください')
    return dict(value)


def download(value):
    value = descriptor(value)
    bucket, name = value['uri'][5:].split('/', 1)
    # Reuse this Cloud Run service's existing identity, without new credentials
    # or an operator-selectable URL. Tokens never enter the source manifest.
    try:
        with httpx.Client(timeout=60, trust_env=False, follow_redirects=False) as client:
            token = client.get('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
                headers={'Metadata-Flavor': 'Google'})
            token.raise_for_status()
            authorization = 'Bearer ' + token.json()['access_token']
            url = f'https://storage.googleapis.com/storage/v1/b/{quote(bucket, safe="")}/o/{quote(name, safe="")}?alt=media'
            with client.stream('GET', url, headers={'Authorization': authorization}) as response:
                response.raise_for_status()
                content = bytearray()
                for block in response.iter_bytes():
                    content.extend(block)
                    if len(content) > MAX_BYTES:
                        raise Conflict('原本セットが取得上限を超えています')
        return bytes(content)
    except Conflict:
        raise
    except Exception:
        raise Conflict('指定した原本セットを取得できません。検証FAXへ代用しません') from None


def unpack(content, target, value):
    value = descriptor(value)
    if len(content) > MAX_BYTES or hashlib.sha256(content).hexdigest() != value['sha256']:
        raise Conflict('原本セットのハッシュが一致しません')
    target = Path(target)
    if target.is_symlink() or (target.exists() and any(target.iterdir())):
        raise Conflict('原本セットは空の専用保存先にだけ展開できます')
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            members = archive.infolist()
            names = [item.filename for item in members]
            if (len(names) != len(set(names)) or len(names) > 2000
                    or sum(item.file_size for item in members) > MAX_BYTES
                    or any(item.is_dir() or item.flag_bits & 1 or (item.external_attr >> 16) & 0o170000 == 0o120000
                           or PurePosixPath(item.filename).is_absolute() or '\\' in item.filename
                           or any(part in ('', '.', '..') for part in item.filename.split('/')) for item in members)):
                raise Conflict('原本セットの保存パスを確認できません')
            manifest = json.loads(archive.read('trial-manifest.json'))
            files = manifest['files']
            if (manifest.get('purpose') != 'sawa-agent-isolated-trial'
                    or manifest.get('initialization') != INITIALIZATION
                    or not isinstance(files, dict) or set(names) != set(files) | {'trial-manifest.json'}
                    or not {'database.sqlite3', 'facility-master.json'}.issubset(files)):
                raise Conflict('実注文用の原本セットではありません')
            sources = manifest['pending_sources']
            authority = manifest['authority']
            if (not isinstance(sources, list) or not sources or authority.get('read_only') is not True
                    or authority.get('source') != 'sawahospitalsystem:asia-northeast2:orders-prod/orders'
                    or len({item['source_order_id'] for item in sources}) != len(sources)
                    or sorted(item['source_order_id'] for item in sources) != sorted(authority['order_ids'])):
                raise Conflict('実注文と原本の対応を確認できません')
            for item in sources:
                if (not re.fullmatch(r'ORD[0-9a-f]+', item['source_order_id'])
                        or not item.get('source_document_id') or not item.get('source_template_version_id')
                        or not item.get('authored_version_id') or not item.get('facility_id') or not item.get('week')
                        or not re.fullmatch(r'[0-9a-f]{64}', str(item.get('source_template_digest','')))
                        or files.get(item['path']) != item['sha256']
                        or type(item.get('fax_rotation_degrees')) is not int
                        or item['fax_rotation_degrees'] not in (0, 180)):
                    raise Conflict('原本・施設・週・向きの対応を確認できません')
            assets = manifest.get('source_assets', {})
            if not isinstance(assets, dict) or any(
                    not isinstance(uri, str) or not isinstance(asset, dict)
                    or not str(asset.get('path', '')).startswith('assets/')
                    or files.get(asset.get('path')) != asset.get('sha256')
                    for uri, asset in assets.items()):
                raise Conflict('施設帳票の原本対応を確認できません')
            for name in names:
                body = archive.read(name)
                if name != 'trial-manifest.json' and hashlib.sha256(body).hexdigest() != files[name]:
                    raise Conflict('原本セット内のファイルが変更されています')
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                path.write_bytes(body)
                path.chmod(0o600)
        with sqlite3.connect(f'file:{target / "database.sqlite3"}?mode=ro', uri=True) as database:
            database.execute('PRAGMA query_only=ON')
            if database.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise Conflict('専用コピーのDBを確認できません')
            for name, in database.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                if not re.fullmatch('[a-z][a-z0-9_]*', name):
                    raise Conflict('専用コピーの保存形式が不正です')
                if name not in SUPPORT_TABLES and database.execute(f'SELECT 1 FROM "{name}" LIMIT 1').fetchone():
                    raise Conflict('原本登録前のコピーに注文・履歴・認証データが混在しています')
            if database.execute('PRAGMA foreign_key_check').fetchone():
                raise Conflict('専用コピーの参照を確認できません')
        return manifest
    except Conflict:
        raise
    except Exception:
        raise Conflict('実注文の原本セットを検証できません。代替データでは続行しません') from None


def inspect(value, root, *, fetch=download):
    value = descriptor(value)
    with TemporaryDirectory(prefix='hospital-source-', dir=root) as directory:
        manifest = unpack(fetch(value), directory, value)
    return {**value, 'orders': [{'order_id':item['source_order_id'], 'facility_id':item['facility_id'],
        'week':item['week']} for item in manifest['pending_sources']], 'captured_at':manifest['authority']['captured_at']}


def copy_registered(target, baseline, source, *, fetch=download):
    target, baseline = Path(target), Path(baseline)
    value = descriptor({key:source[key] for key in ('uri', 'sha256')})
    if target.exists():
        raise Conflict('既存の依頼コピーへ原本セットを上書きしません')
    with TemporaryDirectory(prefix='.hospital-source-', dir=target.parent) as directory:
        root = Path(directory)
        unpack(fetch(value), root, value)
        with sqlite3.connect(f'file:{root / "database.sqlite3"}?mode=ro', uri=True) as actual, \
                sqlite3.connect(f'file:{baseline / "database.sqlite3"}?mode=ro', uri=True) as expected:
            if actual.execute('SELECT version_num FROM alembic_version').fetchall() != expected.execute('SELECT version_num FROM alembic_version').fetchall():
                raise Conflict('実注文コピーと業務APIのDB移行版が一致しません')
            schema = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            if actual.execute(schema).fetchall() != expected.execute(schema).fetchall():
                raise Conflict('実注文コピーと業務APIのDB構造が一致しません')
        shutil.copytree(root, target)
