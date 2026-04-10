from datetime import datetime, timedelta
import threading
from uuid import uuid4
from loguru import logger
from sqlalchemy import delete, select, update, inspect, text

from src.db import session_scope, Base, engine
from src.models.facility import Facility, FacilityArea, FacilityConfig
from src.models.order import Order
from src.services import config_service
from src.services.notification_service import record_event


def _ensure_facility_area_pk() -> None:
    if engine.dialect.name == "sqlite":
        return
    inspector = inspect(engine)
    if "facility_areas" not in inspector.get_table_names():
        return
    pk = inspector.get_pk_constraint("facility_areas") or {}
    cols = pk.get("constrained_columns") or []
    if set(cols) == {"facility_id", "id"}:
        return
    constraint_name = pk.get("name") or "facility_areas_pkey"
    with engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE facility_areas DROP CONSTRAINT IF EXISTS "{constraint_name}"'))
        conn.execute(text("ALTER TABLE facility_areas ADD PRIMARY KEY (facility_id, id)"))


Base.metadata.create_all(bind=engine)
_ensure_facility_area_pk()

_SYNC_LOCK = threading.Lock()
_SYNC_DONE = False
_SYNC_LAST_ERROR_AT: datetime | None = None
_SYNC_RETRY_WINDOW = timedelta(seconds=60)


def _ensure_facility_sync(session) -> None:
    global _SYNC_DONE, _SYNC_LAST_ERROR_AT
    if _SYNC_DONE:
        return
    if _SYNC_LAST_ERROR_AT and datetime.utcnow() - _SYNC_LAST_ERROR_AT < _SYNC_RETRY_WINDOW:
        return
    with _SYNC_LOCK:
        if _SYNC_DONE:
            return
        if _SYNC_LAST_ERROR_AT and datetime.utcnow() - _SYNC_LAST_ERROR_AT < _SYNC_RETRY_WINDOW:
            return
        try:
            _sync_facilities_from_master(session)
            _SYNC_DONE = True
            _SYNC_LAST_ERROR_AT = None
        except Exception as exc:  # noqa: BLE001
            _SYNC_LAST_ERROR_AT = datetime.utcnow()
            logger.warning("Facility sync failed", error=str(exc))


def _normalize_area_payload(areas: list | None) -> list[dict]:
    normalized: list[dict] = []
    for area in areas or []:
        if isinstance(area, str):
            name = area.strip()
            if not name:
                continue
            normalized.append({"id": f"ARE{uuid4().hex[:6]}", "name": name})
            continue
        if isinstance(area, dict):
            name = (area.get("name") or area.get("label") or "").strip()
            if not name:
                continue
            area_id = (area.get("id") or area.get("area_id") or "").strip()
            if not area_id:
                area_id = f"ARE{uuid4().hex[:6]}"
            normalized.append({"id": area_id, "name": name})
    return normalized


def _sync_facilities_from_master(session) -> None:
    master = config_service.load_facility_master()
    facilities = master.get("facilities")
    if not isinstance(facilities, list) or not facilities:
        return
    existing = {fac.id: fac for fac in session.execute(select(Facility)).scalars().all()}
    existing_by_name = {fac.name: fac for fac in existing.values()}
    for entry in facilities:
        if not isinstance(entry, dict):
            continue
        facility_id = str(entry.get("facility_id") or "").strip()
        name = str(entry.get("facility_name") or "").strip()
        if not facility_id or not name:
            continue
        if facility_id in existing:
            fac = existing[facility_id]
            if fac.name != name:
                fac.name = name
            continue
        name_match = existing_by_name.get(name)
        if name_match and name_match.id != facility_id:
            old_id = name_match.id
            session.execute(update(Facility).where(Facility.id == old_id).values(id=facility_id))
            session.execute(
                update(FacilityArea).where(FacilityArea.facility_id == old_id).values(facility_id=facility_id)
            )
            session.execute(
                update(FacilityConfig).where(FacilityConfig.facility_id == old_id).values(facility_id=facility_id)
            )
            session.execute(
                update(Order).where(Order.facility_code == old_id).values(facility_code=facility_id)
            )
            logger.info("Facility ID remapped", old_id=old_id, new_id=facility_id)
            continue
        fac = Facility(id=facility_id, name=name)
        session.add(fac)
        seen_area_ids: set[str] = set()
        for area in _normalize_area_payload(entry.get("areas")):
            if area["id"] in seen_area_ids:
                continue
            seen_area_ids.add(area["id"])
            session.add(FacilityArea(id=area["id"], facility_id=facility_id, name=area["name"]))
        logger.info("Facility created from master", fac=facility_id)


