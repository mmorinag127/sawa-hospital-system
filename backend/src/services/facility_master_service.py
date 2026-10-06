from copy import deepcopy
from typing import Callable

from loguru import logger

from src.db import session_scope
from src.services import canonical_facility_store, facility_service
from src.services.config_validator import validate_facility_master


def get_snapshot() -> canonical_facility_store.MasterSnapshot:
    return canonical_facility_store.read_snapshot()


def get_master() -> dict:
    return get_snapshot().master


def resolve_facility_for_date(facility_id: str, effective_date: object) -> dict:
    return canonical_facility_store.resolve_facility_version(get_snapshot(), facility_id, effective_date)


def get_master_path() -> str:
    return canonical_facility_store.source_location()


def _validated(master: dict) -> dict:
    canonical_facility_store.validate_facility_versions(master)
    validation = validate_facility_master(master)
    if validation["errors"]:
        raise canonical_facility_store.CanonicalStoreError(
            "facility_master_validation_failed", status_code=400, errors=validation["errors"],
        )
    return master


def _project_snapshot(snapshot: canonical_facility_store.MasterSnapshot) -> dict:
    projection = {"status": "synced", "revision": snapshot.revision}
    try:
        canonical_facility_store.check_revision(get_snapshot(), snapshot.revision)
        with session_scope() as session:
            facility_service.upsert_facilities_and_configs_from_master(session, snapshot.master)
        canonical_facility_store.check_revision(get_snapshot(), snapshot.revision)
    except Exception:
        logger.exception("Canonical facility JSON saved; DB projection reconciliation required")
        projection = {"status": "reconcile_required", "revision": snapshot.revision}
    return projection


def reconcile_projection(*, expected_revision: str) -> dict:
    """Explicit maintenance: project a verified snapshot, never write JSON or choose a newer token."""
    snapshot = get_snapshot()
    canonical_facility_store.check_revision(snapshot, expected_revision)
    _validated(snapshot.master)
    return _project_snapshot(snapshot)


def mutate_master(expected_revision: str, transform: Callable[[dict], dict]) -> dict:
    snapshot = canonical_facility_store.update_snapshot(
        expected_revision, lambda current: _validated(transform(deepcopy(current))),
    )
    # JSON is already committed. Projection failure must never be reported as rollback.
    projection = _project_snapshot(snapshot)
    return {
        "facility_master": snapshot.master,
        "revision": snapshot.revision,
        "path": snapshot.source,
        "source": "canonical_json",
        "validation": validate_facility_master(snapshot.master),
        "projection": projection,
        "updated": True,
    }


def save_master(master: dict, *, expected_revision: str) -> dict:
    return mutate_master(expected_revision, lambda _current: deepcopy(master))
