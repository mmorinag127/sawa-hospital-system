from __future__ import annotations

from datetime import datetime
from contextlib import nullcontext
from typing import Any
from uuid import uuid4

from src.db import session_scope
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_current_state import OrderCurrentState
from src.models.order_workflow_state import OrderWorkflowState
from sqlalchemy import delete
from src.services import ocr_sheet_revision_service
from src.services import order_current_state_service
from src.services.workflow_attempt_guard_service import lock_actual_write_ownership, lock_current_synchronous_cache_write, current_nonworkflow_execution

def _field_value_to_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _field_label(field: str) -> str:
    token = str(field or "").strip()
    return token or "col"


def _make_field_names(width: int) -> list[str]:
    normalized = max(int(width or 0), 1)
    return [f"col{idx + 1}" for idx in range(normalized)]


def _serialize_draft(draft: OrderSheetDraft) -> dict[str, Any]:
    return {
        "id": draft.id,
        "order_id": draft.order_id,
        "template_version_id": draft.template_version_id,
        "base_evidence_run_id": draft.base_evidence_run_id,
        "base_template_resolution_id": draft.base_template_resolution_id,
        "base_menu_snapshot_id": draft.base_menu_snapshot_id,
        "draft_sheet_json": draft.draft_sheet_json if isinstance(draft.draft_sheet_json, dict) else {},
        "draft_state": str(draft.draft_state or "draft").strip() or "draft",
        "blockers_json": list(draft.blockers_json or []),
        "warnings_json": list(draft.warnings_json or []),
        "latest_patch_candidate_id": draft.latest_patch_candidate_id,
        "edited_by": draft.edited_by,
        "edited_at": draft.edited_at.isoformat() if isinstance(draft.edited_at, datetime) else None,
        "created_at": draft.created_at.isoformat() if isinstance(draft.created_at, datetime) else None,
    }


def _build_sheet_from_rows(
    *,
    order_id: str,
    rows: list[list[str]],
    header: list[str] | None,
    source: str,
    base_evidence_run_id: str | None = None,
) -> dict[str, Any] | None:
    normalized_rows = [
        [_field_value_to_str(cell) for cell in row]
        for row in rows
        if isinstance(row, list) and any(_field_value_to_str(cell) for cell in row)
    ]
    width = max((len(row) for row in normalized_rows), default=0)
    if isinstance(header, list):
        width = max(width, len(header))
    if width <= 0:
        return None
    fields = _make_field_names(width)
    normalized_header = [_field_value_to_str(cell) for cell in (header or [])][:width]
    if len(normalized_header) < width:
        normalized_header.extend(fields[len(normalized_header) :])
    padded_rows = [row[:width] + [""] * max(0, width - len(row)) for row in normalized_rows]
    return {
        "order_id": order_id,
        "source": source,
        "fields": fields,
        "header": normalized_header,
        "rows": padded_rows,
        "row_ids": [f"row-{idx + 1}" for idx in range(len(padded_rows))],
        "base_evidence_run_id": base_evidence_run_id,
    }


def _looks_like_markdown_separator(row: list[str]) -> bool:
    if not isinstance(row, list) or not row:
        return False
    normalized = [_field_value_to_str(cell).replace(":", "").replace(" ", "") for cell in row]
    return all(token and set(token) <= {"-"} for token in normalized)


def _parse_table_raw(table_raw: object) -> tuple[list[str], list[list[str]]]:
    if not isinstance(table_raw, str) or not table_raw.strip():
        return [], []
    parsed_rows: list[list[str]] = []
    for raw_line in table_raw.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [_field_value_to_str(cell) for cell in line.strip("|").split("|")]
        parsed_rows.append(cells)
    if not parsed_rows:
        return [], []
    header = parsed_rows[0]
    data_rows = parsed_rows[1:]
    if data_rows and _looks_like_markdown_separator(data_rows[0]):
        data_rows = data_rows[1:]
    return header, data_rows


