"""Credential-free cloud fixtures; both callers must stop before opening a DB."""
from copy import deepcopy
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
import staging_db_target as gate
import verify_stg_menu_master_ui as live
import run_stg_menu_master_migration as migration


def install_cloud(monkeypatch):
    documents, images, calls = {}, {}, []
    for service, component in [('web-stg', 'frontend'), ('worker-stg', 'backend')]:
        name = service + '-r1'
        image = gate.REGISTRY + '/sawahospitalsystem/backend/' + component + '@sha256:' + 'a' * 64
        env = [{'name': k, 'value': v} for k,v in {
            'DB_HOST':'/cloudsql/' + gate.INSTANCE, 'DB_NAME':'orders', 'DB_USER':'orders_app',
            'API_PROXY_TARGET':gate.WORKER_URL}.items()]
        env.append({'name':'DB_PASSWORD','valueFrom':{'secretKeyRef':{'name':'test-secret','key':'1'}}})
        template = {'metadata':{'annotations':{'run.googleapis.com/cloudsql-instances':gate.INSTANCE}},
                    'spec':{'containers':[{'image':image, 'env':env}]}}
        documents['services',service] = {'metadata':{'generation':1},'spec':{'template':deepcopy(template)},
            'status':{'url':gate.SERVICE_URLS[service], 'observedGeneration':1,'latestCreatedRevisionName':name,'latestReadyRevisionName':name,
                      'traffic':[{'percent':100,'revisionName':name}]}}
        revision = deepcopy(template)
        revision['metadata']['name'] = name
        revision['status'] = {'conditions':[{'type':'Ready','status':'True'}], 'imageDigest':image}
        documents['revisions',name] = revision
        images[image] = {'Env':[]}
    def describe(*args):
        calls.append(args)
        assert args[0] == 'run' and args[2] == 'describe'
        assert args[4:] == ('--project=sawahospitalsystem','--region=asia-northeast2')
        return deepcopy(documents[args[1],args[3]])
    monkeypatch.setattr(gate.transport, '_run_gcloud_json', describe)
    secret = Mock(return_value='private-password-sentinel')
    monkeypatch.setattr(gate.transport, '_load_secret', secret)
    monkeypatch.setattr(gate, '_access_token', Mock(return_value='private-access-sentinel'))
    monkeypatch.setattr(gate, 'image_config', lambda digest, component, token: deepcopy(images[digest]))
    return documents, images, calls, secret


@pytest.fixture
def cloud_case(monkeypatch):
    return install_cloud(monkeypatch)


def env_set(descriptor, key, value):
    entries = descriptor['spec']['containers'][0]['env']
    entries[:] = [e for e in entries if e['name'] != key]
    if value is not None:
        entries.append({'name':key, 'value':value})


def load(service='web-stg', **kwargs):
    return gate.load_service_db_config(gate.PROJECT, gate.REGION, service, **kwargs)


def test_actual_shape_resolves_both_active_and_intended_without_new_env(cloud_case):
    for service in ('web-stg','worker-stg'):
        config = load(service)
        assert (config.instance_connection_name, config.db_name, config.db_user) == (gate.INSTANCE, 'orders', 'orders_app')
        assert config.db_password == 'private-password-sentinel'
        assert 'private-password' not in repr(config)


def mutate(case, documents, images):
    service = 'worker-stg' if case == 'worker-mismatch' else 'web-stg'
    template = documents['services',service]['spec']['template']
    revision = documents['revisions',service+'-r1']
    if case in ('old-active-target','old-active-proxy'):
        old = deepcopy(revision); old['metadata']['name'] = service+'-old'
        env_set(old, 'DB_HOST' if case == 'old-active-target' else 'API_PROXY_TARGET', 'wrong')
        documents['revisions',service+'-old'] = old
        documents['services',service]['status']['traffic'] = [{'percent':100,'revisionName':service+'-old'}]
    elif case == 'split':
        documents['services',service]['status']['traffic'] = [{'percent':50,'revisionName':service+'-r1'}, {'percent':50,'revisionName':service+'-old'}]
    elif case == 'unobserved':
        documents['services',service]['status']['observedGeneration'] = 0
    elif case == 'service-url':
        documents['services',service]['status']['url'] = 'https://other.invalid'
    elif case == 'missing-active':
        del documents['services',service]['status']['traffic'][0]['revisionName']
    elif case == 'template-drift':
        env_set(template, 'DB_HOST', 'wrong')
    elif case.startswith('baked-'):
        digest = revision['status']['imageDigest']
        key = 'DB_URI' if case == 'baked-uri' else 'API_PROXY_TARGET' if case == 'baked-proxy' else 'DB_HOST'
        for descriptor in (template, revision):
            env_set(descriptor, key, None)
        images[digest]['Env'] = [key + '=wrong-private-value']
    else:
        key,value = {
            'uri':('DB_URI','postgresql+psycopg2://secret:private@other/other'),
            'valid-uri-unsupported':('DB_URI','postgresql+psycopg2://orders_app:private@/orders?host=/cloudsql/'+gate.INSTANCE),
            'uri-whitespace':('DB_URI',' '), 'host':('DB_HOST','127.0.0.1'),
            'host-missing':('DB_HOST',None), 'database':('DB_NAME','other'), 'role':('DB_USER','other'),
            'driver':('DB_DRIVER','sqlite'), 'port':('DB_PORT','5433'), 'proxy':('API_PROXY_TARGET','https://other.invalid'),
            'worker-mismatch':('DB_HOST','/cloudsql/other'),
        }[case]
        for descriptor in (template,revision):
            env_set(descriptor, key, value)


