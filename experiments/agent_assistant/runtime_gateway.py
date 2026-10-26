"""Keep ordinary portal startup independent of isolated-agent restoration."""
import asyncio
from contextlib import asynccontextmanager
import logging

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount


def create_gateway(factory):
    ready = None
    failed = False

    async def dispatch(scope, receive, send):
        if ready is not None:
            await ready(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1013})
            return
        response = JSONResponse(
            {"detail": "エージェントの準備に失敗しました。保存済み内容は保持されています。" if failed
             else "エージェントを準備しています。業務操作はまだ受け付けていません。",
             "code": "agent_initialization_failed" if failed else "agent_initializing"},
            status_code=503,
            headers={"Retry-After": "5", "Cache-Control": "no-store"},
        )
        await response(scope, receive, send)

    @asynccontextmanager
    async def lifespan(_app):
        stopped = asyncio.Event()

        async def initialize():
            nonlocal ready, failed
            try:
                child = await asyncio.to_thread(factory)
                async with child.router.lifespan_context(child):
                    ready = child
                    await stopped.wait()
                    ready = None
            except Exception as exc:
                ready = None
                failed = True
                logging.getLogger(__name__).error("Agent initialization failed (%s)", type(exc).__name__)

        task = asyncio.create_task(initialize())
        try:
            yield
        finally:
            stopped.set()
            await task

    return Starlette(routes=[Mount("/", app=dispatch)], lifespan=lifespan)
