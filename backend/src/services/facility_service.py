from uuid import uuid4
from loguru import logger
from sqlalchemy import delete, select, update

from src.db import session_scope, Base, engine
from src.models.facility import Facility, FacilityArea, FacilityConfig
from src.models.order import Order
from src.services import config_service
from src.services.notification_service import record_event


Base.metadata.create_all(bind=engine)


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
        for area in _normalize_area_payload(entry.get("areas")):
            session.add(FacilityArea(id=area["id"], facility_id=facility_id, name=area["name"]))
        logger.info("Facility created from master", fac=facility_id)


def create_facility(name: str, areas: list | None):
    fac_id = f"FAC{uuid4().hex[:6]}"
    with session_scope() as session:
        fac = Facility(id=fac_id, name=name)
        session.add(fac)
        for area in _normalize_area_payload(areas):
            session.add(FacilityArea(id=area["id"], facility_id=fac_id, name=area["name"]))
        session.flush()
        session.refresh(fac)
        logger.info("Facility created", fac=fac_id)
        record_event("facility_create", actor="system", target=fac_id, fac=fac_id)
        return serialize_facility(fac)


def update_facility(facility_id: str, name: str | None, areas: list | None) -> dict | None:
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
        record_event(
            "facility_update",
            actor="system",
            target=facility_id,
            fac=facility_id,
            metadata={"name": fac.name, "areas": len(fac.areas or [])},
        )
        return serialize_facility(fac)


def update_config(facility_id: str, config: dict) -> bool:
    with session_scope() as session:
        fac = session.get(Facility, facility_id)
        if not fac:
            return False
        # replace config
        session.execute(delete(FacilityConfig).where(FacilityConfig.facility_id == facility_id))
        session.add(FacilityConfig(facility_id=facility_id, config_json=config))
        logger.info("Facility config updated", fac=facility_id)
        record_event(
            "facility_config_update",
            actor="system",
            target=facility_id,
            fac=facility_id,
            metadata={"keys": list(config.keys())},
        )
        return True


def list_facilities() -> list[dict]:
    with session_scope() as session:
        _sync_facilities_from_master(session)
        facilities = session.execute(select(Facility)).scalars().all()
        return [serialize_facility(fac) for fac in facilities]


def get_facility(facility_id: str) -> dict | None:
    with session_scope() as session:
        _sync_facilities_from_master(session)
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
