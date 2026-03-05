# PR Dashboard

GitHub PR management dashboard with multi-space support (multiple orgs/users), hierarchical zoom (org → repo → stack), live GitHub sync, and collaborative review tracking.

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
Requires PostgreSQL. Tables auto-create on startup via `Base.metadata.create_all`.
For migrations: `cd backend && uv run alembic upgrade head`

## Architecture

- **Backend**: FastAPI (async) + SQLAlchemy 2.0 (async) + asyncpg
- **Frontend**: React 19 + TypeScript + Vite + Zustand + @tanstack/react-query
- **Database**: PostgreSQL
- **Real-time**: Server-Sent Events (SSE)

### Key Design Decisions
- **Two-layer auth**: password gate (HMAC cookie) + GitHub OAuth identity (separate cookie)
- **Multi-account**: Users can link multiple GitHub accounts (GitHubAccount model), each with its own token + base_url (supports GitHub.com + GHE)
- **Auto-discovery**: On OAuth login, the app calls `/user/orgs` + `/user` to auto-create Space rows for each org and the personal account
- **Spaces**: each space = a discovered org or personal account, linked to a GitHubAccount for its token. Users toggle spaces on/off to control which orgs are synced.
- Background sync loop runs per-active-space, getting token from `space.github_account`
- Stack detection via BFS on `head_ref`/`base_ref` relationships between open PRs
- SSE broadcasts progress updates and sync completions to connected clients
- Token encryption via Fernet (key derived from SECRET_KEY)
- Users are created via GitHub OAuth login (replaced manual TeamMember roster)

## Project Structure

```
backend/
  src/
    api/          # FastAPI routes (accounts, repos, spaces, pulls, stacks, team, progress, auth, events)
    config/       # Pydantic settings
    db/           # SQLAlchemy engine + base
    models/       # ORM models (tables.py) — User, GitHubAccount, Space, TrackedRepo, PullRequest, etc.
    services/     # GitHub client, sync service, stack detector, SSE events, crypto, discovery
  alembic/        # Database migrations
frontend/
  src/
    api/          # API client + types
    components/   # Shell, SpaceManager, TeamPanel, StatusDot, PRDetailPanel, DependencyGraph
    pages/        # OrgOverview, RepoView
    store/        # Zustand UI state
    styles/       # CSS tokens + global styles
```

## Common Commands

```bash
# Backend
cd backend && uv run python -m src.main                    # Run server
cd backend && uv run ruff format src/ && uv run ruff check src/  # Lint
cd backend && uv run alembic revision --autogenerate -m "description"  # New migration

# Frontend
cd frontend && npm run dev       # Dev server with HMR
cd frontend && npm run build     # Production build
cd frontend && npx tsc --noEmit  # Type check
```

## Environment Variables

See `.env.example` for all options. Key ones:
- `GITHUB_OAUTH_CLIENT_ID` / `GITHUB_OAUTH_CLIENT_SECRET` — GitHub OAuth App credentials
- `DATABASE_URL` — PostgreSQL async connection string
- `SECRET_KEY` — Used for session cookies AND token encryption (change in production!)
- `DASHBOARD_PASSWORD` — Optional, enables password gate (leave empty to disable)
- `GITHUB_TOKEN` / `GITHUB_ORG` — Legacy, used only for migration seeding
