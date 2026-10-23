"""Value-preservation checks for a fresh, unpublished staging copy only."""
import hashlib
import re
from datetime import datetime, timezone


def require_attempt(datasets, request_id, attempt, target):
    if not datasets.staging_runtime() or datasets.current_dataset() != 'test':
        raise ValueError('copy_migration_staging_control_required')
    if not re.fullmatch(r'[a-f0-9]{32}', request_id) or not re.fullmatch(r'[a-f0-9]{32}', attempt):
        raise ValueError('copy_migration_attempt_invalid')
    if target != 'sawa_replay_' + request_id[:24] + '_' + attempt[:8]:
        raise ValueError('copy_migration_target_invalid')
    registry, _ = datasets.read_registry()
    job = registry.get('refresh')
    if (not job or job.get('id') != request_id or job.get('attempt') != attempt
            or job.get('phase') != 'copying_database' or job.get('component') != 'shift'
            or datetime.fromisoformat(job['lease_until']) <= datetime.now(timezone.utc)):
        raise ValueError('copy_migration_attempt_not_current')
    # Only the current job's as-yet-unpublished dataset can own this target.
    if job['dataset'] in registry['snapshots'] or registry['active'] == job['dataset']:
        raise ValueError('copy_migration_published_target_forbidden')
    return job


def columns(connection):
    from sqlalchemy import text
    rows = connection.execute(text("""
        SELECT n.nspname,c.relname,a.attname
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid
        WHERE c.relkind IN ('r','m','p') AND n.nspname<>'information_schema'
        AND n.nspname !~ '^pg_' AND a.attnum>0 AND NOT a.attisdropped
        ORDER BY n.nspname,c.relname,a.attnum
    """))
    result = {}
    for schema, table, name in rows:
        if (schema, table) != ('public', 'alembic_version'):
            result.setdefault((schema, table), []).append(name)
    return result


def fingerprints(connection, original_columns):
    from sqlalchemy import text
    quote = connection.dialect.identifier_preparer.quote_identifier
    result = {}
    for (schema, table), names in original_columns.items():
        projection = ','.join(quote(name) for name in names)
        statement = ('SELECT row_to_json(t)::text FROM (SELECT ' + projection + ' FROM '
                     + quote(schema) + '.' + quote(table) + ') t ORDER BY row_to_json(t)::text COLLATE "C"')
        hashed, count = hashlib.sha256(), 0
        rows = connection.execute(text(statement).execution_options(stream_results=True))
        for (raw,) in rows:
            hashed.update(raw.encode('utf-8')); hashed.update(b'\n'); count += 1
        rows.close()
        result[schema + '.' + table] = {'rows': count, 'sha256': hashed.hexdigest()}
    return result


def preserve_before(connection, expected):
    if connection.dialect.name != 'postgresql':
        raise ValueError('copy_migration_postgres_required')
    original = columns(connection)
    actual = fingerprints(connection, original)
    source = {key: value for key, value in expected.items() if key != 'public.alembic_version'}
    if actual != source:
        raise ValueError('copy_migration_original_values_changed')
    connection.rollback()
    return original, actual


def require_preserved(connection, original_columns, before):
    if fingerprints(connection, original_columns) != before:
        raise ValueError('copy_migration_existing_values_not_preserved')
