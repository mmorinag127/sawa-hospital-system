"""Serve portal authentication without importing OCR and workbook processing."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from src import db
from src.api import portal
from src.http_middleware import configure_http_middleware
from src.maintenance.formal_migration_schema import require_formal_head


class HospitalApplication:
    def __init__(self):
        self.business = None
        self.business_lock = asyncio.Lock()
        self.auth = FastAPI(openapi_url=None, docs_url=None, redoc_url=None,
                            lifespan=self.lifespan)
        configure_http_middleware(self.auth)
        self.auth.include_router(portal.router)

    @asynccontextmanager
    async def lifespan(self, _app):
        self.business_lock = asyncio.Lock()
        def require_schema():
            with db.engine.connect() as connection:
                require_formal_head(connection)

        await run_in_threadpool(require_schema)
        try:
            yield
        finally:
            async with self.business_lock:
                if self.business is not None:
                    await self.business.router.shutdown()
                    self.business = None

    async def business_app(self):
        if self.business is None:
            async with self.business_lock:
                if self.business is None:
                    def initialize():
                        from src.main import app

                        # Import and synchronous schema/projection initialization
                        # must not occupy the loop serving concurrent auth reads.
                        asyncio.run(app.router.startup())
                        return app

                    self.business = await run_in_threadpool(initialize)
        return self.business

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] == "lifespan" or (scope["type"] == "http" and
                (path == "/portal" or path.startswith("/portal/"))):
            await self.auth(scope, receive, send)
        else:
            business = await self.business_app()
            await business(scope, receive, send)


app = HospitalApplication()
