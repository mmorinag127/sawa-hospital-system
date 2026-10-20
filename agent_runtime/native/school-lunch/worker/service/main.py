from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from service import runtime
from service.shared_backend import ensure_backend_path

ensure_backend_path()

from src.db import initialize_database  # noqa: E402
from src.services import implementation_price_ocr_job_service  # noqa: E402
from src.services import implementation_price_ocr_delivery_service, implementation_price_ocr_dispatch_service  # noqa: E402
from src.services.implementation_price_ocr_delivery_auth import require_delivery_drain_identity, require_worker_identity  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="Sawa Implementation Price OCR Worker", lifespan=lifespan)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "price-ocr-worker",
        "provider": "yomitoku",
        "mode": "async-ocr-job-worker",
    }


@app.get("/health/ocr-runtime")
def ocr_runtime_health():
    try:
        return runtime.check_runtime_available()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/warmup-yomitoku")
def warmup_yomitoku():
    try:
        return runtime.warmup_runtime()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class DeliveryCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1)
    delivery_generation: int = Field(gt=0, strict=True)


@app.post("/jobs/{job_id}/process", dependencies=[Depends(require_worker_identity)])
def process_ocr_job(job_id: str, body: DeliveryCommand):
    if body.job_id != job_id:
        raise HTTPException(status_code=422, detail="Delivery job ID mismatch")
    try:
        result = implementation_price_ocr_job_service.process_ocr_job(job_id, delivery_generation=body.delivery_generation)
    except implementation_price_ocr_job_service.OcrJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="ocr job not found") from exc
    except implementation_price_ocr_job_service.OcrJobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except implementation_price_ocr_dispatch_service.OcrDispatchConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result.job


@app.post("/deliveries/drain", dependencies=[Depends(require_delivery_drain_identity)])
def drain_deliveries(limit: int = Query(default=100, ge=1, le=1000)):
    try:
        return implementation_price_ocr_delivery_service.drain_deliveries(limit=limit)
    except implementation_price_ocr_dispatch_service.OcrDispatchConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
