"""Upgrade only the current coordinator's fresh unpublished staging copy."""
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, StrictStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from . import staging_data as datasets, staging_copy_schema as proof
from .db import configure_database
from .schema_contract import assert_migration_source

router = APIRouter(prefix='/internal', tags=['staging-copy'])


class MigrationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: StrictStr
    attempt: StrictStr
    database: StrictStr
    expected_tables: dict[str, dict]
    source_heads: list[StrictStr]
    target_heads: list[StrictStr]


@router.post('/staging-copy-migration')
def upgrade(body: MigrationRequest):
    if not datasets.staging_runtime():
        raise HTTPException(404, 'Not found')
    database = None
    try:
        proof.require_attempt(datasets, body.request_id, body.attempt, body.database)
        base = make_url(configure_database())
        host = base.query.get('host', base.host or '')
        if (base.get_backend_name() != 'postgresql' or host != '/cloudsql/sawahospitalsystem:asia-northeast2:shift-stg'
                or base.database.startswith('sawa_replay_') or not base.username or not base.password):
            raise ValueError('copy_migration_staging_database_required')
        database = create_engine(base.set(database=body.database), hide_parameters=True)
        with database.connect() as connection:
            if connection.execute(text('SELECT current_database()')).scalar_one() != body.database:
                raise ValueError('copy_migration_target_mismatch')
            original, before = proof.preserve_before(connection, body.expected_tables)
            config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
            config.set_main_option('script_location', str(Path(__file__).resolve().parents[1] / 'migrations'))
            config.attributes.update(connection=connection, database_url=database.url.render_as_string(hide_password=False))
            script = ScriptDirectory.from_config(config)
            target = script.get_current_head()
            if [target] != sorted(body.target_heads):
                raise ValueError('copy_migration_formal_head_changed')
            source = assert_migration_source(connection, script)
            if source is None or [source] != sorted(body.source_heads):
                raise ValueError('copy_migration_source_head_mismatch')
            connection.rollback()
            receipt = {}
            def postflight(current, revision):
                if revision != target:
                    raise ValueError('copy_migration_formal_head_changed')
                proof.require_preserved(current, original, before)
                proof.require_attempt(datasets, body.request_id, body.attempt, body.database)
                receipt.update(existing_values_preserved=True, preserved_tables=before)
            config.attributes['catalog_observer'] = postflight
            command.upgrade(config, target)
            if not receipt:
                raise ValueError('copy_migration_unverified')
            return {'database': body.database, **receipt}
    except Exception:
        # No URLs, credentials or raw SQL diagnostics cross the API boundary.
        raise HTTPException(409, 'staging_copy_migration_failed') from None
    finally:
        if database is not None:
            database.dispose()
