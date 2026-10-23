"""Formal upgrades of verified fresh copies; source/Test databases are never targets."""
import os
import subprocess
import sys
from urllib.parse import urlsplit

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from src import staging_copy_schema as proof
from src.services.staging_copy_refresh_service import RefreshError


def upgrade_hospital(destination, port, target, expected, source_heads, target_heads):
    from src.maintenance.formal_migration_schema import ALEMBIC_CONFIG, require_formal_head
    from src.maintenance.live_schema_adoption import adopt
    uri = URL.create('postgresql+psycopg2', username=destination['user'], password=destination['password'],
                     host='127.0.0.1', port=port, database=target)
    database = create_engine(uri, hide_parameters=True)
    try:
        with database.connect() as connection:
            if connection.execute(text('SELECT current_database()')).scalar_one() != target:
                raise RefreshError('refresh_copy_upgrade_target_mismatch')
            original, before = proof.preserve_before(connection, expected)
            if not source_heads:
                receipt = adopt(connection)
                if receipt['source_unchanged'] is not True:
                    raise RefreshError('refresh_copy_upgrade_values_changed')
            else:
                # A separate process keeps ALEMBIC_DB_URI out of concurrent request environments.
                environment = {**os.environ, 'ALEMBIC_DB_URI': uri.render_as_string(hide_password=False)}
                result = subprocess.run([sys.executable, '-m', 'alembic', '-c', str(ALEMBIC_CONFIG), 'upgrade', 'head'],
                    env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=600)
                if result.returncode:
                    raise RefreshError('refresh_copy_upgrade_failed')
            require_formal_head(connection)
            proof.require_preserved(connection, original, before)
            actual = list(connection.execute(text('SELECT version_num FROM alembic_version ORDER BY 1')).scalars())
            if actual != sorted(target_heads):
                raise RefreshError('refresh_copy_upgrade_head_mismatch')
            connection.rollback()
            return {'existing_values_preserved': True, 'preserved_tables': before}
    finally:
        database.dispose()


def upgrade_shift(service, job, destination, port, target, expected, source_heads, target_heads):
    from src.services.staging_copy_refresh_engine import _cloud_session, runtime_environment
    if not service.endswith('/services/shift-api-stg'):
        raise RefreshError('refresh_copy_upgrade_service_invalid')
    session = _cloud_session()
    response = session.get('https://run.googleapis.com/v2/' + service, timeout=30)
    if response.status_code != 200:
        raise RefreshError('refresh_copy_upgrade_service_unavailable')
    endpoint = response.json().get('uri', '')
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.a.run.app') or parsed.path not in {'', '/'}:
        raise RefreshError('refresh_copy_upgrade_service_invalid')
    environment, _ = runtime_environment(service, credential_keys={'SAWA_API_SERVICE_TOKEN'})
    token = environment.get('SAWA_API_SERVICE_TOKEN')
    if not token:
        raise RefreshError('refresh_copy_upgrade_auth_missing')
    # requests without OAuth Authorization: this API uses its existing exact service-token boundary.
    import requests
    response = requests.post(endpoint.rstrip('/') + '/internal/staging-copy-migration',
        headers={'x-sawa-service-token': token}, json={'request_id': job['id'], 'attempt': job['attempt'],
            'database': target, 'expected_tables': expected, 'source_heads': source_heads, 'target_heads': target_heads}, timeout=900)
    if response.status_code != 200:
        raise RefreshError('refresh_copy_upgrade_failed')
    result = response.json()
    if result.get('database') != target:
        raise RefreshError('refresh_copy_upgrade_target_mismatch')
    return result
