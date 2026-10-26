import math
import sqlite3

import pytest

from experiments.agent_assistant import ledger_reads
from experiments.agent_assistant.store import Conflict, Store


def test_row_codec_preserves_sqlite_scalar_types():
    for value in (None, '', '数量は手動訂正', 0, -3, 2.5, b'\x00\xff', float('inf'), -float('inf')):
        restored = ledger_reads.decode(ledger_reads.encode(value))
        assert type(restored) is type(value)
        assert restored == value
    assert math.isnan(ledger_reads.decode(ledger_reads.encode(float('nan'))))
    with pytest.raises(Conflict):
        ledger_reads.decode({'unexpected': 'value'})


def test_capture_keeps_rowid_columns_and_original_values(tmp_path):
    store = Store(tmp_path)
    with store.tx() as db:
        db.execute('CREATE TABLE codec(value BLOB, number REAL)')
        db.execute('INSERT INTO codec(rowid,value,number) VALUES(17,?,?)', (b'\x00\xff', float('inf')))
    with store.read() as db:
        schema, rows = ledger_reads.capture(db)
    assert schema['codec']['columns'] == ['value', 'number']
    import json
    assert [ledger_reads.decode(value) for value in json.loads(rows[('codec', 17)][0])] == [b'\x00\xff', float('inf')]


def test_unprepared_or_unknown_read_never_returns_empty_replacement():
    with pytest.raises(Conflict):
        with ledger_reads.materialize(None, None, view='run', identities=['requested']):
            pytest.fail('unprepared index must stop')
    with pytest.raises(ValueError):
        with ledger_reads.materialize(None, {'settings': {}, 'runs': {}}, view='unknown'):
            pytest.fail('unknown view must stop')
