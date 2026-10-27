import json
from copy import deepcopy
from uuid import uuid4
from loguru import logger
from sqlalchemy import delete, select

from src.db import session_scope
from src.models.facility import Facility, FacilityArea, FacilityConfig
from src.services import canonical_facility_store, config_service
from src.services.notification_service import record_event

_TEMPLATE_DEFINITION_KEYS = ("fax_template_id", "fax_template_ids", "fax_template_override")
_TEMPLATE_COLUMN_KEYS = ("columns", "main_ocr_row_fields", "columns_authoritative")


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _contains_template_definition_change(current_config: dict, next_config: dict) -> bool:
    for key in _TEMPLATE_DEFINITION_KEYS:
        if key not in next_config:
            continue
        current_value, next_value = current_config.get(key), next_config.get(key)
        if key == "fax_template_override" and isinstance(next_value, dict):
            if current_value is not None and not isinstance(current_value, dict):
                return True
            current_override = current_value if isinstance(current_value, dict) else {}
            # Geometry/recognizer settings stay editable; authored column identity is not inferred or rewritten.
            for column_key in _TEMPLATE_COLUMN_KEYS:
                if (column_key in current_override) != (column_key in next_value) or \
                        _stable_json(current_override.get(column_key)) != _stable_json(next_value.get(column_key)):
                    return True
            continue
        if _stable_json(next_value) != _stable_json(current_value):
            return True
    return False


_UNSET = object()


def _new_area_payload(areas: list | None) -> list | None:
    if areas is None:
        return None
    if not isinstance(areas, list):
        raise canonical_facility_store.CanonicalStoreError("facility_areas_invalid", status_code=400)
    created = deepcopy(areas)
    for index, area in enumerate(created):
        if isinstance(area, str):
            created[index] = {"id": f"ARE{uuid4().hex[:6]}", "name": area}
        elif isinstance(area, dict) and "id" not in area and "area_id" not in area:
            area["id"] = f"ARE{uuid4().hex[:6]}"
    return created


def _projection_areas(areas: list | None) -> list[dict]:
    if areas is None:
        return []
    if not isinstance(areas, list):
        raise canonical_facility_store.CanonicalStoreError("facility_area_projection_invalid")
    projected = []
    seen = set()
    for area in areas:
        if not isinstance(area, dict):
            raise canonical_facility_store.CanonicalStoreError("facility_area_projection_identity_missing")
        if "id" in area and "area_id" in area and area["id"] != area["area_id"]:
            raise canonical_facility_store.CanonicalStoreError("facility_area_projection_identity_ambiguous")
        area_id = area.get("area_id") if "area_id" in area else area.get("id")
        name = area.get("name") if "name" in area else area.get("label")
        if not isinstance(area_id, str) or not area_id.strip() or not isinstance(name, str) or not name.strip():
            raise canonical_facility_store.CanonicalStoreError("facility_area_projection_identity_missing")
        if area_id in seen:
            raise canonical_facility_store.CanonicalStoreError("facility_area_projection_identity_duplicate")
        seen.add(area_id)
        projected.append({"id": area_id, "name": name})
    return projected


def _sync_facilities_from_master(session) -> None:
    master = config_service.load_facility_master()
    upsert_facility_rows_from_master(session, master)


def _entry(master: dict, facility_id: str) -> dict | None:
    return config_service.get_facility_by_id(facility_id, master=master)


def _catalog_entry(entry: dict) -> dict:
    name = entry.get("facility_name")
    if not isinstance(name, str) or not name.strip():
        raise canonical_facility_store.CanonicalStoreError("facility_name_unconfigured")
    areas = deepcopy(entry.get("areas", []))
    return {"id": entry["facility_id"], "name": name, "areas": areas}


def facility_response(master: dict, facility_id: str, revision: str) -> dict:
    entry = _entry(master, facility_id)
    if entry is None:
        raise canonical_facility_store.CanonicalStoreError("facility_not_found", status_code=404)
    return {
        "facility": _catalog_entry(entry),
        "config": _facility_config_from_master_entry(entry),
        "resolved_config": config_service.get_facility_config(facility_id, master=master),
        "revision": revision,
        "source": "canonical_json",
        "order_form_patterns": deepcopy(master.get("order_form_patterns", [])),
    }


def _command_response(saved: dict, facility_id: str) -> dict:
    from src.services.config_validator import validate_facility_config

    response = facility_response(saved["facility_master"], facility_id, saved["revision"])
    return {**response, "updated": True, "projection": saved["projection"],
            "validation": validate_facility_config(response["config"])}


def versions_response(master: dict, facility_id: str, revision: str) -> dict:
    versions = canonical_facility_store.facility_versions(master, facility_id)
    reference = _entry(master, facility_id)
    return {"facility_id": facility_id, "versions": versions, "revision": revision, "source": "canonical_json",
            "reference": deepcopy({key: value for key, value in reference.items() if key != "versions"}),
            "reference_status": "unversioned_reference_only"}


