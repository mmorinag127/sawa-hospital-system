from copy import deepcopy
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.db import session_scope
from src.models.order import Order
from src.models.order import OrderLine
from src.models.document import OrderDocument
from src.models.uploaded_pdf import UploadedPdf
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_current_state import OrderCurrentState
from src.models.order_ocr_cache import OrderOcrCache
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_sheet_patch_candidate import OrderSheetPatchCandidate
from src.models.order_workflow_state import OrderWorkflowState
from src.models.order_output_artifact import OrderBaggingResult, OrderOutputBundle
from src.models.facility_template_version import FacilityTemplateVersion
from src.models.facility import Facility
from src.models.ocr_job import OcrJob
from src.models.menu import MenuMaster, MonthlyMenu, MonthlyMenuEntry, MonthlyMenuItem
from src.services import order_service, order_workflow_v2_service
from src.services import workflow_attempt_guard_service as guard
from src.services import order_output_artifact_service as output_artifacts
from src.services import workflow_ocr_delivery_service as delivery
from test_h2_operator_repair_revision import repair_order, _capture_reparse_job
from test_modernization_workflow_surface_parity import database_snapshot


def _current_workflow_revision(order_id: str) -> int:
    workflow, error = order_workflow_v2_service.get_workflow(order_id)
    assert error is None
    assert isinstance(workflow, dict)
    return workflow["revision"]


def _id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:12]}"


@pytest.mark.parametrize("action", [
    order_workflow_v2_service.confirm_bagging,
    order_workflow_v2_service.prepare_output_review,
])
def test_output_review_entrypoints_keep_missing_source_blockers(action, authored_april_context):
    assert action(_id("MISSING"), expected_revision=0) == (None, "order_not_found")
    order_id, _, _ = _create_order_with_evidence()
    assert action(order_id, expected_revision=_current_workflow_revision(order_id)) == (
        None, "bagging_result_required",
    )
    with session_scope() as session:
        assert session.query(OrderOutputBundle).filter_by(order_id=order_id).count() == 0


def _create_order_with_evidence() -> tuple[str, str, str]:
    order_id = _id("ORDv2")
    evidence_id_1 = _id("OEV")
    evidence_id_2 = _id("OEV")
    with session_scope() as session:
        session.add(
            Order(
                id=order_id,
                facility_code="FAC_TEST",
                week_code="2026-04-26",
                status="要確認",
                document_uri=f"file:///{order_id}.pdf",
                message_id=f"msg-{order_id}",
                received_at=datetime.utcnow(),
            )
        )
        for evidence_id in [evidence_id_1, evidence_id_2]:
            session.add(
                OrderOcrEvidenceRun(
                    id=evidence_id,
                    order_id=order_id,
                    schema_version="workflow-v2-test",
                    producer_version="test",
                    source="test",
                    status="ready",
                    payload_json={"pipeline_version": "test-pipeline"},
                    artifact_manifest_json={"overlay": f"{evidence_id}.pdf"},
                    artifact_digest=f"digest-{evidence_id}",
                    capabilities_json={},
                    degraded_reasons_json=[],
                    created_at=datetime.utcnow(),
                )
            )
    return order_id, evidence_id_1, evidence_id_2


def _stamp_evidence_with_workflow_template(order_id: str, *evidence_ids: str) -> str | None:
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        template_version_id = str(getattr(workflow, "template_version_id", "") or "").strip() if workflow else ""
        for evidence_id in evidence_ids:
            evidence = session.get(OrderOcrEvidenceRun, evidence_id)
            if evidence is not None:
                evidence.template_version_id = template_version_id or None
        return template_version_id or None


def _install_fake_materialization(monkeypatch) -> None:
    def fake_candidate(_order_id, *, draft_record, facility_id, existing_week_code, received_at):
        _ = facility_id, existing_week_code, received_at
        sheet = draft_record.get("draft_sheet_json") if isinstance(draft_record, dict) else {}
        if isinstance(sheet, dict) and sheet.get("test_materialization_error"):
            return {"error": "test_materialization_error", "lines": [], "line_count": 0}
        lines = []
        rows = sheet.get("rows") if isinstance(sheet, dict) else []
        for row_idx, row in enumerate(rows if isinstance(rows, list) else []):
            if isinstance(row, dict):
                menu_name = str(row.get("menu_name") or row.get("menu") or "").strip()
                for field in ["regular", "soft", "qty.regular"]:
                    value = row.get(field)
                    if value in {"", None}:
                        continue
                    lines.append(
                        {
                            "date": "2026-04-26",
                            "daypart": "lunch",
                            "menu_name": menu_name,
                            "diet_type": field,
                            "area_id": "main",
                            "quantity_original": float(value),
                            "quantity_corrected": None,
                            "source_row_index": row_idx,
                        }
                    )
        return {"error": None, "lines": lines, "line_count": len(lines), "derived_week_code": "2026-04@2026-04-26~2026-04-30"}

    def fake_materialize(session, order, candidate):
        for idx, line in enumerate(candidate.get("lines") or []):
            session.add(
                OrderLine(
                    id=_id("OL"),
                    order_id=order.id,
                    line_id=f"line-{idx + 1}",
                    date=datetime.fromisoformat(str(line["date"])).date(),
                    daypart=line.get("daypart"),
                    menu_name=line.get("menu_name"),
                    diet_type=line.get("diet_type"),
                    area_id=line.get("area_id"),
                    quantity_original=line.get("quantity_original"),
                    quantity_corrected=line.get("quantity_corrected"),
                )
            )
        order.lines_updated_at = datetime.utcnow()

    fake_order_service = SimpleNamespace(
        _build_materialization_candidate_from_draft_record=fake_candidate,
        _materialize_confirmed_lines_from_candidate=fake_materialize,
    )
    monkeypatch.setattr(order_workflow_v2_service, "_get_order_service_module", lambda: fake_order_service)


def _install_fake_ocr_prerequisite(monkeypatch, error: str | None = None) -> None:
    fake_order_service = SimpleNamespace(
        _build_hakodate_weekly_menu_base_sheet=lambda _order_id: (
            None if error else {"fields": ["date", "daypart", "menu", "qty.regular"], "rows": [["04/26", "朝", "A", ""]]},
            error,
        ),
    )
    monkeypatch.setattr(order_workflow_v2_service, "_get_order_service_module", lambda: fake_order_service)


