from datetime import date, datetime, time, timezone
from decimal import Decimal
import json
import sqlite3
from unittest.mock import Mock

import pytest
from experiments.agent_assistant import school_source as source
from experiments.agent_assistant.store import Conflict


def target(tmp_path):
    with sqlite3.connect(tmp_path/'database.sqlite3') as db:
        db.executescript("CREATE TABLE alembic_version(version_num TEXT); INSERT INTO alembic_version VALUES('test-head'); CREATE TABLE schools(id INTEGER PRIMARY KEY,name TEXT,document JSON,original BLOB,day DATE,at TIME); INSERT INTO schools VALUES(99,'old fixture',NULL,NULL,NULL,NULL);")
    (tmp_path/'trial-manifest.json').write_text(json.dumps({'purpose':'sawa-agent-isolated-trial'}))


class Cursor:
    def __init__(self, pg): self.pg=pg;self.results=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,params=None):
        self.pg.queries.append(sql)
        assert sql.startswith(('SELECT ','SHOW ')),sql
        if sql=='SHOW transaction_read_only':self.results=[(self.pg.readonly,)]
        elif 'information_schema.tables' in sql:self.results=[('alembic_version',),('schools',)]+self.pg.extra
        elif 'information_schema.columns' in sql:
            self.results=[('version_num','text')] if params[0]=='alembic_version' else [('id','integer'),('name','text'),('document','jsonb'),('original','bytea'),('day','date'),('at','time')]
        elif sql=='SELECT version_num FROM alembic_version':self.results=[(self.pg.head,)]
        elif 'FROM "alembic_version"' in sql:self.results=[(self.pg.head,)]
        else:self.results=[(8,'registered','{"amount":1.25}',memoryview(b'original'),date(2026,7,1),time(9,30))]
    def fetchone(self):return self.results.pop(0) if self.results else None
    def fetchall(self):result=self.results;self.results=[];return result
    def fetchmany(self,size):result=self.results[:size];self.results=self.results[size:];return result


class PG:
    def __init__(self):self.queries=[];self.closed=False;self.extra=[];self.head='test-head';self.readonly='on'
    def set_session(self,**kwargs):assert kwargs=={'isolation_level':'REPEATABLE READ','readonly':True}
    def cursor(self):return Cursor(self)
    def close(self):self.closed=True


def test_copy_replaces_fixture_keeps_native_values_and_closes_readonly_source(tmp_path):
    target(tmp_path);pg=PG();source.copy_registered(tmp_path,connect=lambda:pg)
    with sqlite3.connect(tmp_path/'database.sqlite3') as db:
        row=db.execute('SELECT * FROM schools').fetchone()
        assert row==(8,'registered','{"amount":1.25}',b'original','2026-07-01','09:30:00')
    assert pg.closed
    proof=json.loads((tmp_path/'registered-source.json').read_text())
    assert proof['read_only'] and proof['tables']['schools']==1
    assert json.loads((tmp_path/'trial-manifest.json').read_text())['data']=='registered_staging_snapshot'


@pytest.mark.parametrize('change',['head','extra','writeable'])
def test_source_mismatch_blocks_without_falling_back_to_seed(tmp_path,change):
    target(tmp_path);pg=PG()
    if change=='head':pg.head='unknown-head'
    elif change=='extra':pg.extra=[('unexpected',)]
    else:pg.readonly='off'
    with pytest.raises(Conflict):source.copy_registered(tmp_path,connect=lambda:pg)
    assert pg.closed
    assert not (tmp_path/'registered-source.json').exists()


def test_failure_is_sanitized_and_never_claims_a_registered_snapshot(tmp_path):
    target(tmp_path)
    def failed():raise RuntimeError('synthetic-private-password')
    with pytest.raises(Conflict,match='試験データでは続行しません') as exc:source.copy_registered(tmp_path,connect=failed)
    assert 'synthetic-private-password' not in str(exc.value)
    assert not (tmp_path/'registered-source.json').exists()


@pytest.mark.parametrize('url',['','postgresql://school_lunch_app:fake@localhost/school_lunch','postgresql://school_lunch_app:fake@/school_lunch?host=/cloudsql/sawahospitalsystem:asia-northeast2:school-lunch-prod','postgresql://other:fake@/school_lunch?host=/cloudsql/'+source.INSTANCE])
def test_noncanonical_source_is_rejected_before_connect(monkeypatch,url):
    import psycopg2
    connect=Mock();monkeypatch.setattr(psycopg2,'connect',connect)
    monkeypatch.setenv('SAWA_TRIAL_SCHOOL_DATABASE_URL',url)
    with pytest.raises(Conflict):source.connection()
    connect.assert_not_called()


def test_native_scalar_conversions_preserve_dates_times_decimals_and_original_bytes():
    assert source.value(Decimal('1.250000'))=='1.250000'
    assert source.value(datetime(2026,7,1,9,30,tzinfo=timezone.utc))=='2026-07-01 09:30:00'
    assert source.value(memoryview(b'original'))==b'original'


@pytest.mark.parametrize('amount',['999999999999.123456','999999999999.123400','NaN'])
def test_numeric_values_that_native_sqlite_would_change_are_blocked(amount):
    with pytest.raises(Conflict):source.value(Decimal(amount))
