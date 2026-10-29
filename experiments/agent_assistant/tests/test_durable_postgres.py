"""Actual PostgreSQL persistence and fencing; opt in with own disposable DB."""
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3

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


def seed_legacy(database, payload):
    with database() as pg, pg.cursor() as cursor:
        cursor.execute('CREATE SCHEMA sawa_agent_trial')
        cursor.execute('CREATE TABLE sawa_agent_trial.ledger(id integer PRIMARY KEY,epoch bigint NOT NULL,payload bytea)')
        cursor.execute('INSERT INTO sawa_agent_trial.ledger VALUES(1,0,%s)', (payload,))


@pytest.mark.parametrize('compressed', [False, True])
def test_native_migration_preserves_sequence_budget_question_and_raw_archive(tmp_path, database, compressed):
    from experiments.agent_assistant.store import Store
    from experiments.agent_assistant.durable import encode_ledger
    old = Store(tmp_path/'legacy')
    old.set_budget(1000)
    run, _ = old.create('operator', 'migration-sequence', 'original instruction', ['hospital'], 100)
    old.update(run['id'], status='waiting_input', spent=17, reserved=83)
    old.event(run['id'], 'tool_result', {'result': {'question': 'original question', 'command_id': 'original-call', 'options': []}})
    with old.tx() as db:
        db.execute("INSERT INTO events(id,run_id,kind,payload,created) VALUES(999,?,'notice','{}','old')", (run['id'],))
        db.execute('DELETE FROM events WHERE id=999')
    raw = old.path.read_bytes()
    payload = encode_ledger(raw) if compressed else raw
    seed_legacy(database, payload)
    store = PostgresStore(tmp_path/'runtime', database)
    assert store.get(run['id']) == old.get(run['id'])
    assert store.events(run['id']) == old.events(run['id'])
    assert store.budget() == old.budget()
    assert store.pending_question(run['id'])['question_id'] == 'original-call'
    store.event(run['id'], 'notice', {'message': 'new event'})
    assert store.latest_event(run['id'], 'notice')['id'] > 999
    with database() as pg, pg.cursor() as cursor:
        cursor.execute('SELECT payload,storage_format FROM sawa_agent_trial.ledger WHERE id=1')
        saved, storage = cursor.fetchone()
        assert bytes(saved) == payload and storage == 'postgres-rows-v1'


def test_metadata_reads_and_other_run_update_do_not_hydrate_completed_history(tmp_path, database, monkeypatch):
    from experiments.agent_assistant import durable
    store = PostgresStore(tmp_path, database)
    store.set_budget(1000)
    first, _ = store.create('operator', 'old-completed', 'old', ['hospital'], 100)
    store.update(first['id'], status='completed', reserved=0)
    second, _ = store.create('operator', 'current-working', 'current', ['hospital'], 100)
    checkpointed = []
    from experiments.agent_assistant import working_copy
    original = working_copy.checkpoint
    def checkpoint(root, ident, *args, **kwargs):
        checkpointed.append(ident)
        return original(root, ident, *args, **kwargs)
    monkeypatch.setattr(working_copy, 'checkpoint', checkpoint)
    monkeypatch.setattr(durable, 'encode_ledger', lambda *_: pytest.fail('ordinary update serialized entire ledger'))
    store.update(second['id'], spent=4)
    assert checkpointed == [second['id']]
    replacement = PostgresStore(tmp_path, database)
    monkeypatch.setattr(replacement, '_hydrate_run', lambda *_args, **_kwargs: pytest.fail('metadata hydrated working DB'))
    assert replacement.get(first['id'])['status'] == 'completed'
    assert replacement.run_history('operator')['items']
    assert replacement.event_page(second['id'])['events'] == []


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
    working=root/'data/hospital/database.sqlite3'
    working.parent.mkdir(parents=True)
    with sqlite3.connect(working) as db:
        db.execute('CREATE TABLE quantities(value INTEGER)')
        db.execute('INSERT INTO quantities VALUES(21)')
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
    assert not original.exists()  # metadata reads never restore unrelated files
    b.require_working_copy(ident)
    assert original.read_bytes()==b'original-copy'
    with sqlite3.connect(root/'data/hospital/database.sqlite3') as db:
        assert db.execute('SELECT value FROM quantities').fetchone()[0] == 21
    b.require_working_copy(ident)
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


