"""Cloud SQL durability for the isolated staging trial.

The durable ledger uses native PostgreSQL rows. Legacy SQLite bytes are retained
only for migration recovery, never reserialized on ordinary reads or writes.
Working copies are checkpointed with the command receipts for the changed run.
A replacement instance fences its predecessor and requires explicit recovery.
"""
from contextlib import contextmanager, closing, nullcontext
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import weakref
import zlib
from contextvars import ContextVar

from .store import Store, Conflict, now
from . import postgres_ledger


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
    question_condition = ("COALESCE(payload::jsonb->'result'->>'question','')<>'' AND "
                          "COALESCE(payload::jsonb->'result'->>'command_id','')<>''")
    review_state_expression = "payload::jsonb->>'state'"
    workflow_phase_expression = "task_inputs::jsonb->'business_workflow'->>'phase'"
    workflow_version_expression = "task_inputs::jsonb->'business_workflow'->'version'"
    fax_order_expression = "snapshot::jsonb->>'order_id'"
    def __init__(self, root, connect):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connect = connect
        self.working_ready = False
        self._hydrated = set()
        self._preparing = set()
        self._hydration_lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._read_snapshot = ContextVar('agent_read_snapshot', default=None)
        self.path = self.root / 'ledger.sqlite3'
        with closing(connect()) as pg, pg:
            with pg.cursor() as c:
                c.execute('CREATE SCHEMA IF NOT EXISTS sawa_agent_trial')
                c.execute('SET LOCAL search_path TO sawa_agent_trial,pg_catalog')
                c.execute('''CREATE TABLE IF NOT EXISTS sawa_agent_trial.ledger (
                    id integer PRIMARY KEY CHECK(id=1), epoch bigint NOT NULL,
                    payload bytea)''')
                c.execute('''CREATE TABLE IF NOT EXISTS sawa_agent_trial.files (
                    path text PRIMARY KEY, digest text NOT NULL, payload bytea NOT NULL)''')
                c.execute('INSERT INTO sawa_agent_trial.ledger(id,epoch,payload) VALUES (1,0,NULL) ON CONFLICT DO NOTHING')
                c.execute('UPDATE sawa_agent_trial.ledger SET epoch=epoch+1 WHERE id=1 RETURNING epoch,payload IS NULL')
                self.epoch,self.new_ledger = c.fetchone()
                self.columns, self.new_ledger = postgres_ledger.initialize(c, decode_ledger=decode_ledger)
        self.working_ready = True

    def close(self):
        close = getattr(self.connect, "close", None)
        if close is not None:
            close()

    def get(self, ident, owner=None):
        with self.snapshot(view='identities', owner=owner, identities=[ident]):
            run = super().get(ident, owner)
        return run

    def create(self, *args, **kwargs):
        run, fresh = super().create(*args, **kwargs)
        if fresh:
            # New sandbox data belongs to this instance; do not restore an
            # older checkpoint over changes currently being prepared locally.
            with self._hydration_lock:
                self._hydrated.add(run['id'])
                self._preparing.add(run['id'])
        return run, fresh

    def budget(self):
        with self.snapshot(view='configuration'):
            return super().budget()

    def provider(self, value=None):
        if value is not None:
            return super().provider(value)
        with self.snapshot(view='configuration'):
            return super().provider()

    def events(self, ident, *, kinds=None):
        with self.snapshot(view='events', identities=[ident]):
            return super().events(ident, kinds=kinds)

    def listing(self, owner):
        with self.snapshot(view='runs', owner=owner):
            return super().listing(owner)

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
    def snapshot_read(self, *, view=None, owner=None, identities=None):
        # Store queries now execute against indexed canonical rows directly.
        with self.read() as db:
            yield db

    @contextmanager
    def _transaction(self, *, readonly):
        from psycopg2.errors import SerializationFailure, DeadlockDetected
        # Start each writer's snapshot after its predecessor commits. Epoch
        # fencing permits only this instance to write; readers remain concurrent.
        transaction_lock = nullcontext() if readonly else self._write_lock
        with transaction_lock, closing(self.connect()) as pg, pg:
            pg.set_session(readonly=readonly, isolation_level='REPEATABLE READ' if readonly else 'SERIALIZABLE')
            with pg.cursor() as c:
                c.execute('SET LOCAL search_path TO sawa_agent_trial,pg_catalog')
                # Shared epoch locks fence replacement instances without making
                # unrelated run updates queue behind an exclusive ledger lock.
                c.execute('SELECT epoch,storage_format FROM sawa_agent_trial.ledger WHERE id=1'
                          + ('' if readonly else ' FOR SHARE'))
                epoch, storage = c.fetchone()
                if epoch != self.epoch or storage != postgres_ledger.FORMAT:
                    raise Conflict('実行環境が交代しました。画面を再読み込みし、履歴から停止・巻き戻しを行ってください')
                if not readonly:
                    c.execute('CREATE TEMP TABLE changed_agent_runs(run_id text PRIMARY KEY) ON COMMIT DROP')
                db = postgres_ledger.Connection(pg, self.columns)
                try:
                    yield db
                    if readonly:
                        return
                    c.execute('SELECT run_id FROM pg_temp.changed_agent_runs ORDER BY run_id')
                    changed = [row[0] for row in c.fetchall()]
                    from .working_copy import checkpoint
                    for ident in changed:
                        # Only this run's immutable originals/checkpoints are
                        # relevant to the receipt being committed.
                        known_files = {}
                        for folder in ('before', 'sources', 'question-drawings', 'artifacts'):
                            prefix = 'runs/' + ident + '/' + folder + '/'
                            c.execute('SELECT path,digest FROM sawa_agent_trial.files '
                                      'WHERE path COLLATE "C">=%s AND path COLLATE "C"<%s',
                                      (prefix, prefix[:-1] + '0'))
                            known_files.update(c.fetchall())
                        c.execute('SELECT manifest FROM working_copies WHERE run_id=%s', (ident,))
                        current_copy = c.fetchone()
                        if current_copy:
                            try:
                                current_manifest = json.loads(current_copy[0])
                            except (TypeError, ValueError) as exc:
                                raise Conflict('保存済みcheckpointのmanifest形式が不正です') from exc
                            if not isinstance(current_manifest, dict):
                                raise Conflict('保存済みcheckpointのmanifest形式が不正です')
                            keys = list({'runs/' + ident + '/checkpoints/' + digest
                                         for digest in current_manifest.values()
                                         if isinstance(digest, str)})
                            for offset in range(0, len(keys), 200):
                                c.execute('SELECT path,digest FROM sawa_agent_trial.files WHERE path=ANY(%s)',
                                          (keys[offset:offset + 200],))
                                known_files.update(c.fetchall())
                        if self.working_ready and ident in self._hydrated and ident not in self._preparing:
                            manifest = checkpoint(self.root, ident, c, encode_blob, known=known_files)
                            if manifest is not None:
                                db.execute('INSERT INTO working_copies VALUES(?,?) ON CONFLICT(run_id) DO UPDATE SET manifest=excluded.manifest', (ident, manifest))
                            for name in ('before','sources','question-drawings'):
                                for path in sorted((self.root/'runs'/ident/name).rglob('*')):
                                    if path.is_file():
                                        self._file(c, path, known=known_files)
                        for row in db.execute('SELECT path,sha256 FROM artifacts WHERE run_id=?', (ident,)):
                            self._file(c, Path(row['path']), expected=row['sha256'], known=known_files)
                    pg.commit()
                except (SerializationFailure, DeadlockDetected) as exc:
                    # Never replay unknown business mutations automatically.
                    raise Conflict('同時更新のため履歴保存を中断しました。実行結果を照合してから再開してください') from exc
                finally:
                    db.close()

    def _relative(self, path):
        path = path.resolve()
        relative = path.relative_to(self.root)
        if relative.parts[0] != 'runs' or len(relative.parts) < 4:
            raise Conflict('永続化する試用ファイルの保存先が不正です')
        return path, str(relative)

    def _file(self, cursor, path, expected=None, *, known=None):
        path, relative = self._relative(path)
        if known is None:
            cursor.execute('SELECT digest FROM sawa_agent_trial.files WHERE path=%s', (relative,))
            old = cursor.fetchone()
        else:
            old = (known[relative],) if relative in known else None
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
        cursor.execute('INSERT INTO sawa_agent_trial.files VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                       (relative,digest,encode_blob(content)))
        cursor.execute('SELECT digest FROM sawa_agent_trial.files WHERE path=%s', (relative,))
        saved = cursor.fetchone()
        if saved is None or saved[0] != digest:
            raise Conflict('保存済み原本・帳票の上書きは禁止されています')
        if known is not None:
            known[relative] = digest

    def persist_files(self, ident):
        from .working_copy import checkpoint
        root = self.root / 'runs' / ident
        with self._write_lock, closing(self.connect()) as pg, pg:
            with pg.cursor() as c:
                c.execute('SELECT epoch FROM sawa_agent_trial.ledger WHERE id=1 FOR UPDATE')
                if c.fetchone()[0] != self.epoch:
                    raise Conflict('実行環境が交代したため保存を停止しました')
                paths = []
                for name in ('before','artifacts','sources','question-drawings'):
                    directory = root / name
                    for path in sorted(directory.rglob('*')):
                        if path.is_file():
                            paths.append(path)
                relatives = [self._relative(path)[1] for path in paths]
                known_files = {}
                for offset in range(0, len(relatives), 500):
                    c.execute('SELECT path,digest FROM sawa_agent_trial.files WHERE path=ANY(%s)',
                              (relatives[offset:offset + 500],))
                    known_files.update(c.fetchall())
                for path in paths:
                    self._file(c, path, known=known_files)
                manifest = checkpoint(self.root, ident, c, encode_blob, known=known_files)
                if manifest is not None:
                    c.execute('INSERT INTO sawa_agent_trial.working_copies(run_id,manifest) VALUES(%s,%s) '
                              'ON CONFLICT(run_id) DO UPDATE SET manifest=excluded.manifest', (ident, manifest))
        self._preparing.discard(ident)

    def working_copy_ready(self, ident):
        if ident in self._preparing:
            return False
        with self.read() as db:
            return db.execute('SELECT 1 FROM working_copies WHERE run_id=?', (ident,)).fetchone() is not None

    def capture_copy(self, ident, owner):
        with self.read() as db:
            row = db.execute('SELECT r.generation,r.status,r.task_id,r.task_inputs,w.manifest FROM runs r '
                             'JOIN working_copies w ON w.run_id=r.id WHERE r.id=? AND r.owner=?', (ident, owner)).fetchone()
            if row is None or row['task_id'] not in ('hospital_pending_faxes','hospital_review_faxes'):
                raise Conflict('同じ利用者の保存済み病院依頼を選択してください')
            phase = json.loads(row['task_inputs']).get('business_workflow',{}).get('phase')
            if row['status'] != 'completed' and not (row['status'] in ('running','waiting_input') and phase == 'approval'):
                raise Conflict('確認待ちまたは完了した病院依頼を選択してください')
            if db.execute("SELECT 1 FROM commands WHERE run_id=? AND status!='done' LIMIT 1", (ident,)).fetchone():
                raise Conflict('結果未確定の処理がある依頼はコピーできません')
            manifest = json.loads(row['manifest'])
            digest = manifest.get('hospital/trial-manifest.json')
            marker = db.execute('SELECT payload FROM sawa_agent_trial.files WHERE path=? AND digest=?',
                                ('runs/'+ident+'/checkpoints/'+str(digest),digest)).fetchone() if digest else None
            if marker is None or json.loads(decode_blob(marker['payload'],expected=digest)).get('request_relative_storage') is not True:
                raise Conflict('過去の依頼はファイル参照を独立コピーできません。新しい依頼で処理してください')
        return {'run_id':ident,'owner':owner,'generation':row['generation'],'status':row['status'],'manifest':manifest,
                'sha256':hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()}

    def materialize_copy(self, source, target):
        ident, manifest = source['run_id'], source['manifest']
        if (len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident)
                or not isinstance(manifest,dict) or not manifest
                or hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()!=source['sha256']):
            raise Conflict('選択した作業コピーの出典を照合できません')
        targets = {}
        target = Path(target).resolve()
        for name,digest in manifest.items():
            relative = Path(name)
            if (relative.is_absolute() or '..' in relative.parts or len(relative.parts)<2
                    or relative.parts[0]!='hospital' or not isinstance(digest,str) or len(digest)!=64
                    or any(c not in '0123456789abcdef' for c in digest)):
                raise Conflict('病院の保存済み作業コピーだけを選択してください')
            path = target.joinpath(*relative.parts[1:])
            targets.setdefault(digest,[]).append(path)
        with self.read() as db:
            row = db.execute('SELECT generation,status FROM runs WHERE id=? AND owner=?', (ident,source['owner'])).fetchone()
            if row is None or row['generation']!=source['generation'] or row['status'] in ('rolled_back','stopped','failed','reconciliation_required'):
                raise Conflict('元の依頼が停止・復元・変更されています。選び直してください')
            digests = list(targets)
            for offset in range(0,len(digests),20):
                batch = digests[offset:offset+20]
                keys = ['runs/'+ident+'/checkpoints/'+digest for digest in batch]
                found = set()
                for item in db.execute('SELECT path,digest,payload FROM sawa_agent_trial.files WHERE path IN ('
                                       + ','.join('?' for _ in keys)+')',keys):
                    digest = item['digest']
                    if digest not in batch or item['path']!='runs/'+ident+'/checkpoints/'+digest:
                        raise Conflict('保存済み作業コピーの参照が一致しません')
                    content = decode_blob(item['payload'],expected=digest)
                    for path in targets[digest]:
                        path.parent.mkdir(parents=True,exist_ok=True)
                        path.write_bytes(content)
                    found.add(digest)
                if found != set(batch):
                    raise Conflict('保存済み作業コピーの一部がありません')

    def hydrate(self):
        """Explicit full repair only; startup and normal reads restore one run."""
        with self.read() as db:
            for row in db.execute('SELECT id FROM runs'):
                self._hydrate_run(row['id'], force=True)

    def ensure_local_run(self, ident):
        self._hydrate_run(ident)

    def has_local_run(self, ident):
        return ident in self._hydrated

    def materialize_file(self, path, *, expected):
        """Fetch one immutable display artifact, not the working database."""
        path, relative = self._relative(Path(path))
        with self._hydration_lock:
            if path.is_file():
                return
            with self.read() as db:
                row = db.execute('SELECT digest,payload FROM sawa_agent_trial.files WHERE path=?', (relative,)).fetchone()
                if row is None or row['digest'] != expected:
                    raise Conflict('保存済み原本・帳票の参照を照合できません')
                content = decode_blob(row['payload'], expected=expected)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(content)

    def checkpoint_files(self, ident, prefixes):
        """Read small progress receipts without hydrating the native database."""
        with self.read() as db:
            row = db.execute('SELECT manifest FROM working_copies WHERE run_id=?', (ident,)).fetchone()
            if row is None:
                return []
            try:
                manifest = json.loads(row['manifest'])
            except (TypeError, ValueError) as exc:
                raise Conflict('保存済みcheckpointのmanifest形式が不正です') from exc
            if not isinstance(manifest, dict) or any(not isinstance(name, str) for name in manifest):
                raise Conflict('保存済みcheckpointのmanifest形式が不正です')
            selected = {name: digest for name, digest in manifest.items()
                        if isinstance(name, str) and any(name.startswith(prefix) for prefix in prefixes)}
            for name, digest in selected.items():
                if ('..' in Path(name).parts or Path(name).is_absolute()
                        or not isinstance(digest, str) or len(digest) != 64
                        or any(char not in '0123456789abcdef' for char in digest)):
                    raise Conflict('保存済み進捗の参照が不正です')
            content = {}
            keys = ['runs/' + ident + '/checkpoints/' + digest for digest in set(selected.values())]
            for offset in range(0, len(keys), 200):
                batch = keys[offset:offset + 200]
                for item in db.execute('SELECT path,digest,payload FROM sawa_agent_trial.files WHERE path IN ('
                                       + ','.join('?' for _ in batch) + ')', batch):
                    if item['path'] != 'runs/' + ident + '/checkpoints/' + item['digest']:
                        raise Conflict('保存済み進捗の原本参照が一致しません')
                    content[item['digest']] = decode_blob(item['payload'], expected=item['digest'])
            if set(content) != set(selected.values()):
                raise Conflict('保存済み進捗のcheckpointが不足しています')
            return [(name, content[digest]) for name, digest in sorted(selected.items())]

    def _hydrate_run(self, ident, *, force=False):
        from .working_copy import restore
        from uuid import uuid4
        with self._hydration_lock:
            if ident in self._hydrated and not force:
                return
            with closing(self.connect()) as pg, pg:
                pg.set_session(isolation_level='REPEATABLE READ')
                with pg.cursor() as query:
                    query.execute('SELECT epoch FROM sawa_agent_trial.ledger WHERE id=1 FOR SHARE')
                    if query.fetchone()[0] != self.epoch:
                        raise Conflict('実行環境が交代したため作業コピーを復元しません')
                    query.execute('SELECT manifest FROM sawa_agent_trial.working_copies WHERE run_id=%s', (ident,))
                    row = query.fetchone()
                    manifest = row[0] if row else None
                keys = []
                if manifest is not None:
                    try:
                        contents = json.loads(manifest)
                    except (TypeError, ValueError) as exc:
                        raise Conflict('保存済みcheckpointのmanifest形式が不正です') from exc
                    if not isinstance(contents, dict) or any(not isinstance(name, str) for name in contents):
                        raise Conflict('保存済みcheckpointのmanifest形式が不正です')
                    for digest in contents.values():
                        if not isinstance(digest, str) or len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest):
                            raise Conflict('保存済みcheckpointの照合値が不正です')
                        keys.append('runs/' + ident + '/checkpoints/' + digest)

                def receive(cursor):
                    for relative, digest, payload in cursor:
                        path, checked = self._relative(self.root / relative)
                        if checked != relative:
                            raise Conflict('保存済みの試用原本・帳票を照合できません')
                        content = decode_blob(payload, expected=digest)
                        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        path.write_bytes(content)

                ranges, parameters = [], []
                for name in ('before', 'artifacts', 'sources', 'question-drawings'):
                    prefix = 'runs/' + ident + '/' + name + '/'
                    ranges.append('(path COLLATE "C">=%s AND path COLLATE "C"<%s)')
                    parameters.extend((prefix, prefix[:-1] + '0'))
                with pg.cursor(name='agent_files_' + uuid4().hex) as files:
                    files.itersize = 20
                    files.execute('SELECT path,digest,payload FROM sawa_agent_trial.files WHERE '
                                  + ' OR '.join(ranges) + ' ORDER BY path', parameters)
                    receive(files)
                for offset in range(0, len(keys), 200):
                    with pg.cursor(name='agent_checkpoint_' + uuid4().hex) as files:
                        files.itersize = 20
                        files.execute('SELECT path,digest,payload FROM sawa_agent_trial.files WHERE path=ANY(%s)',
                                      (keys[offset:offset + 200],))
                        receive(files)
                if manifest is not None:
                    restore(self.root, ident, manifest)
            self._hydrated.add(ident)

    def require_working_copy(self, ident):
        self._hydrate_run(ident)
        with self.read() as db:
            row = db.execute('SELECT manifest FROM working_copies WHERE run_id=?', (ident,)).fetchone()
        if not row:
            raise Conflict('保存済み作業データがない旧依頼です。開始前データでは再開しません')

    def recover(self):
        with self.tx() as db:
            # A model or native job may still be settling. Preserve costs/reservations.
            for row in db.execute("SELECT id,generation FROM runs WHERE status NOT IN ('rolled_back','completed','stopped','failed','budget_reached')").fetchall():
                db.execute("INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)",
                           (row['id'], 'gateway_recovery_fence', json.dumps({'previous_generation': row['generation'], 'generation': row['generation'] + 1}), now()))
            db.execute("UPDATE runs SET generation=generation+1,status=CASE WHEN status='stopping' THEN 'stop_pending' WHEN status IN ('stop_pending','rollback_stop_pending') THEN status ELSE 'reconciliation_required' END,updated=? WHERE status NOT IN ('rolled_back','completed','stopped','failed','budget_reached')",(now(),))


