"""Add user_id and visibility to spaces for private/shared filtering.

Revision ID: 006
Revises: 005
Create Date: 2026-03-06
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("spaces", sa.Column("user_id", sa.Integer(), nullable=True))
    op.add_column(
        "spaces",
        sa.Column("visibility", sa.String(20), nullable=False, server_default="private"),
    )
    op.create_foreign_key(
        "fk_spaces_user_id", "spaces", "users", ["user_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_spaces_visibility_user_id", "spaces", ["visibility", "user_id"])

    # Backfill user_id from github_accounts
    op.execute(
        "UPDATE spaces SET user_id = ga.user_id "
        "FROM github_accounts ga WHERE spaces.github_account_id = ga.id"
    )
    # Orphan spaces (no github_account) become shared
    op.execute(
        "UPDATE spaces SET visibility = 'shared' WHERE github_account_id IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_spaces_visibility_user_id", table_name="spaces")
    op.drop_constraint("fk_spaces_user_id", "spaces", type_="foreignkey")
    op.drop_column("spaces", "visibility")
    op.drop_column("spaces", "user_id")
