import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import auth_config, health, implementation_price_tables, school_lunch_common, route_diagrams, monthly_procurement
from src.db import initialize_database
from src.api import school_lunch_timing, school_lunch_variants
from src.api import menu_process
from src.api import process_master_transfer
from src.api import process_patterns
from src.api import shared_menus
from src.security import require_service_token
from src.staging_data import DatasetMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="Sawa Implementation Price API", lifespan=lifespan)
app.middleware("http")(require_service_token)
app.add_middleware(DatasetMiddleware)


@app.get("/staging-dataset-ready")
def staging_dataset_ready():
    from fastapi import HTTPException
    from src import staging_data as datasets
    from src.db import get_engine
    from src.migration_catalog import require_runtime_schema
    if not datasets.staging_runtime():
        raise HTTPException(404, "Not found")
    with get_engine().connect() as connection:
        require_runtime_schema(connection)
    return {"dataset": datasets.current_dataset(), "ready": True}

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
        expose_headers=["X-Content-SHA256"],
    )

app.include_router(health.router, prefix="")
app.include_router(auth_config.router, prefix="")
app.include_router(implementation_price_tables.router, prefix="")
app.include_router(school_lunch_common.router, prefix="")
app.include_router(route_diagrams.router)
app.include_router(monthly_procurement.router)
app.include_router(school_lunch_timing.router)
app.include_router(school_lunch_variants.router)
app.include_router(menu_process.router)
app.include_router(process_master_transfer.router)
app.include_router(process_patterns.router)
app.include_router(shared_menus.router)
