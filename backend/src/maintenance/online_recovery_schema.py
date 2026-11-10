"""Only the generated old-business recovery image imports this compatibility gate."""
from sqlalchemy import inspect


def recovery_head_revision(connection):
    if connection.dialect.name != 'postgresql' or 'alembic_version' not in inspect(connection).get_table_names():
        raise RuntimeError('recovery requires a formally tracked PostgreSQL database')
    revisions = list(connection.exec_driver_sql('SELECT version_num FROM alembic_version').scalars())
    if len(revisions) != 1 or revisions[0] not in ('0038', '0039'):
        raise RuntimeError('recovery supports only reviewed catalogs 0038 and 0039')
    return revisions[0]
