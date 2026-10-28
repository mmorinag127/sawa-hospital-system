"""Required schema checks and facility projection run before serving requests."""
from contextlib import contextmanager
from contextvars import ContextVar
import logging
from time import perf_counter

from src import db
from src.maintenance.formal_migration_schema import require_formal_head

_completed_startup = ContextVar("completed_runtime_startup", default=False)
_logger = logging.getLogger("uvicorn.error")


def _startup_step(name, function):
    started = perf_counter()
    succeeded = False
    try:
        result = function()
        succeeded = True
        return result
    finally:
        _logger.info("runtime_startup_timing phase=%s seconds=%.6f succeeded=%s",
                     name, perf_counter() - started, succeeded)


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

    def formal_schema():
        with db.engine.connect() as connection:
            require_formal_head(connection)

    _startup_step("formal_schema", formal_schema)
    _startup_step("menu_schema", menu_service.ensure_menu_schema)
    _startup_step("facility_template_schema", facility_template_version_service.ensure_facility_template_version_schema)
    _startup_step("facility_projection", facility_service.sync_facility_names_from_master)