def _registered_template_config(facility_id: str) -> dict:
    return {
        "facility_id": facility_id,
        "fax_template_id": "fax_layout_regular_forbidden_v1",
        "fax_template": {
            "template_id": "fax_layout_regular_forbidden_v1",
            "columns": [
                {"index": 0, "role": "date", "header": "日付", "source_index": 0},
                {"index": 1, "role": "daypart", "header": "区分", "source_index": 1},
                {"index": 2, "role": "menu_name", "header": "献立", "source_index": 2},
                {"index": 3, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X", "source_index": 3},
                {"index": 4, "role": "quantity", "header": "-", "diet_type": "placeholder", "area_id": "X", "source_index": 4},
                {"index": 5, "role": "note", "header": "備考欄", "source_index": 5},
            ],
        },
    }


def _template_columns() -> list[dict]:
    return [
        {"index": 0, "role": "date", "header": "日付", "source_index": 0},
        {"index": 1, "role": "daypart", "header": "区分", "source_index": 1},
        {"index": 2, "role": "menu_name", "header": "献立", "source_index": 2},
        {"index": 3, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X", "source_index": 3},
        {"index": 4, "role": "note", "header": "備考欄", "source_index": 4},
    ]


def _install_active_template_version(
    facility_id: str = "FAC00001",
    template_id: str = "template-fac00001",
    *,
    columns: list[dict] | None = None,
    valid_from=None,
    valid_to=None,
) -> str:
    normalized_columns = order_workflow_v2_service.facility_template_version_service.normalize_template_columns(
        columns if columns is not None else _template_columns()
    )
    digest = order_workflow_v2_service.facility_template_version_service.template_digest(
        template_id=template_id,
        columns=normalized_columns,
    )
    version_id = _id("FTV")
    with session_scope() as session:
        if session.get(Facility, facility_id) is None:
            session.add(Facility(id=facility_id, name=f"Template Facility {facility_id}"))
        active_versions = (
            session.query(FacilityTemplateVersion)
            .filter(
                FacilityTemplateVersion.facility_id == facility_id,
                FacilityTemplateVersion.status == "active",
            )
            .all()
        )
        if len(active_versions) == 1:
            active = active_versions[0]
            if (
                active.template_id == template_id
                and active.columns_json == normalized_columns
                and active.template_digest == digest
                and active.valid_from == valid_from
                and active.valid_to == valid_to
            ):
                return active.id
        for active in active_versions:
            active.status = "archived"
            active.archived_at = datetime.utcnow()
        session.add(
            FacilityTemplateVersion(
                id=version_id,
                facility_id=facility_id,
                version="test",
                status="active",
                template_id=template_id,
                source="test-active-template",
                columns_json=normalized_columns,
                cells_json=[],
                template_digest=digest,
                valid_from=valid_from,
                valid_to=valid_to,
                validation_json={"errors": [], "warnings": []},
                created_at=datetime.utcnow(),
                activated_at=datetime.utcnow(),
            )
        )
    return version_id


def _fixture_template_history(facility_id):
    with session_scope() as session:
        return [
            tuple(getattr(row, column.name) for column in FacilityTemplateVersion.__table__.columns)
            for row in session.query(FacilityTemplateVersion)
            .filter_by(facility_id=facility_id).order_by(FacilityTemplateVersion.id).all()
        ]


def test_install_same_active_configuration_is_idempotent():
    first = _install_active_template_version()
    before = _fixture_template_history("FAC00001")
    assert _install_active_template_version() == first
    assert _fixture_template_history("FAC00001") == before
    assert len(before) == 1


def test_install_different_configuration_preserves_history():
    first = _install_active_template_version()
    second = _install_active_template_version(template_id="explicit-other-template")
    assert second != first
    with session_scope() as session:
        assert session.get(FacilityTemplateVersion, first).status == "archived"
        assert session.get(FacilityTemplateVersion, second).status == "active"
        assert session.query(FacilityTemplateVersion).filter_by(facility_id="FAC00001").count() == 2


def test_install_archived_configuration_does_not_reuse_history():
    import sqlalchemy as sa
    first = _install_active_template_version()
    with session_scope() as session:
        session.get(FacilityTemplateVersion, first).status = "archived"
    before = _fixture_template_history("FAC00001")
    with pytest.raises(sa.exc.IntegrityError):
        _install_active_template_version()
    assert _fixture_template_history("FAC00001") == before


def test_install_different_effective_range_does_not_ignore_range():
    import sqlalchemy as sa
    from datetime import date
    before = _fixture_template_history("FAC00001")
    with pytest.raises(sa.exc.IntegrityError):
        _install_active_template_version(valid_from=date(2026, 7, 1))
    assert _fixture_template_history("FAC00001") == before


def _add_ocr_job(order_id: str, job_id: str = "OCR-job", *, job_order_id: str | None = None) -> str:
    with session_scope() as session:
        row = session.get(OcrJob, job_id)
        if row is None:
            row = OcrJob(
                id=job_id,
                created_at=datetime.utcnow(),
            )
            session.add(row)
        row.order_id = job_order_id if job_order_id is not None else order_id
        row.template_version_id = None
        row.status = "running"
        row.input_reference = "file:///input.pdf"
        row.updated_at = datetime.utcnow()
    return job_id


def test_captured_workflow_cannot_publish_legacy_table_as_cache_or_evidence(repair_order) -> None:
    order_id, _, _ = repair_order
    job_id = _id("OCR")
    workflow_attempt_id = _id("attempt")
    expected_revision = 7
    with session_scope() as session:
        session.get(OrderWorkflowState, order_id).revision = expected_revision
    with session_scope() as session:
        _captured, pdf, _version = _capture_reparse_job(session, order_id, job_id, workflow_attempt_id)
    payload = {
        "status": "done",
        "input_reference": pdf["storage_uri"],
        "output_reference": f"file:///{order_id}.json",
        "table_raw": "|day|quantity|\n|-|-|\n|1|2|",
    }
    before = database_snapshot()
    with (order_service.workflow_ocr_write_context(workflow_attempt_id, expected_revision),
          guard.workflow_execution_context(job_id, order_id, workflow_attempt_id, expected_revision)):
        persisted = order_service._save_order_ocr_cache(
            order_id,
            payload,
            job_id=job_id,
            augment_hakodate_artifacts=False,
            refresh_workflow=False,
        )

    assert persisted is None
    assert database_snapshot() == before
    with session_scope() as session:
        job = session.get(OcrJob, job_id)
        assert job is not None
        workflow = session.get(OrderWorkflowState, order_id)
        assert workflow is not None
        workflow.revision = expected_revision + 1
    before_stale = database_snapshot()

    with (order_service.workflow_ocr_write_context(workflow_attempt_id, expected_revision),
          guard.workflow_execution_context(job_id, order_id, workflow_attempt_id, expected_revision)):
        with pytest.raises(ValueError, match="configured_execution_input_changed"):
            order_service._save_order_ocr_cache(
                order_id,
                {**payload, "table_raw": "|day|quantity|\n|-|-|\n|1|999|"},
                job_id=job_id,
                augment_hakodate_artifacts=False,
                refresh_workflow=False,
            )

    assert database_snapshot() == before_stale


def test_synchronous_cache_cannot_replace_canonical_source_with_legacy_table(repair_order) -> None:
    order_id, _, _ = repair_order
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        workflow.revision = 11
        session.add(
            OrderOcrCache(
                order_id=order_id,
                payload={"table_raw": "|day|quantity|\n|-|-|\n|1|2|"},
                updated_at=datetime.utcnow(),
            )
        )

    # Capture belongs to the existing authored fixture, not the legacy file URI.
    write_input = order_service._capture_synchronous_cache_write_input(order_id)
    assert isinstance(write_input, dict)
    saved = order_service._save_order_ocr_cache(
        order_id,
        {"table_raw": "|day|quantity|\n|-|-|\n|1|3|"},
        synchronous_write_input=write_input,
        augment_hakodate_artifacts=False,
        persist_evidence=False,
        refresh_workflow=False,
    )
    assert saved is None

    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        assert workflow is not None
        workflow.revision = 12

    rejected = order_service._save_order_ocr_cache(
        order_id,
        {"table_raw": "|day|quantity|\n|-|-|\n|1|999|"},
        synchronous_write_input=write_input,
        augment_hakodate_artifacts=False,
        persist_evidence=False,
        refresh_workflow=False,
    )
    assert rejected is None
    with session_scope() as session:
        cache = session.get(OrderOcrCache, order_id)
        assert cache is not None
        assert cache.payload["table_raw"] == "|day|quantity|\n|-|-|\n|1|2|"


@pytest.fixture(autouse=True)
def _standard_active_template_for_workflow_tests(isolated_formal_schema) -> None:
    _install_active_template_version("FAC00001", "template-fac00001")


def _write_authored_context(tmp_path, monkeypatch, facility):
    """Publish this case's original config; only the dated envelope is new."""
    facility = {"facility_name": f"Template Facility {facility['facility_id']}",
                "areas": None, **facility}
    source = tmp_path / "case-facility.json"
    source.write_text(json.dumps({"facilities": [{
        "facility_id": facility["facility_id"], "versions": [{
            "version_id": "unit-april-contract", "valid_from": "2026-04-26",
            "valid_to": "2026-04-30", "facility": facility,
        }],
    }]}), encoding="utf-8")
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(source))
    monkeypatch.delenv("FACILITY_MASTER_URI", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    return source


def _attach_context_pdf(order_id, tmp_path):
    """Owned component document, with this order's explicit context and no OCR values."""
    import fitz

    with session_scope() as session:
        order = session.get(Order, order_id)
        assert order.template_version_id
        identity = f"{order.facility_code} / {order.week_code} / component input only"
        with fitz.open() as document:
            document.new_page().insert_text((40, 40), identity)
            body = document.tobytes()
        path = tmp_path / f"{order_id}.pdf"
        path.write_bytes(body)
        document = OrderDocument(id=_id("DOC"), storage_uri=str(path), source_email_id=order.message_id)
        session.add(document)
        session.flush()
        order.current_document_id = document.id
        order.document_uri = str(path)
        session.add(UploadedPdf(id=_id("PDF"), message_id=order.message_id,
                               storage_uri=str(path), original_filename=path.name,
                               content_sha256=sha256(body).hexdigest(), received_at=order.received_at))
    with session_scope() as session:
        receipt = guard.capture_selected_pdf_receipt(session, session.get(Order, order_id))
        assert receipt["storage_uri"] == str(path)
        assert receipt["sha256"] == sha256(body).hexdigest()
    return str(path)


@pytest.fixture
def april_output_context(tmp_path, monkeypatch, record_property):
    """Explicit April component inputs; real capture/bagging, not recognized FAX."""
    columns = _template_columns()
    columns.insert(4, {"index": 4, "source_index": 4, "role": "quantity", "header": "軟菜",
                       "diet_type": "soft", "area_id": "X"})
    columns[-1] = {**columns[-1], "index": 5, "source_index": 5}
    _write_authored_context(tmp_path, monkeypatch, {
        "facility_id": "FAC00001", "fax_template_id": "template-fac00001",
        "fax_template": {"template_id": "template-fac00001", "page": 1, "columns": columns},
        "daily_label_comparable_diet_types": [],
        "bag_types": [{"bag_type_id": "UNIT-1000G", "label": "Unit 1000g", "max_qty": 1000, "unit": "g"}],
        "packaging_policy": {"split_key": ["facility", "date", "daypart", "menu_name", "diet_type", "area_id", "bag_type"]},
    })
    build_bag_rows = order_workflow_v2_service._build_bag_rows_for_candidate
    def observe_output(**kwargs):
        try:
            return build_bag_rows(**kwargs)
        except Exception as exc:
            record_property("output_exception", repr(exc.__cause__))
            raise
    monkeypatch.setattr(order_workflow_v2_service, "_build_bag_rows_for_candidate", observe_output)

    def owned_sheet(order_id, original):
        sheet = deepcopy(original)
        old_fields = original.get("fields")
        rows = []
        for row in original["rows"]:
            values = dict(zip(old_fields, row, strict=True)) if old_fields else row
            rows.append([values.get("date", "04/26"), values.get("daypart", "昼"),
                         values.get("menu", values.get("menu_name")),
                         values.get("regular", ""), values.get("soft", ""), values.get("note", "")])
        sheet["fields"] = ["date", "daypart", "menu", "qty.regular_x", "qty.soft_x", "note"]
        sheet["header"] = ["日付", "区分", "献立", "常食", "軟菜", "備考欄"]
        sheet["rows"] = rows
        sheet["row_ids"] = [f"april-row-{index}" for index in range(len(rows))]
        with session_scope() as session:
            if session.get(MonthlyMenu, "2026-04") is None:
                session.add(MonthlyMenu(id="2026-04", month_start=date(2026, 4, 1)))
                session.flush()
            for index, row in enumerate(rows):
                menu_date = date(2026, *map(int, row[0].split("/")))
                for diet in ("regular", "soft"):
                    existing = session.query(MonthlyMenuItem).filter_by(
                        monthly_menu_id="2026-04", name=row[2], daypart=row[1], diet_type=diet).first()
                    if existing is None:
                        normalized = order_service.menu_service._normalize_menu_name(row[2])
                        master = session.query(MenuMaster).filter_by(normalized_name=normalized).first()
                        if master is None:
                            master = MenuMaster(id=_id("MM"), name=row[2], normalized_name=normalized,
                                unit_type="g", qty_per_serving=100, category="主", daypart=row[1])
                            session.add(master)
                            session.flush()
                        session.add(MonthlyMenuItem(id=_id("MI"), monthly_menu_id="2026-04",
                            menu_master_id=master.id, name=row[2], diet_type=diet, category="主",
                            daypart=row[1], unit_type="g", qty_per_serving=100))
                    entry = session.query(MonthlyMenuEntry).filter_by(monthly_menu_id="2026-04",
                        menu_date=menu_date, name=row[2], daypart=row[1], diet_type=diet).first()
                    if entry is None:
                        session.add(MonthlyMenuEntry(id=_id("ME"), monthly_menu_id="2026-04",
                            menu_date=menu_date, daypart=row[1], category="主", diet_type=diet,
                            name=row[2], slot_index=index))
            needs_pdf = session.get(Order, order_id).current_document_id is None
        if needs_pdf:
            _attach_context_pdf(order_id, tmp_path)
            with session_scope() as session:
                order = session.get(Order, order_id)
                order_service._record_current_order_version(session, order, session.get(OrderDocument, order.current_document_id))
        return sheet

    return owned_sheet


@pytest.fixture
def april_projection(monkeypatch, tmp_path, april_output_context):
    """Real captured producer/projection; external rendered OCR files are component doubles."""
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path / "canonical-artifacts"))
    monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
    from src.services import hakodate_cell_ocr_batch_service as batch
    from src.services.storage_service import load_selected_pdf_bytes
    from test_h2_operator_repair_revision import _provider_output_files

    order_id, wrong_id, _ = _create_order_with_evidence()
    context, error = order_workflow_v2_service.confirm_context(
        order_id=order_id, facility_id="FAC00001", week_start="2026-04-26", week_end="2026-04-30",
        template_id="template-fac00001", expected_revision=0,
    )
    assert error is None, error
    april_output_context(order_id, {"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]})
    with session_scope() as session:
        receipt, start = order_workflow_v2_service._axis_source_receipt(
            session, session.get(Order, order_id), session.get(OrderWorkflowState, order_id))
    def manifest(**kwargs):
        return {
            "configured_input": deepcopy(start["configured_input"]),
            "selected_pdf_receipt": deepcopy(receipt["selected_pdf_receipt"]),
            "_verified_pdf_bytes": load_selected_pdf_bytes(
                receipt["selected_pdf_receipt"]["storage_uri"], receipt["selected_pdf_receipt"]),
        }
    monkeypatch.setattr(order_service, "_build_live_hakodate_manifest_item", manifest)
    calls = []
    def recognize(*, item, output_dir, **kwargs):
        regions = [{**deepcopy(region), "bbox": [index * 20, 0, index * 20 + 10, 10]}
                   for index, region in enumerate(item["original_target_semantics"])]
        calls.append(deepcopy(regions))
        values = {region["sheet_cell"]: "70" if region["field"] == "qty.regular_x" else ""
                  for region in regions}
        records = [{**region, "ocr_text": values[region["sheet_cell"]], "recognizer_score": 1.0,
                    "recognizer_accepted_candidate": {"normalized_digits": values[region["sheet_cell"]]}}
                   for region in regions]
        outputs = _provider_output_files(output_dir, records=records, regions=regions, sheet_values=values)
        return SimpleNamespace(
            metrics={"expected_sheet_cells": item["original_expected_sheet_cells"]},
            expected_sheet_cells=tuple(item["original_expected_sheet_cells"]),
            outputs=outputs, ocr_engine="component-external-recognizer",
        ), None
    monkeypatch.setattr(batch, "build_hakodate_best_method_for_manifest_item", recognize)
    job_id = _id("OCRprojection")
    queued = _queue_owned_april_job(order_id, job_id, tmp_path, monkeypatch)
    revision, attempt = queued["workflow"]["revision"], queued["ocr_job"]["workflow_attempt_id"]
    claim = _claim_april_job(queued)
    with (delivery.claim_context(claim),
          order_service.workflow_ocr_write_context(attempt, revision, job_id, order_id),
          guard.workflow_execution_context(job_id, order_id, attempt, revision)):
        evidence, error = order_service.rerun_ocr_evidence_only(
            order_id, job_id=job_id, project_sheet=False, refresh_workflow=False)
    assert error is None, (error, evidence)
    assert len(calls) == 1
    _stamp_evidence_with_workflow_template(order_id, wrong_id)
    selected, error = order_workflow_v2_service.select_ocr_result(
        order_id, evidence["id"], expected_revision=_current_workflow_revision(order_id))
    assert error is None, error
    return order_id, wrong_id, evidence["id"], context["template_version_id"]

def _queue_owned_april_job(order_id, job_id, tmp_path, monkeypatch):
    for key, value in {
        "PROJECT_ID": "synthetic-owned-ocr-unit", "LOCATION": "asia-northeast2",
        "QUEUE": "synthetic-owned-ocr-unit", "TARGET_URL": "https://reserved.invalid",
        "SERVICE_ACCOUNT_EMAIL": "unit-worker@synthetic-owned-ocr-unit.iam.gserviceaccount.com",
        "AUDIENCE": "https://reserved.invalid", "DISPATCH_DEADLINE_SECONDS": "300",
    }.items():
        monkeypatch.setenv("HOSPITAL_WORKFLOW_OCR_DELIVERY_" + key, value)
    with session_scope() as session:
        uri = session.get(Order, order_id).document_uri if session.get(Order, order_id).current_document_id else None
    uri = uri or _attach_context_pdf(order_id, tmp_path)
    queued, error = order_workflow_v2_service.mark_ocr_run_queued(
        order_id, job_id, input_reference=uri, request_mode="ocr_rerun",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None, error
    return queued


def _claim_april_job(queued):
    claim = delivery.claim_delivery(queued["delivery"]["id"], queued["delivery"]["generation"],
                                    lease_expires_at=datetime.utcnow() + timedelta(minutes=5))
    assert not claim.get("duplicate"), claim
    return claim


def _complete_owned_april_job(order_id, job_id, queued, payload, monkeypatch):
    from pathlib import Path
    from src.services import hakodate_cell_ocr_batch_service as batch
    from src.services.storage_service import load_selected_pdf_bytes
    from tests.helpers.authored_cell_values import declared_field
    from test_h2_operator_repair_revision import _provider_output_files

    job = queued["ocr_job"]
    revision, attempt = queued["workflow"]["revision"], job["workflow_attempt_id"]
    claim = _claim_april_job(queued)
    with (delivery.claim_context(claim),
          order_service.workflow_ocr_write_context(attempt, revision, job_id, order_id),
          guard.workflow_execution_context(job_id, order_id, attempt, revision)):
        start = guard.processing_start_read_input()
        pdf = guard.processing_start_pdf_receipt()
        original_path = Path(pdf["storage_uri"])
        assert original_path.is_absolute() and original_path.is_file()
        monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
        monkeypatch.setenv("OCR_ARTIFACT_DIR", str(original_path.parent / "canonical-artifacts"))
        sheet, = payload["tables"]
        fields = sheet["fields"]
        declared = {}
        for row in sheet["rows"]:
            day = order_service.parse_date_string(row[fields.index("date")], datetime(2026, 4, 26),
                explicit_week=start["order"]["week_code"])
            identity = (day.isoformat(), order_service._normalize_daypart_key(row[fields.index("daypart")]),
                        order_service._normalize_menu_text(row[fields.index("menu")]))
            assert identity not in declared
            declared[identity] = row

        def manifest(**_kwargs):
            return {"configured_input": deepcopy(start["configured_input"]),
                    "selected_pdf_receipt": deepcopy(pdf),
                    "_verified_pdf_bytes": load_selected_pdf_bytes(pdf["storage_uri"], pdf)}

        calls = []

        def recognize(*, item, output_dir, **_kwargs):
            regions = [{**deepcopy(region), "bbox": [index * 20, 0, index * 20 + 10, 10]}
                       for index, region in enumerate(item["original_target_semantics"])]
            values = {}
            for region in regions:
                assigned = set()
                for target in region["logical_targets"]:
                    identity = (target["date"], order_service._normalize_daypart_key(target["daypart"]),
                                order_service._normalize_menu_text(target["menu_name"]))
                    assert identity in declared
                    assigned.add(str(declared[identity][fields.index(declared_field(target, fields))]))
                assert len(assigned) == 1
                values[region["sheet_cell"]] = assigned.pop()
            records = [{**region, "ocr_text": values[region["sheet_cell"]], "recognizer_score": 1.0,
                        "recognizer_accepted_candidate": {"normalized_digits": values[region["sheet_cell"]]}}
                       for region in regions]
            calls.append(regions)
            return SimpleNamespace(metrics={"expected_sheet_cells": item["original_expected_sheet_cells"]},
                expected_sheet_cells=tuple(item["original_expected_sheet_cells"]),
                outputs=_provider_output_files(output_dir, records=records, regions=regions, sheet_values=values),
                ocr_engine="component-external-recognizer"), None

        monkeypatch.setattr(order_service, "_build_live_hakodate_manifest_item", manifest)
        monkeypatch.setattr(batch, "build_hakodate_best_method_for_manifest_item", recognize)
        evidence, error = order_service.rerun_ocr_evidence_only(
            order_id, job_id=job_id, project_sheet=False, refresh_workflow=False)
    assert error is None, error
    assert len(calls) == 1
    return evidence, order_workflow_v2_service.get_workflow(order_id)[0]


@pytest.fixture
def authored_april_context(tmp_path, monkeypatch) -> None:
    facility = {
        "facility_id": "FAC00001",
        "facility_name": "Template Facility FAC00001",
        "areas": None,
        "fax_template_id": "template-fac00001",
        "fax_template": {"template_id": "template-fac00001", "page": 1, "columns": _template_columns()},
    }
    source = tmp_path / "facility-master.json"
    source.write_text(json.dumps({"facilities": [{
        "facility_id": "FAC00001",
        "versions": [{
            "version_id": "authored-april-context",
            "valid_from": "2026-04-26",
            "valid_to": "2026-04-30",
            "facility": facility,
        }],
    }]}), encoding="utf-8")
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(source))
    monkeypatch.delenv("FACILITY_MASTER_URI", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)


def test_context_confirm_requires_explicit_facility_week_and_template(authored_april_context) -> None:
    order_id, _, _ = _create_order_with_evidence()

    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        expected_revision=0,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )

    assert error is None
    assert workflow is not None
    assert workflow["state"] == "context_confirmed"
    assert workflow["facility_id"] == "FAC00001"
    assert workflow["week_start"] == "2026-04-26"
    assert workflow["week_end"] == "2026-04-30"
    assert workflow["template_id"] == "template-fac00001"
    assert workflow["selected_ocr_result_id"] is None
    assert workflow["saved_sheet_id"] is None
    with session_scope() as session:
        order = session.get(Order, order_id)
        assert order.facility_code == "FAC00001"
        assert order.week_code == "2026-04@2026-04-26~2026-04-30"


def test_context_suggestion_is_recorded_without_confirming_step1() -> None:
    order_id, _, _ = _create_order_with_evidence()

    workflow, error = order_workflow_v2_service.record_context_suggestion(
        order_id=order_id,
        expected_revision=0,
        suggestion={
            "source": "ingest_first_pass_ocr",
            "facility_id": "FAC00002",
            "facility_name": "シルバーホームなごみ",
            "week_code": "2026-04@2026-04-26~2026-04-30",
            "date_hints": ["4/26", "4/30"],
            "confidence": "high",
        },
    )

    assert error is None
    assert workflow is not None
    assert workflow["state"] == "uploaded"
    assert workflow["facility_id"] is None
    assert workflow["week_start"] is None
    assert workflow["context_suggestion"]["facility_id"] == "FAC00002"
    assert workflow["context_suggestion"]["week_start"] == "2026-04-26"
    assert workflow["context_suggestion"]["week_end"] == "2026-04-30"
    with session_scope() as session:
        order = session.get(Order, order_id)
        assert order.facility_code == "FAC_TEST"
        assert order.week_code == "2026-04-26"


def test_workflow_v2_get_endpoints_do_not_create_workflow_rows(authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()

    workflow, error = order_workflow_v2_service.get_workflow(order_id)
    assert error is None
    assert workflow["state"] == "not_initialized"
    assert workflow["blockers"] == ["workflow_not_initialized"]

    results, error = order_workflow_v2_service.list_ocr_results(order_id)
    assert error is None
    assert results["workflow_state"] == "not_initialized"
    assert results["selected_ocr_result_id"] is None
    assert any(item["ocr_result_id"] == evidence_id_1 and not item["selected"] for item in results["results"])

    saved_sheet, error = order_workflow_v2_service.get_saved_sheet(order_id)
    assert saved_sheet is None
    assert error == "workflow_not_initialized"

    sheet_source, error = order_workflow_v2_service.build_sheet_from_selected_ocr(order_id)
    assert sheet_source is None
    assert error == "workflow_not_initialized"

    quad_review, error = order_workflow_v2_service.get_quad_review(order_id)
    assert quad_review is None
    assert error == "workflow_not_initialized"

    header_axis_review, error = order_workflow_v2_service.get_header_axis_review(order_id)
    assert header_axis_review is None
    assert error == "workflow_not_initialized"

    inspection, error = order_workflow_v2_service.get_inspection(order_id)
    assert error is None
    assert inspection["workflow"]["state"] == "not_initialized"
    assert inspection["artifact_lineage"]["selected_ocr_result_id"] is None

    with session_scope() as session:
        assert session.get(OrderWorkflowState, order_id) is None


def test_list_ocr_results_only_returns_current_template_candidates(authored_april_context) -> None:
    order_id, current_evidence_id, mismatched_evidence_id = _create_order_with_evidence()

    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        expected_revision=0,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    assert error is None
    current_template_version_id = workflow["template_version_id"]
    archived_template_version_id = _id("FTVarchived")
    null_template_evidence_id = _id("OEV")
    with session_scope() as session:
        session.add(
            FacilityTemplateVersion(
                id=archived_template_version_id,
                facility_id="FAC00001",
                version="archived",
                status="archived",
                template_id="template-fac00001",
                source="test-archived-template",
                columns_json=_template_columns(),
                cells_json=[],
                template_digest="archived-digest",
                validation_json={"errors": [], "warnings": []},
                created_at=datetime.utcnow(),
            )
        )
        session.get(OrderOcrEvidenceRun, current_evidence_id).template_version_id = current_template_version_id
        session.get(OrderOcrEvidenceRun, mismatched_evidence_id).template_version_id = archived_template_version_id
        session.add(
            OrderOcrEvidenceRun(
                id=null_template_evidence_id,
                order_id=order_id,
                schema_version="workflow-v2-test",
                producer_version="test",
                source="test",
                status="ready",
                payload_json={"pipeline_version": "test-pipeline"},
                artifact_manifest_json={"overlay": f"{null_template_evidence_id}.pdf"},
                artifact_digest=f"digest-{null_template_evidence_id}",
                capabilities_json={},
                degraded_reasons_json=[],
                created_at=datetime.utcnow(),
            )
        )

    results, error = order_workflow_v2_service.list_ocr_results(order_id)

    assert error is None
    assert results["candidate_template_version_id"] == current_template_version_id
    assert results["hidden_template_mismatch_result_count"] == 2
    assert [item["ocr_result_id"] for item in results["results"]] == [current_evidence_id]
    assert results["results"][0]["template_version_id"] == current_template_version_id


def test_exact_diagnostic_ocr_result_api_is_scoped_authenticated_and_readonly(april_projection, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from src.main import app
    from tests.contract.auth_support import registered_hospital_operator

    order_id, _, selected_evidence_id, template_version_id = april_projection
    diagnostic_id, stale_id, foreign_order_id, foreign_id = (
        _id("OEVdiagnostic"), _id("OEVstale"), _id("ORDforeign"), _id("OEVforeign")
    )
    payload = {
        "_ocr_execution": {
            "job_id": "OCR-diagnostic", "workflow_attempt_id": "attempt-diagnostic",
            "evidence_run_id": diagnostic_id,
            "selected_pdf_receipt": {"order_id": order_id, "document_id": "diagnostic-document"},
        },
        "hakodate_recognition_review": {"count": 1, "cells": [{"sheet_cell": "F11", "raw_text": "1"}]},
        "hakodate_ocr_evidence_records": [{"raw_text": "1"}],
    }
    with session_scope() as session:
        session.add_all([
            OrderOcrEvidenceRun(
                id=diagnostic_id, order_id=order_id, template_version_id=template_version_id,
                schema_version="workflow-v2-test", producer_version="hakodate_best_method_pipeline",
                source="test", status="diagnostic_only", payload_json=payload,
                artifact_manifest_json={"records": "exact-records"}, artifact_digest="diagnostic-digest",
                capabilities_json={}, degraded_reasons_json=[], created_at=datetime.utcnow(),
            ),
            OrderOcrEvidenceRun(
                id=stale_id, order_id=order_id, template_version_id=None,
                schema_version="workflow-v2-test", producer_version="test", source="test", status="ready",
                payload_json={"stale": True}, artifact_manifest_json={}, artifact_digest="stale-digest",
                capabilities_json={}, degraded_reasons_json=[], created_at=datetime.utcnow(),
            ),
            Order(
                id=foreign_order_id, facility_code="FAC_TEST", week_code="2026-04-26", status="要確認",
                document_uri=f"file:///{foreign_order_id}.pdf", message_id=f"msg-{foreign_order_id}",
                received_at=datetime.utcnow(),
            ),
            OrderOcrEvidenceRun(
                id=foreign_id, order_id=foreign_order_id, template_version_id=template_version_id,
                schema_version="workflow-v2-test", producer_version="test", source="test", status="diagnostic_only",
                payload_json={"foreign": True}, artifact_manifest_json={}, artifact_digest="foreign-digest",
                capabilities_json={}, degraded_reasons_json=[], created_at=datetime.utcnow(),
            ),
        ])

    root = f"/orders/{order_id}/workflow-v2/ocr-results"
    with registered_hospital_operator(monkeypatch) as operator:
        client = TestClient(app)
        assert client.get(f"{root}/{diagnostic_id}").status_code == 401
        before = database_snapshot()
        response = client.get(f"{root}/{diagnostic_id}", headers=operator.headers)
        assert response.status_code == 200, response.text
        assert response.json()["id"] == diagnostic_id
        assert response.json()["order_id"] == order_id
        assert response.json()["template_version_id"] == template_version_id
        assert response.json()["status"] == "diagnostic_only"
        assert response.json()["payload_json"] == payload
        for refused_id in (stale_id, foreign_id, "missing"):
            refused = client.get(f"{root}/{refused_id}", headers=operator.headers)
            assert refused.status_code == 404, refused.text
        assert database_snapshot() == before

    workflow, error = order_workflow_v2_service.get_workflow(order_id)
    assert error is None
    assert workflow["selected_ocr_result_id"] == selected_evidence_id


def test_get_workflow_does_not_refresh_prerequisite_state(monkeypatch, authored_april_context) -> None:
    order_id, _, _ = _create_order_with_evidence()
    context, context_error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        expected_revision=0,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    assert context_error is None
    assert context["state"] == "context_confirmed"
    monkeypatch.setattr(
        order_workflow_v2_service,
        "_hakodate_weekly_menu_base_sheet_error",
        lambda _order_id: "menu_entries_missing",
    )

    workflow, error = order_workflow_v2_service.get_workflow(order_id)

    assert error is None
    assert workflow["state"] == "context_confirmed"
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert row.state == "context_confirmed"
        assert row.blockers_json == []


def test_ensure_ocr_prerequisites_blocks_when_weekly_menu_base_sheet_missing(monkeypatch, authored_april_context) -> None:
    order_id, _, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        expected_revision=0,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
    )
    monkeypatch.setattr(
        order_workflow_v2_service,
        "_hakodate_weekly_menu_base_sheet_error",
        lambda _session, _order: "monthly_menu_object_missing",
    )

    workflow, error = order_workflow_v2_service.ensure_ocr_prerequisites(
        order_id,
        expected_revision=_current_workflow_revision(order_id),
    )

    assert error == "monthly_menu_object_missing"
    assert workflow is not None
    assert workflow["state"] == "ocr_blocked"
    assert workflow["blockers"] == ["monthly_menu_object_missing"]


def test_select_ocr_result_requires_confirmed_context(authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()

    workflow, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))

    assert workflow is None
    assert error == "context_not_confirmed"
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert row is None or row.evidence_run_id is None


def test_get_workflow_does_not_recover_missing_context_from_ingest_context(authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    template_version_id = _install_active_template_version("FAC00001", "template-fac00001")
    draft_id = _id("ODS")
    snapshot_id = _id("OCS")
    with session_scope() as session:
        order = session.get(Order, order_id)
        order.facility_code = "FAC00001"
        order.week_code = "2026-04@2026-04-26~2026-04-30"
        evidence = session.get(OrderOcrEvidenceRun, evidence_id_1)
        evidence.template_version_id = template_version_id
        session.add(
            OrderSheetDraft(
                id=draft_id,
                order_id=order_id,
                template_version_id=template_version_id,
                base_evidence_run_id=evidence_id_1,
                draft_sheet_json={
                    "facility_id": "FAC00001",
                    "week_id": "2026-04@2026-04-26~2026-04-30",
                    "fields": ["date", "daypart", "menu", "qty.regular"],
                    "rows": [["04/26", "朝", "A", "1"]],
                },
                draft_state="saved",
                edited_by="test",
                edited_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        session.flush()
        session.add(
            OrderConfirmedSnapshot(
                id=snapshot_id,
                order_id=order_id,
                template_version_id=template_version_id,
                draft_id=draft_id,
                snapshot_digest="digest",
                snapshot_json={"draft_id": draft_id},
                confirmed_by="test",
                confirmed_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        session.flush()
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                template_version_id=template_version_id,
                evidence_run_id=evidence_id_1,
                draft_id=draft_id,
                confirmed_snapshot_id=snapshot_id,
                state="review_required",
                headline="legacy workflow without context",
                primary_action="review",
                secondary_actions_json={order_workflow_v2_service.WORKFLOW_V2_META_KEY: {"template_version_id": template_version_id}},
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )

    before = database_snapshot()
    workflow, error = order_workflow_v2_service.get_workflow(order_id)

    assert error is None
    # Retired: reconstructing confirmed context from Order on a GET.
    assert workflow["state"] == "context_not_confirmed"
    assert workflow["facility_id"] is None
    assert workflow["week_start"] is None
    assert workflow["week_end"] is None
    assert "context_not_confirmed" in workflow["blockers"]
    assert database_snapshot() == before
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        meta = row.secondary_actions_json[order_workflow_v2_service.WORKFLOW_V2_META_KEY]
        assert "facility_id" not in meta
        assert "week_start" not in meta
        assert "week_end" not in meta


def test_context_confirm_blocks_when_facility_template_unresolved(monkeypatch, tmp_path) -> None:
    order_id, _, _ = _create_order_with_evidence()
    configured = (lambda facility_id: {"facility_id": facility_id, "fax_template": {"columns": []}})("FAC_TEMPLATELESS")
    source = _write_authored_context(tmp_path, monkeypatch, configured)
    original_source = source.read_bytes()
    before = database_snapshot()


    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC_TEMPLATELESS",
        week_start="2026-04-26",
        week_end="2026-04-30",
        expected_revision=_current_workflow_revision(order_id),
    )

    assert workflow is None
    assert error == "facility_version_snapshot_incomplete"
    assert database_snapshot() == before
    assert source.read_bytes() == original_source


def test_ocr_run_blocks_legacy_context_without_active_template_version(monkeypatch, authored_april_context) -> None:
    order_id, _, _ = _create_order_with_evidence()
    facility_id = _id("FAC_NO_ACTIVE")
    job_id = _id("OCRnoactive")
    monkeypatch.setattr(
        order_workflow_v2_service.config_service,
        "get_facility_config",
        _registered_template_config,
    )
    with session_scope() as session:
        order = session.get(Order, order_id)
        assert order is not None
        order.facility_code = facility_id
        order.week_code = "2026-04@2026-04-26~2026-04-30"
        session.add(OcrJob(id=job_id, order_id=order_id, status="queued", input_reference="file:///input.pdf"))
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                state="context_confirmed",
                headline="施設・週次・テンプレートが確定しました",
                primary_action="run_ocr",
                secondary_actions_json={
                    order_workflow_v2_service.WORKFLOW_V2_META_KEY: {
                        "facility_id": facility_id,
                        "week_start": "2026-04-26",
                        "week_end": "2026-04-30",
                        "week_code": "2026-04@2026-04-26~2026-04-30",
                        "template_id": "fax_layout_regular_forbidden_v1",
                    }
                },
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )

    workflow, error = order_workflow_v2_service.mark_ocr_run_queued(
        order_id,
        job_id,
        input_reference="file:///input.pdf", request_mode="ocr_rerun",
        expected_revision=_current_workflow_revision(order_id),
    )

    assert workflow is not None
    assert error == "template_version_required"
    assert workflow["state"] == "template_version_required"
    assert workflow["blockers"] == ["template_version_required"]
    with session_scope() as session:
        versions = session.query(FacilityTemplateVersion).filter(FacilityTemplateVersion.facility_id == facility_id).all()
        assert versions == []
        assert session.get(OcrJob, job_id).template_version_id is None


def test_context_confirm_uses_registered_facility_template(monkeypatch, tmp_path, record_property) -> None:
    order_id, _, _ = _create_order_with_evidence()
    facility_id = _id("FACREG")
    _install_active_template_version(facility_id, "fax_layout_regular_forbidden_v1")
    configured = (lambda facility_id: {
            "facility_id": facility_id,
            "fax_template_id": "fax_layout_regular_forbidden_v1",
            "fax_template": {
                "template_id": "fax_layout_regular_forbidden_v1",
                "columns": [
                    {"index": 0, "role": "date", "header": "日付", "source_index": 0},
                    {"index": 1, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X", "source_index": 3},
                ],
            },
        })(facility_id)
    source = _write_authored_context(tmp_path, monkeypatch, configured)
    original_source = source.read_bytes()
    before = database_snapshot()


    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        expected_revision=0,
        facility_id=facility_id,
        week_start="2026-04-26",
        week_end="2026-04-30",
    )

    assert error is None
    assert workflow is not None
    assert workflow["state"] == "context_confirmed"
    record_property("registered_context", json.dumps(workflow, ensure_ascii=False, default=str))
    assert workflow["template_id"] == "fax_layout_regular_forbidden_v1"
    assert workflow["template_version_id"]


def test_context_confirm_blocks_template_id_only_without_source_indexes(monkeypatch, tmp_path) -> None:
    order_id, _, _ = _create_order_with_evidence()
    facility_id = _id("FACBAD")
    with session_scope() as session:
        session.add(Facility(id=facility_id, name=f"Template Facility {facility_id}"))
    configured = (lambda facility_id: {
            "facility_id": facility_id,
            "fax_template_id": "fax_layout_regular_forbidden_v1",
            "fax_template": {
                "columns": [
                    {"index": 0, "role": "date", "header": "日付"},
                    {"index": 1, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"},
                ],
            },
        })(facility_id)
    source = _write_authored_context(tmp_path, monkeypatch, configured)
    original_source = source.read_bytes()
    before = database_snapshot()


    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id=facility_id,
        week_start="2026-04-26",
        week_end="2026-04-30",
        expected_revision=_current_workflow_revision(order_id),
    )

    assert workflow is None
    assert error == "facility_version_snapshot_incomplete"
    assert database_snapshot() == before
    assert source.read_bytes() == original_source


def test_template_version_lineage_flows_to_job_evidence_draft_and_snapshot(monkeypatch, tmp_path, april_output_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    job_id = _id("OCRlineage")

    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    template_version_id = workflow["template_version_id"]

    sheet = april_output_context(order_id, {"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]})
    reservation = _queue_owned_april_job(order_id, job_id, tmp_path, monkeypatch)
    queued = reservation["workflow"]
    assert queued["template_version_id"] == template_version_id

    evidence, completed = _complete_owned_april_job(order_id, job_id, reservation, {"tables": [sheet]}, monkeypatch)
    evidence_id_1 = evidence["id"]
    assert completed["state"] == "ocr_completed"

    selected, error = order_workflow_v2_service.select_ocr_result(
        order_id,
        evidence_id_1,
        expected_revision=completed["revision"],
    )
    assert error is None
    assert selected["template_version_id"] == template_version_id

    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet=sheet,
        edited_by="test",
        expected_revision=selected["revision"],
    )
    assert error is None
    bagging, error = order_workflow_v2_service.run_bagging(
        order_id,
        expected_revision=saved["workflow"]["revision"],
    )
    assert error is None
    assert bagging["workflow"]["template_version_id"] == template_version_id
    confirmed_bagging, error = order_workflow_v2_service.confirm_bagging(
        order_id,
        expected_revision=bagging["workflow"]["revision"],
    )
    assert error is None
    confirmed, error = order_workflow_v2_service.final_confirm(
        order_id,
        confirmed_by="tester",
        expected_revision=confirmed_bagging["workflow"]["revision"],
    )
    assert error is None

    with session_scope() as session:
        order = session.get(Order, order_id)
        job = session.get(OcrJob, job_id)
        evidence = session.get(OrderOcrEvidenceRun, evidence_id_1)
        draft = session.get(OrderSheetDraft, saved["saved_sheet"]["saved_sheet_id"])
        snapshot = session.get(OrderConfirmedSnapshot, confirmed["confirmed_snapshot_id"])
        assert order.template_version_id == template_version_id
        assert job.template_version_id == template_version_id
        assert evidence.template_version_id == template_version_id
        assert draft.template_version_id == template_version_id
        assert snapshot.template_version_id == template_version_id


def test_save_sheet_blocks_legacy_selected_evidence_without_template_version(monkeypatch, tmp_path) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    _write_authored_context(tmp_path, monkeypatch, _registered_template_config("FAC00001"))
    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    assert workflow["template_version_id"]
    with session_scope() as session:
        order = session.get(Order, order_id)
        row = session.get(OrderWorkflowState, order_id)
        evidence = session.get(OrderOcrEvidenceRun, evidence_id_1)
        assert order is not None
        assert row is not None
        assert evidence is not None
        meta = order_workflow_v2_service._workflow_meta(row)
        meta["template_version_id"] = None
        row.secondary_actions_json = {"workflow_v2": meta}
        row.template_version_id = None
        row.evidence_run_id = evidence_id_1
        row.state = "ocr_selected"
        row.primary_action = "edit_sheet"
        order.template_version_id = None
        evidence.template_version_id = None

    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu": "A", "qty": "1"}]},
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )

    assert saved["state"] == "template_version_required"
    assert error == "template_version_required"
    with session_scope() as session:
        order = session.get(Order, order_id)
        row = session.get(OrderWorkflowState, order_id)
        evidence = session.get(OrderOcrEvidenceRun, evidence_id_1)
        assert order.template_version_id is None
        assert row.template_version_id is None
        assert row.state == "template_version_required"
        assert row.blockers_json == ["template_version_required"]
        assert evidence.template_version_id is None


def test_select_ocr_result_blocks_template_version_mismatch(monkeypatch, tmp_path) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    _write_authored_context(tmp_path, monkeypatch, _registered_template_config("FAC00001"))
    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    mismatched_version_id = _id("FTVbad")
    with session_scope() as session:
        session.add(
            FacilityTemplateVersion(
                id=mismatched_version_id,
                facility_id="FAC00001",
                version="mismatch",
                status="archived",
                template_id="fax_layout_regular_forbidden_v1",
                source="test",
                columns_json=[],
                cells_json=[],
                template_digest="different-digest",
                validation_json={"errors": [], "warnings": []},
                created_at=datetime.utcnow(),
            )
        )
        session.get(OrderOcrEvidenceRun, evidence_id_1).template_version_id = mismatched_version_id

    before = database_snapshot()
    selected, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))

    assert selected is not None
    assert error == "template_version_mismatch"
    assert selected == workflow
    assert database_snapshot() == before
    assert selected["template_version_id"] == workflow["template_version_id"]


def test_select_ocr_result_blocks_legacy_cache_backfill(monkeypatch, tmp_path) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    _write_authored_context(tmp_path, monkeypatch, _registered_template_config("FAC00001"))
    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    with session_scope() as session:
        evidence = session.get(OrderOcrEvidenceRun, evidence_id_1)
        evidence.template_version_id = workflow["template_version_id"]
        evidence.source = "legacy-cache-backfill"
        evidence.status = "repair_blocked"

    before = database_snapshot()
    selected, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))

    assert selected is not None
    assert error == "legacy_ocr_evidence_not_selectable"
    assert selected == workflow
    assert database_snapshot() == before
    assert selected["selected_ocr_result_id"] is None


