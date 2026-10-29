"""Read saved workflow surfaces without rebuilding their upstream producers."""
from copy import deepcopy
from types import SimpleNamespace

from sqlalchemy import JSON, case, cast, column, func, literal, or_, select, true, type_coerce

from src.db import retain_read_models, session_scope
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.order_workflow_state import OrderWorkflowState
from src.models.document import OrderDocument
from src.models.uploaded_pdf import UploadedPdf
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_version import OrderVersion
from src.models.ocr_display_projection_v1 import JOB_KEYS, VERSION_KEYS
from src.services import order_workflow_v2_service as workflow
from src.services import order_output_artifact_service
from src.services.ocr_history_read import read_history_summary


def _saved_reference(session, model, identity, order_id, *columns):
    if not identity:
        return None
    return session.execute(select(model.id, model.order_id, *columns).where(
        model.id == identity, model.order_id == order_id,
    )).first()


def _reference_error(session, order, state, meta, *, reads=None, include_overlay=False):
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
        columns = [OrderOcrEvidenceRun.template_version_id]
        if include_overlay:
            columns.append(OrderOcrEvidenceRun.display_overlay_json.label("overlay"))
        evidence = _saved_reference(session, OrderOcrEvidenceRun, state.evidence_run_id, order.id, *columns)
        if reads is not None:
            reads["selected_evidence"] = evidence
        if evidence is None:
            return "selected_ocr_missing"
        if evidence.template_version_id != version:
            return "template_version_mismatch"
    if canonical == "confirmed" or workflow._workflow_state_requires_saved_sheet(canonical):
        saved = _saved_reference(session, OrderSheetDraft, state.draft_id, order.id,
                                OrderSheetDraft.template_version_id, OrderSheetDraft.base_evidence_run_id,
                                OrderSheetDraft.edited_at, OrderSheetDraft.created_at)
        if reads is not None:
            reads["saved_sheet"] = saved
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


_JOB_KEYS = JOB_KEYS
_VERSION_KEYS = VERSION_KEYS


