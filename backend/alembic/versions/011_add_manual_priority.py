"""Add manual_priority column to pull_requests.

Revision ID: 011
Revises: 010
Create Date: 2026-03-09
"""

import sqlalchemy as sa

from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pull_requests",
        sa.Column("manual_priority", sa.String(10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pull_requests", "manual_priority")
