"""Link each Traderie listing to its own page

Revision ID: 8f2a1c7d4b3e
Revises: c5059d1902d1
Create Date: 2026-11-23 22:00:00.000000

Every Traderie row was stored with its item's product page as `url`, and seeing a listing
again never rewrites `url`, so the rows already stored are corrected here.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "8f2a1c7d4b3e"  # pragma: allowlist secret - an alembic revision id
down_revision: str | Sequence[str] | None = "c5059d1902d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Keep in step with `d2_trader.sources.traderie.listing_url`; a test compares the two.
BACKFILL = """
    UPDATE listings
    SET url = 'https://traderie.com/diablo2resurrected/listing/' || external_id
    WHERE source = 'traderie'
"""


def upgrade() -> None:
    op.execute(BACKFILL)


def downgrade() -> None:
    """Nothing to undo: the old link was the same for every listing of an item."""
