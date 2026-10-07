import os
from typing import Any

from google.cloud import firestore


UNCLASSIFIED_COLLECTION = os.getenv("OCR_UNCLASSIFIED_COLLECTION", "unclassified")

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
