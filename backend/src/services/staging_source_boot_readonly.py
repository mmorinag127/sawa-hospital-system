"""Read-only startup checks for the approved API-only production hotfix."""
from sqlalchemy import inspect, select
from src.db import engine
from src.models.facility import Facility
from src.services import config_service
from src.api.staging_source_settings import _file


def require_initialized_runtime():
    """Fail startup on missing state; never repair schema or rewrite facility names."""
    if engine.dialect.name != 'postgresql':
        raise RuntimeError('Initialized production PostgreSQL is required')
    required = {'menu_masters': {'condiments'}, 'menu_facility_overrides': {'menu_master_id', 'facility_id'},
                'monthly_menu_items': {'menu_master_id', 'master_resolution_mode', 'bag_max_qty', 'bag_max_unit'},
                'monthly_menu_entries': {'facility_override'},
                'facility_template_versions': {'config_json', 'valid_from', 'valid_to'}}
    _, master = _file(config_service.FACILITY_MASTER_PATH)
    with engine.connect().execution_options(isolation_level='REPEATABLE READ', postgresql_readonly=True) as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        if not set(required) <= tables:
            raise RuntimeError('Existing production schema is required')
        for table, names in required.items():
            if not names <= {row['name'] for row in inspector.get_columns(table)}:
                raise RuntimeError('Existing production columns are required')
        indexes = {row['name'] for row in inspector.get_indexes('monthly_menu_items')}
        if 'uq_monthly_menu_items_scope_identity' not in indexes:
            raise RuntimeError('Existing production menu identity index is required')
        indexes = {row['name'] for row in inspector.get_indexes('facility_template_versions')}
        if not {'ix_facility_template_versions_valid_from', 'ix_facility_template_versions_valid_to'} <= indexes:
            raise RuntimeError('Existing production template indexes are required')
        names = dict(connection.execute(select(Facility.id, Facility.name)).all())
        for item in master['facilities']:
            ident = str(item.get('facility_id') or '').strip()
            name = str(item.get('facility_name') or item.get('name') or '').strip()
            if ident and name and names.get(ident) != name:
                raise RuntimeError('Production facility source changed; automatic name synchronization is forbidden')
        connection.rollback()
