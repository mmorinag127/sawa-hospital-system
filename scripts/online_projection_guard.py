"""Transaction-local guard for the single reviewed 0038 -> 0039 projection change."""
import hashlib
from sqlalchemy import text


def projection_snapshot(connection):
    digest = hashlib.sha256()
    rows = connection.exec_driver_sql("""SELECT id, md5(COALESCE(metrics::text,'SQL_NULL')),
        md5(COALESCE(progress_metrics::text,'SQL_NULL')),
        md5(COALESCE(display_read_json::jsonb::text,'SQL_NULL'))
        FROM ocr_jobs ORDER BY id""").fetchall()
    for row in rows:
        for field in row:
            encoded = str(field).encode()
            digest.update(len(encoded).to_bytes(8, 'big') + encoded)
    return {"rows": len(rows), "sha256": digest.hexdigest()}


def begin_projection_upgrade(connection):
    from src.maintenance.formal_migration_schema import formal_head_revision
    if connection.dialect.name != 'postgresql' or formal_head_revision() != '0039':
        raise RuntimeError('online release only supports PostgreSQL head 0039')
    version = list(connection.exec_driver_sql('SELECT version_num FROM alembic_version').scalars())
    if version not in (['0038'], ['0039']):
        raise RuntimeError('online release requires exact revision 0038 or 0039')
    connection.exec_driver_sql("SET LOCAL lock_timeout='2s'")
    connection.exec_driver_sql("SET LOCAL statement_timeout='20s'")
    connection.exec_driver_sql('LOCK TABLE ocr_jobs IN ACCESS EXCLUSIVE MODE')
    running = connection.execute(text("SELECT count(*) FROM ocr_jobs WHERE status='running'")).scalar_one()
    if running:
        raise RuntimeError('online projection migration blocked by active OCR jobs')
    return projection_snapshot(connection)


def finish_projection_upgrade(connection, before):
    after = projection_snapshot(connection)
    if before != after:
        raise RuntimeError('online projection changed existing input or display values')
    return after