def test_file_digests_are_batched_without_allowing_original_overwrites(tmp_path, database):
    import psycopg2
    from psycopg2.extensions import cursor

    with database() as connection:
        dsn = connection.dsn
    statements = []

    class ObservedCursor(cursor):
        def execute(self, query, parameters=None):
            statements.append(query)
            return super().execute(query, parameters)

    store = PostgresStore(tmp_path, lambda: psycopg2.connect(dsn, cursor_factory=ObservedCursor))
    store.set_budget(1000)
    run, _ = store.create('operator', 'batched-originals', 'synthetic persistence', ['hospital'], 100)
    directory = tmp_path / 'runs' / run['id'] / 'before'
    directory.mkdir(parents=True)
    for index in range(20):
        (directory / str(index)).write_bytes(('synthetic original ' + str(index)).encode())
    store.persist_files(run['id'])
    statements.clear()
    store.persist_files(run['id'])
    assert sum('SELECT path,digest FROM sawa_agent_trial.files WHERE path=ANY' in sql for sql in statements) == 1
    assert not any('SELECT digest FROM sawa_agent_trial.files' in sql for sql in statements)
    assert not any(sql.startswith('INSERT INTO sawa_agent_trial.files') for sql in statements)
    (directory / '0').write_bytes(b'changed original')
    with pytest.raises(Conflict, match='上書き'):
        store.persist_files(run['id'])
    with database() as connection, connection.cursor() as query:
        query.execute('SELECT digest FROM sawa_agent_trial.files WHERE path=%s', (f"runs/{run['id']}/before/0",))
        assert query.fetchone()[0] == hashlib.sha256(b'synthetic original 0').hexdigest()


def test_unchanged_checkpoints_skip_compression_but_changed_bytes_are_saved(tmp_path, database, monkeypatch):
    from experiments.agent_assistant import durable

    store = PostgresStore(tmp_path, database)
    store.set_budget(1000)
    run, _ = store.create('operator', 'checkpoint-reuse', 'synthetic persistence', ['hospital'], 100)
    path = tmp_path / 'runs' / run['id'] / 'data' / 'hospital' / 'owned.txt'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'owned working original')
    real_encode = durable.encode_blob
    encoded = []

    def encode(content):
        encoded.append(content)
        return real_encode(content)

    monkeypatch.setattr(durable, 'encode_blob', encode)
    store.event(run['id'], 'notice', {'step': 1})
    store.event(run['id'], 'notice', {'step': 2})
    assert encoded.count(b'owned working original') == 1
    path.write_bytes(b'owned working changed')
    store.event(run['id'], 'notice', {'step': 3})
    assert encoded.count(b'owned working changed') == 1
    with database() as connection, connection.cursor() as query:
        query.execute('SELECT digest,payload FROM sawa_agent_trial.files WHERE path LIKE %s',
                      (f"runs/{run['id']}/checkpoints/%",))
        contents = {digest: durable.decode_blob(payload, expected=digest) for digest, payload in query}
    assert contents == {hashlib.sha256(content).hexdigest(): content
                        for content in (b'owned working original', b'owned working changed')}