def test_ocr_job_order_mismatch_blocks_queue(monkeypatch, authored_april_context, isolated_formal_schema, record_property) -> None:
    from tests.integration.test_workflow_ocr_enqueue_transaction import _database_snapshot

    order_id, evidence_id, _ = _create_order_with_evidence()
    other_order_id, _, _ = _create_order_with_evidence()
    context, context_error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=0,
    )
    assert context_error is None
    assert context["state"] == "context_confirmed"
    _add_ocr_job(order_id, "OCR-job-mismatch", job_order_id=other_order_id)
    with session_scope() as session:
        session.add(OrderSheetDraft(id="owner-guard-draft", order_id=order_id, base_evidence_run_id=evidence_id,
                    draft_sheet_json={"rows": []}, edited_by="component-operator"))
        session.add(OrderWorkflowState(order_id=other_order_id, state="uploaded"))
        session.flush()
        workflow = session.get(OrderWorkflowState, order_id)
        workflow.evidence_run_id, workflow.draft_id = evidence_id, "owner-guard-draft"
        session.flush()
        revision = workflow.revision
        input_reference = session.get(Order, order_id).document_uri

    def unexpected(*args, **kwargs):
        pytest.fail("foreign job must stop before prerequisites, capture, or downstream invalidation")

    for name in ("_refresh_ocr_prerequisite_state", "capture_reparse_read_input", "capture_selected_pdf_receipt", "_delete_downstream_after_ocr_change"):
        monkeypatch.setattr(order_workflow_v2_service, name, unexpected)
    before = _database_snapshot(isolated_formal_schema)
    queued, error = order_workflow_v2_service.mark_ocr_run_queued(
        order_id, "OCR-job-mismatch", input_reference=input_reference,
        request_mode="ocr_rerun", expected_revision=revision,
    )
    after = _database_snapshot(isolated_formal_schema)
    record_property("sql_before", json.dumps(before, default=str, sort_keys=True))
    record_property("sql_after", json.dumps(after, default=str, sort_keys=True))

    assert queued is not None
    assert error == "ocr_job_order_mismatch"
    assert queued["state"] == "ocr_job_order_mismatch"
    assert queued["ocr_job"] is None
    assert queued["selected_ocr_result_id"] == evidence_id
    assert queued["saved_sheet_id"] == "owner-guard-draft"
    assert queued["revision"] == revision + 1
    assert queued["blockers"] == ["ocr_job_order_mismatch"]
    for table, rows in before.items():
        if table != "order_workflow_states":
            assert after[table] == rows
            continue
        for previous, current in zip(rows, after[table], strict=True):
            if previous["order_id"] != order_id:
                assert current == previous
            else:
                allowed = {"state", "headline", "primary_action", "blockers_json", "warnings_json", "last_transition_at", "revision"}
                assert {k: v for k, v in current.items() if k not in allowed} == {k: v for k, v in previous.items() if k not in allowed}


