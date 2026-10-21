"""Durable trial ledger. Dollar values are integer US cents, never floats."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
from zoneinfo import ZoneInfo


class Conflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def billing_month():
    return datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m")


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "ledger.sqlite3"
        with self.tx() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), monthly_cents INTEGER NOT NULL DEFAULT 0 CHECK(monthly_cents>=0));
              INSERT OR IGNORE INTO settings(id) VALUES(1);
              CREATE TABLE IF NOT EXISTS runs(
                id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, owner TEXT NOT NULL,
                instruction TEXT NOT NULL, domains TEXT NOT NULL, status TEXT NOT NULL,
                month TEXT NOT NULL, cap INTEGER NOT NULL, spent INTEGER NOT NULL DEFAULT 0,
                reserved INTEGER NOT NULL, session_id TEXT, generation INTEGER NOT NULL DEFAULT 0,
                usage TEXT NOT NULL DEFAULT '{}', created TEXT NOT NULL, updated TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, provider_id TEXT,
                kind TEXT NOT NULL, payload TEXT NOT NULL, created TEXT NOT NULL,
                UNIQUE(run_id,provider_id));
              CREATE TABLE IF NOT EXISTS commands(
                run_id TEXT NOT NULL, id TEXT NOT NULL, request TEXT NOT NULL,
                status TEXT NOT NULL, result TEXT, PRIMARY KEY(run_id,id));
              CREATE TABLE IF NOT EXISTS artifacts(
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, path TEXT NOT NULL,
                name TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL);
            """)
            if "task_id" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
                db.execute("ALTER TABLE runs ADD COLUMN task_id TEXT")
            if "task_inputs" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
                db.execute("ALTER TABLE runs ADD COLUMN task_inputs TEXT NOT NULL DEFAULT '{}'")
            if "provider" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
                db.execute("ALTER TABLE runs ADD COLUMN provider TEXT NOT NULL DEFAULT 'claude'")
            if "provider" not in {row[1] for row in db.execute("PRAGMA table_info(settings)")}:
                db.execute("ALTER TABLE settings ADD COLUMN provider TEXT NOT NULL DEFAULT 'claude'")
            db.execute("CREATE TABLE IF NOT EXISTS selections(id TEXT PRIMARY KEY,owner TEXT NOT NULL,task_id TEXT NOT NULL,payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS task_reviews(run_id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS fax_batches(run_id TEXT PRIMARY KEY)")
            db.execute("""CREATE TABLE IF NOT EXISTS fax_items(
                run_id TEXT NOT NULL, fax_id TEXT NOT NULL, snapshot TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'processing', review TEXT, decision TEXT,
                PRIMARY KEY(run_id,fax_id))""")

    @contextmanager
    def tx(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def read(self):
        with self.tx() as db:
            db.execute('PRAGMA query_only=ON')
            yield db

    def budget(self):
        month = billing_month()
        with self.read() as db:
            limit = db.execute("SELECT monthly_cents FROM settings").fetchone()[0]
            spent, reserved = db.execute("SELECT COALESCE(SUM(spent),0),COALESCE(SUM(reserved),0) FROM runs WHERE month=?", (month,)).fetchone()
        return dict(month=month, currency="USD", monthly_cents=limit, spent_cents=spent,
                    reserved_cents=reserved, available_cents=max(0, limit-spent-reserved),
                    basis="provider_list_cost_or_configured_token_estimate", final_invoice=False,
                    notice="Claudeは接続先の定価換算値、Geminiはトークン数と設定単価による概算です。実行中のモデル要求により上限超過が生じることがあります。請求額・付属クレジット残高とは異なります。")

    def set_budget(self, cents):
        if type(cents) is not int or cents < 0:
            raise ValueError("予算は0以上の整数セントで指定してください")
        with self.tx() as db:
            db.execute("UPDATE settings SET monthly_cents=? WHERE id=1", (cents,))
        return self.budget()

    def provider(self, value=None):
        with (self.read() if value is None else self.tx()) as db:
            if value is not None:
                if value not in ('claude', 'gemini'): raise ValueError('ClaudeまたはGeminiを選択してください')
                db.execute('UPDATE settings SET provider=? WHERE id=1', (value,))
            return db.execute('SELECT provider FROM settings WHERE id=1').fetchone()[0]

    def create(self, owner, key, instruction, domains, cap, *, task_id=None, task_inputs=None, provider='claude'):
        if provider not in ('claude', 'gemini'): raise ValueError('未対応の接続先です')
        if type(cap) is not int or cap <= 0:
            raise ValueError("依頼上限は1セント以上が必要です")
        task_inputs = task_inputs or {}
        domains = sorted(set(domains))
        if not domains or set(domains) - {"hospital", "shift", "school-lunch"}:
            raise ValueError("業務を選択してください")
        timestamp = now()
        with self.tx() as db:
            old = db.execute("SELECT * FROM runs WHERE request_key=?", (key,)).fetchone()
            if old:
                if old['provider'] != provider: raise Conflict('同一依頼キーの接続先が異なります')
                if (old["owner"], old["instruction"], json.loads(old["domains"]), old["cap"], old["task_id"], json.loads(old["task_inputs"])) != (owner, instruction, domains, cap, task_id, task_inputs):
                    raise Conflict("同一依頼キーの内容が異なります")
                return self.decode(old), False
            month = billing_month()
            limit = db.execute("SELECT monthly_cents FROM settings").fetchone()[0]
            total = db.execute("SELECT COALESCE(SUM(spent+reserved),0) FROM runs WHERE month=?", (month,)).fetchone()[0]
            if total + cap > limit:
                raise Conflict("月次予算の空きがありません。上限または実行中の依頼を確認してください")
            # Sandbox ports are private to one active request; no concurrent writers.
            if db.execute("SELECT 1 FROM runs WHERE status NOT IN ('rolled_back','stopped','failed','completed','waiting_input','budget_reached')").fetchone():
                raise Conflict("他の依頼を処理中です")
            ident = uuid4().hex
            db.execute("INSERT INTO runs(id,request_key,owner,instruction,domains,status,month,cap,reserved,created,updated) VALUES(?,?,?,?,?,'preparing',?,?,?,?,?)",
                       (ident, key, owner, instruction, json.dumps(domains), month, cap, cap, timestamp, timestamp))
            db.execute("UPDATE runs SET task_id=?,task_inputs=? WHERE id=?", (task_id, json.dumps(task_inputs, ensure_ascii=False), ident))
            db.execute('UPDATE runs SET provider=? WHERE id=?', (provider, ident))
            row = db.execute("SELECT * FROM runs WHERE id=?", (ident,)).fetchone()
        return self.decode(row), True

    @staticmethod
    def decode(row):
        data = dict(row)
        data["domains"] = json.loads(data["domains"])
        data["usage"] = json.loads(data["usage"])
        data["task_inputs"] = json.loads(data["task_inputs"])
        return data

    def get(self, ident, owner=None):
        with self.read() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (ident,)).fetchone()
            if not row or (owner is not None and row["owner"] != owner):
                raise KeyError("依頼が見つかりません")
            return self.decode(row)

    def listing(self, owner):
        with self.read() as db:
            return [self.decode(row) for row in db.execute("SELECT * FROM runs WHERE owner=? ORDER BY created DESC LIMIT 100", (owner,))]

    def update(self, ident, **values):
        allowed = {"status", "session_id", "usage", "spent", "reserved", "task_inputs"}
        if set(values) - allowed:
            raise ValueError("unknown ledger fields")
        for field in ("usage", "task_inputs"):
            if field in values: values[field] = json.dumps(values[field], ensure_ascii=False)
        values["updated"] = now()
        with self.tx() as db:
            db.execute("UPDATE runs SET " + ",".join(k+"=?" for k in values) + " WHERE id=?", (*values.values(), ident))

    def fence(self, ident):
        with self.tx() as db:
            db.execute("UPDATE runs SET generation=generation+1,status='stopping',updated=? WHERE id=?", (now(), ident))

    def event(self, ident, kind, payload, provider_id=None):
        with self.tx() as db:
            cur = db.execute("INSERT OR IGNORE INTO events(run_id,provider_id,kind,payload,created) VALUES(?,?,?,?,?)",
                             (ident, provider_id, kind, json.dumps(payload, ensure_ascii=False), now()))
            return cur.rowcount == 1

    def events(self, ident):
        with self.read() as db:
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY id", (ident,))]

    def command(self, ident, command_id, request):
        encoded = json.dumps(request, sort_keys=True, ensure_ascii=False)
        with self.tx() as db:
            row = db.execute("SELECT * FROM commands WHERE run_id=? AND id=?", (ident, command_id)).fetchone()
            if row:
                if row["request"] != encoded:
                    raise Conflict("命令IDが別の内容で再利用されました")
                if row["status"] != "done":
                    raise Conflict("前の命令の結果が未確定です。再送せず履歴を確認してください")
                return json.loads(row["result"])
            db.execute("INSERT INTO commands VALUES(?,?,?,'executing',NULL)", (ident, command_id, encoded))
        return None

    def finish_command(self, ident, command_id, result):
        with self.tx() as db:
            db.execute("UPDATE commands SET status='done',result=? WHERE run_id=? AND id=?",
                       (json.dumps(result, ensure_ascii=False), ident, command_id))

    def usage(self, ident, usage, terminal=False):
        cost = usage.get("list_cost")
        if not isinstance(cost, dict) or cost.get("currency") != "USD" or not str(cost.get("amount", "")).isdigit():
            raise Conflict("接続先から料金を取得できません。予算予約を維持します")
        spent = int(cost["amount"])
        with self.tx() as db:
            row = db.execute("SELECT spent,cap,reserved FROM runs WHERE id=?", (ident,)).fetchone()
            spent = max(row[0], spent)
            db.execute("UPDATE runs SET spent=?,reserved=?,usage=?,updated=? WHERE id=?",
                       (spent, 0 if terminal else min(row[2], max(0, row[1]-spent)), json.dumps(usage), now(), ident))

    def recover(self):
        # A crashed gateway cannot assume a remote session stopped or refund its reservation.
        with self.tx() as db:
            db.execute("UPDATE runs SET status='reconciliation_required' WHERE status IN ('preparing','dispatching','running','stopping','restoring')")
