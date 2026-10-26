from contextlib import asynccontextmanager
from threading import Event
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from experiments.agent_assistant import runtime_gateway


def test_portal_gateway_does_not_wait_for_agent_but_never_accepts_early_work():
    preparing, release, initialized, stopped = Event(), Event(), Event(), Event()
    calls = []

    @asynccontextmanager
    async def lifespan(_app):
        initialized.set()
        yield
        stopped.set()

    child = FastAPI(lifespan=lifespan)

    @child.post("/runs")
    async def create(request: Request):
        if request.headers.get("authorization") != "Bearer expected":
            return JSONResponse({}, status_code=401)
        calls.append("create")
        return {"accepted": True}

    def factory():
        preparing.set()
        assert release.wait(10)
        return child

    with TestClient(runtime_gateway.create_gateway(factory)) as client:
        try:
            assert preparing.wait(5)
            response = client.post("/runs", headers={"Authorization": "Bearer expected"})
            assert response.status_code == 503
            assert response.json()["code"] == "agent_initializing"
            assert response.headers["retry-after"] == "5"
            assert calls == []
        finally:
            release.set()
        assert initialized.wait(5)
        assert client.post("/runs").status_code == 401
        assert client.post("/runs", headers={"Authorization": "Bearer expected"}).json() == {"accepted": True}
        assert calls == ["create"]
    assert stopped.is_set()


def test_initialization_failure_never_exposes_or_accepts_business_state(monkeypatch):
    failed = Event()
    monkeypatch.setattr(runtime_gateway, "logging", SimpleNamespace(
        getLogger=lambda _name: SimpleNamespace(error=lambda *_args: failed.set())))

    def factory():
        raise ValueError("private diagnostic")

    with TestClient(runtime_gateway.create_gateway(factory)) as client:
        assert failed.wait(5)
        response = client.post("/runs")
        assert response.status_code == 503
        assert response.json()["code"] == "agent_initialization_failed"
        assert "private diagnostic" not in response.text
