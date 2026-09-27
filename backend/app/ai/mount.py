"""Attach the AI module to the application. Called only when FTP_AI_MODULE=1."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.core.config import settings

log = logging.getLogger(__name__)


def mount(app: FastAPI) -> None:
    from app.ai.api import router
    from app.ai.permissions import register

    register()
    app.include_router(router, prefix=settings.API_V1_PREFIX)
    log.info("AI module mounted")
