# PR Dashboard

GitHub PR management dashboard for organizations with hierarchical navigation (org → repo → stack), live GitHub sync, dependency graph visualization, and collaborative review tracking.

## Features

- **Org-level overview** — health cards per repo showing open PR count, failing CI, stale PRs, and stack count
- **Repo browser** — browse and track repos from a configured GitHub org, sorted by recent activity
- **Dependency graph** — visual SVG graph of PR relationships based on head/base ref chains, with snake/wrap layout for large stacks
- **Stack detection** — automatic BFS-based detection of stacked PRs from branch relationships
- **Sticky R/A tracking** — per-PR Reviewed/Approved toggles that persist across rebases, with amber warning when HEAD SHA changes after approval
- **Live sync** — background sync every 3 min (configurable), with SSE broadcasts for real-time UI updates
- **PR detail panel** — slide-out panel with diff stats, CI checks, reviews, and tracking toggles
- **Filtering** — filter by author, CI status, stack, and assignee
- **Auth** — optional HMAC-signed session cookie authentication

## Architecture

| Layer | Tech |
|-------|------|
| Backend | FastAPI (async) + SQLAlchemy 2.0 (async) + asyncpg |
| Frontend | React 19 + TypeScript + Vite + Zustand + @tanstack/react-query |
| Database | PostgreSQL |
| Real-time | Server-Sent Events (SSE) |
| Deployment | Docker (multi-stage) on Railway |

## Quick Start

### Backend

```bash
cd backend
cp ../.env.example .env  # Edit with your GitHub token and DB URL
uv pip install -r pyproject.toml
uv run alembic upgrade head
uv run python -m src.main
```

### Frontend

```bash
cd frontend
npm install
npm run dev  # Starts on :5173, proxies /api to :8000
```

### Database

Requires PostgreSQL. On first run, `Base.metadata.create_all` creates tables automatically. For schema changes, use alembic:

```bash
cd backend
uv run alembic upgrade head
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GITHUB_TOKEN` | Fine-grained PAT with read access to PRs, checks, reviews | (required) |
| `GITHUB_ORG` | GitHub organization to browse repos from | (required) |
| `DATABASE_URL` | PostgreSQL async connection string | `postgresql+asyncpg://...` |
| `SYNC_INTERVAL_SECONDS` | Seconds between GitHub sync cycles | `180` |
| `DASHBOARD_PASSWORD` | Optional password for auth (leave empty to disable) | (empty) |
| `SECRET_KEY` | HMAC signing key for session cookies | `change-me-in-production` |

## Project Structure

```
backend/
  src/
    api/          # FastAPI routes (repos, pulls, stacks, team, progress, auth, events)
    config/       # Pydantic settings
    db/           # SQLAlchemy engine + base
    models/       # ORM models (tables.py)
    services/     # GitHub client, sync service, stack detector, SSE events
  alembic/        # Database migrations
frontend/
  src/
    api/          # API client, types, SSE hook
    components/   # Shell, StatusDot, PRDetailPanel, DependencyGraph
    pages/        # OrgOverview, RepoView
    store/        # Zustand UI state
    styles/       # CSS tokens + global styles
```

## Database Schema

### Core Tables

- **tracked_repos** — repos being monitored (owner, name, sync status)
- **pull_requests** — PR metadata synced from GitHub, plus dashboard tracking fields (`head_sha`, `dashboard_reviewed`, `dashboard_approved`, `approved_at_sha`)
- **check_runs** — CI check results per PR
- **reviews** — GitHub review states per PR

### Stack Detection

- **pr_stacks** — detected stacks (groups of related PRs)
- **pr_stack_memberships** — PR-to-stack mapping with position and parent linkage

### Collaboration

- **team_members** — team roster with GitHub login mapping
- **user_progress** — per-member review/approval tracking per PR
- **quality_snapshots** — point-in-time CI/quality metrics per PR

## Deployment

The project ships with a multi-stage Dockerfile. On Railway:

```bash
railway init --name pr-dashboard
railway up -d
railway domain
```

The container runs `alembic upgrade head` before starting uvicorn to ensure schema migrations are applied on each deploy.
