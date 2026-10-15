"""Read selected saved results for an explicit initial import, without reprocessing."""

from copy import deepcopy
from datetime import date, datetime
import json

from sqlalchemy import select

from src.models.facility_template_version import FacilityTemplateVersion
from src.models.order import Order, OrderLine
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_output_artifact import OrderBaggingResult, OrderOutputBundle
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services.canonical_facility_lineage import digest


SOURCE = "initial_saved_import/v1"
SELECTED_KEYS = (
    "draft_id", "evidence_run_id", "template_version_id", "confirmed_snapshot_id",
    "bagging_result_id", "output_bundle_id",
)


def _id(value, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("initial_saved_source_id_invalid")
    return value


def _json_safe(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return deepcopy(value)


def _selected(snapshot):
    workflow = snapshot["workflow"]
    return {
        "draft_id": workflow["draft_id"],
        "evidence_run_id": workflow["evidence_run_id"],
        "template_version_id": snapshot["order"]["template_version_id"],
        "confirmed_snapshot_id": workflow["confirmed_snapshot_id"],
        "bagging_result_id": snapshot["bagging"]["id"] if snapshot["bagging"] is not None else None,
        "output_bundle_id": snapshot["output"]["id"] if snapshot["output"] is not None else None,
    }


def _current_confirmation(snapshot):
    workflow = snapshot["workflow"]
    if workflow["state"] != "confirmed":
        return None
    confirmed = snapshot["confirmed"]
    if confirmed is None:
        raise ValueError("initial_saved_source_confirmed_missing")
    draft = snapshot["draft"]
    expected = {"id": workflow["confirmed_snapshot_id"], "order_id": snapshot["order"]["id"],
                "template_version_id": snapshot["template"]["id"],
                "bagging_result_id": snapshot["bagging"]["id"] if snapshot["bagging"] is not None else None,
                "output_bundle_id": snapshot["output"]["id"] if snapshot["output"] is not None else None}
    draft_ids = [confirmed[key] for key in ("draft_id", "saved_sheet_id") if confirmed[key] is not None]
    if (not draft_ids or any(value != draft["id"] for value in draft_ids)
            or any(confirmed[key] != value for key, value in expected.items())
            or draft["base_evidence_run_id"] != snapshot["evidence"]["id"]):
        raise ValueError("initial_saved_source_confirmed_selection_mismatch")
    return confirmed


def _explicit_saved_fixture(order, workflow, draft):
    sheet = draft["draft_sheet_json"]
    if not (isinstance(sheet, dict) and sheet.get("source") == "codex_stg_verification"
            and draft.get("edited_by") == "codex-stg-verification"):
        return False
    actions = workflow["secondary_actions_json"]
    metadata = actions.get("workflow_v2", {}) if isinstance(actions, dict) else {}
    if (order["status"] != "sheet_saved" or workflow["state"] != "sheet_saved"
            or draft["draft_state"] != "saved"
            or any(row["template_version_id"] is not None for row in (order, workflow, draft))
            or workflow["evidence_run_id"] is not None or draft["base_evidence_run_id"] is not None
            or workflow["confirmed_snapshot_id"] is not None
            or not isinstance(metadata, dict)
            or any(metadata.get(key) is not None for key in (
                "bagging_result_id", "output_bundle_id", "template_version_id"))
            or any(draft.get(key) is not None for key in (
                "base_menu_snapshot_id", "base_template_resolution_id", "latest_patch_candidate_id"))
            or "row_ids" in sheet
            or sheet.get("resolved_week_id") != order["week_code"]):
        raise ValueError("initial_saved_source_fixture_selection_mismatch")
    return True


def capture_saved_source(session, order_id):
    """Core reads avoid identity-cache state and never flush caller mutations."""
    order_id = _id(order_id)

    def read(model, identity, key="id", *, optional=False):
        identity = _id(identity, optional=optional)
        if identity is None:
            return None
        table = model.__table__
        row = session.execute(select(*table.c).where(table.c[key] == identity)).mappings().one_or_none()
        if row is None:
            raise ValueError("initial_saved_source_missing:" + table.name)
        return dict(row)

    with session.no_autoflush:
        order = read(Order, order_id)
        workflow = read(OrderWorkflowState, order_id, "order_id")
        draft = read(OrderSheetDraft, workflow["draft_id"])
        fixture = _explicit_saved_fixture(order, workflow, draft)
        evidence = read(OrderOcrEvidenceRun, workflow["evidence_run_id"], optional=fixture)
        template = read(FacilityTemplateVersion, order["template_version_id"], optional=fixture)
        confirmed = read(OrderConfirmedSnapshot, workflow["confirmed_snapshot_id"], optional=True)
        actions = workflow["secondary_actions_json"]
        if actions is not None and not isinstance(actions, dict):
            raise ValueError("initial_saved_source_selection_invalid")
        metadata = actions.get("workflow_v2") if actions is not None else None
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("initial_saved_source_selection_invalid")
        bagging_id = metadata.get("bagging_result_id") if metadata is not None else None
        output_id = metadata.get("output_bundle_id") if metadata is not None else None
        bagging = read(OrderBaggingResult, bagging_id, optional=True)
        output = read(OrderOutputBundle, output_id, optional=True)
        lines = OrderLine.__table__
        line_rows = [dict(row) for row in session.execute(
            select(*lines.c).where(lines.c.order_id == order_id).order_by(lines.c.id)
        ).mappings()]

    for row in (workflow, draft, evidence, confirmed, bagging, output):
        if row is not None and row["order_id"] != order_id:
            raise ValueError("initial_saved_source_foreign_order")
    if not fixture and template["facility_id"] != order["facility_code"]:
        raise ValueError("initial_saved_source_foreign_facility")
    if not fixture and any(row["template_version_id"] != template["id"] for row in (order, workflow, draft, evidence)):
        raise ValueError("initial_saved_source_template_mismatch")
    if not fixture and draft["base_evidence_run_id"] != evidence["id"]:
        raise ValueError("initial_saved_source_evidence_mismatch")
    sheet = draft["draft_sheet_json"]
    if (draft["draft_state"] != "saved" or not isinstance(sheet, dict)
            or any(not isinstance(sheet.get(key), list) or not sheet[key] for key in (("fields", "rows") if fixture else ("fields", "rows", "row_ids")))
            or any(not isinstance(row, list) for row in sheet["rows"])
            or (not fixture and (len(sheet["rows"]) != len(sheet["row_ids"])
                or any(not isinstance(value, str) or not value for value in sheet["row_ids"])
                or len(set(sheet["row_ids"])) != len(sheet["row_ids"])))):
        raise ValueError("initial_saved_source_saved_sheet_invalid")
    for row in (bagging, output):
        if row is not None and (row["source_saved_sheet_id"] != draft["id"]
                                or row["source_ocr_result_id"] != evidence["id"]
                                or row["template_version_id"] != template["id"]):
            raise ValueError("initial_saved_source_output_selection_mismatch")
    if output is not None and (bagging is None or output["source_bagging_result_id"] != bagging["id"]):
        raise ValueError("initial_saved_source_output_selection_mismatch")
    snapshot = _json_safe(dict(order=order, workflow=workflow, draft=draft, evidence=evidence,
                               template=template, confirmed=confirmed, bagging=bagging,
                               output=output, lines=line_rows))
    json.dumps(snapshot, allow_nan=False)
    _current_confirmation(snapshot)
    return snapshot


def source_receipt(snapshot):
    """Hash at import time; this is not proof of past OCR or a new approval."""
    hashed = deepcopy(snapshot)
    actions = hashed["workflow"]["secondary_actions_json"]
    if isinstance(actions, dict):
        actions.pop("_initial_saved_import_receipt", None)
    return {"source": SOURCE, "order_id": snapshot["order"]["id"],
            "selected": _selected(snapshot), "source_sha256": digest(hashed)}


def validate_saved_source(session, receipt):
    try:
        if (not isinstance(receipt, dict)
                or set(receipt) != {"source", "order_id", "selected", "source_sha256"}
                or receipt["source"] != SOURCE
                or not isinstance(receipt["selected"], dict)
                or set(receipt["selected"]) != set(SELECTED_KEYS)):
            raise ValueError("invalid receipt")
        _id(receipt["order_id"])
        snapshot = capture_saved_source(session, receipt["order_id"])
        for key in SELECTED_KEYS:
            _id(receipt["selected"][key], optional=key in {
                "confirmed_snapshot_id", "bagging_result_id", "output_bundle_id"}
                or (snapshot["evidence"] is None and key in {"evidence_run_id", "template_version_id"}))
        if receipt != source_receipt(snapshot):
            raise ValueError("changed receipt")
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("initial_saved_source_changed") from exc
    return snapshot


def current_sheet_payload(snapshot):
    return deepcopy(snapshot["draft"]["draft_sheet_json"])


def accepted_output_payload(snapshot):
    if snapshot["evidence"] is None:
        raise ValueError("selected_ocr_required")
    bagging = snapshot["bagging"]
    if not isinstance(bagging, dict) or not isinstance(bagging.get("payload_json"), dict):
        raise ValueError("initial_saved_source_output_invalid")
    payload = bagging["payload_json"]
    if (not isinstance(payload.get("materialization_candidate"), dict)
            or not isinstance(payload.get("bag_rows"), list)
            or any(not isinstance(row, dict) for row in payload["bag_rows"])):
        raise ValueError("initial_saved_source_output_invalid")
    confirmed = _current_confirmation(snapshot)
    if confirmed is not None and not isinstance(confirmed.get("snapshot_json"), dict):
        raise ValueError("initial_saved_source_output_invalid")
    return deepcopy({"materialization_candidate": payload["materialization_candidate"],
                     "bag_rows": payload["bag_rows"],
                     "confirmed_snapshot": confirmed["snapshot_json"] if confirmed is not None else None})
