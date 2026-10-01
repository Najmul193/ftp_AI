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

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, LargeBinary, MetaData,
    Numeric, SmallInteger, String, Text, UniqueConstraint, func, text,
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
    #: May the copilot let this model chain several lookups? None: decided
    #: from the model's name (`providers.base.agentic_default`).
    agentic: Mapped[bool | None] = mapped_column(Boolean)
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


class MarketSeries(AiBase):
    """A market rate or price the module tracks. Rows mirror `market.catalog`."""

    __tablename__ = "ai_market_series"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(30))
    unit: Mapped[str] = mapped_column(String(12))
    source: Mapped[str] = mapped_column(String(30))
    tenor_days: Mapped[int | None] = mapped_column(Integer)


class MarketObservation(AiBase):
    """One value of a series on one date. Re-entering a date replaces the
    value, keeping the one it replaced, who entered it and from where."""

    __tablename__ = "ai_market_observations"
    __table_args__ = (UniqueConstraint("series_id", "obs_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("ai_market_series.id", ondelete="CASCADE"),
                                           index=True)
    obs_date: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    #: fred | exchangerate_api | bb_paste | manual
    source: Mapped[str] = mapped_column(String(30))
    #: What the value was read from: a URL, or the pasted row it came from.
    source_ref: Mapped[str | None] = mapped_column(Text)
    entered_by: Mapped[int | None] = mapped_column(Integer)
    previous_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now())


class MarketNews(AiBase):
    __tablename__ = "ai_market_news"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    feed: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, unique=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str] = mapped_column(String(10))
    tags: Mapped[list] = mapped_column(JSONB)
    impacts: Mapped[list] = mapped_column(JSONB)
    rate_signal: Mapped[int] = mapped_column(SmallInteger)
    relevance: Mapped[int] = mapped_column(SmallInteger, index=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class JobRun(AiBase):
    """One run of a scheduled job: when, what it found, and what failed."""

    __tablename__ = "ai_job_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job: Mapped[str] = mapped_column(String(40), index=True)
    trigger: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))
    detail: Mapped[dict | None] = mapped_column(JSONB)


class Insight(AiBase):
    """Something a detector found that a person should know or decide.

    Written by `insights.engine` only. At most one active row per (kind,
    subject, scope_key); when the condition clears the row is resolved, and a
    later recurrence is a new row.
    """

    __tablename__ = "ai_insights"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    #: What it is about: a product code, a series code, a branch code, or "".
    subject: Mapped[str] = mapped_column(String(60), server_default=text("''"))
    #: HO | DIV:<id> | PUBLIC -- who may see it (see `insights.visibility`).
    scope_key: Mapped[str] = mapped_column(String(30))
    #: critical | serious | warning | info
    severity: Mapped[str] = mapped_column(String(12))
    #: Display text. Entity names appear as {BR:code} placeholders, filled
    #: in for the reader at read time.
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    money_at_stake: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    #: What the amount measures, e.g. "per month", "over the week".
    money_basis: Mapped[str | None] = mapped_column(String(40))
    evidence: Mapped[list] = mapped_column(JSONB)
    action: Mapped[dict | None] = mapped_column(JSONB)
    sources: Mapped[list] = mapped_column(JSONB)
    business_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), server_default=text("'active'"))
    #: When it was raised -- or re-raised at a higher severity, which makes it
    #: unread again.
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InsightMark(AiBase):
    """One reader's state for one insight: read, useful or not, dismissed."""

    __tablename__ = "ai_insight_marks"

    insight_id: Mapped[int] = mapped_column(
        ForeignKey("ai_insights.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    useful: Mapped[bool | None] = mapped_column(Boolean)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class Brief(AiBase):
    """An AI write-up of the morning brief. The standard brief is composed by
    code on every read and is not stored; only what a provider wrote is."""

    __tablename__ = "ai_briefs"
    __table_args__ = (UniqueConstraint("scope_key", "brief_date", "lang"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(30))
    brief_date: Mapped[date] = mapped_column(Date)
    lang: Mapped[str] = mapped_column(String(5))
    #: The inputs it was written from; a different fingerprint means the data
    #: has moved since, and the write-up is shown as out of date.
    fingerprint: Mapped[str] = mapped_column(String(120))
    narrative: Mapped[str] = mapped_column(Text)
    #: The masked facts that were sent, shown under "What was sent to AI".
    sent: Mapped[str] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(120))
    request_id: Mapped[int | None] = mapped_column(BigInteger)
    grounded: Mapped[bool | None] = mapped_column(Boolean)
    unverified: Mapped[list | None] = mapped_column(JSONB)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Conversation(AiBase):
    """An Ask FTP thread. Its vault is encrypted: the token map is as
    sensitive as the names it stands for."""

    __tablename__ = "ai_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(200))
    vault_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(AiBase):
    """One question and what came of it. The result is kept so the thread
    reads back exactly as it was answered, even after the data moves on."""

    __tablename__ = "ai_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("ai_conversations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lang: Mapped[str] = mapped_column(String(5))
    question: Mapped[str] = mapped_column(Text)
    masked_question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12))
    plan: Mapped[dict | None] = mapped_column(JSONB)
    where: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB)
    answer: Mapped[str | None] = mapped_column(Text)
    grounded: Mapped[bool | None] = mapped_column(Boolean)
    unverified: Mapped[list | None] = mapped_column(JSONB)
    provider: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(120))
    request_ids: Mapped[list | None] = mapped_column(JSONB)


