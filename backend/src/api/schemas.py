"""Pydantic response/request schemas for the API."""

from datetime import datetime

from pydantic import BaseModel

# ── Repos ────────────────────────────────────────────────────


class RepoCreate(BaseModel):
    owner: str = ""
    name: str


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


class RepoDetail(BaseModel):
    id: int
    owner: str
    name: str
    full_name: str
    is_active: bool
    default_branch: str
    last_synced_at: datetime | None
    created_at: datetime


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


# ── Team ─────────────────────────────────────────────────────


class TeamMemberCreate(BaseModel):
    display_name: str
    github_login: str | None = None
    email: str | None = None


class TeamMemberUpdate(BaseModel):
    display_name: str | None = None
    github_login: str | None = None
    email: str | None = None
    is_active: bool | None = None


class TeamMemberOut(BaseModel):
    id: int
    display_name: str
    github_login: str | None
    email: str | None
    is_active: bool
    created_at: datetime


# ── Progress ─────────────────────────────────────────────────


class ProgressUpdate(BaseModel):
    team_member_id: int
    reviewed: bool | None = None
    approved: bool | None = None
    notes: str | None = None


class ProgressOut(BaseModel):
    id: int
    pull_request_id: int
    team_member_id: int
    team_member_name: str = ""
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
