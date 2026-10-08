from __future__ import annotations

import hashlib
import json
from typing import Any


def build_position_fallback_artifacts(
    payload: dict[str, Any] | None,
    template: dict[str, Any] | None,
    *,
    template_id: str | None = None,
) -> dict[str, Any] | None:
    return None


def augment_payload_with_position_fallback(
    payload: dict[str, Any] | None,
    template: dict[str, Any] | None,
    *,
    template_id: str | None = None,
) -> dict[str, Any] | None:
    return payload


def candidate_resolution_uses_position_fallback(candidate_resolution: dict[str, Any] | None) -> bool:
    return False


def payload_uses_ready_position_fallback(
    payload: dict[str, Any] | None,
    *,
    template: dict[str, Any] | None = None,
) -> bool:
    return False


def payload_uses_partial_position_fallback(payload: dict[str, Any] | None) -> bool:
    return False


def payload_position_fallback_requires_choice(payload: dict[str, Any] | None) -> bool:
    return False


def stable_mapping_signature(value: object) -> str:
    return hashlib.sha1(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
