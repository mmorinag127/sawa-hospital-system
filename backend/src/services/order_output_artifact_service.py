from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from src.models.order_output_artifact import OrderBaggingResult, OrderOutputBundle


def payload_digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def save_bagging_result_artifact(
    session: Session,
    *,
    payload: dict[str, Any],
    created_by: str | None = None,
) -> OrderBaggingResult:
    artifact_id = str(payload.get("bagging_result_id") or "")
    existing = session.get(OrderBaggingResult, artifact_id) if artifact_id else None
    if existing is not None:
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
    session.add(artifact)
    session.flush()
    return artifact


def load_bagging_result_payload(session: Session, artifact_id: str | None) -> dict[str, Any] | None:
    normalized_id = _optional_id(artifact_id)
    if not normalized_id:
        return None
    artifact = session.get(OrderBaggingResult, normalized_id)
    if artifact is None or not isinstance(artifact.payload_json, dict):
        return None
    return deepcopy(artifact.payload_json)


def load_output_bundle_payload(session: Session, artifact_id: str | None) -> dict[str, Any] | None:
    normalized_id = _optional_id(artifact_id)
    if not normalized_id:
        return None
    artifact = session.get(OrderOutputBundle, normalized_id)
    if artifact is None or not isinstance(artifact.payload_json, dict):
        return None
    return deepcopy(artifact.payload_json)


def replace_output_bundle_payload(
    session: Session,
    *,
    artifact_id: str,
    payload: dict[str, Any],
) -> OrderOutputBundle | None:
    artifact = session.get(OrderOutputBundle, _optional_id(artifact_id))
    if artifact is None:
        return None
    artifact.payload_json = deepcopy(payload)
    artifact.payload_digest = payload_digest(payload)
    artifact.materialization_digest = _optional_id(payload.get("materialization_digest"))
    session.flush()
    return artifact


def delete_artifacts_for_order(session: Session, order_id: str) -> None:
    normalized_order_id = _optional_id(order_id)
    if not normalized_order_id:
        return
    session.query(OrderOutputBundle).filter(OrderOutputBundle.order_id == normalized_order_id).delete(synchronize_session=False)
    session.query(OrderBaggingResult).filter(OrderBaggingResult.order_id == normalized_order_id).delete(synchronize_session=False)
    session.flush()


def enrich_workflow_meta_with_artifacts(session: Session, meta: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(meta)
    bagging_result = load_bagging_result_payload(session, _optional_id(enriched.get("bagging_result_id")))
    output_bundle = load_output_bundle_payload(session, _optional_id(enriched.get("output_bundle_id")))
    if bagging_result is not None:
        enriched["bagging_result"] = bagging_result
    if output_bundle is not None:
        enriched["output_bundle"] = output_bundle
    return enriched


def _optional_id(value: object) -> str | None:
    token = str(value or "").strip()
    return token or None
