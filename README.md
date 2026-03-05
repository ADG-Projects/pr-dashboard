# PR Dashboard

GitHub PR management dashboard with multi-space support, hierarchical navigation (org → repo → stack), live GitHub sync, dependency graph visualization, and collaborative review tracking.

## Features

- **Multi-space support** — connect multiple GitHub orgs/users, each with their own token (PAT or OAuth)
- **GitHub OAuth identity** — "Connect GitHub" flow for avatars, names, and token sharing across spaces
- **Two-layer auth** — optional password gate + GitHub OAuth identity (independent layers)
- **Org-level overview** — health cards per repo grouped by space, showing open PR count, failing CI, stale PRs, and stack count
- **Repo browser** — browse and track repos per space, sorted by recent activity
- **Dependency graph** — visual SVG graph of PR relationships based on head/base ref chains, with snake/wrap layout for large stacks
- **Stack detection** — automatic BFS-based detection of stacked PRs from branch relationships
- **Sticky R/A tracking** — per-PR Reviewed/Approved toggles that persist across rebases, with amber warning when HEAD SHA changes after approval
- **Live sync** — background sync every 3 min (configurable), with SSE broadcasts for real-time UI updates
- **PR detail panel** — slide-out panel with diff stats, CI checks, reviews, and tracking toggles
- **Filtering** — filter by author, CI status, stack, and assignee
- **Token encryption** — Fernet-based encryption for all stored GitHub tokens

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
cp ../.env.example .env  # Edit with your tokens and DB URL
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

### GitHub OAuth Setup

1. Register an OAuth App at [github.com/settings/developers](https://github.com/settings/developers)
2. Homepage URL: `http://localhost:5173` (dev) or your production URL
3. Callback URL: `http://localhost:8000/api/auth/github/callback` (dev)
4. Set `GITHUB_OAUTH_CLIENT_ID` and `GITHUB_OAUTH_CLIENT_SECRET` in `.env`

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GITHUB_TOKEN` | Legacy PAT (for migration seeding, optional) | (empty) |
| `GITHUB_ORG` | Legacy org (for migration seeding, optional) | (empty) |
| `GITHUB_OAUTH_CLIENT_ID` | GitHub OAuth App client ID | (empty) |
| `GITHUB_OAUTH_CLIENT_SECRET` | GitHub OAuth App client secret | (empty) |
| `DATABASE_URL` | PostgreSQL async connection string | `postgresql+asyncpg://...` |
| `SYNC_INTERVAL_SECONDS` | Seconds between GitHub sync cycles | `180` |
| `DASHBOARD_PASSWORD` | Optional password for auth (leave empty to disable) | (empty) |
| `SECRET_KEY` | HMAC signing key for session cookies + token encryption | `change-me-in-production` |

## Project Structure

```
backend/
  src/
    api/          # FastAPI routes (repos, spaces, pulls, stacks, team, progress, auth, events)
    config/       # Pydantic settings
    db/           # SQLAlchemy engine + base
    models/       # ORM models (tables.py)
    services/     # GitHub client, sync service, stack detector, SSE events, crypto
  alembic/        # Database migrations
frontend/
  src/
    api/          # API client, types, SSE hook
    components/   # Shell, SpaceManager, StatusDot, PRDetailPanel, DependencyGraph
    pages/        # OrgOverview, RepoView
    store/        # Zustand UI state
    styles/       # CSS tokens + global styles
```

## Database Schema

### Identity & Connections

- **users** — GitHub users from OAuth login (github_id, login, name, avatar_url, encrypted_token)
- **spaces** — GitHub connections (name, slug, type org/user, base_url, encrypted_token)

### Core Tables

- **tracked_repos** — repos being monitored (owner, name, space_id FK, sync status)
- **pull_requests** — PR metadata synced from GitHub (assignee_id FK to users)
- **check_runs** — CI check results per PR
- **reviews** — GitHub review states per PR

### Stack Detection

- **pr_stacks** — detected stacks (groups of related PRs)
- **pr_stack_memberships** — PR-to-stack mapping with position and parent linkage

### Collaboration

- **user_progress** — per-user review/approval tracking per PR
- **quality_snapshots** — point-in-time CI/quality metrics per PR

## Deployment

The project ships with a multi-stage Dockerfile. On Railway:

```bash
railway init --name pr-dashboard
railway up -d
railway domain
```

The container runs `alembic upgrade head` before starting uvicorn to ensure schema migrations are applied on each deploy.
