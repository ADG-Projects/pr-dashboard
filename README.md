# PR Dashboard

A self-hosted GitHub PR management dashboard with multi-account support, hierarchical navigation, live sync, dependency graph visualization, and a cross-repo priority queue.

Built for teams that work with stacked PRs across multiple GitHub orgs and want a single pane of glass for review readiness, CI health, and merge ordering.

## Highlights

- **Multi-account, multi-org** - Link multiple GitHub accounts (personal, enterprise, different orgs). Each account auto-discovers its orgs as spaces. Toggle spaces on/off to control what gets synced.
- **Hierarchical navigation** - Org overview (health cards per repo) -> Repo view (PRs + dependency graph) -> PR detail panel (diff stats, CI checks, reviews).
- **Stack detection** - Automatic BFS-based detection of stacked PRs from branch relationships. Visual SVG dependency graph with parent-child arrows.
- **Priority queue** - Cross-repo ranked view scoring every open PR on review readiness (35pts), CI status (25pts), size (15pts), mergeability (10pts), age (10pts), and rebase freshness (5pts). Stack-aware ordering respects parent-child dependencies. Manual priority overrides available.
- **Live sync** - GitHub webhooks for instant updates (< 2s), with background polling every 15 minutes as fallback. SSE broadcasts for real-time UI updates. Token fallback across multiple trackers per repo.
- **Collaborative tracking** - Multiple users can independently track the same repo through their own spaces. Assignee and reviewer changes sync bidirectionally with GitHub.
- **Two-layer auth** - Optional password gate (HMAC cookie) + GitHub OAuth identity (separate cookie). Either, both, or neither.

## Architecture

| Layer | Tech |
|-------|------|
| Backend | FastAPI (async) + SQLAlchemy 2.0 (async) + asyncpg |
| Frontend | React 19 + TypeScript + Vite + Zustand + @tanstack/react-query |
| Database | PostgreSQL |
| Real-time | Server-Sent Events (SSE) with exponential backoff reconnection |
| Deployment | Docker (multi-stage) / Railway (Railpack) |

## Quick Start

### Prerequisites

