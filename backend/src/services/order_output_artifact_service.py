from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from src.models.order_output_artifact import OrderBaggingResult, OrderOutputBundle


def payload_digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class BaggingReceiptInvalid(ValueError):
    """A saved bagging candidate has no consistent materialization receipt."""


def validate_bagging_receipt(payload: object) -> str:
    if not isinstance(payload, dict) or not isinstance(payload.get("materialization_candidate"), dict):
        raise BaggingReceiptInvalid("bagging_result_materialization_missing")
    candidate = payload["materialization_candidate"]
    configured = candidate.get("configured_input")
    if (not isinstance(configured, dict)
            or not isinstance(configured.get("facility"), dict)
            or not isinstance(configured.get("policy"), dict)):
        raise BaggingReceiptInvalid("bagging_result_configuration_missing")
    digest = payload.get("materialization_digest")
    if not isinstance(digest, str) or not digest.strip():
        raise BaggingReceiptInvalid("bagging_result_materialization_digest_missing")
    from src.services.order_workflow_v2_service import _canonical_payload_digest
    try:
        actual_digest = _canonical_payload_digest(candidate)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise BaggingReceiptInvalid("bagging_result_materialization_invalid") from exc
    if digest != actual_digest:
        raise BaggingReceiptInvalid("bagging_result_materialization_digest_mismatch")
    return digest


def save_bagging_result_artifact(
    session: Session,
    *,
    payload: dict[str, Any],
    created_by: str | None = None,
) -> OrderBaggingResult:
    artifact_id = str(payload.get("bagging_result_id") or "")
    existing = session.get(OrderBaggingResult, artifact_id) if artifact_id else None
    if existing is not None:
        if load_bagging_result_payload(session, artifact_id, expected_order_id=payload.get("order_id")) != payload:
            raise ValueError("bagging_result_artifact_conflict")
        return existing
    artifact = OrderBaggingResult(
        id=artifact_id,
        order_id=str(payload.get("order_id") or ""),
        source_saved_sheet_id=str(payload.get("source_saved_sheet_id") or ""),
        source_ocr_result_id=_optional_id(payload.get("source_ocr_result_id")),
        template_version_id=_optional_id(payload.get("template_version_id")),
        payload_json=deepcopy(payload),
        payload_digest=payload_digest(payload),
        created_by=_optional_id(created_by),
    )
    if not _valid_payload(artifact, payload, expected_order_id=payload.get("order_id")):
        raise ValueError("bagging_result_artifact_invalid")
    session.add(artifact)
    session.flush()
    return artifact


def save_output_bundle_artifact(
    session: Session,
    *,
    payload: dict[str, Any],
    created_by: str | None = None,
) -> OrderOutputBundle:
    artifact_id = str(payload.get("output_bundle_id") or "")
    existing = session.get(OrderOutputBundle, artifact_id) if artifact_id else None
    if existing is not None:
        if load_output_bundle_payload(session, artifact_id, expected_order_id=payload.get("order_id")) != payload:
            raise ValueError("output_bundle_artifact_conflict")
        return existing
    artifact = OrderOutputBundle(
        id=artifact_id,
        order_id=str(payload.get("order_id") or ""),
        source_bagging_result_id=str(payload.get("source_bagging_result_id") or ""),
        source_saved_sheet_id=str(payload.get("source_saved_sheet_id") or ""),
        source_ocr_result_id=_optional_id(payload.get("source_ocr_result_id")),
        template_version_id=_optional_id(payload.get("template_version_id")),
        materialization_digest=_optional_id(payload.get("materialization_digest")),
        payload_json=deepcopy(payload),
        payload_digest=payload_digest(payload),
        created_by=_optional_id(created_by),
    )
    if not _valid_payload(artifact, payload, expected_order_id=payload.get("order_id")):
        raise ValueError("output_bundle_artifact_invalid")
    session.add(artifact)
    session.flush()
    return artifact


def load_bagging_result_payload(session: Session, artifact_id: str | None, *, expected_order_id: str) -> dict[str, Any] | None:
    normalized_id = _optional_id(artifact_id)
    if not normalized_id:
        return None
    artifact = session.get(OrderBaggingResult, normalized_id, populate_existing=True)
    if artifact is None or not _valid_payload(artifact, artifact.payload_json, expected_order_id=expected_order_id):
        return None
    return deepcopy(artifact.payload_json)


def load_output_bundle_payload(session: Session, artifact_id: str | None, *, expected_order_id: str) -> dict[str, Any] | None:
    normalized_id = _optional_id(artifact_id)
    if not normalized_id:
        return None
    artifact = session.get(OrderOutputBundle, normalized_id, populate_existing=True)
    if artifact is None or not _valid_payload(artifact, artifact.payload_json, expected_order_id=expected_order_id):
        return None
    return deepcopy(artifact.payload_json)


