"""AI module: public data (peer bank rates, macro, peer financials) and agentic providers

`ai_peer_rates` holds Bangladesh Bank's monthly bank-by-bank deposit and
lending rate tables, one row per bank, product and month. `ai_macro` holds
annual macro figures, actual (World Bank) and projected (IMF), each kept per
source so a projection never overwrites an outturn. `ai_peer_financials` is
typed or pasted by head office: DSE does not serve programs.

Revision ID: ai0005
Revises: ai0004
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ai0005"
down_revision: Union[str, None] = "ai0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NULL: decided from the model's name; true/false: an administrator chose.
    op.add_column("ai_providers", sa.Column("agentic", sa.Boolean))

    op.create_table(
        "ai_peer_rates",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("month", sa.Date, nullable=False),
        sa.Column("bank", sa.String(40), nullable=False),
        #: SCB | DFI | PCB | FB
        sa.Column("bank_group", sa.String(10), nullable=False),
        #: deposit | lending
        sa.Column("book", sa.String(10), nullable=False),
        sa.Column("product", sa.String(40), nullable=False),
        sa.Column("rate_low", sa.Numeric(8, 4), nullable=False),
        sa.Column("rate_high", sa.Numeric(8, 4), nullable=False),
        sa.Column("source_ref", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("month", "bank", "book", "product",
                            name="uq_ai_peer_rates_month_bank_book_product"),
    )
    op.create_index("ix_ai_peer_rates_month_book_product", "ai_peer_rates",
                    ["month", "book", "product"])

    op.create_table(
        "ai_macro",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("year", sa.SmallInteger, nullable=False),
        sa.Column("value", sa.Numeric(20, 6), nullable=False),
        #: worldbank | imf
        sa.Column("source", sa.String(20), nullable=False),
        #: actual | projection
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("code", "year", "source", name="uq_ai_macro_code_year_source"),
    )

    op.create_table(
        "ai_peer_financials",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("bank", sa.String(40), nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        #: nim | cost_of_funds | yield_on_advances | deposit_growth | advance_growth | casa | roa | npl
        sa.Column("metric", sa.String(30), nullable=False),
        sa.Column("value", sa.Numeric(12, 4), nullable=False),
        sa.Column("source_ref", sa.Text),
        sa.Column("entered_by", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("bank", "period_end", "metric",
                            name="uq_ai_peer_financials_bank_period_end_metric"),
    )


def downgrade() -> None:
    op.drop_table("ai_peer_financials")
    op.drop_table("ai_macro")
    op.drop_table("ai_peer_rates")
    op.drop_column("ai_providers", "agentic")
