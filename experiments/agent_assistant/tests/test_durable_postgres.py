"""Actual PostgreSQL persistence and fencing; opt in with own disposable DB."""
import hashlib
import os
from pathlib import Path
import shutil

import pytest

from experiments.agent_assistant.durable import PostgresStore
from experiments.agent_assistant.store import Conflict


@pytest.fixture
def database():
    dsn = os.environ.get('SAWA_AGENT_TEST_POSTGRES_DSN')
    if not dsn:
        pytest.skip('Dedicated PostgreSQL required')
    import psycopg2
    def connect(): return psycopg2.connect(dsn)
    pg=connect()
    try:
        with pg:
            with pg.cursor() as c:
                c.execute('SELECT current_database()')
                assert c.fetchone()[0].startswith('agent_trial')
                c.execute('DROP SCHEMA IF EXISTS sawa_agent_trial CASCADE')
                c.execute('DROP TABLE IF EXISTS public.sawa_agent_trial_ledger,public.sawa_agent_trial_files')
        yield connect
    finally:
        pg.close()


def test_restart_keeps_costs_before_images_outputs_and_fences_previous(tmp_path,database):
    a=PostgresStore(tmp_path,database);a.set_budget(10000)
    with database() as pg:
        with pg.cursor() as c:
            c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'sawa_agent%'")
            assert c.fetchall()==[]
    run,_=a.create('operator','request-12345','test',['hospital'],200,provider='gemini')
    ident=run['id'];root=tmp_path/'runs'/ident
    original=root/'before/hospital/database.sqlite3'
    original.parent.mkdir(parents=True);original.write_bytes(b'original-copy')
    a.persist_files(ident)
    output=root/'artifacts/sealed';output.parent.mkdir();output.write_bytes(b'official-output')
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    with a.tx() as db:db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?)',('sealed',ident,str(output),'label.xlsx',digest,output.stat().st_size))
    a.update(ident,status='waiting_input',spent=47,reserved=153)
    shutil.rmtree(root)
    b=PostgresStore(tmp_path,database);b.recover()
    recovered=b.get(ident)
    assert recovered['status']=='reconciliation_required'
    assert recovered['generation']==run['generation']+1
    assert recovered['spent']==47 and recovered['reserved']==153
    assert original.read_bytes()==b'original-copy'
    assert (root/'data/hospital/database.sqlite3').read_bytes()==b'original-copy'
    assert output.read_bytes()==b'official-output'
    with pytest.raises(Conflict,match='交代'):a.update(ident,status='completed')
    with pytest.raises(Conflict,match='交代'):a.persist_files(ident)
    original.write_bytes(b'corrupted')
    with pytest.raises(Conflict,match='上書き'):b.persist_files(ident)


def test_completed_artifacts_survive_without_claiming_active_work_resumed(tmp_path,database):
    a=PostgresStore(tmp_path,database);a.set_budget(1000)
    run,_=a.create('operator','request-23456','test',['hospital'],100)
    a.update(run['id'],status='completed',spent=12,reserved=0)
    b=PostgresStore(tmp_path,database);b.recover()
    assert b.get(run['id'])['status']=='completed'
    assert b.get(run['id'])['spent']==12
