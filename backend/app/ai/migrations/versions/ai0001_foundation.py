"""AI module foundation: providers and the egress log

`ai_requests` is append-only. UPDATE is always refused; DELETE is refused
unless the session sets `ai.retention_purge = on`, which only the retention
job does -- so the record of what left the bank cannot be edited, and can be
removed only by the one path that is meant to remove it.

Revision ID: ai0001
Revises:
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "ai0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_providers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("brand", sa.String(30), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("base_url", sa.String(300), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("api_key_enc", sa.LargeBinary),
        sa.Column("key_last4", sa.String(8)),
        sa.Column("max_data_tier", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("is_free_tier", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("trial_ack_by", sa.Integer, sa.ForeignKey("users.id", name="fk_ai_providers_trial_ack_by_users")),
        sa.Column("trial_ack_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default="untested"),
        sa.Column("status_detail", sa.Text),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        sa.Column("daily_token_budget", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", name="fk_ai_providers_created_by_users")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("max_data_tier BETWEEN 0 AND 2", name="ck_ai_providers_tier"),
    )

    op.create_table(
        "ai_requests",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("user_id", sa.Integer),
        sa.Column("username", sa.String(120)),
        sa.Column("purpose", sa.String(60), nullable=False),
        sa.Column("provider_id", sa.Integer),
        sa.Column("provider_label", sa.String(200)),
        sa.Column("model", sa.String(120)),
        sa.Column("tier", sa.SmallInteger, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("blocked_reason", sa.Text),
        sa.Column("masked_payload", JSONB, nullable=False),
        sa.Column("masked_response", sa.Text),
        sa.Column("grounded", sa.Boolean),
        sa.Column("unverified", JSONB),
        sa.Column("tokens_in", sa.Integer),
        sa.Column("tokens_out", sa.Integer),
        sa.Column("latency_ms", sa.Integer),
    )
    op.create_index("ix_ai_requests_created_at", "ai_requests", ["created_at"])
    op.create_index("ix_ai_requests_user_id", "ai_requests", ["user_id"])
    op.create_index("ix_ai_requests_provider_day", "ai_requests", ["provider_id", "created_at"])

    op.execute("""
    CREATE OR REPLACE FUNCTION ai_requests_append_only()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE'
           AND coalesce(current_setting('ai.retention_purge', true), '') = 'on' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'ai_requests is append-only; % is not permitted', TG_OP;
    END $$;
    """)
    op.execute("""
    CREATE TRIGGER trg_ai_requests_append_only
    BEFORE UPDATE OR DELETE ON ai_requests
    FOR EACH ROW EXECUTE FUNCTION ai_requests_append_only();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_ai_requests_append_only ON ai_requests")
    op.execute("DROP FUNCTION IF EXISTS ai_requests_append_only()")
    op.drop_table("ai_requests")
    op.drop_table("ai_providers")
