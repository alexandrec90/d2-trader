"""Add listing visits

Revision ID: 9c4d2e7f1a58
Revises: 6b1f4e8a2d97
Create Date: 2026-11-26 21:00:00.000000

Which listing pages the operator has opened from the dashboard, so an offer already
looked at is marked as such.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c4d2e7f1a58"  # pragma: allowlist secret - an alembic revision id
down_revision: str | Sequence[str] | None = (
    "6b1f4e8a2d97"  # pragma: allowlist secret - an alembic revision id
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "listing_visits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=400), nullable=False),
        sa.Column("visited_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )


def downgrade() -> None:
    op.drop_table("listing_visits")
