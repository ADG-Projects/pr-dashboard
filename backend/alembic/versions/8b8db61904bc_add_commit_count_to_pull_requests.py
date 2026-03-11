"""add commit_count to pull_requests

Revision ID: 8b8db61904bc
Revises: 2f2f348ad3af
Create Date: 2026-03-11 14:33:20.155502

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b8db61904bc"
down_revision: str | Sequence[str] | None = "2f2f348ad3af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "pull_requests", sa.Column("commit_count", sa.Integer(), server_default="0", nullable=False)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("pull_requests", "commit_count")
