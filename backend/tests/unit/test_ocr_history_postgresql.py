"""Run against an explicitly supplied disposable PostgreSQL instance."""
from copy import deepcopy
from datetime import datetime
import json
import os
import gc
import tracemalloc
from statistics import median
from time import perf_counter

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, select
from sqlalchemy.orm import Session

from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.services.ocr_history_read import read_history


def test_postgresql_projection_values_storage_and_memory(record_property):
    url = os.environ.get('OCR_HISTORY_PROJECTION_POSTGRES_URL')
    if not url:
        pytest.skip('Requires explicit disposable PostgreSQL URL')
    engine = create_engine(url)
    source = OrderOcrEvidenceRun.__table__
    table = Table(source.name, MetaData(), *[
        Column(column.name, column.type) for column in source.columns
    ], prefixes=['TEMPORARY'])
    payloads = [
        {'diagnostic_pipeline_output': {'blob': 'x' * 1000000},
         'raw': ['1', '26', '0'], 'nested': {'diagnostic_pipeline_output': 'keep'},
         'unknown': None, 'enabled': False, 'text': '日本語'},
        {}, None, [], [{'diagnostic_pipeline_output': 'keep'}],
        'text', 123, 0, True, False,
        {'diagnostic_pipeline_output': {}, 'unknown': '\u0000'},
    ]
    payloads.extend({'diagnostic_pipeline_output': {'blob': 'x' * 250000}, 'raw': ['1', '26', '0']}
                    for _ in range(20))
    try:
        with engine.connect() as connection:
            table.create(connection)
            connection.execute(table.insert(), [
                {'id': f'E{i:02}', 'order_id': 'O', 'payload_json': deepcopy(payload),
                 'created_at': datetime(2026, 10, 20)}
                for i, payload in enumerate(payloads)
            ])
            with Session(bind=connection) as session:
                full_statement = select(table).where(table.c.order_id == 'O').order_by(
                    table.c.created_at.desc(), table.c.id.desc())
                before = session.execute(full_statement).mappings().all()
                projected = read_history(session, 'O')
                assert [row.id for row in projected] == [row['id'] for row in before]
                for old, new in zip(before, projected):
                    expected = dict(old)
                    payload = expected['payload_json']
                    if isinstance(payload, dict):
                        expected['payload_json'] = {k: v for k, v in payload.items()
                                                    if k != 'diagnostic_pipeline_output'}
                    assert vars(new) == expected
                assert read_history(session, 'missing') == []
                assert session.execute(full_statement).mappings().all() == before
                assert not session.dirty and not session.identity_map
                full_bytes = len(json.dumps([r['payload_json'] for r in before], ensure_ascii=False).encode())
                projected_bytes = len(json.dumps([r.payload_json for r in projected], ensure_ascii=False).encode())
                assert projected_bytes < full_bytes / 100
                durations = {'full': [], 'projected': []}
                for _ in range(3):
                    start = perf_counter()
                    session.execute(full_statement).mappings().all()
                    durations['full'].append(perf_counter() - start)
                    start = perf_counter()
                    read_history(session, 'O')
                    durations['projected'].append(perf_counter() - start)
                peaks = {}
                for name, read in [('full', lambda: session.execute(full_statement).mappings().all()),
                                   ('projected', lambda: read_history(session, 'O'))]:
                    gc.collect()
                    tracemalloc.start()
                    value = read()
                    peaks[name] = tracemalloc.get_traced_memory()[1]
                    tracemalloc.stop()
                    del value
                assert peaks['projected'] < peaks['full'] / 2
                result = {'full_retained_json_bytes': full_bytes, 'projected_retained_json_bytes': projected_bytes,
                          'full_peak_python_bytes': peaks['full'], 'projected_peak_python_bytes': peaks['projected'],
                          'full_median_ms': median(durations['full']) * 1000,
                          'projected_median_ms': median(durations['projected']) * 1000}
                for key, value in result.items():
                    record_property(key, value)
                print(json.dumps(result))
            connection.rollback()
    finally:
        engine.dispose()