def _build_sheet_from_payload(
    *,
    order_id: str,
    payload: dict[str, Any],
    source: str,
    base_evidence_run_id: str | None = None,
) -> dict[str, Any] | None:
    own_result = payload.get("_reparse_recognizer_result")
    if isinstance(own_result, dict):
        if not own_result.get("fields") or not isinstance(own_result.get("rows"), list):
            return None
        return {
            "order_id": order_id, "source": source,
            "base_evidence_run_id": base_evidence_run_id,
            "fields": list(own_result["fields"]), "header": list(own_result.get("header") or []),
            "rows": [list(row) for row in own_result["rows"]],
            "row_ids": [f"ocr-row-{index + 1}" for index in range(len(own_result["rows"]))],
        }
    edited_revision = ocr_sheet_revision_service.select_edited_sheet_revision(payload, exact_only=False)
    if isinstance(edited_revision, dict):
        revision_sheet = ocr_sheet_revision_service.build_sheet_payload_from_revision(
            order_id=order_id,
            revision=edited_revision,
            field_label=_field_label,
            field_value_to_str=_field_value_to_str,
        )
        if isinstance(revision_sheet, dict):
            revision_sheet["source"] = "edited_sheet"
            revision_sheet["base_evidence_run_id"] = base_evidence_run_id
            return revision_sheet

    tables = payload.get("tables")
    if isinstance(tables, list):
        for table in tables:
            rows = table.get("rows") if isinstance(table, dict) else None
            if not isinstance(rows, list) or not rows:
                continue
            header = rows[0] if isinstance(rows[0], list) else None
            data_rows = rows[1:] if header else rows
            sheet = _build_sheet_from_rows(
                order_id=order_id,
                rows=data_rows,
                header=header if isinstance(header, list) else None,
                source=source,
                base_evidence_run_id=base_evidence_run_id,
            )
            if isinstance(sheet, dict):
                return sheet

    header, rows = _parse_table_raw(payload.get("table_raw"))
    return _build_sheet_from_rows(
        order_id=order_id,
        rows=rows,
        header=header or None,
        source=source,
        base_evidence_run_id=base_evidence_run_id,
    )


def persist_sheet_draft(
    *,
    order_id: str,
    draft_sheet_json: dict[str, Any],
    base_evidence_run_id: str | None = None,
    template_version_id: str | None = None,
    base_template_resolution_id: str | None = None,
    base_menu_snapshot_id: str | None = None,
    draft_state: str = "draft_ready",
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
    latest_patch_candidate_id: str | None = None,
    edited_by: str | None = None,
    workflow_attempt_id: str | None = None,
    workflow_job_id: str | None = None,
    expected_workflow_revision: int | None = None,
    synchronous_write_input: dict[str, Any] | None = None,
    write_session: Any = None,
) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id or not isinstance(draft_sheet_json, dict):
        return None
    now = datetime.utcnow()
    with (nullcontext(write_session) if write_session is not None else session_scope()) as session:
        if isinstance(synchronous_write_input, dict):
            if workflow_job_id or workflow_attempt_id is not None or expected_workflow_revision is not None:
                return None
            write_allowed = lock_current_synchronous_cache_write(
                session,
                order_id=normalized_order_id,
                expected_order_lines_updated_at=synchronous_write_input.get("order_lines_updated_at"),
                expected_workflow_revision=synchronous_write_input.get("workflow_revision"),
                expected_evidence_run_id=synchronous_write_input.get("evidence_run_id"),
                expected_draft_id=synchronous_write_input.get("draft_id"),
                expected_draft_base_evidence_run_id=synchronous_write_input.get("draft_base_evidence_run_id"),
                expected_cache_exists=synchronous_write_input.get("cache_exists") is True,
                expected_cache_updated_at=synchronous_write_input.get("cache_updated_at"),
                expected_input_versions=synchronous_write_input.get("input_versions"),
            )
            write_allowed = write_allowed and base_evidence_run_id == synchronous_write_input.get("evidence_run_id")
        else:
            if not workflow_job_id:
                return None
            write_allowed = lock_actual_write_ownership(
                session,
                job_id=workflow_job_id,
                workflow_attempt_id=workflow_attempt_id,
                order_id=normalized_order_id,
                expected_workflow_revision=expected_workflow_revision,
            )
        if not write_allowed:
            return None
        evidence = session.get(OrderOcrEvidenceRun, base_evidence_run_id) if base_evidence_run_id else None
        if evidence is None or evidence.order_id != normalized_order_id:
            return None
        if template_version_id != evidence.template_version_id:
            return None
        if synchronous_write_input is not None:
            from src.services.ocr_evidence_service import evidence_run_is_selectable

            if not evidence_run_is_selectable(evidence):
                return None
        if evidence.status == "diagnostic_only" and draft_sheet_json.get("source") != "reparse_candidate":
            return None
        if workflow_job_id:
            execution = (evidence.payload_json or {}).get("_ocr_execution")
            if (
                not isinstance(execution, dict)
                or execution.get("job_id") != workflow_job_id
                or execution.get("workflow_attempt_id") != workflow_attempt_id
                or execution.get("workflow_revision") != expected_workflow_revision
                or execution.get("evidence_run_id") != evidence.id
                or (
                    current_nonworkflow_execution() is not None
                    and execution.get("nonworkflow_execution_id") != current_nonworkflow_execution().get("execution_id")
                )
            ):
                return None
            if execution.get("disposition") == "rejected" and draft_sheet_json.get("source") != "reparse_candidate":
                return None
            draft_sheet_json = {
                **draft_sheet_json, "_ocr_execution": dict(execution), "adopted": False,
            }
        if any(
            draft_sheet_json.get(key) and draft_sheet_json[key] != base_evidence_run_id
            for key in ("base_evidence_run_id", "evidence_run_id")
        ):
            return None
        draft = OrderSheetDraft(
            id=f"ODR{uuid4().hex[:12]}",
            order_id=normalized_order_id,
            template_version_id=str(template_version_id or "").strip() or None,
            base_evidence_run_id=str(base_evidence_run_id or "").strip() or None,
            base_template_resolution_id=str(base_template_resolution_id or "").strip() or None,
            base_menu_snapshot_id=str(base_menu_snapshot_id or "").strip() or None,
            draft_sheet_json=draft_sheet_json,
            draft_state=str(draft_state or "draft_ready").strip() or "draft_ready",
            blockers_json=list(blockers or []),
            warnings_json=list(warnings or []),
            latest_patch_candidate_id=str(latest_patch_candidate_id or "").strip() or None,
            edited_by=str(edited_by or "").strip() or None,
            edited_at=now,
            created_at=now,
        )
        session.add(draft)
        session.flush()
        serialized = _serialize_draft(draft)
        if write_session is not None:
            session.execute(delete(OrderCurrentState).where(OrderCurrentState.order_id == normalized_order_id))
    if write_session is None:
        order_current_state_service.delete_current_state(normalized_order_id)
    return serialized