def test_workflow_refuses_legacy_meta_without_canonical_template(monkeypatch, authored_april_context) -> None:
    order_id, _, _ = _create_order_with_evidence()
    monkeypatch.setattr(
        order_workflow_v2_service.config_service,
        "get_facility_config",
        lambda facility_id: {
            "facility_id": facility_id,
            "fax_template_id": "fax_layout_floor_2f3f_v1",
        },
    )
    with session_scope() as session:
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                state="context_confirmed",
                headline="legacy context",
                primary_action="run_ocr",
                secondary_actions_json={
                    "workflow_v2": {
                        "facility_id": "FAC_LEGACY",
                        "week_start": "2026-04-26",
                        "week_end": "2026-04-30",
                        "template_id": None,
                    }
                },
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )

    before = database_snapshot()
    workflow, error = order_workflow_v2_service.get_workflow(order_id)

    assert error is None
    assert workflow is not None
    # The legacy config is auxiliary, never a replacement for selected lineage.
    assert workflow["template_id"] is None
    assert workflow["template_source"] is None
    assert workflow["state"] == "template_version_required"
    assert database_snapshot() == before


def test_context_confirm_blocks_facility_columns_without_template_id(monkeypatch, tmp_path) -> None:
    order_id, _, _ = _create_order_with_evidence()
    configured = (lambda facility_id: {
            "facility_id": facility_id,
            "fax_template": {
                "columns": [
                    {"index": 0, "role": "date", "header": "日付"},
                    {"index": 1, "role": "daypart", "header": "区分"},
                    {"index": 2, "role": "menu_name", "header": "メニュー"},
                    {
                        "index": 3,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "header": "常食2F",
                    },
                    {
                        "index": 4,
                        "role": "quantity",
                        "diet_type": "regular",
                        "area_id": "3F",
                        "header": "常食3F",
                    },
                    {"index": 5, "role": "note", "header": "備考"},
                ],
            },
        })("FAC_FLOOR")
    source = _write_authored_context(tmp_path, monkeypatch, configured)
    original_source = source.read_bytes()
    before = database_snapshot()


    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC_FLOOR",
        week_start="2026-04-26",
        week_end="2026-04-30",
        expected_revision=_current_workflow_revision(order_id),
    )

    assert workflow is None
    assert error == "facility_version_snapshot_incomplete"
    assert database_snapshot() == before
    assert source.read_bytes() == original_source


def test_context_confirm_normalizes_legacy_non_dict_workflow_meta(authored_april_context) -> None:
    order_id, _, _ = _create_order_with_evidence()
    with session_scope() as session:
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                state="uploaded",
                headline="legacy",
                primary_action="confirm_context",
                secondary_actions_json="confirm_context",
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )

    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )

    assert error is None
    assert workflow is not None
    assert workflow["state"] == "context_confirmed"
    assert workflow["template_id"] == "template-fac00001"
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert isinstance(row.secondary_actions_json, dict)
        assert row.secondary_actions_json["workflow_v2"]["facility_id"] == "FAC00001"


def test_mark_ocr_run_queued_requires_context_and_preserves_operator_work(monkeypatch, authored_april_context, tmp_path, record_property) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    # Transport intention only; no dispatcher or external provider is invoked.
    for key, value in {
        "PROJECT_ID": "synthetic-owned-ocr-unit", "LOCATION": "asia-northeast2",
        "QUEUE": "synthetic-owned-ocr-unit", "TARGET_URL": "https://reserved.invalid",
        "SERVICE_ACCOUNT_EMAIL": "unit-worker@synthetic-owned-ocr-unit.iam.gserviceaccount.com",
        "AUDIENCE": "https://reserved.invalid", "DISPATCH_DEADLINE_SECONDS": "300",
    }.items():
        monkeypatch.setenv("HOSPITAL_WORKFLOW_OCR_DELIVERY_" + key, value)

    queued, error = order_workflow_v2_service.mark_ocr_run_queued(order_id, "OCR-job", input_reference="file:///input.pdf", request_mode="ocr_rerun", expected_revision=_current_workflow_revision(order_id))
    # Bootstrap creates revision 1; the original uninitialized revision 0 cannot enqueue.
    assert error == "workflow_revision_conflict"
    with session_scope() as session:
        assert session.get(OcrJob, "OCR-job") is None
        assert session.get(OrderOcrEvidenceRun, evidence_id_1) is not None
    before_refusal = database_snapshot()
    queued, error = order_workflow_v2_service.mark_ocr_run_queued(
        order_id, "OCR-job", input_reference="file:///input.pdf", request_mode="ocr_rerun",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert database_snapshot() == before_refusal
    initial_queued, initial_error = queued, error
    record_property("initial_queue", json.dumps({"error": initial_error, "workflow": initial_queued}, default=str))

    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu": "A", "qty": "1"}]},
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    saved_sheet_id = saved["saved_sheet"]["saved_sheet_id"]
    with session_scope() as session:
        session.add(
            OrderCurrentState(
                order_id=order_id,
                draft_id=saved_sheet_id,
                evidence_run_id=evidence_id_1,
                snapshot_version="v1",
                state_json={"source": "test-current-state"},
                updated_at=datetime.utcnow(),
            )
        )

    input_reference = _attach_context_pdf(order_id, tmp_path)
    before_rerun = database_snapshot()
    queued, error = order_workflow_v2_service.mark_ocr_run_queued(
        order_id, "OCR-job", input_reference=input_reference, request_mode="ocr_rerun",
        expected_revision=_current_workflow_revision(order_id),
    )

    assert error is None
    queued = queued["workflow"]
    assert queued["state"] == "ocr_running"
    assert queued["selected_ocr_result_id"] == evidence_id_1
    assert queued["saved_sheet_id"] == saved_sheet_id
    with session_scope() as session:
        assert session.get(OrderSheetDraft, saved_sheet_id) is not None
        current_state = session.get(OrderCurrentState, order_id)
        assert current_state is not None
        assert current_state.draft_id == saved_sheet_id
        assert current_state.evidence_run_id == evidence_id_1
    after_rerun = database_snapshot()
    for table in ("order_sheet_drafts", "order_current_states", "order_ocr_evidence_runs"):
        assert after_rerun[table] == before_rerun[table], table

    assert initial_error == "context_not_confirmed"
    assert initial_queued is None


def test_mark_ocr_run_queued_blocks_when_weekly_menu_is_missing(monkeypatch, authored_april_context) -> None:
    order_id, _, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    monkeypatch.setattr(order_service, "_build_hakodate_weekly_menu_base_sheet",
                        lambda _order_id, *, processing_read_input: (None, "menu_entries_missing"))

    queued, error = order_workflow_v2_service.mark_ocr_run_queued(order_id, "OCR-job", input_reference="file:///input.pdf", request_mode="ocr_rerun", expected_revision=_current_workflow_revision(order_id))

    assert queued is not None
    assert error == "menu_entries_missing"
    assert queued["state"] == "ocr_blocked"
    assert queued["blockers"] == ["menu_entries_missing"]
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert row.state == "ocr_blocked"
        assert row.blockers_json == ["menu_entries_missing"]


def test_workflow_ocr_job_serializes_progress_from_processing_stage(monkeypatch, authored_april_context) -> None:
    order_id, _, _ = _create_order_with_evidence()
    job_id = _id("OCRprogress")

    with session_scope() as session:
        session.add(
            OcrJob(
                id=job_id,
                order_id=order_id,
                status="running",
                input_reference="gs://bucket/input.pdf",
                metrics={
                    "processing_stage": "hakodate_live_pipeline",
                    "result_state": "processing",
                },
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
    # Serializer component only; queue ownership is covered by enqueue contracts.
    with session_scope() as session:
        queued = {"ocr_job": order_workflow_v2_service._serialize_ocr_job(session.get(OcrJob, job_id))}

    assert queued["ocr_job"]["progress_step"] == 3
    assert queued["ocr_job"]["progress_total"] == 6
    assert queued["ocr_job"]["progress_label"] == "位置合わせ/OCR"
    assert queued["ocr_job"]["error_message"] is None
    assert queued["ocr_job"]["error"] is None


def test_mark_ocr_run_completed_preserves_context_and_does_not_select_result(monkeypatch, april_output_context, tmp_path) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    context, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    original_base = april_output_context(order_id, {
        "fields": ["date", "daypart", "menu", "regular"], "rows": [["04/26", "朝", "A", ""]],
    })
    queued = _queue_owned_april_job(order_id, "OCR-job", tmp_path, monkeypatch)
    evidence, completed = _complete_owned_april_job(order_id, "OCR-job", queued,
                                                   {"pipeline_version": "test-pipeline", "tables": [original_base]}, monkeypatch)
    evidence_id_1 = evidence["id"]
    assert error is None
    assert completed["state"] == "ocr_completed"
    assert completed["facility_id"] == "FAC00001"
    assert completed["week_start"] == "2026-04-26"
    assert completed["week_end"] == "2026-04-30"
    assert completed["template_id"] == "template-fac00001"
    assert completed["selected_ocr_result_id"] is None
    assert completed["saved_sheet_id"] is None
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert row.evidence_run_id is None
        assert row.draft_id is None
        assert row.secondary_actions_json["workflow_v2"]["latest_ocr_result_id"] == evidence_id_1


def test_sheet_source_uses_only_selected_ocr_payload(monkeypatch, april_projection) -> None:
    order_id, evidence_id_1, evidence_id_2, _ = april_projection
    with session_scope() as session:
        session.get(OrderOcrEvidenceRun, evidence_id_1).payload_json = {"selected_marker": "wrong"}
        expected_payload = deepcopy(session.get(OrderOcrEvidenceRun, evidence_id_2).payload_json)
    captured = {}
    actual = order_service._build_hakodate_evidence_assignment_from_payload
    def observe(**kwargs):
        captured["payload"] = deepcopy(kwargs["payload"])
        return actual(**kwargs)
    monkeypatch.setattr(order_service, "_build_hakodate_evidence_assignment_from_payload", observe)
    source, error = order_workflow_v2_service.build_sheet_from_selected_ocr(order_id)
    assert error is None, error
    assert captured["payload"] == expected_payload
    assert source["selected_ocr_result_id"] == evidence_id_2
    assert source["sheet"]["rows"][0][source["sheet"]["fields"].index("qty.regular_x")] == "70"


def test_sheet_source_blocks_monthly_menu_missing_before_projection(monkeypatch, april_projection) -> None:
    order_id, _, evidence_id_1, _ = april_projection
    with session_scope() as session:
        session.query(MonthlyMenuEntry).filter_by(monthly_menu_id="2026-04").delete()
    def fail_apply(**kwargs):
        raise AssertionError("OCR quantities must not be projected without canonical monthly menu rows")
    monkeypatch.setattr(order_service, "_apply_hakodate_sheet_output_to_sheet_payload", fail_apply)
    before = database_snapshot()
    source, error = order_workflow_v2_service.build_sheet_from_selected_ocr(order_id)
    assert source is None
    # Real capture refuses the now-missing original menu before any projection.
    assert error == "hakodate_original_expectation_required"
    assert database_snapshot() == before

def test_sheet_source_blocks_when_selected_ocr_has_no_confirmed_context(authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    with session_scope() as session:
        order = session.get(Order, order_id)
        order.facility_code = "FAC00001"
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                evidence_run_id=evidence_id_1,
                state="review_required",
                headline="legacy selected OCR without workflow-v2 context",
                primary_action="review",
                secondary_actions_json=None,
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )

    source, error = order_workflow_v2_service.build_sheet_from_selected_ocr(order_id)

    assert source is None
    assert error == "context_not_confirmed"


def test_sheet_source_requires_fixed_workflow_template_version(monkeypatch, authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    monkeypatch.setattr(
        order_workflow_v2_service.config_service,
        "get_facility_config",
        lambda facility_id: _registered_template_config(facility_id),
    )
    with session_scope() as session:
        evidence = session.get(OrderOcrEvidenceRun, evidence_id_1)
        evidence.template_version_id = None
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                evidence_run_id=evidence_id_1,
                state="ocr_selected",
                headline="selected OCR without fixed template version",
                primary_action="edit_sheet",
                secondary_actions_json={
                    order_workflow_v2_service.WORKFLOW_V2_META_KEY: {
                        "facility_id": "FAC00001",
                        "week_start": "2026-04-26",
                        "week_end": "2026-04-30",
                        "week_code": "2026-04@2026-04-26~2026-04-30",
                        "template_id": "template-fac00001",
                    }
                },
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )

    source, error = order_workflow_v2_service.build_sheet_from_selected_ocr(order_id)

    assert source is None
    assert error == "template_version_required"
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert row.state == "ocr_selected"
        assert row.template_version_id is None
        assert row.blockers_json == []


def test_stale_saved_sheet_requires_explicit_reselection_before_selected_ocr_rebuild(
    april_projection, monkeypatch, record_property,
) -> None:
    import os
    from pathlib import Path
    from fastapi.testclient import TestClient
    from src.main import app
    from tests.contract.auth_support import registered_hospital_operator

    order_id, _, evidence_id_1, template_version_id = april_projection
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"fields": ["date", "menu_name", "qty.regular_x"], "rows": [["04/26", "A", "1"]]},
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    assert template_version_id

    with session_scope() as session:
        draft = session.get(OrderSheetDraft, saved["saved_sheet"]["saved_sheet_id"])
        assert draft is not None
        stale_id = _id("FTVstale")
        session.add(FacilityTemplateVersion(
            id=stale_id, facility_id="FAC00001", version="intentionally-stale", status="invalid",
            template_id="template-fac00001", columns_json=_template_columns(), template_digest=stale_id,
        ))
        session.flush()
        draft.template_version_id = stale_id
        row = session.get(OrderWorkflowState, order_id)
        assert row is not None
        row.state = "sheet_saved"
        row.primary_action = "run_bagging"

    workflow, error = order_workflow_v2_service.get_workflow(order_id)
    assert error is None
    assert workflow["state"] == "saved_sheet_template_mismatch"
    assert workflow["primary_action"] == "edit_sheet"
    assert workflow["selected_ocr_result_id"] == evidence_id_1
    assert workflow["saved_sheet_id"] == saved["saved_sheet"]["saved_sheet_id"]

    before = database_snapshot()
    source, error = order_workflow_v2_service.build_sheet_from_selected_ocr(order_id)

    record_property("rebuild_response", json.dumps({"error": error, "source": source}, default=str))
    assert database_snapshot() == before
    assert source is None
    assert error == "synchronous_input_missing"

    source_path = Path(os.environ["FACILITY_MASTER_PATH"])
    source_bytes = source_path.read_bytes()
    with registered_hospital_operator(monkeypatch) as actor:
        client = TestClient(app)
        try:
            root = f"/orders/{order_id}/workflow-v2"
            before = database_snapshot()
            refused = client.get(root + "/sheet-source", headers=actor.headers)
            assert refused.status_code == 400
            assert refused.json() == {"detail": "synchronous_input_missing"}
            assert database_snapshot() == before

            selected = client.post(
                root + f"/ocr-results/{evidence_id_1}/select",
                headers={**actor.headers, "If-Match": str(workflow["revision"])},
            )
            assert selected.status_code == 200, selected.text
            current = selected.json()
            assert current["selected_ocr_result_id"] == evidence_id_1
            assert current["saved_sheet_id"] is None
            assert current["confirmed_snapshot_id"] is None
            assert current["revision"] > workflow["revision"]
            after = database_snapshot()
            invalidated = {"order_lines", "bags", "label_rows", "delivery_notes",
                           "order_confirmed_snapshots", "order_bagging_results", "order_output_bundles"}
            for table, rows in before.items():
                if table in invalidated:
                    assert after[table] == [row for row in rows if row["order_id"] != order_id], table
                elif table == "order_current_states":
                    for old in rows:
                        new = next(row for row in after[table] if row["order_id"] == old["order_id"])
                        if old["order_id"] == order_id:
                            assert {key for key in old if old[key] != new[key]} <= {
                                "draft_id", "evidence_run_id", "template_version_id"}
                            assert all(new[key] is None for key in
                                       ("draft_id", "evidence_run_id", "template_version_id"))
                        else:
                            assert new == old
                elif table != "order_workflow_states":
                    assert after[table] == rows, table
            stale = client.post(
                root + f"/ocr-results/{evidence_id_1}/select",
                headers={**actor.headers, "If-Match": str(workflow["revision"])},
            )
            assert stale.status_code == 409, stale.text
            assert database_snapshot() == after
            original_row = next(row for row in before["order_workflow_states"] if row["order_id"] == order_id)
            current_row = next(row for row in after["order_workflow_states"] if row["order_id"] == order_id)
            changed = {key for key in original_row if original_row[key] != current_row[key]}
            assert changed <= {"revision", "draft_id", "confirmed_snapshot_id", "state", "headline",
                               "primary_action", "secondary_actions_json", "blockers_json",
                               "warnings_json", "last_transition_at"}
            assert current_row["draft_id"] is None
            for row in before["order_workflow_states"]:
                if row["order_id"] != order_id:
                    assert row in after["order_workflow_states"]

            recovered = client.get(root + "/sheet-source", headers=actor.headers)
            assert recovered.status_code == 200, recovered.text
            source, error = order_workflow_v2_service.build_sheet_from_selected_ocr(order_id)
            assert error is None, error
            assert source == recovered.json()
            assert source["selected_ocr_result_id"] == evidence_id_1
            assert source["sheet"]["template_version_id"] == template_version_id
            assert source["sheet"]["rows"][0][source["sheet"]["fields"].index("qty.regular_x")] == "70"
            assert database_snapshot() == after
            record_property("explicit_reselection_recovery", json.dumps({
                "direct_status": refused.status_code, "direct_error": refused.json(),
                "select_status": selected.status_code, "source_status": recovered.status_code,
                "selected_ocr_result_id": evidence_id_1,
                "revision_before": workflow["revision"], "revision_after": current["revision"],
                "changed_workflow_columns": sorted(changed),
                "all_other_tables_unchanged": True, "source_read_nonmutation": True,
                "preserved_draft": next(row for row in after["order_sheet_drafts"]
                                        if row["id"] == saved["saved_sheet"]["saved_sheet_id"]),
            }, default=str))
        finally:
            client.close()
    assert source_path.read_bytes() == source_bytes


def test_save_sheet_blocks_when_selected_ocr_has_no_confirmed_context(authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    with session_scope() as session:
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                evidence_run_id=evidence_id_1,
                state="review_required",
                headline="legacy selected OCR without workflow-v2 context",
                primary_action="review",
                secondary_actions_json=None,
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )

    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [["04/26", "朝", "大豆のトマト煮", "1"]]},
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )

    assert saved["state"] == "context_not_confirmed"
    assert error == "context_not_confirmed"


