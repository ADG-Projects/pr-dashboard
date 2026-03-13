"""Pydantic response/request schemas for the API."""

import ipaddress
import socket
from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, field_validator

from src.config.settings import settings

# ── Spaces ───────────────────────────────────────────────────


class SpaceToggle(BaseModel):
    is_active: bool


class SpaceOut(BaseModel):
    id: int
    name: str
    slug: str
    space_type: str
    base_url: str
    is_active: bool
    has_token: bool
    created_at: datetime
    github_account_id: int | None = None
    github_account_login: str | None = None


_ALLOWED_GITHUB_DOMAINS = {"api.github.com", "github.com"}


class GitHubAccountCreate(BaseModel):
    token: str
    base_url: str = "https://api.github.com"

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        parsed = urlparse(v.rstrip("/"))

        # Require https in production, allow http in dev mode
        allowed_schemes = {"https", "http"} if settings.dev_mode else {"https"}
        if parsed.scheme not in allowed_schemes:
            raise ValueError(f"base_url must use {' or '.join(allowed_schemes)} scheme")

        if not parsed.hostname:
            raise ValueError("base_url must have a valid hostname")

        # Block private/reserved IP ranges (SSRF protection)
        if _is_private_ip(parsed.hostname):
            raise ValueError("base_url must not point to a private or reserved IP address")

        # Check against allowed domains
        hostname = parsed.hostname.lower()
        allowed = _ALLOWED_GITHUB_DOMAINS.copy()

        # Add user-configured GHE domains
        ghe_domains = settings.allowed_ghe_domains.strip()
        if ghe_domains:
            for domain in ghe_domains.split(","):
                domain = domain.strip().lower()
                if domain:
                    allowed.add(domain)

        if not any(hostname == d or hostname.endswith(f".{d}") for d in allowed):
            raise ValueError(
                f"base_url must be a recognized GitHub domain "
                f"({', '.join(sorted(allowed))}). "
                f"Configure ALLOWED_GHE_DOMAINS for GitHub Enterprise."
            )

        return v


class AddSpaceRequest(BaseModel):
    slug: str  # org login or username
    space_type: str = "org"  # "org" or "user"
    name: str | None = None  # display name, defaults to slug


class GitHubAccountOut(BaseModel):
    id: int
    login: str
    avatar_url: str | None
    base_url: str
    has_token: bool
    created_at: datetime
    last_login_at: datetime


# ── Repos ────────────────────────────────────────────────────


class RepoCreate(BaseModel):
    owner: str = ""
    name: str
    space_id: int | None = None


class RepoVisibilityUpdate(BaseModel):
    visibility: str  # "private" or "shared"


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
    visibility: str = "private"
    user_id: int | None = None
    tracker_count: int = 1


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
    visibility: str = "private"
    user_id: int | None = None


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
    github_requested_reviewers: list[dict] = []
    all_reviewers: list[dict] = []
    rebased_since_approval: bool = False
    merged_at: datetime | None = None
    manual_priority: str | None = None
    commenters_without_review: list[str] = []


class WorkItemOut(BaseModel):
    id: int
    work_item_id: int
    title: str
    state: str
    work_item_type: str
    url: str
    assigned_to: str | None


class PRDetail(PRSummary):
    check_runs: list[CheckRunOut] = []
    reviews: list[ReviewOut] = []
    work_items: list[WorkItemOut] = []


# ── Stacks ───────────────────────────────────────────────────


class StackMemberOut(BaseModel):
    pull_request_id: int
    position: int
    parent_pr_id: int | None
    pr: PRSummary


class StackRename(BaseModel):
    name: str


class StackOut(BaseModel):
    id: int
    name: str | None
    root_pr_id: int | None
    detected_at: datetime
    members: list[StackMemberOut] = []


# ── Users (from GitHub OAuth) ────────────────────────────────


class UserUpdate(BaseModel):
    is_active: bool | None = None


class LinkedAccount(BaseModel):
    login: str
    avatar_url: str | None
    space_slugs: list[str] = []


class UserOut(BaseModel):
    id: int
    login: str
    name: str | None
    avatar_url: str | None
    is_active: bool
    created_at: datetime
    linked_accounts: list[LinkedAccount] = []


# ── Assignee ─────────────────────────────────────────────────


class PriorityBreakdown(BaseModel):
    review: int
    ci: int
    size: int
    mergeable: int
    age: int
    rebase: int
    draft_penalty: int


class PrioritizedPROut(BaseModel):
    pr: PRSummary
    repo_full_name: str
    repo_id: int
    priority_score: int
    priority_breakdown: PriorityBreakdown
    merge_position: int
    blocked_by_pr_id: int | None = None
    stack_id: int | None = None
    stack_name: str | None = None
    priority_tier: str = "normal"
    mode: str = "default"


class PriorityUpdate(BaseModel):
    priority: str | None = None


class AssigneeUpdate(BaseModel):
    assignee_id: int | None = None


class ReviewerUpdate(BaseModel):
    add_user_ids: list[int] = []
    remove_logins: list[str] = []


# ── ADO Accounts ────────────────────────────────────────────


_ALLOWED_ADO_DOMAINS = {
    "dev.azure.com",
    "visualstudio.com",
}


def _is_private_ip(hostname: str) -> bool:
    """Check if a hostname resolves to a private/reserved IP address."""
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for _, _, _, _, sockaddr in addr_info:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
    except socket.gaierror:
        pass
    return False


class AdoAccountCreate(BaseModel):
    token: str
    org_url: str
    project: str

    @field_validator("org_url")
    @classmethod
    def validate_org_url(cls, v: str) -> str:
        parsed = urlparse(v.rstrip("/"))

        # Require https in production, allow http in dev mode
        allowed_schemes = {"https", "http"} if settings.dev_mode else {"https"}
        if parsed.scheme not in allowed_schemes:
            raise ValueError(f"org_url must use {' or '.join(allowed_schemes)} scheme")

        if not parsed.hostname:
            raise ValueError("org_url must have a valid hostname")

        # Block private/reserved IP ranges (SSRF protection)
        if _is_private_ip(parsed.hostname):
            raise ValueError("org_url must not point to a private or reserved IP address")

        # Restrict to known ADO domains
        hostname = parsed.hostname.lower()
        if not any(hostname == d or hostname.endswith(f".{d}") for d in _ALLOWED_ADO_DOMAINS):
            raise ValueError(
                f"org_url must be a recognized Azure DevOps domain "
                f"({', '.join(sorted(_ALLOWED_ADO_DOMAINS))})"
            )

        return v


class AdoAccountOut(BaseModel):
    id: int
    org_url: str
    project: str
    display_name: str | None
    has_token: bool
    created_at: datetime


# ── Auth ─────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    password: str


class AuthStatus(BaseModel):
    authenticated: bool
    auth_enabled: bool
    oauth_configured: bool = False
    user: dict | None = None
