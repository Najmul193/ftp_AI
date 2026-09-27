"""AI module: market series, observations, news and job runs

Revision ID: ai0002
Revises: ai0001
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "ai0002"
down_revision: Union[str, None] = "ai0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_market_series",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("unit", sa.String(12), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("tenor_days", sa.Integer),
    )
    op.create_index("ix_ai_market_series_code", "ai_market_series", ["code"], unique=True)

    op.create_table(
        "ai_market_observations",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("series_id", sa.Integer, sa.ForeignKey(
            "ai_market_series.id", ondelete="CASCADE",
            name="fk_ai_market_observations_series_id_ai_market_series"), nullable=False),
        sa.Column("obs_date", sa.Date, nullable=False),
        sa.Column("value", sa.Numeric(18, 6), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("source_ref", sa.Text),
        sa.Column("entered_by", sa.Integer, sa.ForeignKey(
            "users.id", name="fk_ai_market_observations_entered_by_users")),
        sa.Column("previous_value", sa.Numeric(18, 6)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("series_id", "obs_date", name="uq_ai_market_observations_series_id_obs_date"),
    )
    op.create_index("ix_ai_market_observations_series_id", "ai_market_observations", ["series_id"])

    op.create_table(
        "ai_market_news",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("feed", sa.String(80), nullable=False),
        sa.Column("source", sa.String(160), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("summary", sa.Text),
        sa.Column("region", sa.String(10), nullable=False),
        sa.Column("tags", JSONB, nullable=False),
        sa.Column("impacts", JSONB, nullable=False),
        sa.Column("rate_signal", sa.SmallInteger, nullable=False),
        sa.Column("relevance", sa.SmallInteger, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("url", name="uq_ai_market_news_url"),
    )
    op.create_index("ix_ai_market_news_published_at", "ai_market_news", ["published_at"])
    op.create_index("ix_ai_market_news_relevance", "ai_market_news", ["relevance"])

    op.create_table(
        "ai_job_runs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("job", sa.String(40), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("detail", JSONB),
    )
    op.create_index("ix_ai_job_runs_job", "ai_job_runs", ["job"])


def downgrade() -> None:
    op.drop_table("ai_job_runs")
    op.drop_table("ai_market_news")
    op.drop_table("ai_market_observations")
    op.drop_table("ai_market_series")
