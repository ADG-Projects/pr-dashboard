/** API client for the PR Dashboard backend. */

const BASE = '';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(body.detail || `HTTP ${resp.status}`);
  }
  if (resp.status === 204 || resp.headers.get('content-length') === '0') {
    return undefined as T;
  }
  return resp.json();
}

// ── Types ────────────────────────────────────────

export interface GitHubUser {
  id: number;
  login: string;
  name: string | null;
  avatar_url: string | null;
}

export interface Space {
  id: number;
  name: string;
  slug: string;
  space_type: string;
  base_url: string;
  is_active: boolean;
  has_token: boolean;
  created_at: string;
  github_account_id: number | null;
  github_account_login: string | null;
}

export interface GitHubAccountInfo {
  id: number;
  login: string;
  avatar_url: string | null;
  base_url: string;
  has_token: boolean;
  created_at: string;
  last_login_at: string;
}

export interface RepoSummary {
  id: number;
  owner: string;
  name: string;
  full_name: string;
  is_active: boolean;
  default_branch: string;
  last_synced_at: string | null;
  open_pr_count: number;
  failing_ci_count: number;
  stale_pr_count: number;
  stack_count: number;
  space_id: number | null;
  space_name: string | null;
  visibility: 'private' | 'shared';
  user_id: number | null;
  tracker_count: number;
}

export interface PRSummary {
  id: number;
  number: number;
  title: string;
  state: string;
  draft: boolean;
  head_ref: string;
  base_ref: string;
  author: string;
  additions: number;
  deletions: number;
  changed_files: number;
  mergeable_state: string | null;
  unresolved_thread_count: number | null;
  html_url: string;
  head_sha: string | null;
  commit_count: number;
  created_at: string;
  updated_at: string;
  ci_status: string;
  review_state: string;
  stack_id: number | null;
  assignee_id: number | null;
  assignee_name: string | null;
  github_requested_reviewers: { login: string; avatar_url: string | null }[];
  all_reviewers: { login: string; avatar_url: string | null }[];
  rebased_since_approval: boolean;
  merged_at: string | null;
  manual_priority: string | null;
  labels: { name: string; color: string }[];
  commenters_without_review: string[];
}

export interface CheckRun {
  id: number;
  name: string;
  status: string;
  conclusion: string | null;
  details_url: string | null;
}

export interface Review {
  id: number;
  reviewer: string;
  state: string;
  submitted_at: string;
}

export interface WorkItem {
  id: number;
  work_item_id: number;
  title: string;
  state: string;
  work_item_type: string;
  url: string;
  assigned_to: string | null;
}

export interface WorkItemSearchResult {
  work_item_id: number;
  title: string;
  state: string;
  work_item_type: string;
  url: string;
  assigned_to: string | null;
}

export interface PRDetail extends PRSummary {
  check_runs: CheckRun[];
  reviews: Review[];
  work_items: WorkItem[];
}

export interface StackMember {
  pull_request_id: number;
  position: number;
  parent_pr_id: number | null;
  pr: PRSummary;
}

export interface Stack {
  id: number;
  name: string | null;
  root_pr_id: number | null;
  detected_at: string;
  members: StackMember[];
}

export interface User {
  id: number;
  login: string;
  name: string | null;
  avatar_url: string | null;
  is_active: boolean;
  created_at: string;
  linked_accounts: { login: string; avatar_url: string | null; space_slugs: string[] }[];
}

export interface AvailableRepo {
  name: string;
  full_name: string;
  description: string | null;
  private: boolean;
  pushed_at: string | null;
}

export interface AvailableReposResponse {
  total_from_github: number;
  already_tracked_count: number;
  repos: AvailableRepo[];
  sso_required?: boolean;
}

export interface AuthStatus {
  authenticated: boolean;
  auth_enabled: boolean;
  user: GitHubUser | null;
}

export interface PriorityBreakdown {
  review: number;
  ci: number;
  size: number;
  mergeable: number;
  age: number;
  rebase: number;
  draft_penalty: number;
}

export type PriorityMode = 'review' | 'owner' | 'all';

export interface PrioritizedPR {
  pr: PRSummary;
  repo_full_name: string;
  repo_id: number;
  priority_score: number;
  priority_breakdown: PriorityBreakdown;
  merge_position: number;
  blocked_by_pr_id: number | null;
  stack_id: number | null;
  stack_name: string | null;
  priority_tier: string;
  mode: string;
}

export const ALLOWED_LABELS = [
  { name: 'bug', color: 'd73a4a', description: "Something isn't working" },
  { name: 'enhancement', color: '0075ca', description: 'New feature or request' },
  { name: 'documentation', color: '0e8a16', description: 'Documentation changes' },
  { name: 'refactor', color: '7057ff', description: 'Code restructuring' },
  { name: 'testing', color: 'fbca04', description: 'Test-related changes' },
] as const;