def save_facility_version(facility_id: str, version_id: str, version: dict, *, expected_revision: str, create: bool) -> dict:
    from src.services import facility_master_service

    if not isinstance(version, dict) or version.get("version_id") != version_id:
        raise canonical_facility_store.CanonicalStoreError("facility_version_target_mismatch", status_code=422)
    canonical_facility_store.facility_versions(
        {"facilities": [{"facility_id": facility_id, "versions": [version]}]}, facility_id,
    )
    def transform(master: dict) -> dict:
        entry = next((item for item in master["facilities"] if item["facility_id"] == facility_id), None)
        if entry is None:
            raise canonical_facility_store.CanonicalStoreError("facility_not_found", status_code=404)
        existing = entry.get("versions", [])
        if not isinstance(existing, list) or any(not isinstance(item, dict) for item in existing):
            raise canonical_facility_store.CanonicalStoreError("facility_versions_invalid", status_code=422)
        targets = [index for index, item in enumerate(existing) if item.get("version_id") == version_id]
        if (create and targets) or (not create and len(targets) != 1):
            raise canonical_facility_store.CanonicalStoreError("facility_version_create_conflict" if create else "facility_version_edit_target_missing", status_code=409)
        next_versions = deepcopy(existing)
        if create:
            next_versions = canonical_facility_store.append_facility_version(existing, version)
        else:
            if existing[targets[0]] != version:
                raise canonical_facility_store.CanonicalStoreError("facility_version_requires_new_version", status_code=409)
            next_versions[targets[0]] = deepcopy(version)
        entry["versions"] = next_versions
        return master
    saved = facility_master_service.mutate_master(expected_revision, transform)
    response = versions_response(saved["facility_master"], facility_id, saved["revision"])
    own_version = next(item for item in response["versions"] if item["version_id"] == version_id)
    return {**response, "version": own_version, "updated": True, "projection": {**saved["projection"], "scope": "unversioned_reference_only"}}


def _record_command_event(response: dict, event: str, facility_id: str, **metadata) -> dict:
    try:
        record_event(event, actor="system", target=facility_id, fac=facility_id, metadata=metadata)
        response["event"] = {"status": "recorded"}
    except Exception:
        logger.exception("Facility JSON saved; notification event recording failed")
        response["event"] = {"status": "record_failed"}
    return response


def create_facility(name: str, areas: list | None, *, expected_revision: str):
    from src.services import facility_master_service

    fac_id = f"FAC{uuid4().hex[:6]}"
    def transform(master: dict) -> dict:
        master["facilities"].append({"facility_id": fac_id, "facility_name": name,
                                     "areas": _new_area_payload(areas)})
        facility_response(master, fac_id, expected_revision)
        return master
    saved = facility_master_service.mutate_master(expected_revision, transform)
    return _record_command_event(_command_response(saved, fac_id), "facility_create", fac_id)


def update_facility(facility_id: str, name: str | None, areas=_UNSET, *, expected_revision: str) -> dict:
    from src.services import facility_master_service

    def transform(master: dict) -> dict:
        entry = next((item for item in master["facilities"] if item["facility_id"] == facility_id), None)
        if entry is None:
            raise canonical_facility_store.CanonicalStoreError("facility_not_found", status_code=404)
        if name:
            entry["facility_name"] = name
        if areas is not _UNSET:
            entry["areas"] = deepcopy(areas)
        facility_response(master, facility_id, expected_revision)
        return master
    saved = facility_master_service.mutate_master(expected_revision, transform)
    response = _command_response(saved, facility_id)
    return _record_command_event(response, "facility_update", facility_id,
                                 name=response["facility"]["name"], areas=len(response["facility"]["areas"]) if isinstance(response["facility"]["areas"], list) else None)


def update_config(
    facility_id: str,
    config: dict,
    *,
    expected_revision: str,
    allow_authoritative_column_changes: bool = False,
) -> dict:
    from src.services import facility_master_service
    from src.services.config_validator import validate_facility_config

    def transform(master: dict) -> dict:
        current = _entry(master, facility_id)
        if current is None:
            raise canonical_facility_store.CanonicalStoreError("facility_not_found", status_code=404)
        current_config = _facility_config_from_master_entry(current)
        if not allow_authoritative_column_changes and _contains_template_definition_change(current_config, config):
            raise canonical_facility_store.CanonicalStoreError(
                "facility_template_definition_update_requires_versioned_template_endpoint", status_code=400,
            )
        next_config = deepcopy(config)
        # Omitted template keys are not authority to erase the canonical template.
        for key in _TEMPLATE_DEFINITION_KEYS:
            if key not in next_config and key in current_config:
                next_config[key] = deepcopy(current_config[key])
        validation = validate_facility_config(next_config)
        if validation["errors"]:
            raise canonical_facility_store.CanonicalStoreError(
                "facility_config_validation_failed", status_code=400, errors=validation["errors"],
            )
        identity = {key: deepcopy(value) for key, value in current.items()
                    if key in {"facility_id", "facility_name", "name", "areas"}}
        replacement = {**next_config, **identity}
        master["facilities"] = [replacement if item["facility_id"] == facility_id else item
                                for item in master["facilities"]]
        facility_response(master, facility_id, expected_revision)
        return master
    saved = facility_master_service.mutate_master(expected_revision, transform)
    return _record_command_event(_command_response(saved, facility_id), "facility_config_update", facility_id,
                                 keys=list(config.keys()))


