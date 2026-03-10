"""Tests for computed CI status and review state logic."""

from dataclasses import dataclass
from datetime import UTC, datetime

from src.api.pulls import _compute_ci_status, _compute_review_state


@dataclass
class FakeCheck:
    """Lightweight stand-in for CheckRun (avoids ORM init issues)."""

    name: str
    status: str
    conclusion: str | None = None


@dataclass
class FakeReview:
    """Lightweight stand-in for Review."""

    reviewer: str
    state: str
    submitted_at: datetime = datetime(2025, 1, 1, tzinfo=UTC)


# ── CI Status ─────────────────────────────────────


class TestComputeCiStatus:
    def test_no_checks(self):
        assert _compute_ci_status([]) == "unknown"

    def test_all_success(self):
        checks = [
            FakeCheck("lint", "completed", "success"),
            FakeCheck("build", "completed", "success"),
        ]
        assert _compute_ci_status(checks) == "success"  # type: ignore[arg-type]

    def test_any_failure_means_failure(self):
        checks = [
            FakeCheck("lint", "completed", "success"),
            FakeCheck("build", "completed", "failure"),
        ]
        assert _compute_ci_status(checks) == "failure"  # type: ignore[arg-type]

    def test_pending_when_in_progress(self):
        checks = [
            FakeCheck("lint", "completed", "success"),
            FakeCheck("build", "in_progress", None),
        ]
        assert _compute_ci_status(checks) == "pending"  # type: ignore[arg-type]

    def test_pending_when_queued(self):
        checks = [FakeCheck("build", "queued", None)]
        assert _compute_ci_status(checks) == "pending"  # type: ignore[arg-type]

    def test_action_required(self):
        checks = [FakeCheck("cla", "completed", "action_required")]
        assert _compute_ci_status(checks) == "action_required"  # type: ignore[arg-type]

    def test_failure_takes_precedence_over_pending(self):
        checks = [
            FakeCheck("lint", "completed", "failure"),
            FakeCheck("build", "in_progress", None),
        ]
        assert _compute_ci_status(checks) == "failure"  # type: ignore[arg-type]


# ── Review State ──────────────────────────────────


class TestComputeReviewState:
    def test_no_reviews(self):
        assert _compute_review_state([]) == "none"

    def test_approved(self):
        reviews = [FakeReview("alice", "APPROVED")]
        assert _compute_review_state(reviews) == "approved"  # type: ignore[arg-type]

    def test_changes_requested(self):
        reviews = [FakeReview("alice", "CHANGES_REQUESTED")]
        assert _compute_review_state(reviews) == "changes_requested"  # type: ignore[arg-type]

    def test_mixed_when_both_approved_and_changes_requested(self):
        reviews = [
            FakeReview("alice", "APPROVED", datetime(2025, 1, 1, 0, 0, tzinfo=UTC)),
            FakeReview("bob", "CHANGES_REQUESTED", datetime(2025, 1, 1, 0, 1, tzinfo=UTC)),
        ]
        assert _compute_review_state(reviews) == "mixed"  # type: ignore[arg-type]

    def test_latest_review_per_reviewer_wins(self):
        """If alice first requests changes then approves, overall = approved."""
        reviews = [
            FakeReview("alice", "CHANGES_REQUESTED", datetime(2025, 1, 1, 0, 0, tzinfo=UTC)),
            FakeReview("alice", "APPROVED", datetime(2025, 1, 1, 0, 1, tzinfo=UTC)),
        ]
        assert _compute_review_state(reviews) == "approved"  # type: ignore[arg-type]

    def test_comment_only_is_reviewed(self):
        reviews = [FakeReview("alice", "COMMENTED")]
        assert _compute_review_state(reviews) == "reviewed"  # type: ignore[arg-type]

    def test_dismissed_only_is_reviewed(self):
        reviews = [FakeReview("alice", "DISMISSED")]
        assert _compute_review_state(reviews) == "reviewed"  # type: ignore[arg-type]
