"""HTTP protections shared by the authentication and business applications."""
import os

from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.responses import JSONResponse

from src.services.read_only_request_guard_service import read_only_request_guard
from src.staging_data import DatasetMiddleware


def configure_http_middleware(app):
    app.add_middleware(DatasetMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=1)

    @app.middleware("http")
    async def block_canonical_writes_during_get(request, call_next):
        if os.environ.get("SAWA_RELEASE_READ_ONLY") == "1" and request.method not in {"GET", "HEAD", "OPTIONS"}:
            return JSONResponse({"detail": "release_maintenance"}, status_code=503,
                                headers={"Retry-After": "300", "Cache-Control": "no-store"})
        if str(request.method or "").upper() != "GET":
            return await call_next(request)
        with read_only_request_guard(method=request.method, path=str(request.url.path)):
            return await call_next(request)

    origins = [item.strip() for item in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if item.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])
