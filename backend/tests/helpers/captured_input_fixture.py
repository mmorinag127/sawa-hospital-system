"""Explicit toy source authoring for captured-input component tests, not OCR proof."""
from copy import deepcopy
from datetime import date
import json

from src.db import session_scope
from src.models.facility import Facility
from src.models.menu import MonthlyMenu, MonthlyMenuEntry, MonthlyMenuItem, MenuMaster
from src.services import canonical_facility_lineage as lineage, config_service

DAY = date(2026, 10, 7)
PERIOD = "2026-10@2026-10-05~2026-10-11"


def authored_input(monkeypatch, tmp_path, configured, facility_id):
    """Author the supplied toy settings; selection/projection/capture are real."""
    facility = deepcopy(configured["facility"])
    facility["facility_id"] = facility_id
    facility["facility_name"] = "Synthetic captured input facility"
    facility.setdefault("areas", [])
    template = facility["fax_template"]
    template.setdefault("template_id", "own")
    template.setdefault("page", 1)
    if not template.get("columns"):
        # Toy sheet identities: date/daypart/menu and explicit aggregate regular.
        template["columns"] = [
            {"index": 0, "source_index": 0, "role": "date", "name": "date"},
            {"index": 1, "source_index": 1, "role": "daypart", "name": "daypart"},
            {"index": 2, "source_index": 2, "role": "menu", "name": "menu"},
            {"index": 3, "source_index": 3, "role": "quantity", "name": "qty.regular", "name_locked": True, "diet_type": "regular", "area_id": None},
        ]
    master = tmp_path / "captured-facilities.json"
    master.write_text(json.dumps({"facilities": [{"facility_id": facility_id,
        "facility_name": facility["facility_name"], "versions": [{
            "version_id": "captured-original", "valid_from": "2026-10-05",
            "valid_to": "2026-10-11", "facility": facility}]}]}))
    policy = tmp_path / "captured-policy.json"
    policy.write_text(json.dumps(configured["policy"]))
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(master))
    monkeypatch.setattr(config_service, "INGEST_POLICY_PATH", policy)
    with session_scope() as session:
        session.add(Facility(id=facility_id, name=facility["facility_name"]))
        session.add(MonthlyMenu(id="2026-10", month_start=DAY.replace(day=1)))
        session.add(MonthlyMenuEntry(id="captured-own-row", monthly_menu_id="2026-10", menu_date=DAY,
                                    daypart="lunch", name="own", diet_type="regular", slot_index=0,
                                    category="主菜", facility_override=facility_id))
        # Explicit synthetic amount, unrelated to the order's original zero.
        session.add(MenuMaster(id="captured-own-master", name="own", normalized_name="own",
                               qty_per_serving=1, unit_type="g", category="主菜"))
        session.add(MonthlyMenuItem(id="captured-own-item", monthly_menu_id="2026-10",
                                   menu_master_id="captured-own-master", name="own", diet_type="regular",
                                   facility_override=facility_id, daypart="lunch", category="主菜",
                                   qty_per_serving=1, unit_type="g", master_resolution_mode="manual"))
        session.flush()
        selection, _ = lineage.resolve(session, facility_id, DAY)
        _, projection = lineage.materialize(session, selection)
        projection_id = projection.id
        receipt = lineage.verify_projection(projection)
    captured = config_service.capture_authored_processing_config(
        facility_id, selected_authored_version=receipt["authored_version"])
    configured.clear()
    configured.update(captured)
    return projection_id, policy
