"""Required schema checks and facility projection run before serving requests."""
from src import db
from src.maintenance.formal_migration_schema import require_formal_head


def initialize_runtime():
    from src.services import facility_service, facility_template_version_service, menu_service

    with db.engine.connect() as connection:
        require_formal_head(connection)
    menu_service.ensure_menu_schema()
    facility_template_version_service.ensure_facility_template_version_schema()
    facility_service.sync_facility_names_from_master()