def read_recorded_job_values(session, identities):
    if not identities:
        return {}
    keys = _JOB_KEYS
    metrics = (cast(OcrJob.display_read_json, JSON) if session.get_bind().dialect.name == "postgresql"
               else type_coerce(OcrJob.display_read_json, JSON))
    fields = [
        OcrJob.id, OcrJob.order_id, OcrJob.order_document_id, OcrJob.uploaded_pdf_id,
        OcrJob.input_artifact_digest, OcrJob.template_version_id, OcrJob.status,
        OcrJob.error_message, OcrJob.created_at, OcrJob.updated_at,
        OcrJob.input_reference, OcrJob.output_reference, OcrJob.template_id,
    ]
    if session.get_bind().dialect.name == "postgresql":
        object_metrics = case((func.json_typeof(metrics) == "object", metrics), else_=literal({}, type_=JSON))
        projected = func.json_to_record(object_metrics).table_valued(
            *(column(key, JSON) for key in (*keys, "nonworkflow_execution", "selected_version_input")),
        ).render_derived(with_types=True).lateral("display_metrics")
        native = projected.c.nonworkflow_execution
        native_object = case((func.json_typeof(native) == "object", native), else_=literal({}, type_=JSON))
        native_fields = func.json_to_record(native_object).table_valued(
            column("execution_id", JSON), column("ingest_ocr_mode", JSON), column("intake_protocol", JSON),
            column("job_id", JSON), column("order_id", JSON),
            column("selected_pdf_receipt", JSON), column("selected_version_input", JSON),
            column("ingest_lease", JSON),
        ).render_derived(with_types=True).lateral("display_execution")
        statement = select(*fields, *(projected.c[key] for key in keys),
            native_fields.c.execution_id.label("native_execution_id"),
            native_fields.c.ingest_ocr_mode.label("native_mode"),
            native_fields.c.intake_protocol.label("native_protocol"),
            native_fields.c.job_id.label("native_job_id"), native_fields.c.order_id.label("native_order_id"),
        ).select_from(OcrJob).join(projected, true()).join(native_fields, true())
        native_pdf = native_fields.c.selected_pdf_receipt
        version = case((or_(projected.c.workflow_attempt_id.is_(None),
            func.json_typeof(projected.c.workflow_attempt_id) == "null"), native_fields.c.selected_version_input),
            else_=projected.c.selected_version_input)
        version_kind = metrics["selected_version_kind"].as_string()
        version_fields = func.json_to_record(case((version_kind == "object", version),
            else_=literal({}, type_=JSON))).table_valued(
                *(column(key, JSON) for key in _VERSION_KEYS)).render_derived(with_types=True).lateral("display_version")
        statement = statement.join(version_fields, true()).add_columns(
            *(version_fields.c[key].label("version_" + key) for key in _VERSION_KEYS))
        generation = native_fields.c.ingest_lease["generation"]
        native_owner = or_(projected.c.workflow_attempt_id.is_(None),
                           func.json_typeof(projected.c.workflow_attempt_id) == "null")
        receipt = projected.c.selected_pdf_receipt
    else:
        native = metrics["nonworkflow_execution"]
        statement = select(*fields, *(metrics[key].label(key) for key in keys),
            metrics["nonworkflow_execution"]["execution_id"].label("native_execution_id"),
            metrics["nonworkflow_execution"]["ingest_ocr_mode"].label("native_mode"),
            metrics["nonworkflow_execution"]["intake_protocol"].label("native_protocol"),
            metrics["nonworkflow_execution"]["job_id"].label("native_job_id"),
            metrics["nonworkflow_execution"]["order_id"].label("native_order_id"))
        native_pdf = native["selected_pdf_receipt"]
        version = case((metrics["workflow_attempt_id"].as_string().is_(None), native["selected_version_input"]),
            else_=metrics["selected_version_input"])
        version_kind = metrics["selected_version_kind"].as_string()
        statement = statement.add_columns(*(version[key].label("version_" + key) for key in _VERSION_KEYS))
        generation = native["ingest_lease"]["generation"]
        native_owner = metrics["workflow_attempt_id"].as_string().is_(None)
        receipt = metrics["selected_pdf_receipt"]
        statement = statement.select_from(OcrJob)
    statement = statement.add_columns(
        native_pdf.label("native_pdf"), version_kind.label("version_kind"),
        generation.label("native_generation"),
        OcrJob.display_current_document_json.label("current_document"),
        OcrJob.display_native_template_json.label("native_template_version"))
    pdf = case((native_owner, native_pdf), else_=receipt)
    # Keep outer joins: a missing source must remain a visible binding error.
    related = (
        (OrderDocument, pdf["document_id"], ("id", "storage_uri", "source_email_id")),
        (UploadedPdf, pdf["uploaded_pdf_id"], ("id", "storage_uri", "message_id", "content_sha256",
                                               "current_order_id", "current_document_id")),
        (OrderVersion, version["id"], _VERSION_KEYS),
    )
    for model, identity, names in related:
        kind = func.json_typeof(identity) if session.get_bind().dialect.name == "postgresql" else func.json_type(identity)
        string_kind = "string" if session.get_bind().dialect.name == "postgresql" else "text"
        join_identity = case((kind == string_kind, identity.as_string()), else_=None)
        statement = statement.outerjoin(model, model.id == join_identity).add_columns(
            *(getattr(model, name).label(model.__tablename__ + "__" + name) for name in names))
    rows = {row["id"]: dict(row) for row in session.execute(statement.where(OcrJob.id.in_(identities))).mappings()}
    for row in rows.values():
        native_owner = row["workflow_attempt_id"] is None
        row["_pdf"] = row["native_pdf"] if native_owner else row["selected_pdf_receipt"]
        row["_version"] = {key: row["version_" + key] for key in _VERSION_KEYS} if row["version_kind"] == "object" else None
        row["_current_document"] = row["current_document"]
    for row in rows.values():
        for key, (model, _identity, names) in zip(("_document", "_upload", "_version_row"), related):
            values = {name: row.pop(model.__tablename__ + "__" + name) for name in names}
            row[key] = SimpleNamespace(**values, _mapping=values) if values["id"] is not None else None
    return rows