class Pin(AiBase):
    """An answer kept as a tile. It stores the plan, not the numbers: the
    tile re-runs the query -- with no model involved -- every time it is
    shown, so it follows every upload."""

    __tablename__ = "ai_pins"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(200))
    question: Mapped[str] = mapped_column(Text)
    plan: Mapped[dict] = mapped_column(JSONB)
    where: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PeerRate(AiBase):
    """One bank's posted rate for one product in one month, from Bangladesh
    Bank's bank-by-bank tables. A single rate has low == high."""

    __tablename__ = "ai_peer_rates"
    __table_args__ = (UniqueConstraint("month", "bank", "book", "product"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    month: Mapped[date] = mapped_column(Date)
    bank: Mapped[str] = mapped_column(String(40))
    bank_group: Mapped[str] = mapped_column(String(10))
    book: Mapped[str] = mapped_column(String(10))
    product: Mapped[str] = mapped_column(String(40))
    rate_low: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    rate_high: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    source_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Macro(AiBase):
    """An annual macro figure for Bangladesh: an outturn or a projection."""

    __tablename__ = "ai_macro"
    __table_args__ = (UniqueConstraint("code", "year", "source"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    year: Mapped[int] = mapped_column(SmallInteger)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    source: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(12))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PeerFinancial(AiBase):
    """A peer bank's reported figure for a quarter, entered by head office."""

    __tablename__ = "ai_peer_financials"
    __table_args__ = (UniqueConstraint("bank", "period_end", "metric"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bank: Mapped[str] = mapped_column(String(40))
    period_end: Mapped[date] = mapped_column(Date)
    metric: Mapped[str] = mapped_column(String(30))
    value: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    source_ref: Mapped[str | None] = mapped_column(Text)
    entered_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ForecastRecord(AiBase):
    """A forecast as it was made, and -- once known -- what actually happened."""

    __tablename__ = "ai_forecasts"
    __table_args__ = (UniqueConstraint("made_on", "scope_key", "target", "target_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    made_on: Mapped[date] = mapped_column(Date)
    scope_key: Mapped[str] = mapped_column(String(30))
    target: Mapped[str] = mapped_column(String(60))
    target_date: Mapped[date] = mapped_column(Date)
    unit: Mapped[str] = mapped_column(String(8))
    p10: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    p50: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    p90: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    confidence: Mapped[str] = mapped_column(String(10))
    actual: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