CASES = ['uri','valid-uri-unsupported','uri-whitespace','host','host-missing','database','role','driver','port','proxy',
         'baked-uri','baked-host','baked-proxy','old-active-target','old-active-proxy','split','unobserved','service-url','missing-active','template-drift','worker-mismatch']


@pytest.mark.parametrize('caller', ['migration','live'])
@pytest.mark.parametrize('case', CASES)
def test_shared_gate_rejects_before_either_caller_opens_database(cloud_case, monkeypatch, caller, case, tmp_path, capsys):
    from test_stg_menu_master_live import CONTEXT, SHA
    documents, images, calls, _ = cloud_case
    mutate(case, documents, images)
    for k,v in CONTEXT.items():
        monkeypatch.setenv(k,v)
    module = migration if caller == 'migration' else live
    proxy, engine = Mock(), Mock()
    monkeypatch.setattr(module,'cloud_sql_proxy',proxy)
    monkeypatch.setattr(module,'create_engine',engine)
    if caller == 'live':
        monkeypatch.setattr(live,'private_command',lambda args: '' if 'diff' in args else SHA)
        monkeypatch.setattr(live,'check_token',Mock())
        monkeypatch.setattr(live,'deployed_sources',lambda sha:{s:{'revision':documents['services',s]['status']['traffic'][0].get('revisionName','')} for s in ('web-stg','worker-stg')})
        monkeypatch.setattr(live,'OUTPUT',tmp_path)
    assert module.main() == 1
    if caller == 'live':
        import json
        assert json.loads((tmp_path/'result.json').read_text())['code'].startswith('staging-db-')
    else:
        assert capsys.readouterr().err.startswith('staging-db-')
    proxy.assert_not_called(); engine.assert_not_called()
    assert calls


def test_revision_env_overrides_baked_values_including_explicit_empty_uri(cloud_case):
    documents,images,_,_ = cloud_case
    revision = documents['revisions','web-stg-r1']
    images[revision['status']['imageDigest']]['Env'] = ['DB_URI=private-unsupported', 'DB_HOST=wrong', 'API_PROXY_TARGET=wrong']
    for descriptor in (revision,documents['services','web-stg']['spec']['template']):
        env_set(descriptor,'DB_URI','')
    assert load().db_name == 'orders'


@pytest.mark.parametrize('change', ['duplicate', 'secret-uri', 'ambiguous', 'missing-password', 'wrong-instance'])
def test_unknown_sources_block_without_inference(cloud_case, change):
    documents,images,_,_ = cloud_case
    for descriptor in (documents['revisions','web-stg-r1'], documents['services','web-stg']['spec']['template']):
        entries = descriptor['spec']['containers'][0]['env']
        if change == 'duplicate': entries.append({'name':'DB_HOST','value':'wrong'})
        elif change == 'secret-uri': entries.append({'name':'DB_URI','valueFrom':{'secretKeyRef':{'name':'private','key':'1'}}})
        elif change == 'ambiguous': entries[0]['valueFrom'] = {'secretKeyRef':{'name':'private','key':'1'}}
        elif change == 'missing-password': env_set(descriptor,'DB_PASSWORD',None)
        else: descriptor['metadata']['annotations']['run.googleapis.com/cloudsql-instances'] += ',other'
    with pytest.raises(gate.StagingConfigBlocked): load()


def test_source_checked_revision_cannot_silently_change(cloud_case):
    with pytest.raises(gate.StagingConfigBlocked, match='API-source-revision-changed'):
        load(expected_revision='web-stg-other')


def test_different_active_revision_is_allowed_only_after_its_own_config_check(cloud_case):
    documents,_,calls,_ = cloud_case
    old = deepcopy(documents['revisions','web-stg-r1']); old['metadata']['name']='web-stg-old'
    documents['revisions','web-stg-old']=old
    documents['services','web-stg']['status']['traffic']=[{'percent':100,'revisionName':'web-stg-old'}]
    assert load(expected_revision='web-stg-old').db_name == 'orders'
    assert [c[3] for c in calls] == ['web-stg','web-stg-r1','web-stg-old']
