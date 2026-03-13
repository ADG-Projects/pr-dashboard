"""add work_item_links table

Revision ID: fa68a6a436de
Revises: 2f2f348ad3af
Create Date: 2026-03-11 10:02:15.513450

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fa68a6a436de"
down_revision: str | Sequence[str] | None = "2f2f348ad3af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "work_item_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pull_request_id", sa.Integer(), nullable=False),
        sa.Column("work_item_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("state", sa.String(length=100), nullable=False),
        sa.Column("work_item_type", sa.String(length=100), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("assigned_to", sa.String(length=255), nullable=True),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["pull_request_id"], ["pull_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pull_request_id", "work_item_id", name="uq_pr_work_item"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("work_item_links")
