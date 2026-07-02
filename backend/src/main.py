import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
    worker,
    facility_master,
    ocr_registry,
    shipping,
    order_forms,
    totals,
    base_menus,
    system,
    users,
)
from src.services import facility_service, facility_template_version_service, menu_service
from src.services.read_only_request_guard_service import read_only_request_guard
from src.workers.ingest_worker import start_uploaded_pdf_recovery_loop

app = FastAPI(title="Hospital Order System API")


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
app.include_router(shipping.router, prefix="")
app.include_router(order_forms.router, prefix="")
app.include_router(totals.router, prefix="")
app.include_router(worker.router, prefix="")
app.include_router(facility_master.router, prefix="")
app.include_router(ocr_registry.router, prefix="")
app.include_router(base_menus.router, prefix="")
app.include_router(system.router, prefix="")
app.include_router(users.router, prefix="")


@app.on_event("startup")
def _initialize_menu_schema() -> None:
    menu_service.ensure_menu_schema()
    facility_template_version_service.ensure_facility_template_version_schema()
    facility_service.sync_facility_names_from_master()
    start_uploaded_pdf_recovery_loop()
