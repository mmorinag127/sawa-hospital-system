"""No cloud writes. Real SQL uses only the caller-owned isolated local cluster."""
from copy import deepcopy
from contextlib import nullcontext
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import Mock
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
import stg_menu_master_safety as safety
import verify_stg_menu_master_ui as runner
import staging_db_target as target
from src.models.menu import MenuMaster

NAME = 'c1-live-123-1-' + 'a' * 32
SHA = '1' * 40
CONTEXT = dict(GITHUB_ACTIONS='true', GITHUB_EVENT_NAME='workflow_dispatch', C1_LIVE_VERIFY='true',
    GITHUB_REF='refs/heads/develop', GITHUB_REF_NAME='develop', GITHUB_SHA=SHA,
    GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1', PROJECT_ID='sawahospitalsystem', REGION='asia-northeast2',
    WEB_SERVICE='web-stg', WORKER_SERVICE='worker-stg', WEB_URL=runner.WEB, WORKER_URL=runner.WORKER,
    LIVE_SERVICE_ACCOUNT=safety.SA_EMAIL, GOOGLE_OAUTH_CLIENT_ID='123-test.apps.googleusercontent.com',
    PROD_GOOGLE_OAUTH_CLIENT_ID='123-prod.apps.googleusercontent.com')


@pytest.mark.parametrize('key', list(CONTEXT))
def test_invalid_context_stops_before_cloud(key, monkeypatch):
    env = {**CONTEXT, key: ''}
    for k, value in env.items():
        monkeypatch.setenv(k, value)
    command = Mock(side_effect=lambda args: '' if 'diff' in args else SHA)
    cloud = Mock()
    monkeypatch.setattr(runner, 'private_command', command)
    monkeypatch.setattr(runner, 'deployed_sources', cloud)
    with pytest.raises(safety.Blocked):
        runner.run({})
    cloud.assert_not_called()
    assert command.call_count == 1  # Rejected by context, not a later dirty-source guard.


def test_valid_opt_in_and_full_sha_only():
    runner.require_context(CONTEXT, SHA)
    for changed in ({'C1_LIVE_VERIFY': 'false'}, {'GITHUB_EVENT_NAME': 'push'}, {'GITHUB_SHA': SHA[:12]},
                    {'GOOGLE_OAUTH_CLIENT_ID': CONTEXT['PROD_GOOGLE_OAUTH_CLIENT_ID']}):
        with pytest.raises(safety.Blocked):
            runner.require_context({**CONTEXT, **changed}, SHA)


class Response(io.BytesIO):
    def __init__(self, status, data=b'', location=None):
        super().__init__(data)
        self.status = status
        self.headers = {'Location': location} if location else {}


@pytest.mark.parametrize('redirect_url', ['http://storage.googleapis.com/b/o', 'https://evil.test/o',
    'https://storage.googleapis.com.evil.test/o', 'https://user@storage.googleapis.com/o',
    'https://storage.googleapis.com:444/o', '//storage.googleapis.com/o'])
def test_registry_redirect_rejects_untrusted_target(redirect_url, monkeypatch):
    opener = Mock()
    opener.open.return_value = Response(307, location=redirect_url)
    monkeypatch.setattr(target.urllib.request, 'build_opener', Mock(return_value=opener))
    with pytest.raises(target.StagingConfigBlocked, match='redirect-target-rejected'):
        runner.read_http('https://' + runner.REGISTRY + '/v2/blob', 'private-sentinel', blob_redirect=True)
    assert opener.open.call_count == 1


def test_signed_gcs_blob_redirect_never_receives_registry_authorization(monkeypatch):
    opener = Mock()
    opener.open.side_effect = [Response(307, location='https://storage.googleapis.com/b/o?signature=private'), Response(200, b'{}')]
    monkeypatch.setattr(target.urllib.request, 'build_opener', Mock(return_value=opener))
    assert runner.read_http('https://' + runner.REGISTRY + '/v2/blob', 'private-sentinel', blob_redirect=True) == (200, b'{}')
    requests = [call.args[0] for call in opener.open.call_args_list]
    assert requests[0].get_header('Authorization') == 'Bearer private-sentinel'
    assert requests[1].get_header('Authorization') is None


