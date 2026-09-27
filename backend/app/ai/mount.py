"""Attach the AI module to the application. Called only when FTP_AI_MODULE=1."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.core.config import settings

log = logging.getLogger(__name__)


def mount(app: FastAPI) -> None:
    from app.ai.api import router
    from app.ai.permissions import register

    from app.ai import scheduler

    register()
    app.include_router(router, prefix=settings.API_V1_PREFIX)
    # Started with the server, never at import; `scheduler.start` also
    # declines outside an ASGI server (see there).
    app.router.add_event_handler("startup", scheduler.start)
    app.router.add_event_handler("shutdown", scheduler.stop)
    log.info("AI module mounted")
