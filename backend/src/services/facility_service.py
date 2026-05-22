import json
from uuid import uuid4
from loguru import logger
from sqlalchemy import delete, select, update

from src.db import session_scope
from src.models.facility import Facility, FacilityArea, FacilityConfig
from src.models.order import Order
from src.services import config_service
from src.services.notification_service import record_event

_TEMPLATE_DEFINITION_KEYS = ("fax_template_id", "fax_template_ids", "fax_template_override")


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _contains_template_definition_change(current_config: dict, next_config: dict) -> bool:
    for key in _TEMPLATE_DEFINITION_KEYS:
        if key not in next_config:
            continue
        if _stable_json(next_config.get(key)) != _stable_json(current_config.get(key)):
            return True
    return False


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


def update_config(
    facility_id: str,
    config: dict,
    *,
    allow_authoritative_column_changes: bool = False,
) -> bool:
    updated = False
    with session_scope() as session:
        fac = ensure_facility_materialized(session, facility_id)
        if not fac:
            return False
        current_config = fac.config.config_json if fac.config and isinstance(fac.config.config_json, dict) else {}
        sanitized_config = config_service.sanitize_facility_config_for_storage(
            facility_id,
            config,
            current_config=current_config,
            allow_authoritative_column_changes=allow_authoritative_column_changes,
        )
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
    master = config_service.load_facility_master()
    merged: dict[str, dict] = {}
    for entry in master.get("facilities", []):
        if not isinstance(entry, dict):
            continue
        facility_id = str(entry.get("facility_id") or "").strip()
        name = str(entry.get("facility_name") or entry.get("name") or "").strip()
        if not facility_id or not name:
            continue
        merged[facility_id] = {
            "id": facility_id,
            "name": name,
            "areas": _normalize_area_payload(entry.get("areas")),
        }
    with session_scope() as session:
        facilities = session.execute(select(Facility)).scalars().all()
        for fac in facilities:
            merged[fac.id] = serialize_facility(fac)
    return list(merged.values())


def ensure_facility_materialized(session, facility_id: str) -> Facility | None:
    normalized_facility_id = str(facility_id or "").strip()
    if not normalized_facility_id:
        return None
    existing = session.get(Facility, normalized_facility_id)
    if existing is not None:
        return existing
    master = config_service.load_facility_master()
    for entry in master.get("facilities", []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("facility_id") or "").strip() != normalized_facility_id:
            continue
        name = str(entry.get("facility_name") or entry.get("name") or "").strip()
        if not name:
            return None
        fac = Facility(id=normalized_facility_id, name=name)
        session.add(fac)
        seen_area_ids: set[str] = set()
        for area in _normalize_area_payload(entry.get("areas")):
            if area["id"] in seen_area_ids:
                continue
            seen_area_ids.add(area["id"])
            session.add(FacilityArea(id=area["id"], facility_id=normalized_facility_id, name=area["name"]))
        session.flush()
        logger.info("Facility materialized from master", fac=normalized_facility_id)
        return fac
    return None


def upsert_facility_rows_from_master(session, master: dict) -> None:
    facilities = master.get("facilities") if isinstance(master, dict) else None
    if not isinstance(facilities, list):
        return
    for entry in facilities:
        if not isinstance(entry, dict):
            continue
        facility_id = str(entry.get("facility_id") or "").strip()
        name = str(entry.get("facility_name") or entry.get("name") or "").strip()
        if not facility_id or not name:
            continue
        fac = session.get(Facility, facility_id)
        if fac is None:
            fac = Facility(id=facility_id, name=name)
            session.add(fac)
            session.flush()
        elif fac.name != name:
            fac.name = name
        session.execute(delete(FacilityArea).where(FacilityArea.facility_id == facility_id))
        seen_area_ids: set[str] = set()
        for area in _normalize_area_payload(entry.get("areas")):
            if area["id"] in seen_area_ids:
                continue
            seen_area_ids.add(area["id"])
            session.add(FacilityArea(id=area["id"], facility_id=facility_id, name=area["name"]))


def _facility_config_from_master_entry(entry: dict) -> dict:
    config = {
        key: value
        for key, value in entry.items()
        if key not in {"facility_id", "facility_name", "name", "areas"}
    }
    if config:
        config.setdefault("facility_template_source", "db_override")
    return config


def upsert_facilities_and_configs_from_master(session, master: dict) -> None:
    upsert_facility_rows_from_master(session, master)
    facilities = master.get("facilities") if isinstance(master, dict) else None
    if not isinstance(facilities, list):
        return
    for entry in facilities:
        if not isinstance(entry, dict):
            continue
        facility_id = str(entry.get("facility_id") or "").strip()
        if not facility_id:
            continue
        config = _facility_config_from_master_entry(entry)
        session.execute(delete(FacilityConfig).where(FacilityConfig.facility_id == facility_id))
        if config:
            session.add(FacilityConfig(facility_id=facility_id, config_json=config))


def build_facility_master_from_db(base_master: dict) -> dict:
    master = dict(base_master or {})
    facilities: list[dict] = []
    with session_scope() as session:
        rows = session.execute(select(Facility).order_by(Facility.id.asc())).scalars().all()
        for fac in rows:
            entry = {
                "facility_id": fac.id,
                "facility_name": fac.name,
                "areas": [{"id": area.id, "name": area.name} for area in (fac.areas or [])],
            }
            if fac.config and isinstance(fac.config.config_json, dict):
                config = dict(fac.config.config_json)
                config.pop("facility_template_source", None)
                entry.update(config)
            facilities.append(entry)
    master["facilities"] = facilities
    return master


def get_facility(facility_id: str) -> dict | None:
    with session_scope() as session:
        fac = session.get(Facility, facility_id)
        if not fac:
            master = config_service.load_facility_master()
            for entry in master.get("facilities", []):
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("facility_id") or "").strip() != facility_id:
                    continue
                name = str(entry.get("facility_name") or entry.get("name") or "").strip()
                if not name:
                    return None
                return {
                    "id": facility_id,
                    "name": name,
                    "areas": _normalize_area_payload(entry.get("areas")),
                }
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
