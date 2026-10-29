"""Durable trial ledger. Dollar values are integer US cents, never floats."""
from contextlib import contextmanager
from contextvars import ContextVar
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
    question_condition = ("COALESCE(json_extract(payload,'$.result.question'),'')<>'' AND "
                          "COALESCE(json_extract(payload,'$.result.command_id'),'')<>''")
    review_state_expression = "json_extract(payload,'$.state')"
    workflow_phase_expression = "json_extract(task_inputs,'$.business_workflow.phase')"
    workflow_version_expression = "json_extract(task_inputs,'$.business_workflow.version')"
    fax_order_expression = "CAST(json_extract(snapshot,'$.order_id') AS TEXT)"
    def __init__(self, root: Path):
        self._read_snapshot = ContextVar("agent_read_snapshot", default=None)
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
            db.execute('CREATE INDEX IF NOT EXISTS events_run_id ON events(run_id,id)')
            db.execute('CREATE INDEX IF NOT EXISTS events_run_kind ON events(run_id,kind,id)')
            if "task_id" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
                db.execute("ALTER TABLE runs ADD COLUMN task_id TEXT")
            if "task_inputs" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
                db.execute("ALTER TABLE runs ADD COLUMN task_inputs TEXT NOT NULL DEFAULT '{}'")
            if "provider" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
                db.execute("ALTER TABLE runs ADD COLUMN provider TEXT NOT NULL DEFAULT 'claude'")
            if "provider" not in {row[1] for row in db.execute("PRAGMA table_info(settings)")}:
                db.execute("ALTER TABLE settings ADD COLUMN provider TEXT NOT NULL DEFAULT 'claude'")
            db.execute("CREATE TABLE IF NOT EXISTS selections(id TEXT PRIMARY KEY,owner TEXT NOT NULL,task_id TEXT NOT NULL,payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS target_lookups(id TEXT PRIMARY KEY,owner TEXT NOT NULL,task_id TEXT NOT NULL,inputs TEXT NOT NULL,state TEXT NOT NULL,payload TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS task_reviews(run_id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS fax_batches(run_id TEXT PRIMARY KEY)")
            db.execute("""CREATE TABLE IF NOT EXISTS fax_items(
                run_id TEXT NOT NULL, fax_id TEXT NOT NULL, snapshot TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'processing', review TEXT, decision TEXT,
                PRIMARY KEY(run_id,fax_id))""")
            db.execute('''CREATE TABLE IF NOT EXISTS provider_usage(
                run_id TEXT NOT NULL,interaction_id TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,output_tokens INTEGER NOT NULL,thought_tokens INTEGER NOT NULL,
                PRIMARY KEY(run_id,interaction_id))''')
            db.execute('CREATE TABLE IF NOT EXISTS provider_usage_migrations(run_id TEXT PRIMARY KEY)')
            db.execute('CREATE INDEX IF NOT EXISTS fax_items_run_state ON fax_items(run_id,state)')
            db.execute('CREATE INDEX IF NOT EXISTS fax_items_order ON fax_items(run_id,' + self.fax_order_expression + ')')

    @contextmanager
    def tx(self):
        if self._read_snapshot.get() is not None:
            raise Conflict("読取スナップショット内では変更できません")
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
        snapshot = self._read_snapshot.get()
        if snapshot is not None:
            yield snapshot
            return
        with self.tx() as db:
            db.execute('PRAGMA query_only=ON')
            yield db

    @contextmanager
    def snapshot(self, *, view=None, owner=None, identities=None):
        """Keep every part of one response on the same committed ledger."""
        with self.snapshot_read(view=view, owner=owner, identities=identities) as db:
            token = self._read_snapshot.set(db)
            try:
                yield
            finally:
                self._read_snapshot.reset(token)

    @contextmanager
    def snapshot_read(self, *, view=None, owner=None, identities=None):
        with self.read() as db:
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
            for active in db.execute("SELECT id,status FROM runs WHERE status NOT IN ('rolled_back','stopped','failed','completed','waiting_input','budget_reached')"):
                if active['status'] == 'rollback_stop_pending':
                    receipt = db.execute("SELECT payload FROM events WHERE run_id=? AND kind='rollback' ORDER BY id DESC LIMIT 1", (active['id'],)).fetchone()
                    restored = json.loads(receipt['payload']) if receipt else {}
                    if restored.get('restored') is True and restored.get('scope') == 'this_request_sandbox':
                        continue
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

    def get_many(self, identities):
        if not identities:
            return []
        with self.read() as db:
            records = {row['id']: self.decode(row) for row in db.execute(
                'SELECT * FROM runs WHERE id IN (' + ','.join('?' for _ in identities) + ')', identities)}
        if any(ident not in records for ident in identities):
            raise KeyError('依頼が見つかりません')
        return [records[ident] for ident in identities]

    def find_request(self, owner, key):
        with self.read() as db:
            row = db.execute('SELECT * FROM runs WHERE owner=? AND request_key=?', (owner, key)).fetchone()
            return self.decode(row) if row else None

    def target_lookup(self, ident, owner):
        with self.snapshot(view='lookup', owner=owner, identities=[ident]):
            with self.read() as db:
                row = db.execute('SELECT * FROM target_lookups WHERE id=? AND owner=?',
                                 (ident, owner)).fetchone()
                if row is None:
                    raise KeyError(ident)
                return dict(row)

    def listing(self, owner):
        with self.read() as db:
            return [self.decode(row) for row in db.execute("SELECT * FROM runs WHERE owner=? ORDER BY created DESC,id DESC LIMIT 100", (owner,))]

    def run_history(self, owner, *, before_created=None, before_id=None, unfinished=False):
        if bool(before_created) != bool(before_id):
            raise ValueError('依頼履歴cursorの日時とIDを両方指定してください')
        condition, values = 'owner=?', [owner]
        if before_created is not None:
            datetime.fromisoformat(before_created)
            condition += ' AND (created<? OR (created=? AND id<?))'
            values.extend((before_created, before_created, before_id))
        if unfinished:
            condition += " AND status NOT IN ('completed','stopped','rolled_back','failed')"
        with self.read() as db:
            records = db.execute('SELECT id,instruction,domains,status,spent,created,updated,task_id,provider,cap,'
                + self.workflow_phase_expression + ' AS _workflow_phase,'
                + self.workflow_version_expression + ' AS _workflow_version '
                'FROM runs WHERE ' + condition + ' ORDER BY created DESC,id DESC LIMIT 101', values).fetchall()
        items = [{**dict(row), 'domains': json.loads(row['domains'])} for row in records[:100]]
        last = items[-1] if items and len(records) > 100 else None
        return {'items': items, 'next': {'before_created': last['created'], 'before_id': last['id']} if last else None}

    def listing_business_inputs(self, runs):
        identities = [run['id'] for run in runs]
        if not identities:
            return {}, set(), set()
        placeholders = ','.join('?' for _ in identities)
        with self.read() as db:
            reviews = {row['run_id']: {'state': row['state']} for row in db.execute(
                'SELECT run_id,' + self.review_state_expression + ' AS state FROM task_reviews '
                'WHERE run_id IN (' + placeholders + ')', identities)}
            faxes = {row[0] for row in db.execute('SELECT DISTINCT run_id FROM fax_items '
                "WHERE state='awaiting_approval' AND run_id IN (" + placeholders + ')', identities)}
            questions = {row[0] for row in db.execute('SELECT r.id FROM runs r WHERE r.id IN ('
                + placeholders + ") AND r.status='waiting_input' AND EXISTS (SELECT 1 FROM events e "
                "WHERE e.run_id=r.id AND e.kind='tool_result' AND " + self.question_condition
                + " AND e.id>COALESCE((SELECT MAX(reply.id) FROM events reply "
                "WHERE reply.run_id=r.id AND reply.kind='operator_reply'),0))", identities)}
        return reviews, faxes, questions

    def update(self, ident, *, expected_generation=None, expected_stop_generation=None, **values):
        if expected_generation is not None and expected_stop_generation is not None:
            raise ValueError('generation guards are mutually exclusive')
        allowed = {"status", "session_id", "usage", "spent", "reserved", "task_inputs"}
        if set(values) - allowed:
            raise ValueError("unknown ledger fields")
        for field in ("usage", "task_inputs"):
            if field in values: values[field] = json.dumps(values[field], ensure_ascii=False)
        values["updated"] = now()
        with self.tx() as db:
            if expected_stop_generation is not None:
                current = db.execute('SELECT generation,status FROM runs WHERE id=?', (ident,)).fetchone()
                if current['generation'] != expected_stop_generation or current['status'] not in ('stopping', 'stop_pending', 'rollback_stop_pending'):
                    raise Conflict('停止対象の世代が変更されています')
            if expected_generation is not None:
                current = db.execute('SELECT generation,status FROM runs WHERE id=?', (ident,)).fetchone()
                if current['generation'] != expected_generation or current['status'] in ('completed', 'stopping', 'stopped', 'rolled_back', 'stop_pending', 'rollback_stop_pending'):
                    raise Conflict('依頼の状態が変更されています。遅延した処理は実行しません')
            db.execute("UPDATE runs SET " + ",".join(k+"=?" for k in values) + " WHERE id=?", (*values.values(), ident))

    def fence(self, ident, *, expected_generation=None):
        with self.tx() as db:
            previous = db.execute('SELECT * FROM runs WHERE id=?', (ident,)).fetchone()
            if expected_generation is not None:
                row = db.execute("UPDATE runs SET generation=generation+1,status='stopping',updated=? WHERE id=? AND generation=? AND status NOT IN ('completed','stopping','stopped','rolled_back','stop_pending','rollback_stop_pending') RETURNING generation",
                                 (now(), ident, expected_generation)).fetchone()
            else:
                row = db.execute("UPDATE runs SET generation=generation+1,status='stopping',updated=? WHERE id=? RETURNING generation", (now(), ident)).fetchone()
            return (self.decode(previous), row['generation']) if row else None

    def event(self, ident, kind, payload, provider_id=None):
        with self.tx() as db:
            cur = db.execute("INSERT OR IGNORE INTO events(run_id,provider_id,kind,payload,created) VALUES(?,?,?,?,?)",
                             (ident, provider_id, kind, json.dumps(payload, ensure_ascii=False), now()))
            return cur.rowcount == 1

    def events(self, ident, *, kinds=None):
        if kinds is not None and (not isinstance(kinds, (tuple, list))
                                  or any(not isinstance(kind, str) for kind in kinds)):
            raise ValueError('履歴種別が不正です')
        if kinds is not None and not kinds:
            return []
        condition = ' AND kind IN (' + ','.join('?' for _ in kinds) + ')' if kinds is not None else ''
        with self.read() as db:
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in db.execute(
                'SELECT * FROM events WHERE run_id=?' + condition + ' ORDER BY id', (ident, *(kinds or ())))]

    def latest_event(self, ident, kind, *, first=False):
        with self.read() as db:
            row = db.execute('SELECT * FROM events WHERE run_id=? AND kind=? ORDER BY id '
                             + ('ASC' if first else 'DESC') + ' LIMIT 1',
                             (ident, kind)).fetchone()
            return {**dict(row), 'payload': json.loads(row['payload'])} if row else None

    def pending_question(self, ident):
        from .conversation import pending_question
        with self.read() as db:
            reply = db.execute("SELECT COALESCE(MAX(id),0) FROM events WHERE run_id=? AND kind='operator_reply'",
                               (ident,)).fetchone()[0]
            row = db.execute("SELECT * FROM events WHERE run_id=? AND kind='tool_result' AND id>? AND "
                             + self.question_condition + ' ORDER BY id DESC LIMIT 1', (ident, reply)).fetchone()
            events = [{**dict(row), 'payload': json.loads(row['payload'])}] if row else []
            return pending_question(events)

    def event_page(self, ident, *, after=0, before=None, limit=200):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('履歴取得範囲が不正です')
        if before is not None and (type(before) is not int or before < 1 or after):
            raise ValueError('履歴cursorが不正です')
        with self.read() as db:
            condition = ' AND id<?' if before is not None else ' AND id>?'
            reverse = before is not None or not after
            records = db.execute('SELECT * FROM events WHERE run_id=?' + condition
                                 + (' ORDER BY id DESC' if reverse else ' ORDER BY id') + ' LIMIT ?',
                                 (ident, before if before is not None else after, limit + 1)).fetchall()
            more = len(records) > limit
            records = records[:limit]
            if reverse:
                records.reverse()
            events = [{**dict(row), 'payload': json.loads(row['payload'])} for row in records]
            return {'events': events, 'next_event': events[-1]['id'] if events else after,
                    'previous_event': events[0]['id'] if events else before,
                    'has_previous': more if reverse else False,
                    'has_more': more if not reverse else False}

    def gemini_usage_totals(self, ident, interaction, usage):
        keys = ('total_input_tokens', 'total_output_tokens', 'total_thought_tokens')

        def values(raw):
            if not isinstance(raw, dict) or any(type(raw.get(key)) is not int or raw[key] < 0 for key in keys):
                raise Conflict('Geminiの保存済み利用量が未確定です。予約額を保持します')
            return tuple(raw[key] for key in keys)

        with self.tx() as db:
            def upsert(interaction_id, tokens):
                if not isinstance(interaction_id, str) or not interaction_id:
                    raise Conflict('Geminiの利用量と実行IDを照合できません')
                db.execute('''INSERT INTO provider_usage VALUES(?,?,?,?,?)
                    ON CONFLICT(run_id,interaction_id) DO UPDATE SET
                    input_tokens=CASE WHEN provider_usage.input_tokens<excluded.input_tokens THEN excluded.input_tokens ELSE provider_usage.input_tokens END,
                    output_tokens=CASE WHEN provider_usage.output_tokens<excluded.output_tokens THEN excluded.output_tokens ELSE provider_usage.output_tokens END,
                    thought_tokens=CASE WHEN provider_usage.thought_tokens<excluded.thought_tokens THEN excluded.thought_tokens ELSE provider_usage.thought_tokens END''',
                    (ident, interaction_id, *tokens))

            if not db.execute('SELECT 1 FROM runs WHERE id=?', (ident,)).fetchone():
                raise KeyError('依頼が見つかりません')
            if not db.execute('SELECT 1 FROM provider_usage_migrations WHERE run_id=?', (ident,)).fetchone():
                for row in db.execute("SELECT payload FROM events WHERE run_id=? AND kind='gemini_usage' ORDER BY id", (ident,)):
                    previous = json.loads(row['payload'])
                    upsert(previous['id'], values(previous['usage']))
                db.execute('INSERT INTO provider_usage_migrations VALUES(?)', (ident,))
            tokens = values(usage)
            previous = db.execute('SELECT input_tokens,output_tokens,thought_tokens FROM provider_usage '
                                  'WHERE run_id=? AND interaction_id=?', (ident, interaction)).fetchone()
            if previous is None or any(value > previous[index] for index, value in enumerate(tokens)):
                upsert(interaction, tokens)
                db.execute('INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)',
                           (ident, 'gemini_usage', json.dumps({'id': interaction, 'usage': usage}), now()))
            totals = db.execute('SELECT COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),'
                                'COALESCE(SUM(thought_tokens),0) FROM provider_usage WHERE run_id=?', (ident,)).fetchone()
            return dict(zip(keys, totals))

    def command(self, ident, command_id, request):
        encoded = json.dumps(request, sort_keys=True, ensure_ascii=False)
        with self.tx() as db:
            row = db.execute("SELECT * FROM commands WHERE run_id=? AND id=?", (ident, command_id)).fetchone()
            if row:
                if row["request"] != encoded:
                    raise Conflict("命令IDが別の内容で再利用されました")
                if row['status'] == 'recoverable_wait' and request.get('name') in ('wait_for_task_decision', 'wait_for_native_job'):
                    db.execute("UPDATE commands SET status='executing' WHERE run_id=? AND id=?", (ident, command_id))
                    return None
                if row['status'] == 'recoverable_read':
                    from .catalog import OPERATIONS
                    operation = OPERATIONS.get(request.get('name'))
                    if operation and operation.method == 'GET':
                        db.execute("UPDATE commands SET status='executing' WHERE run_id=? AND id=?", (ident, command_id))
                        return None
                if row["status"] != "done":
                    raise Conflict("前の命令の結果が未確定です。再送せず履歴を確認してください")
                return json.loads(row["result"])
            db.execute("INSERT INTO commands VALUES(?,?,?,'executing',NULL)", (ident, command_id, encoded))
        return None

    def finish_command(self, ident, command_id, result):
        with self.tx() as db:
            db.execute("UPDATE commands SET status='done',result=? WHERE run_id=? AND id=?",
                       (json.dumps(result, ensure_ascii=False), ident, command_id))

    def usage(self, ident, usage, terminal=False, *, terminal_statuses=None, terminal_generation=None, terminal_session=None):
        cost = usage.get("list_cost")
        if not isinstance(cost, dict) or cost.get("currency") != "USD" or not str(cost.get("amount", "")).isdigit():
            raise Conflict("接続先から料金を取得できません。予算予約を維持します")
        spent = int(cost["amount"])
        with self.tx() as db:
            row = db.execute("SELECT spent,cap,reserved,status,generation,session_id FROM runs WHERE id=?", (ident,)).fetchone()
            spent = max(row[0], spent)
            terminal = terminal and (terminal_statuses is None or row['status'] in terminal_statuses)
            terminal = terminal and (terminal_generation is None or row['generation'] == terminal_generation)
            terminal = terminal and (terminal_session is None or row['session_id'] == terminal_session)
            db.execute("UPDATE runs SET spent=?,reserved=?,usage=?,updated=? WHERE id=?",
                       (spent, 0 if terminal else min(row[2], max(0, row[1]-spent)), json.dumps(usage), now(), ident))

    def release_unstarted_model(self, ident, generation):
        with self.tx() as db:
            run = db.execute('SELECT generation,status,session_id FROM runs WHERE id=?', (ident,)).fetchone()
            dispatched = db.execute("SELECT 1 FROM events WHERE run_id=? AND kind IN ('gemini_dispatch','agent.custom_tool_use','late_session_created') LIMIT 1", (ident,)).fetchone()
            if run['generation'] != generation or run['status'] != 'completed' or run['session_id'] or dispatched:
                raise Conflict('モデル未使用の業務完了を確認できないため予約額を保持します')
            db.execute('UPDATE runs SET reserved=0,updated=? WHERE id=?', (now(), ident))

    def finish_rollback(self, ident, generation, result):
        with self.tx() as db:
            run = db.execute('SELECT generation,status FROM runs WHERE id=?', (ident,)).fetchone()
            if run['generation'] != generation or run['status'] not in ('stopped', 'stop_pending', 'rollback_stop_pending'):
                raise Conflict('復元結果の保存対象が変更されています')
            status = 'rolled_back' if run['status'] == 'stopped' else 'rollback_stop_pending'
            db.execute('INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)',
                       (ident, 'rollback', json.dumps(result, ensure_ascii=False), now()))
            db.execute('UPDATE runs SET status=?,updated=? WHERE id=?', (status, now(), ident))

    def recover(self):
        # A crashed gateway cannot assume a remote session stopped or refund its reservation.
        with self.tx() as db:
            for row in db.execute("SELECT id,generation FROM runs WHERE status IN ('preparing','dispatching','running','stopping','restoring')").fetchall():
                db.execute('INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)',
                           (row['id'], 'gateway_recovery_fence', json.dumps({'previous_generation': row['generation'], 'generation': row['generation'] + 1}), now()))
            db.execute("UPDATE runs SET generation=generation+1,status=CASE WHEN status='stopping' THEN 'stop_pending' ELSE 'reconciliation_required' END,updated=? WHERE status IN ('preparing','dispatching','running','stopping','restoring')", (now(),))
