"""Add github_requested_reviewers JSONB column to pull_requests.

Revision ID: 008
Revises: 007
Create Date: 2026-03-06
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pull_requests", sa.Column("github_requested_reviewers", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("pull_requests", "github_requested_reviewers")
