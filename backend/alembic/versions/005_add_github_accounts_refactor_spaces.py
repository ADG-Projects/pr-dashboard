"""Add github_accounts table, move tokens from users/spaces, refactor spaces.

Revision ID: 005
Revises: 004
Create Date: 2026-03-05
"""

from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create github_accounts table (if not already created by create_all)
    conn = op.get_bind()
    table_exists = conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'github_accounts')"
        )
    ).scalar()

    if not table_exists:
        op.create_table(
            "github_accounts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("github_id", sa.Integer(), nullable=False),
            sa.Column("login", sa.String(255), nullable=False),
            sa.Column("avatar_url", sa.String(1024), nullable=True),
            sa.Column("encrypted_token", sa.Text(), nullable=True),
            sa.Column(
                "base_url",
                sa.String(1024),
                nullable=False,
                server_default="https://api.github.com",
            ),
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
            sa.UniqueConstraint("user_id", "github_id", name="uq_user_github_account"),
        )

    # 2. Migrate existing user tokens -> github_accounts
    has_encrypted_token = conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'users' AND column_name = 'encrypted_token')"
        )
    ).scalar()

    if has_encrypted_token:
        users_with_tokens = conn.execute(
            sa.text(
                "SELECT id, github_id, login, avatar_url, encrypted_token "
                "FROM users WHERE encrypted_token IS NOT NULL"
            )
        )
        for row in users_with_tokens:
            conn.execute(
                sa.text(
                    "INSERT INTO github_accounts "
                    "(user_id, github_id, login, avatar_url, encrypted_token, base_url, is_active) "
                    "VALUES (:user_id, :github_id, :login, :avatar_url, :token, "
                    "'https://api.github.com', true) "
                    "ON CONFLICT (user_id, github_id) DO NOTHING"
                ),
                {
                    "user_id": row[0],
                    "github_id": row[1],
                    "login": row[2],
                    "avatar_url": row[3],
                    "token": row[4],
                },
            )

    # 3. Add github_account_id to spaces (if not already there)
    has_account_id = conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'spaces' AND column_name = 'github_account_id')"
        )
    ).scalar()

    if not has_account_id:
        op.add_column(
            "spaces",
            sa.Column(
                "github_account_id",
                sa.Integer(),
                sa.ForeignKey("github_accounts.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )

    # 4. Link existing spaces to github_accounts where tokens match
    has_space_token = conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'spaces' AND column_name = 'encrypted_token')"
        )
    ).scalar()

    if has_space_token:
        conn.execute(
            sa.text(
                "UPDATE spaces SET github_account_id = ga.id "
                "FROM github_accounts ga "
                "WHERE spaces.encrypted_token = ga.encrypted_token "
                "AND spaces.encrypted_token IS NOT NULL"
            )
        )
        # 5. Drop encrypted_token and base_url from spaces
        op.drop_column("spaces", "encrypted_token")

    has_base_url = conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'spaces' AND column_name = 'base_url')"
        )
    ).scalar()
    if has_base_url:
        op.drop_column("spaces", "base_url")

    # 6. Drop encrypted_token from users
    if has_encrypted_token:
        op.drop_column("users", "encrypted_token")


def downgrade() -> None:
    # Re-add encrypted_token to users
    op.add_column(
        "users",
        sa.Column("encrypted_token", sa.Text(), nullable=True),
    )

    # Re-add base_url and encrypted_token to spaces
    op.add_column(
        "spaces",
        sa.Column(
            "base_url",
            sa.String(1024),
            nullable=False,
            server_default="https://api.github.com",
        ),
    )
    op.add_column(
        "spaces",
        sa.Column("encrypted_token", sa.Text(), nullable=True),
    )

    # Copy tokens back from github_accounts to users/spaces
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE users SET encrypted_token = ga.encrypted_token "
            "FROM github_accounts ga WHERE users.id = ga.user_id"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE spaces SET encrypted_token = ga.encrypted_token, "
            "base_url = ga.base_url "
            "FROM github_accounts ga WHERE spaces.github_account_id = ga.id"
        )
    )

    # Drop github_account_id from spaces
    op.drop_column("spaces", "github_account_id")

    # Drop github_accounts table
    op.drop_table("github_accounts")
