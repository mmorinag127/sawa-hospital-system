"""Read the registered staging school database into an owned request copy."""
from datetime import date, datetime, time, timezone
from contextlib import closing
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, unquote, urlsplit

from .store import Conflict

INSTANCE = 'sawahospitalsystem:asia-northeast2:school-lunch-stg'


def connection():
    import psycopg2
    parsed = urlsplit(os.environ.get('SAWA_TRIAL_SCHOOL_DATABASE_URL', ''))
    query = parse_qs(parsed.query, strict_parsing=True)
    if (parsed.scheme not in ('postgresql', 'postgresql+psycopg2')
            or parsed.hostname or parsed.port or parsed.fragment
            or unquote(parsed.username or '') != 'school_lunch_app'
            or parsed.path != '/school_lunch'
            or query != {'host': ['/cloudsql/' + INSTANCE]}
            or not parsed.password):
        raise Conflict('登録学校の正規stgデータ接続を確認できません')
    return psycopg2.connect(host=query['host'][0], dbname='school_lunch',
        user='school_lunch_app', password=unquote(parsed.password), connect_timeout=15)


def value(item):
    if isinstance(item, memoryview):
        return bytes(item)
    if isinstance(item, datetime):
        if item.tzinfo is not None:
            item = item.astimezone(timezone.utc).replace(tzinfo=None)
        return item.isoformat(sep=' ')
    if isinstance(item, date):
        return item.isoformat()
    if isinstance(item, time):
        return item.isoformat()
    if isinstance(item, Decimal):
        # Native SQLite Numeric readers use binary floats. Refuse any source
        # quantity that would change at its recorded decimal scale.
        if not item.is_finite():
            raise Conflict('登録数量が有限値ではありません')
        scale = max(0, -item.as_tuple().exponent)
        if Decimal(format(float(item), f'.{scale}f')) != item:
            raise Conflict('登録数量の精度を依頼コピーで保持できません')
        return str(item)
    return item


def copy_registered(directory, *, connect=connection):
    """The source transaction is server-enforced read-only; never restore it."""
    directory = Path(directory)
    target = directory / 'database.sqlite3'
    try:
        with closing(connect()) as source, sqlite3.connect(target) as local:
            source.set_session(isolation_level='REPEATABLE READ', readonly=True)
            with source.cursor() as cursor:
                cursor.execute('SHOW transaction_read_only')
                if cursor.fetchone()[0] != 'on':
                    raise Conflict('登録学校の取得は読み取り専用で行う必要があります')
                tables = [row[0] for row in local.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                if not tables or any(not re.fullmatch('[a-z][a-z0-9_]*', name) for name in tables):
                    raise Conflict('学校給食コピーの保存形式を確認できません')
                cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")
                if tables != [row[0] for row in cursor.fetchall()]:
                    raise Conflict('登録学校と業務APIの保存形式が異なります')
                cursor.execute('SELECT version_num FROM alembic_version')
                if cursor.fetchall() != local.execute('SELECT version_num FROM alembic_version').fetchall():
                    raise Conflict('登録学校と業務APIの保存版が異なります')
                local.execute('PRAGMA foreign_keys=OFF')
                for table in tables:
                    local.execute(f'DELETE FROM "{table}"')
                counts = {}
                for table in tables:
                    columns = [row[1] for row in local.execute(f'PRAGMA table_info("{table}")')]
                    cursor.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (table,))
                    types = dict(cursor.fetchall())
                    if set(columns) != set(types):
                        raise Conflict('登録学校の項目と業務APIが一致しません：' + table)
                    projection = ','.join(f'"{name}"' + ('::text' if types[name] in ('json', 'jsonb') else '') for name in columns)
                    cursor.execute(f'SELECT {projection} FROM "{table}"')
                    names = ','.join(f'"{name}"' for name in columns)
                    placeholders = ','.join('?' for _ in columns)
                    insert = f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})'
                    count = 0
                    while batch := cursor.fetchmany(200):
                        local.executemany(insert,
                            [tuple(value(item) for item in row) for row in batch])
                        count += len(batch)
                    counts[table] = count
                if local.execute('PRAGMA foreign_key_check').fetchone():
                    raise Conflict('登録学校データの参照を確認できません')
                local.commit()
        proof = {'source': INSTANCE + '/school_lunch', 'read_only': True,
                 'tables': counts, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
        (directory / 'registered-source.json').write_text(json.dumps(proof, ensure_ascii=False))
        marker = json.loads((directory / 'trial-manifest.json').read_text())
        marker['data'] = 'registered_staging_snapshot'
        (directory / 'trial-manifest.json').write_text(json.dumps(marker, ensure_ascii=False))
    except Conflict:
        raise
    except Exception:
        # Credentials and raw PostgreSQL errors must not reach the UI/model.
        raise Conflict('登録学校の正規データを取得できません。試験データでは続行しません') from None
