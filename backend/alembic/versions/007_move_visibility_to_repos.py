"""Move visibility from spaces to tracked_repos for per-repo access control.

Revision ID: 007
Revises: 006
Create Date: 2026-03-06
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add user_id and visibility to tracked_repos
    op.add_column("tracked_repos", sa.Column("user_id", sa.Integer(), nullable=True))
    op.add_column(
        "tracked_repos",
        sa.Column("visibility", sa.String(20), nullable=False, server_default="private"),
    )
    op.create_foreign_key(
        "fk_tracked_repos_user_id", "tracked_repos", "users", ["user_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_tracked_repos_visibility_user_id", "tracked_repos", ["visibility", "user_id"])

    # Backfill from spaces: copy user_id and visibility from parent space
    op.execute(
        "UPDATE tracked_repos SET user_id = s.user_id, visibility = s.visibility "
        "FROM spaces s WHERE tracked_repos.space_id = s.id"
    )
    # Orphan repos (no space) become shared to preserve current behavior
    op.execute(
        "UPDATE tracked_repos SET visibility = 'shared' WHERE space_id IS NULL"
    )

    # Drop visibility from spaces (no longer needed)
    op.drop_index("ix_spaces_visibility_user_id", table_name="spaces")
    op.drop_column("spaces", "visibility")


def downgrade() -> None:
    # Re-add visibility to spaces
    op.add_column(
        "spaces",
        sa.Column("visibility", sa.String(20), nullable=False, server_default="private"),
    )
    op.create_index("ix_spaces_visibility_user_id", "spaces", ["visibility", "user_id"])

    # Backfill spaces.visibility from tracked_repos (best effort: use first repo's visibility)
    op.execute(
        "UPDATE spaces SET visibility = COALESCE("
        "  (SELECT tr.visibility FROM tracked_repos tr WHERE tr.space_id = spaces.id LIMIT 1),"
        "  'private'"
        ")"
    )

    # Drop from tracked_repos
    op.drop_index("ix_tracked_repos_visibility_user_id", table_name="tracked_repos")
    op.drop_constraint("fk_tracked_repos_user_id", "tracked_repos", type_="foreignkey")
    op.drop_column("tracked_repos", "visibility")
    op.drop_column("tracked_repos", "user_id")
