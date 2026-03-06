"""Add users and spaces tables, migrate team_members, drop team_members.

Revision ID: 004
Revises: 003
Create Date: 2026-03-05
"""

import os

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create users table
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("github_id", sa.Integer(), unique=True, nullable=False),
        sa.Column("login", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("avatar_url", sa.String(1024), nullable=True),
        sa.Column("encrypted_token", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_login_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # 2. Create spaces table
    op.create_table(
        "spaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("space_type", sa.String(20), nullable=False),
        sa.Column(
            "base_url",
            sa.String(1024),
            nullable=False,
            server_default="https://api.github.com",
        ),
        sa.Column("encrypted_token", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # 3. Add space_id to tracked_repos (nullable initially)
    op.add_column(
        "tracked_repos",
        sa.Column("space_id", sa.Integer(), sa.ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True),
    )

    # 4. Seed default space from env vars if available, and assign existing repos
    github_token = os.environ.get("GITHUB_TOKEN", "")
    github_org = os.environ.get("GITHUB_ORG", "")
    if github_org:
        # Encrypt token at migration time if available
        encrypted = None
        if github_token:
            try:
                from src.services.crypto import encrypt_token

                encrypted = encrypt_token(github_token)
            except Exception:
                pass

        conn = op.get_bind()
        conn.execute(
            sa.text(
                "INSERT INTO spaces (name, slug, space_type, base_url, encrypted_token) "
                "VALUES (:name, :slug, :stype, :base_url, :token)"
            ),
            {
                "name": github_org,
                "slug": github_org,
                "stype": "org",
                "base_url": "https://api.github.com",
                "token": encrypted,
            },
        )
        # Get the space id
        result = conn.execute(sa.text("SELECT id FROM spaces WHERE slug = :slug"), {"slug": github_org})
        space_id = result.scalar()
        if space_id:
            conn.execute(
                sa.text("UPDATE tracked_repos SET space_id = :sid"),
                {"sid": space_id},
            )

    # 5. Migrate team_members -> users (best-effort: only those with github_login)
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT id, display_name, github_login FROM team_members WHERE github_login IS NOT NULL")
    )
    for row in result:
        # Use negative IDs as placeholder github_ids for migrated team members
        conn.execute(
            sa.text(
                "INSERT INTO users (github_id, login, name, is_active) "
                "VALUES (:gid, :login, :name, true) "
                "ON CONFLICT (github_id) DO NOTHING"
            ),
            {"gid": -row[0], "login": row[2], "name": row[1]},
        )

    # 6. Update pull_requests.assignee_id FK: drop old FK, add new one pointing to users
    op.drop_constraint("pull_requests_assignee_id_fkey", "pull_requests", type_="foreignkey")
    # Clear any assignee_ids that reference old team_members — we'll need to remap
    # For simplicity, null them out (users will reassign via the new UI)
    conn.execute(sa.text("UPDATE pull_requests SET assignee_id = NULL"))
    op.create_foreign_key(
        "pull_requests_assignee_id_fkey",
        "pull_requests",
        "users",
        ["assignee_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 7. Update user_progress: rename team_member_id -> user_id, repoint FK
    op.drop_constraint("uq_pr_member_progress", "user_progress", type_="unique")
    op.drop_constraint("user_progress_team_member_id_fkey", "user_progress", type_="foreignkey")
    op.alter_column("user_progress", "team_member_id", new_column_name="user_id")
    # Clear user_ids that don't map to new users
    conn.execute(sa.text("UPDATE user_progress SET user_id = NULL WHERE user_id NOT IN (SELECT id FROM users)"))
    # user_id is NOT NULL so delete orphaned rows
    conn.execute(sa.text("DELETE FROM user_progress WHERE user_id IS NULL"))
    op.create_foreign_key(
        "user_progress_user_id_fkey",
        "user_progress",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint("uq_pr_user_progress", "user_progress", ["pull_request_id", "user_id"])

    # 8. Drop team_members table
    op.drop_table("team_members")


def downgrade() -> None:
    # Recreate team_members
    op.create_table(
        "team_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("github_login", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # Revert user_progress
    op.drop_constraint("uq_pr_user_progress", "user_progress", type_="unique")
    op.drop_constraint("user_progress_user_id_fkey", "user_progress", type_="foreignkey")
    op.alter_column("user_progress", "user_id", new_column_name="team_member_id")
    op.create_foreign_key(
        "user_progress_team_member_id_fkey",
        "user_progress",
        "team_members",
        ["team_member_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_pr_member_progress", "user_progress", ["pull_request_id", "team_member_id"]
    )

    # Revert pull_requests FK
    op.drop_constraint("pull_requests_assignee_id_fkey", "pull_requests", type_="foreignkey")
    op.create_foreign_key(
        "pull_requests_assignee_id_fkey",
        "pull_requests",
        "team_members",
        ["assignee_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Drop space_id from tracked_repos
    op.drop_column("tracked_repos", "space_id")

    # Drop new tables
    op.drop_table("spaces")
    op.drop_table("users")