@pytest.mark.parametrize('failure', [None, 'sha', 'config', 'manifest'])
def test_image_proof_checks_manifest_blob_and_full_source_sha(monkeypatch, failure):
    config = json.dumps({'config': {'Labels': {'sawa.git_sha': SHA if failure != 'sha' else SHA[:12]}}}).encode()
    manifest = json.dumps({'config': {'digest': 'sha256:' + hashlib.sha256(config).hexdigest()}}).encode()
    digest = 'sha256:' + hashlib.sha256(manifest).hexdigest()
    transport = Mock(side_effect=[(200, manifest if failure != 'manifest' else b'{}'),
                                  (200, config if failure != 'config' else b'{}')])
    monkeypatch.setattr(target, 'read_http', transport)
    def run():
        runner.image_source(runner.REGISTRY + '/sawahospitalsystem/backend/frontend@' + digest, 'frontend', SHA, 'private')
    if failure:
        with pytest.raises((safety.Blocked, target.StagingConfigBlocked)):
            run()
    else:
        run()
        assert transport.call_args.kwargs['blob_redirect'] is True


@pytest.fixture
def pg():
    raw = os.environ['C1_LIVE_PG_URI']  # Missing real PG is an error, never a passing skip.
    url = sa.make_url(raw)
    assert url.drivername == 'postgresql+psycopg2' and url.host is None and url.database == 'c1_live'
    socket = Path(url.query['host']).resolve()
    assert socket.parent == Path('/private/tmp') and socket.name.startswith('sawa-c1stg-pg.')
    assert (socket / 'OWNER').read_text().strip() == str(ROOT)
    engine = sa.create_engine(url)
    with engine.connect() as c:
        assert Path(c.execute(sa.text('SHOW data_directory')).scalar_one()).resolve() == ROOT / 'tmp/stg-migration/pgdata'
        assert c.execute(sa.text('SHOW listen_addresses')).scalar_one() == ''
    with engine.begin() as c:
        c.execute(sa.text('DROP SCHEMA public CASCADE'))
        c.execute(sa.text('CREATE SCHEMA public'))
    metadata = sa.MetaData()
    master = MenuMaster.__table__.to_metadata(metadata)
    for name in safety.TABLES:
        if name != 'menu_masters':
            cols = [sa.Column('id', sa.String, primary_key=True), sa.Column('name', sa.String)]
            if name in safety.ID_TABLES:
                # monthly_menu_items deliberately has no FK, matching migration 0015.
                cols.append(sa.Column('menu_master_id', sa.String, *([sa.ForeignKey('menu_masters.id')] if name == 'menu_facility_overrides' else [])))
            if name == 'menu_facility_overrides':
                # Both definitions coexist in migration 0015 and carry index attributes.
                cols.extend([sa.Column('facility_id', sa.String, nullable=False),
                    sa.UniqueConstraint('menu_master_id', 'facility_id', name='uq_menu_facility_override_scope')])
            sa.Table(name, metadata, *cols)
    overrides = metadata.tables['menu_facility_overrides']
    sa.Index('uq_menu_facility_overrides_master_facility', overrides.c.menu_master_id, overrides.c.facility_id, unique=True)
    sa.Table('users', metadata, sa.Column('id', sa.String, primary_key=True), sa.Column('account', sa.String), sa.Column('role', sa.String), sa.Column('status', sa.String))
    sa.Table('user_system_access', metadata, sa.Column('user_id', sa.String), sa.Column('system_key', sa.String), sa.Column('enabled', sa.Boolean))
    metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(metadata.tables['users'].insert(), dict(id='sa', account=safety.SA_EMAIL, role='operator', status='active'))
        c.execute(metadata.tables['user_system_access'].insert(), dict(user_id='sa', system_key='hospital', enabled=True))
    engine.test_master = master
    try:
        yield engine
    finally:
        engine.dispose()


