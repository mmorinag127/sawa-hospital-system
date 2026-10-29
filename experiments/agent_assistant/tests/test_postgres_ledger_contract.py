from decimal import Decimal
from types import SimpleNamespace

from experiments.agent_assistant.postgres_ledger import Connection, Result, statement


def test_bound_parameters_never_replace_question_marks_inside_quoted_values():
    assert statement("SELECT '?' AS literal, \"question?\" FROM events WHERE run_id=?") == "SELECT '?' AS literal, \"question?\" FROM events WHERE run_id=%s"
    assert statement('INSERT OR IGNORE INTO events(run_id,payload) VALUES(?,?);') == 'INSERT INTO events(run_id,payload) VALUES(%s,%s) ON CONFLICT DO NOTHING'


def test_native_row_keeps_sqlite_mapping_and_integer_sum_contract():
    cursor = SimpleNamespace(description=[SimpleNamespace(name=name) for name in ('id', 'rowid', 'total')], rowcount=1)
    result = Result(cursor, include_rowid=False)
    row = result._row(('run', 123, Decimal(17)))
    assert dict(row) == {'id': 'run', 'total': 17}
    assert row[0] == 'run' and row['total'] == 17 and list(row) == ['run', 17]
    assert type(row['total']) is int


def test_positional_insert_names_canonical_columns_and_keeps_values_bound():
    calls = []
    cursor = SimpleNamespace(description=None, rowcount=1,
        execute=lambda query, parameters: calls.append((query, parameters)), close=lambda: None)
    connection = Connection(SimpleNamespace(cursor=lambda: cursor), {'commands': ['run_id', 'id', 'request', 'status', 'result']})
    values = ('run', "quote' ? payload", '{}', 'executing', None)
    connection.execute('INSERT INTO commands VALUES(?,?,?,?,?)', values)
    assert calls == [('INSERT INTO commands("run_id","id","request","status","result") VALUES(%s,%s,%s,%s,%s)', values)]
    connection.close()


def test_distinct_aggregates_with_identical_driver_names_keep_both_positions():
    cursor = SimpleNamespace(description=[SimpleNamespace(name='coalesce'), SimpleNamespace(name='coalesce')], rowcount=1)
    row = Result(cursor, include_rowid=False)._row((Decimal(17), Decimal(83)))
    spent, reserved = row
    assert (spent, reserved) == (17, 83)
    assert row[1] == 83
