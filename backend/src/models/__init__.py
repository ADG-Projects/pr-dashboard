"""Models package — import all tables for Alembic and ORM discovery."""

from src.models.tables import (
    CheckRun,
    PRStack,
    PRStackMembership,
    PullRequest,
    QualitySnapshot,
    Review,
    Space,
    TrackedRepo,
    User,
    UserProgress,
)

__all__ = [
    "CheckRun",
    "PRStack",
    "PRStackMembership",
    "PullRequest",
    "QualitySnapshot",
    "Review",
    "Space",
    "TrackedRepo",
    "User",
    "UserProgress",
]
