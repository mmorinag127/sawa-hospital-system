"""Explicit facility master seed command; intentionally outside Alembic revision discovery."""

import json
import os
from pathlib import Path

from sqlalchemy import delete

from src.db import session_scope
from src.models.facility import Facility, FacilityArea, FacilityConfig


def _load_master(path: Path) -> dict:
    master = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(master, dict) or not isinstance(master.get("facilities"), list):
        raise ValueError("facility master must contain a facilities array")
    if not master["facilities"]:
        raise ValueError("facility master facilities array must not be empty")
    facility_ids = set()
    for facility in master["facilities"]:
        if not isinstance(facility, dict):
            raise ValueError("facility must be an object")
        for key in ("facility_id", "facility_name"):
            _require_text(facility, key)
        if facility["facility_id"] in facility_ids:
            raise ValueError("duplicate facility_id")
        facility_ids.add(facility["facility_id"])
        if not isinstance(facility.get("areas"), list):
            raise ValueError("facility must contain an areas array")
        area_ids = set()
        for area in facility["areas"]:
            if not isinstance(area, dict):
                raise ValueError("area must be an object")
            for key in ("area_id", "name"):
                _require_text(area, key)
            if area["area_id"] in area_ids:
                raise ValueError("duplicate area_id within facility")
            area_ids.add(area["area_id"])
    return master


def _require_text(item: dict, key: str) -> None:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a nonempty string")


def _extract_config(facility: dict) -> dict:
    keys = [
        "aliases",
        "bag_types",
        "fax_template_override",
        "packaging_policy_override",
        "label_profile_override",
        "fax_template",
        "packaging_policy",
        "label_profile",
        "invoice_template",
    ]
    return {key: facility[key] for key in keys if key in facility}


def seed_from_master(path: Path) -> int:
    master = _load_master(path)
    facilities = master["facilities"]
    count = 0
    with session_scope() as session:
        for facility in facilities:
            facility_id = facility["facility_id"]
            name = facility["facility_name"]
            existing = session.get(Facility, facility_id)
            if existing:
                existing.name = name
                session.execute(delete(FacilityArea).where(FacilityArea.facility_id == facility_id))
                session.execute(delete(FacilityConfig).where(FacilityConfig.facility_id == facility_id))
            else:
                session.add(Facility(id=facility_id, name=name))
            for area in facility["areas"]:
                session.add(FacilityArea(
                    id=area["area_id"],
                    facility_id=facility_id,
                    name=area["name"],
                ))
            config = _extract_config(facility)
            if config:
                session.add(FacilityConfig(facility_id=facility_id, config_json=config))
            count += 1
    return count


def main() -> int:
    value = os.environ.get("FACILITY_MASTER_PATH")
    if value is None or not value.strip():
        raise ValueError("FACILITY_MASTER_PATH is required and must not be blank")
    return seed_from_master(Path(value))


if __name__ == "__main__":
    print(f"Seeded facilities: {main()}")
