"""Cloud SQL durability for the isolated staging trial.

The existing SQLite ledger schema is serialized inside one PostgreSQL row lock.
It is never placed on a FUSE mount. Before-images and sealed files are stored in
that same database; working copies are checkpointed with their command receipts.
A replacement instance fences its predecessor and requires explicit recovery.
"""
from contextlib import contextmanager, closing
import hashlib
from pathlib import Path
import sqlite3
import zlib

from .store import Store, Conflict, now


SQLITE_HEADER = b"SQLite format 3\0"
BLOB_HEADER = b"SAWA-Blob-Zlib:1\0"


def encode_blob(raw):
    """Keep transport lossless; hashes always describe the original bytes."""
    return BLOB_HEADER + hashlib.sha256(raw).digest() + zlib.compress(raw)


def decode_blob(payload, *, expected=None):
    raw = bytes(payload)
    # An existing file is identified by its persisted content hash, not a guess.
    if expected and hashlib.sha256(raw).hexdigest() == expected:
        return raw
    if not raw.startswith(BLOB_HEADER):
        raise Conflict('保存済み試用データの形式を確認できません')
    digest_start = len(BLOB_HEADER)
    digest = raw[digest_start:digest_start + 32]
    try:
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(raw[digest_start + 32:]) + decoder.flush()
    except zlib.error as exc:
        raise Conflict('保存済み試用データを復元できません') from exc
    actual = hashlib.sha256(decoded)
    if (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
            or actual.digest() != digest or (expected and actual.hexdigest() != expected)):
        raise Conflict('保存済み試用データの内容を照合できません')
    return decoded


def encode_ledger(raw):
    if not raw.startswith(SQLITE_HEADER):
        raise Conflict('保存する実行履歴の形式が不正です')
    return encode_blob(raw)


def decode_ledger(payload):
    raw = bytes(payload)
    decoded = raw if raw.startswith(SQLITE_HEADER) else decode_blob(raw)
    if not decoded.startswith(SQLITE_HEADER):
        raise Conflict('保存済み実行履歴の形式を確認できません')
    page_size = int.from_bytes(decoded[16:18], 'big')
    page_size = 65536 if page_size == 1 else page_size
    pages = int.from_bytes(decoded[28:32], 'big')
    if (len(decoded) < 100 or page_size < 512 or page_size > 65536
            or page_size & (page_size - 1) or not pages or len(decoded) != page_size * pages):
        raise Conflict('保存済み実行履歴が破損しています')
    # Validate both supported formats before schema initialization can write.
    try:
        with closing(sqlite3.connect(':memory:')) as check:
            check.deserialize(decoded)
            check.execute('PRAGMA query_only=ON')
            if check.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise Conflict('保存済み実行履歴が破損しています')
    except sqlite3.Error as exc:
        raise Conflict('保存済み実行履歴が破損しています') from exc
    return decoded


