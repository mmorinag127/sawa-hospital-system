"""Private disk-backed cleanup proofs; a reviewed digest fixes the exact row set."""
from contextlib import closing, contextmanager
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import shutil
import stat
import tempfile

from sqlalchemy import select


def row_json(value):
    def encode(item):
        if isinstance(item, (date, datetime)):
            return {'type': type(item).__name__, 'value': item.isoformat()}
        if isinstance(item, Decimal):
            return {'type': 'Decimal', 'value': str(item)}
        if isinstance(item, bytes):
            return {'type': 'bytes', 'value': item.hex()}
        raise TypeError('unsupported cleanup row value: ' + type(item).__name__)
    return json.dumps(dict(value), sort_keys=True, ensure_ascii=True, separators=(',', ':'), default=encode)


def row_key(model, row):
    return json.dumps([row[column.key] for column in model.__table__.primary_key], ensure_ascii=True, separators=(',', ':'))


def columns(model):
    return [column for column in model.__table__.columns if column.computed is None]


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def create(session, header, queries):
    root = Path(tempfile.mkdtemp(prefix='sawa-cleanup-plan-'))
    path = root / 'plan.sqlite'
    try:
        with closing(sqlite3.connect(path)) as manifest, manifest:
            os.chmod(path, 0o600)
            manifest.execute('CREATE TABLE metadata (value TEXT NOT NULL)')
            manifest.execute('INSERT INTO metadata VALUES (?)', (json.dumps(header, sort_keys=True),))
            manifest.execute('CREATE TABLE rows (table_name TEXT, owner_id TEXT, key_json TEXT, row_json TEXT, PRIMARY KEY(table_name,key_json))')
            manifest.execute('CREATE INDEX rows_owner ON rows(table_name,owner_id,key_json)')
            for model, statement in queries:
                rows = session.execute(statement.execution_options(yield_per=200)).mappings()
                try:
                    for row in rows:
                        owner = row['id'] if model.__tablename__ == 'orders' else row['order_id']
                        manifest.execute('INSERT INTO rows VALUES (?,?,?,?)', (
                            model.__tablename__, owner, row_key(model, row), row_json(row)))
                finally:
                    rows.close()
        return {'path': str(path), 'sha256': digest_file(path), 'schema': 1}
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


@contextmanager
def open_verified(path, expected_sha256):
    if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(char not in '0123456789abcdef' for char in expected_sha256)):
        raise ValueError('cleanup_plan_digest_required')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'rb') as source, tempfile.TemporaryDirectory(prefix='sawa-cleanup-verified-') as root:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('cleanup_plan_must_be_owner_only_regular_file')
        copy = Path(root) / 'plan.sqlite'
        digest = hashlib.sha256()
        with copy.open('wb') as output:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != expected_sha256:
            raise ValueError('cleanup_plan_changed')
        with closing(sqlite3.connect(copy.as_uri() + '?mode=ro&immutable=1', uri=True)) as manifest:
            rows = manifest.execute('SELECT value FROM metadata').fetchall()
            if len(rows) != 1:
                raise ValueError('cleanup_plan_header_invalid')
            header = json.loads(rows[0][0])
            if not isinstance(header, dict) or header.get('schema') != 1:
                raise ValueError('cleanup_plan_header_invalid')
            yield manifest, header


def order_batches(manifest, size=200):
    cursor = manifest.execute("SELECT owner_id FROM rows WHERE table_name='orders' ORDER BY key_json")
    while rows := cursor.fetchmany(size):
        yield [row[0] for row in rows]


def require_current_rows(session, manifest, model, owner_column, identities):
    placeholders = ','.join('?' for _ in identities)
    expected = manifest.execute('SELECT COUNT(*) FROM rows WHERE table_name=? AND owner_id IN (' + placeholders + ')',
                                (model.__tablename__, *identities)).fetchone()[0]
    observed = 0
    rows = session.execute(select(*columns(model)).where(owner_column.in_(identities))
        .with_for_update().execution_options(yield_per=200)).mappings()
    try:
        for row in rows:
            stored = manifest.execute('SELECT row_json FROM rows WHERE table_name=? AND key_json=?',
                                      (model.__tablename__, row_key(model, row))).fetchone()
            if stored is None or stored[0] != row_json(row):
                raise ValueError('cleanup_source_changed_since_review')
            observed += 1
    finally:
        rows.close()
    if observed != expected:
        raise ValueError('cleanup_source_changed_since_review')
