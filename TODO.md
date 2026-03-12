# PR Dashboard — TODO

## Completed
- [x] PR label/tag support: two-way sync for 5 predefined labels (bug, enhancement, documentation, refactor, testing) between dashboard and GitHub, JSONB storage on PullRequest, colored badge pills on PR cards, toggleable label chips in PRDetailPanel, soft label filter dropdown on RepoView that dims non-matching PRs (2026-03-12)
- [x] Flag "Commented but No Formal Review": detect commenters who left comments (conversation or inline) without submitting a formal GitHub review, surface as amber "Commented (no review)" badge in PRDetailPanel reviewer list and "Unsubmitted review" badge in Priority Queue, +5 owner score bonus, summary stat in owner mode (2026-03-10)
- [x] Priority queue Review/Owner modes: segmented toggle splits priority queue into "Review Queue" (PRs where I'm a requested reviewer, scored by ball-in-my-court logic) and "My PRs" (PRs I authored, scored by action-required signals like failing CI, changes requested, conflicts). Mode-aware tooltips, badges, scoring guide, and summary stats. Unauthenticated users fall back to legacy scoring. Backend filtering by reviewer/author moved server-side. (2026-03-10)
- [x] PR prioritization: cross-repo priority queue ranking all open PRs by computed score (review readiness, CI, size, mergeability, age, rebase, draft penalty), stack-aware merge/review order respecting parent-child dependencies, score breakdown tooltip, new `/prioritize` page with summary bar and ranked list (2026-03-09)
- [x] Custom filter dropdowns + PR state filter + merged PR support: author filter converted to custom avatar dropdown matching reviewer style, new combined state filter (all open, needs review, reviewed, approved, changes requested, draft, recently merged), backend supports returning merged PRs via include_merged_days param, merged PR cards shown with purple border and badge (2026-03-09)
- [x] Collaborative repo ownership + dev impersonation: RepoTracker junction table lets multiple users independently track the same repo with their own token/space; sync uses token fallback across trackers; dev-mode impersonation endpoint + seed script for multi-user testing; frontend dev user switcher (2026-03-09)
- [x] Remove local overrides — sync everything with GitHub: assignees synced from GitHub during sync, assignee/reviewer changes write back to GitHub API, UserProgress table dropped (dashboard mirrors GitHub review state), card assignee display is read-only (edit in detail panel only) (2026-03-09)
- [x] Per-repo visibility (private/shared): visibility moved from spaces to individual repos, each user only sees their own repos unless explicitly shared; spaces simplified to owner-only; alembic migrations run on startup (2026-03-06)
- [x] Space visibility (private/shared): spaces default to private, users can toggle to shared so coworkers see them; repos inherit visibility from parent space; visibility filter applied to spaces and repos API endpoints (2026-03-06)
- [x] Auto-discover spaces + multi-account support: GitHubAccount model, OAuth auto-discovers orgs/personal repos, toggle spaces on/off, link multiple GitHub accounts (personal + enterprise), no manual space creation (2026-03-05)
- [x] Multi-space support with GitHub OAuth: spaces for multiple GitHub orgs/users, OAuth identity flow, per-space tokens with Fernet encryption, grouped OrgOverview, SpaceManager UI, User model replaces TeamMember (2026-03-05)
- [x] User management & PR assignment: team management modal, per-PR assignee dropdown, assignee filter that dims non-matching cards, team progress checkboxes in detail panel (2026-03-05)
- [x] Rebase detection from GitHub reviews: derive review/approval state from actual GitHub reviews, warn when PR is rebased after last approval by comparing review commit_id vs head_sha (2026-03-05)
- [x] Org repo browser: browse and track repos from configured GitHub org instead of manual input (2026-03-05)
- [x] Backend foundation: FastAPI + SQLAlchemy async + all DB models (2026-03-04)
- [x] GitHub client: async httpx wrapper for PRs, checks, reviews (2026-03-04)
- [x] Background sync service with configurable interval (2026-03-04)
- [x] Core API: repos CRUD, PR list/detail, team CRUD, progress tracking (2026-03-04)
- [x] Stack detection algorithm (BFS on head_ref/base_ref) (2026-03-04)
- [x] Stack API endpoints (2026-03-04)
- [x] SSE broadcast service for real-time updates (2026-03-04)
- [x] Auth: HMAC-signed session cookies (2026-03-04)
- [x] Frontend scaffold: React 19 + TypeScript + Vite + Zustand + react-query (2026-03-04)
- [x] Org overview page with repo health cards (2026-03-04)
- [x] Repo view with sortable/filterable PR table (2026-03-04)
- [x] Stack detail page with SVG dependency graph (2026-03-04)
- [x] PR detail slide-out panel (2026-03-04)
- [x] Dark theme with design tokens ported from original dashboard (2026-03-04)
- [x] Alembic async migration setup (2026-03-04)

## In Progress
- [ ] Error trend chart (stacked bar) for stack view

## Planned
- [ ] Notification system for review nudges (email/Slack when someone comments without formal review)
- [ ] Login page UI (currently only API)
- [ ] SSE client-side integration (EventSource in react-query)
- [ ] Quality snapshots collection during sync (parse CI output)
- [ ] Railway deployment config (Railpack)
- [ ] Force-sync button feedback (loading spinner, toast)
- [ ] Mobile responsive layout
- [ ] GitHub Enterprise Server OAuth flow (custom authorize/token URLs per base_url)
