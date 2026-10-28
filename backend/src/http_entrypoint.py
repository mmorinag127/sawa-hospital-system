"""Serve portal authentication without importing OCR and workbook processing."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from src.api import portal
from src.http_middleware import configure_http_middleware
from src.runtime_startup import _startup_step, completed_startup_scope, initialize_runtime


class HospitalApplication:
    def __init__(self):
        self.business = None
        self.business_context = None
        self.runtime_ready = False
        self.business_lock = asyncio.Lock()
        self.auth = FastAPI(openapi_url=None, docs_url=None, redoc_url=None,
                            lifespan=self.lifespan)
        configure_http_middleware(self.auth)
        self.auth.include_router(portal.router)

    @asynccontextmanager
    async def lifespan(self, _app):
        self.business_lock = asyncio.Lock()
        await run_in_threadpool(initialize_runtime)
        self.runtime_ready = True
        try:
            yield
        finally:
            async with self.business_lock:
                try:
                    if self.business_context is not None:
                        await self.business_context.__aexit__(None, None, None)
                finally:
                    self.business = None
                    self.business_context = None
                    self.runtime_ready = False

    def load_business(self):
        from src.main import app, _initialize_menu_schema

        # The shared initializer already ran before the listener became ready.
        # New startup hooks require explicit integration, not silent omission.
        if app.router.on_startup != [_initialize_menu_schema] or _initialize_menu_schema is not initialize_runtime:
            raise RuntimeError("unrecognized business startup lifecycle")
        return app

    async def business_app(self):
        if not self.runtime_ready:
            raise RuntimeError("runtime startup is required")
        if self.business is None:
            async with self.business_lock:
                if self.business is None:
                    business = await run_in_threadpool(_startup_step, "business_import", self.load_business)
                    context = business.router.lifespan_context(business)
                    with completed_startup_scope():
                        await context.__aenter__()
                    self.business_context = context
                    self.business = business
        return self.business

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not self.runtime_ready:
            await JSONResponse({"detail": "runtime startup is required"}, status_code=503)(scope, receive, send)
            return
        path = scope.get("path", "")
        if scope["type"] == "lifespan" or (scope["type"] == "http" and
                (path == "/portal" or path.startswith("/portal/"))):
            await self.auth(scope, receive, send)
        else:
            business = await self.business_app()
            await business(scope, receive, send)


app = HospitalApplication()
