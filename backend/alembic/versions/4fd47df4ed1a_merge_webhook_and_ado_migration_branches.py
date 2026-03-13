"""merge webhook and ado migration branches

Revision ID: 4fd47df4ed1a
Revises: d93d8601050b, f979047596b7
Create Date: 2026-03-13 15:49:14.544422

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "4fd47df4ed1a"
down_revision: str | Sequence[str] | None = ("d93d8601050b", "f979047596b7")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
