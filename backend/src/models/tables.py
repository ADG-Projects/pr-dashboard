"""Database models for the PR Dashboard."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class RepoTracker(Base):
    """Junction table: each user independently tracks a repo through their own space."""

    __tablename__ = "repo_trackers"
    __table_args__ = (UniqueConstraint("user_id", "repo_id", name="uq_user_repo_tracker"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_repos.id", ondelete="CASCADE"), nullable=False
    )
    space_id: Mapped[int | None] = mapped_column(
        ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True
    )
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, server_default="private")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    repo: Mapped["TrackedRepo"] = relationship(back_populates="trackers")
    space: Mapped["Space | None"] = relationship(foreign_keys=[space_id])


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    github_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    login: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(1024))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    github_accounts: Mapped[list["GitHubAccount"]] = relationship(back_populates="user")


class GitHubAccount(Base):
    __tablename__ = "github_accounts"
    __table_args__ = (UniqueConstraint("user_id", "github_id", name="uq_user_github_account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    github_id: Mapped[int] = mapped_column(Integer, nullable=False)
    login: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(1024))
    encrypted_token: Mapped[str | None] = mapped_column(Text)
    base_url: Mapped[str] = mapped_column(
        String(1024), nullable=False, default="https://api.github.com"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="github_accounts")
    spaces: Mapped[list["Space"]] = relationship(back_populates="github_account")


class Space(Base):
    __tablename__ = "spaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    space_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "org" or "user"
    github_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("github_accounts.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    github_account: Mapped["GitHubAccount | None"] = relationship(back_populates="spaces")
    user: Mapped["User | None"] = relationship(foreign_keys=[user_id])


class TrackedRepo(Base):
    __tablename__ = "tracked_repos"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    trackers: Mapped[list["RepoTracker"]] = relationship(
        back_populates="repo", cascade="all, delete-orphan"
    )
    pull_requests: Mapped[list["PullRequest"]] = relationship(back_populates="repo")
    stacks: Mapped[list["PRStack"]] = relationship(back_populates="repo")


class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (UniqueConstraint("repo_id", "number", name="uq_repo_pr_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("tracked_repos.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)  # open, closed, merged
    draft: Mapped[bool] = mapped_column(Boolean, default=False)
    head_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    base_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    changed_files: Mapped[int] = mapped_column(Integer, default=0)
    mergeable_state: Mapped[str | None] = mapped_column(String(50))
    html_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    head_sha: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    repo: Mapped["TrackedRepo"] = relationship(back_populates="pull_requests")
    check_runs: Mapped[list["CheckRun"]] = relationship(
        back_populates="pull_request", cascade="all, delete-orphan"
    )
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="pull_request", cascade="all, delete-orphan"
    )
    stack_memberships: Mapped[list["PRStackMembership"]] = relationship(
        back_populates="pull_request",
        foreign_keys="PRStackMembership.pull_request_id",
    )
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assignee: Mapped["User | None"] = relationship(foreign_keys=[assignee_id])
    github_requested_reviewers: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), default=list
    )
    quality_snapshots: Mapped[list["QualitySnapshot"]] = relationship(
        back_populates="pull_request", cascade="all, delete-orphan"
    )


class CheckRun(Base):
    __tablename__ = "check_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    pull_request_id: Mapped[int] = mapped_column(ForeignKey("pull_requests.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    conclusion: Mapped[str | None] = mapped_column(String(50))  # success, failure, etc.
    details_url: Mapped[str | None] = mapped_column(String(1024))
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    pull_request: Mapped["PullRequest"] = relationship(back_populates="check_runs")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    pull_request_id: Mapped[int] = mapped_column(ForeignKey("pull_requests.id", ondelete="CASCADE"))
    reviewer: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(50), nullable=False)
    commit_id: Mapped[str | None] = mapped_column(String(40))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    pull_request: Mapped["PullRequest"] = relationship(back_populates="reviews")


class PRStack(Base):
    __tablename__ = "pr_stacks"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("tracked_repos.id", ondelete="CASCADE"))
    name: Mapped[str | None] = mapped_column(String(255))
    root_pr_id: Mapped[int | None] = mapped_column(
        ForeignKey("pull_requests.id", ondelete="SET NULL")
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    repo: Mapped["TrackedRepo"] = relationship(back_populates="stacks")
    root_pr: Mapped["PullRequest | None"] = relationship(foreign_keys=[root_pr_id])
    memberships: Mapped[list["PRStackMembership"]] = relationship(
        back_populates="stack", cascade="all, delete-orphan"
    )


class PRStackMembership(Base):
    __tablename__ = "pr_stack_memberships"

    id: Mapped[int] = mapped_column(primary_key=True)
    stack_id: Mapped[int] = mapped_column(ForeignKey("pr_stacks.id", ondelete="CASCADE"))
    pull_request_id: Mapped[int] = mapped_column(ForeignKey("pull_requests.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_pr_id: Mapped[int | None] = mapped_column(
        ForeignKey("pull_requests.id", ondelete="SET NULL")
    )

    stack: Mapped["PRStack"] = relationship(back_populates="memberships")
    pull_request: Mapped["PullRequest"] = relationship(
        foreign_keys=[pull_request_id], back_populates="stack_memberships"
    )
    parent_pr: Mapped["PullRequest | None"] = relationship(foreign_keys=[parent_pr_id])


class QualitySnapshot(Base):
    __tablename__ = "quality_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    pull_request_id: Mapped[int] = mapped_column(ForeignKey("pull_requests.id", ondelete="CASCADE"))
    pytest_passed: Mapped[int] = mapped_column(Integer, default=0)
    pytest_failed: Mapped[int] = mapped_column(Integer, default=0)
    pytest_errors: Mapped[int] = mapped_column(Integer, default=0)
    mypy_errors: Mapped[int] = mapped_column(Integer, default=0)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    pull_request: Mapped["PullRequest"] = relationship(back_populates="quality_snapshots")
