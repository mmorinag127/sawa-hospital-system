"""Synthetic selected-date bagging components, never customer/operator OCR proof."""
import json
from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import fitz

from src.db import session_scope
from src.models.menu import MonthlyMenu, MonthlyMenuEntry, MonthlyMenuItem, MenuMaster
from src.models.uploaded_pdf import UploadedPdf
from src.services import order_workflow_v2_service as workflow, menu_service
from src.services.template_field_schema_service import canonical_field_name_from_template_column
from tests.helpers.ingest_original import (
    authored_sources, configure_delivery, reserve_uploaded_original,
    register_authored_original,
)


def authored_tabular_invoice(root, *, quantity_columns):
    """A physical workbook with explicitly declared component quantity columns."""
    from openpyxl import Workbook

    columns = [{"name": "日付", "source": "date"},
        {"name": "区分", "source": "daypart"},
        {"name": "献立", "source": "menu_display"}, *deepcopy(quantity_columns)]
    assert quantity_columns and all(column["source"] == "quantity" for column in quantity_columns)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Authored delivery"
    for index, column in enumerate(columns, 1):
        column["column_index"] = index
        sheet.cell(1, index, column["name"])
    path = root / "authored-delivery.xlsx"
    workbook.save(path)
    workbook.close()
    return {"template_uri": str(path), "sheet_name": sheet.title,
            "include_menu_name": True, "columns": columns}


