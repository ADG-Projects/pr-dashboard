"""Add assignee_id to pull_requests.

Revision ID: 002_add_assignee
Revises: 001_add_tracking
Create Date: 2026-03-05
"""

from alembic import op
import sqlalchemy as sa

revision = "002_add_assignee"
down_revision = "001_add_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pull_requests",
        sa.Column("assignee_id", sa.Integer(), sa.ForeignKey("team_members.id", ondelete="SET NULL"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pull_requests", "assignee_id")
