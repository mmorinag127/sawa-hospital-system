"""Read saved workflow surfaces without rebuilding their upstream producers."""
from copy import deepcopy
from types import SimpleNamespace

from sqlalchemy import JSON, case, column, func, literal, select, true

from src.db import retain_read_models, session_scope
from src.models.ocr_job import OcrJob
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_version import OrderVersion
from src.services import order_workflow_v2_service as workflow
from src.services import order_output_artifact_service
from src.services.ocr_history_read import read_history_summary


def _saved_reference(session, model, identity, order_id, *columns):
    if not identity:
        return None
    return session.execute(select(model.id, model.order_id, *columns).where(
        model.id == identity, model.order_id == order_id,
    )).first()


def _reference_error(session, order, state, meta):
    if workflow._workflow_template_selection_pending(state, meta):
        return None
    version, error = workflow._require_existing_workflow_template_version(
        session, order=order, workflow=state, meta=meta,
    )
    if error:
        return error
    canonical = workflow._canonical_workflow_v2_state(state, meta)
    if (workflow._workflow_state_requires_selected_ocr(canonical) or workflow._workflow_state_requires_saved_sheet(canonical)) and not workflow._workflow_meta_has_confirmed_context(meta):
        return "context_not_confirmed"
    if workflow._workflow_state_requires_selected_ocr(canonical):
        evidence = _saved_reference(session, OrderOcrEvidenceRun, state.evidence_run_id, order.id,
                                    OrderOcrEvidenceRun.template_version_id)
        if evidence is None:
            return "selected_ocr_missing"
        if evidence.template_version_id != version:
            return "template_version_mismatch"
    if canonical == "confirmed" or workflow._workflow_state_requires_saved_sheet(canonical):
        saved = _saved_reference(session, OrderSheetDraft, state.draft_id, order.id,
                                OrderSheetDraft.template_version_id, OrderSheetDraft.base_evidence_run_id)
        if saved is None:
            return "saved_sheet_missing"
        if saved.template_version_id != version:
            return "saved_sheet_template_mismatch"
        if saved.base_evidence_run_id != state.evidence_run_id:
            return "saved_sheet_evidence_lineage_mismatch"
    for model, key, required, prefix in (
        (workflow.OrderBaggingResult, "bagging_result_id", canonical == "confirmed" or workflow._workflow_state_requires_bagging(canonical), "bagging_result"),
        (workflow.OrderOutputBundle, "output_bundle_id", canonical == "confirmed" or workflow._workflow_state_requires_output_review(canonical), "output_bundle"),
    ):
        if not required and not meta.get(key):
            continue
        artifact = _saved_reference(session, model, meta.get(key), order.id,
                                    model.template_version_id, model.source_saved_sheet_id)
        if artifact is None:
            return prefix + "_artifact_missing"
        if artifact.template_version_id != version:
            return prefix + "_template_mismatch"
        if artifact.source_saved_sheet_id != state.draft_id:
            return prefix + "_source_mismatch"
    if canonical == "confirmed":
        snapshot = _saved_reference(session, OrderConfirmedSnapshot,
            state.confirmed_snapshot_id or workflow._confirmed_snapshot_id_from_meta(meta), order.id,
            OrderConfirmedSnapshot.template_version_id, OrderConfirmedSnapshot.draft_id,
            OrderConfirmedSnapshot.bagging_result_id, OrderConfirmedSnapshot.output_bundle_id)
        if snapshot is None:
            return "confirmed_snapshot_required"
        if snapshot.template_version_id != version:
            return "confirmed_snapshot_template_mismatch"
        if (snapshot.draft_id != state.draft_id or snapshot.bagging_result_id != meta.get("bagging_result_id")
                or snapshot.output_bundle_id != meta.get("output_bundle_id")):
            return "confirmed_snapshot_lineage_mismatch"
    return None


