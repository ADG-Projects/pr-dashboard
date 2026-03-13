"""add github_webhook_id to tracked_repos

Revision ID: d93d8601050b
Revises: 79203f2f4a89
Create Date: 2026-03-13 09:48:10.931491

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d93d8601050b"
down_revision: str | Sequence[str] | None = "79203f2f4a89"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("tracked_repos", sa.Column("github_webhook_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tracked_repos", "github_webhook_id")
