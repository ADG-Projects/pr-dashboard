"""Add ON DELETE SET NULL to parent_pr_id and root_pr_id FKs.

Revision ID: 71fdfa6af00d
Revises: 009
Create Date: 2026-03-09
"""

from alembic import op

revision = "71fdfa6af00d"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "pr_stack_memberships_parent_pr_id_fkey", "pr_stack_memberships", type_="foreignkey"
    )
    op.create_foreign_key(
        "pr_stack_memberships_parent_pr_id_fkey",
        "pr_stack_memberships",
        "pull_requests",
        ["parent_pr_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint("pr_stacks_root_pr_id_fkey", "pr_stacks", type_="foreignkey")
    op.create_foreign_key(
        "pr_stacks_root_pr_id_fkey",
        "pr_stacks",
        "pull_requests",
        ["root_pr_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("pr_stacks_root_pr_id_fkey", "pr_stacks", type_="foreignkey")
    op.create_foreign_key(
        "pr_stacks_root_pr_id_fkey",
        "pr_stacks",
        "pull_requests",
        ["root_pr_id"],
        ["id"],
    )
    op.drop_constraint(
        "pr_stack_memberships_parent_pr_id_fkey", "pr_stack_memberships", type_="foreignkey"
    )
    op.create_foreign_key(
        "pr_stack_memberships_parent_pr_id_fkey",
        "pr_stack_memberships",
        "pull_requests",
        ["parent_pr_id"],
        ["id"],
    )
