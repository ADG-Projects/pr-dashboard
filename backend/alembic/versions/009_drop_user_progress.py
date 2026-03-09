"""Drop user_progress table — review state now comes from GitHub.

Revision ID: 009
Revises: 008
Create Date: 2026-03-09
"""

import sqlalchemy as sa

from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("user_progress")


def downgrade() -> None:
    op.create_table(
        "user_progress",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "pull_request_id",
            sa.Integer,
            sa.ForeignKey("pull_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reviewed", sa.Boolean, default=False),
        sa.Column("approved", sa.Boolean, default=False),
        sa.Column("notes", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("pull_request_id", "user_id", name="uq_pr_user_progress"),
    )
