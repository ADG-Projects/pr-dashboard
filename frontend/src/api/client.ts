/** API client for the PR Dashboard backend. */

const BASE = import.meta.env.DEV ? 'http://localhost:8000' : '';

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
  return resp.json();
}

// ── Types ────────────────────────────────────────

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
  html_url: string;
  created_at: string;
  updated_at: string;
  ci_status: string;
  review_state: string;
  stack_id: number | null;
  assignee_id: number | null;
  assignee_name: string | null;
  dashboard_reviewed: boolean;
  dashboard_approved: boolean;
  rebased_since_approval: boolean;
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

export interface PRDetail extends PRSummary {
  check_runs: CheckRun[];
  reviews: Review[];
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

export interface TeamMember {
  id: number;
  display_name: string;
  github_login: string | null;
  email: string | null;
  is_active: boolean;
  created_at: string;
}

export interface Progress {
  id: number;
  pull_request_id: number;
  team_member_id: number;
  team_member_name: string;
  reviewed: boolean;
  approved: boolean;
  notes: string | null;
  updated_at: string;
}

export interface AvailableRepo {
  name: string;
  full_name: string;
  description: string | null;
  private: boolean;
  pushed_at: string | null;
}

// ── API functions ────────────────────────────────

export const api = {
  // Repos
  listRepos: () => request<RepoSummary[]>('/api/repos'),
  listAvailableRepos: () => request<AvailableRepo[]>('/api/repos/available'),
  addRepo: (name: string) =>
    request('/api/repos', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),
  removeRepo: (id: number) =>
    request(`/api/repos/${id}`, { method: 'DELETE' }),
  syncRepo: (id: number) =>
    request<{ status: string }>(`/api/repos/${id}/sync`, { method: 'POST' }),

  // PRs
  listPulls: (repoId: number, params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return request<PRSummary[]>(`/api/repos/${repoId}/pulls${qs}`);
  },
  getPull: (repoId: number, number: number) =>
    request<PRDetail>(`/api/repos/${repoId}/pulls/${number}`),
  updateTracking: (repoId: number, number: number, data: { reviewed?: boolean; approved?: boolean }) =>
    request(`/api/repos/${repoId}/pulls/${number}/tracking`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  // Stacks
  listStacks: (repoId: number) =>
    request<Stack[]>(`/api/repos/${repoId}/stacks`),
  getStack: (repoId: number, stackId: number) =>
    request<Stack>(`/api/repos/${repoId}/stacks/${stackId}`),

  // Team
  listTeam: () => request<TeamMember[]>('/api/team'),
  addTeamMember: (data: { display_name: string; github_login?: string; email?: string }) =>
    request<TeamMember>('/api/team', { method: 'POST', body: JSON.stringify(data) }),
  updateTeamMember: (id: number, data: { display_name?: string; github_login?: string; is_active?: boolean }) =>
    request<TeamMember>(`/api/team/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteTeamMember: (id: number) =>
    request<void>(`/api/team/${id}`, { method: 'DELETE' }),

  // Assignee
  assignPr: (repoId: number, number: number, assigneeId: number | null) =>
    request<PRSummary>(`/api/repos/${repoId}/pulls/${number}/assignee`, {
      method: 'PATCH',
      body: JSON.stringify({ assignee_id: assigneeId }),
    }),

  // Progress
  getProgress: (prId: number) =>
    request<Progress[]>(`/api/pulls/${prId}/progress`),
  updateProgress: (prId: number, data: { team_member_id: number; reviewed?: boolean; approved?: boolean; notes?: string }) =>
    request<Progress>(`/api/pulls/${prId}/progress`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  // Auth
  login: (password: string) =>
    request<{ authenticated: boolean }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  authStatus: () => request<{ authenticated: boolean; auth_enabled: boolean }>('/api/auth/me'),
  logout: () => request('/api/auth/logout', { method: 'POST' }),
};
