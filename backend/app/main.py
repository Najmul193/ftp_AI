"""Application entrypoint."""

from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import (
    analytics, audit, auth, config, dashboard, org, products, system, uploads,
)
from app.core.config import settings
from app.core.db import healthcheck
from app.domain.errors import DomainError
from app.domain.scope import ScopeViolation

app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    description=(
        "Funds Transfer Pricing platform. Replaces the FTP1.xlsm workbook with "
        "a governed, auditable, multi-user system."
    ),
    docs_url="/docs",
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Correlate API, worker and audit records through one request id."""
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    response.headers["x-response-time-ms"] = f"{(time.perf_counter()-started)*1000:.1f}"
    return response


@app.exception_handler(ScopeViolation)
async def _scope_violation(_request: Request, exc: ScopeViolation):
    """403, never an empty 200 -- see `deps.scope_violation_to_403`."""
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(DomainError)
async def _domain_error(_request: Request, exc: DomainError):
    return JSONResponse(
        status_code=422,
        content={"detail": exc.message, "code": exc.code, "field": exc.field},
    )


@app.get("/health", tags=["system"])
def health():
    """Deep check: the API is only ready if its database is."""
    try:
        db_ok = healthcheck()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=503,
                            content={"status": "unhealthy", "database": str(exc)})
    return {"status": "ok", "database": db_ok, "environment": settings.ENVIRONMENT}


for r in (auth.router, dashboard.router, analytics.router, org.router,
          products.router, config.router, audit.router, uploads.router,
          system.router):
    app.include_router(r, prefix=settings.API_V1_PREFIX)

# Optional AI module. Off, `app.ai` is never imported: no route, job,
# permission or table of its exists, and the platform is unchanged.
if settings.AI_MODULE:
    from app.ai.mount import mount as mount_ai

    mount_ai(app)
