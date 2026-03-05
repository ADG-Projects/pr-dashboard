"""Pydantic response/request schemas for the API."""

from datetime import datetime

from pydantic import BaseModel

# ── Spaces ───────────────────────────────────────────────────


class SpaceCreate(BaseModel):
    name: str
    slug: str
    space_type: str = "org"  # "org" or "user"
    base_url: str = "https://api.github.com"
    token: str = ""  # PAT or "use_oauth"


class SpaceUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    space_type: str | None = None
    base_url: str | None = None
    token: str | None = None


class SpaceOut(BaseModel):
    id: int
    name: str
    slug: str
    space_type: str
    base_url: str
    is_active: bool
    has_token: bool
    created_at: datetime


# ── Repos ────────────────────────────────────────────────────


class RepoCreate(BaseModel):
    owner: str = ""
    name: str
    space_id: int | None = None


class AvailableRepo(BaseModel):
    name: str
    full_name: str
    description: str | None = None
    private: bool = False
    pushed_at: str | None = None


class RepoSummary(BaseModel):
    id: int
    owner: str
    name: str
    full_name: str
    is_active: bool
    default_branch: str
    last_synced_at: datetime | None
    open_pr_count: int = 0
    failing_ci_count: int = 0
    stale_pr_count: int = 0
    stack_count: int = 0
    space_id: int | None = None
    space_name: str | None = None


class RepoDetail(BaseModel):
    id: int
    owner: str
    name: str
    full_name: str
    is_active: bool
    default_branch: str
    last_synced_at: datetime | None
    created_at: datetime
    space_id: int | None = None


# ── Pull Requests ────────────────────────────────────────────


class CheckRunOut(BaseModel):
    id: int
    name: str
    status: str
    conclusion: str | None
    details_url: str | None


class ReviewOut(BaseModel):
    id: int
    reviewer: str
    state: str
    submitted_at: datetime


class PRSummary(BaseModel):
    id: int
    number: int
    title: str
    state: str
    draft: bool
    head_ref: str
    base_ref: str
    author: str
    additions: int
    deletions: int
    changed_files: int
    mergeable_state: str | None
    html_url: str
    created_at: datetime
    updated_at: datetime
    ci_status: str = "unknown"  # computed: success, failure, pending, unknown
    review_state: str = "none"  # computed: approved, changes_requested, reviewed, none
    stack_id: int | None = None
    assignee_id: int | None = None
    assignee_name: str | None = None
    rebased_since_approval: bool = False


class PRDetail(PRSummary):
    check_runs: list[CheckRunOut] = []
    reviews: list[ReviewOut] = []


# ── Stacks ───────────────────────────────────────────────────


class StackMemberOut(BaseModel):
    pull_request_id: int
    position: int
    parent_pr_id: int | None
    pr: PRSummary


class StackOut(BaseModel):
    id: int
    name: str | None
    root_pr_id: int | None
    detected_at: datetime
    members: list[StackMemberOut] = []


# ── Users (from GitHub OAuth) ────────────────────────────────


class UserUpdate(BaseModel):
    is_active: bool | None = None


class UserOut(BaseModel):
    id: int
    login: str
    name: str | None
    avatar_url: str | None
    is_active: bool
    created_at: datetime


# ── Progress ─────────────────────────────────────────────────


class ProgressUpdate(BaseModel):
    user_id: int
    reviewed: bool | None = None
    approved: bool | None = None
    notes: str | None = None


class ProgressOut(BaseModel):
    id: int
    pull_request_id: int
    user_id: int
    user_name: str = ""
    reviewed: bool
    approved: bool
    notes: str | None
    updated_at: datetime


# ── Quality ──────────────────────────────────────────────────


class QualitySnapshotOut(BaseModel):
    id: int
    pull_request_id: int
    pytest_passed: int
    pytest_failed: int
    pytest_errors: int
    mypy_errors: int
    snapshot_at: datetime


# ── Assignee ─────────────────────────────────────────────────


class AssigneeUpdate(BaseModel):
    assignee_id: int | None = None



# ── Auth ─────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    password: str


class AuthStatus(BaseModel):
    authenticated: bool
    auth_enabled: bool
    user: dict | None = None
