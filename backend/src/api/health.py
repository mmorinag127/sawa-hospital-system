from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/health/backlog")
def backlog():
    # Placeholder using in-memory queue sizes if available
    return {"ingest_queue_depth": 0, "exports_queue_depth": 0, "oldest_pending_seconds": 0}