class PostgresStore(Store):
    def __init__(self, root, connect):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connect = connect
        self.working_ready = False
        with closing(connect()) as pg, pg:
            with pg.cursor() as c:
                c.execute('CREATE SCHEMA IF NOT EXISTS sawa_agent_trial')
                c.execute('''CREATE TABLE IF NOT EXISTS sawa_agent_trial.ledger (
                    id integer PRIMARY KEY CHECK(id=1), epoch bigint NOT NULL,
                    payload bytea)''')
                c.execute('''CREATE TABLE IF NOT EXISTS sawa_agent_trial.files (
                    path text PRIMARY KEY, digest text NOT NULL, payload bytea NOT NULL)''')
                c.execute('INSERT INTO sawa_agent_trial.ledger VALUES (1,0,NULL) ON CONFLICT DO NOTHING')
                c.execute('UPDATE sawa_agent_trial.ledger SET epoch=epoch+1 WHERE id=1 RETURNING epoch,payload IS NULL')
                self.epoch,self.new_ledger = c.fetchone()
        super().__init__(self.root)
        self.hydrate()
        self.working_ready = True

    @contextmanager
    def tx(self):
        if self._read_snapshot.get() is not None:
            raise Conflict("読取スナップショット内では変更できません")
        with self._transaction(readonly=False) as db:
            yield db

    @contextmanager
    def read(self):
        snapshot = self._read_snapshot.get()
        if snapshot is not None:
            yield snapshot
            return
        with self._transaction(readonly=True) as db:
            yield db

    @contextmanager
    def _transaction(self, *, readonly):
        with closing(self.connect()) as pg, pg:
            pg.set_session(readonly=readonly)
            with pg.cursor() as c:
                c.execute('SELECT epoch,payload FROM sawa_agent_trial.ledger WHERE id=1' + ('' if readonly else ' FOR UPDATE'))
                epoch, payload = c.fetchone()
                if epoch != self.epoch:
                    raise Conflict('実行環境が交代しました。画面を再読み込みし、履歴から停止・巻き戻しを行ってください')
                db = sqlite3.connect(':memory:')
                db.row_factory = sqlite3.Row
                try:
                    if payload is not None:
                        db.deserialize(decode_ledger(payload))
                    db.execute('PRAGMA query_only=' + ('ON' if readonly else 'OFF'))
                    db.execute('BEGIN' if readonly else 'BEGIN IMMEDIATE')
                    yield db
                    if readonly:
                        db.commit()
                        return
                    db.execute('CREATE TABLE IF NOT EXISTS working_copies(run_id TEXT PRIMARY KEY,manifest TEXT NOT NULL)')
                    if self.working_ready:
                        from .working_copy import checkpoint
                        for row in db.execute('SELECT id FROM runs').fetchall():
                            manifest = checkpoint(self.root, row['id'], c, encode_blob)
                            if manifest is not None:
                                db.execute('INSERT INTO working_copies VALUES(?,?) ON CONFLICT(run_id) DO UPDATE SET manifest=excluded.manifest', (row['id'], manifest))
                            # A question receipt must never commit before the
                            # original drawing or source binding it references.
                            for name in ('before','sources','question-drawings'):
                                for path in sorted((self.root/'runs'/row['id']/name).rglob('*')):
                                    if path.is_file():
                                        self._file(c, path)
                    db.commit()
                    # Persist a sealed artifact before acknowledging its ledger row.
                    if db.execute("SELECT 1 FROM sqlite_master WHERE name='artifacts'").fetchone():
                        for row in db.execute('SELECT path,sha256 FROM artifacts'):
                            self._file(c, Path(row['path']), expected=row['sha256'])
                    c.execute('UPDATE sawa_agent_trial.ledger SET payload=%s WHERE id=1', (encode_ledger(db.serialize()),))
                except BaseException:
                    db.rollback()
                    raise
                finally:
                    db.close()

    def _relative(self, path):
        path = path.resolve()
        relative = path.relative_to(self.root)
        if relative.parts[0] != 'runs' or len(relative.parts) < 4:
            raise Conflict('永続化する試用ファイルの保存先が不正です')
        return path, str(relative)

    def _file(self, cursor, path, expected=None):
        path, relative = self._relative(path)
        cursor.execute('SELECT digest FROM sawa_agent_trial.files WHERE path=%s', (relative,))
        old = cursor.fetchone()
        if old and expected and old[0] == expected:
            return
        if path.is_symlink() or not path.is_file():
            raise Conflict('永続化する原本または帳票が見つかりません')
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if expected and digest != expected:
            raise Conflict('永続化する帳票の内容が変更されています')
        if old:
            if old[0] != digest:
                raise Conflict('保存済み原本・帳票の上書きは禁止されています')
            return
        cursor.execute('INSERT INTO sawa_agent_trial.files VALUES (%s,%s,%s)', (relative,digest,encode_blob(content)))

    def persist_files(self, ident):
        root = self.root / 'runs' / ident
        with closing(self.connect()) as pg, pg:
            with pg.cursor() as c:
                c.execute('SELECT epoch FROM sawa_agent_trial.ledger WHERE id=1 FOR UPDATE')
                if c.fetchone()[0] != self.epoch:
                    raise Conflict('実行環境が交代したため保存を停止しました')
                for name in ('before','artifacts','sources','question-drawings'):
                    directory = root / name
                    for path in sorted(directory.rglob('*')):
                        if path.is_file():
                            self._file(c,path)

    def hydrate(self):
        with closing(self.connect()) as pg, pg:
            with pg.cursor() as c:
                c.execute('SELECT path,digest,payload FROM sawa_agent_trial.files ORDER BY path')
                for relative,digest,payload in c:
                    path = self.root / relative
                    path, checked = self._relative(path)
                    content = decode_blob(payload, expected=digest)
                    if checked != relative:
                        raise Conflict('保存済みの試用原本・帳票を照合できません')
                    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                    path.write_bytes(content)
        from .working_copy import restore
        with self.read() as db:
            snapshots = db.execute('SELECT run_id,manifest FROM working_copies').fetchall()
        for row in snapshots:
            restore(self.root, row['run_id'], row['manifest'])

    def require_working_copy(self, ident):
        with self.read() as db:
            row = db.execute('SELECT manifest FROM working_copies WHERE run_id=?', (ident,)).fetchone()
        if not row:
            raise Conflict('保存済み作業データがない旧依頼です。開始前データでは再開しません')

    def recover(self):
        with self.tx() as db:
            # A model or native job may still be settling. Preserve costs/reservations.
            db.execute("UPDATE runs SET generation=generation+1,status=CASE WHEN status IN ('stop_pending','rollback_stop_pending') THEN status ELSE 'reconciliation_required' END,updated=? WHERE status NOT IN ('rolled_back','completed','stopped','failed','budget_reached')",(now(),))


def cloud_store(root):
    import os
    import psycopg2
    required = ('DB_HOST','DB_NAME','DB_USER','DB_PASSWORD')
    if any(not os.environ.get(name) for name in required):
        raise RuntimeError('stg実行履歴のCloud SQL設定が不足しています')
    if (os.environ['DB_HOST'] != '/cloudsql/sawahospitalsystem:asia-northeast2:orders-stg'
            or os.environ['DB_NAME'] != 'orders' or os.environ['DB_USER'] != 'orders_app'):
        raise RuntimeError('試用エージェントの永続化先はorders-stg専用です')
    def connect():
        return psycopg2.connect(host=os.environ['DB_HOST'],dbname=os.environ['DB_NAME'],
            user=os.environ['DB_USER'],password=os.environ['DB_PASSWORD'],connect_timeout=10,
            application_name='sawa-agent-isolated-trial')
    return PostgresStore(root,connect)
