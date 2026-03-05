"""Drop manual tracking columns, add commit_id to reviews.

Revision ID: 002_github_reviews
Revises: 001_add_tracking
Create Date: 2026-03-05
"""

import sqlalchemy as sa

from alembic import op

revision = "002_github_reviews"
down_revision = "001_add_tracking"
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
    # Drop manual tracking columns from pull_requests
    if _column_exists("pull_requests", "dashboard_reviewed"):
        op.drop_column("pull_requests", "dashboard_reviewed")
    if _column_exists("pull_requests", "dashboard_approved"):
        op.drop_column("pull_requests", "dashboard_approved")
    if _column_exists("pull_requests", "approved_at_sha"):
        op.drop_column("pull_requests", "approved_at_sha")

    # Add commit_id to reviews for rebase detection
    if not _column_exists("reviews", "commit_id"):
        op.add_column("reviews", sa.Column("commit_id", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("reviews", "commit_id")
    op.add_column(
        "pull_requests",
        sa.Column("approved_at_sha", sa.String(40), nullable=True),
    )
    op.add_column(
        "pull_requests",
        sa.Column("dashboard_approved", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "pull_requests",
        sa.Column("dashboard_reviewed", sa.Boolean(), server_default="false", nullable=False),
    )
