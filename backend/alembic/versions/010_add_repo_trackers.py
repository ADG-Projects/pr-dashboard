"""Add repo_trackers junction table and migrate ownership from tracked_repos.

Revision ID: 010
Revises: 009
Create Date: 2026-03-09
"""

import sqlalchemy as sa

from alembic import op

revision = "010"
down_revision = ("009", "71fdfa6af00d")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repo_trackers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "repo_id",
            sa.Integer,
            sa.ForeignKey("tracked_repos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "space_id",
            sa.Integer,
            sa.ForeignKey("spaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "visibility",
            sa.String(20),
            nullable=False,
            server_default="private",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "repo_id", name="uq_user_repo_tracker"),
    )

    # Backfill from existing tracked_repos rows that have a user_id
    op.execute(
        """
        INSERT INTO repo_trackers (user_id, repo_id, space_id, visibility)
        SELECT user_id, id, space_id, visibility
        FROM tracked_repos
        WHERE user_id IS NOT NULL
        """
    )

    # Drop old columns from tracked_repos
    op.drop_constraint("tracked_repos_space_id_fkey", "tracked_repos", type_="foreignkey")
    op.drop_constraint("fk_tracked_repos_user_id", "tracked_repos", type_="foreignkey")
    op.drop_column("tracked_repos", "space_id")
    op.drop_column("tracked_repos", "user_id")
    op.drop_column("tracked_repos", "visibility")


def downgrade() -> None:
    # Re-add columns to tracked_repos
    op.add_column(
        "tracked_repos",
        sa.Column(
            "visibility",
            sa.String(20),
            nullable=False,
            server_default="private",
        ),
    )
    op.add_column(
        "tracked_repos",
        sa.Column("user_id", sa.Integer, nullable=True),
    )
    op.add_column(
        "tracked_repos",
        sa.Column("space_id", sa.Integer, nullable=True),
    )
    op.create_foreign_key(
        "fk_tracked_repos_user_id",
        "tracked_repos",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "tracked_repos_space_id_fkey",
        "tracked_repos",
        "spaces",
        ["space_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Restore data from first tracker per repo
    op.execute(
        """
        UPDATE tracked_repos SET
            user_id = rt.user_id,
            space_id = rt.space_id,
            visibility = rt.visibility
        FROM (
            SELECT DISTINCT ON (repo_id) repo_id, user_id, space_id, visibility
            FROM repo_trackers
            ORDER BY repo_id, created_at ASC
        ) rt
        WHERE tracked_repos.id = rt.repo_id
        """
    )

    op.drop_table("repo_trackers")