def test_expanded_cell_copy_mode_override_is_passed_to_sheet_projection(monkeypatch, april_projection) -> None:
    order_id, _, evidence_id_1, _ = april_projection
    changed, error = order_workflow_v2_service.set_expanded_cell_copy_mode(
        order_id, "enabled", expected_revision=_current_workflow_revision(order_id))
    assert error is None
    assert changed["expanded_cell_copy_mode"] == "enabled"
    captured = {}
    actual = order_service._apply_hakodate_sheet_output_to_sheet_payload
    def observe(**kwargs):
        captured["facility_config"] = deepcopy(kwargs["facility_config"])
        return actual(**kwargs)
    monkeypatch.setattr(order_service, "_apply_hakodate_sheet_output_to_sheet_payload", observe)
    source, error = order_workflow_v2_service.build_sheet_from_selected_ocr(order_id)
    assert error is None, error
    assert source["sheet"]["expanded_cell_copy_mode"] == "enabled"
    assert captured["facility_config"]["expanded_cell_same_daypart_copy_enabled"] is True

def test_facility_template_columns_save_is_disabled_and_keeps_downstream(monkeypatch, tmp_path) -> None:
    order_id, evidence_id_1, evidence_id_2 = _create_order_with_evidence()
    _install_active_template_version("FAC00016", "template-fac00016")
    _write_authored_context(tmp_path, monkeypatch, {
        "facility_id": "FAC00016", "fax_template_id": "template-fac00016",
        "fax_template": {"template_id": "template-fac00016", "columns": _template_columns()},
    })
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00016",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00016",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1, evidence_id_2)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu": "A", "qty": "1"}]},
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    saved_sheet_id = saved["saved_sheet"]["saved_sheet_id"]

    columns = [
        {"index": 0, "role": "date", "header": "日付", "format": "MM/DD", "source_index": 0},
        {"index": 1, "role": "daypart", "header": "区分", "source_index": 1},
        {"index": 2, "role": "menu_name", "header": "メニュー", "source_index": 3},
        {"index": 3, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X", "source_index": 4},
        {"index": 4, "role": "note", "header": "備考欄", "source_index": 5},
    ]

    before = database_snapshot()

    result, error = order_workflow_v2_service.save_facility_template_columns(order_id, columns, expected_revision=_current_workflow_revision(order_id))

    assert database_snapshot() == before
    assert result == {"error": "legacy_facility_template_column_override_disabled"}
    assert error == "legacy_facility_template_column_override_disabled"
    with session_scope() as session:
        assert session.get(OrderOcrEvidenceRun, evidence_id_1) is not None
        assert session.get(OrderOcrEvidenceRun, evidence_id_2) is not None
        assert session.get(OrderSheetDraft, saved_sheet_id) is not None


def test_disabled_facility_columns_preserve_confirmed_snapshot_reference(monkeypatch, tmp_path) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    _install_active_template_version("FAC00016", "template-fac00016")
    _write_authored_context(tmp_path, monkeypatch, {
        "facility_id": "FAC00016", "fax_template_id": "template-fac00016",
        "fax_template": {"template_id": "template-fac00016", "columns": _template_columns()},
    })
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00016",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00016",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]},
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    # Stored legacy reference only; the disabled writer must not delete it.
    snapshot_id = _id("OCSlegacy")
    with session_scope() as session:
        draft = session.get(OrderSheetDraft, saved["saved_sheet"]["saved_sheet_id"])
        session.add(OrderConfirmedSnapshot(
            id=snapshot_id, order_id=order_id, draft_id=draft.id, saved_sheet_id=draft.id,
            template_version_id=draft.template_version_id, snapshot_digest="legacy-reference",
            snapshot_json={"saved_sheet": draft.draft_sheet_json},
        ))
        session.flush()
        workflow = session.get(OrderWorkflowState, order_id)
        workflow.state = "confirmed"
        workflow.confirmed_snapshot_id = snapshot_id

    columns = [
        {"index": 0, "role": "date", "header": "日付", "format": "MM/DD", "source_index": 0},
        {"index": 1, "role": "daypart", "header": "区分", "source_index": 1},
        {"index": 2, "role": "menu_name", "header": "メニュー", "source_index": 3},
        {"index": 3, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X", "source_index": 4},
        {"index": 4, "role": "quantity", "header": "不明(-)", "diet_type": "placeholder", "area_id": "X", "source_index": 5},
        {"index": 5, "role": "note", "header": "備考欄", "source_index": 6},
    ]

    before = database_snapshot()
    result, error = order_workflow_v2_service.save_facility_template_columns(order_id, columns, expected_revision=_current_workflow_revision(order_id))

    assert error == "legacy_facility_template_column_override_disabled"
    assert result == {"error": error}
    assert database_snapshot() == before
    with session_scope() as session:
        assert session.get(OrderWorkflowState, order_id).confirmed_snapshot_id == snapshot_id
        assert session.get(OrderConfirmedSnapshot, snapshot_id) is not None


def test_selecting_ocr_result_blocks_template_version_mismatch(authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    assert workflow["template_version_id"]

    before = database_snapshot()
    selected, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))

    assert selected is not None
    assert error == "template_version_mismatch"
    assert selected == workflow
    assert database_snapshot() == before


def test_selecting_ocr_result_clears_active_sheet_but_retains_history(authored_april_context) -> None:
    order_id, evidence_id_1, evidence_id_2 = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1, evidence_id_2)
    selected, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    assert error is None
    assert selected["selected_ocr_result_id"] == evidence_id_1

    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu": "A", "qty": "1"}]},
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    assert saved["workflow"]["state"] == "sheet_saved"
    first_saved_sheet_id = saved["saved_sheet"]["saved_sheet_id"]

    switched, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_2, expected_revision=_current_workflow_revision(order_id))

    assert error is None
    assert switched["selected_ocr_result_id"] == evidence_id_2
    assert switched["saved_sheet_id"] is None
    with session_scope() as session:
        historical = session.get(OrderSheetDraft, first_saved_sheet_id)
        assert historical.base_evidence_run_id == evidence_id_1
        assert historical.draft_sheet_json == {"rows": [{"menu": "A", "qty": "1"}]}
    before = database_snapshot()
    removed, error = order_workflow_v2_service.delete_ocr_result(
        order_id, evidence_id_1, expected_revision=switched["revision"])
    assert removed is None and error == "ocr_result_saved_history_conflict"
    assert database_snapshot() == before


def test_deleting_selected_ocr_with_saved_history_is_refused_without_changes(authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu": "A", "qty": "1"}]},
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    saved_sheet_id = saved["saved_sheet"]["saved_sheet_id"]

    revision = _current_workflow_revision(order_id)
    before = database_snapshot()
    workflow, error = order_workflow_v2_service.delete_ocr_result(order_id, evidence_id_1, expected_revision=revision)

    assert workflow is None
    assert error == "ocr_result_saved_history_conflict"
    assert database_snapshot() == before
    with session_scope() as session:
        assert session.get(OrderOcrEvidenceRun, evidence_id_1) is not None
        draft = session.get(OrderSheetDraft, saved_sheet_id)
        assert draft.base_evidence_run_id == evidence_id_1
        assert draft.draft_sheet_json == {"rows": [{"menu": "A", "qty": "1"}]}


@pytest.mark.parametrize("selected", [False, True])
def test_ocr_with_patch_candidate_only_history_cannot_be_deleted(authored_april_context, selected) -> None:
    order_id, evidence_id, _ = _create_order_with_evidence()
    context, error = order_workflow_v2_service.confirm_context(
        order_id=order_id, facility_id="FAC00001", week_start="2026-04-26", week_end="2026-04-30",
        template_id="template-fac00001", expected_revision=_current_workflow_revision(order_id))
    assert error is None
    _stamp_evidence_with_workflow_template(order_id, evidence_id)
    if selected:
        context, error = order_workflow_v2_service.select_ocr_result(
            order_id, evidence_id, expected_revision=context["revision"])
        assert error is None
    with session_scope() as session:
        assert session.query(OrderSheetDraft.id).filter(OrderSheetDraft.base_evidence_run_id == evidence_id).first() is None
        session.add(OrderSheetPatchCandidate(id=f"OPC{uuid4().hex[:12]}", order_id=order_id,
            base_evidence_run_id=evidence_id, base_draft_id=None, draft_id=None,
            candidate_state="ready", patches_json={"raw": "7"}, proposed_draft_sheet_json={"rows": [["7"]]}))
    before = database_snapshot()
    removed, error = order_workflow_v2_service.delete_ocr_result(
        order_id, evidence_id, expected_revision=context["revision"])
    assert removed is None and error == "ocr_result_saved_history_conflict"
    assert database_snapshot() == before


def test_deleting_unselected_ocr_without_history_preserves_the_active_saved_sheet(authored_april_context) -> None:
    order_id, selected_id, unused_id = _create_order_with_evidence()
    context, error = order_workflow_v2_service.confirm_context(
        order_id=order_id, facility_id="FAC00001", week_start="2026-04-26", week_end="2026-04-30",
        template_id="template-fac00001", expected_revision=_current_workflow_revision(order_id))
    assert error is None
    _stamp_evidence_with_workflow_template(order_id, selected_id, unused_id)
    selected, error = order_workflow_v2_service.select_ocr_result(
        order_id, selected_id, expected_revision=context["revision"])
    assert error is None
    saved, error = order_workflow_v2_service.save_sheet(order_id=order_id,
        sheet={"rows": [{"menu": "A", "qty": "1"}]}, edited_by="test",
        expected_revision=selected["revision"])
    assert error is None
    with session_scope() as session:
        before = deepcopy(session.get(OrderSheetDraft, saved["saved_sheet"]["saved_sheet_id"]).draft_sheet_json)
    removed, error = order_workflow_v2_service.delete_ocr_result(
        order_id, unused_id, expected_revision=saved["workflow"]["revision"])
    assert error is None
    assert removed["selected_ocr_result_id"] == selected_id
    assert removed["saved_sheet_id"] == saved["saved_sheet"]["saved_sheet_id"]
    with session_scope() as session:
        assert session.get(OrderOcrEvidenceRun, unused_id) is None
        assert session.get(OrderOcrEvidenceRun, selected_id) is not None
        assert session.get(OrderSheetDraft, removed["saved_sheet_id"]).draft_sheet_json == before


def test_changing_selected_ocr_deletes_confirmed_snapshot_too(monkeypatch, april_output_context) -> None:
    order_id, evidence_id_1, evidence_id_2 = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1, evidence_id_2)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet=april_output_context(order_id, {"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]}),
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    order_workflow_v2_service.run_bagging(order_id, expected_revision=_current_workflow_revision(order_id))
    order_workflow_v2_service.confirm_bagging(order_id, expected_revision=_current_workflow_revision(order_id))
    order_workflow_v2_service.prepare_output_review(order_id, expected_revision=_current_workflow_revision(order_id))
    confirmed, error = order_workflow_v2_service.final_confirm(order_id, confirmed_by="tester", expected_revision=_current_workflow_revision(order_id))
    assert error is None
    snapshot_id = confirmed["confirmed_snapshot_id"]

    switched, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_2, expected_revision=_current_workflow_revision(order_id))

    assert error is None
    assert switched["selected_ocr_result_id"] == evidence_id_2
    assert switched["saved_sheet_id"] is None
    assert switched["confirmed_snapshot_id"] is None
    with session_scope() as session:
        assert session.get(OrderConfirmedSnapshot, snapshot_id) is None


def test_reconfirming_step1_invalidates_current_selection_without_deleting_saved_history(monkeypatch, april_output_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet=april_output_context(order_id, {"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]}),
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    saved_sheet_id = saved["saved_sheet"]["saved_sheet_id"]
    order_workflow_v2_service.run_bagging(order_id, expected_revision=_current_workflow_revision(order_id))
    order_workflow_v2_service.confirm_bagging(order_id, expected_revision=_current_workflow_revision(order_id))
    order_workflow_v2_service.prepare_output_review(order_id, expected_revision=_current_workflow_revision(order_id))
    confirmed, error = order_workflow_v2_service.final_confirm(order_id, confirmed_by="tester", expected_revision=_current_workflow_revision(order_id))
    assert error is None
    snapshot_id = confirmed["confirmed_snapshot_id"]
    history_tables = {"order_ocr_evidence_runs", "order_sheet_drafts", "order_sheet_patch_candidates",
                      "order_confirmed_snapshots", "order_bagging_results", "order_output_bundles", "order_ocr_revisions"}
    before_reconfirm = database_snapshot()

    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )

    assert error is None
    assert workflow["state"] == "context_confirmed"
    assert workflow["selected_ocr_result_id"] is None
    assert workflow["saved_sheet_id"] is None
    assert workflow["confirmed_snapshot_id"] is None
    after_reconfirm = database_snapshot()
    assert {name: after_reconfirm[name] for name in history_tables} == {
        name: before_reconfirm[name] for name in history_tables
    }
    with session_scope() as session:
        assert session.get(OrderOcrEvidenceRun, evidence_id_1) is not None
        assert session.get(OrderSheetDraft, saved_sheet_id) is not None
        assert session.get(OrderConfirmedSnapshot, snapshot_id) is not None
        assert session.query(OrderLine).filter(OrderLine.order_id == order_id).count() == 0
        assert session.get(Order, order_id).status == "要確認"


def test_sheet_cannot_be_saved_without_selected_ocr(authored_april_context) -> None:
    order_id, _, _ = _create_order_with_evidence()
    context, context_error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert context_error is None
    assert context["state"] == "context_confirmed"

    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": []},
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )

    assert saved is None
    assert error == "selected_ocr_required"


def test_workflow_v2_does_not_use_order_lines_as_sheet_source(authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    context, context_error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert context_error is None
    assert context["state"] == "context_confirmed"
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))

    sheet, error = order_workflow_v2_service.get_saved_sheet(order_id)

    assert sheet is None
    assert error == "saved_sheet_missing"
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        assert workflow is not None
        assert workflow.evidence_run_id == evidence_id_1


def test_inspection_is_read_only_projection_of_current_lineage(authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"rows": [{"menu": "A", "qty": "1"}]},
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    saved_sheet_id = saved["saved_sheet"]["saved_sheet_id"]

    inspection, error = order_workflow_v2_service.get_inspection(order_id)

    assert error is None
    assert inspection["source"] == "workflow_v2_inspection"
    assert inspection["workflow"]["selected_ocr_result_id"] == evidence_id_1
    assert inspection["artifact_lineage"]["selected_ocr_result_id"] == evidence_id_1
    assert inspection["artifact_lineage"]["saved_sheet_id"] == saved_sheet_id
    assert inspection["saved_sheet"]["saved_sheet_id"] == saved_sheet_id
    assert any(item["ocr_result_id"] == evidence_id_1 and item["selected"] for item in inspection["ocr_results"])


def test_bagging_requires_saved_sheet_and_uses_saved_sheet_as_source(monkeypatch, april_output_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))

    missing, error = order_workflow_v2_service.run_bagging(order_id, expected_revision=_current_workflow_revision(order_id))
    assert missing is None
    assert error == "saved_sheet_required"

    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet=april_output_context(order_id, {
            "rows": [
                {"menu_name": "大豆のトマト煮", "regular": "70", "soft": "5"},
                {"menu_name": "胡瓜のサラダ", "regular": ""},
            ]
        }),
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    saved_sheet_id = saved["saved_sheet"]["saved_sheet_id"]

    bagging, error = order_workflow_v2_service.run_bagging(order_id, expected_revision=_current_workflow_revision(order_id))

    assert error is None
    assert bagging["workflow"]["state"] == "output_review"
    assert bagging["bagging_result"]["source_saved_sheet_id"] == saved_sheet_id
    assert bagging["output_bundle"]["source_bagging_result_id"] == bagging["bagging_result"]["bagging_result_id"]
    assert bagging["bagging_result"]["materialization_digest"]
    assert bagging["output_bundle"]["materialization_digest"] == bagging["bagging_result"]["materialization_digest"]
    assert bagging["bagging_result"]["summary"]["total_quantity"] == 75.0
    assert bagging["bagging_result"]["summary"]["quantity_line_count"] == 2
    assert bagging["bagging_result"]["summary"]["bag_row_count"] >= 1
    assert bagging["bagging_result"]["bag_rows"]
    assert "anomaly_review" not in bagging["bagging_result"]
    with session_scope() as session:
        bagging_artifact = session.get(OrderBaggingResult, bagging["bagging_result"]["bagging_result_id"])
        output_artifact = session.get(OrderOutputBundle, bagging["output_bundle"]["output_bundle_id"])
        workflow = session.get(OrderWorkflowState, order_id)
        assert bagging_artifact is not None
        assert output_artifact is not None
        assert bagging_artifact.payload_json["source_saved_sheet_id"] == saved_sheet_id
        assert output_artifact.source_bagging_result_id == bagging_artifact.id
        assert workflow.secondary_actions_json["workflow_v2"]["bagging_result"] is None
        assert workflow.secondary_actions_json["workflow_v2"]["output_bundle"] is None