def selected_bagging_order(monkeypatch, tmp_path, lines, *, invoice_template=None, menu_category="主菜", omit_invoice=False, menu_qty_per_serving=1, menu_unit_type="g", output_diet_types=None, accessory_masters=None, daily_label_comparable_diet_types=None, facility_id=None, facility_name=None, menu_temp_type=None, area_names=None, prepare_only=False, prepared_sources=False, message_id=None, week_start=None, declared_quantity_pairs=None, physical_menu_category=None, physical_slot_indexes=None, week_end=None, label_profile=None, prepared_original=None, prepared_menu_sources=False):
    """Prepare sources once, then consume them without changing canonical inputs."""
    assert prepared_original is None or (prepared_sources and not prepare_only), "prepared original requires predeclared sources"
    for key in ("RAW_BUCKET", "OCR_OUTPUT_BUCKET", "K_SERVICE", "FACILITY_MASTER_URI"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path / "native-artifacts"))
    assert lines, "explicit authored quantity lines required"
    identities = [(line["date"], line["daypart"], line["menu_name"],
                   line["diet_type"], line["area_id"]) for line in lines]
    assert len(identities) == len(set(identities)), "duplicate authored cell identity"
    assert all(line["date"] == lines[0]["date"] for line in lines), "one exact authored date required"
    supplied_pairs = {(line["diet_type"], line["area_id"]) for line in lines}
    declared = list(declared_quantity_pairs) if declared_quantity_pairs is not None else list(
        dict.fromkeys((line["diet_type"], line["area_id"]) for line in lines))
    assert all(isinstance(pair, (tuple, list)) and len(pair) == 2 for pair in declared), "explicit quantity pairs required"
    quantities = [tuple(pair) for pair in declared]
    assert len(quantities) == len(set(quantities)), "duplicate declared quantity pair"
    assert supplied_pairs <= set(quantities), "input quantity has no declared physical column"
    declared_fields = [canonical_field_name_from_template_column(
        {"role": "quantity", "diet_type": diet, "area_id": area}) for diet, area in quantities]
    assert len(declared_fields) == len(set(declared_fields)), "duplicate canonical quantity column"
    physical_category = menu_category if physical_menu_category is None else physical_menu_category
    day = date.fromisoformat(lines[0]["date"])
    start = date.fromisoformat(week_start) if week_start is not None else day
    end = date.fromisoformat(week_end) if week_end is not None else day
    assert start <= day <= end
    week_id = f"{start:%Y-%m}@{start.isoformat()}~{end.isoformat()}"
    facility = facility_id if facility_id is not None else f"bagging-component-{uuid4().hex}"
    configure_delivery(monkeypatch)
    original_name = f"original-{uuid4().hex}.pdf" if prepare_only or prepared_sources else "original.pdf"
    if prepared_sources:
        assert facility_id is not None
        assert not prepare_only
        path = str(tmp_path / original_name)
    else:
        path, _ = authored_sources(monkeypatch, tmp_path, facility_id=facility,
            menu_date=start, seed_menu=False, original_name=original_name)
    master_path = tmp_path / "authored-facilities.json"
    master = json.loads(master_path.read_text())
    configured = next(item for item in master["facilities"] if item["facility_id"] == facility)
    if not prepared_sources:
        if facility_name is not None:
            configured["facility_name"] = facility_name
        if daily_label_comparable_diet_types is not None:
            configured["daily_label_comparable_diet_types"] = deepcopy(daily_label_comparable_diet_types)
        authored_areas = {
            "common": {"area_id": "common", "name": "Synthetic component common area"},
            "2F": {"area_id": "2F", "name": "Synthetic component second floor"},
            "3F": {"area_id": "3F", "name": "Synthetic component third floor"},
        }
        for area, name in (area_names or {}).items():
            assert isinstance(area, str) and area and isinstance(name, str) and name
            authored_areas[area] = {"area_id": area, "name": name}
        columns = configured["fax_template"]["columns"][:3]
        configured["areas"] = []
        for area in dict.fromkeys(area for _, area in quantities):
            if area in {None, "", "X"}:
                continue
            assert area in authored_areas, ("explicit synthetic area definition required", area)
            configured["areas"].append(deepcopy(authored_areas[area]))
        for index, (diet, area) in enumerate(quantities, 3):
            columns.append({"index": index, "source_index": index, "role": "quantity",
                            "name": f"qty.{diet}_{area}",
                            "diet_type": diet, "area_id": area})
            if output_diet_types and diet in output_diet_types:
                columns[-1]["output_diet_type"] = output_diet_types[diet]
        configured["fax_template"]["columns"] = columns
        if invoice_template is None:
            # Explicit authored tabular component contract; not a production fallback.
            invoice_template = {"columns": [
                {"name": "日付", "source": "date"},
                {"name": "区分", "source": "daypart"},
                {"name": "献立", "source": "menu_display"},
                *[{"name": f"qty.{diet}_{area}", "source": "quantity", "diet_type": diet,
                   "area_id": area} for diet, area in quantities]], "include_menu_name": True}
        if invoice_template is not None:
            if not omit_invoice and "template_uri" not in invoice_template:
                from tests.helpers.authored_invoice import declare_invoice
                invoice_template = declare_invoice(tmp_path, invoice_template)
            configured["invoice_template"] = deepcopy(invoice_template)
        if label_profile is not None:
            configured["label_profile"] = deepcopy(label_profile)
        if omit_invoice:
            configured.pop("invoice_template", None)
        configured["versions"][0]["facility"] = deepcopy({key: value for key, value in configured.items() if key != "versions"})
        master_path.write_text(json.dumps(master))
    columns = deepcopy(configured["fax_template"]["columns"])
    for column in columns:
        column["name"] = canonical_field_name_from_template_column(column)
        assert column["name"]
        assert type(column["source_index"]) is int and column["source_index"] >= 0
    assert len({column["name"] for column in columns}) == len(columns), "duplicate canonical physical column"
    assert len({column["source_index"] for column in columns}) == len(columns), "duplicate physical source index"
    quantity_columns = [column for column in columns if column["role"] == "quantity"]
    physical_pairs = [(column["diet_type"], column.get("area_id")) for column in quantity_columns]
    assert len(physical_pairs) == len(set(physical_pairs)), "duplicate physical quantity identity"
    assert supplied_pairs <= set(physical_pairs), "input quantity has no prepared physical column"
    if declared_quantity_pairs is not None:
        assert set(physical_pairs) == set(quantities), "prepared physical quantity columns differ from declaration"
    quantities = physical_pairs
    assert all(sum(column["role"] == role for column in columns) == 1
               for role in ("date", "daypart", "menu_name")), "explicit physical identity columns required"
    if prepared_original is not None:
        assert prepared_original["facility_id"] == facility
        assert prepared_original["week_id"] == week_id
        assert prepared_original["lines"] == lines
        assert prepared_original["quantity_pairs"] == quantities
        assert prepared_original["facility_config"] == {
            key: value for key, value in configured.items() if key != "versions"}
        original = Path(prepared_original["path"])
        assert original.resolve().is_relative_to(tmp_path.resolve()), "fixture-owned original required"
        body = original.read_bytes()
        assert sha256(body).hexdigest() == prepared_original["content_sha256"]
        path = str(original)
    else:
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((40, 40), f"Synthetic component / {facility} / {week_id} / {message_id or ''}")
            y = 75
            for line in lines:
                if y > 780:
                    page = pdf.new_page()
                    y = 40
                page.insert_text((30, y),
                    f"{line['date']} {line['daypart']} {line['menu_name']} "
                    f"{line['diet_type']} / {line['area_id']} / {line['quantity_original']}",
                    fontname="japan", fontsize=9)
                y += 25
            body = pdf.tobytes()
        Path(path).write_bytes(body)
    groups = {}
    for line in lines:
        assert date.fromisoformat(line["date"]) == day
        groups.setdefault((line["daypart"], line["menu_name"]), []).append(line)
    if physical_slot_indexes is not None:
        assert all(name in physical_slot_indexes and type(physical_slot_indexes[name]) is int
                   for _meal, name in groups)
        groups = dict(sorted(groups.items(), key=lambda item: (
            {"朝": 0, "昼": 1, "夕": 2}[item[0][0]], physical_slot_indexes[item[0][1]],
        )))
    with session_scope() as session:
        if not prepared_sources and not prepared_menu_sources:
            if session.get(MonthlyMenu, day.strftime("%Y-%m")) is None:
                session.add(MonthlyMenu(id=day.strftime("%Y-%m"), month_start=day.replace(day=1)))
                session.flush()
            if facility_name is not None:
                from src.models.facility import Facility
                session.get(Facility, facility).name = facility_name
            for name, values in (accessory_masters or {}).items():
                session.add(MenuMaster(id=str(uuid4()), name=name, normalized_name=menu_service._normalize_menu_name(name),
                    qty_per_serving=values["qty_per_serving"], unit_type=values["unit_type"], category="添え"))
            slots_by_daypart = {}
            for (daypart, menu_name), group in groups.items():
                slot = physical_slot_indexes[menu_name] if physical_slot_indexes is not None else slots_by_daypart.get(daypart, 0)
                slots_by_daypart[daypart] = slot + 1
                canonical_daypart = {"朝": "breakfast", "昼": "lunch", "夕": "supper"}[daypart]
                existing_master = session.query(MenuMaster).filter_by(normalized_name=menu_service._normalize_menu_name(menu_name)).one_or_none()
                master_id = existing_master.id if existing_master is not None else str(uuid4())
                if existing_master is not None:
                    assert (existing_master.name, existing_master.qty_per_serving, existing_master.unit_type,
                            existing_master.category, existing_master.temp_type, existing_master.condiments or []) == (
                        menu_name, menu_qty_per_serving, menu_unit_type, menu_category, menu_temp_type,
                        ["ソース"] if group[0]["bag_type"] == "condiment" else []), "existing authored menu master differs from explicit inputs"
                if existing_master is None:
                    session.add(MenuMaster(id=master_id, name=menu_name, normalized_name=menu_service._normalize_menu_name(menu_name),
                        qty_per_serving=menu_qty_per_serving, unit_type=menu_unit_type, category=menu_category,
                        temp_type=menu_temp_type,
                        condiments=["ソース"] if group[0]["bag_type"] == "condiment" else []))
                    session.flush()
                # Physical regular identity is authored separately from requested
                # logical quantities. It never adds a regular order column/value.
                for diet in dict.fromkeys(["regular", *[diet for diet, _area in quantities]]):
                    session.add(MonthlyMenuEntry(id=str(uuid4()), monthly_menu_id=day.strftime("%Y-%m"),
                        menu_date=day, name=menu_name, daypart=canonical_daypart, diet_type=diet,
                        category=physical_category, facility_override=facility, slot_index=slot))
                    session.add(MonthlyMenuItem(id=str(uuid4()), monthly_menu_id=day.strftime("%Y-%m"),
                        menu_master_id=master_id, name=menu_name, diet_type=diet, facility_override=facility,
                        qty_per_serving=menu_qty_per_serving, unit_type=menu_unit_type, category=physical_category, daypart=canonical_daypart,
                        master_resolution_mode="manual"))
        if prepare_only:
            return {
                "path": str(path), "content_sha256": sha256(body).hexdigest(),
                "facility_id": facility, "week_id": week_id, "lines": deepcopy(lines),
                "quantity_pairs": deepcopy(quantities),
                "facility_config": deepcopy({key: value for key, value in configured.items()
                                             if key != "versions"}),
            }
        upload_id, message = str(uuid4()), message_id if message_id is not None else str(uuid4())
        session.add(UploadedPdf(id=upload_id, message_id=message, storage_uri=path,
            content_sha256=sha256(body).hexdigest(), original_filename="synthetic.pdf",
            received_at=datetime.combine(day, datetime.min.time()), facility_hint=facility,
            week_hint=week_id, status="pending"))
    reserve_uploaded_original(upload_id, effective_date=day)
    registered = register_authored_original(upload_id, effective_date=day)
    current, error = workflow.get_workflow_readonly(registered["id"])
    assert error is None, error
    context, error = workflow.confirm_context(order_id=registered["id"], facility_id=facility,
        week_start=start.isoformat(), week_end=end.isoformat(), expected_revision=current["revision"])
    assert error is None, error
    rows, sheet_rows = [], []
    for (daypart, menu_name), group in groups.items():
        amounts = {(line["diet_type"], line["area_id"]): line["quantity_original"] for line in group}
        raw = [""] * (max(column["source_index"] for column in columns) + 1)
        for column in columns:
            role = column["role"]
            if role == "quantity":
                value = amounts.get((column["diet_type"], column.get("area_id")))
            elif role in {"date", "daypart", "menu_name"}:
                value = {"date": day.strftime("%m/%d"), "daypart": daypart, "menu_name": menu_name}[role]
            else:
                assert role == "note", "unsupported authored physical column role"
                value = ""
            raw[column["source_index"]] = "" if value is None else str(value)
        rows.append(raw)
        sheet_rows.append([day.isoformat(), daypart, menu_name,
            *[int(str(amounts[pair])) if pair in amounts and amounts[pair] is not None else None
              for pair in physical_pairs]])
    from tests.helpers.native_selection import select_authored_native_result
    evidence = select_authored_native_result(monkeypatch, order_id=registered["id"],
        table_rows=rows, columns=columns, unlisted_cells="",
        artifact_root=tmp_path / "native-artifacts")
    assert isinstance(evidence, dict) and evidence["id"]
    assert evidence["terminal_receipt"]["status"] == "done"
    assert evidence["terminal_receipt"]["evidence_run_id"] == evidence["id"]
    quantity_fields = [column["name"] for column in quantity_columns]
    assert all(quantity_fields)
    assert len(set(quantity_fields)) == len(quantities)
    sheet = {"fields": ["date", "daypart", "menu", *quantity_fields],
        "rows": sheet_rows, "week_id": week_id}
    return registered, sheet