class _PooledConnections:
    def __init__(self, **parameters):
        from psycopg2.pool import ThreadedConnectionPool
        self.pool = ThreadedConnectionPool(1, 4, **parameters)
        self.finalize = weakref.finalize(self, self.pool.closeall)
        self.slots = threading.BoundedSemaphore(4)

    def __call__(self):
        if not self.slots.acquire(timeout=10):
            raise Conflict("実行履歴への接続が混雑しています。業務操作は受け付けていません。")
        try:
            return _ConnectionLease(self.pool.getconn(), self)
        except BaseException:
            self.slots.release()
            raise

    def close(self):
        self.finalize()


class _ConnectionLease:
    def __init__(self, connection, owner):
        self.connection, self.owner = connection, owner

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def __enter__(self):
        return self.connection.__enter__()

    def __exit__(self, *exception):
        return self.connection.__exit__(*exception)

    def close(self):
        if self.connection is not None:
            connection, self.connection = self.connection, None
            try:
                if not connection.closed:
                    try:
                        connection.reset()
                    except BaseException:
                        self.owner.pool.putconn(connection, close=True)
                        raise
                self.owner.pool.putconn(connection, close=bool(connection.closed))
            finally:
                self.owner.slots.release()


def cloud_store(root):
    import os
    required = ('DB_HOST','DB_NAME','DB_USER','DB_PASSWORD')
    if any(not os.environ.get(name) for name in required):
        raise RuntimeError('stg実行履歴のCloud SQL設定が不足しています')
    if (os.environ['DB_HOST'] != '/cloudsql/sawahospitalsystem:asia-northeast2:orders-stg'
            or os.environ['DB_NAME'] != 'orders' or os.environ['DB_USER'] != 'orders_app'):
        raise RuntimeError('試用エージェントの永続化先はorders-stg専用です')
    connect = _PooledConnections(host=os.environ['DB_HOST'], dbname=os.environ['DB_NAME'],
        user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], connect_timeout=10,
        application_name='sawa-agent-isolated-trial')
    try:
        return PostgresStore(root, connect)
    except BaseException:
        connect.close()
        raise
