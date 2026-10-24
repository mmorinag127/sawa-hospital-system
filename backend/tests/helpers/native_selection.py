"""Actual owned Hakodate production/selection for authored component inputs.

Only the external recognizer and its rendered files are doubled. This helper
does not establish live FAX accuracy or same-input production parity.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from src.db import session_scope
from src.models.order import Order
from src.models.order_workflow_state import OrderWorkflowState
from src.services import hakodate_cell_ocr_batch_service as batch
from src.services import hakodate_assignment_service as assignment
from src.services import order_service, order_workflow_v2_service as workflow
from src.services import workflow_attempt_guard_service as guard
from src.services import workflow_ocr_delivery_service as delivery
from src.services.sheet_week_service import parse_sheet_week_value
from src.services.storage_service import load_selected_pdf_bytes
from tests.helpers.ingest_original import configure_delivery
from tests.helpers.authored_cell_values import assert_declared_cells_used, declared_field
from tests.unit.test_h2_operator_repair_revision import _provider_output_files


def produce_authored_native_result(monkeypatch, *, order_id, table_rows, columns, unlisted_cells, artifact_root,
                                   recognizer_score=1.0):
    """Consume an already registered/context-confirmed original, never fabricate ownership."""
    assert isinstance(unlisted_cells, str), "unlisted component cells require an explicit literal"
    assert type(recognizer_score) in (int, float) and 0 <= recognizer_score <= 1
    assert not {"note", "remarks"} <= {column["name"] for column in columns}, "ambiguous declared note aliases"
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(artifact_root))
    for key in ("OCR_OUTPUT_BUCKET", "RAW_BUCKET", "K_SERVICE"):
        monkeypatch.delenv(key, raising=False)
    configure_delivery(monkeypatch)
    for key, value in {
        "PROJECT_ID": "native-component", "LOCATION": "asia-northeast2", "QUEUE": "native-component",
        "TARGET_URL": "https://reserved.invalid", "SERVICE_ACCOUNT_EMAIL": "worker@native-component.iam.gserviceaccount.com",
        "AUDIENCE": "https://reserved.invalid", "DISPATCH_DEADLINE_SECONDS": "300",
    }.items():
        monkeypatch.setenv("HOSPITAL_WORKFLOW_OCR_DELIVERY_" + key, value)
    with session_scope() as session:
        order = session.get(Order, order_id)
        _, start, end = parse_sheet_week_value(order.week_code)
        assert start is not None and end is not None
        original_uri = order.document_uri
    roles = {role: [column for column in columns if column["role"] == role]
             for role in ("date", "daypart", "menu_name")}
    assert all(len(items) == 1 for items in roles.values())
    values, required = {}, set()
    for row in table_rows:
        raw_day = str(row[roles["date"][0]["source_index"]])
        if len(raw_day) == 10:
            day = date.fromisoformat(raw_day)
        else:
            month, day_number = (int(item) for item in raw_day.split("/"))
            candidates = []
            for year in sorted({start.year, end.year}):
                try:
                    candidate = date(year, month, day_number)
                except ValueError:
                    continue
                if start <= candidate <= end:
                    candidates.append(candidate)
            assert len(candidates) == 1, "authored date must resolve uniquely inside the explicit period"
            day = candidates[0]
        assert start <= day <= end
        daypart = order_service._normalize_daypart_key(row[roles["daypart"][0]["source_index"]])
        menu = assignment._normalize_slot_text(row[roles["menu_name"][0]["source_index"]])
        assert daypart in {"朝", "昼", "夕"}, "authored daypart must have a canonical identity"
        assert menu, "authored menu identity required"
        for column in columns:
            if column["role"] in roles:
                continue
            key = day.isoformat(), daypart, menu, column["name"]
            assert key not in values, "duplicate authored cell identity"
            value = row[column["source_index"]]
            values[key] = "" if value is None else str(value)
            if column["role"] in {"quantity", "note"}:
                required.add(key)
    current, error = workflow.get_workflow_readonly(order_id)
    assert error is None
    job_id = "OCR-native-component-" + uuid4().hex
    queued, error = workflow.mark_ocr_run_queued(order_id, job_id, input_reference=original_uri,
        request_mode="ocr_rerun", expected_revision=current["revision"])
    assert error is None, error
    claim = delivery.claim_delivery(queued["delivery"]["id"], queued["delivery"]["generation"],
        lease_expires_at=datetime.utcnow() + timedelta(minutes=5))
    assert not claim.get("duplicate"), claim
    with session_scope() as session:
        receipt, captured = workflow._axis_source_receipt(
            session, session.get(Order, order_id), session.get(OrderWorkflowState, order_id))

    def manifest(**_kwargs):
        pdf = receipt["selected_pdf_receipt"]
        return {"configured_input": deepcopy(captured["configured_input"]),
                "selected_pdf_receipt": deepcopy(pdf),
                "_verified_pdf_bytes": load_selected_pdf_bytes(pdf["storage_uri"], pdf)}

    calls = []

    def recognize(*, item, output_dir, **_kwargs):
        assert item["_verified_pdf_bytes"] == load_selected_pdf_bytes(
            receipt["selected_pdf_receipt"]["storage_uri"], receipt["selected_pdf_receipt"])
        regions = [{**deepcopy(region), "bbox": [index * 20, 0, index * 20 + 10, 10]}
                   for index, region in enumerate(item["original_target_semantics"])]
        cell_values, used = {}, set()
        for region in regions:
            explicit = set()
            for target in region["logical_targets"]:
                daypart = order_service._normalize_daypart_key(target["daypart"])
                assert daypart in {"朝", "昼", "夕"}, "physical daypart must have a canonical identity"
                key = (str(target["date"]), daypart, assignment._normalize_slot_text(target["menu_name"]),
                       declared_field(target, [column["name"] for column in columns]))
                explicit.add(values[key] if key in values else unlisted_cells)
                if key in values:
                    used.add(key)
            assert len(explicit) == 1, "a merged authored region must have one explicit value"
            cell_values[region["sheet_cell"]] = explicit.pop()
        assert_declared_cells_used(required, used)
        records = [{**region, "ocr_text": cell_values[region["sheet_cell"]], "recognizer_score": recognizer_score,
                    "recognizer_accepted_candidate": {"normalized_digits": cell_values[region["sheet_cell"]]}}
                   for region in regions]
        calls.append(deepcopy(regions))
        outputs = _provider_output_files(output_dir, records=records, regions=regions, sheet_values=cell_values)
        return SimpleNamespace(metrics={"expected_sheet_cells": item["original_expected_sheet_cells"]},
            expected_sheet_cells=tuple(item["original_expected_sheet_cells"]), outputs=outputs,
            ocr_engine="authored-component-external-recognizer"), None

    revision, attempt = queued["workflow"]["revision"], queued["ocr_job"]["workflow_attempt_id"]
    with (monkeypatch.context() as provider_patch, delivery.claim_context(claim),
          order_service.workflow_ocr_write_context(attempt, revision, job_id, order_id),
          guard.workflow_execution_context(job_id, order_id, attempt, revision)):
        provider_patch.setattr(order_service, "_build_live_hakodate_manifest_item", manifest)
        provider_patch.setattr(batch, "build_hakodate_best_method_for_manifest_item", recognize)
        evidence, error = order_service.rerun_ocr_evidence_only(order_id, job_id=job_id,
            project_sheet=False, refresh_workflow=False)
    assert error is None and evidence is not None, error
    assert len(calls) == 1
    return evidence


def select_authored_native_result(monkeypatch, *, order_id, table_rows, columns, unlisted_cells, artifact_root,
                                  recognizer_score=1.0):
    evidence = produce_authored_native_result(monkeypatch, order_id=order_id,
        table_rows=table_rows, columns=columns, unlisted_cells=unlisted_cells, artifact_root=artifact_root,
        recognizer_score=recognizer_score)
    current, error = workflow.get_workflow_readonly(order_id)
    assert error is None
    selected, error = workflow.select_ocr_result(order_id, evidence["id"], expected_revision=current["revision"])
    assert error is None and selected is not None, error
    return evidence
