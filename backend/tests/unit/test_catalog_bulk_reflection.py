from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from src.maintenance import formal_migration_schema as catalog


METHODS = {
    'columns': 'columns', 'pk': 'pk_constraint', 'unique': 'unique_constraints',
    'fk': 'foreign_keys', 'checks': 'check_constraints', 'indexes': 'indexes',
}


def reflected_fixture():
    return {name: {
        'columns': [{'name': 'id', 'type': sa.BigInteger(), 'nullable': False, 'default': None},
                    {'name': 'amount', 'type': sa.Numeric(18, 6), 'nullable': True, 'default': '0',
                     'comment': 'retained', 'computed': {'sqltext': 'id + 1', 'persisted': True}}],
        'pk': {'name': name + '_pk', 'constrained_columns': ['id'], 'comment': None},
        'unique': [{'name': name + '_uq', 'column_names': ['amount'], 'dialect_options': {'postgresql_nulls_not_distinct': True}}],
        'fk': [{'name': name + '_fk', 'constrained_columns': ['id'], 'referred_schema': None,
                'referred_table': 'a', 'referred_columns': ['id'], 'options': {'deferrable': True, 'initially': 'DEFERRED'}}],
        'checks': [{'name': name + '_check', 'sqltext': 'amount >= 0'}],
        'indexes': [{'name': name + '_idx', 'column_names': ['amount'], 'unique': False,
                     'include_columns': ['id'], 'dialect_options': {'postgresql_include': ['id'], 'postgresql_where': 'amount > 0'}}],
    } for name in ['z', 'a']}


class Result:
    def __init__(self, rows): self.rows = rows
    def scalars(self): return self
    def all(self): return self.rows
    def mappings(self): return self.rows
    def __iter__(self): return iter(self.rows)


class Connection:
    dialect = SimpleNamespace(name='postgresql')
    def __init__(self):
        self.sql = []
        self.unsupported = []
        self.types = []
        self.routines = []
        self.triggers = []
        self.native = [{'table_name': 'a', 'native_type': 'numeric(18,6)', 'attnotnull': False}]
    def exec_driver_sql(self, sql):
        self.sql.append(sql)
        assert sql.lstrip().startswith('SELECT')
        if sql == 'SELECT version_num FROM alembic_version': return Result(['0001'])
        if 'c.relkind IN' in sql: return Result(self.unsupported)
        if 'FROM pg_type' in sql: return Result(self.types)
        if 'FROM pg_proc' in sql: return Result(self.routines)
        if 'FROM pg_trigger t JOIN pg_class' in sql: return Result(self.triggers)
        return Result(self.native if 'FROM pg_attribute' in sql else [])


class Inspector:
    def __init__(self, tables):
        self.tables, self.calls = tables, []
        self.missing = None
        self.failure = None
        self.materialized = []
    def get_table_names(self): return list(self.tables)
    def get_materialized_view_names(self): return self.materialized
    def get_view_names(self): return ['view_z', 'view_a']
    def get_view_definition(self, name): return 'SELECT id FROM a /* ' + name + ' */'
    def __getattr__(self, name):
        for key, suffix in METHODS.items():
            if name == 'get_multi_' + suffix:
                def multi(*, schema, filter_names, scope, kind, key=key):
                    self.calls.append((key, schema, filter_names))
                    assert schema is None and filter_names == sorted(self.tables)
                    assert scope is sa.engine.reflection.ObjectScope.ANY
                    assert kind is sa.engine.reflection.ObjectKind.ANY
                    if self.failure == key: raise LookupError('reflection failed')
                    return {(None, table): deepcopy(values[key]) for table, values in self.tables.items()
                            if (key, table) != self.missing}
                return multi
        raise AssertionError('unexpected single-table call: ' + name)


def test_full_reflected_fixture_native_views_and_order_equal(monkeypatch):
    tables = reflected_fixture()
    inspector, connection = Inspector(tables), Connection()
    monkeypatch.setattr(catalog.sa, 'inspect', lambda conn: inspector)
    actual = catalog.read_catalog(connection)
    expected = {'tables': {name: catalog._clean(tables[name]) for name in sorted(tables)},
                'native': connection.native,
                'views': {name: inspector.get_view_definition(name) for name in inspector.get_view_names()}}
    assert actual == expected
    assert list(actual['tables']) == ['a', 'z']
    assert [call[0] for call in inspector.calls] == list(METHODS)
    assert len(connection.sql) == 8


def test_empty_tables_do_not_trigger_unfiltered_multi_reflection(monkeypatch):
    inspector = Inspector({})
    monkeypatch.setattr(catalog.sa, 'inspect', lambda conn: inspector)
    assert catalog.read_catalog(Connection())['tables'] == {}
    assert inspector.calls == []