def _job_summary(session, order, state, meta):
    identity = meta.get("ocr_job_id")
    if not identity:
        missing = state.state in {"ocr_running", "ocr_completed", "ocr_failed"} or "ocr_job_id" in meta or "ocr_job_attempt_id" in meta
        return None, "ocr_job_not_recorded" if missing else None, None
    keys = ("workflow_attempt_id", "workflow_completion_revision", "ocr_started_at", "run_started_at", "ocr_finished_at",
            "run_finished_at", "stage_updated_at", "ocr_elapsed_seconds", "elapsed_seconds",
            "processing_stage", "result_state", "progress_step", "progress_total", "progress_label",
            "error", "error_detail", "error_user_message", "recovery_action", "evidence_run_id", "rerun_terminal_receipt")
    metrics = OcrJob.metrics
    fields = [
        OcrJob.id, OcrJob.order_id, OcrJob.order_document_id, OcrJob.uploaded_pdf_id,
        OcrJob.input_artifact_digest, OcrJob.template_version_id, OcrJob.status,
        OcrJob.error_message, OcrJob.created_at, OcrJob.updated_at,
        OcrJob.input_reference, OcrJob.output_reference, OcrJob.template_id,
    ]
    if session.get_bind().dialect.name == "postgresql":
        object_metrics = case((func.json_typeof(metrics) == "object", metrics), else_=literal({}, type_=JSON))
        projected = func.json_to_record(object_metrics).table_valued(
            *(column(key, JSON) for key in (*keys, "nonworkflow_execution")),
        ).render_derived(with_types=True).lateral("display_metrics")
        native = projected.c.nonworkflow_execution
        native_object = case((func.json_typeof(native) == "object", native), else_=literal({}, type_=JSON))
        native_fields = func.json_to_record(native_object).table_valued(
            column("execution_id", JSON), column("ingest_ocr_mode", JSON), column("intake_protocol", JSON),
            column("job_id", JSON), column("order_id", JSON),
        ).render_derived(with_types=True).lateral("display_execution")
        statement = select(*fields, *(projected.c[key] for key in keys),
            native_fields.c.execution_id.label("native_execution_id"),
            native_fields.c.ingest_ocr_mode.label("native_mode"),
            native_fields.c.intake_protocol.label("native_protocol"),
            native_fields.c.job_id.label("native_job_id"), native_fields.c.order_id.label("native_order_id"),
        ).select_from(OcrJob).join(projected, true()).join(native_fields, true())
    else:
        statement = select(*fields, *(metrics[key].label(key) for key in keys),
            metrics["nonworkflow_execution"]["execution_id"].label("native_execution_id"),
            metrics["nonworkflow_execution"]["ingest_ocr_mode"].label("native_mode"),
            metrics["nonworkflow_execution"]["intake_protocol"].label("native_protocol"),
            metrics["nonworkflow_execution"]["job_id"].label("native_job_id"),
            metrics["nonworkflow_execution"]["order_id"].label("native_order_id"))
    row = session.execute(statement.where(OcrJob.id == identity)).mappings().first()
    if row is None:
        return None, "ocr_recorded_job_missing", None
    if row["order_id"] != order.id:
        return None, "ocr_recorded_job_order_mismatch", None
    if type(row["workflow_attempt_id"]) is not type(meta.get("ocr_job_attempt_id")) or row["workflow_attempt_id"] != meta.get("ocr_job_attempt_id"):
        return None, "ocr_recorded_job_attempt_mismatch", None
    if row["order_document_id"] != order.current_document_id:
        return None, "ocr_recorded_job_document_mismatch", None
    if row["template_version_id"] != state.template_version_id:
        return None, "template_version_mismatch", None
    values = dict(row)
    values["metrics"] = {key: values.pop(key) for key in keys}
    if values.pop("native_mode") == "hakodate":
        values["metrics"]["nonworkflow_execution"] = {
            "ingest_ocr_mode": "hakodate", "execution_id": values["native_execution_id"],
            "intake_protocol": values["native_protocol"],
            "job_id": values["native_job_id"], "order_id": values["native_order_id"],
        }
    # A recorded terminal summary is not a selectable result or a command proof.
    status = str(values["status"] or "").strip().lower()
    common = workflow._recorded_hospital_job_status(values, {"status": status})
    return workflow._serialize_ocr_job(SimpleNamespace(**values)), None, common


def _candidates(session, order, state, meta):
    rows = read_history_summary(session, order.id)
    template = workflow._effective_workflow_template_version_id(state, meta) if state else None
    visible = [row for row in rows if state is None or (template and row.template_version_id == template)]
    return {
        "order_id": order.id, "workflow_state": state.state if state else "not_initialized",
        "selected_ocr_result_id": state.evidence_run_id if state else None,
        "blockers": list(state.blockers_json or []) if state else ["workflow_not_initialized"],
        "candidate_template_version_id": template,
        "hidden_template_mismatch_result_count": len(rows) - len(visible),
        "results": [{"ocr_result_id": row.id, "order_id": row.order_id,
                     "template_version_id": row.template_version_id, "schema_version": row.schema_version,
                     "producer_version": row.producer_version, "source": row.source, "status": row.status,
                     "selected": state is not None and row.id == state.evidence_run_id,
                     "artifact_digest": row.artifact_digest, "created_at": workflow._serialize_datetime(row.created_at),
                     "detail_state": "not_loaded"} for row in visible],
    }