- Python 3.12+ with [uv](https://docs.astral.sh/uv/)
- Node.js 22+
- PostgreSQL 15+

### 1. Clone and configure

```bash
git clone https://github.com/ADG-Projects/pr-dashboard.git
cd pr-dashboard
cp .env.example .env
# Edit .env with your database URL and (optionally) OAuth credentials
```

### 2. Start the backend

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run python -m src.main
```

The backend starts on `http://localhost:8000`. Alembic migrations run automatically to set up the database schema.

### 3. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend starts on `http://localhost:5173` and proxies `/api` requests to the backend.

### 4. Connect your GitHub account

Open the dashboard and click **Sign in with GitHub** (if OAuth is configured) or use the Space Manager to link a Personal Access Token. The app auto-discovers your orgs and creates spaces for each one.

### GitHub OAuth Setup

1. Register an OAuth App at [github.com/settings/developers](https://github.com/settings/developers)
2. **Homepage URL**: `http://localhost:5173` (dev) or your production URL
3. **Callback URL**: `http://localhost:8000/api/auth/github/callback`
4. Set `GITHUB_OAUTH_CLIENT_ID` and `GITHUB_OAUTH_CLIENT_SECRET` in `.env`

### GitHub Webhooks (Optional)

Webhooks enable instant PR updates (< 2 seconds) instead of waiting for the next polling cycle. When configured, polling is automatically reduced to a 15-minute fallback.

1. Generate a webhook secret:
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

2. Set up a public tunnel for local development:
   ```bash
   brew install cloudflared
   cloudflared tunnel --url http://localhost:8000
   # Gives you a public https://*.trycloudflare.com URL
   ```

3. Add to `.env`:
   ```
   GITHUB_WEBHOOK_SECRET=your-generated-secret
   WEBHOOK_BASE_URL=https://your-tunnel.trycloudflare.com
   ```

4. Start the backend. Webhooks are auto-registered when you add repos. For existing repos:
   ```bash
   curl -X POST http://localhost:8000/api/webhooks/admin/register-all
   ```

5. Check webhook status:
   ```bash
   curl http://localhost:8000/api/webhooks/admin/status
   ```

## Features in Detail

### Org Overview

The landing page shows all tracked repos grouped by space as health cards. Each card displays:
- Open PR count
- Failing CI count (PRs with at least one failing check)
- Stale PR count (open 7+ days with no recent updates)
- Stack count

A repo browser modal lets you add repos from any discovered org, sorted by recent activity.

### Repo View

Drill into a repo to see all its PRs with a rich set of filters that work together:

| Filter | Options |
|--------|---------|
| Author | Avatar dropdown of PR authors |
| State | Open, all, needs review, reviewed, approved, changes requested, draft, merged (last 7 days) |
| Reviewer | Avatar dropdown of requested reviewers |
| CI Status | Pass, fail, pending, unknown |
| Priority | High, normal, low |
| Stack | Dropdown of detected stacks |

### Dependency Graph

PRs are visualized as an SVG tree based on head/base ref relationships:
- Depth-first recursive layout (depth = column, siblings stacked vertically)
- Bezier curve arrows connecting parent to child PRs
- Stack labels with optional renaming
- Hover highlighting to show all members of a stack
- Dimming for filtered authors/reviewers
- Standalone PRs displayed in a flexbox grid below

### Priority Queue

The `/prioritize` page ranks all open PRs across all repos by a computed score (0-100):

| Signal | Max Points | Details |
|--------|-----------|---------|
| Review readiness | 35 | Approved=35, reviewed=20, none=10, changes requested=0 |
| CI status | 25 | Success=25, pending=10, unknown=5, failure=0 |
| Size (inverse) | 15 | <=50 lines=15, <=200=12, <=500=8, <=1000=4, >1000=0 |
| Mergeability | 10 | Clean=10, unstable=5, conflicts=0 |
| Age | 10 | Linear 0-10 over 7 days |
| Rebase freshness | 5 | +5 if not rebased since last approval |
| Draft penalty | -30 | Applied to all draft PRs |

Stack-aware ordering ensures parents always appear before their children. Manual priority (high/low) overrides score-based ordering.

### PR Detail Panel

A slide-out panel for any PR showing:
- Priority toggle (High / Normal / Low)
- Requested reviewers with search dropdown for adding team members
- Diff stats (files changed, additions, deletions)
- CI checks table with links to details
- Reviews list with approval/changes/comment states
- Rebase warning when PR is rebased after the last approval

### Space Management

- **Auto-discovery**: On OAuth login, the app discovers your orgs and creates a space for each
- **Multi-account**: Link multiple GitHub accounts (personal + enterprise + different orgs)
- **Token linking**: Manual PAT input for GitHub Enterprise or fine-grained tokens, with guides for both classic and fine-grained PAT creation
- **Space toggling**: Show/hide spaces to control which orgs are synced
- **Account unlinking**: Remove an account and all its spaces

### Team View

Lists all users in two sections:
- **Signed in** - Users who authenticated via OAuth, with their linked accounts
- **Discovered from PRs** - Shadow users found in PR data (authors, reviewers)

### Real-time Updates

- SSE endpoint broadcasts sync completions, space discoveries, and errors
- Frontend reconnects with exponential backoff (1s to 30s max)
- Connection status indicator in the app shell ("Disconnected" warning)
- Automatic query invalidation on sync events

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL async connection string | `postgresql+asyncpg://postgres:postgres@localhost:5432/pr_dashboard` |
| `GITHUB_OAUTH_CLIENT_ID` | GitHub OAuth App client ID | (empty) |
| `GITHUB_OAUTH_CLIENT_SECRET` | GitHub OAuth App client secret | (empty) |
| `SECRET_KEY` | Session cookies + token encryption key | `change-me-in-production` |
| `DASHBOARD_PASSWORD` | Password gate (leave empty to disable) | (empty) |
| `SYNC_INTERVAL_SECONDS` | Seconds between GitHub sync cycles | `180` |
| `GITHUB_WEBHOOK_SECRET` | Secret for webhook signature validation (HMAC-SHA256) | (empty) |
| `WEBHOOK_BASE_URL` | Public URL for webhook callbacks (e.g. cloudflared tunnel) | (empty) |
| `DEV_MODE` | Enable dev features (user impersonation) | `false` |
| `HOST` | Server bind address | `0.0.0.0` |
| `PORT` | Server port | `8000` |
| `LOG_LEVEL` | Logging level | `INFO` |

## Project Structure

```
backend/
  src/
    api/            # FastAPI routes
      auth.py       #   OAuth flow, password gate, dev login
      accounts.py   #   GitHub account linking + discovery
      repos.py      #   Repo CRUD, visibility, repo browser
      pulls.py      #   PR list/detail, assignee/reviewer sync
      prioritize.py #   Cross-repo priority scoring
      stacks.py     #   Stack CRUD + rename
      spaces.py     #   Space toggle + listing
      team.py       #   User management
      events.py     #   SSE endpoint
      webhooks.py   #   GitHub webhook receiver
      webhook_admin.py # Webhook registration + status
    config/         # Pydantic settings from env vars
    db/             # SQLAlchemy async engine + base
    models/
      tables.py     # All ORM models (User, GitHubAccount, Space,
                    #   TrackedRepo, RepoTracker, PullRequest,
                    #   CheckRun, Review, PRStack, PRStackMembership)
    services/
      github_client.py  # Async GitHub API wrapper (supports GHE)
      sync_service.py   # Background sync loop + token fallback
      stack_detector.py # BFS stack detection algorithm
      discovery.py      # Auto-discover orgs + personal spaces
      crypto.py         # Fernet token encryption
      events.py         # SSE broadcast system
  alembic/          # Database migrations (11 versions)

frontend/
  src/
    api/
      client.ts     # Fetch wrapper + TypeScript types
      useSSE.ts     # EventSource hook with reconnection
    components/
      Shell.tsx           # App shell, nav, user menu, SSE indicator
      SpaceManager.tsx    # Account + space management modal
      TeamPanel.tsx       # Team listing modal
      PRDetailPanel.tsx   # PR detail slide-out
      DependencyGraph.tsx # SVG dependency tree
      StatusDot.tsx       # CI/review status indicator
      DevUserSwitcher.tsx # Dev-mode user switcher
    pages/
      OrgOverview.tsx     # Repo health cards by space
      RepoView.tsx        # PR list + filters + graph
      PrioritizeView.tsx  # Cross-repo priority queue
      Login.tsx           # Password gate + OAuth errors
    store/
      useStore.ts   # Zustand UI state
    styles/         # CSS tokens + global dark theme
```

## Database Schema

### Identity & Connections
- **users** - GitHub users from OAuth (github_id, login, name, avatar_url)
- **github_accounts** - Linked accounts per user (encrypted token, base_url for GHE)
- **spaces** - Auto-discovered orgs/personal accounts (name, slug, type, is_active)

### Core Tracking
- **tracked_repos** - Monitored repos (owner, name, sync status, webhook_id)
- **repo_trackers** - Junction table: users independently track repos with their own space + visibility
- **pull_requests** - PR metadata synced from GitHub (state, draft, refs, diff stats, mergeable_state, manual_priority)

### CI & Reviews
- **check_runs** - CI check results per PR (name, status, conclusion, details_url)
- **reviews** - GitHub review states per PR (reviewer, state, commit_id for rebase detection)

### Stacks
- **pr_stacks** - Detected stacks (name, root PR)
- **pr_stack_memberships** - PR-to-stack mapping with position and parent linkage

## Deployment

### Docker

The project ships with a multi-stage Dockerfile (Node 22 for frontend build, Python 3.12 for runtime):

```bash
docker build -t pr-dashboard .
docker run -p 8000:8000 --env-file .env pr-dashboard
```

The container runs `alembic upgrade head` before starting uvicorn.

### Railway

```bash
railway init --name pr-dashboard
railway up -d
railway domain
```

Railway auto-detects the Dockerfile. Add a PostgreSQL plugin for the database; Railway injects `DATABASE_URL` automatically. Set OAuth credentials and `SECRET_KEY` as environment variables.

## Development

```bash
# Backend: format + lint
cd backend && uv run ruff format src/ && uv run ruff check src/

# Frontend: type check
cd frontend && npx tsc --noEmit

# Frontend: production build
cd frontend && npm run build

# New database migration
cd backend && uv run alembic revision --autogenerate -m "description"
```

### Dev Mode

Set `DEV_MODE=true` to enable user impersonation for testing multi-user scenarios:

```bash
# Seed fake users (shares your real token)
cd backend && uv run python scripts/seed_dev_users.py

# Switch user in the UI via the DevUserSwitcher dropdown
# Or via API: POST /api/auth/dev-login/{user_id}
```

## License

MIT
