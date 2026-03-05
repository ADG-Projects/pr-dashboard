"""Add tracking columns to pull_requests.

Revision ID: 001_add_tracking
Revises:
Create Date: 2026-03-05
"""

import sqlalchemy as sa

from alembic import op

revision = "001_add_tracking"
down_revision = None
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    )
    return result.scalar() is not None


def upgrade() -> None:
    if not _column_exists("pull_requests", "head_sha"):
        op.add_column("pull_requests", sa.Column("head_sha", sa.String(40), nullable=True))
    if not _column_exists("pull_requests", "dashboard_reviewed"):
        op.add_column(
            "pull_requests",
            sa.Column("dashboard_reviewed", sa.Boolean(), server_default="false", nullable=False),
        )
    if not _column_exists("pull_requests", "dashboard_approved"):
        op.add_column(
            "pull_requests",
            sa.Column("dashboard_approved", sa.Boolean(), server_default="false", nullable=False),
        )
    if not _column_exists("pull_requests", "approved_at_sha"):
        op.add_column("pull_requests", sa.Column("approved_at_sha", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("pull_requests", "approved_at_sha")
    op.drop_column("pull_requests", "dashboard_approved")
    op.drop_column("pull_requests", "dashboard_reviewed")
    op.drop_column("pull_requests", "head_sha")
