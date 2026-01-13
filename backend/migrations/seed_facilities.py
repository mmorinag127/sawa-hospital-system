import json
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete

from src.db import session_scope, Base, engine
from src.models.facility import Facility, FacilityArea, FacilityConfig

Base.metadata.create_all(bind=engine)


def _load_master(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
    config = {key: facility[key] for key in keys if key in facility}
    return config


def seed_from_master(path: Path) -> int:
    master = _load_master(path)
    facilities = master.get("facilities", [])
    if not facilities:
        return 0
    count = 0
    with session_scope() as session:
        for facility in facilities:
            facility_id = facility.get("facility_id") or f"FAC{uuid4().hex[:6]}"
            name = facility.get("facility_name") or "Unnamed"
            existing = session.get(Facility, facility_id)
            if existing:
                existing.name = name
                session.execute(delete(FacilityArea).where(FacilityArea.facility_id == facility_id))
                session.execute(delete(FacilityConfig).where(FacilityConfig.facility_id == facility_id))
                fac = existing
            else:
                fac = Facility(id=facility_id, name=name)
                session.add(fac)

            for area in facility.get("areas", []):
                area_id = area.get("area_id") or f"ARE{uuid4().hex[:6]}"
                area_name = area.get("name") or "Area"
                session.add(FacilityArea(id=area_id, facility_id=facility_id, name=area_name))

            config = _extract_config(facility)
            if config:
                session.add(FacilityConfig(facility_id=facility_id, config_json=config))
            count += 1
    return count


if __name__ == "__main__":
    default_path = Path(__file__).resolve().parents[2] / "src" / "data" / "facility_master.template.json"
    path = Path(os.getenv("FACILITY_MASTER_PATH", default_path))
    total = seed_from_master(path)
    print(f"Seeded facilities: {total}")
