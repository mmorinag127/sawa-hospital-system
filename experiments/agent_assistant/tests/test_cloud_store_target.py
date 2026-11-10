from unittest.mock import Mock
import pytest
from experiments.agent_assistant import durable


def target(monkeypatch,environment):
    monkeypatch.setenv('DB_HOST',f'/cloudsql/sawahospitalsystem:asia-northeast2:orders-{environment}')
    monkeypatch.setenv('DB_NAME','orders')
    monkeypatch.setenv('DB_USER','orders_app')
    monkeypatch.setenv('DB_PASSWORD','synthetic-test-password')
    monkeypatch.setenv('PORTAL_AUTH_ME_URL',f'https://worker-{environment}-avlnzjjrca-dt.a.run.app/portal/auth/me')


@pytest.mark.parametrize('environment',['stg','prod'])
def test_exact_environment_pair_uses_only_its_existing_database(monkeypatch,tmp_path,environment):
    target(monkeypatch,environment)
    pool=Mock(return_value=Mock())
    store=Mock()
    monkeypatch.setattr(durable,'_PooledConnections',pool)
    monkeypatch.setattr(durable,'PostgresStore',store)
    assert durable.cloud_store(tmp_path) is store.return_value
    assert pool.call_args.kwargs['host'].endswith('orders-'+environment)
    assert pool.call_args.kwargs['dbname']=='orders'
    store.assert_called_once_with(tmp_path,pool.return_value)


@pytest.mark.parametrize('name,value',[
    ('DB_HOST','/cloudsql/other:asia-northeast2:orders-prod'),
    ('DB_HOST','localhost'),('DB_NAME','other'),('DB_USER','postgres'),
    ('PORTAL_AUTH_ME_URL','https://worker-stg-avlnzjjrca-dt.a.run.app/portal/auth/me'),
    ('PORTAL_AUTH_ME_URL','https://untrusted.invalid/portal/auth/me'),('DB_PASSWORD',''),
])
def test_wrong_or_cross_environment_target_rejected_before_connection(monkeypatch,tmp_path,name,value):
    target(monkeypatch,'prod')
    monkeypatch.setenv(name,value)
    pool=Mock()
    monkeypatch.setattr(durable,'_PooledConnections',pool)
    with pytest.raises(RuntimeError):durable.cloud_store(tmp_path)
    pool.assert_not_called()
