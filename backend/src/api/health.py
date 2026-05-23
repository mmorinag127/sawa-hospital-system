from fastapi import APIRouter, Depends

from src.api.auth import require_role
from src.services.ingest_job_service import summarize_backlog_metrics

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/health/backlog", dependencies=[Depends(require_role("operator"))])
def backlog():
    return summarize_backlog_metrics()
