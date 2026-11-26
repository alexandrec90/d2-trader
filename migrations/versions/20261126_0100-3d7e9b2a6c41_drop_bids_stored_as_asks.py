"""Drop Traderie bids that were also stored as asks

Revision ID: 3d7e9b2a6c41
Revises: 8f2a1c7d4b3e
Create Date: 2026-11-26 01:00:00.000000

The ask request sent no `selling` filter, so Traderie answered it with "they want"
listings too, and each was stored as an ask of the item its poster wants. Where the same
listing was also stored as a bid, the ask row is provably wrong and is deleted here. A
misread bid never fetched as a bid cannot be told apart; the fixed request no longer
returns it, so it drops off the Runes page once a collector stops seeing it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "3d7e9b2a6c41"  # pragma: allowlist secret - an alembic revision id
down_revision: str | Sequence[str] | None = (
    "8f2a1c7d4b3e"  # pragma: allowlist secret - an alembic revision id
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLEANUP = """
    DELETE FROM listings AS ask
    USING listings AS bid
    WHERE ask.source = 'traderie'
      AND ask.kind = 'ask'
      AND bid.source = ask.source
      AND bid.external_id = ask.external_id
      AND bid.kind = 'bid'
"""


def upgrade() -> None:
    op.execute(CLEANUP)


def downgrade() -> None:
    """Nothing to restore: every deleted row was a bid read as an ask."""
