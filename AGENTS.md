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
Requires PostgreSQL. Alembic migrations run automatically on startup.
For manual migrations: `cd backend && uv run alembic upgrade head`

## Architecture

- **Backend**: FastAPI (async) + SQLAlchemy 2.0 (async) + asyncpg
- **Frontend**: React 19 + TypeScript + Vite + Zustand + @tanstack/react-query
- **Database**: PostgreSQL
- **Real-time**: Server-Sent Events (SSE)

### Key Design Decisions
- **Two-layer auth**: password gate (HMAC cookie) + GitHub OAuth identity (separate cookie)
- **Multi-account**: Users can link multiple GitHub accounts (GitHubAccount model), each with its own token + base_url (supports GitHub.com + GHE)
- **Auto-discovery**: On OAuth login, the app calls `/user/orgs` + `/user` to auto-create Space rows for each org and the personal account
- **Spaces**: each space = a discovered org or personal account, linked to a GitHubAccount for its token. Users toggle spaces on/off to control which orgs are synced. Spaces are owner-only (no shared concept).
- **Collaborative repo ownership**: `RepoTracker` junction table links Users to TrackedRepos. Each user independently tracks a repo through their own space (and token). Multiple users can track the same repo. `visibility` and `space_id` live on RepoTracker, not TrackedRepo. Repos with zero trackers are deactivated.
- **Token fallback**: Sync iterates all active TrackedRepos (not spaces), resolving a token from any tracker's space. If one tracker's token fails, the next is tried.
- **Dev impersonation**: `DEV_MODE=true` enables `POST /api/auth/dev-login/{user_id}` to switch users without OAuth. Seed script creates fake users sharing the real user's token.
- **Webhooks**: Optional GitHub webhook receiver (`POST /api/webhooks/github`) for instant PR updates. HMAC-SHA256 signature validation. Auto-registers on repo add, auto-cleans on repo delete. Polling reduced to 15-min fallback when active. Admin API at `/api/webhooks/admin/`.
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
    models/       # ORM models (tables.py) — User, GitHubAccount, Space, TrackedRepo, RepoTracker, PullRequest, etc.
    services/     # GitHub client, sync service, stack detector, SSE events, crypto, discovery
  alembic/        # Database migrations
frontend/
  src/
    api/          # API client + types
    components/   # Shell, SpaceManager, TeamPanel, StatusDot, PRDetailPanel, DependencyGraph, DevUserSwitcher
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

## Releasing

When creating a new release, update the version in these places:
- `backend/pyproject.toml` - `version` field
- `backend/src/main.py` - FastAPI `version` kwarg (should match pyproject.toml)

The in-app version badge (`/api/version`) reads from `pyproject.toml` at startup and fetches release notes from the GitHub Releases API.

## Environment Variables

See `.env.example` for all options. Key ones:
- `GITHUB_OAUTH_CLIENT_ID` / `GITHUB_OAUTH_CLIENT_SECRET` — GitHub OAuth App credentials
- `DATABASE_URL` — PostgreSQL async connection string
- `SECRET_KEY` — Used for session cookies AND token encryption (change in production!)
- `DASHBOARD_PASSWORD` — Optional, enables password gate (leave empty to disable)
- `DEV_MODE` — Enable dev-only features (user impersonation endpoint)

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **pr-dashboard** (1042 symbols, 2739 relationships, 83 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## When Debugging

1. `gitnexus_query({query: "<error or symptom>"})` — find execution flows related to the issue
2. `gitnexus_context({name: "<suspect function>"})` — see all callers, callees, and process participation
3. `READ gitnexus://repo/pr-dashboard/process/{processName}` — trace the full execution flow step by step
4. For regressions: `gitnexus_detect_changes({scope: "compare", base_ref: "main"})` — see what your branch changed

## When Refactoring

- **Renaming**: MUST use `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` first. Review the preview — graph edits are safe, text_search edits need manual review. Then run with `dry_run: false`.
- **Extracting/Splitting**: MUST run `gitnexus_context({name: "target"})` to see all incoming/outgoing refs, then `gitnexus_impact({target: "target", direction: "upstream"})` to find all external callers before moving code.
- After any refactor: run `gitnexus_detect_changes({scope: "all"})` to verify only expected files changed.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `query` | Find code by concept | `gitnexus_query({query: "auth validation"})` |
| `context` | 360-degree view of one symbol | `gitnexus_context({name: "validateUser"})` |
| `impact` | Blast radius before editing | `gitnexus_impact({target: "X", direction: "upstream"})` |
| `detect_changes` | Pre-commit scope check | `gitnexus_detect_changes({scope: "staged"})` |
| `rename` | Safe multi-file rename | `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` |
| `cypher` | Custom graph queries | `gitnexus_cypher({query: "MATCH ..."})` |

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d=1 | WILL BREAK — direct callers/importers | MUST update these |
| d=2 | LIKELY AFFECTED — indirect deps | Should test |
| d=3 | MAY NEED TESTING — transitive | Test if critical path |

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/pr-dashboard/context` | Codebase overview, check index freshness |
| `gitnexus://repo/pr-dashboard/clusters` | All functional areas |
| `gitnexus://repo/pr-dashboard/processes` | All execution flows |
| `gitnexus://repo/pr-dashboard/process/{name}` | Step-by-step execution trace |

## Self-Check Before Finishing

Before completing any code modification task, verify:
1. `gitnexus_impact` was run for all modified symbols
2. No HIGH/CRITICAL risk warnings were ignored
3. `gitnexus_detect_changes()` confirms changes match expected scope
4. All d=1 (WILL BREAK) dependents were updated

## Keeping the Index Fresh

After committing code changes, the GitNexus index becomes stale. Re-run analyze to update it:

```bash
npx gitnexus analyze
```

If the index previously included embeddings, preserve them by adding `--embeddings`:

```bash
npx gitnexus analyze --embeddings
```

To check whether embeddings exist, inspect `.gitnexus/meta.json` — the `stats.embeddings` field shows the count (0 means no embeddings). **Running analyze without `--embeddings` will delete any previously generated embeddings.**

> Claude Code users: A PostToolUse hook handles this automatically after `git commit` and `git merge`.

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
