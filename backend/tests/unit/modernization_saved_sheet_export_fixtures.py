"""Synthetic unit/API boundary data; not operator FAX, OCR, or staging proof.

Shapes come from Order/OrderSheetDraft/OrderOcrEvidenceRun/OrderWorkflowState,
test_canonical_facility_lineage.py and test_order_form_service.py. Projection
receipts are produced by the real JSON selector/materializer, never fabricated.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.api import outputs
from src.db import Base
from src.models.facility import Facility
from src.models.facility_template_version import FacilityTemplateVersion
from src.models.order import Order
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import canonical_facility_lineage as lineage
from src.services import ocr_evidence_service, order_form_service


WEEK = "3月1日～3月7日"
FAMILY = "fax_layout_regular_forbidden_v1"


def source_workbook(path, *, sheet=WEEK, marker="authored-target"):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    worksheet["A2"] = "【発　注　連　絡　表】"
    worksheet["G3"] = "締切日1月1日まで"
    worksheet["D7"] = "献立"
    for cell, label in (("E7", "常食"), ("F7", "肉禁"),
                        ("G7", "魚禁"), ("H7", "職員")):
        worksheet[cell] = label
    worksheet["J5"] = marker
    workbook.save(path)
    workbook.close()


class SavedSheetFixture:
    order_id = "ORD-SAVED-SELECTED"
    evidence_id = "OCR-SELECTED"
    draft_id = "DRAFT-SELECTED"

    def __init__(self, tmp_path, monkeypatch):
        self.root = tmp_path
        self.output = tmp_path / "output"
        self.output.mkdir()
        self.source = tmp_path / "authored-source.xlsx"
        source_workbook(self.source)
        self.master_path = tmp_path / "canonical-facilities.json"
        self.master = {"facilities": [{
            "facility_id": "F", "facility_name": "Reference only",
            "versions": [{
                "version_id": "authored-March", "valid_from": "2026-03-01",
                "valid_to": "2026-03-31", "facility": {
                    "facility_id": "F", "facility_name": "保存済み施設",
                    "areas": None, "fax_template_id": FAMILY,
                    "order_form_source_workbook": self.source.name,
                    "unknown": {"zero": 0, "null": None},
                    "fax_template": {
                        "template_id": FAMILY, "units": "normalized",
                        "table_box": [0, 0, 1, 1], "header_rows": 0,
                        "columns": [{"index": index, "source_index": index,
                                     "role": "quantity", "diet_type": diet,
                                     "area_id": "X"}
                                    for index, diet in enumerate(
                                        ("regular", "no_meat", "no_fish", "staff"), start=4)],
                    },
                },
            }],
        }]}
        self.write_master()
        monkeypatch.delenv("FACILITY_MASTER_URI", raising=False)
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.setenv("FACILITY_MASTER_PATH", str(self.master_path))
        self.engine = create_engine(
            f"sqlite:///{tmp_path / 'isolated.sqlite'}",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(self.engine, "connect")
        def enforce_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        # Existing conftest registers the real model set, including all FK targets.
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        monkeypatch.setattr(order_form_service, "session_scope", self.session_scope)
        monkeypatch.setattr(order_form_service, "_FAX_SOURCE_TEMPLATE_DIR", tmp_path)
        monkeypatch.setattr(order_form_service, "_OUTPUT_DIR", self.output)
        self.payload = {
            "base_evidence_run_id": self.evidence_id,
            "fields": ["date", "daypart", "category", "menu_name",
                       "qty.regular_x", "qty.no_meat_x", "qty.no_fish_x", "qty.staff_x"],
            "rows": [["3/1", "朝", "主", "選択献立", "12", "3", "4", "0"]],
            "unknown": {"zero": 0, "null": None, "precision": "0.0000"},
        }
        with self.session_scope() as session:
            session.add(Facility(id="F", name="DB mirror not canonical"))
            session.flush()
            selected, _ = lineage.resolve(session, "F", date(2026, 3, 1))
            _, projection = lineage.materialize(session, selected)
            self.projection_id = projection.id
            self.original_receipt = lineage.verify_projection(projection)
            for identifier in (self.order_id, "ORD-FOREIGN"):
                session.add(Order(
                    id=identifier, facility_code="F",
                    week_code="2026-03@2026-03-01~2026-03-07",
                    status="要確認", document_uri="unit-only:no-pdf",
                    message_id=identifier, received_at=datetime(2026, 3, 1, 9),
                    template_version_id=projection.id,
                ))
            session.flush()
            evidence_payload = {"tables": [{"rows": deepcopy(self.payload["rows"])}]}
            session.add(OrderOcrEvidenceRun(
                id=self.evidence_id, order_id=self.order_id,
                template_version_id=projection.id, schema_version="v2",
                producer_version="unit-fixture/v1", source="unit-fixture",
                status="ready", payload_json=evidence_payload,
                artifact_digest=hashlib.sha256(json.dumps(evidence_payload).encode()).hexdigest(),
            ))
            session.flush()
            for identifier, order_id, value in (
                (self.draft_id, self.order_id, "12"),
                ("DRAFT-LATEST-UNSELECTED", self.order_id, "999"),
                ("DRAFT-FOREIGN", "ORD-FOREIGN", "777"),
            ):
                payload = deepcopy(self.payload)
                payload["rows"][0][4] = value
                session.add(OrderSheetDraft(
                    id=identifier, order_id=order_id,
                    template_version_id=projection.id,
                    base_evidence_run_id=self.evidence_id,
                    base_template_resolution_id=FAMILY,
                    draft_sheet_json=payload, draft_state="draft",
                    created_at=datetime(2026, 3, 1 if identifier == self.draft_id else 2),
                ))
            session.flush()
            session.add(OrderWorkflowState(
                order_id=self.order_id, revision=1, state="sheet_saved",
                template_version_id=projection.id, evidence_run_id=self.evidence_id,
                draft_id=self.draft_id, confirmed_snapshot_id=None,
                secondary_actions_json={"workflow_v2": {
                    "facility_id": "F", "week_start": "2026-03-01",
                    "week_end": "2026-03-07", "template_version_id": projection.id,
                }},
            ))
        self.app = FastAPI()
        self.app.include_router(outputs.router, prefix="/outputs")
        # Synthetic authorization only. Actual controllers and builder remain intact.
        for route in outputs.router.routes:
            if route.path in {"/order-form-saved-sheet", "/file-preview"}:
                for dependency in route.dependencies:
                    self.app.dependency_overrides[dependency.dependency] = lambda: None

    @contextmanager
    def session_scope(self):
        with self.sessions() as session:
            with session.begin():
                yield session

    def write_master(self):
        self.master_path.write_text(json.dumps(self.master, ensure_ascii=False), encoding="utf-8")

    @property
    def authored(self):
        return self.master["facilities"][0]["versions"][0]["facility"]

    def republish(self):
        """Explicit fixture setup adoption, not a production guard override."""
        invoice = self.authored.get("invoice_template")
        if invoice is not None and "template_uri" not in invoice:
            from tests.helpers.authored_invoice import declare_invoice
            self.authored["invoice_template"] = declare_invoice(self.root, invoice)
        self.write_master()
        with self.session_scope() as session:
            selected, _ = lineage.resolve(session, "F", date(2026, 3, 1))
            _, projection = lineage.materialize(session, selected)
            session.get(Order, self.order_id).template_version_id = projection.id
            session.get(OrderOcrEvidenceRun, self.evidence_id).template_version_id = projection.id
            session.get(OrderSheetDraft, self.draft_id).template_version_id = projection.id
            workflow = session.get(OrderWorkflowState, self.order_id)
            workflow.template_version_id = projection.id
            metadata = deepcopy(workflow.secondary_actions_json)
            metadata["workflow_v2"]["template_version_id"] = projection.id
            workflow.secondary_actions_json = metadata
            self.projection_id = projection.id

    def request(self, boundary):
        path = "/outputs/order-form-saved-sheet" if boundary == "excel" else "/outputs/file-preview"
        params = {"order_id": self.order_id}
        if boundary == "preview":
            params["type"] = "order_form_saved_sheet"
        with TestClient(self.app) as client:
            return client.get(path, params=params)

    def verify_current(self):
        with self.session_scope() as session:
            projection = lineage.read_projection(session, self.projection_id)
            receipt = lineage.verify_projection(projection)
            assert receipt["facility_id"] == "F"
            assert projection.validation_json["creation_source_proof"]["source"] == "canonical_json"
            assert ocr_evidence_service.evidence_run_is_selectable(
                session.get(OrderOcrEvidenceRun, self.evidence_id))
            workflow = session.get(OrderWorkflowState, self.order_id)
            assert workflow.confirmed_snapshot_id is None
            assert workflow.state == "sheet_saved"
            assert session.get(OrderSheetDraft, self.draft_id).draft_sheet_json == self.payload

    def close(self):
        self.engine.dispose()