def _job_summary(session, order, state, meta, *, _rows=None):
    identity = meta.get("ocr_job_id")
    if not identity:
        missing = state.state in {"ocr_running", "ocr_completed", "ocr_failed"} or "ocr_job_id" in meta or "ocr_job_attempt_id" in meta
        return None, "ocr_job_not_recorded" if missing else None, None
    if not isinstance(identity, str):
        return None, "ocr_recorded_job_reference_invalid", None
    keys = _JOB_KEYS
    row = (_rows if _rows is not None else read_recorded_job_values(session, [identity])).get(identity)
    if row is None:
        return None, "ocr_recorded_job_missing", None
    if row["order_id"] != order.id:
        return None, "ocr_recorded_job_order_mismatch", None
    if type(row["workflow_attempt_id"]) is not type(meta.get("ocr_job_attempt_id")) or row["workflow_attempt_id"] != meta.get("ocr_job_attempt_id"):
        return None, "ocr_recorded_job_attempt_mismatch", None
    if not isinstance(row["_current_document"], str) or not row["_current_document"]:
        return None, "ocr_recorded_job_input_missing", None
    if row["_current_document"] != order.current_document_id:
        return None, "ocr_recorded_job_document_changed", None
    pdf = row["_pdf"]
    if (not isinstance(pdf, dict) or pdf.get("order_id") != order.id
            or not all(isinstance(pdf.get(key), str) and pdf[key]
                for key in ("document_id", "uploaded_pdf_id", "message_id", "storage_uri", "sha256"))
            or pdf["storage_uri"] != row["input_reference"]):
        return None, "ocr_recorded_job_pdf_mismatch", None
    document, upload = row["_document"], row["_upload"]
    if (document is None or upload is None
            or (document.storage_uri, document.source_email_id) != (pdf["storage_uri"], pdf["message_id"])
            or (upload.storage_uri, upload.message_id, upload.content_sha256) != (pdf["storage_uri"], pdf["message_id"], pdf["sha256"])):
        return None, "ocr_recorded_job_pdf_mismatch", None
    native_owner = row["workflow_attempt_id"] is None
    for recorded, expected in ((row["order_document_id"], pdf["document_id"]),
            (row["uploaded_pdf_id"], pdf["uploaded_pdf_id"]), (row["input_artifact_digest"], pdf["sha256"])):
        if (native_owner or recorded is not None) and recorded != expected:
            return None, "ocr_recorded_job_pdf_mismatch", None
    if native_owner and (upload.current_order_id != order.id or upload.current_document_id != pdf["document_id"]):
        return None, "ocr_recorded_job_pdf_mismatch", None
    version = row["_version"]
    if row["version_kind"] not in {None, "null", "object"}:
        return None, "ocr_recorded_job_pdf_mismatch", None
    if version is None:
        if pdf["document_id"] != order.current_document_id:
            return None, "ocr_recorded_job_document_changed", None
    else:
        from src.services.workflow_attempt_guard_service import _json_snapshot, snapshots_equal
        actual = row["_version_row"]
        # Version identity is checked here; the potentially large line snapshot
        # remains an execution/selection check, not a display-time input proof.
        if (actual is None or actual.order_id != order.id or not isinstance(version, dict)
                or not snapshots_equal(_json_snapshot(dict(actual._mapping)),
                    {key: version.get(key) for key in actual._mapping})):
            return None, "ocr_recorded_job_pdf_mismatch", None
        if version.get("document_id") != pdf["document_id"]:
            return None, "ocr_recorded_job_pdf_mismatch", None
    recorded_template = row["native_template_version"] if native_owner else row["template_version_id"]
    if (recorded_template != state.template_version_id
            or native_owner and row["template_version_id"] is not None and row["template_version_id"] != recorded_template):
        return None, "template_version_mismatch", None
    attempt = row["workflow_attempt_id"]
    if attempt is not None and (not isinstance(attempt, str) or not attempt):
        return None, "ocr_recorded_job_attempt_mismatch", None
    if attempt is None and row["native_mode"] != "hakodate":
        return None, "ocr_recorded_job_input_missing", None
    if attempt is not None and row["native_mode"] is not None:
        return None, "ocr_recorded_job_attempt_mismatch", None
    values = dict(row)
    values["metrics"] = {key: values.pop(key) for key in keys}
    if values.pop("native_mode") == "hakodate":
        if (values["native_job_id"] != identity or values["native_order_id"] != order.id
                or not isinstance(values["native_execution_id"], str) or not values["native_execution_id"]
                or values["native_protocol"] != "native_ingest_v2"):
            return None, "ocr_job_ownership_mismatch", None
        values["metrics"]["nonworkflow_execution"] = {
            "ingest_ocr_mode": "hakodate", "execution_id": values["native_execution_id"],
            "intake_protocol": values["native_protocol"],
            "job_id": values["native_job_id"], "order_id": values["native_order_id"],
        }
    # A recorded terminal summary is not a selectable result or a command proof.
    described = workflow.ocr_job_service.describe_recorded_job_state(values)
    common = workflow._recorded_hospital_job_status(values, described)
    serialized = workflow._serialize_ocr_job(SimpleNamespace(**values))
    serialized["recorded_state"] = described["status"]
    generation = row["native_generation"]
    serialized["recorded_generation"] = generation if type(generation) in {str, int} else None
    return serialized, None, common