def test_indexed_views_preserve_results_without_decoding_entire_ledger(tmp_path, database, monkeypatch):
    from experiments.agent_assistant import durable
    store = PostgresStore(tmp_path, database)
    store.set_budget(10000)
    first, _ = store.create('operator', 'index-first', 'first', ['hospital'], 100)
    store.update(first['id'], status='completed', spent=17, reserved=0)
    second, _ = store.create('other', 'index-second', 'second', ['shift'], 200)
    store.event(first['id'], 'notice', {'original': 'unchanged'})
    with store.tx() as db:
        db.execute("INSERT INTO target_lookups VALUES(?,?,?,?,'completed',?)",
                   ('lookup-first', 'operator', 'shift_plan', '{}', '{"value":7}'))
    with store.snapshot():
        expected = (store.get(first['id']), store.listing('operator'), store.budget(), store.events(first['id']))
    def forbidden(*args, **kwargs):
        pytest.fail('page read decoded the complete compressed SQLite ledger')
    monkeypatch.setattr(durable, 'decode_ledger', forbidden)
    assert (store.get(first['id']), store.listing('operator'), store.budget(), store.events(first['id'])) == expected
    assert expected[2]['reserved_cents'] == second['reserved']
    with pytest.raises(KeyError):
        store.get(first['id'], 'other')
    assert store.target_lookup('lookup-first', 'operator')['payload'] == '{"value":7}'
    with pytest.raises(KeyError):
        store.target_lookup('lookup-first', 'other')
    with store.snapshot(view='run', owner='operator', identities=[first['id']]):
        assert store.get(first['id']) == expected[0]
        assert store.events(first['id']) == expected[3]
        with store.read() as db:
            assert db.execute('SELECT * FROM commands WHERE run_id=?', (first['id'],)).fetchall() == []


def test_native_rows_reject_legacy_overwrite_and_unknown_storage_format(tmp_path, database):
    import psycopg2
    store = PostgresStore(tmp_path, database)
    store.set_budget(1000)
    run, _ = store.create('operator', 'index-drift', 'original', ['hospital'], 100)
    with pytest.raises(psycopg2.Error, match='Legacy ledger writes disabled'):
        with database() as pg, pg.cursor() as cursor:
            cursor.execute('UPDATE sawa_agent_trial.ledger SET payload=payload WHERE id=1')
    with database() as pg, pg.cursor() as cursor:
        cursor.execute("UPDATE sawa_agent_trial.ledger SET storage_format='unknown' WHERE id=1")
    with pytest.raises(Conflict, match='交代'):
        store.get(run['id'])
    with pytest.raises(Conflict, match='交代'):
        store.update(run['id'], status='completed')
    with database() as pg, pg.cursor() as cursor:
        cursor.execute("UPDATE sawa_agent_trial.ledger SET storage_format='postgres-rows-v1' WHERE id=1")
    store.update(run['id'], status='completed', spent=31, reserved=0)
    assert store.get(run['id'])['spent'] == 31
    assert store.get(run['id'])['instruction'] == 'original'


def test_indexed_snapshot_stays_on_one_commit_and_preserves_fencing(tmp_path, database):
    store = PostgresStore(tmp_path, database)
    store.set_budget(1000)
    run, _ = store.create('operator', 'index-snapshot', 'original', ['hospital'], 100)
    with store.snapshot(view='run', owner='operator', identities=[run['id']]):
        assert store.get(run['id'])['spent'] == 0
        import contextvars
        contextvars.Context().run(store.update, run['id'], spent=23)
        assert store.get(run['id'])['spent'] == 0
    assert store.get(run['id'])['spent'] == 23
    PostgresStore(tmp_path, database)
    with pytest.raises(Conflict, match='交代'):
        store.get(run['id'])


def test_legacy_before_image_is_never_used_as_a_resume_copy(tmp_path, database):
    a=PostgresStore(tmp_path,database);a.set_budget(1000)
    run,_=a.create('operator','legacy-request','test',['shift'],100)
    original=tmp_path/'runs'/run['id']/'before/shift/database.sqlite3'
    original.parent.mkdir(parents=True);original.write_bytes(b'original-only')
    a.persist_files(run['id'])
    shutil.rmtree(original.parents[2])
    b=PostgresStore(tmp_path,database);b.recover()
    assert not original.exists()
    with pytest.raises(Conflict,match='開始前データ'):b.require_working_copy(run['id'])
    assert original.read_bytes()==b'original-only'
    assert not (original.parents[2]/'data').exists()
    with pytest.raises(Conflict,match='開始前データ'):b.require_working_copy(run['id'])
    assert b.get(run['id'])['reserved']==100