@pytest.mark.parametrize('key', METHODS)
@pytest.mark.parametrize('failure', [False, True])
def test_missing_entries_and_reflection_errors_stop(monkeypatch, key, failure):
    inspector = Inspector(reflected_fixture())
    inspector.failure = key if failure else None
    inspector.missing = None if failure else (key, 'a')
    monkeypatch.setattr(catalog.sa, 'inspect', lambda conn: inspector)
    with pytest.raises(LookupError): catalog.read_catalog(Connection())


@pytest.mark.parametrize('kind', ['materialized', 'unsupported', 'types', 'routines'])
def test_unsupported_objects_still_stop_before_reflection(monkeypatch, kind):
    inspector, connection = Inspector(reflected_fixture()), Connection()
    if kind == 'materialized': inspector.materialized = ['mat']
    else: setattr(connection, kind, [('custom', 'enum')] if kind == 'types' else ['unsupported'])
    monkeypatch.setattr(catalog.sa, 'inspect', lambda conn: inspector)
    with pytest.raises(RuntimeError, match='unsupported'): catalog.read_catalog(connection)
    assert inspector.calls == []


def test_require_head_success_and_constraint_drift_rejection(monkeypatch, tmp_path):
    tables = reflected_fixture()
    tables['alembic_version'] = deepcopy(tables['a'])
    inspector, connection = Inspector(tables), Connection()
    monkeypatch.setattr(catalog.sa, 'inspect', lambda conn: inspector)
    monkeypatch.setattr(catalog, 'formal_head_revision', lambda: '0001')
    old = {'tables': catalog._clean(tables), 'native': connection.native, 'views': {
        name: inspector.get_view_definition(name) for name in inspector.get_view_names()}}
    shapes = catalog.table_shapes(old, 'postgresql')
    contract = tmp_path / 'contract.json'
    contract.write_text(json.dumps({'revisions': {'postgresql': {'0001': {
        'tables': {name: name for name in tables}, 'views': old['views']}}}, 'shapes': shapes}))
    monkeypatch.setattr(catalog, 'CONTRACT', contract)
    assert catalog.require_formal_head(connection) is None
    tables['a']['checks'][0]['sqltext'] = 'amount > 0'
    with pytest.raises(RuntimeError, match='mismatch: checks'):
        catalog.require_formal_head(connection)


def test_sqlite_catalog_matches_single_table_inspector_and_is_read_only():
    engine = sa.create_engine('sqlite://')
    with engine.connect() as conn:
        conn.exec_driver_sql('CREATE TABLE parent (id INTEGER PRIMARY KEY, amount NUMERIC(18,6) CHECK(amount>=0), UNIQUE(amount))')
        conn.exec_driver_sql('CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id))')
        conn.exec_driver_sql('CREATE INDEX child_parent ON child(parent_id)')
        conn.exec_driver_sql('CREATE VIEW parent_view AS SELECT id FROM parent')
        inspector = sa.inspect(conn)
        expected = {'tables': {name: catalog._clean({key: getattr(inspector, 'get_' + method)(name)
                    for key, method in METHODS.items()}) for name in sorted(inspector.get_table_names())},
                    'native': catalog._clean([dict(row) for row in conn.exec_driver_sql(
                        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").mappings()]),
                    'views': {name: inspector.get_view_definition(name) for name in inspector.get_view_names()}}
        statements = []
        def track(conn, cursor, statement, parameters, context, many): statements.append(statement)
        sa.event.listen(conn, 'before_cursor_execute', track)
        try: assert catalog.read_catalog(conn) == expected
        finally: sa.event.remove(conn, 'before_cursor_execute', track)
        assert all(sql.lstrip().upper().startswith(('SELECT', 'PRAGMA')) for sql in statements)
    engine.dispose()


@pytest.mark.parametrize('field,value', [('function_definition', 'changed body'), ('tgenabled', 'D')])
def test_trigger_body_and_enabled_state_are_part_of_formal_catalog(monkeypatch, tmp_path, field, value):
    tables = reflected_fixture()
    tables['alembic_version'] = deepcopy(tables['a'])
    inspector, connection = Inspector(tables), Connection()
    connection.triggers = [{'table_name': 'a', 'trigger_name': 'owned',
                            'function_definition': 'original body', 'tgenabled': 'O'}]
    monkeypatch.setattr(catalog.sa, 'inspect', lambda _connection: inspector)
    monkeypatch.setattr(catalog, 'formal_head_revision', lambda: '0001')
    observed = catalog.read_catalog(connection)
    shapes = catalog.table_shapes(observed, 'postgresql')
    contract = tmp_path / 'contract.json'
    contract.write_text(json.dumps({'revisions': {'postgresql': {'0001': {
        'tables': {name: name for name in tables}, 'views': observed['views']}}}, 'shapes': shapes}))
    monkeypatch.setattr(catalog, 'CONTRACT', contract)
    catalog.require_formal_head(connection)
    connection.triggers[0][field] = value
    with pytest.raises(RuntimeError, match='mismatch: native'):
        catalog.require_formal_head(connection)
