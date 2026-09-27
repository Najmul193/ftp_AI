"""AI module: Ask FTP conversations, messages and pinned answers

A conversation keeps its vault -- the token map that lets a follow-up say
"that branch" and mean the same BR_ token -- encrypted with the module's key.

Revision ID: ai0004
Revises: ai0003
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "ai0004"
down_revision: Union[str, None] = "ai0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey(
            "users.id", ondelete="CASCADE", name="fk_ai_conversations_user_id_users"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("vault_enc", sa.LargeBinary),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_conversations_user_id", "ai_conversations", ["user_id"])

    op.create_table(
        "ai_messages",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey(
            "ai_conversations.id", ondelete="CASCADE",
            name="fk_ai_messages_conversation_id_ai_conversations"), nullable=False),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lang", sa.String(5), nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("masked_question", sa.Text, nullable=False),
        #: ok | clarify | refused | error
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("plan", JSONB),
        sa.Column("where", JSONB),
        sa.Column("result", JSONB),
        sa.Column("answer", sa.Text),
        sa.Column("grounded", sa.Boolean),
        sa.Column("unverified", JSONB),
        sa.Column("provider", sa.String(200)),
        sa.Column("model", sa.String(120)),
        sa.Column("request_ids", JSONB),
    )
    op.create_index("ix_ai_messages_conversation_id", "ai_messages", ["conversation_id"])
    op.create_index("ix_ai_messages_user_created", "ai_messages", ["user_id", "created_at"])

    op.create_table(
        "ai_pins",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey(
            "users.id", ondelete="CASCADE", name="fk_ai_pins_user_id_users"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("plan", JSONB, nullable=False),
        sa.Column("where", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_pins_user_id", "ai_pins", ["user_id"])


def downgrade() -> None:
    op.drop_table("ai_pins")
    op.drop_table("ai_messages")
    op.drop_table("ai_conversations")