def proof(pg):
    return safety.preflight(pg, NAME, database='c1_live', role=pg.url.username)


def owned(pg):
    checked = proof(pg)
    payload = dict(name=NAME, unit_type='cut', qty_per_serving=0, bag_max_qty=1500, bag_max_unit='count',
                   temp_type='cold', daypart='昼食', category='主菜', condiments=['塩'])
    item = dict(id='MNU0123abcd', revision=1, normalized_name=NAME, **payload)
    with pg.begin() as c:
        c.execute(pg.test_master.insert(), item)
    initial = dict(version=1, source=SHA, name=NAME, runId='123', attempt='1', nonce='local-only',
                   schemaSHA256=safety.fingerprint(checked), absentBefore=True, receipts=[], writeAttempted=False, pending=None)
    ledger = {**initial, 'writeAttempted': True, 'receipts': [dict(method='POST', status=200, payload=payload, item=item)]}
    return initial, ledger, checked


def count(pg):
    with pg.connect() as c:
        return c.execute(sa.text('SELECT count(*) FROM menu_masters')).scalar_one()


def test_own_matching_record_cleanup_after_secondary_ui_failure(pg):
    initial, ledger, checked = owned(pg)
    # The UI display failed after its validated receipt; finally must still clean up.
    assert safety.cleanup_owned(pg, initial, ledger, checked) == {'status': 'deleted', 'deleted': ['MNU0123abcd']}
    assert count(pg) == 0


@pytest.mark.parametrize('ordinary_index', [False, True], ids=['migration-0015-indexes', 'ordinary-index-sibling'])
def test_index_attributes_are_not_independent_references(pg, ordinary_index):
    expected = {'uq_menu_facility_override_scope', 'uq_menu_facility_overrides_master_facility'}
    with pg.begin() as c:
        if ordinary_index:
            c.execute(sa.text('CREATE INDEX c1_monthly_reference ON monthly_menu_items(menu_master_id)'))
            expected.add('c1_monthly_reference')
        rows = c.execute(sa.text("""
            SELECT c.relname,c.relkind,a.attname,pg_get_indexdef(c.oid) AS definition
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid
            WHERE n.nspname='public' AND c.relname=ANY(:names) AND a.attname='menu_master_id'
            ORDER BY c.relname
        """), {'names': sorted(expected)}).mappings().all()
    assert {r['relname'] for r in rows} == expected
    assert all(r['relkind'] == 'i' for r in rows)
    print('isolated catalog index metadata:', [dict(r) for r in rows])
    initial, ledger, checked = owned(pg)
    assert safety.cleanup_owned(pg, initial, ledger, checked)['deleted'] == ['MNU0123abcd']
    assert count(pg) == 0


@pytest.mark.parametrize('case', ['existing', 'foreign-ledger', 'foreign-name', 'mutated', 'mutated-fields-only', 'unknown-id', 'pending', 'post-timeout', 'payload-mismatch'])
def test_ambiguous_foreign_existing_mutated_records_are_not_deleted(pg, case):
    initial, ledger, checked = owned(pg)
    ledger = deepcopy(ledger)
    if case == 'existing':
        with pytest.raises(safety.Blocked, match='already-exists'):
            proof(pg)
        initial['absentBefore'] = ledger['absentBefore'] = False
    elif case == 'foreign-ledger':
        ledger['nonce'] = 'other-run'
    elif case == 'foreign-name':
        ledger['receipts'][0]['item']['name'] = 'unowned'
    elif case == 'mutated':
        with pg.begin() as c:
            c.execute(sa.text("UPDATE menu_masters SET revision=2,category='external edit'"))
    elif case == 'mutated-fields-only':
        with pg.begin() as c:
            c.execute(sa.text("UPDATE menu_masters SET qty_per_serving=1"))
    elif case == 'unknown-id':
        ledger['receipts'][0]['item']['id'] = 'MNUffffffff'
    elif case == 'pending':
        ledger['pending'] = {'method': 'PUT'}
    elif case == 'post-timeout':
        ledger['receipts'] = []
    else:
        ledger['receipts'][0]['payload']['daypart'] = '昼'
    with pytest.raises(safety.Blocked):
        safety.cleanup_owned(pg, initial, ledger, checked)
    assert count(pg) == 1


