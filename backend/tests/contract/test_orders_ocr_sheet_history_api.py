"""Current explicit operator/read contracts, not live FAX or recognition proof.

SQL setup declares stored state, not ingestion. Selected-sheet cases use real
context/capture/claim/persistence/HTTP selection/save; only external recognition
and its rendered files are doubled. Saves do not manufacture SQL revision history.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event

from src.db import session_scope
from src.main import app
from src.models.document import OrderDocument
from src.models.facility import Facility, FacilityConfig
from src.models.menu import MonthlyMenu, MonthlyMenuEntry
from src.models.ocr_job import OcrJob
from src.models.order import Order, OrderLine
from src.models.order_ocr_cache import OrderOcrCache
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_ocr_revision import OrderOcrRevision
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.uploaded_pdf import UploadedPdf
from src.models.user import AuditLog
from src.services import config_service, order_service
from src.services import order_workflow_v2_service as workflow
from src.services import workflow_attempt_guard_service as guard
from src.services import workflow_ocr_delivery_service as delivery
from src.services.storage_service import load_selected_pdf_bytes
from tests.contract.auth_support import hospital_operator  # noqa: F401
from tests.unit.test_disabled_facility_columns import _snapshot
from tests.unit.test_h2_operator_repair_revision import _provider_output_files
from tests.helpers.authored_menu_recipe import declare_period_recipes
from tests.helpers.readonly_catalog import readonly_catalog_statements


HEADER = ["日付", "区分", "メニュー", "常食2F", "備考"]
FIELDS = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
WEEK = "2026-01@2026-01-04~2026-01-10"
TEMPLATE = "history-contract-layout"
LEGACY = {"error": "legacy_order_workflow_disabled",
          "message": "注文処理は workflow-v2 に移行しました。この旧 order workflow endpoint は使用できません。",
          "replacement": "workflow-v2"}


def _line(day="2026-01-08", menu="Menu A", quantity=2, *, daypart="昼", area="2F"):
    return {"date": date.fromisoformat(day), "daypart": daypart, "menu_name": menu,
            "diet_type": "regular", "area_id": area, "bag_type": "standard", "quantity_original": quantity}


def _sheet(quantity, remark, row_id):
    return {"header": HEADER, "fields": FIELDS,
            "rows": [["01/08", "昼", "Menu A", quantity, remark]], "row_ids": [row_id]}


class HistoryCase:
    def __init__(self, engine, root, monkeypatch, actor, *, week=WEEK, facility="FAC00001",
                 period=WEEK, received=datetime(2026, 1, 8, 9), lines=None, menu=True,
                 columns=None, menu_entries=None):
        self.engine, self.root, self.monkeypatch = engine, root, monkeypatch
        self.actor, self.client, self.id = actor, TestClient(app), "history-contract-order"
        self.facility, self.period = facility, period
        start, end = period.split("@", 1)[1].split("~")
        if columns is None:
            columns = [{"index": i, "source_index": i, "name": name, "header": header, "role": role}
                for i, (name, header, role) in enumerate(zip(FIELDS, HEADER, ["date", "daypart", "menu_name", "quantity", "note"]))]
            columns[3].update(diet_type="regular", area_id="2F" if facility == "FAC00001" else "X")
        else:
            columns = deepcopy(columns)
        config = {"facility_id": facility, "facility_name": f"Synthetic history {facility}", "areas": None,
                  "fax_template_id": TEMPLATE, "fax_template": {"template_id": TEMPLATE, "page": 1, "columns": columns}}
        # Explicit synthetic envelope; not the live facility's dated configuration.
        self.source = root / "authored-facility.json"
        self.source.write_text(json.dumps({"facilities": [{**config, "versions": [{
            "version_id": "history-contract-period", "valid_from": start, "valid_to": end, "facility": config,
        }]}]}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv("FACILITY_MASTER_PATH", str(self.source))
        for key in ("FACILITY_MASTER_URI", "K_SERVICE", "OCR_OUTPUT_BUCKET", "RAW_BUCKET"):
            monkeypatch.delenv(key, raising=False)
        policy = root / "policy.json"
        policy.write_bytes(config_service.INGEST_POLICY_PATH.read_bytes())
        monkeypatch.setattr(config_service, "INGEST_POLICY_PATH", policy)
        monkeypatch.setenv("OCR_ARTIFACT_DIR", str(root / "artifacts"))
        with session_scope() as session:
            if engine.dialect.name == "sqlite":
                assert session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            else:
                assert engine.dialect.name == "postgresql"
                assert session.connection().exec_driver_sql("SHOW session_replication_role").scalar_one() == "origin"
            session.add(Facility(id=facility, name=config["facility_name"]))
            session.add(Order(id=self.id, facility_code=facility, week_code=week,
                              document_uri="stored:history-component", message_id=self.id, received_at=received))
            session.flush()
            for index, line in enumerate(lines if lines is not None else [_line()]):
                session.add(OrderLine(id=f"history-line-{index}", line_id=f"line-{index + 1}", order_id=self.id, **line))
            if menu_entries is not None:
                months = {entry["menu_date"].replace(day=1) for entry in menu_entries}
                for month in sorted(months):
                    month_id = month.strftime("%Y-%m")
                    session.add(MonthlyMenu(id=month_id, month_start=month, filename=f"seed-{month_id}.xlsx"))
                session.flush()
                for entry in menu_entries:
                    session.add(MonthlyMenuEntry(**entry))
            elif menu:
                session.add(MonthlyMenu(id="2026-01", month_start=date(2026, 1, 1), filename="seed-2026-01.xlsx"))
                session.flush()
                session.add(MonthlyMenuEntry(id="seed-entry-2026-01-08-lunch-menu-a", monthly_menu_id="2026-01",
                    menu_date=date(2026, 1, 8), daypart="昼", name="Menu A", slot_index=0, diet_type="regular", category="副菜"))
            session.flush()
            # This component declares a one-gram recipe, not a headcount inferred
            # from its stored lines or recognizer response.
            declare_period_recipes(session, facility_id=facility, period=period,
                qty_per_serving=1, unit_type="g")
        data = Path(__file__).resolve().parents[2] / "src/data"
        self.protected = (self.source, policy, data / "facility_master.template.json",
                          data / "order_form_master_templates/master_layout_template.xlsx")
        self.observations = []

    @contextmanager
    def readonly(self):
        before = _snapshot(self.engine)
        schema_reads = readonly_catalog_statements(self.engine)
        paths = self.protected + ((Path(self.engine.url.database),) if self.engine.dialect.name == "sqlite" else ())
        hashes = {str(p): sha256(p.read_bytes()).hexdigest() for p in paths}
        statements = []
        def observe(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", observe)
        try:
            yield
        finally:
            event.remove(self.engine, "before_cursor_execute", observe)
            after = _snapshot(self.engine)
            changed = {key: {"before": repr(before[key]), "after": repr(after[key])}
                       for key in before if before[key] != after[key]}
            non_select = [sql for sql in statements if not sql.lstrip().upper().startswith("SELECT")]
            after_hashes = {str(p): sha256(p.read_bytes()).hexdigest() for p in paths}
            self.observations.append({"changed_tables": changed, "non_select": non_select,
                "before_hashes": hashes, "after_hashes": after_hashes})
            assert not changed, changed
            assert set(non_select) <= schema_reads, non_select
            assert after_hashes == hashes

    def get(self, suffix="", *, status=200):
        with self.readonly():
            response = self.client.get(f"/orders/{self.id}" + ("/" + suffix if suffix else ""), headers=self.actor.headers)
        self.observations.append({"path": suffix, "status": response.status_code, "body": response.json()})
        assert response.status_code == status, response.text
        return response.json()

    def revision(self):
        return self.get("workflow-v2")["revision"]

    def command(self, method, suffix, body=None):
        response = self.client.request(method, f"/orders/{self.id}/workflow-v2/{suffix}",
            headers={**self.actor.headers, "If-Match": f'"{self.revision()}"'}, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    def confirm(self):
        start, end = self.period.split("@", 1)[1].split("~")
        result = self.command("POST", "context", {"facility_id": self.facility,
            "week_start": start, "week_end": end, "template_id": TEMPLATE})
        assert result["state"] == "context_confirmed"
        return result

    def queue(self):
        import fitz
        if not hasattr(self, "pdf"):
            self.confirm()
            self.pdf = self.root / "owned-component.pdf"
            with fitz.open() as document:
                document.new_page().insert_text((40, 40), f"{self.facility} / {self.period} / component input only")
                body = document.tobytes()
            self.pdf.write_bytes(body)
            self.protected += (self.pdf,)
            with session_scope() as session:
                order = session.get(Order, self.id)
                document = OrderDocument(id="history-document", storage_uri=str(self.pdf), source_email_id=order.message_id)
                session.add(document)
                session.flush()
                order.current_document_id, order.document_uri = document.id, str(self.pdf)
                session.add(UploadedPdf(id="history-pdf", message_id=order.message_id, storage_uri=str(self.pdf),
                    original_filename=self.pdf.name, content_sha256=sha256(body).hexdigest(), received_at=order.received_at))
                session.flush()
                order_service._record_current_order_version(session, order, document)
        else:
            with session_scope() as session:
                order = session.get(Order, self.id)
                document = session.get(OrderDocument, order.current_document_id)
                uploaded = session.get(UploadedPdf, "history-pdf")
                state = session.get(OrderWorkflowState, self.id)
                context = state.secondary_actions_json["workflow_v2"]
                start, end = self.period.split("@", 1)[1].split("~")
                assert (context["week_start"], context["week_end"]) == (start, end)
                assert order.facility_code == context["facility_id"] == self.facility
                assert order.week_code == context["week_code"] == self.period
                assert context["template_id"] == TEMPLATE
                assert state.template_version_id == order.template_version_id == context["template_version_id"]
                assert document.id == "history-document"
                assert document.storage_uri == order.document_uri == str(self.pdf)
                assert uploaded.storage_uri == str(self.pdf)
                assert uploaded.content_sha256 == sha256(self.pdf.read_bytes()).hexdigest()
        for key, value in {"PROJECT_ID": "history-component", "LOCATION": "asia-northeast2", "QUEUE": "history-component",
            "TARGET_URL": "https://reserved.invalid", "SERVICE_ACCOUNT_EMAIL": "worker@history-component.iam.gserviceaccount.com",
            "AUDIENCE": "https://reserved.invalid", "DISPATCH_DEADLINE_SECONDS": "300"}.items():
            self.monkeypatch.setenv("HOSPITAL_WORKFLOW_OCR_DELIVERY_" + key, value)
        self.job = "OCR-" + self.id
        self.queued, error = workflow.mark_ocr_run_queued(self.id, self.job, input_reference=str(self.pdf),
            request_mode="ocr_rerun", expected_revision=self.revision())
        assert error is None, error
        self.claim = delivery.claim_delivery(self.queued["delivery"]["id"], self.queued["delivery"]["generation"],
                                             lease_expires_at=datetime.utcnow() + timedelta(minutes=5))
        assert not self.claim.get("duplicate"), self.claim

    @contextmanager
    def execution(self):
        revision = self.queued["workflow"]["revision"]
        attempt = self.queued["ocr_job"]["workflow_attempt_id"]
        with (delivery.claim_context(self.claim), order_service.workflow_ocr_write_context(attempt, revision, self.job, self.id),
              guard.workflow_execution_context(self.job, self.id, attempt, revision)):
            yield

    def prepare_provider(self, *, cell_values="2"):
        from src.services import hakodate_cell_ocr_batch_service as batch
        self.queue()
        with session_scope() as session:
            receipt, start = workflow._axis_source_receipt(session, session.get(Order, self.id), session.get(OrderWorkflowState, self.id))
        def manifest(**_kwargs):
            pdf = receipt["selected_pdf_receipt"]
            return {"configured_input": deepcopy(start["configured_input"]), "selected_pdf_receipt": deepcopy(pdf),
                    "_verified_pdf_bytes": load_selected_pdf_bytes(pdf["storage_uri"], pdf)}
        calls = []
        def recognize(*, item, output_dir, **_kwargs):
            assert item["_verified_pdf_bytes"] == self.pdf.read_bytes()
            regions = [{**deepcopy(r), "bbox": [i * 20, 0, i * 20 + 10, 10]}
                       for i, r in enumerate(item["original_target_semantics"])]
            calls.append(regions)
            if callable(cell_values):
                values = {r["sheet_cell"]: cell_values(deepcopy(r)) for r in regions}
            else:
                values = {r["sheet_cell"]: cell_values[r["sheet_cell"]] if isinstance(cell_values, dict) else cell_values
                          for r in regions}
            if isinstance(cell_values, dict):
                assert set(values) == set(cell_values)
            records = [{**r, "ocr_text": values[r["sheet_cell"]], "recognizer_score": 1.0,
                        "recognizer_accepted_candidate": {"normalized_digits": values[r["sheet_cell"]]}} for r in regions]
            outputs = _provider_output_files(output_dir, records=records, regions=regions, sheet_values=values)
            return SimpleNamespace(metrics={"expected_sheet_cells": item["original_expected_sheet_cells"]},
                expected_sheet_cells=tuple(item["original_expected_sheet_cells"]), outputs=outputs,
                ocr_engine="component-external-recognizer"), None
        self.monkeypatch.setattr(order_service, "_build_live_hakodate_manifest_item", manifest)
        self.monkeypatch.setattr(batch, "build_hakodate_best_method_for_manifest_item", recognize)
        return calls

    def produce(self, *, cell_values="2"):
        calls = self.prepare_provider(cell_values=cell_values)
        with self.execution():
            evidence, error = order_service.rerun_ocr_evidence_only(self.id, job_id=self.job, project_sheet=False, refresh_workflow=False)
        assert error is None, (error, evidence)
        assert len(calls) == 1
        self.evidence = evidence["id"]
        self.command("POST", f"ocr-results/{self.evidence}/select")
        source = self.get("workflow-v2/sheet-source")
        assert source["selected_ocr_result_id"] == self.evidence
        return source

    def save(self, sheet):
        self.command("POST", "sheet/anomaly-review", {"sheet": sheet, "use_llm": False})
        self.command("POST", "sheet/review-confirm", {"sheet": sheet})
        saved = self.command("PUT", "sheet", {"sheet": sheet, "edited_by": "operator"})
        assert saved["saved_sheet"]["sheet"] == sheet
        return saved


@pytest.fixture
def history_case(isolated_formal_schema, tmp_path, monkeypatch, hospital_operator):
    cases = []
    def create(**kwargs):
        assert not cases, "one independently owned order/source per scenario"
        fixture = HistoryCase(isolated_formal_schema, tmp_path, monkeypatch, hospital_operator, **kwargs)
        cases.append(fixture)
        return fixture
    yield create
    for case in cases:
        (tmp_path / "history-contract-observations.json").write_text(json.dumps(case.observations, indent=2), encoding="utf-8")


@pytest.mark.parametrize("value,remark,row_id", [
    ("3", "api", "row-api-1"), ("8", "exact-save", "row-api-save-1"), ("11", "keep-me", "row-api-save-keep-1"),
])
def test_operator_saved_values_and_identity_survive_history_reads(history_case, value, remark, row_id):
    f = history_case()
    original = f.produce()["sheet"]
    assert isinstance(original["fields"], list) and isinstance(original["rows"], list)
    assert original["rows"][0][original["fields"].index("qty.regular_2f")] == "2"
    sheet = _sheet(value, remark, row_id)
    saved = f.save(sheet)["saved_sheet"]
    current = f.get("workflow-v2")
    assert current["saved_sheet_id"] == saved["saved_sheet_id"]
    assert current["selected_ocr_result_id"] == f.evidence
    assert f.get("workflow-v2/sheet")["sheet"] == sheet
    history = f.get("ocr-history")
    assert history["order_id"] == f.id
    assert history["latest"] is None and history["revisions"] == []
    assert history["diagnostics"]["latest_evidence"]["record"]["id"] == f.evidence
    assert isinstance(f.get("history")["items"], list)
    assert f.get("workflow-v2/sheet")["sheet"] == sheet
    projected = f.get("workflow-v2/sheet-source")
    assert projected["selected_ocr_result_id"] == f.evidence
    assert projected["sheet"]["rows"][0][projected["sheet"]["fields"].index("qty.regular_2f")] == "2"
    detail = f.get()
    assert detail["id"] == f.id and detail["week_value"] == WEEK
    # Review-save is not final OrderLine materialization or SQL revision retention.
    with session_scope() as session:
        assert session.query(OrderLine).filter_by(order_id=f.id).count() == 0
        draft = session.get(OrderSheetDraft, saved["saved_sheet_id"])
        assert draft.draft_sheet_json["rows"] == [["01/08", "昼", "Menu A", value, remark]]
        assert draft.draft_sheet_json["row_ids"] == [row_id]
        assert session.query(OrderOcrRevision).filter_by(order_id=f.id).count() == 0


def test_ocr_history_keeps_evidence_as_diagnostics_not_revision(history_case):
    f = history_case()
    payload = {"status": "done", "pages": [{"page_index": 1, "tables": [{"table_id": "p1_t1",
        "rows": [["日付", "区分", "メニュー", "常食2F"], ["01/08", "昼", "Menu A", "3"]]}],
        "ocr_overlay_uri": "gs://dummy/overlay.png"}],
        "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|01/08|昼|Menu A|3|",
        "template_resolution": {"resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1"},
        "table_box": [0.1, 0.1, 0.9, 0.9], "grid_column_edges": [0.1, 0.5, 0.9], "grid_row_edges": [0.1, 0.5, 0.9]}
    with session_scope() as session:
        session.add(OrderOcrEvidenceRun(id="OEV-stored-diagnostic", order_id=f.id, schema_version="v1_legacy",
            status="diagnostic_only", payload_json=payload, artifact_digest=sha256(json.dumps(payload).encode()).hexdigest()))
    history = f.get("ocr-history")
    assert history["latest"] is None and history["revisions"] == []
    assert history["raw_output"] == payload
    assert history["raw_output_source"] == "order_ocr_evidence_runs.payload_json"
    assert history["diagnostics"]["latest_evidence"]["record"]["payload_json"] == payload


@pytest.mark.parametrize("error,stage,old,cache,retained", [
    ("main_ocr_failed:gemini", "inference", False, False, None),
    ("lines_empty", "line_parse", False, True, True),
    ("main_ocr_failed:gemini:Gemini OCR HTTP 400 INVALID_ARGUMENT: Budget 0 is invalid. This model only works in thinking mode.",
     "inference", True, True, False),
])
def test_stored_terminal_reparse_error_survives_current_reads(history_case, error, stage, old, cache, retained):
    f = history_case()
    f.confirm()
    f.job = "OCR-" + f.id
    metrics = {"request_mode": "llm_reparse", "requested_provider": "gemini", "provider": "gemini",
               "processing_stage": stage, "result_state": "hard_failed", "error": error, "llm_assist": True}
    if retained is not None:
        metrics["confirmed_lines_retained"] = retained
    # Existing terminal SQL is the read input. No success/claim/receipt is invented,
    # and this is not proof of the current worker's completion-stage transitions.
    with session_scope() as session:
        current = session.get(OrderWorkflowState, f.id)
        session.add(OcrJob(id=f.job, order_id=f.id, input_reference="stored:history-component",
            status="failed", error_message=error, metrics=metrics, template_version_id=current.template_version_id,
            updated_at=datetime.utcnow() - timedelta(minutes=45) if old else datetime.utcnow()))
        secondary = deepcopy(current.secondary_actions_json)
        secondary["workflow_v2"]["ocr_job_id"] = f.job
        current.secondary_actions_json = secondary
    if cache:
        output = {"status": "done", "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|01/08|昼|Menu A|2|"}
        path = f.root / "original-diagnostic-output.json"
        path.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
        f.protected += (path,)
        rows = [["01/08", "昼", "Menu A", "2"]]
        cached = {**output, "rows": rows, "table_rows": rows, "tables": [{"rows": rows}],
                  "pages": [{"page_index": 1, "tables": [{"rows": rows}]}]}
        with session_scope() as session:
            session.add(OrderOcrCache(order_id=f.id, payload=cached))
            session.get(OcrJob, f.job).output_reference = f"file://{path}"
    detail = f.get()
    assert detail["ocr_status"] == "failed" and detail["ocr_error"] == error
    assert detail["ocr_metrics"]["request_mode"] == "llm_reparse"
    assert detail["ocr_metrics"]["processing_stage"] == stage
    assert detail["ocr_metrics"]["result_state"] == "hard_failed"
    assert detail["ocr_processing_stage"] == stage
    assert detail["ocr_result_state"] == "hard_failed"
    current = f.get("workflow-v2")
    assert current["ocr_job"]["ocr_job_id"] == f.job
    assert current["ocr_job"]["status"] == "failed" and current["ocr_job"]["error_message"] == error
    assert current["ocr_job"]["processing_stage"] == stage
    assert current["ocr_job"]["result_state"] == "hard_failed"
    assert f.get("workflow-v2/sheet", status=400) == {"detail": "saved_sheet_missing"}
    assert f.get("workflow-v2/sheet-source", status=400) == {"detail": "selected_ocr_required"}
    history = f.get("ocr-history")
    assert history["latest"] is None and history["revisions"] == []
    assert history["reparse_state"]["status"] == "hard_failed"
    if cache:
        assert history["diagnostics"]["cache"]["payload"] == cached
    attempt = history["latest_reparse_attempt"]
    assert {key: attempt[key] for key in ("job_id", "request_mode", "requested_provider", "provider", "processing_stage", "result_state", "error_message")} == {
        "job_id": f.job, "request_mode": "llm_reparse", "requested_provider": "gemini", "provider": "gemini",
        "processing_stage": stage, "result_state": "hard_failed", "error_message": error}
    f.get("history")
    with session_scope() as session:
        job = session.get(OcrJob, f.job)
        assert job.error_message == error and job.metrics["processing_stage"] == stage
        if old:
            assert job.updated_at < datetime.utcnow() - timedelta(minutes=40)
        if retained is not None:
            assert job.metrics["confirmed_lines_retained"] is retained


def test_week_options_and_explicit_context_selection(history_case):
    f = history_case()
    f.confirm()
    selected = next(o for o in f.get("week-options")["options"] if o["selected"])
    assert selected["week_id"] == WEEK and selected["label"].startswith("2026-01 (")
    assert selected["date_from"] == "2026-01-04" and selected["date_to"] == "2026-01-10"
    detail = f.get()
    assert detail["week"] == "2026-01" and detail["week_value"] == WEEK
    assert detail["week_label"] == selected["label"]


def test_week_options_offer_future_ranges_without_menu_or_received_date_selection(history_case):
    f = history_case(week="2026-03", period="2026-03@2026-03-22~2026-03-28", received=datetime(2026, 3, 11, 9),
                     lines=[_line("2026-03-22", "Future Menu", 3)], menu=False)
    options = f.get("week-options")["options"]
    target = next(o for o in options if o["week_id"] == "2026-03@2026-03-22~2026-03-28")
    assert target["date_from"] == "2026-03-22" and target["date_to"] == "2026-03-28"
    assert target["selected"] is False and not any(o["selected"] for o in options)
    detail = f.get()
    assert detail["stored_week_value"] == "2026-03"
    assert detail["week_value"] != "2026-03@2026-03-08~2026-03-14"


def test_order_week_candidates_match_calendar_suggestions_without_adoption(history_case):
    f = history_case(week="2026-04", period="2026-04@2026-04-05~2026-04-11", received=datetime(2026, 4, 6, 9),
                     lines=[_line("2026-04-06", "Week Match Menu", 3)], menu=False)
    detail = f.get()
    options = f.get("week-options")["options"]
    values = {item["week_id"] for item in options}
    assert "2026-04@2026-04-05~2026-04-11" in values
    assert "2026-04@2026-04-01~2026-04-07" not in values
    assert "2026-04@2026-04-05~2026-04-11" in {o["week_id"] for o in options}
    assert not any(o["selected"] for o in options)
    assert detail["stored_week_value"] == detail["week_value"] == "2026-04"
    assert f.get() == detail


@pytest.mark.parametrize("menu", [False, True], ids=["no-menu", "registered-menu"])
def test_ingest_calendar_options_are_menu_independent_readonly_suggestions(history_case, menu):
    f = history_case(menu=menu)
    with f.readonly():
        response = f.client.get("/ingest/week-options", params={"month_id": "2026-01", "facility_id": f.facility}, headers=f.actor.headers)
    assert response.status_code == 200, response.text
    options = response.json()["options"]
    assert [(item["date_from"], item["date_to"]) for item in options] == [
        ("2026-01-01", "2026-01-03"), ("2026-01-04", "2026-01-10"),
        ("2026-01-11", "2026-01-17"), ("2026-01-18", "2026-01-24"), ("2026-01-25", "2026-01-31")]
    assert all("selected" not in item for item in options)
    order_options = f.get("week-options")["options"]
    assert [{key: item[key] for key in options[0]} for item in order_options if item["week_id"].startswith("2026-01@")] == options
    assert f.get("workflow-v2")["state"] == "not_initialized"
    assert f.get()["stored_week_value"] == WEEK


@pytest.mark.parametrize("month", ["", "2026-13", "2026-04junk"])
def test_ingest_calendar_options_reject_invalid_month_without_mutation(history_case, month):
    f = history_case(menu=False)
    with f.readonly():
        response = f.client.get("/ingest/week-options", params={"month_id": month}, headers=f.actor.headers)
    assert response.status_code == 400
    assert response.json() == {"detail": "month_id is required" if not month else "invalid month_id"}


@pytest.mark.parametrize("period,facility,lines", [
    ("2026-04@2026-04-26~2026-05-02", "FAC00001", [
        _line("2026-04-29", "A", 1, daypart="朝"), _line("2026-04-30", "B", 1, daypart="朝"),
        _line("2026-05-01", "C", 1, daypart="朝"), _line("2026-05-02", "D", 1, daypart="朝")]),
    ("2026-04@2026-04-26~2026-04-30", "FAC00001", [
        _line(day, name, 1, daypart="朝") for day, name in [
            ("2026-04-26", "A"), ("2026-04-27", "A2"), ("2026-04-28", "A3"), ("2026-04-29", "A4"),
            ("2026-04-30", "A5"), ("2026-05-01", "B"), ("2026-05-02", "C")]]),
    ("2026-04@2026-04-26~2026-05-02", "FAC00014", [
        _line("2026-04-26", "A", 1, daypart="朝", area="X"), _line("2026-04-30", "B", 1, daypart="夕", area="X")]),
    ("2026-05@2026-05-01~2026-05-02", "FAC00001", [_line()]),
], ids=["explicit-cross-month", "explicit-short-not-promoted", "cross-month-single-month-lines", "two-day-exception"])
def test_explicit_operator_ranges_survive_context_and_readonly_options(history_case, period, facility, lines):
    f = history_case(week=None, period=period, facility=facility, received=datetime(2026, 4, 30, 9), lines=lines, menu=False)
    assert not any(o["selected"] for o in f.get("week-options")["options"])
    f.confirm()
    selected = next(o for o in f.get("week-options")["options"] if o["selected"])
    start, end = period.split("@", 1)[1].split("~")
    assert selected["week_id"] == period
    assert selected["date_from"] == start and selected["date_to"] == end
    detail = f.get()
    assert detail["week_value"] == period and detail["persisted_week_value"] == period
    current = f.get("workflow-v2")
    assert current["week_start"] == start and current["week_end"] == end
    with session_scope() as session:
        stored = session.query(OrderLine).filter_by(order_id=f.id).order_by(OrderLine.id).all()
        assert [{key: getattr(row, key) for key in line} for row, line in zip(stored, lines, strict=True)] == lines


def test_week_options_center_on_explicit_order_month_not_clock(history_case, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 3, 15, 9)
    monkeypatch.setattr(order_service, "datetime", FrozenDateTime)
    f = history_case()
    ids = {o["week_id"] for o in f.get("week-options")["options"]}
    assert any(i.startswith("2025-10@") or i.startswith("2025-11@") for i in ids)
    assert any(i.startswith("2026-03@") for i in ids)
    assert not any(i.startswith("2025-09@") for i in ids)
    assert not any(i.startswith("2026-04@") for i in ids)


def test_plain_month_is_not_promoted_from_received_date(history_case):
    f = history_case(week="2026-01")
    detail = f.get()
    assert detail["stored_week_value"] == "2026-01" and detail["week_value"] == "2026-01"
    assert not any(o["selected"] for o in f.get("week-options")["options"])
    assert f.get("workflow-v2")["state"] == "not_initialized"
    f.confirm()
    resolved = f.get()
    assert resolved["week"] == "2026-01"
    assert resolved["week_value"] == WEEK
    assert resolved["week_label"] == "2026-01 (01/04-01/10)"


def test_saved_sheet_is_not_rebased_by_legacy_db_config_on_read(history_case):
    f = history_case()
    f.produce()
    sheet = _sheet("8", "seeded", "row-schema-rebase-1")
    f.save(sheet)
    with session_scope() as session:
        session.add(FacilityConfig(facility_id=f.facility, config_json={"fax_template_override": {
            "columns_authoritative": True, "columns": [
                {"index": 0, "role": "date", "name": "date_mmdd", "header": "日付"},
                {"index": 1, "role": "daypart", "name": "daypart", "header": "区分"},
                {"index": 2, "role": "menu_name", "name": "menu", "header": "メニュー"},
                {"index": 3, "role": "quantity", "name": "qty.regular_2f", "header": "施設常食2F", "diet_type": "regular", "area_id": "2F"},
                {"index": 4, "role": "note", "name": "remarks", "header": "備考"},
            ]}}))
    assert f.get("workflow-v2/sheet")["sheet"] == sheet
    source = f.get("workflow-v2/sheet-source")["sheet"]
    assert source["header"][source["fields"].index("qty.regular_2f")] == "常食2F"
    assert f.get("workflow-v2/sheet")["sheet"]["rows"][0][3] == "8"


def test_build_recoverable_ocr_sheet_payload_can_skip_saved_draft(history_case):
    f = history_case()
    source = f.produce()["sheet"]
    sheet = _sheet("2", "", "row-recoverable-1")
    sheet["header"] = [*HEADER[:3], "旧常食2F", HEADER[4]]
    f.save(sheet)
    with f.readonly():
        with_saved, error = order_service.build_recoverable_ocr_sheet_payload(f.id, "menu_entries_missing", use_saved_draft=True)
        assert error is None and isinstance(with_saved, dict)
        assert with_saved["rows"] == sheet["rows"] and with_saved["row_ids"] == sheet["row_ids"]
        assert with_saved["header"] == sheet["header"]
        without_saved, error = order_service.build_recoverable_ocr_sheet_payload(f.id, "menu_entries_missing", use_saved_draft=False)
        assert error is None and isinstance(without_saved, dict)
        assert without_saved["source"] == source["source"] == "workflow_v2_selected_ocr_projection"
        for key in ("rows", "row_ids", "fields", "header"):
            assert guard.snapshots_equal(without_saved[key], source[key])
        assert without_saved["rows"][0][without_saved["fields"].index("qty.regular_2f")] == "2"
        assert without_saved["header"][3] != "旧常食2F"


@pytest.mark.parametrize("selected", [False, True], ids=["no-selected-source", "selected-source-2"])
def test_build_recoverable_ocr_sheet_payload_does_not_rehydrate_revision_without_saved_draft(history_case, selected):
    f = history_case()
    if selected:
        source = f.produce()["sheet"]
        assert source["rows"][0][source["fields"].index("qty.regular_2f")] == "2"
    else:
        f.confirm()
    with session_scope() as session:
        state = session.get(OrderWorkflowState, f.id)
        assert state.evidence_run_id == (f.evidence if selected else None)
        assert state.draft_id is None
        assert session.query(OrderSheetDraft).filter_by(order_id=f.id).count() == 0
        cache = session.get(OrderOcrCache, f.id)
        if cache is None:
            cache = OrderOcrCache(order_id=f.id)
            session.add(cache)
        cache.payload = {"table_rows": [["01/08", "昼", "Menu A", "2"]], "date_strings": ["01/08"]}
        session.add(OrderOcrRevision(id="REV-1", order_id=f.id, fields=FIELDS,
            header=[*HEADER[:3], "旧常食2F", HEADER[4]], rows=[["01/01", "朝", "Revision Menu", "9", ""]], row_ids=["rev-1"], row_count=1))
    assert f.get("ocr-history")["latest"]["rows"] == [["01/01", "朝", "Revision Menu", "9", ""]]
    with f.readonly():
        try:
            recovered, error = order_service.build_recoverable_ocr_sheet_payload(f.id, "menu_entries_missing", use_saved_draft=True)
        except Exception as exc:
            f.observations.append({"selected": selected, "exception": type(exc).__name__, "message": str(exc)})
            raise
        f.observations.append({"recoverable_payload": deepcopy(recovered), "error": error})
        if selected:
            assert error is None and isinstance(recovered, dict)
            assert recovered["row_ids"] != ["rev-1"]
            assert all("Revision Menu" not in row and "9" not in row for row in recovered["rows"])
            assert recovered["row_ids"] == source["row_ids"]
            assert recovered["rows"][0][recovered["fields"].index("qty.regular_2f")] == "2"
        else:
            assert recovered is None and error == "selected_ocr_required"


def test_activity_history_reads_existing_audit_without_reviving_lines_writer(history_case):
    f = history_case(lines=[_line(quantity=5)])
    with session_scope() as session:
        session.add(AuditLog(id="original-lines-audit", target=f.id, actor="operator", action="order_lines_update",
                            metadata_json={"line_id": "line-1", "quantity_original": 5}))
    history = f.get("history")
    assert history["order_id"] == f.id and isinstance(history["items"], list)
    audit = next(i for i in history["items"] if i["id"] == "original-lines-audit")
    assert audit["action"] == "order_lines_update"
    assert audit["metadata"] == {"line_id": "line-1", "quantity_original": 5}


@pytest.mark.parametrize("method,endpoint,body", [
    ("GET", "ocr-sheet", None), ("GET", "draft-sheet", None), ("GET", "workflow-state", None),
    ("POST", "ocr-apply", {**_sheet("3", "api", "row-api-1"), "ui_mode": "sheet"}),
    ("POST", "ocr-sheet-save", _sheet("8", "exact-save", "row-api-save-1")),
    ("POST", "week", {"week": "2026-05@2026-05-01~2026-05-02"}),
    ("PUT", "lines", {"lines": [{**_line(quantity=5), "date": "2026-01-08", "line_id": "line-1"}]}),
])
def test_retired_history_entrypoints_refuse_without_mutation(history_case, method, endpoint, body):
    f = history_case()
    with f.readonly():
        response = f.client.request(method, f"/orders/{f.id}/{endpoint}", json=body, headers=f.actor.headers)
    assert response.status_code == 410
    assert response.json() == {"detail": {**LEGACY, "endpoint": endpoint}}
