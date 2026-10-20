from __future__ import annotations

import hmac
import os

from fastapi import Request
from starlette.responses import JSONResponse


PUBLIC_PATHS = frozenset({"/health"})


def service_auth_enabled() -> bool:
    return bool(os.getenv("SAWA_API_SERVICE_TOKEN", "").strip())


async def require_service_token(request: Request, call_next):
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    expected = os.getenv("SAWA_API_SERVICE_TOKEN", "").strip()
    if not expected:
        return JSONResponse(status_code=503, content={"detail": "API認証が設定されていません"})

    supplied = request.headers.get("x-sawa-service-token", "")
    if not hmac.compare_digest(supplied, expected):
        return JSONResponse(status_code=401, content={"detail": "認証が必要です"})
    return await call_next(request)
