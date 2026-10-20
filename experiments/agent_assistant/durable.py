"""Cloud SQL durability for the isolated staging trial.

The existing SQLite ledger schema is serialized inside one PostgreSQL row lock.
It is never placed on a FUSE mount. Before-images and sealed files are stored in
that same database; working business databases remain disposable per-run copies.
A replacement instance fences its predecessor and requires explicit recovery.
"""
from contextlib import contextmanager, closing
import hashlib
from pathlib import Path
import shutil
import sqlite3

from .store import Store, Conflict, now


class PostgresStore(Store):
    def __init__(self, root, connect):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connect = connect
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

    @contextmanager
    def tx(self):
        with closing(self.connect()) as pg, pg:
            with pg.cursor() as c:
                c.execute('SELECT epoch,payload FROM sawa_agent_trial.ledger WHERE id=1 FOR UPDATE')
                epoch, payload = c.fetchone()
                if epoch != self.epoch:
                    raise Conflict('実行環境が交代しました。画面を再読み込みし、履歴から停止・巻き戻しを行ってください')
                db = sqlite3.connect(':memory:')
                db.row_factory = sqlite3.Row
                try:
                    if payload is not None:
                        db.deserialize(bytes(payload))
                    db.execute('BEGIN IMMEDIATE')
                    yield db
                    db.commit()
                    # Persist a sealed artifact before acknowledging its ledger row.
                    if db.execute("SELECT 1 FROM sqlite_master WHERE name='artifacts'").fetchone():
                        for row in db.execute('SELECT path,sha256 FROM artifacts'):
                            self._file(c, Path(row['path']), expected=row['sha256'])
                    c.execute('UPDATE sawa_agent_trial.ledger SET payload=%s WHERE id=1', (db.serialize(),))
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
        cursor.execute('INSERT INTO sawa_agent_trial.files VALUES (%s,%s,%s)', (relative,digest,content))

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
                    if checked != relative or hashlib.sha256(bytes(payload)).hexdigest() != digest:
                        raise Conflict('保存済みの試用原本・帳票を照合できません')
                    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                    path.write_bytes(bytes(payload))
        for before in (self.root / 'runs').glob('*/before'):
            data = before.parent / 'data'
            if not data.exists():
                shutil.copytree(before,data)

    def recover(self):
        with self.tx() as db:
            # A model or native job may still be settling. Preserve costs/reservations.
            db.execute("UPDATE runs SET generation=generation+1,status='reconciliation_required',updated=? WHERE status NOT IN ('rolled_back','completed','stopped','failed','budget_reached')",(now(),))


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
