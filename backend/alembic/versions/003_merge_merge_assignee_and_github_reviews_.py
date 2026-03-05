"""merge assignee and github_reviews branches

Revision ID: 003_merge
Revises: 002_add_assignee, 002_github_reviews
Create Date: 2026-03-05 13:56:35.236365

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '003_merge'
down_revision: Union[str, Sequence[str], None] = ('002_add_assignee', '002_github_reviews')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