@pytest.mark.parametrize('table', safety.NAME_TABLES + safety.ID_TABLES)
def test_fk_nonfk_and_normalized_name_references_block_cleanup(pg, table):
    initial, ledger, checked = owned(pg)
    with pg.begin() as c:
        if table == 'menu_facility_overrides':
            c.execute(sa.text("INSERT INTO menu_facility_overrides(id,name,menu_master_id,facility_id) VALUES('r','other','MNU0123abcd','f')"))
        elif table in safety.ID_TABLES:
            c.execute(sa.text(f"INSERT INTO {table}(id,name,menu_master_id) VALUES('r','other','MNU0123abcd')"))
        else:
            c.execute(sa.text(f"INSERT INTO {table}(id,name) VALUES('r',:name)"), {'name': ' ' + NAME.upper() + ' '})
    with pytest.raises(safety.Blocked, match='reference-present'):
        safety.cleanup_owned(pg, initial, ledger, checked)
    assert count(pg) == 1


@pytest.mark.parametrize('change', [
    "CREATE TABLE unexpected(id TEXT REFERENCES menu_masters(id))",
    "CREATE TABLE unexpected(menu_master_id TEXT)",
    "CREATE FUNCTION c1_trigger() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN OLD; END'; CREATE TRIGGER unexpected BEFORE DELETE ON menu_masters FOR EACH ROW EXECUTE FUNCTION c1_trigger()",
    "ALTER TABLE menu_masters ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY unexpected ON menu_masters USING (true)",
])
def test_unknown_schema_changes_fail_closed(pg, change):
    initial, ledger, checked = owned(pg)
    with pg.begin() as c:
        c.execute(sa.text(change))
    with pytest.raises(safety.Blocked):
        safety.cleanup_owned(pg, initial, ledger, checked)
    assert count(pg) == 1


@pytest.mark.parametrize('change', ["UPDATE users SET status='inactive'", "UPDATE user_system_access SET enabled=false", "DELETE FROM user_system_access"])
def test_preflight_identity_gate_no_write(pg, change):
    with pg.begin() as c:
        c.execute(sa.text(change))
    with pytest.raises(safety.Blocked, match='deploy-principal'):
        proof(pg)
    assert count(pg) == 0


def test_preflight_wrong_database_and_missing_privilege_stop(pg):
    with pytest.raises(safety.Blocked, match='database-or-role'):
        safety.preflight(pg, NAME, database='orders', role='orders_app')
    # This role and grants exist only inside this disposable cluster, never in Actions/stg.
    with pg.begin() as c:
        if not c.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname='c1_readonly'")).first():
            c.execute(sa.text('CREATE ROLE c1_readonly'))
        c.execute(sa.text('GRANT USAGE ON SCHEMA public TO c1_readonly'))
        c.execute(sa.text('GRANT SELECT ON ALL TABLES IN SCHEMA public TO c1_readonly'))
    with pg.connect() as c:
        c.execute(sa.text('SET ROLE c1_readonly'))
        with pytest.raises(safety.Blocked, match='privilege-missing'):
            safety.schema_gate(c)
    assert count(pg) == 0


