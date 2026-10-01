"""AI module: forecasts kept, so each can be scored against what happened

One row per forecast target per day it was made: the band then, the actual
once it is known. The page shows the record -- how often the outcome fell
inside the band -- which is what makes a forecast worth trusting.

Revision ID: ai0006
Revises: ai0005
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ai0006"
down_revision: Union[str, None] = "ai0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_forecasts",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("made_on", sa.Date, nullable=False),
        #: HO | DIV:<id> | MARKET
        sa.Column("scope_key", sa.String(30), nullable=False),
        #: book:<metric>:month | market:<code>:30d
        sa.Column("target", sa.String(60), nullable=False),
        sa.Column("target_date", sa.Date, nullable=False),
        sa.Column("unit", sa.String(8), nullable=False),
        sa.Column("p10", sa.Numeric(24, 6), nullable=False),
        sa.Column("p50", sa.Numeric(24, 6), nullable=False),
        sa.Column("p90", sa.Numeric(24, 6), nullable=False),
        sa.Column("confidence", sa.String(10), nullable=False),
        sa.Column("actual", sa.Numeric(24, 6)),
        sa.Column("scored_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("made_on", "scope_key", "target", "target_date",
                            name="uq_ai_forecasts_made_on_scope_key_target_target_date"),
    )
    op.create_index("ix_ai_forecasts_unscored", "ai_forecasts", ["target_date"],
                    postgresql_where=sa.text("actual IS NULL"))


def downgrade() -> None:
    op.drop_table("ai_forecasts")
