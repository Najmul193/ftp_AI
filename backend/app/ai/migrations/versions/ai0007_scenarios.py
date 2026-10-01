"""AI module: saved what-if scenarios (at most 30 a person; the oldest goes)

Revision ID: ai0007
Revises: ai0006
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "ai0007"
down_revision: Union[str, None] = "ai0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_scenarios",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey(
            "users.id", ondelete="CASCADE", name="fk_ai_scenarios_user_id_users"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("params", JSONB, nullable=False),
        sa.Column("summary", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_scenarios_user_id", "ai_scenarios", ["user_id"])


def downgrade() -> None:
    op.drop_table("ai_scenarios")
