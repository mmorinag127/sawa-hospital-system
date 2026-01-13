import os
from datetime import datetime, timezone
from typing import Any

from google.cloud import firestore


TEMPLATE_COLLECTION = os.getenv("OCR_TEMPLATE_COLLECTION", "templates")
UNCLASSIFIED_COLLECTION = os.getenv("OCR_UNCLASSIFIED_COLLECTION", "unclassified")
JOBS_COLLECTION = os.getenv("OCR_JOBS_COLLECTION", "jobs")
FACILITY_COLLECTION = os.getenv("OCR_FACILITY_COLLECTION", "facilities")

_CLIENT: firestore.Client | None = None


def _project_id() -> str | None:
    return (
        os.getenv("GCP_PROJECT_ID")
        or os.getenv("GCP_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or None
    )


def _client() -> firestore.Client:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = firestore.Client(project=_project_id())
    return _CLIENT


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_templates(limit: int = 100) -> list[dict[str, Any]]:
    docs = _client().collection(TEMPLATE_COLLECTION).limit(limit).stream()
    return [{"id": doc.id, "data": doc.to_dict() or {}} for doc in docs]


def get_template(template_id: str) -> dict[str, Any] | None:
    doc = _client().collection(TEMPLATE_COLLECTION).document(template_id).get()
    if not doc.exists:
        return None
    return {"id": doc.id, "data": doc.to_dict() or {}}


def save_template(template_id: str, data: dict) -> dict[str, Any]:
    payload = dict(data)
    payload.setdefault("template_id", template_id)
    payload["updated_at"] = _utc_now()
    if "created_at" not in payload:
        payload["created_at"] = payload["updated_at"]
    _client().collection(TEMPLATE_COLLECTION).document(template_id).set(payload)
    return {"id": template_id, "data": payload}


def delete_template(template_id: str) -> bool:
    _client().collection(TEMPLATE_COLLECTION).document(template_id).delete()
    return True


def list_unclassified(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    query = _client().collection(UNCLASSIFIED_COLLECTION)
    if status:
        query = query.where("status", "==", status)
    docs = query.limit(limit).stream()
    return [{"id": doc.id, "data": doc.to_dict() or {}} for doc in docs]


def get_unclassified(job_id: str) -> dict[str, Any] | None:
    doc = _client().collection(UNCLASSIFIED_COLLECTION).document(job_id).get()
    if not doc.exists:
        return None
    return {"id": doc.id, "data": doc.to_dict() or {}}


def resolve_unclassified(job_id: str, template_id: str, note: str | None = None) -> dict[str, Any]:
    payload = {
        "status": "resolved",
        "resolved_template_id": template_id,
        "resolved_at": _utc_now(),
    }
    if note:
        payload["resolved_note"] = note
    _client().collection(UNCLASSIFIED_COLLECTION).document(job_id).set(payload, merge=True)
    _client().collection(JOBS_COLLECTION).document(job_id).set(
        {"resolved_template_id": template_id, "resolved_at": payload["resolved_at"]},
        merge=True,
    )
    return payload


def list_facilities(limit: int = 100) -> list[dict[str, Any]]:
    docs = _client().collection(FACILITY_COLLECTION).limit(limit).stream()
    return [{"id": doc.id, "data": doc.to_dict() or {}} for doc in docs]


def get_facility(facility_id: str) -> dict[str, Any] | None:
    doc = _client().collection(FACILITY_COLLECTION).document(facility_id).get()
    if not doc.exists:
        return None
    return {"id": doc.id, "data": doc.to_dict() or {}}


def save_facility(facility_id: str, data: dict) -> dict[str, Any]:
    payload = dict(data)
    payload.setdefault("facility_id", facility_id)
    payload["updated_at"] = _utc_now()
    if "created_at" not in payload:
        payload["created_at"] = payload["updated_at"]
    _client().collection(FACILITY_COLLECTION).document(facility_id).set(payload)
    return {"id": facility_id, "data": payload}


def delete_facility(facility_id: str) -> bool:
    _client().collection(FACILITY_COLLECTION).document(facility_id).delete()
    return True
