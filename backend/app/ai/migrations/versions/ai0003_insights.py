"""AI module: insights, per-user marks, and morning briefs

One *active* insight per (kind, subject, scope): the partial unique index is
what lets the engine re-run every few minutes without piling up duplicates.
When a condition clears, its row is resolved rather than deleted, so the feed
can say what was raised and when it went away.

Revision ID: ai0003
Revises: ai0002
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "ai0003"
down_revision: Union[str, None] = "ai0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_insights",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("subject", sa.String(60), nullable=False, server_default=""),
        sa.Column("scope_key", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(12), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("money_at_stake", sa.Numeric(20, 2)),
        sa.Column("money_basis", sa.String(40)),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("action", JSONB),
        sa.Column("sources", JSONB, nullable=False),
        sa.Column("business_date", sa.Date),
        sa.Column("status", sa.String(12), nullable=False, server_default="active"),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.create_index("uq_ai_insights_active", "ai_insights", ["kind", "subject", "scope_key"],
                    unique=True, postgresql_where=sa.text("status = 'active'"))
    op.create_index("ix_ai_insights_scope_status", "ai_insights", ["scope_key", "status"])

    op.create_table(
        "ai_insight_marks",
        sa.Column("insight_id", sa.BigInteger, sa.ForeignKey(
            "ai_insights.id", ondelete="CASCADE",
            name="fk_ai_insight_marks_insight_id_ai_insights"), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey(
            "users.id", ondelete="CASCADE", name="fk_ai_insight_marks_user_id_users"),
            primary_key=True),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("useful", sa.Boolean),
        sa.Column("dismissed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_insight_marks_user_id", "ai_insight_marks", ["user_id"])

    op.create_table(
        "ai_briefs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("scope_key", sa.String(30), nullable=False),
        sa.Column("brief_date", sa.Date, nullable=False),
        sa.Column("lang", sa.String(5), nullable=False),
        sa.Column("fingerprint", sa.String(120), nullable=False),
        sa.Column("narrative", sa.Text, nullable=False),
        sa.Column("sent", sa.Text, nullable=False),
        sa.Column("provider", sa.String(200)),
        sa.Column("model", sa.String(120)),
        sa.Column("request_id", sa.BigInteger),
        sa.Column("grounded", sa.Boolean),
        sa.Column("unverified", JSONB),
        sa.Column("created_by", sa.Integer, sa.ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_ai_briefs_created_by_users")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("scope_key", "brief_date", "lang",
                            name="uq_ai_briefs_scope_key_brief_date_lang"),
    )


def downgrade() -> None:
    op.drop_table("ai_briefs")
    op.drop_table("ai_insight_marks")
    op.drop_table("ai_insights")
