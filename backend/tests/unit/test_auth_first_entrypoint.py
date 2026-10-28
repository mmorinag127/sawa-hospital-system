import asyncio
import json
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import http_entrypoint as entry
from src.api import auth


def test_auth_import_does_not_import_business_or_workbook_processing():
    command = (
        "import json,sys; import src.http_entrypoint; "
        "print(json.dumps([name for name in sys.modules if name == 'src.main' "
        "or name.startswith(('openpyxl', 'src.services.order_service', 'src.workers.ingest_worker'))]))"
    )
    result = subprocess.run([sys.executable, "-c", command], check=True, capture_output=True,
                            text=True, timeout=20)
    assert json.loads(result.stdout) == []


def test_formal_schema_failure_still_prevents_startup(monkeypatch):
    monkeypatch.setattr(entry.db.engine, "connect", MagicMock())

    def reject(_connection):
        raise RuntimeError("formal schema mismatch")

    monkeypatch.setattr(entry, "require_formal_head", reject)
    with pytest.raises(RuntimeError, match="formal schema mismatch"):
        with TestClient(entry.HospitalApplication()):
            pytest.fail("Failed formal schema must not serve authentication")


def test_auth_denial_is_served_while_business_startup_is_blocked(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setattr(auth, "AUTH_PROVIDER", "local")
    started, released = threading.Event(), threading.Event()
    calls = []
    business = FastAPI()

    @business.on_event("startup")
    def initialize():
        calls.append("startup")
        started.set()
        assert released.wait(5)

    @business.get("/probe")
    def probe():
        return {"unchanged": True}

    monkeypatch.setitem(sys.modules, "src.main", SimpleNamespace(app=business))

    async def exercise():
        application = entry.HospitalApplication()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://fixture.invalid") as client:
            pending = asyncio.create_task(client.get("/probe"))
            try:
                assert await asyncio.to_thread(started.wait, 3)
                denied = await asyncio.wait_for(client.get("/portal/auth/me"), timeout=1)
                assert denied.status_code == 401
                assert application.business is None
            finally:
                released.set()
                result = await pending
            assert result.status_code == 200
            assert result.json() == {"unchanged": True}
            assert (await client.get("/probe")).json() == result.json()
            assert calls == ["startup"]

    asyncio.run(exercise())


def test_business_startup_failure_is_not_cached_as_success(monkeypatch):
    business = FastAPI()
    attempts = []

    @business.on_event("startup")
    def reject():
        attempts.append(1)
        raise RuntimeError("business schema unavailable")

    monkeypatch.setitem(sys.modules, "src.main", SimpleNamespace(app=business))

    async def exercise():
        application = entry.HospitalApplication()
        for _ in range(2):
            with pytest.raises(RuntimeError, match="business schema unavailable"):
                await application.business_app()
            assert application.business is None
        assert len(attempts) == 2

    asyncio.run(exercise())


def test_business_shutdown_runs_once_after_formal_startup(monkeypatch):
    business = FastAPI()
    calls = []
    monkeypatch.setattr(entry.db.engine, "connect", MagicMock())
    monkeypatch.setattr(entry, "require_formal_head", lambda _connection: calls.append("formal"))

    @business.on_event("startup")
    def startup():
        calls.append("business")

    @business.on_event("shutdown")
    def shutdown():
        calls.append("shutdown")

    monkeypatch.setitem(sys.modules, "src.main", SimpleNamespace(app=business))

    async def exercise():
        application = entry.HospitalApplication()
        async with application.lifespan(application.auth):
            first, second = await asyncio.gather(application.business_app(), application.business_app())
            assert first is second is business
        assert application.business is None
        assert calls == ["formal", "business", "shutdown"]

    asyncio.run(exercise())