def get_display(order_id, *, scope="summary"):
    if scope not in {"summary", "ocr", "sheet", "output", "inspection"}:
        return None, "workflow_display_scope_invalid"
    with session_scope() as session, session.no_autoflush, retain_read_models(session):
        order, error = workflow._get_order_or_error(session, order_id)
        if error:
            return None, error
        state = workflow._get_workflow_readonly(session, order.id)
        meta = workflow._workflow_meta(state, copy_meta=False)
        serialized = workflow._serialize_workflow(state, meta_override=meta) if state else workflow._serialize_uninitialized_workflow(order)
        if state:
            if not workflow._workflow_meta_has_confirmed_context(meta) and not serialized.get("context_suggestion"):
                serialized["context_suggestion"] = workflow._order_context_suggestion(order)
            error = _reference_error(session, order, state, meta)
            serialized = (workflow._workflow_blocker_projection(serialized, error) if error else
                          workflow._apply_canonical_workflow_state_projection(serialized, row=state, meta=meta,
                            state=workflow._canonical_workflow_v2_state(state, meta)))
            serialized["ocr_job"], serialized["ocr_job_binding_error"], serialized["common_job_status"] = _job_summary(session, order, state, meta)
        serialized["validation_state"] = "references_only"
        versions = [dict(row) for row in session.execute(select(
            OrderVersion.version_no, OrderVersion.document_id, OrderVersion.message_id,
            OrderVersion.received_at, OrderVersion.is_current,
        ).where(OrderVersion.order_id == order.id).order_by(OrderVersion.version_no.desc(), OrderVersion.id.desc())).mappings()]
        count = session.scalar(select(func.count()).select_from(OrderOcrEvidenceRun).where(OrderOcrEvidenceRun.order_id == order.id))
        result = {"order_id": order.id, "source": "workflow_display/v1", "display_scope": scope,
                  "workflow": serialized, "order_header": {"id": order.id, "document_id": order.current_document_id, "versions": versions,
                    "version_count": len(versions), "current_version": next((row for row in versions if row["is_current"]), None)},
                  "ocr_result_count": count, "loaded_sections": [],
                  "artifact_lineage": {"selected_ocr_result_id": state.evidence_run_id if state else None,
                    "saved_sheet_id": state.draft_id if state else None,
                    "confirmed_snapshot_id": state.confirmed_snapshot_id if state else None,
                    "bagging_result_id": meta.get("bagging_result_id"), "output_bundle_id": meta.get("output_bundle_id")}}
        if state and state.draft_id:
            saved = _saved_reference(session, OrderSheetDraft, state.draft_id, order.id,
                OrderSheetDraft.base_evidence_run_id, OrderSheetDraft.edited_at, OrderSheetDraft.created_at)
            if saved:
                result["saved_sheet"] = {"saved_sheet_id": saved.id, "source_ocr_result_id": saved.base_evidence_run_id,
                    "edited_at": workflow._serialize_datetime(saved.edited_at), "created_at": workflow._serialize_datetime(saved.created_at)}
        if scope in {"ocr", "sheet", "inspection"}:
            result["ocr_candidates"] = _candidates(session, order, state, meta)
            result["ocr_results"] = result["ocr_candidates"]["results"]
            result["loaded_sections"].append("ocr")
        if scope in {"sheet", "inspection"}:
            if error:
                return None, error
            if state and state.draft_id:
                draft = session.get(OrderSheetDraft, state.draft_id)
                evidence = session.get(OrderOcrEvidenceRun, draft.base_evidence_run_id) if draft else None
                result["saved_sheet"] = workflow._saved_sheet_with_target_cell_map(order=order, workflow=state,
                    saved_sheet=draft, evidence_payload=evidence.payload_json if evidence and evidence.order_id == order.id else None)
                sheet = result["saved_sheet"].get("sheet")
                result["pre_save_checks"] = workflow._workflow_pre_save_checks(meta)
                result["pre_save_status"] = workflow._pre_save_status_for_sheet(meta, workflow._stable_json_hash(sheet)) if isinstance(sheet, dict) else None
            else:
                result["saved_sheet"] = None
            result["loaded_sections"].append("sheet")
        if scope in {"output", "inspection"}:
            if error:
                return None, error
            if state and workflow._canonical_workflow_v2_state(state, meta) == "confirmed":
                snapshot = session.get(OrderConfirmedSnapshot, serialized["confirmed_snapshot_id"])
                artifacts = order_output_artifact_service.confirmed_snapshot_artifact_payloads(session, snapshot, expected_order_id=order.id) if snapshot else None
                if artifacts is None:
                    return None, "confirmed_snapshot_lineage_mismatch"
                result["bagging_result"], result["output_bundle"] = artifacts
            else:
                result["bagging_result"] = order_output_artifact_service.load_bagging_result_payload(session, meta.get("bagging_result_id"), expected_order_id=order.id)
                result["output_bundle"] = order_output_artifact_service.load_output_bundle_payload(session, meta.get("output_bundle_id"), expected_order_id=order.id)
                if meta.get("bagging_result_id") and result["bagging_result"] is None:
                    return None, "bagging_result_artifact_missing"
                if meta.get("output_bundle_id") and result["output_bundle"] is None:
                    return None, "output_bundle_artifact_missing"
            result["anomaly_review"] = deepcopy(meta.get("anomaly_review"))
            result["loaded_sections"].append("output")
        if scope == "inspection" and state and state.evidence_run_id:
            evidence = session.get(OrderOcrEvidenceRun, state.evidence_run_id)
            if evidence is None or evidence.order_id != order.id:
                return None, "selected_ocr_missing"
            selected = workflow._serialize_ocr_result(evidence, selected=True, include_execution_details=False)
            result["ocr_results"] = [selected if row["ocr_result_id"] == evidence.id else row for row in result["ocr_results"]]
            result["ocr_candidates"]["results"] = result["ocr_results"]
        return result, None
