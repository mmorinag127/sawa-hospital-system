"""Required schema checks and facility projection run before serving requests."""
from contextlib import contextmanager
from contextvars import ContextVar

from src import db
from src.maintenance.formal_migration_schema import require_formal_head

_completed_startup = ContextVar("completed_runtime_startup", default=False)


@contextmanager
def completed_startup_scope():
    token = _completed_startup.set(True)
    try:
        yield
    finally:
        _completed_startup.reset(token)


def initialize_runtime():
    if _completed_startup.get():
        return
    from src.services import facility_service, facility_template_version_service, menu_service

    with db.engine.connect() as connection:
        require_formal_head(connection)
    menu_service.ensure_menu_schema()
    facility_template_version_service.ensure_facility_template_version_schema()
    facility_service.sync_facility_names_from_master()
