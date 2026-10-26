"""Transactionally indexed ledger rows for bounded operator-page reads."""
from contextlib import closing, contextmanager
import json
import math
import sqlite3

from .store import Conflict, billing_month


def identifier(name):
    return '"' + name.replace('"', '""') + '"'


def encode(value):
    if isinstance(value, float) and not math.isfinite(value):
        return {'real': value.hex()}
    return {'blob': value.hex()} if isinstance(value, bytes) else value


def decode(value):
    if isinstance(value, dict):
        if set(value) == {'blob'}:
            return bytes.fromhex(value['blob'])
        if set(value) == {'real'}:
            return float.fromhex(value['real'])
        raise Conflict('保存済み履歴の行データ形式が不正です')
    return value


def capture(db):
    schema, rows = {}, {}
    for table, statement in db.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        columns = [row[1] for row in db.execute('PRAGMA table_info(' + identifier(table) + ')')]
        schema[table] = {'sql': statement, 'columns': columns}
        for record in db.execute('SELECT rowid,* FROM ' + identifier(table)):
            values = list(record)[1:]
            data = dict(zip(columns, values))
            rows[(table, record[0])] = (
                json.dumps([encode(value) for value in values], ensure_ascii=False, allow_nan=False),
                data.get('owner'), data.get('run_id'),
                str(data['id']) if data.get('id') is not None else None)
    return schema, rows


def synchronize(cursor, before, after):
    from psycopg2.extras import Json, execute_values
    old_rows, new_rows = before[1], after[1]
    removed = [(table, rowid) for table, rowid in old_rows.keys() - new_rows.keys()]
    if removed:
        execute_values(cursor, '''DELETE FROM sawa_agent_trial.read_rows AS r
            USING (VALUES %s) AS obsolete(table_name,row_number)
            WHERE r.table_name=obsolete.table_name AND r.row_number=obsolete.row_number''', removed)
    changed = [(table, rowid, Json(json.loads(value[0])), *value[1:])
               for (table, rowid), value in new_rows.items() if old_rows.get((table, rowid)) != value]
    if changed:
        execute_values(cursor, '''INSERT INTO sawa_agent_trial.read_rows
            (table_name,row_number,payload,owner,run_id,entity_id) VALUES %s
            ON CONFLICT(table_name,row_number) DO UPDATE SET payload=excluded.payload,
            owner=excluded.owner,run_id=excluded.run_id,entity_id=excluded.entity_id''', changed)


def initialize(cursor):
    cursor.execute('ALTER TABLE sawa_agent_trial.ledger ADD COLUMN IF NOT EXISTS read_schema jsonb')
    cursor.execute('ALTER TABLE sawa_agent_trial.ledger ADD COLUMN IF NOT EXISTS read_digest text')
    cursor.execute('''CREATE TABLE IF NOT EXISTS sawa_agent_trial.read_rows (
        table_name text NOT NULL,row_number bigint NOT NULL,payload jsonb NOT NULL,
        owner text,run_id text,entity_id text,PRIMARY KEY(table_name,row_number))''')
    cursor.execute('''CREATE INDEX IF NOT EXISTS read_rows_owner
        ON sawa_agent_trial.read_rows(table_name,owner)''')
    cursor.execute('''CREATE INDEX IF NOT EXISTS read_rows_parent
        ON sawa_agent_trial.read_rows(table_name,run_id)''')
    cursor.execute('''CREATE INDEX IF NOT EXISTS read_rows_entity
        ON sawa_agent_trial.read_rows(table_name,entity_id)''')


@contextmanager
def materialize(cursor, schema, *, view, owner=None, identities=None):
    """Use SQLite's original read semantics, but fetch only the requested rows."""
    if not isinstance(schema, dict) or not {'settings', 'runs'} <= schema.keys():
        raise Conflict('実行履歴の行索引が未準備です。古い履歴へ代用せず停止しました')
    if view not in {'configuration', 'runs', 'identities', 'run', 'events', 'lookup'}:
        raise ValueError('Unknown ledger read view')
    with closing(sqlite3.connect(':memory:')) as db:
        db.row_factory = sqlite3.Row
        for definition in schema.values():
            db.execute(definition['sql'])
        loaded = set()

        def load(table, condition='', parameters=(), *, page=False):
            columns = schema[table]['columns']
            ordering = ' ORDER BY row_number'
            if page:
                ordering = ' ORDER BY (payload->>%s) COLLATE "C" DESC,row_number LIMIT 100'
                parameters = (*parameters, columns.index('created'))
            cursor.execute('SELECT row_number,payload FROM sawa_agent_trial.read_rows '
                           'WHERE table_name=%s' + condition + ordering, (table, *parameters))
            values = [(rowid, *[decode(value) for value in payload]) for rowid, payload in cursor.fetchall()]
            if values:
                db.executemany('INSERT INTO ' + identifier(table) + '(rowid,'
                               + ','.join(identifier(column) for column in columns) + ') VALUES ('
                               + ','.join('?' for _ in range(len(columns) + 1)) + ')', values)
            loaded.add(table)

        if view == 'configuration':
            load('settings')
            # Budget includes all owners, exactly as Store.budget does.
            month_index = schema['runs']['columns'].index('month')
            load('runs', ' AND payload->>%s=%s', (month_index, billing_month()))
        elif view in {'identities', 'run', 'events', 'lookup'}:
            if not isinstance(identities, list) or any(not isinstance(value, str) for value in identities):
                raise ValueError('Explicit ledger identities required')
            if view == 'lookup':
                if not isinstance(owner, str) or not owner:
                    raise ValueError('Explicit ledger owner required')
                load('target_lookups', ' AND entity_id=ANY(%s) AND owner=%s', (identities, owner))
            elif view != 'events':
                condition, parameters = ' AND entity_id=ANY(%s)', (identities,)
                if owner is not None:
                    if not isinstance(owner, str) or not owner:
                        raise ValueError('Explicit ledger owner required')
                    condition += ' AND owner=%s'
                    parameters = (*parameters, owner)
                load('runs', condition, parameters)
                identities = [row['id'] for row in db.execute('SELECT id FROM runs')]
            if view in {'run', 'events'}:
                for table in (('events',) if view == 'events' else
                              ('events', 'task_reviews', 'fax_items', 'artifacts')):
                    load(table, ' AND run_id=ANY(%s)', (identities,))
        else:
            if not isinstance(owner, str) or not owner:
                raise ValueError('Explicit ledger owner required')
            load('runs', ' AND owner=%s', (owner,), page=True)
            selected = [row['id'] for row in db.execute(
                'SELECT id FROM runs WHERE owner=? ORDER BY created DESC LIMIT 100', (owner,))]
            for table in ('task_reviews', 'fax_items'):
                load(table, ' AND run_id=ANY(%s)', (selected,))
            waiting = [row['id'] for row in db.execute(
                "SELECT id FROM runs WHERE id IN (" + ','.join('?' for _ in selected)
                + ") AND status='waiting_input'", selected)] if selected else []
            load('events', ' AND run_id=ANY(%s)', (waiting,))
        db.commit()
        db.execute('PRAGMA query_only=ON')

        def authorize(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ and table not in loaded:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(authorize)
        yield db
