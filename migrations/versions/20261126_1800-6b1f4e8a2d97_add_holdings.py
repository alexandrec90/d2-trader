"""Add holdings

Revision ID: 6b1f4e8a2d97
Revises: 3d7e9b2a6c41
Create Date: 2026-11-26 18:00:00.000000

What the operator holds in each market, so the Trade up page keeps only the offers it
can pay for without the runes being typed in again on every visit.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6b1f4e8a2d97"  # pragma: allowlist secret - an alembic revision id
down_revision: str | Sequence[str] | None = (
    "3d7e9b2a6c41"  # pragma: allowlist secret - an alembic revision id
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "holdings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=40), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("market_id", "currency"),
    )


def downgrade() -> None:
    op.drop_table("holdings")