def test_delete_privilege_is_required_even_when_reference_locks_are_allowed(pg):
    with pg.begin() as c:
        if not c.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname='c1_update'")).first():
            c.execute(sa.text('CREATE ROLE c1_update'))
        c.execute(sa.text('GRANT USAGE ON SCHEMA public TO c1_update'))
        c.execute(sa.text('GRANT SELECT,UPDATE ON ALL TABLES IN SCHEMA public TO c1_update'))
    with pg.connect() as c:
        c.execute(sa.text('SET ROLE c1_update'))
        with pytest.raises(safety.Blocked, match='delete-privilege-missing'):
            safety.schema_gate(c)


def test_preflight_executes_no_data_or_schema_mutation(pg):
    statements = []
    sa.event.listen(pg, 'before_cursor_execute', lambda c, cursor, sql, *args: statements.append(sql))
    proof(pg)
    assert not any(sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','ALTER','DROP','CREATE')) for sql in statements)
    assert count(pg) == 0


def test_latest_acknowledged_update_is_required_and_deleted_exactly(pg):
    initial, ledger, checked = owned(pg)
    previous = ledger['receipts'][-1]['item']
    payload = {k: previous[k] for k in safety.FIELDS}
    payload.update(qty_per_serving=None, bag_max_qty=0, daypart='夕食')
    current = {**previous, **payload, 'revision': 2}
    with pg.begin() as c:
        c.execute(pg.test_master.update().where(pg.test_master.c.id == current['id']).values(**current))
    ledger['receipts'].append(dict(method='PUT', status=200, payload={**payload, 'revision':1}, item=current))
    assert safety.cleanup_owned(pg, initial, ledger, checked)['deleted'] == [current['id']]
    assert count(pg) == 0


def test_lock_contention_stops_cleanup_without_deletion(pg):
    initial, ledger, checked = owned(pg)
    with pg.begin() as blocker:
        blocker.execute(sa.text('LOCK TABLE monthly_menu_items IN ROW EXCLUSIVE MODE'))
        with pytest.raises(sa.exc.DBAPIError):
            safety.cleanup_owned(pg, initial, ledger, checked)
    assert count(pg) == 1


@pytest.fixture
def local_runner(pg, monkeypatch, tmp_path):
    for k, v in CONTEXT.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv('LIVE_ID_TOKEN', 'private-test-sentinel')
    monkeypatch.setattr(runner, 'OUTPUT', tmp_path)
    monkeypatch.setattr(runner, 'private_command', lambda args: '' if 'diff' in args else SHA)
    monkeypatch.setattr(runner, 'check_token', Mock())
    monkeypatch.setattr(runner, 'deployed_sources', lambda sha: {s:{'revision':s+'-r1'} for s in ('web-stg','worker-stg')})
    monkeypatch.setattr(runner, '_load_service_db_config', lambda *args, **kwargs: SimpleNamespace(
        instance_connection_name=runner.INSTANCE, db_name='orders', db_user='orders_app', db_password='private'))
    monkeypatch.setattr(runner, 'cloud_sql_proxy', lambda *args: nullcontext())
    monkeypatch.setattr(runner, 'create_engine', lambda *args, **kwargs: pg)
    monkeypatch.setattr(runner, 'uuid4', lambda: SimpleNamespace(hex='a' * 32))
    monkeypatch.setattr(runner, 'preflight', lambda engine, name: safety.preflight(engine, name, database='c1_live', role=pg.url.username))


@pytest.mark.parametrize('column', ['menu_master_id', 'menu_master_name'])
@pytest.mark.parametrize('kind', ['r', 'p', 'f', 'm', 'v', 'c'])
def test_unknown_reference_relation_blocks_runner_before_post(pg, local_runner, monkeypatch, tmp_path, kind, column):
    ddl = {
        'r': f'CREATE TABLE unexpected({column} TEXT); CREATE INDEX c1_unknown_ref ON unexpected({column})',
        'p': f'CREATE TABLE unexpected({column} TEXT) PARTITION BY HASH({column}); CREATE INDEX c1_unknown_ref ON unexpected({column})',
        'f': f'CREATE FOREIGN TABLE unexpected({column} TEXT) SERVER c1_catalog_only',
        'm': f'CREATE MATERIALIZED VIEW unexpected AS SELECT NULL::TEXT AS {column}',
        'v': f'CREATE VIEW unexpected AS SELECT NULL::TEXT AS {column}',
        'c': f'CREATE TYPE unexpected AS ({column} TEXT)',
    }
    with pg.begin() as c:
        if kind == 'f':
            # No handler, user mapping or network target: catalog metadata only.
            c.execute(sa.text('CREATE FOREIGN DATA WRAPPER c1_catalog_only'))
            c.execute(sa.text('CREATE SERVER c1_catalog_only FOREIGN DATA WRAPPER c1_catalog_only'))
        c.execute(sa.text(ddl[kind]))
        assert c.execute(sa.text("SELECT relkind FROM pg_class WHERE oid='public.unexpected'::regclass")).scalar_one() == kind
    api, browser = Mock(), Mock()
    monkeypatch.setattr(runner, 'api', api)
    monkeypatch.setattr(runner, 'browser', browser)
    result = {}
    try:
        with pytest.raises(safety.Blocked, match='^unknown-non-FK-reference-column$'):
            runner.run(result)
        assert result['phase'] == 'read-only-database-preflight'
        api.assert_not_called()
        browser.assert_not_called()
        assert not (tmp_path / 'ledger.json').exists()
        assert count(pg) == 0
    finally:
        if kind == 'f':
            with pg.begin() as c:
                c.execute(sa.text('DROP SERVER c1_catalog_only CASCADE'))
                c.execute(sa.text('DROP FOREIGN DATA WRAPPER c1_catalog_only'))


@pytest.mark.parametrize('outcome', ['pass', 'display-failure', 'post-timeout'])
def test_runner_finally_real_sql_cleanup_or_explicit_unresolved(pg, local_runner, monkeypatch, tmp_path, outcome):
    def api(path, token, expected=200):
        if '/portal/auth/me' in path:
            return dict(account=safety.SA_EMAIL, role='operator', systems=['hospital'])
        if path == '/api/auth/me':
            return dict(auth_disabled=False, role='operator')
        if expected == 401:
            assert token is None
            return None
        assert count(pg) == 0
        return {'items': [], 'total': 0} if expected == 200 else None

    def browser(path, token):
        ledger = json.loads(path.read_text())
        assert ledger['absentBefore'] is True and ledger['receipts'] == [] and count(pg) == 0
        payload = dict(name=NAME, unit_type='cut', qty_per_serving=0, bag_max_qty=1500, bag_max_unit='count',
                       temp_type='cold', daypart='昼食', category='主菜', condiments=['塩'])
        item = dict(id='MNU0123abcd', revision=1, normalized_name=NAME, **payload)
        with pg.begin() as c:
            c.execute(pg.test_master.insert(), item)
        ledger['writeAttempted'] = True
        if outcome == 'post-timeout':
            ledger['pending'] = {'method': 'POST', 'payload': payload}
        else:
            ledger['receipts'].append(dict(method='POST', status=200, payload=payload, item=item))
        runner.write_json(path, ledger)
        if outcome != 'pass':
            raise safety.Blocked('browser-secondary-failure')
    monkeypatch.setattr(runner, 'api', api)
    monkeypatch.setattr(runner, 'browser', browser)
    result = {}
    if outcome == 'pass':
        runner.run(result)
    else:
        with pytest.raises(safety.Blocked):
            runner.run(result)
    if outcome == 'post-timeout':
        assert result['cleanup']['status'] == 'unresolved' and count(pg) == 1
    else:
        assert result['cleanup']['apiAbsenceVerified'] is True and count(pg) == 0
    assert 'private' not in json.dumps(result)
