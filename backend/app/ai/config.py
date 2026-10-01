"""The AI module's own settings. Read only when the module is loaded.

Kept out of `app.core.config` so the platform's settings carry exactly one AI
line -- the flag that decides whether this package is imported at all.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import settings as core


class AiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",
                                      extra="ignore")

    #: Fernet key that encrypts provider API keys at rest. Required in
    #: production; in development a key derived from SECRET_KEY is used so the
    #: module runs out of the box, with a warning.
    AI_KEY_ENCRYPTION_KEY: str = ""
    #: Shared secret for `POST /ai/jobs/tick`, which an external scheduler can
    #: call to wake a host that sleeps when idle.
    AI_JOBS_TOKEN: str = ""
    #: Run the in-process scheduler (market collectors, insight jobs).
    AI_SCHEDULER_ENABLED: bool = True
    #: FRED (St. Louis Fed) API key -- free; global rates are skipped without it.
    FRED_API_KEY: str = ""
    #: Outbound HTTP timeout for providers and market sources, seconds.
    AI_HTTP_TIMEOUT: float = 60.0
    #: Days the egress log is kept before the retention job may purge it.
    AI_REQUEST_RETENTION_DAYS: int = 365
    #: How long public data is kept; the daily prune job deletes anything
    #: older, so the tables stop growing. A series' latest value is always
    #: kept, however old (a policy rate can stand for a year).
    AI_MARKET_HISTORY_YEARS: int = 10
    AI_PEER_RATES_MONTHS: int = 36
    AI_PEER_FINANCIALS_YEARS: int = 5
    AI_MACRO_YEARS: int = 30

    @field_validator("AI_KEY_ENCRYPTION_KEY")
    @classmethod
    def _required_in_production(cls, v: str) -> str:
        if core.is_production and not v:
            raise ValueError("AI_KEY_ENCRYPTION_KEY must be set when ENVIRONMENT=production")
        return v


@lru_cache
def ai_settings() -> AiSettings:
    return AiSettings()
