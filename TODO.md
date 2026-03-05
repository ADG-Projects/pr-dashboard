# PR Dashboard — TODO

## Completed
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
- [ ] Login page UI (currently only API)
- [ ] SSE client-side integration (EventSource in react-query)
- [ ] Quality snapshots collection during sync (parse CI output)
- [ ] Railway deployment config (Railpack)
- [ ] Force-sync button feedback (loading spinner, toast)
- [ ] Mobile responsive layout
- [ ] GitHub Enterprise Server support (non-EMU, custom API base URL)
