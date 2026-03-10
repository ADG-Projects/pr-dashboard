"""clean up orphaned spaces and dev seed users

Revision ID: 2f2f348ad3af
Revises: a66a3ecc331d
Create Date: 2026-03-10 18:13:27.979137

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2f2f348ad3af"
down_revision: str | Sequence[str] | None = "a66a3ecc331d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fake github_ids used by seed_dev_users.py
DEV_GITHUB_IDS = [900001, 900002]  # alice-dev, bob-dev


def upgrade() -> None:
    """Remove orphaned spaces (NULL github_account_id) and dev seed user data."""
    conn = op.get_bind()

    # 1. Delete orphaned spaces from pre-OAuth era
    result = conn.execute(sa.text("DELETE FROM spaces WHERE github_account_id IS NULL"))
    print(f"  Deleted {result.rowcount} orphaned space(s) with NULL github_account_id")

    # 2. Delete dev seed users and their associated data
    for github_id in DEV_GITHUB_IDS:
        # Find the user
        row = conn.execute(
            sa.text("SELECT id FROM users WHERE github_id = :gid"),
            {"gid": github_id},
        ).fetchone()
        if not row:
            continue
        user_id = row[0]

        # Delete repo_trackers owned by this user
        r = conn.execute(
            sa.text("DELETE FROM repo_trackers WHERE user_id = :uid"),
            {"uid": user_id},
        )
        print(f"  Deleted {r.rowcount} repo_tracker(s) for user {user_id} (github_id={github_id})")

        # Delete spaces owned by this user
        r = conn.execute(
            sa.text("DELETE FROM spaces WHERE user_id = :uid"),
            {"uid": user_id},
        )
        print(f"  Deleted {r.rowcount} space(s) for user {user_id}")

        # Delete github_accounts owned by this user
        r = conn.execute(
            sa.text("DELETE FROM github_accounts WHERE user_id = :uid"),
            {"uid": user_id},
        )
        print(f"  Deleted {r.rowcount} github_account(s) for user {user_id}")

        # Delete the user
        conn.execute(
            sa.text("DELETE FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        print(f"  Deleted dev user {user_id} (github_id={github_id})")


def downgrade() -> None:
    """Data migration, no automatic rollback."""
    pass
