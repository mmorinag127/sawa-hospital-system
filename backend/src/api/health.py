from fastapi import APIRouter

from src.services.ingest_job_service import summarize_backlog_metrics

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/health/backlog")
def backlog():
    return summarize_backlog_metrics()