def get_latest_sheet_draft(order_id: str) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    with session_scope() as session:
        draft = (
            session.query(OrderSheetDraft)
            .filter(OrderSheetDraft.order_id == normalized_order_id)
            .order_by(OrderSheetDraft.edited_at.desc(), OrderSheetDraft.created_at.desc(), OrderSheetDraft.id.desc())
            .first()
        )
        if not draft:
            return None
        return _serialize_draft(draft)


def get_selected_sheet_draft(order_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        draft = session.get(OrderSheetDraft, workflow.draft_id) if workflow and workflow.draft_id else None
        if (
            draft is None or draft.order_id != order_id
            or not workflow.evidence_run_id or draft.base_evidence_run_id != workflow.evidence_run_id
            or draft.template_version_id != workflow.template_version_id
        ):
            return None
        evidence = session.get(OrderOcrEvidenceRun, workflow.evidence_run_id)
        from src.services.ocr_evidence_service import evidence_run_is_selectable

        if (
            evidence is None or evidence.order_id != order_id
            or evidence.template_version_id != workflow.template_version_id
            or not evidence_run_is_selectable(evidence)
        ):
            return None
        payload = draft.draft_sheet_json or {}
        if payload.get("source") == "reparse_candidate" or payload.get("adopted") is False:
            return None
        return _serialize_draft(draft)


def build_initial_sheet_draft(order_id: str) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    # Current-sheet ownership lives in order_service. If an authoritative saved
    # draft already exists, return that exact saved sheet; otherwise delegate the
    # no-draft bootstrap to the canonical sheet builder.
    from src.services import order_service

    latest_draft = get_selected_sheet_draft(normalized_order_id)
    if order_service._draft_record_is_authoritative_current_sheet(latest_draft):
        draft_payload = (
            latest_draft.get("draft_sheet_json")
            if isinstance(latest_draft.get("draft_sheet_json"), dict)
            else latest_draft
        )
        if isinstance(draft_payload, dict):
            return dict(draft_payload)
    return order_service.build_initial_sheet_draft(normalized_order_id)


def build_sheet_draft_from_evidence(
    order_id: str,
    evidence_run: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    payload = None
    base_evidence_run_id = None
    if isinstance(evidence_run, dict):
        if (
            evidence_run.get("order_id") != normalized_order_id
            or not evidence_run.get("id")
            or evidence_run.get("selectable") is not True
        ):
            return None
        payload = evidence_run.get("payload_json") if isinstance(evidence_run.get("payload_json"), dict) else None
        base_evidence_run_id = str(evidence_run.get("id") or "").strip() or None
        if not isinstance(payload, dict):
            return None
    if not isinstance(payload, dict):
        with session_scope() as session:
            workflow = session.get(OrderWorkflowState, normalized_order_id)
            selected = session.get(OrderOcrEvidenceRun, workflow.evidence_run_id) if workflow and workflow.evidence_run_id else None
            if selected and selected.order_id == normalized_order_id and selected.template_version_id == workflow.template_version_id:
                from src.services.ocr_evidence_service import evidence_run_is_selectable

                if not evidence_run_is_selectable(selected):
                    return None
                payload = selected.payload_json
                base_evidence_run_id = selected.id
    if not isinstance(payload, dict):
        return None
    return _build_sheet_from_payload(
        order_id=normalized_order_id,
        payload=payload,
        source="ocr_evidence",
        base_evidence_run_id=base_evidence_run_id,
    )
