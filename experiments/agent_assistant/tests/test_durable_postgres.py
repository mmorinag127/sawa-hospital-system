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


def test_readers_do_not_wait_for_writer_or_rewrite_ledger(tmp_path,database):
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    a=PostgresStore(tmp_path,database);a.set_budget(1000)
    def version():
        from contextlib import closing
        with closing(database()) as pg, pg:
            with pg.cursor() as c:
                c.execute('SELECT xmin::text FROM sawa_agent_trial.ledger WHERE id=1')
                return c.fetchone()[0]
    before=version()
    with ThreadPoolExecutor(max_workers=6) as pool:
        # The committed snapshot remains readable while an approval/update holds
        # the ledger row; no polling request may join its update-lock queue.
        with a.tx() as db:
            db.execute('UPDATE settings SET monthly_cents=2000 WHERE id=1')
            futures=[pool.submit(a.budget) for _ in range(6)]
            assert [f.result(timeout=3)['monthly_cents'] for f in futures]==[1000]*6
            assert version()==before
        assert a.budget()['monthly_cents']==2000
    after=version()
    with pytest.raises(sqlite3.OperationalError,match='readonly'):
        with a.read() as db:db.execute('UPDATE settings SET monthly_cents=9999 WHERE id=1')
    assert a.budget()['monthly_cents']==2000 and version()==after
    b=PostgresStore(tmp_path,database)
    with pytest.raises(Conflict,match='交代'):a.budget()
    assert b.budget()['monthly_cents']==2000


def test_response_snapshot_reads_once_and_preserves_committed_version(tmp_path,database):
    from concurrent.futures import ThreadPoolExecutor
    calls=[]
    def counted():
        calls.append(1)
        return database()
    store=PostgresStore(tmp_path,counted)
    store.set_budget(1000)
    run,_=store.create('operator','snapshot-request','test',['shift'],100)
    calls.clear()
    with store.snapshot():
        assert store.get(run['id'])['status']=='preparing'
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(store.update,run['id'],status='completed',spent=4,reserved=0).result(timeout=3)
        # Reads in the response cannot mix an old status with a newer decision.
        assert store.get(run['id'])['status']=='preparing'
        assert store.events(run['id'])==[]
        assert store.budget()['spent_cents']==0
        with pytest.raises(Conflict,match='読取スナップショット'):
            store.update(run['id'],status='failed')
    assert len(calls)==2  # one response snapshot plus the concurrent writer
    assert store.get(run['id'])['status']=='completed'
    assert store.budget()['spent_cents']==4


def test_legacy_raw_ledger_migrates_without_losing_business_or_cost_state(tmp_path,database):
    from contextlib import closing
    from experiments.agent_assistant.durable import decode_ledger, BLOB_HEADER
    a=PostgresStore(tmp_path,database);a.set_budget(1000)
    run,_=a.create('operator','legacy-ledger-request','test',['shift'],100,provider='gemini')
    a.update(run['id'],status='waiting_input',spent=37,reserved=63)
    a.event(run['id'],'operator_answer',{'text':'指定した厨房と釜で実施'})
    with closing(database()) as pg,pg:
        with pg.cursor() as c:
            c.execute('SELECT payload FROM sawa_agent_trial.ledger WHERE id=1')
            raw=decode_ledger(c.fetchone()[0])
            c.execute('UPDATE sawa_agent_trial.ledger SET payload=%s WHERE id=1',(raw,))
    assert a.get(run['id'])['spent']==37
    assert a.events(run['id'])[0]['payload']['text']=='指定した厨房と釜で実施'
    a.update(run['id'],status='stopped',reserved=0)
    with closing(database()) as pg,pg:
        with pg.cursor() as c:
            c.execute('SELECT payload FROM sawa_agent_trial.ledger WHERE id=1')
            assert bytes(c.fetchone()[0]).startswith(BLOB_HEADER)
    b=PostgresStore(tmp_path,database);b.recover()
    assert b.get(run['id'])['status']=='stopped'
    assert b.budget()['spent_cents']==37 and b.budget()['reserved_cents']==0
    assert b.events(run['id'])[0]['payload']['text']=='指定した厨房と釜で実施'
    with pytest.raises(Conflict,match='交代'):a.get(run['id'])


def test_originals_restore_from_known_raw_or_compressed_storage_and_reject_corruption(tmp_path,database):
    from contextlib import closing
    from experiments.agent_assistant.durable import BLOB_HEADER
    a=PostgresStore(tmp_path,database);a.set_budget(1000)
    run,_=a.create('operator','file-codec-request','test',['hospital'],100)
    original=tmp_path/'runs'/run['id']/'before/hospital/verified.pdf'
    original.parent.mkdir(parents=True);raw=b'%PDF-approved-source\n'+b'trial data'*100_000;original.write_bytes(raw)
    a.persist_files(run['id'])
    with closing(database()) as pg,pg:
        with pg.cursor() as c:
            c.execute('SELECT path,digest,payload FROM sawa_agent_trial.files')
            path,digest,payload=c.fetchone()
            assert digest==hashlib.sha256(raw).hexdigest()
            assert bytes(payload).startswith(BLOB_HEADER) and len(payload)<len(raw)//10
    original.unlink();a.hydrate();assert original.read_bytes()==raw
    with closing(database()) as pg,pg:
        with pg.cursor() as c:c.execute('UPDATE sawa_agent_trial.files SET payload=%s WHERE path=%s',(raw,path))
    original.unlink();a.hydrate();assert original.read_bytes()==raw
    with closing(database()) as pg,pg:
        with pg.cursor() as c:c.execute('UPDATE sawa_agent_trial.files SET payload=%s WHERE path=%s',(b'corrupted',path))
    with pytest.raises(Conflict):a.hydrate()
    assert original.read_bytes()==raw


def test_corrupt_legacy_ledger_is_never_reinitialized(tmp_path,database):
    from contextlib import closing
    from experiments.agent_assistant.durable import decode_ledger
    a=PostgresStore(tmp_path,database);a.set_budget(1000)
    run,_=a.create('operator','corrupt-legacy-request','test',['shift'],100)
    with closing(database()) as pg,pg:
        with pg.cursor() as c:
            c.execute('SELECT payload FROM sawa_agent_trial.ledger WHERE id=1')
            damaged=decode_ledger(c.fetchone()[0])[:-100]
            c.execute('UPDATE sawa_agent_trial.ledger SET payload=%s WHERE id=1',(damaged,))
    with pytest.raises(Conflict,match='破損'):a.get(run['id'])
    with pytest.raises(Conflict,match='破損'):PostgresStore(tmp_path,database)
    with closing(database()) as pg,pg:
        with pg.cursor() as c:
            c.execute('SELECT payload FROM sawa_agent_trial.ledger WHERE id=1')
            assert bytes(c.fetchone()[0])==damaged
