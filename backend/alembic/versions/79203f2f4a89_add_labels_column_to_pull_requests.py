"""add labels column to pull_requests

Revision ID: 79203f2f4a89
Revises: 0c7cd9111e8e
Create Date: 2026-03-12 10:06:50.068947

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "79203f2f4a89"
down_revision: str | Sequence[str] | None = "0c7cd9111e8e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "pull_requests",
        sa.Column(
            "labels",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("pull_requests", "labels")
