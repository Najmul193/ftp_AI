"""The AI module's tables.

Declared on their own metadata, not the platform's `Base`, and migrated by
their own Alembic history (version table `alembic_version_ai`). The platform's
schema and its migration chain never see them, so with the module off the
database is exactly what the platform alone would create.

References to platform tables (`users.id`) are plain integers here and real
foreign keys in the migration: the ORM does not need the relationship, and
declaring it would pull the platform's metadata into this one.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Integer, LargeBinary, MetaData, SmallInteger,
    String, Text, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.base import NAMING_CONVENTION


class AiBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class AiProvider(AiBase):
    """A connected AI provider. The key is stored encrypted and never returned."""

    __tablename__ = "ai_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: gemini | openai | anthropic | groq | openrouter | ollama | deepseek | custom
    brand: Mapped[str] = mapped_column(String(30))
    #: The wire protocol: openai_compat | anthropic
    kind: Mapped[str] = mapped_column(String(30))
    label: Mapped[str] = mapped_column(String(120))
    base_url: Mapped[str] = mapped_column(String(300))
    model: Mapped[str] = mapped_column(String(120))
    api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_last4: Mapped[str | None] = mapped_column(String(8))
    #: policy.Tier: the most sensitive data this provider is cleared to receive.
    max_data_tier: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    #: Free tiers may train on prompts. Clearing one above PUBLIC needs an
    #: administrator to acknowledge that, and the acknowledgement is recorded.
    is_free_tier: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    trial_ack_by: Mapped[int | None] = mapped_column(Integer)
    trial_ack_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'untested'"))
    status_detail: Mapped[str | None] = mapped_column(Text)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: 0 means unlimited.
    daily_token_budget: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now())


class AiRequest(AiBase):
    """One call through the gateway, allowed or blocked: the egress record.

    Append-only (a trigger refuses UPDATE and DELETE). Holds exactly what left
    the system -- the masked text -- so an auditor can see what a provider saw.
    Kept apart from `audit_log` deliberately: the newest audit id is the
    analytics cache's data version, and an AI call is not a data change.
    """

    __tablename__ = "ai_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    username: Mapped[str | None] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(60))
    #: No foreign key: the row outlives a deleted provider, and an append-only
    #: table cannot take the UPDATE that ON DELETE SET NULL would issue.
    provider_id: Mapped[int | None] = mapped_column(Integer)
    provider_label: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(120))
    tier: Mapped[int] = mapped_column(SmallInteger)
    #: ok | blocked | error
    status: Mapped[str] = mapped_column(String(20))
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    masked_payload: Mapped[dict] = mapped_column(JSONB)
    masked_response: Mapped[str | None] = mapped_column(Text)
    grounded: Mapped[bool | None] = mapped_column(Boolean)
    unverified: Mapped[list | None] = mapped_column(JSONB)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