def _candidates(session, order, state, meta, *, before_id=None, total_count=None):
    template = workflow._effective_workflow_template_version_id(state, meta) if state else None
    before = None
    if before_id:
        rows = read_history_summary(session, order.id, evidence_id=before_id)
        if not rows:
            raise ValueError("ocr_history_cursor_invalid")
        before = rows[0]
    total = total_count if total_count is not None else session.scalar(select(func.count()).select_from(OrderOcrEvidenceRun).where(
        OrderOcrEvidenceRun.order_id == order.id))
    visible_count = total if state is None else (session.scalar(select(func.count()).select_from(
        OrderOcrEvidenceRun).where(OrderOcrEvidenceRun.order_id == order.id,
        OrderOcrEvidenceRun.template_version_id == template)) if template else 0)
    rows = read_history_summary(session, order.id, limit=26, before=before,
                               template_id=template) if state is None or template else []
    visible = rows[:25]
    next_before_id = visible[-1].id if len(rows) > 25 else None
    if not before_id and state and state.evidence_run_id and all(row.id != state.evidence_run_id for row in visible):
        selected = read_history_summary(session, order.id, evidence_id=state.evidence_run_id, template_id=template)
        if template:
            visible.extend(selected)
    return {
        "order_id": order.id, "workflow_state": state.state if state else "not_initialized",
        "selected_ocr_result_id": state.evidence_run_id if state else None,
        "blockers": list(state.blockers_json or []) if state else ["workflow_not_initialized"],
        "candidate_template_version_id": template,
        "hidden_template_mismatch_result_count": total - visible_count,
        "next_before_id": next_before_id,
        "total_count": total,
        "results": [{"ocr_result_id": row.id, "order_id": row.order_id,
                     "template_version_id": row.template_version_id, "schema_version": row.schema_version,
                     "producer_version": row.producer_version, "source": row.source, "status": row.status,
                     "selected": state is not None and row.id == state.evidence_run_id,
                     "artifact_digest": row.artifact_digest, "created_at": workflow._serialize_datetime(row.created_at),
                     "detail_state": "not_loaded"} for row in visible],
    }


