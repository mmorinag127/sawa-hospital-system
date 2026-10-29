"""Read-only PostgreSQL capacity/recovery inventory; never performs maintenance."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess


TABLES = '''SELECT s.schemaname,s.relname,s.n_live_tup,s.n_dead_tup,s.last_autovacuum,
    s.last_autoanalyze,pg_relation_size(s.relid) AS heap_bytes,
    CASE WHEN c.reltoastrelid=0 THEN 0 ELSE pg_total_relation_size(c.reltoastrelid) END AS toast_bytes,
    pg_table_size(s.relid) AS table_bytes_including_toast,
    pg_indexes_size(s.relid) AS index_bytes,pg_total_relation_size(s.relid) AS total_bytes
    FROM pg_stat_user_tables s JOIN pg_class c ON c.oid=s.relid
    ORDER BY pg_total_relation_size(s.relid) DESC'''
CONNECTIONS = '''SELECT state,count(*) AS connections,
    max(EXTRACT(epoch FROM clock_timestamp()-xact_start)) AS oldest_transaction_seconds
    FROM pg_stat_activity WHERE datname=current_database() GROUP BY state'''


def inspect_database(url):
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    engine = create_engine(url, poolclass=NullPool, connect_args={'connect_timeout': 10})
    try:
        with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
            connection.exec_driver_sql('SET TRANSACTION READ ONLY')
            connection.exec_driver_sql("SET LOCAL statement_timeout='15s'")
            identity = connection.execute(text('''SELECT current_database() AS database,
                current_setting('server_version') AS server_version,
                current_setting('max_connections')::integer AS max_connections,
                current_setting('autovacuum') AS autovacuum,
                pg_database_size(current_database()) AS database_bytes''')).mappings().one()
            return {'identity': dict(identity),
                    'tables': [dict(row) for row in connection.execute(text(TABLES)).mappings()],
                    'connections': [dict(row) for row in connection.execute(text(CONNECTIONS)).mappings()],
                    'wal': dict(connection.execute(text('SELECT wal_records,wal_fpi,wal_bytes,stats_reset FROM pg_stat_wal')).mappings().one()),
                    'wal_scope': 'Cluster-wide cumulative counters since stats_reset; not retained disk bytes or per-database WAL',
                    'row_counts': 'pg_stat estimates, not exact COUNT(*)',
                    'retention_execution': 'unknown: inventory is not proof of scheduled deletion',
                    'restore_verified': False}
    finally:
        engine.dispose()


def cloud_settings(project, instance):
    result = subprocess.run(['gcloud', 'sql', 'instances', 'describe', instance,
        '--project=' + project, '--format=json(name,region,databaseVersion,'
        'settings.dataDiskSizeGb,settings.storageAutoResize,settings.storageAutoResizeLimit,'
        'settings.availabilityType,settings.backupConfiguration)'],
        capture_output=True, text=True, timeout=30, check=True)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url-env', default='DATABASE_URL')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--project')
    parser.add_argument('--instance')
    parser.add_argument('--max-instances', type=int)
    parser.add_argument('--processes', type=int)
    parser.add_argument('--active-engines', type=int)
    parser.add_argument('--pool-size', type=int)
    parser.add_argument('--overflow', type=int)
    parser.add_argument('--reserved-connections', type=int)
    args = parser.parse_args()
    url = os.environ.get(args.database_url_env)
    if not url:
        parser.error('指定した環境変数に接続先がありません。接続値は表示しません')
    if bool(args.project) != bool(args.instance):
        parser.error('--project と --instance は両方指定してください')
    values = [args.max_instances, args.processes, args.active_engines, args.pool_size,
              args.overflow, args.reserved_connections]
    if any(value is not None for value in values):
        if any(value is None for value in values) or any(value < 0 for value in values):
            parser.error('接続数見積りの6項目はすべて0以上で指定してください')
        if any(value == 0 for value in values[:4]):
            parser.error('instance/process/engine/pool-size は1以上で指定してください')
    try:
        report = {'observed_at': datetime.now(timezone.utc).isoformat(),
                  'mode': 'read-only', **inspect_database(url)}
        if args.project:
            report['cloud_sql'] = cloud_settings(args.project, args.instance)
        if all(value is not None for value in values):
            maximum = values[0] * values[1] * values[2] * (values[3] + values[4]) + values[5]
            report['connection_budget'] = {'inputs': dict(zip(
                ('max_instances', 'processes', 'active_engines', 'pool_size', 'overflow', 'reserved'), values)),
                'maximum': maximum, 'within_database_limit': maximum <= report['identity']['max_connections'],
                'note': 'Upper bound from supplied settings; not a measured concurrency value'}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2) + '\n')
    except Exception as exc:
        # Driver errors can contain credentials or the DSN. Do not print them.
        parser.exit(1, '容量取得に失敗しました（' + type(exc).__name__ + '）。接続値は表示しません。\n')


if __name__ == '__main__':
    main()
