"""Explicit monthly SQL inputs for output components, not accepted/OCR receipts."""

from copy import deepcopy
import json
from datetime import date
from uuid import uuid4

from src.db import session_scope
from src.models.facility import Facility
from src.models.menu import MenuMaster, MonthlyMenu, MonthlyMenuEntry, MonthlyMenuItem
from src.services import menu_service, output_builder
from tests.helpers.ingest_original import authored_sources


def authored_entries(rows):
    return [dict(zip(("menu_date", "daypart", "slot_index", "name", "category", "diet_type"),
                     row, strict=True)) for row in rows]


def publish_sources(entries, *, facility="FAC00009", items=(), accessories=(), physical_amount=None):
    """Persist only caller-authored monthly and accessory inputs."""
    items = deepcopy(list(items))
    if physical_amount is not None:
        quantity, unit = physical_amount
        for entry in entries:
            if any((item["name"], item["diet_type"], item["daypart"]) ==
                   (entry["name"], entry["diet_type"], entry["daypart"]) for item in items):
                continue
            items.append({"name": entry["name"], "diet_type": entry["diet_type"],
                          "daypart": entry["daypart"], "category": entry["category"],
                          "qty_per_serving": quantity, "unit_type": unit})
    with session_scope() as session:
        if session.get(Facility, facility) is None:
            session.add(Facility(id=facility, name="Synthetic monthly override facility"))
        months = {entry["menu_date"][:7] for entry in entries}
        for month in months:
            if session.get(MonthlyMenu, month) is None:
                session.add(MonthlyMenu(id=month, month_start=date.fromisoformat(month + "-01")))
        session.flush()
        for entry in entries:
            session.add(MonthlyMenuEntry(
                id=str(uuid4()), monthly_menu_id=entry["menu_date"][:7],
                menu_date=date.fromisoformat(entry["menu_date"]),
                daypart=entry["daypart"], slot_index=entry["slot_index"], name=entry["name"],
                category=entry["category"], diet_type=entry["diet_type"],
                facility_override=entry.get("facility_override", facility)))
        for item in items:
            master_id = str(uuid4())
            session.add(MenuMaster(id=master_id, name=item["name"],
                normalized_name=menu_service._normalize_menu_name(item["name"]),
                qty_per_serving=item["qty_per_serving"], unit_type=item["unit_type"],
                category=item["category"], temp_type=item.get("temp_type")))
            session.flush()
            assert len(months) == 1
            session.add(MonthlyMenuItem(id=str(uuid4()), monthly_menu_id=next(iter(months)),
                menu_master_id=master_id, name=item["name"], facility_override=facility,
                diet_type=item["diet_type"], daypart=item["daypart"], category=item["category"],
                qty_per_serving=item["qty_per_serving"], unit_type=item["unit_type"],
                temp_type=item.get("temp_type"), master_resolution_mode="manual"))
        for item in accessories:
            session.add(MenuMaster(id=str(uuid4()), name=item["name"],
                normalized_name=menu_service._normalize_menu_name(item["name"]),
                qty_per_serving=item["qty_per_serving"], unit_type=item["unit_type"],
                category="添え"))


def publish_configuration(monkeypatch, tmp_path, *, day, columns=(), daily_label_comparable_diet_types=None):
    authored_sources(monkeypatch, tmp_path, facility_id="FAC00009", menu_date=date.fromisoformat(day))
    with session_scope() as session:
        session.delete(session.get(MonthlyMenuEntry, "authored-physical-row"))
    path = tmp_path / "authored-facilities.json"
    master = json.loads(path.read_text())
    facility = master["facilities"][0]
    if daily_label_comparable_diet_types is not None:
        facility["daily_label_comparable_diet_types"] = deepcopy(daily_label_comparable_diet_types)
    for index, column in enumerate(columns, 4):
        facility["fax_template"]["columns"].append({
            **deepcopy(column), "index": index, "source_index": index})
    facility["areas"].append({"area_id": "3F", "name": "Synthetic third floor"})
    facility["versions"][0]["facility"] = deepcopy({key: value for key, value in facility.items() if key != "versions"})
    path.write_text(json.dumps(master))
    return output_builder.config_service.capture_authored_processing_config("FAC00009")


def produce(order, *, configured_input):
    """Run the real producer and its SQL source capture, never a read receipt."""
    candidate = {"lines": deepcopy(order["lines"]), "configured_input": deepcopy(configured_input)}
    result = output_builder.build_order_lines_for_outputs(order, producer_candidate=candidate)
    assert candidate["output_source_snapshot"]["facility"] == order["facility"]
    output_builder._validate_output_sources(candidate["output_source_snapshot"])
    return result


def captured_overrides(lines, entries, *, week, facility="FAC00009", source_indexes, physical_amount):
    publish_sources(entries, facility=facility, physical_amount=physical_amount)
    sources = output_builder._capture_output_sources(week, facility)
    assert len(lines) == len(source_indexes)
    bound = [{**deepcopy(line), "source_row_index": index}
             for line, index in zip(lines, source_indexes, strict=True)]
    result = output_builder._apply_menu_entry_overrides(bound, sources["entries"])
    output_builder._validate_output_sources(sources)
    return result