def accepted_bagging_order(monkeypatch, tmp_path, lines, *, invoice_template=None, menu_category="主菜", confirm=False, omit_invoice=False, menu_qty_per_serving=1, menu_unit_type="g", menu_temp_type=None, output_diet_types=None, accessory_masters=None, daily_label_comparable_diet_types=None, facility_id=None, prepared_sources=False, message_id=None, week_start=None, declared_quantity_pairs=None, physical_menu_category=None, physical_slot_indexes=None, week_end=None, label_profile=None, area_names=None, facility_name=None, prepared_original=None, prepared_menu_sources=False):
    registered, sheet = selected_bagging_order(
        monkeypatch, tmp_path, lines, invoice_template=invoice_template,
        menu_category=menu_category, omit_invoice=omit_invoice,
        menu_qty_per_serving=menu_qty_per_serving, menu_unit_type=menu_unit_type,
        menu_temp_type=menu_temp_type,
        output_diet_types=output_diet_types, accessory_masters=accessory_masters,
        daily_label_comparable_diet_types=daily_label_comparable_diet_types,
        facility_id=facility_id, prepared_sources=prepared_sources, message_id=message_id, week_start=week_start,
        prepared_original=prepared_original,
        prepared_menu_sources=prepared_menu_sources,
        area_names=area_names, facility_name=facility_name,
        declared_quantity_pairs=declared_quantity_pairs, physical_menu_category=physical_menu_category,
        physical_slot_indexes=physical_slot_indexes, week_end=week_end, label_profile=label_profile,
    )
    current, error = workflow.get_workflow_readonly(registered["id"])
    assert error is None, error
    saved, error = workflow.save_sheet(order_id=registered["id"], sheet=sheet,
                                      expected_revision=current["revision"])
    assert error is None, error
    from src.models.order import Order
    from src.models.order_sheet_draft import OrderSheetDraft
    with session_scope() as session:
        order = session.get(Order, registered["id"])
        draft = session.get(OrderSheetDraft, saved["saved_sheet"]["saved_sheet_id"])
        candidate = workflow._build_materialization_candidate_for_saved_sheet(session=session, order=order, saved_sheet=draft)
        resolution = workflow.candidate_menu_amount_resolution(order, candidate)
        assert resolution["status"] == "resolved", (resolution, candidate["lines"])
    bagging, error = workflow.run_bagging(registered["id"], expected_revision=saved["workflow"]["revision"])
    assert error is None, error
    from src.models.order_output_artifact import OrderBaggingResult
    from src.services.order_output_artifact_service import payload_digest
    with session_scope() as session:
        receipt = session.get(OrderBaggingResult, bagging["bagging_result"]["bagging_result_id"])
        assert receipt.payload_json == bagging["bagging_result"]
        assert receipt.payload_digest == payload_digest(receipt.payload_json)
        assert receipt.payload_json["materialization_digest"] == workflow._canonical_payload_digest(bagging["bagging_result"]["materialization_candidate"])
        assert workflow._canonical_payload_digest(receipt.payload_json["materialization_candidate"]) == receipt.payload_json["materialization_digest"]
    if confirm:
        confirmed, error = workflow.final_confirm(registered["id"], expected_revision=bagging["workflow"]["revision"])
        assert error is None, error
        assert confirmed is not None
    return registered