def test_workflow_v2_sheet_auto_edit_ignores_ocr_values(authored_april_context, tmp_path) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))

    _attach_context_pdf(order_id, tmp_path)
    result, error = order_workflow_v2_service.propose_sheet_auto_edit(
        order_id=order_id,
        sheet={
            "fields": ["date", "daypart", "menu", "qty.regular"],
            "header": ["日付", "区分", "献立", "常食"],
            "rows": [["04/26", "朝", "大豆のトマト煮", "110"]],
            "ocr_numeric_cell_items": [
                {
                    "target_row_index": 0,
                    "target_col_index": 3,
                    "value": "10",
                    "classification": "accepted",
                    "confidence_tier": "high",
                }
            ],
        },
        use_llm=False,
        expected_revision=_current_workflow_revision(order_id),
    )

    assert error is None
    assert result["status"] == "ok"
    assert result["patches"] == []
    assert result["rule_patches"] == []


def test_workflow_v2_sheet_auto_edit_refuses_without_selected_ocr_before_provider(monkeypatch, authored_april_context) -> None:
    order_id, _, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )

    def forbidden_provider(**kwargs):
        pytest.fail("selected-less auto-edit must stop before the provider")
    monkeypatch.setattr(order_workflow_v2_service.workflow_v2_sheet_review_service,
                        "_gemini_json_request", forbidden_provider)
    before = database_snapshot()
    result, error = order_workflow_v2_service.propose_sheet_auto_edit(
        order_id=order_id,
        sheet={
            "fields": ["date", "daypart", "menu", "qty.regular"],
            "header": ["日付", "区分", "献立", "常食"],
            "rows": [["04/26", "朝", "大豆のトマト煮", "110"]],
        },
        use_llm=False,
        expected_revision=_current_workflow_revision(order_id),
    )

    # Retired: auto-edit success without an explicitly selected source.
    assert error == "sheet_auto_edit_start_input_missing"
    assert result is None
    assert database_snapshot() == before


def test_workflow_v2_sheet_auto_edit_llm_payload_excludes_ocr_values(monkeypatch, authored_april_context, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_gemini_json_request(**kwargs):
        captured.update(kwargs)
        return {"patches": []}, {"status": "ok", "model": "fake"}

    monkeypatch.setattr(
        order_workflow_v2_service.workflow_v2_sheet_review_service,
        "_gemini_json_request",
        fake_gemini_json_request,
    )
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))

    _attach_context_pdf(order_id, tmp_path)
    result, error = order_workflow_v2_service.propose_sheet_auto_edit(
        order_id=order_id,
        sheet={
            "fields": ["date", "daypart", "menu", "qty.regular"],
            "header": ["日付", "区分", "献立", "常食"],
            "rows": [["04/26", "朝", "大豆のトマト煮", ""]],
            "ocr_numeric_cell_items": [
                {
                    "target_row_index": 0,
                    "target_col_index": 3,
                    "value": "10",
                    "classification": "accepted",
                    "confidence_tier": "high",
                }
            ],
            "target_cell_map": [
                {
                    "target_cell_id": "cell-r0c3",
                    "sheet_cell": "R0C3",
                    "target_row_index": 0,
                    "target_col_index": 3,
                    "worksheet_row": 11,
                    "worksheet_col": 4,
                    "bbox": [100, 100, 120, 120],
                    "field": "qty.regular",
                    "field_label": "常食",
                }
            ],
        },
        use_llm=True,
        expected_revision=_current_workflow_revision(order_id),
    )

    assert error is None
    assert result["status"] == "ok"
    user_payload = captured["user_payload"]
    assert isinstance(user_payload, dict)
    assert user_payload["ocr_numeric_cell_items"] == []
    assert "ocr_sheet_comparison" not in user_payload
    assert user_payload["target_cell_map"][0]["target_cell_id"] == "cell-r0c3"
    system_prompt = captured["system_prompt"]
    assert isinstance(system_prompt, str)
    assert "100% certainty" in system_prompt
    assert "merely plausible" in system_prompt


def test_workflow_v2_sheet_auto_edit_chunks_target_cells_and_reports_partial_failure(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_gemini_json_request(**kwargs):
        calls.append(kwargs)
        user_payload = kwargs["user_payload"]
        assert isinstance(user_payload, dict)
        assert user_payload["ocr_numeric_cell_items"] == []
        assert "ocr_sheet_comparison" not in user_payload
        target_cells = user_payload["target_cell_map"]
        assert isinstance(target_cells, list)
        computed_context = user_payload["computed_context"]
        assert isinstance(computed_context, dict)
        if computed_context["target_chunk_index"] == 1:
            return None, {"status": "failed", "model": "fake", "error": "bad json"}
        first_cell = target_cells[0]
        assert isinstance(first_cell, dict)
        return {
            "patches": [
                {
                    "row_index": first_cell["target_row_index"],
                    "col_index": first_cell["target_col_index"],
                    "current_value": "1",
                    "suggested_value": "7",
                    "reason": "not_100_percent_match",
                }
            ]
        }, {"status": "ok", "model": "fake"}

    monkeypatch.setenv("WORKFLOW_V2_AUTO_EDIT_TARGET_CHUNK_SIZE", "2")
    monkeypatch.setattr(
        order_workflow_v2_service.workflow_v2_sheet_review_service,
        "_gemini_json_request",
        fake_gemini_json_request,
    )
    target_cell_map = [
        {
            "target_cell_id": f"cell-r0c{col}",
            "sheet_cell": f"R0C{col}",
            "target_row_index": 0,
            "target_col_index": col,
            "worksheet_row": 11,
            "worksheet_col": col + 1,
            "bbox": [100 + col, 100, 120 + col, 120],
            "field": f"qty.{col}",
            "field_label": f"数量{col}",
        }
        for col in range(3, 8)
    ]

    result = order_workflow_v2_service.workflow_v2_sheet_review_service.propose_auto_sheet_edits(
        sheet={
            "fields": ["date", "daypart", "menu", "qty.3", "qty.4", "qty.5", "qty.6", "qty.7"],
            "header": ["日付", "区分", "献立", "数量3", "数量4", "数量5", "数量6", "数量7"],
            "rows": [["04/26", "朝", "大豆のトマト煮", "", "2", "3", "4", "5"]],
            "ocr_numeric_cell_items": [{"target_row_index": 0, "target_col_index": 3, "value": "999"}],
            "target_cell_map": target_cell_map,
        },
        use_llm=True,
    )

    assert len(calls) == 5
    assert calls[0]["response_schema"]["required"] == ["patches"]
    primary_calls = sorted(
        calls[:3],
        key=lambda call: call["user_payload"]["computed_context"]["target_chunk_index"],
    )
    primary_target_ids = [
        [target["target_cell_id"] for target in call["user_payload"]["target_cell_map"]]
        for call in primary_calls
    ]
    assert primary_target_ids == [
        ["cell-r0c3", "cell-r0c4"],
        ["cell-r0c5", "cell-r0c6"],
        ["cell-r0c7"],
    ]
    assert [
        call["user_payload"]["computed_context"]["target_chunk_index"] for call in primary_calls
    ] == [0, 1, 2]
    retry_target_ids = [
        [target["target_cell_id"] for target in call["user_payload"]["target_cell_map"]]
        for call in calls[3:]
    ]
    assert retry_target_ids == [["cell-r0c5"], ["cell-r0c6"]]
    assert [
        call["user_payload"]["computed_context"]["target_chunk_index"] for call in calls[3:]
    ] == [1, 1]
    assert result["llm"]["status"] == "partial_failed"
    assert result["llm"]["failed_chunks"] == 1
    assert result["llm"]["total_chunks"] == 3
    assert result["llm"]["error"] == "bad json"
    assert len(result["patches"]) == 2


@pytest.mark.parametrize("first_value", ["1", "110"])
def test_workflow_v2_sheet_auto_edit_selector_excludes_present_first_value_and_selects_neighbors(
    first_value: str,
) -> None:
    target_cell_map = [
        {
            "target_cell_id": f"cell-r0c{col}",
            "sheet_cell": f"R0C{col}",
            "target_row_index": 0,
            "target_col_index": col,
            "worksheet_row": 11,
            "worksheet_col": col + 1,
            "bbox": [100 + col, 100, 120 + col, 120],
            "field": f"qty.{col}",
            "field_label": f"数量{col}",
        }
        for col in range(3, 8)
    ]

    selected = order_workflow_v2_service.workflow_v2_sheet_review_service._suspect_target_cells_from_presence(
        {
            "fields": ["date", "daypart", "menu", "qty.3", "qty.4", "qty.5", "qty.6", "qty.7"],
            "header": ["日付", "区分", "献立", "数量3", "数量4", "数量5", "数量6", "数量7"],
            "rows": [["04/26", "朝", "大豆のトマト煮", first_value, "2", "3", "4", "5"]],
            "ocr_numeric_cell_items": [{"target_row_index": 0, "target_col_index": 3, "value": "999"}],
            "target_cell_map": target_cell_map,
        }
    )

    assert [target["target_cell_id"] for target in selected] == [
        "cell-r0c4",
        "cell-r0c5",
        "cell-r0c6",
        "cell-r0c7",
    ]


def test_workflow_v2_sheet_anomaly_review_is_separate_from_bagging(monkeypatch, april_output_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet=april_output_context(order_id, {
            "fields": ["date", "daypart", "menu", "regular", "soft"],
            "header": ["日付", "区分", "献立", "常食", "軟菜"],
            "rows": [
                ["04/28", "朝", "A", "110", "5"],
                ["04/28", "朝", "B", "12", "5"],
                ["04/29", "朝", "C", "10", "5"],
                ["04/30", "朝", "D", "11", "5"],
            ],
            "ocr_numeric_cell_items": [
                {
                    "target_row_index": 0,
                    "target_col_index": 3,
                    "value": "10",
                    "classification": "accepted",
                    "confidence_tier": "high",
                }
            ],
        }),
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )

    bagging, error = order_workflow_v2_service.run_bagging(order_id, expected_revision=_current_workflow_revision(order_id))

    assert error is None
    assert "anomaly_review" not in bagging["bagging_result"]

    anomaly, error = order_workflow_v2_service.run_sheet_anomaly_review(order_id, use_llm=False, expected_revision=_current_workflow_revision(order_id))

    assert error is None
    review = anomaly["anomaly_review"]
    assert review["status"] == "ok"
    assert any(item["type"] == "high_outlier" for item in review["warnings"])
    assert not any(item["type"] == "sheet_differs_from_ocr" for item in review["warnings"])
    assert review["source_saved_sheet_id"] == bagging["bagging_result"]["source_saved_sheet_id"]

    inspection, error = order_workflow_v2_service.get_inspection(order_id)

    assert error is None
    assert inspection["artifact_lineage"]["anomaly_review_id"] == review["anomaly_review_id"]
    assert inspection["anomaly_review"]["anomaly_review_id"] == review["anomaly_review_id"]
    assert "anomaly_review" not in inspection["bagging_result"]


def test_workflow_v2_sheet_anomaly_review_accepts_unsaved_sheet_and_persists_check(monkeypatch, authored_april_context) -> None:
    _install_fake_materialization(monkeypatch)
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))

    sheet = {
        "fields": ["date", "daypart", "menu", "regular", "soft"],
        "header": ["日付", "区分", "献立", "常食", "軟菜"],
        "rows": [
            ["04/28", "朝", "A", "110", "5"],
            ["04/28", "朝", "B", "12", "5"],
            ["04/29", "朝", "C", "10", "5"],
            ["04/30", "朝", "D", "11", "5"],
        ],
    }

    anomaly, error = order_workflow_v2_service.run_sheet_anomaly_review(
        order_id,
        sheet=sheet,
        use_llm=False,
        expected_revision=_current_workflow_revision(order_id),
    )

    assert error is None
    review = anomaly["anomaly_review"]
    assert review["source"] == "unsaved_sheet"
    assert review["source_saved_sheet_id"] is None
    assert review["sheet_hash"] == order_workflow_v2_service._stable_json_hash(sheet)
    high_outlier = next(item for item in review["warnings"] if item["type"] == "high_outlier")
    assert high_outlier["suggested_value"] == "11.5"

    inspection, error = order_workflow_v2_service.get_inspection(order_id)

    assert error is None
    assert inspection["saved_sheet"] is None
    assert inspection["anomaly_review"]["anomaly_review_id"] == review["anomaly_review_id"]
    assert inspection["artifact_lineage"]["anomaly_review_id"] == review["anomaly_review_id"]
    assert inspection["pre_save_checks"]["anomaly_review"]["confirmed"] is True
    assert inspection["pre_save_checks"]["anomaly_review"]["sheet_hash"] == review["sheet_hash"]
    assert inspection["pre_save_checks"]["anomaly_review"]["anomaly_review_id"] == review["anomaly_review_id"]


