"""Application settings, all overridable by environment variable.

Nothing here carries a production secret as a default. `SECRET_KEY` has a
development-only value that the application refuses to start with when
`ENVIRONMENT` is production.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    APP_NAME: str = "FTP Platform"
    API_V1_PREFIX: str = "/api/v1"

    # --- database --------------------------------------------------------- #
    DATABASE_URL: str = "postgresql+psycopg://ftp:ftp_dev@localhost:55432/ftp"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO: bool = False
    #: Kills a runaway query rather than letting it occupy a connection.
    DB_STATEMENT_TIMEOUT_MS: int = 30_000

    # --- security --------------------------------------------------------- #
    SECRET_KEY: str = "dev-only-insecure-key-change-me"
    ACCESS_TOKEN_MINUTES: int = 15
    REFRESH_TOKEN_HOURS: int = 8
    #: Gate the application until a seeded password is replaced. Off while the
    #: platform is in development so it does not sit in the way; turn it on for
    #: any real deployment, where a shared initial password is a live exposure.
    ENFORCE_PASSWORD_CHANGE: bool = False
    MAX_FAILED_LOGINS: int = 5
    LOCKOUT_MINUTES: int = 15
    PASSWORD_MIN_LENGTH: int = 12

    # --- optional modules -------------------------------------------------- #
    #: Load the AI module (`app.ai`). Off: the package is never imported, so no
    #: AI route, job, permission or table exists. Its own settings live in
    #: `app.ai.config`.
    AI_MODULE: bool = Field(False, validation_alias="FTP_AI_MODULE")

    # --- ingestion -------------------------------------------------------- #
    UPLOAD_MAX_BYTES: int = 100 * 1024 * 1024
    UPLOAD_STORAGE_DIR: str = "./var/uploads"
    INGEST_CHUNK_SIZE: int = 50_000

    # --- CORS -------------------------------------------------------------- #
    #: `NoDecode` stops pydantic-settings parsing this as JSON before the
    #: validator below sees it, so the validator can accept the plain forms a
    #: person actually types into a hosting dashboard.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_origins(cls, v: object) -> list[str]:
        """Accept a JSON array, a comma-separated list, or a single origin.

        Requiring JSON here cost a deploy: a dashboard field holding
        `https://example.com` is the obvious thing to type, and it failed with
        a JSONDecodeError pointing at column 1 of a value the operator never
        thought of as JSON. A setting that is configured by hand should accept
        what a hand writes.
        """
        if v is None:
            return []
        if isinstance(v, (list, tuple, set)):
            return [str(x).strip() for x in v if str(x).strip()]

        text = str(v).strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"CORS_ORIGINS looks like JSON but does not parse: {exc}. "
                    f"A comma-separated list is also accepted."
                ) from exc
            return [str(x).strip() for x in loaded if str(x).strip()]
        return [part.strip() for part in text.split(",") if part.strip()]

    @field_validator("DATABASE_URL")
    @classmethod
    def _require_psycopg_driver(cls, v: str) -> str:
        """Normalise the scheme to the driver that is actually installed.

        Managed providers hand out `postgres://` or `postgresql://`. SQLAlchemy
        maps both to psycopg2, which is not a dependency here -- the driver is
        psycopg 3. Left alone that fails at first connection with a missing
        module, which reads as a packaging fault rather than a URL that needs
        one word changed. Rewriting it here means a pasted connection string
        works as pasted.
        """
        for prefix in ("postgresql+psycopg://", "postgresql+psycopg_async://"):
            if v.startswith(prefix):
                return v
        for prefix in ("postgresql://", "postgres://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

    @field_validator("SECRET_KEY")
    @classmethod
    def _reject_dev_secret_in_production(cls, v: str, info) -> str:
        env = (info.data or {}).get("ENVIRONMENT", "development")
        if env == "production" and v.startswith("dev-only"):
            raise ValueError(
                "SECRET_KEY must be set explicitly when ENVIRONMENT=production"
            )
        return v

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
