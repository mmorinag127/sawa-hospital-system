"""Synthetic input-only fixture; real canonical receipts, bagging and confirmation."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from test_modernization_explicit_ocr_quantity_contract import PublicSavedCandidate
from src.models.facility import Facility
from src.models.menu import MonthlyMenu, MonthlyMenuItem, MonthlyMenuEntry
from src.models.order import Order
from src.models.document import OrderDocument
from src.models.uploaded_pdf import UploadedPdf
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_workflow_state import OrderWorkflowState
from src.services import canonical_facility_lineage as lineage, order_service, order_workflow_v2_service as workflow


class TotalsInputs:
    def __init__(self, fixture):
        self.f = fixture
        self.inputs = {}

    def clear_all(self):
        # A fresh isolated database per test; never clear another test's data.
        assert not self.inputs

    def create_order_from_ingest(self, payload, *, lines):
        oid = "ORD" + uuid4().hex
        target = date.fromisoformat(lines[0]["date"]) if lines else payload.received_at.date()
        start = target - timedelta(days=(target.weekday() + 1) % 7)
        end = start + timedelta(days=6)
        week = f"{start:%Y-%m}@{start}~{end}"
        self.inputs[oid] = (payload.facility_hint, target, week)
        self._seed(oid, lines)
        return {"id": oid}

    def _seed(self, oid, lines):
        facility, target, week = self.inputs[oid]
        diets = list(dict.fromkeys((r["diet_type"], r["area_id"]) for r in lines)) or [("regular", "X")]
        fields = ["date", "daypart", "menu"] + [f"qty.{d}_{a.lower()}" for d, a in diets]
        rows = []
        for line in lines:
            key = [line["date"], line["daypart"], line["menu_name"]]
            row = next((r for r in rows if r[:3] == key), None)
            if row is None:
                row = key + [""] * len(diets)
                rows.append(row)
            row[3 + diets.index((line["diet_type"], line["area_id"]))] = str(line["quantity_original"])
        self._publish(oid, fields, rows)

    def _publish(self, oid, fields, rows):
        f = self.f
        facility, target, week = self.inputs[oid]
        config = deepcopy(f.authored)
        config["facility_id"] = facility
        config["facility_name"] = facility
        config["areas"] = [{"area_id": area, "name": area} for area in sorted({field[4:].rsplit("_", 1)[1].upper() for field in fields if field.startswith("qty.")})]
        config["bag_types"] = [{"bag_type_id": "UNIT-1000G", "label": "Unit", "max_qty": 1000, "unit": "g"}]
        config["packaging_policy"] = {"split_key": ["facility", "date", "daypart", "menu_name", "diet_type", "area_id", "bag_type"]}
        config["fax_template"]["columns"] = []
        for i, field in enumerate(fields):
            column = {"index": i, "source_index": i, "role": {"date": "date", "date_mmdd": "date", "daypart": "daypart", "menu": "menu_name", "remarks": "note"}.get(field, "quantity")}
            if field.startswith("qty."):
                diet, area = field[4:].rsplit("_", 1)
                column.update(diet_type=diet, area_id=area.upper())
            config["fax_template"]["columns"].append(column)
        entry = {"facility_id": facility, "versions": [{"version_id": oid, "valid_from": target.replace(day=1).isoformat(), "valid_to": None, "facility": config}]}
        f.master["facilities"] = [e for e in f.master["facilities"] if e["facility_id"] != facility] + [entry]
        f.write_master()
        sheet = {"order_id": oid, "fields": fields, "rows": rows, "row_ids": [f"row-{i}" for i in range(len(rows))]}
        with f.session_scope() as session:
            if session.get(Facility, facility) is None:
                session.add(Facility(id=facility, name=facility))
                session.flush()
            selected, _ = lineage.resolve(session, facility, target)
            _, version = lineage.materialize(session, selected)
            order = session.get(Order, oid)
            if order is None:
                order = Order(id=oid, facility_code=facility, week_code=week, status="要確認", document_uri=str(f.pdf), message_id=oid, received_at=datetime.combine(target, datetime.min.time()), template_version_id=version.id)
                session.add(order)
                session.flush()
                session.add(OrderDocument(id="DOC"+oid, facility_code=facility, week_code=week, storage_uri=str(f.pdf), source_email_id=oid, status="done"))
                session.flush()
                order.current_document_id = "DOC"+oid
                session.add(UploadedPdf(id="PDF"+oid, message_id=oid, storage_uri=str(f.pdf), original_filename=f.pdf.name, content_sha256=sha256(f.pdf.read_bytes()).hexdigest(), received_at=datetime.combine(target, datetime.min.time()), page_count=1, current_document_id="DOC"+oid, current_order_id=oid))
            order.template_version_id = version.id
            evidence = session.get(OrderOcrEvidenceRun, "EV"+oid)
            if evidence is None:
                evidence = OrderOcrEvidenceRun(id="EV"+oid, order_id=oid, schema_version="v2", source="unit-fixture", status="ready")
                session.add(evidence)
            evidence.template_version_id = version.id
            evidence.payload_json = {"tables": [{"rows": deepcopy(rows)}]}
            evidence.artifact_digest = sha256(json.dumps(evidence.payload_json).encode()).hexdigest()
            session.flush()
            draft = session.get(OrderSheetDraft, "DR"+oid)
            if draft is None:
                draft = OrderSheetDraft(id="DR"+oid, order_id=oid)
                session.add(draft)
            draft.template_version_id = version.id
            draft.base_evidence_run_id = evidence.id
            draft.base_template_resolution_id = config["fax_template"]["template_id"]
            draft.draft_sheet_json = sheet
            draft.draft_state = "saved"
            session.flush()
            state = session.get(OrderWorkflowState, oid)
            if state is None:
                state = OrderWorkflowState(order_id=oid, revision=1)
                session.add(state)
            state.state = "sheet_saved"
            state.template_version_id = version.id
            state.evidence_run_id = evidence.id
            state.draft_id = draft.id
            state.secondary_actions_json = {"workflow_v2": {"facility_id": facility, "week_start": week.split("@")[1].split("~")[0], "week_end": week.split("~")[1], "template_version_id": version.id}}
            # Explicit dated menu ownership for each input diet, using registered item metadata.
            month = target.strftime("%Y-%m")
            if session.get(MonthlyMenu, month) is None:
                session.add(MonthlyMenu(id=month, month_start=target.replace(day=1)))
                session.flush()
            for row in rows:
                items = session.execute(select(MonthlyMenuItem).where(MonthlyMenuItem.name == row[2], MonthlyMenuItem.facility_override.is_(None))).scalars().all()
                sources = [item for item in items if item.diet_type is None]
                assert len(sources) == 1, "fixture requires one explicitly registered recipe"
                item = sources[0]
                entry_diets = set()
                for field in fields:
                    if not field.startswith("qty."):
                        continue
                    diet = field[4:].rsplit("_", 1)[0]
                    recipe_diet = "regular" if diet in {"staff", "daycare"} else diet
                    registered = session.execute(select(MonthlyMenuItem).where(MonthlyMenuItem.monthly_menu_id == month, MonthlyMenuItem.name == row[2], MonthlyMenuItem.diet_type == recipe_diet, MonthlyMenuItem.facility_override.is_(None))).scalars().one_or_none()
                    if registered is None:
                        session.add(MonthlyMenuItem(id="MI"+uuid4().hex, monthly_menu_id=month, name=row[2], menu_master_id=item.menu_master_id, diet_type=recipe_diet, facility_override=None, category=item.category, daypart=row[1], unit_type=item.unit_type, qty_per_serving=item.qty_per_serving))
                    # One shared recipe per output context, not one per delivery column
                    # or facility. Original sheet diet fields and quantities stay intact.
                    entry_diet = recipe_diet
                    if entry_diet in entry_diets:
                        continue
                    entry_diets.add(entry_diet)
                    existing = session.execute(select(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == month, MonthlyMenuEntry.menu_date == target, MonthlyMenuEntry.name == row[2], MonthlyMenuEntry.diet_type == entry_diet, MonthlyMenuEntry.facility_override.is_(None))).scalars().one_or_none()
                    if existing is None:
                        slot = rows.index(row)
                        session.add(MonthlyMenuEntry(id="ME"+uuid4().hex, monthly_menu_id=month, menu_date=target, daypart=row[1], name=row[2], diet_type=entry_diet, category=item.category, facility_override=None, slot_index=slot))
            order_service._record_current_order_version(session, order, session.get(OrderDocument, "DOC"+oid))
        return sheet

    def set_status(self, oid, status):
        assert status == "確定"
        return self.confirm_order(oid)

    def save_ocr_sheet_exact(self, oid, *, fields, rows, **kwargs):
        return self._publish(oid, fields, rows), None

    def confirm_order(self, oid):
        with self.f.session_scope() as session:
            revision = session.get(OrderWorkflowState, oid).revision
        result, error = workflow.run_bagging(oid, expected_revision=revision)
        assert error is None, error
        assert result is not None
        with self.f.session_scope() as session:
            revision = session.get(OrderWorkflowState, oid).revision
        result, error = workflow.final_confirm(oid, expected_revision=revision, confirmed_by="unit-contract")
        assert error is None, error
        return result


@pytest.fixture
def totals_inputs(tmp_path, monkeypatch):
    fixture = PublicSavedCandidate(tmp_path, monkeypatch)
    monkeypatch.setenv("AUTH_DISABLED", "true")
    try:
        yield TotalsInputs(fixture)
    finally:
        fixture.close()
