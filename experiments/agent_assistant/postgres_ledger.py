"""Native PostgreSQL ledger, preserving the existing Store query contract.

SQLite remains the local sandbox format, not the durable ledger format. The
small dialect bridge accepts only Store's positional parameters and conflict
syntax; all values remain driver-bound parameters.
"""
from contextlib import closing
import re
import sqlite3
from tempfile import TemporaryDirectory
from pathlib import Path
from decimal import Decimal

from .store import Store, Conflict


FORMAT = 'postgres-rows-v1'
_TOKENS = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|\?|[^'\"?]+")


def statement(sql):
    tokens = _TOKENS.findall(sql)
    if ''.join(tokens) != sql:
        raise ValueError('Unsupported ledger SQL quoting')
    parts = []
    for token in tokens:
        if token == '?':
            if parts and re.search(r'\bIS\s+(?:NOT\s+)?$', parts[-1], re.I):
                parts[-1] = re.sub(r'\bIS\s+(NOT\s+)?$',
                    lambda match: 'IS DISTINCT FROM ' if match.group(1) else 'IS NOT DISTINCT FROM ',
                    parts[-1], flags=re.I)
            parts.append('%s')
        else:
            parts.append(token)
    sql = ''.join(parts)
    if re.match(r'\s*INSERT OR IGNORE\b', sql, re.I):
        sql = re.sub(r'\bINSERT OR IGNORE\b', 'INSERT', sql, count=1, flags=re.I)
        sql = sql.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    return sql


class Row:
    def __init__(self, names, values):
        self.names, self.values = names, values

    def keys(self):
        return self.names

    def __getitem__(self, key):
        return self.values[key if isinstance(key, (int, slice)) else self.names.index(key)]

    def __iter__(self):
        return iter(self.values)


class Result:
    def __init__(self, cursor, *, include_rowid):
        self.cursor = cursor
        self.columns = []
        seen = set()
        for index, column in enumerate(cursor.description or ()):
            name = column.name
            if name == 'rowid' and (not include_rowid or name in seen):
                continue
            seen.add(name)
            self.columns.append((index, name))

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def _row(self, values):
        if values is None:
            return None
        return Row([name for _, name in self.columns],
                   [int(values[index]) if isinstance(values[index], Decimal)
                    and values[index] == values[index].to_integral_value()
                    else values[index] for index, _ in self.columns])

    def fetchone(self):
        return self._row(self.cursor.fetchone())

    def fetchall(self):
        return [self._row(row) for row in self.cursor.fetchall()]

    def __iter__(self):
        for row in self.cursor:
            yield self._row(row)


class Connection:
    def __init__(self, pg, columns):
        self.pg, self.columns = pg, columns
        self.cursors = []

    def execute(self, query, parameters=()):
        sql = statement(query)
        # Hidden ordering keys must not change positional INSERT/SELECT * APIs.
        positional = re.match(r'(\s*INSERT\s+INTO\s+)([a-z_]+)(\s+VALUES\b)', sql, re.I)
        if positional:
            table = positional.group(2)
            if table not in self.columns:
                raise ValueError('Unknown ledger table')
            names = ','.join('"' + name + '"' for name in self.columns[table])
            sql = sql[:positional.end(2)] + '(' + names + ')' + sql[positional.end(2):]
        cursor = self.pg.cursor()
        self.cursors.append(cursor)
        cursor.execute(sql, parameters)
        return Result(cursor, include_rowid=bool(re.search(r'\browid\b', query, re.I)))

    def close(self):
        for cursor in self.cursors:
            cursor.close()


def schema():
    """Derive table definitions from the same schema used by the local Store."""
    with TemporaryDirectory(prefix='sawa-ledger-schema-') as directory:
        store = Store(Path(directory))
        with closing(sqlite3.connect(store.path)) as db:
            return [(name, sql, [column[1] for column in db.execute(
                'PRAGMA table_info("' + name + '")')])
                for name, sql in db.execute("SELECT name,sql FROM sqlite_master "
                                           "WHERE type='table' AND name NOT LIKE 'sqlite_%'")]


def initialize(cursor, *, decode_ledger):
    """Migrate once under the existing epoch lock; retain original bytes."""
    from psycopg2 import sql
    from psycopg2.extras import execute_values

    cursor.execute('ALTER TABLE sawa_agent_trial.ledger ADD COLUMN IF NOT EXISTS storage_format text')
    cursor.execute('SELECT storage_format,payload FROM sawa_agent_trial.ledger WHERE id=1 FOR UPDATE')
    storage, payload = cursor.fetchone()
    if storage not in (None, FORMAT):
        raise Conflict('実行履歴の保存形式が未対応です。旧形式へ戻しません')
    definitions = schema()
    columns = {name: names for name, _, names in definitions}
    columns['working_copies'] = ['run_id', 'manifest']
    for name, ddl, _ in definitions:
        ddl = re.sub(r'^CREATE TABLE\s+', 'CREATE TABLE IF NOT EXISTS ', ddl, count=1, flags=re.I)
        ddl = re.sub(r'INTEGER PRIMARY KEY AUTOINCREMENT', 'BIGSERIAL PRIMARY KEY', ddl, flags=re.I)
        ddl = re.sub(r'\bINTEGER\b', 'BIGINT', ddl, flags=re.I)
        cursor.execute(ddl)
        cursor.execute(sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS rowid BIGSERIAL UNIQUE NOT NULL')
                       .format(sql.Identifier(name)))
        cursor.execute('SELECT column_name FROM information_schema.columns '
                       'WHERE table_schema=%s AND table_name=%s', ('sawa_agent_trial', name))
        if {row[0] for row in cursor.fetchall()} != set(columns[name]) | {'rowid'}:
            raise Conflict('実行履歴のschema移行が必要です。旧列・未知列を推測して使いません')
    cursor.execute('CREATE TABLE IF NOT EXISTS working_copies(run_id TEXT PRIMARY KEY,manifest TEXT NOT NULL)')
    if storage is None and payload is not None:
        with closing(sqlite3.connect(':memory:')) as source:
            source.deserialize(decode_ledger(payload))
            source.execute('PRAGMA query_only=ON')
            existing = {name for name, in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if {name for name in existing if not name.startswith('sqlite_')} - columns.keys():
                raise Conflict('移行元に未対応の履歴tableがあります。データを捨てず停止しました')
            for table, names in columns.items():
                if table not in existing:
                    continue
                fields = [column[1] for column in source.execute('PRAGMA table_info("' + table + '")')]
                # Missing newly-added columns retain their canonical defaults.
                if set(fields) - set(names):
                    raise Conflict('移行元の履歴schemaが未対応です。列を捨てず停止しました')
                with_rowid = table != 'working_copies'
                target = (['rowid'] if with_rowid else []) + fields
                insert = sql.SQL('INSERT INTO {} ({}) VALUES %s').format(
                    sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, target)))
                rows = source.execute('SELECT ' + ('rowid,' if with_rowid else '') + '* FROM "' + table + '"')
                while batch := rows.fetchmany(200):
                    execute_values(cursor, insert, batch)
                    key = 'rowid' if with_rowid else 'run_id'
                    cursor.execute(sql.SQL('SELECT {} FROM {} WHERE {}=ANY(%s)').format(
                        sql.SQL(',').join(map(sql.Identifier, target)), sql.Identifier(table), sql.Identifier(key)),
                        ([row[0] for row in batch],))
                    restored = {row[0]: tuple(bytes(value) if isinstance(value, memoryview) else value
                                               for value in row) for row in cursor.fetchall()}
                    if restored != {row[0]: tuple(row) for row in batch}:
                        raise Conflict('移行前後の履歴行が一致しません。移行を確定しませんでした')
                if with_rowid:
                    cursor.execute(sql.SQL("SELECT setval(pg_get_serial_sequence(%s,'rowid'), "
                                           "COALESCE(MAX(rowid),1),MAX(rowid) IS NOT NULL) FROM {}")
                                   .format(sql.Identifier(table)), (table,))
                if table == 'events':
                    sequence = (source.execute("SELECT seq FROM sqlite_sequence WHERE name='events'").fetchone()
                                if 'sqlite_sequence' in existing else None)
                    high_water = sequence[0] if sequence else 0
                    cursor.execute("SELECT setval(pg_get_serial_sequence('events','id'), "
                                   "GREATEST(COALESCE(MAX(id),1),%s),MAX(id) IS NOT NULL OR %s>0) FROM events",
                                   (high_water, high_water))
    cursor.execute('INSERT INTO settings(id) VALUES(1) ON CONFLICT DO NOTHING')
    cursor.execute('CREATE INDEX IF NOT EXISTS ledger_runs_owner_cursor ON runs(owner,created DESC,id DESC)')
    cursor.execute('CREATE INDEX IF NOT EXISTS ledger_runs_month ON runs(month) INCLUDE(spent,reserved)')
    cursor.execute("CREATE INDEX IF NOT EXISTS ledger_active_runs ON runs(id) WHERE status NOT IN "
                   "('rolled_back','stopped','failed','completed','waiting_input','budget_reached')")
    cursor.execute("CREATE INDEX IF NOT EXISTS ledger_recoverable_runs ON runs(id) WHERE status NOT IN "
                   "('rolled_back','completed','stopped','failed','budget_reached')")
    cursor.execute('CREATE INDEX IF NOT EXISTS ledger_events_parent ON events(run_id,id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS ledger_events_kind ON events(run_id,kind,id DESC)')
    cursor.execute('CREATE INDEX IF NOT EXISTS ledger_artifacts_parent ON artifacts(run_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS ledger_artifacts_cursor ON artifacts(run_id,rowid)')
    cursor.execute('CREATE INDEX IF NOT EXISTS ledger_faxes_cursor ON fax_items(run_id,rowid)')
    cursor.execute('CREATE INDEX IF NOT EXISTS ledger_faxes_state ON fax_items(run_id,state)')
    cursor.execute("CREATE INDEX IF NOT EXISTS ledger_faxes_order ON fax_items(run_id,(snapshot::jsonb->>'order_id'),rowid)")
    cursor.execute('CREATE INDEX IF NOT EXISTS ledger_files_prefix ON sawa_agent_trial.files(path COLLATE "C")')
    cursor.execute('''CREATE OR REPLACE FUNCTION sawa_agent_trial.track_run_change() RETURNS trigger
        LANGUAGE plpgsql AS $$ DECLARE changed text; BEGIN
          IF TG_TABLE_NAME='runs' THEN
            changed := CASE WHEN TG_OP='DELETE' THEN OLD.id ELSE NEW.id END;
          ELSE
            changed := CASE WHEN TG_OP='DELETE' THEN OLD.run_id ELSE NEW.run_id END;
          END IF;
          INSERT INTO pg_temp.changed_agent_runs VALUES(changed) ON CONFLICT DO NOTHING;
          RETURN NULL;
        END $$''')
    for table, names in columns.items():
        if table == 'working_copies' or (table != 'runs' and 'run_id' not in names):
            continue
        cursor.execute(sql.SQL('DROP TRIGGER IF EXISTS ledger_changed ON {}').format(sql.Identifier(table)))
        cursor.execute(sql.SQL('CREATE TRIGGER ledger_changed AFTER INSERT OR UPDATE OR DELETE ON {} '
                               'FOR EACH ROW EXECUTE FUNCTION sawa_agent_trial.track_run_change()')
                       .format(sql.Identifier(table)))
    # Old application versions must not overwrite the archived SQLite snapshot.
    cursor.execute('''CREATE OR REPLACE FUNCTION sawa_agent_trial.protect_legacy_ledger() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF OLD.storage_format='postgres-rows-v1' THEN
            RAISE EXCEPTION 'Legacy ledger writes disabled after native migration';
          END IF;
          RETURN NEW;
        END $$''')
    cursor.execute('DROP TRIGGER IF EXISTS protect_legacy_ledger ON sawa_agent_trial.ledger')
    cursor.execute('''CREATE TRIGGER protect_legacy_ledger BEFORE UPDATE OF payload ON sawa_agent_trial.ledger
        FOR EACH ROW EXECUTE FUNCTION sawa_agent_trial.protect_legacy_ledger()''')
    cursor.execute('UPDATE sawa_agent_trial.ledger SET storage_format=%s WHERE id=1', (FORMAT,))
    return columns, storage is None and payload is None
