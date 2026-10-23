import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from src.api import (
    auth_config,
    health,
    ingest,
    orders,
    menus,
    menu_masters,
    menu_rules,
    facilities,
    outputs,
    output_history,
    worker,
    facility_master,
    ocr_registry,
    shipping,
    order_forms,
    totals,
    base_menus,
    system,
    portal,
)
from src.services import facility_service, facility_template_version_service, menu_service
from src.services.ocr_evidence_service import SelectedEvidenceRejected
from src.services.read_only_request_guard_service import read_only_request_guard
from src import db
from src.api import staging_datasets
from src.staging_data import DatasetMiddleware
from src.maintenance.formal_migration_schema import require_formal_head

app = FastAPI(title="Hospital Order System API")
from src.api import staging_source_probe, staging_source_settings
app.include_router(staging_source_probe.router)
app.include_router(staging_source_settings.router)
app.add_middleware(DatasetMiddleware)
# Full OCR evidence can exceed Cloud Run's buffered response limit.
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=1)


@app.exception_handler(SelectedEvidenceRejected)
async def _selected_evidence_rejected(request, exc: SelectedEvidenceRejected):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.middleware("http")
async def _block_canonical_writes_during_get(request, call_next):
    if str(request.method or "").upper() != "GET":
        return await call_next(request)
    with read_only_request_guard(method=request.method, path=str(request.url.path)):
        return await call_next(request)


cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "").split(",")
    if origin.strip()
]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.include_router(health.router, prefix="")
app.include_router(auth_config.router, prefix="")
app.include_router(ingest.router, prefix="/ingest")
app.include_router(orders.router, prefix="/orders")
app.include_router(menus.router, prefix="/monthly-menus")
app.include_router(menu_masters.router, prefix="")
app.include_router(menu_rules.router, prefix="/menu-rules")
app.include_router(facilities.router, prefix="/facilities")
app.include_router(outputs.router, prefix="/outputs")
app.include_router(output_history.router, prefix="/outputs")
app.include_router(shipping.router, prefix="")
app.include_router(order_forms.router, prefix="")
app.include_router(totals.router, prefix="")
app.include_router(worker.router, prefix="")
app.include_router(facility_master.router, prefix="")
app.include_router(ocr_registry.router, prefix="")
app.include_router(base_menus.router, prefix="")
app.include_router(system.router, prefix="")
app.include_router(portal.router, prefix="")
app.include_router(staging_datasets.router)


@app.on_event("startup")
def _initialize_menu_schema() -> None:
    with db.engine.connect() as connection:
        require_formal_head(connection)
    menu_service.ensure_menu_schema()
    facility_template_version_service.ensure_facility_template_version_schema()
    facility_service.sync_facility_names_from_master()