def list_facilities(*, master: dict | None = None) -> list[dict]:
    master = config_service.load_facility_master() if master is None else master
    return [_catalog_entry(entry) for entry in master["facilities"]]


def ensure_facility_materialized(session, facility_id: str) -> Facility | None:
    normalized_facility_id = str(facility_id or "").strip()
    if not normalized_facility_id:
        return None
    master = config_service.load_facility_master()
    if _entry(master, normalized_facility_id) is None:
        return None
    existing = session.get(Facility, normalized_facility_id)
    if existing is not None:
        return existing
    for entry in master.get("facilities", []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("facility_id") or "").strip() != normalized_facility_id:
            continue
        name = entry.get("facility_name")
        if not isinstance(name, str) or not name.strip():
            raise canonical_facility_store.CanonicalStoreError("facility_name_unconfigured")
        fac = Facility(id=normalized_facility_id, name=name)
        session.add(fac)
        seen_area_ids: set[str] = set()
        for area in _projection_areas(entry.get("areas")):
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
        facility_id = entry.get("facility_id")
        name = entry.get("facility_name")
        if not isinstance(facility_id, str) or not facility_id.strip() or not isinstance(name, str) or not name.strip():
            raise canonical_facility_store.CanonicalStoreError("facility_projection_identity_missing")
        fac = session.get(Facility, facility_id)
        if fac is None:
            fac = Facility(id=facility_id, name=name)
            session.add(fac)
            session.flush()
        elif fac.name != name:
            fac.name = name
        session.execute(delete(FacilityArea).where(FacilityArea.facility_id == facility_id))
        seen_area_ids: set[str] = set()
        for area in _projection_areas(entry.get("areas")):
            if area["id"] in seen_area_ids:
                continue
            seen_area_ids.add(area["id"])
            session.add(FacilityArea(id=area["id"], facility_id=facility_id, name=area["name"]))


def sync_facility_names_from_master() -> int:
    master = config_service.load_facility_master()
    facilities = master.get("facilities") if isinstance(master, dict) else None
    if not isinstance(facilities, list):
        return 0
    updated = 0
    with session_scope() as session:
        facility_ids = [entry.get("facility_id") for entry in facilities if isinstance(entry, dict)]
        existing = {fac.id: fac for fac in session.scalars(select(Facility).where(Facility.id.in_(
            [facility_id for facility_id in facility_ids if isinstance(facility_id, str)]
        )))}
        for entry in facilities:
            if not isinstance(entry, dict):
                continue
            facility_id = entry.get("facility_id")
            name = entry.get("facility_name")
            if not isinstance(facility_id, str) or not facility_id.strip() or not isinstance(name, str) or not name.strip():
                raise canonical_facility_store.CanonicalStoreError("facility_projection_identity_missing")
            fac = existing.get(facility_id)
            if fac is None:
                fac = Facility(id=facility_id, name=name)
                session.add(fac)
                existing[facility_id] = fac
                updated += 1
            elif fac.name != name:
                fac.name = name
                updated += 1
    return updated


def _facility_config_from_master_entry(entry: dict) -> dict:
    return {
        key: value
        for key, value in entry.items()
        if key not in {"facility_id", "facility_name", "name", "areas"}
    }


def upsert_facilities_and_configs_from_master(session, master: dict) -> None:
    upsert_facility_rows_from_master(session, master)
    facilities = master.get("facilities") if isinstance(master, dict) else None
    if not isinstance(facilities, list):
        return
    for entry in facilities:
        if not isinstance(entry, dict):
            continue
        facility_id = entry.get("facility_id")
        if not isinstance(facility_id, str) or not facility_id.strip():
            raise canonical_facility_store.CanonicalStoreError("facility_projection_identity_missing")
        config = _facility_config_from_master_entry(entry)
        session.execute(delete(FacilityConfig).where(FacilityConfig.facility_id == facility_id))
        if config:
            session.add(FacilityConfig(facility_id=facility_id, config_json=config))


def build_facility_master_from_db(base_master: dict) -> dict:
    raise canonical_facility_store.CanonicalStoreError("facility_master_db_authority_disabled")


def get_facility(facility_id: str) -> dict | None:
    entry = config_service.get_facility_by_id(facility_id)
    return _catalog_entry(entry) if entry is not None else None


def get_facility_config(facility_id: str) -> dict | None:
    entry = config_service.get_facility_by_id(facility_id)
    return _facility_config_from_master_entry(entry) if entry is not None else None


def serialize_facility(fac: Facility):
    areas = [{"id": area.id, "name": area.name} for area in (fac.areas or [])]
    return {"id": fac.id, "name": fac.name, "areas": areas}