def test_pre_checkpoint_ledger_starts_and_only_legacy_run_is_blocked(tmp_path,database):
    from experiments.agent_assistant.store import Store
    from experiments.agent_assistant.durable import encode_ledger
    old=Store(tmp_path/'legacy');old.set_budget(1000)
    run,_=old.create('operator','pre-checkpoint-request','test',['shift'],100)
    old.update(run['id'],status='waiting_input',spent=17,reserved=83)
    with old.read() as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='working_copies'").fetchone()
    seed_legacy(database, encode_ledger(old.path.read_bytes()))
    current=PostgresStore(tmp_path/'runtime',database);current.recover()
    assert current.get(run['id'])['spent']==17 and current.get(run['id'])['reserved']==83
    assert current.get(run['id'])['status']=='reconciliation_required'
    with pytest.raises(Conflict,match='開始前データ'):current.require_working_copy(run['id'])


def test_restart_recovers_committed_wal_and_exact_question_through_resume(tmp_path, database, monkeypatch):
    import asyncio
    import httpx
    from experiments.agent_assistant.sandbox import Sandbox
    from experiments.agent_assistant.tools import Tools
    from experiments.agent_assistant.providers import Providers
    from experiments.agent_assistant.mcp_server import build_mcp
    from experiments.agent_assistant.conversation import pending_question
    from experiments.agent_assistant.gemini import command_identity
    from experiments.agent_assistant.tests.test_gemini import interaction
    for key,value in {'GEMINI_API_KEY':'synthetic','GEMINI_AGENT_MODEL':'gemini-test',
                      'GEMINI_AGENT_INPUT_USD_PER_MILLION':'1','GEMINI_AGENT_OUTPUT_USD_PER_MILLION':'2'}.items():
        monkeypatch.setenv(key,value)
    a=PostgresStore(tmp_path,database);a.set_budget(1000)
    run,_=a.create('operator','wal-restart-request','test',['shift'],100,provider='gemini')
    ident=run['id'];root=tmp_path/'runs'/ident
    original=root/'before/shift/database.sqlite3';original.parent.mkdir(parents=True)
    with sqlite3.connect(original) as db:
        db.execute('CREATE TABLE quantities(value INTEGER)');db.execute('INSERT INTO quantities VALUES(7)')
    working=root/'data/shift/database.sqlite3';working.parent.mkdir(parents=True)
    shutil.copyfile(original,working)
    a.persist_files(ident)
    db=sqlite3.connect(working)
    try:
        db.execute('PRAGMA journal_mode=WAL');db.execute('UPDATE quantities SET value=21');db.commit()
        args={'question':'この内容でよいですか？','options':['はい','いいえ']}
        command=command_identity('turn','question')
        a.command(ident,command,{'name':'ask_operator','arguments':args})
        drawing=b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="10" height="10"/></svg>'
        digest=hashlib.sha256(drawing).hexdigest()
        drawing_path=root/'question-drawings'/(digest+'.svg');drawing_path.parent.mkdir()
        drawing_path.write_bytes(drawing)
        result={'waiting_input':True,'question':args['question'],'options':args['options'],'command_id':command,
                'question_map':{'sha256':digest,'view_box':[0,0,100,100],'hotspots':[]}}
        a.finish_command(ident,command,result)
        a.event(ident,'operator_wait',{'command_id':command,'result':result})
        a.event(ident,'gemini_dispatch',{'id':'dispatch'})
        a.event(ident,'gemini_response',{'id':'turn','dispatch':'dispatch'})
        response=interaction('turn',[('question','ask_operator',args)])
        a.event(ident,'gemini_state',{'interaction':'turn','calls':response['steps'],'results':[],'index':0,'pending':command})
        a.update(ident,status='waiting_input',session_id='turn')
    finally:db.close()
    shutil.rmtree(root)
    b=PostgresStore(tmp_path,database);b.recover()
    sandbox=Sandbox(tmp_path,{},durable_store=b);tools=Tools(b,sandbox)
    requests=[]
    def handler(request):
        requests.append(request.method)
        assert request.method=='GET'
        return httpx.Response(200,json=response)
    client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider=Providers(b,sandbox,tools,build_mcp(tools),{},gemini_client=client)
    async def scenario():
        resumed=await provider.resume(ident)
        assert resumed['status']=='waiting_input' and resumed['generation']==1
        assert resumed['spent']+resumed['reserved']==100
        assert pending_question(b.events(ident))['question_id']==command
        assert pending_question(b.events(ident))['question_map']['sha256']==digest
        assert drawing_path.read_bytes()==drawing
        assert requests==['GET']
        await client.aclose()
    asyncio.run(scenario())
    with sqlite3.connect(working) as db:assert db.execute('SELECT value FROM quantities').fetchone()[0]==21
    with sqlite3.connect(original) as db:assert db.execute('SELECT value FROM quantities').fetchone()[0]==7


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
    from psycopg2.errors import ReadOnlySqlTransaction
    with pytest.raises(ReadOnlySqlTransaction,match='read-only'):
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
    from experiments.agent_assistant.store import Store
    old=Store(tmp_path/'legacy');old.set_budget(1000)
    run,_=old.create('operator','legacy-ledger-request','test',['shift'],100,provider='gemini')
    old.update(run['id'],status='waiting_input',spent=37,reserved=63)
    old.event(run['id'],'operator_answer',{'text':'指定した厨房と釜で実施'})
    raw = old.path.read_bytes()
    seed_legacy(database, raw)
    a=PostgresStore(tmp_path/'runtime',database)
    a.update(run['id'],status='stopped',reserved=0)
    assert a.get(run['id'])['spent']==37
    assert a.events(run['id'])[0]['payload']['text']=='指定した厨房と釜で実施'
    with closing(database()) as pg,pg:
        with pg.cursor() as c:
            c.execute('SELECT payload FROM sawa_agent_trial.ledger WHERE id=1')
            assert bytes(c.fetchone()[0]) == raw  # immutable migration archive
    b=PostgresStore(tmp_path/'runtime',database);b.recover()
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
    from experiments.agent_assistant.store import Store
    old=Store(tmp_path/'legacy');old.set_budget(1000)
    old.create('operator','corrupt-legacy-request','test',['shift'],100)
    damaged = old.path.read_bytes()[:-100]
    seed_legacy(database, damaged)
    with pytest.raises(Conflict,match='破損'):PostgresStore(tmp_path,database)
    with closing(database()) as pg,pg:
        with pg.cursor() as c:
            c.execute('SELECT payload FROM sawa_agent_trial.ledger WHERE id=1')
            assert bytes(c.fetchone()[0])==damaged


@pytest.mark.parametrize('status',['stop_pending','rollback_stop_pending'])
def test_restart_preserves_stop_uncertainty_and_restoration_receipt(tmp_path,database,status):
    a=PostgresStore(tmp_path,database);a.set_budget(1000)
    run,_=a.create('u','stop-restart','old',['hospital'],200,provider='gemini')
    a.update(run['id'],status=status,spent=17,reserved=183)
    a.event(run['id'],'rollback',{'restored':True,'scope':'this_request_sandbox'})
    b=PostgresStore(tmp_path,database);b.recover()
    recovered=b.get(run['id'])
    assert recovered['status']==status and recovered['generation']==run['generation']+1
    assert recovered['spent']==17 and recovered['reserved']==183
    if status=='rollback_stop_pending':
        assert b.create('u','next-work','new',['shift'],100)[0]['status']=='preparing'
    else:
        with pytest.raises(Conflict,match='他の依頼'):b.create('u','next-work','new',['shift'],100)