export interface AdoAccountInfo {
  id: number;
  org_url: string;
  project: string;
  display_name: string | null;
  has_token: boolean;
  created_at: string;
}

export interface VersionInfo {
  version: string;
  release_notes: string | null;
  release_url: string | null;
  release_name: string | null;
  published_at: string | null;
}

export interface ReleaseInfo {
  release_notes: string | null;
  release_url: string | null;
  release_name: string | null;
  published_at: string | null;
  tag_name: string | null;
}

export interface Remediation {
  action: string;
  label: string;
  url?: string | null;
  description: string;
}

export interface AuthHealthAccount {
  id: number;
  login: string;
  token_status: string;
  token_error: string | null;
  token_checked_at: string | null;
  affected_repos: string[];
  remediation: Remediation;
}

export interface AuthHealthRepo {
  id: number;
  full_name: string;
  last_sync_error: string | null;
  last_sync_error_at: string | null;
  last_successful_sync_at: string | null;
  remediation: Remediation;
}

export interface AuthHealthResponse {
  has_issues: boolean;
  accounts: AuthHealthAccount[];
  stale_repos: AuthHealthRepo[];
}

// ── API functions ────────────────────────────────

export const api = {
  // Accounts
  listAccounts: () => request<GitHubAccountInfo[]>('/api/accounts'),
  linkAccountWithToken: (token: string, baseUrl?: string) =>
    request<GitHubAccountInfo>('/api/accounts', {
      method: 'POST',
      body: JSON.stringify({ token, base_url: baseUrl || 'https://api.github.com' }),
    }),
  discoverSpaces: (accountId: number) =>
    request<{ discovered: number; spaces: { id: number; slug: string; space_type: string }[] }>(
      `/api/accounts/${accountId}/discover`,
      { method: 'POST' },
    ),
  addSpaceToAccount: (accountId: number, slug: string, spaceType: string = 'org', name?: string) =>
    request<{ id: number; slug: string; already_exists: boolean }>(
      `/api/accounts/${accountId}/spaces`,
      {
        method: 'POST',
        body: JSON.stringify({ slug, space_type: spaceType, name }),
      },
    ),
  removeAccount: (accountId: number) =>
    request<void>(`/api/accounts/${accountId}`, { method: 'DELETE' }),

  // Spaces
  listSpaces: () => request<Space[]>('/api/spaces'),
  toggleSpace: (id: number, isActive: boolean) =>
    request<Space>(`/api/spaces/${id}/toggle`, {
      method: 'PATCH',
      body: JSON.stringify({ is_active: isActive }),
    }),
  deleteSpace: (id: number) =>
    request<void>(`/api/spaces/${id}`, { method: 'DELETE' }),
  listSpaceAvailableRepos: (spaceId: number) =>
    request<AvailableReposResponse>(`/api/spaces/${spaceId}/available-repos`),
  setRepoVisibility: (id: number, visibility: 'private' | 'shared') =>
    request<RepoSummary>(`/api/repos/${id}/visibility`, {
      method: 'PATCH',
      body: JSON.stringify({ visibility }),
    }),
  checkSpaceConnectivity: (spaceId: number) =>
    request<{ ok: boolean; error?: string }>(
      `/api/spaces/${spaceId}/connectivity`,
      { method: 'POST' },
    ),

  // Repos
  listRepos: (spaceId?: number) => {
    const qs = spaceId ? `?space_id=${spaceId}` : '';
    return request<RepoSummary[]>(`/api/repos${qs}`);
  },
  addRepo: (name: string, spaceId: number, owner?: string) =>
    request('/api/repos', {
      method: 'POST',
      body: JSON.stringify({ name, space_id: spaceId, owner: owner || '' }),
    }),
  removeRepo: (id: number) =>
    request(`/api/repos/${id}`, { method: 'DELETE' }),
  syncRepo: (id: number) =>
    request<{ status: string }>(`/api/repos/${id}/sync`, { method: 'POST' }),

  // Prioritization
  listPrioritized: (repoId?: number, mode?: PriorityMode) => {
    const params = new URLSearchParams();
    if (repoId) params.set('repo_id', String(repoId));
    if (mode) params.set('mode', mode);
    const qs = params.toString() ? `?${params.toString()}` : '';
    return request<PrioritizedPR[]>(`/api/pulls/prioritized${qs}`);
  },

  // PRs
  listPulls: (repoId: number, params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return request<PRSummary[]>(`/api/repos/${repoId}/pulls${qs}`);
  },
  getPull: (repoId: number, number: number) =>
    request<PRDetail>(`/api/repos/${repoId}/pulls/${number}`),

  // Stacks
  listStacks: (repoId: number) =>
    request<Stack[]>(`/api/repos/${repoId}/stacks`),
  getStack: (repoId: number, stackId: number) =>
    request<Stack>(`/api/repos/${repoId}/stacks/${stackId}`),
  renameStack: (repoId: number, stackId: number, name: string) =>
    request<Stack>(`/api/repos/${repoId}/stacks/${stackId}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    }),

  // Team (users from OAuth)
  listTeam: () => request<User[]>('/api/team'),
  listParticipants: (repoId: number) => request<string[]>(`/api/team/participated?repo_id=${repoId}`),
  updateUser: (id: number, data: { is_active?: boolean }) =>
    request<User>(`/api/team/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  // Assignee
  assignPr: (repoId: number, number: number, assigneeId: number | null) =>
    request<PRSummary>(`/api/repos/${repoId}/pulls/${number}/assignee`, {
      method: 'PATCH',
      body: JSON.stringify({ assignee_id: assigneeId }),
    }),

  // Priority
  setPriority: (repoId: number, number: number, priority: string | null) =>
    request<PRSummary>(`/api/repos/${repoId}/pulls/${number}/priority`, {
      method: 'PATCH',
      body: JSON.stringify({ priority }),
    }),

  // Reviewers
  updateReviewers: (repoId: number, number: number, addUserIds: number[], removeLogins: string[]) =>
    request<{ github_requested_reviewers: { login: string; avatar_url: string | null }[] }>(
      `/api/repos/${repoId}/pulls/${number}/reviewers`,
      {
        method: 'PATCH',
        body: JSON.stringify({ add_user_ids: addUserIds, remove_logins: removeLogins }),
      },
    ),

  // Labels
  updateLabels: (repoId: number, number: number, add: string[], remove: string[]) =>
    request<PRSummary>(`/api/repos/${repoId}/pulls/${number}/labels`, {
      method: 'PATCH',
      body: JSON.stringify({ add, remove }),
    }),

  // ADO Accounts
  listAdoAccounts: () => request<AdoAccountInfo[]>('/api/ado-accounts'),
  linkAdoAccount: (token: string, orgUrl: string, project: string) =>
    request<AdoAccountInfo>('/api/ado-accounts', {
      method: 'POST',
      body: JSON.stringify({ token, org_url: orgUrl, project }),
    }),
  removeAdoAccount: (accountId: number) =>
    request<void>(`/api/ado-accounts/${accountId}`, { method: 'DELETE' }),

  // ADO Work Items
  getAdoStatus: () => request<{ configured: boolean }>('/api/ado/status'),
  listAdoWorkItems: () =>
    request<WorkItemSearchResult[]>('/api/ado/work-items'),
  searchAdoWorkItems: (q: string) =>
    request<WorkItemSearchResult[]>(`/api/ado/search?q=${encodeURIComponent(q)}`),
  linkWorkItem: (repoId: number, number: number, workItemId: number) =>
    request<WorkItem>(`/api/repos/${repoId}/pulls/${number}/work-items`, {
      method: 'POST',
      body: JSON.stringify({ work_item_id: workItemId }),
    }),
  unlinkWorkItem: (repoId: number, number: number, workItemId: number) =>
    request<void>(`/api/repos/${repoId}/pulls/${number}/work-items/${workItemId}`, {
      method: 'DELETE',
    }),

  // Version
  getVersion: () => request<VersionInfo>('/api/version'),
  getReleases: () => request<ReleaseInfo[]>('/api/version/releases'),

  // Auth
  login: (password: string) =>
    request<AuthStatus>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  authStatus: () => request<AuthStatus>('/api/auth/me'),
  logout: () => request('/api/auth/logout', { method: 'POST' }),
  deleteMyAccount: () => request('/api/auth/me', { method: 'DELETE' }),
  getGitHubUser: () => request<GitHubUser | null>('/api/auth/user'),
  disconnectGitHub: () =>
    request('/api/auth/github/disconnect', { method: 'POST' }),
  authHealth: () => request<AuthHealthResponse>('/api/auth/health'),
  authHealthCheck: () =>
    request<AuthHealthResponse>('/api/auth/health/check', { method: 'POST' }),

  // Dev mode
  devListUsers: () => request<GitHubUser[]>('/api/auth/dev-users'),
  devLogin: (userId: number) =>
    request<GitHubUser>(`/api/auth/dev-login/${userId}`, { method: 'POST' }),
};