def create_facility(name: str, areas: list | None):
    fac_id = f"FAC{uuid4().hex[:6]}"
    serialized: dict | None = None
    with session_scope() as session:
        fac = Facility(id=fac_id, name=name)
        session.add(fac)
        for area in _normalize_area_payload(areas):
            session.add(FacilityArea(id=area["id"], facility_id=fac_id, name=area["name"]))
        session.flush()
        session.refresh(fac)
        logger.info("Facility created", fac=fac_id)
        serialized = serialize_facility(fac)
    record_event("facility_create", actor="system", target=fac_id, fac=fac_id)
    return serialized


def update_facility(facility_id: str, name: str | None, areas: list | None) -> dict | None:
    serialized: dict | None = None
    event_metadata: dict[str, object] | None = None
    with session_scope() as session:
        fac = session.get(Facility, facility_id)
        if not fac:
            return None
        if name:
            fac.name = name
        if areas is not None:
            session.execute(delete(FacilityArea).where(FacilityArea.facility_id == facility_id))
            for area in _normalize_area_payload(areas):
                session.add(FacilityArea(id=area["id"], facility_id=facility_id, name=area["name"]))
        session.flush()
        session.refresh(fac)
        logger.info("Facility updated", fac=facility_id)
        serialized = serialize_facility(fac)
        event_metadata = {"name": fac.name, "areas": len(fac.areas or [])}
    record_event(
        "facility_update",
        actor="system",
        target=facility_id,
        fac=facility_id,
        metadata=event_metadata,
    )
    return serialized


def update_config(facility_id: str, config: dict) -> bool:
    updated = False
    sanitized_config = config_service.sanitize_facility_config_for_storage(facility_id, config)
    with session_scope() as session:
        _ensure_facility_sync(session)
        session.flush()
        fac = session.get(Facility, facility_id)
        if not fac:
            _sync_facilities_from_master(session)
            session.flush()
            fac = session.get(Facility, facility_id)
        if not fac:
            return False
        # replace config
        session.execute(delete(FacilityConfig).where(FacilityConfig.facility_id == facility_id))
        session.add(FacilityConfig(facility_id=facility_id, config_json=sanitized_config))
        logger.info("Facility config updated", fac=facility_id)
        updated = True
    if updated:
        record_event(
            "facility_config_update",
            actor="system",
            target=facility_id,
            fac=facility_id,
            metadata={"keys": list(sanitized_config.keys())},
        )
    return updated


def list_facilities() -> list[dict]:
    with session_scope() as session:
        _ensure_facility_sync(session)
        facilities = session.execute(select(Facility)).scalars().all()
        return [serialize_facility(fac) for fac in facilities]


def get_facility(facility_id: str) -> dict | None:
    with session_scope() as session:
        _ensure_facility_sync(session)
        fac = session.get(Facility, facility_id)
        if not fac:
            return None
        return serialize_facility(fac)


def get_facility_config(facility_id: str) -> dict | None:
    with session_scope() as session:
        fac = session.get(Facility, facility_id)
        if not fac or not fac.config:
            return None
        return fac.config.config_json or {}


def serialize_facility(fac: Facility):
    areas = [{"id": area.id, "name": area.name} for area in (fac.areas or [])]
    return {"id": fac.id, "name": fac.name, "areas": areas}