@workflow._measure_inspection
def get_display(order_id, *, scope="summary", history_before_id=None, selected_document_id=None):
    if scope not in {"initial", "summary", "ocr", "sheet", "output", "inspection"}:
        return None, "workflow_display_scope_invalid"
    overlay_reference = None
    with session_scope() as session, session.no_autoflush, retain_read_models(session):
        workflow._inspection_next("display_order")
        identity = workflow._normalize_id(order_id)
        if not identity:
            return None, "order_id_required"
        pair = session.execute(select(Order, OrderWorkflowState).outerjoin(
            OrderWorkflowState, OrderWorkflowState.order_id == Order.id).where(Order.id == identity)).first()
        if pair is None:
            return None, "order_not_found"
        order, state = pair
        error = None
        reads = {}
        meta = workflow._workflow_meta(state, copy_meta=False)
        serialized = workflow._serialize_workflow(state, meta_override=meta) if state else workflow._serialize_uninitialized_workflow(order)
        if state:
            if not workflow._workflow_meta_has_confirmed_context(meta) and not serialized.get("context_suggestion"):
                serialized["context_suggestion"] = workflow._order_context_suggestion(order)
            error = workflow._inspection_call("display_references", _reference_error, session, order, state, meta,
                                              reads=reads, include_overlay=scope in {"sheet", "inspection"})
            serialized = (workflow._workflow_blocker_projection(serialized, error) if error else
                          workflow._apply_canonical_workflow_state_projection(serialized, row=state, meta=meta,
                            state=workflow._canonical_workflow_v2_state(state, meta)))
            serialized["ocr_job"], serialized["ocr_job_binding_error"], serialized["common_job_status"] = workflow._inspection_call("display_job", _job_summary, session, order, state, meta)
        serialized["validation_state"] = "references_only"
        if scope == "initial":
            current_state = serialized.get("state")
            scope = ("ocr" if current_state == "ocr_completed" else
                     "sheet" if current_state == "ocr_selected" else
                     "output" if current_state in {"sheet_saved", "bagging_ready", "bagging_confirmed", "output_review", "confirmed"} else
                     "summary")
        workflow._inspection_next("display_header")
        from src.services.order_serialization_service import order_version_metadata_page
        version_page = order_version_metadata_page(session, order.id, selected_document_id=selected_document_id)
        count = session.scalar(select(func.count()).select_from(
            OrderOcrEvidenceRun).where(OrderOcrEvidenceRun.order_id == order.id))
        result = {"order_id": order.id, "source": "workflow_display/v1", "display_scope": scope,
                  "workflow": serialized, "order_header": {"id": order.id, "document_id": order.current_document_id,
                    "versions": version_page['items'], "version_count": version_page['total'],
                    "versions_next_before_id": version_page['next_before_id'],
                    "current_version": version_page['current_version']},
                  "ocr_result_count": count, "loaded_sections": [],
                  "artifact_lineage": {"selected_ocr_result_id": state.evidence_run_id if state else None,
                    "saved_sheet_id": state.draft_id if state else None,
                    "confirmed_snapshot_id": state.confirmed_snapshot_id if state else None,
                    "bagging_result_id": meta.get("bagging_result_id"), "output_bundle_id": meta.get("output_bundle_id")}}
        if state and state.draft_id:
            saved = reads.get("saved_sheet") if "saved_sheet" in reads else _saved_reference(
                session, OrderSheetDraft, state.draft_id, order.id,
                OrderSheetDraft.base_evidence_run_id, OrderSheetDraft.edited_at, OrderSheetDraft.created_at)
            if saved:
                result["saved_sheet"] = {"saved_sheet_id": saved.id, "source_ocr_result_id": saved.base_evidence_run_id,
                    "edited_at": workflow._serialize_datetime(saved.edited_at), "created_at": workflow._serialize_datetime(saved.created_at)}
        if scope in {"ocr", "sheet", "inspection"}:
            workflow._inspection_next("display_candidates")
            result["ocr_candidates"] = _candidates(session, order, state, meta, before_id=history_before_id, total_count=count)
            result["ocr_results"] = result["ocr_candidates"]["results"]
            result["loaded_sections"].append("ocr")
        if scope in {"sheet", "inspection"}:
            workflow._inspection_next("display_sheet")
            if error:
                return None, error
            if state and state.draft_id:
                draft = session.get(OrderSheetDraft, state.draft_id)
                sheet_data = draft.draft_sheet_json if draft and isinstance(draft.draft_sheet_json, dict) else {}
                target_map = sheet_data.get("target_cell_map")
                needs_evidence = not (isinstance(target_map, list) and target_map)
                evidence = session.get(OrderOcrEvidenceRun, draft.base_evidence_run_id) if draft and needs_evidence else None
                result["saved_sheet"] = workflow._saved_sheet_with_target_cell_map(order=order, workflow=state,
                    saved_sheet=draft, evidence_payload=evidence.payload_json if evidence and evidence.order_id == order.id else None)
                sheet = result["saved_sheet"].get("sheet")
                result["pre_save_checks"] = workflow._workflow_pre_save_checks(meta)
                result["pre_save_status"] = workflow._pre_save_status_for_sheet(meta, workflow._stable_json_hash(sheet)) if isinstance(sheet, dict) else None
            else:
                result["saved_sheet"] = None
            result["loaded_sections"].append("sheet")
        if scope in {"output", "inspection"}:
            workflow._inspection_next("display_output")
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
        if scope in {"sheet", "inspection"} and state and state.evidence_run_id:
            workflow._inspection_next("display_overlay")
            evidence = reads.get("selected_evidence") if "selected_evidence" in reads else _saved_reference(
                session, OrderOcrEvidenceRun, state.evidence_run_id, order.id,
                OrderOcrEvidenceRun.display_overlay_json.label("overlay"))
            if evidence is None:
                return None, "selected_ocr_missing"
            overlay = evidence.overlay if isinstance(evidence.overlay, dict) else {}
            uri = workflow._normalize_id(overlay.get("uri") or overlay.get("overlay_uri"))
            url = workflow._normalize_id(overlay.get("url") or overlay.get("overlay_url"))
            overlay_reference = (evidence.id, uri, url,
                workflow._normalize_id(overlay.get("sheet_review_base_uri")),
                workflow._normalize_id(overlay.get("sheet_review_base_url")))
        workflow._inspection_next("display_session_exit")
    if overlay_reference is not None:
        evidence_id, uri, url, base_uri, base_url = overlay_reference
        if uri and not url:
            url = workflow._normalize_id(workflow._inspection_call(
                "sign_url", getattr(workflow._get_order_service_module(), "_signed_url_from_uri", lambda _uri: None), uri,
            ))
        if base_uri and not base_url:
            base_url = workflow._normalize_id(workflow._inspection_call(
                "sign_url", getattr(workflow._get_order_service_module(), "_signed_url_from_uri", lambda _uri: None), base_uri,
            ))
        preview = {"overlay_url": url or None, "overlay_uri": uri or None,
                   "sheet_review_base_uri": base_uri or None, "sheet_review_base_url": base_url or None,
                   "overlay_status": "ready" if url else "missing",
                   "overlay_message": None if url else "このOCR結果には表示可能なoverlay成果物がありません。"}
        # Preview signing is outside the transaction; it does not validate commands.
        result["ocr_results"] = [{**row, **preview} if row["ocr_result_id"] == evidence_id else row for row in result["ocr_results"]]
        result["ocr_candidates"]["results"] = result["ocr_results"]
    return result, None
