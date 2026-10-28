from fastapi import FastAPI
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
from src.http_middleware import configure_http_middleware
from src import db
from src.api import staging_datasets
from src.maintenance.formal_migration_schema import require_formal_head
from src.runtime_startup import initialize_runtime as _initialize_menu_schema

app = FastAPI(title="Hospital Order System API")
from src.api import staging_source_probe, staging_source_settings
app.include_router(staging_source_probe.router)
app.include_router(staging_source_settings.router)
configure_http_middleware(app)


@app.exception_handler(SelectedEvidenceRejected)
async def _selected_evidence_rejected(request, exc: SelectedEvidenceRejected):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


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


app.router.add_event_handler("startup", _initialize_menu_schema)
