"""add author_last_commented_at to pull_requests

Revision ID: 0c7cd9111e8e
Revises: 8b8db61904bc
Create Date: 2026-03-11 16:20:27.992168

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0c7cd9111e8e"
down_revision: str | Sequence[str] | None = "8b8db61904bc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "pull_requests",
        sa.Column("author_last_commented_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("pull_requests", "author_last_commented_at")
