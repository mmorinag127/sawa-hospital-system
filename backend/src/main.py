import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import (
    health,
    ingest,
    orders,
    menus,
    facilities,
    outputs,
    worker,
    facility_master,
    ocr_registry,
)

app = FastAPI(title="Hospital Order System API")
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
app.include_router(ingest.router, prefix="/ingest")
app.include_router(orders.router, prefix="/orders")
app.include_router(menus.router, prefix="/weekly-menus")
app.include_router(facilities.router, prefix="/facilities")
app.include_router(outputs.router, prefix="/outputs")
app.include_router(worker.router, prefix="")
app.include_router(facility_master.router, prefix="")
app.include_router(ocr_registry.router, prefix="")