def replace_output_bundle_payload(
    session: Session,
    *,
    artifact_id: str,
    payload: dict[str, Any],
    expected_order_id: str,
) -> OrderOutputBundle | None:
    if load_output_bundle_payload(session, artifact_id, expected_order_id=expected_order_id) is None:
        raise ValueError("output_bundle_artifact_missing_or_invalid")
    artifact = session.get(OrderOutputBundle, _optional_id(artifact_id))
    if not _valid_payload(artifact, payload, expected_order_id=expected_order_id, check_digest=False):
        raise ValueError("output_bundle_artifact_identity_mismatch")
    artifact.payload_json = deepcopy(payload)
    artifact.payload_digest = payload_digest(payload)
    session.flush()
    return artifact


def delete_artifacts_for_order(session: Session, order_id: str) -> None:
    normalized_order_id = _optional_id(order_id)
    if not normalized_order_id:
        return
    session.query(OrderOutputBundle).filter(OrderOutputBundle.order_id == normalized_order_id).delete(synchronize_session=False)
    session.query(OrderBaggingResult).filter(OrderBaggingResult.order_id == normalized_order_id).delete(synchronize_session=False)
    session.flush()


def enrich_workflow_meta_with_artifacts(session: Session, meta: dict[str, Any], *, expected_order_id: str) -> dict[str, Any]:
    enriched = dict(meta)
    enriched["bagging_result"] = load_bagging_result_payload(session, _optional_id(enriched.get("bagging_result_id")), expected_order_id=expected_order_id)
    enriched["output_bundle"] = load_output_bundle_payload(session, _optional_id(enriched.get("output_bundle_id")), expected_order_id=expected_order_id)
    return enriched


def _valid_payload(artifact: Any, payload: object, *, expected_order_id: str, check_digest: bool = True) -> bool:
    if not isinstance(payload, dict) or not isinstance(expected_order_id, str) or not expected_order_id.strip():
        return False
    if artifact.order_id != expected_order_id:
        return False
    is_output = isinstance(artifact, OrderOutputBundle)
    required = {"output_bundle_id" if is_output else "bagging_result_id": "id",
                "order_id": "order_id", "source_saved_sheet_id": "source_saved_sheet_id"}
    if is_output:
        required["source_bagging_result_id"] = "source_bagging_result_id"
    for key, column in required.items():
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip() or value != getattr(artifact, column):
            return False
    if is_output:
        materialization_digest = payload.get("materialization_digest")
        if not isinstance(materialization_digest, str) or not materialization_digest.strip():
            return False
    else:
        try:
            validate_bagging_receipt(payload)
        except BaggingReceiptInvalid:
            return False
    optional = ["source_ocr_result_id", "template_version_id"]
    if is_output:
        optional.append("materialization_digest")
    for key in optional:
        value = payload.get(key)
        if value is not None and not isinstance(value, str):
            return False
        if _optional_id(value) != getattr(artifact, key):
            return False
    try:
        digest = payload_digest(payload)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False
    return not check_digest or digest == artifact.payload_digest


def confirmed_snapshot_artifact_payloads(
    session: Session, snapshot: Any, *, expected_order_id: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    payload = snapshot.snapshot_json
    if snapshot.order_id != expected_order_id or not isinstance(payload, dict):
        return None
    try:
        if snapshot.snapshot_digest != payload_digest(payload):
            return None
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None
    if payload.get("order_id") != expected_order_id or payload.get("saved_sheet_id") != snapshot.saved_sheet_id or payload.get("template_version_id") != snapshot.template_version_id:
        return None
    if not snapshot.saved_sheet_id or snapshot.draft_id != snapshot.saved_sheet_id:
        return None
    bagging = load_bagging_result_payload(session, snapshot.bagging_result_id, expected_order_id=expected_order_id)
    output = load_output_bundle_payload(session, snapshot.output_bundle_id, expected_order_id=expected_order_id)
    if bagging is None or output is None:
        return None
    if payload.get("bagging_result") != bagging or output.get("source_bagging_result_id") != snapshot.bagging_result_id:
        return None
    for artifact in (bagging, output):
        if artifact.get("source_saved_sheet_id") != snapshot.saved_sheet_id or artifact.get("template_version_id") != snapshot.template_version_id:
            return None
    # final_confirm captures the snapshot before adding its reference to the DB artifact.
    if output.get("confirmed_snapshot_id") != snapshot.id:
        return None
    captured_output = payload.get("output_bundle")
    if not isinstance(captured_output, dict):
        return None
    comparable_output = deepcopy(output)
    if "confirmed_snapshot_id" not in captured_output:
        comparable_output.pop("confirmed_snapshot_id", None)
    if captured_output != comparable_output:
        return None
    return bagging, output


def _optional_id(value: object) -> str | None:
    token = str(value or "").strip()
    return token or None
