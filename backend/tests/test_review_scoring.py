"""Tests for review scoring, ball-in-my-court logic, and review queue filtering."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from src.api.prioritize import (
    _compute_age_pts,
    _compute_ball_in_my_court,
    _compute_size_pts,
    _is_my_review,
    compute_review_score,
)


@dataclass
class FakeReview:
    reviewer: str
    state: str
    submitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    commit_id: str | None = None


@dataclass
class FakePR:
    head_sha: str | None = "abc123"
    reviews: list = field(default_factory=list)
    github_requested_reviewers: list = field(default_factory=list)
    author_last_commented_at: datetime | None = None
    author: str = "other-user"


# ── _compute_age_pts ──────────────────────────────────


class TestComputeAgePts:
    def test_brand_new_pr(self):
        """A PR created just now scores 0 age points."""
        assert _compute_age_pts(datetime.now(UTC), max_pts=15) == 0

    def test_exactly_max_days(self):
        """A PR exactly `days` old scores max_pts."""
        created = datetime.now(UTC) - timedelta(days=7)
        assert _compute_age_pts(created, max_pts=15, days=7) == 15

    def test_older_than_cap(self):
        """A PR older than the cap still scores max_pts (clamped)."""
        created = datetime.now(UTC) - timedelta(days=30)
        assert _compute_age_pts(created, max_pts=15, days=7) == 15

    def test_half_age(self):
        """A PR half the max age scores roughly half the points."""
        created = datetime.now(UTC) - timedelta(days=3, hours=12)
        pts = _compute_age_pts(created, max_pts=14, days=7)
        assert pts == 7

    def test_custom_max_pts(self):
        """Works with different max_pts values."""
        created = datetime.now(UTC) - timedelta(days=7)
        assert _compute_age_pts(created, max_pts=10, days=7) == 10


# ── _compute_size_pts ─────────────────────────────────


class TestComputeSizePts:
    @pytest.mark.parametrize(
        "lines, expected",
        [
            (0, 15),
            (50, 15),
            (51, 12),
            (200, 12),
            (201, 7),
            (500, 7),
            (501, 3),
            (1000, 3),
            (1001, 0),
            (5000, 0),
        ],
    )
    def test_size_brackets(self, lines, expected):
        assert _compute_size_pts(lines) == expected


# ── _compute_ball_in_my_court ─────────────────────────


class TestComputeBallInMyCourt:
    def test_never_reviewed(self):
        """No reviews from me = 35 (I'm blocking)."""
        reviews = [FakeReview("someone_else", "APPROVED")]
        assert _compute_ball_in_my_court(reviews, {"me"}, "abc") == 35

    def test_no_reviews_at_all(self):
        """Empty review list = 35."""
        assert _compute_ball_in_my_court([], {"me"}, "abc") == 35

    def test_author_pushed_new_commits(self):
        """My review commit differs from head = 30."""
        reviews = [FakeReview("me", "COMMENTED", commit_id="old_sha")]
        assert _compute_ball_in_my_court(reviews, {"me"}, "new_sha") == 30

    def test_author_replied_to_comments(self):
        """Author commented after my review = 25."""
        my_review_time = datetime.now(UTC) - timedelta(hours=2)
        author_reply = datetime.now(UTC) - timedelta(hours=1)
        reviews = [
            FakeReview("me", "CHANGES_REQUESTED", submitted_at=my_review_time, commit_id="abc")
        ]
        assert _compute_ball_in_my_court(reviews, {"me"}, "abc", author_reply) == 25

    def test_nothing_changed(self):
        """I reviewed and nothing happened since = 0."""
        reviews = [FakeReview("me", "APPROVED", commit_id="abc")]
        assert _compute_ball_in_my_court(reviews, {"me"}, "abc") == 0

    def test_approved_but_rebased(self):
        """I approved but author rebased = 30."""
        reviews = [FakeReview("me", "APPROVED", commit_id="old_sha")]
        assert _compute_ball_in_my_court(reviews, {"me"}, "new_sha") == 30

    def test_multiple_logins(self):
        """Works when user has multiple logins."""
        reviews = [FakeReview("my-work-account", "COMMENTED", commit_id="abc")]
        logins = {"me", "my-work-account"}
        assert _compute_ball_in_my_court(reviews, logins, "abc") == 0


# ── _is_my_review ────────────────────────────────────


class TestIsMyReview:
    def test_explicitly_requested(self):
        """I'm in the requested reviewers list."""
        pr = FakePR(github_requested_reviewers=[{"login": "me"}])
        assert _is_my_review(pr, {"me"}) is True

    def test_reviewed_but_not_approved(self):
        """I submitted a review with changes_requested, still in queue."""
        pr = FakePR(reviews=[FakeReview("me", "CHANGES_REQUESTED", commit_id="abc")])
        assert _is_my_review(pr, {"me"}) is True

    def test_commented_still_in_queue(self):
        """I commented (not approved), still in queue."""
        pr = FakePR(reviews=[FakeReview("me", "COMMENTED", commit_id="abc")])
        assert _is_my_review(pr, {"me"}) is True

    def test_approved_not_in_queue(self):
        """I approved and head hasn't changed, not in queue."""
        pr = FakePR(
            head_sha="abc",
            reviews=[FakeReview("me", "APPROVED", commit_id="abc")],
        )
        assert _is_my_review(pr, {"me"}) is False

    def test_approved_but_author_rebased(self):
        """I approved but author pushed new commits, back in queue."""
        pr = FakePR(
            head_sha="new_sha",
            reviews=[FakeReview("me", "APPROVED", commit_id="old_sha")],
        )
        assert _is_my_review(pr, {"me"}) is True

    def test_never_reviewed_not_requested(self):
        """I never reviewed and I'm not requested, not in queue."""
        pr = FakePR(
            reviews=[FakeReview("someone_else", "APPROVED", commit_id="abc")],
            github_requested_reviewers=[{"login": "someone_else"}],
        )
        assert _is_my_review(pr, {"me"}) is False

    def test_multiple_logins_requested(self):
        """My alternate login is in requested reviewers."""
        pr = FakePR(github_requested_reviewers=[{"login": "my-work"}])
        assert _is_my_review(pr, {"me", "my-work"}) is True

    def test_self_authored_pr_excluded(self):
        """My own PRs never appear in my review queue."""
        pr = FakePR(
            author="me",
            github_requested_reviewers=[{"login": "me"}],
            reviews=[FakeReview("me", "COMMENTED", commit_id="abc")],
        )
        assert _is_my_review(pr, {"me"}) is False

    def test_self_authored_pr_excluded_alt_login(self):
        """My own PR via alternate login is also excluded."""
        pr = FakePR(author="my-work", github_requested_reviewers=[{"login": "me"}])
        assert _is_my_review(pr, {"me", "my-work"}) is False


# ── compute_review_score ──────────────────────────────


class TestComputeReviewScore:
    def test_max_score_scenario(self):
        """Blocking review + CI pass + tiny PR + old + clean merge = high score."""
        score, bd = compute_review_score(
            reviews=[],
            user_logins={"me"},
            ci_status="success",
            total_lines=10,
            mergeable_state="clean",
            created_at=datetime.now(UTC) - timedelta(days=14),
            head_sha="abc",
        )
        # ball=35, ci=20, size=15, age=15, merge=10 = 95
        assert score == 95
        assert bd.review == 35
        assert bd.ci == 20
        assert bd.size == 15
        assert bd.age == 15
        assert bd.mergeable == 10

    def test_already_reviewed_low_score(self):
        """I reviewed and nothing changed, CI failing, huge PR = low score."""
        reviews = [FakeReview("me", "APPROVED", commit_id="abc")]
        score, bd = compute_review_score(
            reviews=reviews,
            user_logins={"me"},
            ci_status="failure",
            total_lines=2000,
            mergeable_state=None,
            created_at=datetime.now(UTC),
            head_sha="abc",
        )
        assert bd.review == 0
        assert bd.ci == 0
        assert bd.size == 0
        assert bd.mergeable == 0
        assert score == 0

    def test_score_never_negative(self):
        """Score is clamped to 0 minimum."""
        score, _ = compute_review_score(
            reviews=[FakeReview("me", "APPROVED", commit_id="abc")],
            user_logins={"me"},
            ci_status="failure",
            total_lines=5000,
            mergeable_state=None,
            created_at=datetime.now(UTC),
            head_sha="abc",
        )
        assert score >= 0

    def test_pending_ci_partial_score(self):
        """Pending CI gives partial points."""
        _, bd = compute_review_score(
            reviews=[],
            user_logins={"me"},
            ci_status="pending",
            total_lines=100,
            mergeable_state="unstable",
            created_at=datetime.now(UTC),
            head_sha="abc",
        )
        assert bd.ci == 8
        assert bd.mergeable == 5