def test_workflow_v2_save_sheet_requires_completed_pre_save_checks(monkeypatch, authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    sheet = {
        "fields": ["date", "daypart", "menu", "regular", "soft"],
        "header": ["日付", "区分", "献立", "常食", "軟菜"],
        "rows": [
            ["04/28", "朝", "A", "110", "5"],
            ["04/28", "朝", "B", "12", "5"],
        ],
    }

    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet=sheet,
        edited_by="test",
        require_pre_save_checks=True,
        expected_revision=_current_workflow_revision(order_id),
    )

    assert saved is None
    assert error == "sheet_pre_save_checks_required"

    anomaly, error = order_workflow_v2_service.run_sheet_anomaly_review(
        order_id,
        sheet=sheet,
        use_llm=False,
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    sheet_review, error = order_workflow_v2_service.confirm_sheet_review(
        order_id=order_id,
        sheet=sheet,
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    sheet_hash = order_workflow_v2_service._stable_json_hash(sheet)
    assert anomaly["pre_save_checks"]["anomaly_review"]["sheet_hash"] == sheet_hash
    assert sheet_review["pre_save_checks"]["sheet_review"]["sheet_hash"] == sheet_hash
    assert anomaly["pre_save_status"] == {
        "sheet_hash": sheet_hash,
        "anomaly_review_confirmed": True,
        "sheet_review_confirmed": False,
        "ready": False,
    }
    assert sheet_review["pre_save_status"] == {
        "sheet_hash": sheet_hash,
        "anomaly_review_confirmed": True,
        "sheet_review_confirmed": True,
        "ready": True,
    }

    status, error = order_workflow_v2_service.get_sheet_pre_save_status(
        order_id=order_id,
        sheet=sheet,
    )
    assert error is None
    assert status["pre_save_status"]["ready"] is True

    changed_sheet = dict(sheet)
    changed_sheet["rows"] = [list(row) for row in sheet["rows"]]
    changed_sheet["rows"][0][3] = "111"
    status, error = order_workflow_v2_service.get_sheet_pre_save_status(
        order_id=order_id,
        sheet=changed_sheet,
    )
    assert error is None
    assert status["pre_save_status"]["ready"] is True
    assert status["pre_save_status"]["anomaly_review_confirmed"] is True
    assert status["pre_save_status"]["sheet_review_confirmed"] is True

    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet=changed_sheet,
        edited_by="test",
        require_pre_save_checks=True,
        expected_revision=_current_workflow_revision(order_id),
    )

    assert error is None
    assert saved["workflow"]["state"] == "sheet_saved"
    assert saved["workflow"]["pre_save_checks"] == {}
    assert saved["pre_save_status"]["ready"] is False


def test_workflow_v2_dismiss_sheet_anomaly_warning_persists_review(monkeypatch, authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    sheet = {
        "fields": ["date", "daypart", "menu", "regular", "soft"],
        "header": ["日付", "区分", "献立", "常食", "軟菜"],
        "rows": [
            ["04/28", "朝", "A", "110", "5"],
            ["04/28", "朝", "B", "12", "5"],
            ["04/29", "朝", "C", "10", "5"],
            ["04/30", "朝", "D", "11", "5"],
        ],
    }
    anomaly, error = order_workflow_v2_service.run_sheet_anomaly_review(
        order_id,
        sheet=sheet,
        use_llm=False,
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    warning = next(item for item in anomaly["anomaly_review"]["warnings"] if item["type"] == "high_outlier")

    dismissed, error = order_workflow_v2_service.dismiss_sheet_anomaly_warning(
        order_id=order_id,
        sheet=sheet,
        warning=warning,
        expected_revision=_current_workflow_revision(order_id),
    )

    assert error is None
    assert dismissed["pre_save_checks"]["anomaly_review"]["confirmed"] is True
    assert dismissed["pre_save_checks"]["anomaly_review"]["sheet_hash"] == order_workflow_v2_service._stable_json_hash(sheet)
    assert dismissed["pre_save_status"]["anomaly_review_confirmed"] is True
    assert dismissed["pre_save_status"]["sheet_review_confirmed"] is False
    assert dismissed["pre_save_status"]["ready"] is False
    assert not any(
        item["row_index"] == warning["row_index"]
        and item["col_index"] == warning["col_index"]
        and item["type"] == warning["type"]
        for item in dismissed["anomaly_review"]["warnings"]
    )
    assert dismissed["anomaly_review"]["summary"]["dismissed_warning_count"] == 1

    inspection, error = order_workflow_v2_service.get_inspection(order_id)

    assert error is None
    assert inspection["anomaly_review"]["anomaly_review_id"] == dismissed["anomaly_review"]["anomaly_review_id"]
    assert inspection["anomaly_review"]["summary"]["dismissed_warning_count"] == 1
    assert not any(
        item["row_index"] == warning["row_index"]
        and item["col_index"] == warning["col_index"]
        and item["type"] == warning["type"]
        for item in inspection["anomaly_review"]["warnings"]
    )


def test_workflow_v2_dismiss_sheet_anomaly_warning_keeps_review_after_sheet_edit(monkeypatch, authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    sheet = {
        "fields": ["date", "daypart", "menu", "regular", "soft"],
        "header": ["日付", "区分", "献立", "常食", "軟菜"],
        "rows": [
            ["04/28", "朝", "A", "110", "5"],
            ["04/28", "朝", "B", "12", "5"],
            ["04/29", "朝", "C", "10", "5"],
            ["04/30", "朝", "D", "11", "5"],
        ],
    }
    anomaly, error = order_workflow_v2_service.run_sheet_anomaly_review(
        order_id,
        sheet=sheet,
        use_llm=False,
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    warning = next(item for item in anomaly["anomaly_review"]["warnings"] if item["type"] == "high_outlier")
    changed_sheet = dict(sheet)
    changed_sheet["rows"] = [list(row) for row in sheet["rows"]]
    changed_sheet["rows"][0][3] = "111"

    dismissed, error = order_workflow_v2_service.dismiss_sheet_anomaly_warning(
        order_id=order_id,
        sheet=changed_sheet,
        warning=warning,
        expected_revision=_current_workflow_revision(order_id),
    )

    assert error is None
    assert dismissed["pre_save_checks"]["anomaly_review"]["confirmed"] is True
    assert dismissed["pre_save_status"]["anomaly_review_confirmed"] is True
    assert dismissed["pre_save_status"]["ready"] is False
    assert not any(
        item["row_index"] == warning["row_index"]
        and item["col_index"] == warning["col_index"]
        and item["type"] == warning["type"]
        for item in dismissed["anomaly_review"]["warnings"]
    )


def test_workflow_v2_dismiss_sheet_anomaly_warning_matches_uncelled_llm_warning(monkeypatch, authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    sheet = {
        "fields": ["date", "daypart", "menu", "regular"],
        "header": ["日付", "区分", "献立", "常食"],
        "rows": [["04/28", "朝", "A", "24"]],
    }
    sheet_hash = order_workflow_v2_service._stable_json_hash(sheet)
    warning = {
        "type": "llm_anomaly",
        "severity": "medium",
        "row_index": None,
        "col_index": None,
        "field": None,
        "label": None,
        "value": "",
        "suggested_value": "25.0",
        "message": "Inconsistent quantity.",
        "date": None,
        "daypart": None,
        "menu": None,
        "context_label": None,
        "evidence": {},
    }
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        assert workflow is not None
        meta = dict(workflow.secondary_actions_json.get(order_workflow_v2_service.WORKFLOW_V2_META_KEY) or {})
        meta["anomaly_review"] = {
            "anomaly_review_id": "OAR-test-uncelled",
            "source": "unsaved_sheet",
            "sheet_hash": sheet_hash,
            "warnings": [dict(warning)],
            "summary": {"warning_count": 1},
        }
        meta["pre_save_checks"] = {
            "anomaly_review": {
                "confirmed": True,
                "sheet_hash": sheet_hash,
                "anomaly_review_id": "OAR-test-uncelled",
            },
        }
        workflow.secondary_actions_json = {
            **dict(workflow.secondary_actions_json or {}),
            order_workflow_v2_service.WORKFLOW_V2_META_KEY: meta,
        }

    dismissed, error = order_workflow_v2_service.dismiss_sheet_anomaly_warning(
        order_id=order_id,
        sheet=sheet,
        warning=warning,
        expected_revision=_current_workflow_revision(order_id),
    )

    assert error is None
    assert dismissed["anomaly_review"]["warnings"] == []
    assert dismissed["pre_save_checks"]["anomaly_review"]["confirmed"] is True
    assert dismissed["pre_save_status"]["anomaly_review_confirmed"] is True


def test_workflow_v2_sheet_anomaly_review_llm_payload_uses_sheet_only(monkeypatch, authored_april_context) -> None:
    captured: dict[str, object] = {}

    def fake_gemini_json_request(**kwargs):
        captured.update(kwargs)
        return {"warnings": []}, {"status": "ok", "model": "fake"}

    monkeypatch.setattr(
        order_workflow_v2_service.workflow_v2_sheet_review_service,
        "_gemini_json_request",
        fake_gemini_json_request,
    )
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))

    anomaly, error = order_workflow_v2_service.run_sheet_anomaly_review(
        order_id,
        sheet={
            "fields": ["date", "daypart", "menu", "regular", "soft"],
            "header": ["日付", "区分", "献立", "常食", "軟菜"],
            "rows": [
                ["04/28", "朝", "A", "110", "5"],
                ["04/28", "朝", "B", "12", "5"],
                ["04/29", "朝", "C", "10", "5"],
            ],
            "ocr_numeric_cell_items": [
                {
                    "target_row_index": 0,
                    "target_col_index": 3,
                    "value": "10",
                    "classification": "accepted",
                    "confidence_tier": "high",
                }
            ],
        },
        use_llm=True,
        expected_revision=_current_workflow_revision(order_id),
    )

    assert error is None
    assert anomaly["anomaly_review"]["status"] == "ok"
    user_payload = captured["user_payload"]
    assert isinstance(user_payload, dict)
    assert user_payload["ocr_numeric_cell_items"] == []
    assert "ocr_sheet_comparison" not in user_payload
    assert "materialization_candidate" not in user_payload
    assert "bagging_summary" not in user_payload
    assert user_payload["rows"][0]["quantities"][0]["value"] == "110"


def test_workflow_v2_sheet_anomaly_review_always_returns_suggestions(authored_april_context) -> None:
    order_id, _, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )

    anomaly, error = order_workflow_v2_service.run_sheet_anomaly_review(
        order_id,
        sheet={
            "fields": ["date", "daypart", "menu", "regular", "soft"],
            "header": ["日付", "区分", "献立", "常食", "軟菜"],
            "rows": [
                ["04/26", "朝", "A", "10", "5"],
                ["04/27", "朝", "B", "11", "5"],
                ["04/28", "朝", "C", "110", "5"],
                ["04/29", "朝", "D", "12", "5"],
            ],
        },
        use_llm=False,
        expected_revision=_current_workflow_revision(order_id),
    )

    assert error is None
    warnings = anomaly["anomaly_review"]["warnings"]
    assert warnings
    assert all(str(item.get("suggested_value") or "").strip() for item in warnings)


def test_bagging_blocks_when_saved_sheet_cannot_materialize(monkeypatch, authored_april_context, tmp_path) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"test_materialization_error": True, "rows": [{"menu_name": "A", "regular": "1"}]},
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    assert saved is not None

    _attach_context_pdf(order_id, tmp_path)
    def reject_materialization(_order_id, *, draft_record, **kwargs):
        assert draft_record["draft_sheet_json"]["test_materialization_error"] is True
        return {"error": "test_materialization_error", "lines": [], "line_count": 0}
    monkeypatch.setattr(order_service, "_build_materialization_candidate_from_draft_record", reject_materialization)
    before = database_snapshot()
    bagging, error = order_workflow_v2_service.run_bagging(order_id, expected_revision=_current_workflow_revision(order_id))

    assert database_snapshot() == before
    assert bagging is None
    assert error == "test_materialization_error"


def test_step5_confirm_requires_output_review_and_writes_confirmed_snapshot(monkeypatch, april_output_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet=april_output_context(order_id, {"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]}),
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    blocked, error = order_workflow_v2_service.final_confirm(order_id, confirmed_by="tester", expected_revision=_current_workflow_revision(order_id))
    assert blocked is None
    assert error == "output_review_required"

    bagging, error = order_workflow_v2_service.run_bagging(order_id, expected_revision=_current_workflow_revision(order_id))
    assert error is None
    bagging_result_id = bagging["bagging_result"]["bagging_result_id"]
    assert bagging["workflow"]["state"] == "output_review"
    assert bagging["output_bundle"]["source_bagging_result_id"] == bagging_result_id

    confirmed, error = order_workflow_v2_service.final_confirm(order_id, confirmed_by="tester", expected_revision=_current_workflow_revision(order_id))

    assert error is None
    assert confirmed["workflow"]["state"] == "confirmed"
    assert confirmed["confirmed_snapshot_id"]
    with session_scope() as session:
        snapshot = session.get(OrderConfirmedSnapshot, confirmed["confirmed_snapshot_id"])
        assert snapshot is not None
        assert snapshot.snapshot_json["source"] == "workflow_v2"
        assert snapshot.snapshot_json["bagging_result"]["bagging_result_id"] == bagging_result_id
        assert snapshot.saved_sheet_id == snapshot.draft_id
        assert snapshot.bagging_result_id == bagging_result_id
        assert snapshot.output_bundle_id == snapshot.snapshot_json["output_bundle"]["output_bundle_id"]
        output_artifact = session.get(OrderOutputBundle, snapshot.snapshot_json["output_bundle"]["output_bundle_id"])
        assert output_artifact is not None
        assert output_artifact.payload_json["confirmed_snapshot_id"] == snapshot.id
        lines = session.query(OrderLine).filter(OrderLine.order_id == order_id).all()
        assert len(lines) == 1
        assert lines[0].confirmed_snapshot_id == snapshot.id
        assert isinstance(lines[0].line_digest, str) and len(lines[0].line_digest) == 64
        assert snapshot.snapshot_json["order_lines"][0]["line_digest"] == lines[0].line_digest
        assert session.get(Order, order_id).status == "確定"


@pytest.mark.parametrize("outer_valid", [True, False], ids=["inner-digest", "outer-digest"])
def test_step4_final_confirm_blocks_materialization_digest_drift(monkeypatch, april_output_context, outer_valid) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet=april_output_context(order_id, {"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]}),
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    bagging, error = order_workflow_v2_service.run_bagging(order_id, expected_revision=_current_workflow_revision(order_id))
    assert error is None
    assert bagging["output_bundle"]["materialization_digest"]

    with session_scope() as session:
        output_artifact = session.get(OrderOutputBundle, bagging["output_bundle"]["output_bundle_id"])
        assert output_artifact is not None
        output_bundle = dict(output_artifact.payload_json)
        output_bundle["materialization_digest"] = "stale-digest"
        output_artifact.payload_json = output_bundle
        if outer_valid:
            output_artifact.materialization_digest = "stale-digest"
            output_artifact.payload_digest = output_artifacts.payload_digest(output_bundle)

    confirmed, error = order_workflow_v2_service.final_confirm(order_id, confirmed_by="tester", expected_revision=_current_workflow_revision(order_id))

    assert confirmed is None
    if outer_valid:
        assert error == "output_bundle_materialization_mismatch"
    else:
        assert error == "output_review_required"


def test_confirmed_workflow_refuses_missing_artifacts_without_rebuilding(monkeypatch, april_output_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet=april_output_context(order_id, {"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]}),
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    bagging, error = order_workflow_v2_service.run_bagging(order_id, expected_revision=_current_workflow_revision(order_id))
    assert error is None
    original_bagging_result_id = bagging["bagging_result"]["bagging_result_id"]
    confirmed, error = order_workflow_v2_service.final_confirm(order_id, confirmed_by="tester", expected_revision=_current_workflow_revision(order_id))
    assert error is None
    inspection, error = order_workflow_v2_service.get_inspection(order_id)
    assert error is None
    assert inspection["bagging_result"]["bagging_result_id"] == original_bagging_result_id
    assert inspection["output_bundle"]["source_bagging_result_id"] == original_bagging_result_id

    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        assert workflow is not None
        meta = dict(workflow.secondary_actions_json.get(order_workflow_v2_service.WORKFLOW_V2_META_KEY) or {})
        for key in ["bagging_result_id", "bagging_result", "output_bundle_id", "output_bundle"]:
            meta.pop(key, None)
        workflow.secondary_actions_json = {
            **dict(workflow.secondary_actions_json or {}),
            order_workflow_v2_service.WORKFLOW_V2_META_KEY: meta,
        }
        snapshot = session.get(OrderConfirmedSnapshot, confirmed["confirmed_snapshot_id"])
        assert snapshot is not None
        snapshot.snapshot_json = {
            key: value
            for key, value in dict(snapshot.snapshot_json or {}).items()
            if key not in {"bagging_result", "output_bundle", "materialization_candidate"}
        }

    before = database_snapshot()
    workflow_payload, error = order_workflow_v2_service.get_workflow(order_id)
    assert error is None
    assert workflow_payload["state"] == "confirmed_snapshot_artifacts_missing"
    assert workflow_payload["confirmed_snapshot_id"] == confirmed["confirmed_snapshot_id"]
    inspection, error = order_workflow_v2_service.get_inspection(order_id)
    assert error is None
    assert inspection["bagging_result"] is None
    assert inspection["output_bundle"] is None
    assert database_snapshot() == before


def test_confirmed_legacy_display_requires_existing_context_and_lineage(monkeypatch, april_output_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet=april_output_context(order_id, {"rows": [{"menu_name": "白身魚のフライ", "regular": "4"}]}),
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    order_workflow_v2_service.run_bagging(order_id, expected_revision=_current_workflow_revision(order_id))
    confirmed, error = order_workflow_v2_service.final_confirm(order_id, confirmed_by="tester", expected_revision=_current_workflow_revision(order_id))
    assert error is None

    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        assert workflow is not None
        workflow.state = "apply_ready"
        workflow.headline = "下書きを明細へ反映できます"
        workflow.primary_action = "apply_draft"
        workflow.blockers_json = ["draft_newer_than_lines"]
        workflow.warnings_json = ["draft_newer_than_lines"]

    before = database_snapshot()
    projected, error = order_workflow_v2_service.get_workflow(order_id)

    assert error is None
    assert projected["state"] == "confirmed"
    assert projected["headline"] in {"確定済み", "注文が確定されました"}
    assert projected["primary_action"] in {None, "none"}
    assert projected["confirmed_snapshot_id"] == confirmed["confirmed_snapshot_id"]
    assert projected["bagging_result_id"]
    assert projected["output_bundle_id"]
    assert projected["blockers"] == []
    assert projected["warnings"] == []
    assert projected["legacy_state"] == "apply_ready"
    assert database_snapshot() == before
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        workflow.secondary_actions_json = {}
        draft = session.get(OrderSheetDraft, workflow.draft_id)
        assert draft is not None
        draft.template_version_id = None
    before = database_snapshot()
    blocked, error = order_workflow_v2_service.get_workflow(order_id)
    assert error is None
    assert blocked["state"] == "context_not_confirmed"
    assert blocked["confirmed_snapshot_id"] == confirmed["confirmed_snapshot_id"]
    assert database_snapshot() == before


def test_confirmed_legacy_refuses_inline_bundle_when_snapshot_is_missing(monkeypatch, authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    template_version_id = _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"fields": ["date", "menu_name", "qty.regular_x"], "rows": [["04/26", "A", "1"]]},
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    snapshot_id = _id("OCSlegacy")

    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert row is not None
        row.state = "apply_ready"
        row.confirmed_snapshot_id = None
        row.blockers_json = ["draft_newer_than_lines"]
        row.warnings_json = ["draft_newer_than_lines"]
        meta = dict(row.secondary_actions_json[order_workflow_v2_service.WORKFLOW_V2_META_KEY])
        meta["output_bundle_id"] = _id("OOBlegacy")
        meta["output_bundle"] = {
            "output_bundle_id": meta["output_bundle_id"],
            "confirmed_snapshot_id": snapshot_id,
            "template_version_id": template_version_id,
            "source_saved_sheet_id": saved["saved_sheet"]["saved_sheet_id"],
        }
        row.secondary_actions_json = {order_workflow_v2_service.WORKFLOW_V2_META_KEY: meta}
        draft = session.get(OrderSheetDraft, saved["saved_sheet"]["saved_sheet_id"])
        assert draft is not None
        draft.template_version_id = None

    before = database_snapshot()
    workflow, error = order_workflow_v2_service.get_workflow(order_id)

    assert error is None
    # Inline auxiliary output cannot restore a missing canonical snapshot.
    assert workflow["state"] == "saved_sheet_template_mismatch"
    assert workflow["confirmed_snapshot_id"] is None
    assert "saved_sheet_template_mismatch" in workflow["blockers"]
    assert database_snapshot() == before

    # Isolate the inline-only fault after retaining the original invalid-version case.
    with session_scope() as session:
        session.get(OrderSheetDraft, saved["saved_sheet"]["saved_sheet_id"]).template_version_id = template_version_id
    before = database_snapshot()
    workflow, error = order_workflow_v2_service.get_workflow(order_id)
    assert error is None
    assert workflow["state"] == "sheet_saved"
    assert workflow["selected_ocr_result_id"] == evidence_id_1
    assert workflow["saved_sheet_id"] == saved["saved_sheet"]["saved_sheet_id"]
    assert workflow["confirmed_snapshot_id"] is None
    assert database_snapshot() == before


def test_confirmed_legacy_refuses_missing_snapshot_despite_output_ids(monkeypatch, authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"fields": ["date", "menu_name", "qty.regular_x"], "rows": [["04/26", "A", "1"]]},
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None

    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert row is not None
        row.state = "apply_ready"
        missing_snapshot_id = _id("OCSmissing")
        row.confirmed_snapshot_id = None
        row.blockers_json = ["draft_newer_than_lines"]
        row.warnings_json = ["draft_newer_than_lines"]
        meta = dict(row.secondary_actions_json[order_workflow_v2_service.WORKFLOW_V2_META_KEY])
        # A legacy JSON reference can be absent; a dangling SQL FK cannot.
        meta["confirmed_snapshot_id"] = missing_snapshot_id
        meta["bagging_result_id"] = _id("OBGlegacy")
        meta["output_bundle_id"] = _id("OOBlegacy")
        row.secondary_actions_json = {order_workflow_v2_service.WORKFLOW_V2_META_KEY: meta}
        draft = session.get(OrderSheetDraft, saved["saved_sheet"]["saved_sheet_id"])
        assert draft is not None
        draft.template_version_id = None

    before = database_snapshot()
    workflow, error = order_workflow_v2_service.get_workflow(order_id)

    assert error is None
    assert workflow["state"] == "confirmed_snapshot_required"
    assert "confirmed_snapshot_required" in workflow["blockers"]
    assert database_snapshot() == before
    with session_scope() as session:
        assert session.get(OrderConfirmedSnapshot, missing_snapshot_id) is None


def test_confirmed_legacy_refuses_stale_workflow_draft_despite_output_ids(monkeypatch, authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    template_version_id = _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet={"fields": ["date", "menu_name", "qty.regular_x"], "rows": [["04/26", "A", "1"]]},
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    snapshot_id = _id("OCSlegacy")

    with session_scope() as session:
        stale_draft_id = _id("ODRstale")
        current = session.get(OrderSheetDraft, saved["saved_sheet"]["saved_sheet_id"])
        session.add(OrderSheetDraft(
            id=stale_draft_id, order_id=order_id, template_version_id=template_version_id,
            base_evidence_run_id=evidence_id_1, draft_sheet_json=current.draft_sheet_json,
        ))
        session.flush()
        session.add(
            OrderConfirmedSnapshot(
                id=snapshot_id,
                order_id=order_id,
                template_version_id=template_version_id,
                draft_id=stale_draft_id,
                snapshot_digest="digest",
                snapshot_json={},
                confirmed_by="test",
                confirmed_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        session.flush()
        row = session.get(OrderWorkflowState, order_id)
        assert row is not None
        row.state = "apply_ready"
        row.confirmed_snapshot_id = snapshot_id
        row.blockers_json = ["draft_newer_than_lines"]
        row.warnings_json = ["draft_newer_than_lines"]
        meta = dict(row.secondary_actions_json[order_workflow_v2_service.WORKFLOW_V2_META_KEY])
        meta["bagging_result_id"] = _id("OBGlegacy")
        meta["output_bundle_id"] = _id("OOBlegacy")
        row.secondary_actions_json = {order_workflow_v2_service.WORKFLOW_V2_META_KEY: meta}
        draft = session.get(OrderSheetDraft, saved["saved_sheet"]["saved_sheet_id"])
        assert draft is not None
        draft.template_version_id = None

    before = database_snapshot()
    workflow, error = order_workflow_v2_service.get_workflow(order_id)

    assert error is None
    assert workflow["state"] == "confirmed_snapshot_required"
    assert "confirmed_snapshot_required" in workflow["blockers"]
    assert workflow["saved_sheet_id"] == saved["saved_sheet"]["saved_sheet_id"]
    assert workflow["confirmed_snapshot_id"] == snapshot_id
    assert database_snapshot() == before


def test_output_source_mismatch_projects_to_step5_rebuild(monkeypatch, april_output_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet=april_output_context(order_id, {"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]}),
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    order_workflow_v2_service.run_bagging(order_id, expected_revision=_current_workflow_revision(order_id))
    order_workflow_v2_service.confirm_bagging(order_id, expected_revision=_current_workflow_revision(order_id))

    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert row is not None
        meta = order_workflow_v2_service._workflow_meta(row)
        output_artifact = session.get(OrderOutputBundle, meta["output_bundle_id"])
        assert output_artifact is not None
        output_bundle = dict(output_artifact.payload_json)
        output_bundle["source_saved_sheet_id"] = _id("ODSstale")
        output_artifact.payload_json = output_bundle
        output_artifact.source_saved_sheet_id = output_bundle["source_saved_sheet_id"]
        output_artifact.payload_digest = output_artifacts.payload_digest(output_bundle)

    workflow, error = order_workflow_v2_service.get_workflow(order_id)

    assert error is None
    assert workflow["state"] == "output_bundle_source_mismatch"
    assert workflow["primary_action"] == "final_confirm"
    assert workflow["blockers"] == ["output_bundle_source_mismatch"]


def test_output_review_blocks_when_output_bundle_artifact_row_is_missing(monkeypatch, april_output_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet=april_output_context(order_id, {"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]}),
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    bagging, error = order_workflow_v2_service.run_bagging(order_id, expected_revision=_current_workflow_revision(order_id))
    assert error is None

    with session_scope() as session:
        output_artifact = session.get(OrderOutputBundle, bagging["output_bundle"]["output_bundle_id"])
        assert output_artifact is not None
        session.delete(output_artifact)

    workflow, error = order_workflow_v2_service.get_workflow(order_id)

    assert error is None
    assert workflow["state"] == "output_bundle_artifact_missing"
    assert workflow["primary_action"] == "run_bagging"
    assert workflow["blockers"] == ["output_bundle_artifact_missing"]


def test_saving_sheet_after_final_confirm_invalidates_snapshot_and_lines(monkeypatch, april_output_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet=april_output_context(order_id, {"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]}),
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert order_workflow_v2_service.run_bagging(order_id, expected_revision=_current_workflow_revision(order_id))[1] is None
    assert order_workflow_v2_service.confirm_bagging(order_id, expected_revision=_current_workflow_revision(order_id))[1] is None
    confirmed, error = order_workflow_v2_service.final_confirm(order_id, confirmed_by="tester", expected_revision=_current_workflow_revision(order_id))
    assert error is None
    snapshot_id = confirmed["confirmed_snapshot_id"]

    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet=april_output_context(order_id, {"rows": [{"menu_name": "大豆のトマト煮", "regular": "71"}]}),
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )

    assert error is None
    assert saved["workflow"]["state"] == "sheet_saved"
    assert saved["workflow"]["confirmed_snapshot_id"] is None
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        assert workflow is not None
        assert workflow.confirmed_snapshot_id is None
        assert workflow.draft_id == saved["saved_sheet"]["saved_sheet_id"]
        assert session.get(OrderConfirmedSnapshot, snapshot_id) is None
        assert session.query(OrderLine).filter(OrderLine.order_id == order_id).count() == 0
        assert session.get(Order, order_id).status == "要確認"


def test_get_workflow_projects_blocker_for_inconsistent_confirmed_lineage(authored_april_context) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    assert workflow is not None
    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        evidence = session.get(OrderOcrEvidenceRun, evidence_id_1)
        assert row is not None
        assert evidence is not None
        row.state = "confirmed"
        row.headline = "確定済み"
        row.primary_action = None
        row.evidence_run_id = evidence_id_1
        row.draft_id = None
        row.confirmed_snapshot_id = None
        row.blockers_json = []
        meta = order_workflow_v2_service._workflow_meta(row)
        meta.pop("template_version_id", None)
        row.secondary_actions_json = {order_workflow_v2_service.WORKFLOW_V2_META_KEY: meta}
        evidence.template_version_id = None

    projected, error = order_workflow_v2_service.get_workflow(order_id)
    results, result_error = order_workflow_v2_service.list_ocr_results(order_id)

    assert error is None
    assert projected["state"] == "template_version_mismatch"
    assert projected["primary_action"] == "run_ocr"
    assert projected["blockers"] == ["template_version_mismatch"]
    assert result_error is None
    assert results["workflow_state"] == "template_version_mismatch"
    assert results["blockers"] == ["template_version_mismatch"]
    assert results["results"] == []


def test_get_workflow_projects_legacy_state_from_v2_lineage(april_output_context, tmp_path) -> None:
    order_id, evidence_id_1, _ = _create_order_with_evidence()
    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    assert workflow is not None
    _stamp_evidence_with_workflow_template(order_id, evidence_id_1)
    selected, error = order_workflow_v2_service.select_ocr_result(order_id, evidence_id_1, expected_revision=_current_workflow_revision(order_id))
    assert error is None
    assert selected["state"] == "ocr_selected"
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order_id,
        sheet=april_output_context(order_id, {"rows": [{"menu_name": "大豆のトマト煮", "regular": "70"}]}),
        edited_by="test",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    assert saved["workflow"]["state"] == "sheet_saved"

    with session_scope() as session:
        row = session.get(OrderWorkflowState, order_id)
        assert row is not None
        row.state = "apply_ready"
        row.headline = "下書きを明細へ反映できます"
        row.primary_action = "apply_draft"
        row.blockers_json = ["draft_newer_than_lines"]
        row.warnings_json = ["draft_newer_than_lines"]

    projected, error = order_workflow_v2_service.get_workflow(order_id)

    assert error is None
    assert projected["state"] == "sheet_saved"
    assert projected["primary_action"] == "run_bagging"
    assert projected["blockers"] == []
    assert projected["warnings"] == []
    assert projected["legacy_state"] == "apply_ready"


def test_sheet_auto_edit_job_stores_running_and_done_result(monkeypatch, authored_april_context, tmp_path) -> None:
    order_id, evidence_id, _ = _create_order_with_evidence()
    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order_id,
        facility_id="FAC00001",
        week_start="2026-04-26",
        week_end="2026-04-30",
        template_id="template-fac00001",
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    assert workflow is not None

    _stamp_evidence_with_workflow_template(order_id, evidence_id)
    selected, error = order_workflow_v2_service.select_ocr_result(
        order_id, evidence_id, expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    _attach_context_pdf(order_id, tmp_path)
    started, error = order_workflow_v2_service.start_sheet_auto_edit_job(
        order_id=order_id,
        sheet={"rows": [["menu", "70"]]},
        use_llm=True,
        expected_revision=_current_workflow_revision(order_id),
    )
    assert error is None
    assert started is not None
    job_id = started["job"]["job_id"]
    assert started["job"]["status"] == "running"

    provider_calls = []
    def fake_gemini(**kwargs):
        provider_calls.append(kwargs)
        return {"patches": [{"row_index": 0, "col_index": 1, "suggested_value": "70"}]}, {"status": "ok"}

    monkeypatch.setattr(order_workflow_v2_service.workflow_v2_sheet_review_service,
                        "_gemini_json_request", fake_gemini)

    propose = order_workflow_v2_service.propose_sheet_auto_edit

    def observed_proposal(**kwargs):
        assert kwargs["order_id"] == order_id
        assert kwargs["sheet"] == {"rows": [["menu", "70"]]}
        return propose(**kwargs)

    monkeypatch.setattr(order_workflow_v2_service, "propose_sheet_auto_edit", observed_proposal)
    order_workflow_v2_service.run_sheet_auto_edit_job(
        order_id=order_id,
        job_id=job_id,
        sheet={"rows": [["menu", "70"]]},
        use_llm=True,
        expected_revision=started["job"]["workflow_completion_revision"],
    )
    payload, error = order_workflow_v2_service.get_sheet_auto_edit_job(order_id=order_id, job_id=job_id)

    assert error is None
    assert payload is not None
    assert payload["job"]["status"] == "done"
    assert provider_calls
    assert started["workflow"]["order_id"] == order_id
    assert payload["result"]["patches"][0]["suggested_value"] == "70"
