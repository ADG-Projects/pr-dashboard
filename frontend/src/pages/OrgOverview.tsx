/** Level 1 — Org overview showing all tracked repos as cards, grouped by space. */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useState, useMemo } from 'react';
import { api, type RepoSummary, type Space, type AvailableRepo, type AvailableReposResponse } from '../api/client';
import { useCurrentUser } from '../App';
import { GitHubIcon } from '../components/GitHubIcon';
import { Tooltip } from '../components/Tooltip';
import styles from './OrgOverview.module.css';

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return '';
  const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

function healthColor(repo: RepoSummary): string {
  if (repo.failing_ci_count > 0) return 'var(--ci-fail)';
  if (repo.stale_pr_count > 0) return 'var(--ci-pending)';
  return 'var(--ci-pass)';
}

function RepoBrowser({ space, onClose }: { space: Space; onClose: () => void }) {
  const qc = useQueryClient();
  const [search, setSearch] = useState('');

  const { data: response, isLoading, isFetching, refetch, isError, error } = useQuery({
    queryKey: ['available-repos', space.id],
    queryFn: () => api.listSpaceAvailableRepos(space.id),
    staleTime: 5 * 60 * 1000,
  });

  const addMutation = useMutation({
    mutationFn: (repo: AvailableRepo) => {
      const [owner, name] = repo.full_name.split('/');
      return api.addRepo(name, space.id, owner);
    },
    onSuccess: (_data, trackedRepo) => {
      qc.invalidateQueries({ queryKey: ['repos'], refetchType: 'active' });
      qc.setQueryData<AvailableReposResponse>(
        ['available-repos', space.id],
        (old) => old ? {
          ...old,
          already_tracked_count: old.already_tracked_count + 1,
          repos: old.repos.filter((r) => r.full_name !== trackedRepo.full_name),
        } : old,
      );
    },
  });

  const available = response?.repos;

  const filtered = useMemo(() => {
    if (!available) return [];
    if (!search) return available;
    const q = search.toLowerCase();
    return available.filter(
      (r) =>
        r.name.toLowerCase().includes(q) ||
        r.description?.toLowerCase().includes(q),
    );
  }, [available, search]);

  return (
    <div className={styles.modalOverlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <h2 className={styles.modalTitle}>Add repos from {space.name}</h2>
          <button className={styles.modalClose} onClick={onClose}>
            x
          </button>
        </div>
        <div className={styles.searchRow}>
          <input
            className={styles.searchInput}
            placeholder="Search repos..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
          />
          <button
            className={styles.refreshBtn}
            onClick={() => refetch()}
            disabled={isFetching}
            title="Refresh repo list from GitHub"
          >
            <svg
              className={isFetching ? styles.refreshSpin : ''}
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M21 2v6h-6" />
              <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
              <path d="M3 22v-6h6" />
              <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
            </svg>
          </button>
        </div>
        <div className={styles.repoList}>
          {isLoading && (
            <div className={styles.listEmpty}>Loading repos...</div>
          )}
          {!isLoading && isError && (
            <div className={styles.listEmpty} style={{ color: 'var(--ci-fail, #d73a4a)' }}>
              {error instanceof Error ? error.message : 'Failed to load repos from GitHub'}
            </div>
          )}
          {!isLoading && !isError && filtered.length === 0 && (
            <div className={styles.listEmpty}>
              {search
                ? 'No matching repos'
                : response && response.total_from_github === 0
                  ? (
                    <div className={styles.emptyHelpBox}>
                      <strong>No repos found</strong>
                      {response.sso_required ? (
                        <p>
                          Your token needs SSO authorization for this org. Go to{' '}
                          <a href="https://github.com/settings/tokens" target="_blank" rel="noreferrer">
                            GitHub → Settings → Tokens
                          </a>
                          , find your token, click <strong>Configure SSO</strong>, and authorize this org.
                        </p>
                      ) : (
                        <ul>
                          <li>If this is a SAML/SSO org, make sure your token has SSO authorization
                            {' — '}go to{' '}
                            <a href="https://github.com/settings/tokens" target="_blank" rel="noreferrer">
                              GitHub Tokens
                            </a>
                            {' → Configure SSO → Authorize'}
                          </li>
                          <li>If you regenerated your token, SSO authorization does not carry over.
                            You need to re-authorize and update the token in the dashboard.</li>
                          <li>Check that the token has the <code>repo</code> scope</li>
                        </ul>
                      )}
                    </div>
                  )
                  : 'All repos are already tracked'}
            </div>
          )}
          {filtered.map((repo) => (
            <div key={repo.full_name} className={styles.repoRow}>
              <div className={styles.repoInfo}>
                <span className={styles.repoRowName}>
                  {repo.name}
                  {repo.private && (
                    <Tooltip text="This is a private repository" position="top">
                      <span className={styles.privateBadge}>private</span>
                    </Tooltip>
                  )}
                  {repo.pushed_at && (
                    <Tooltip text="Last push to this repository" position="top">
                      <span className={styles.pushedAt}>{timeAgo(repo.pushed_at)}</span>
                    </Tooltip>
                  )}
                </span>
                {repo.description && (
                  <span className={styles.repoDesc}>{repo.description}</span>
                )}
              </div>
              <button
                className={styles.trackBtn}
                disabled={addMutation.isPending}
                onClick={() => addMutation.mutate(repo)}
              >
                Track
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function OrgOverview() {
  const qc = useQueryClient();
  const { user, oauthConfigured } = useCurrentUser();
  const { data: repos, isLoading: reposLoading } = useQuery({
    queryKey: ['repos'],
    queryFn: () => api.listRepos(),
    refetchInterval: 30_000,
  });

  const { data: spaces } = useQuery({
    queryKey: ['spaces'],
    queryFn: api.listSpaces,
  });

  const removeMutation = useMutation({
    mutationFn: (id: number) => api.removeRepo(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['repos'], refetchType: 'active' });
      qc.invalidateQueries({ queryKey: ['available-repos'], refetchType: 'active' });
    },
  });

  const visibilityMutation = useMutation({
    mutationFn: ({ id, visibility }: { id: number; visibility: 'private' | 'shared' }) =>
      api.setRepoVisibility(id, visibility),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['repos'], refetchType: 'active' });
    },
  });

  const [browserSpace, setBrowserSpace] = useState<Space | null>(null);

  // Group repos by space
  const grouped = useMemo(() => {
    if (!repos) return [];
    const groups: { space: Space | null; repos: RepoSummary[] }[] = [];
    const spaceMap = new Map<number, Space>();
    spaces?.forEach((s) => spaceMap.set(s.id, s));

    const bySpace = new Map<number | null, RepoSummary[]>();
    for (const repo of repos) {
      const key = repo.space_id;
      if (!bySpace.has(key)) bySpace.set(key, []);
      bySpace.get(key)!.push(repo);
    }

    // Active spaces first (in order), then remaining repos
    for (const space of spaces || []) {
      if (!space.is_active) {
        bySpace.delete(space.id);
        continue;
      }
      const spaceRepos = bySpace.get(space.id);
      groups.push({ space, repos: spaceRepos || [] });
      bySpace.delete(space.id);
    }
    // Collect repos from spaces the user doesn't own (shared repos) + unassigned
    const remaining: RepoSummary[] = [];
    for (const [, spaceRepos] of bySpace) {
      remaining.push(...spaceRepos);
    }
    if (remaining.length) {
      groups.push({ space: null, repos: remaining });
    }

    return groups;
  }, [repos, spaces]);

  if (reposLoading) return <div className={styles.loading}>Loading repos...</div>;

  const hasContent = (repos && repos.length > 0) || (spaces && spaces.some((s) => s.is_active));

  return (
    <div>
      <div className={styles.titleRow}>
        <h1 className={styles.title}>Tracked Repositories</h1>
      </div>

      {!hasContent && (
        <div className={styles.onboarding}>
          <h2 className={styles.onboardingTitle}>Welcome to PR Dashboard</h2>
          <p className={styles.onboardingDesc}>
            Track pull requests across your GitHub orgs and personal repos, all in one place.
          </p>
          <div className={styles.steps}>
            {oauthConfigured && (() => {
              const hasAccounts = !!user && (spaces ?? []).length > 0;
              return (
                <div className={`${styles.step} ${hasAccounts ? styles.stepDone : ''}`}>
                  <span className={styles.stepNum}>{hasAccounts ? '\u2713' : '1'}</span>
                  <div className={styles.stepContent}>
                    <strong>{hasAccounts ? 'GitHub account linked' : 'Sign in with GitHub'}</strong>
                    <span className={styles.stepDesc}>
                      Your orgs and personal account are auto-discovered. Link multiple accounts for work + personal.
                    </span>
                    {!hasAccounts && (
                      <button
                        className={styles.githubBtn}
                        onClick={() => { window.location.href = user ? '/api/auth/github?link=true' : '/api/auth/github'; }}
                      >
                        <GitHubIcon size={16} />
                        {user ? 'Link a GitHub account' : 'Sign in with GitHub'}
                      </button>
                    )}
                  </div>
                </div>
              );
            })()}
            {(() => {
              const hasActiveSpaces = (spaces ?? []).some(s => s.is_active);
              return (
                <div className={`${styles.step} ${hasActiveSpaces ? styles.stepDone : ''}`}>
                  <span className={styles.stepNum}>{hasActiveSpaces ? '\u2713' : oauthConfigured ? '2' : '1'}</span>
                  <div className={styles.stepContent}>
                    <strong>Enable spaces</strong>
                    <span className={styles.stepDesc}>
                      Toggle on the orgs you want to track. Open Spaces to see your discovered accounts.
                    </span>
                <button
                  className={styles.stepBtn}
                  onClick={() => window.dispatchEvent(new Event('open-spaces'))}
                >
                  Open Spaces
                </button>
              </div>
            </div>
              );
            })()}
            <div className={styles.step}>
              <span className={styles.stepNum}>{oauthConfigured ? '3' : '2'}</span>
              <div className={styles.stepContent}>
                <strong>Track repos</strong>
                <span className={styles.stepDesc}>
                  Pick which repos to monitor. PRs, CI status, and stacks sync automatically.
                </span>
              </div>
            </div>
          </div>
        </div>
      )}

      {grouped.map(({ space, repos: groupRepos }) => (
        <div key={space?.id ?? 'none'} className={styles.spaceGroup}>
          <div className={styles.spaceHeader}>
            <h2 className={styles.spaceName}>
              {space?.name ?? 'Shared with you'}
            </h2>
            {space && (
              <span className={styles.spaceSlug}>{space.slug}</span>
            )}
            {space && (
              <Tooltip text={`Add repos from ${space.name}`} position="right">
                <button
                  className={styles.spaceAddBtn}
                  onClick={() => setBrowserSpace(space)}
                >
                  + Add repos
                </button>
              </Tooltip>
            )}
          </div>
          <div className={styles.grid}>
            {groupRepos.map((repo) => (
              <Link
                key={repo.id}
                to={`/repos/${repo.owner}/${repo.name}`}
                className={styles.card}
              >
                <div className={styles.cardHeader}>
                  <Tooltip text={
                    !repo.last_synced_at ? 'Not yet synced' :
                    repo.failing_ci_count > 0 ? 'Some PRs have failing CI' :
                    repo.stale_pr_count > 0 ? 'Some PRs are stale (no updates in 7 days)' :
                    'All PRs healthy'
                  } position="right">
                    <span
                      className={styles.healthDot}
                      style={{ background: repo.last_synced_at ? healthColor(repo) : 'var(--text-dim)' }}
                    />
                  </Tooltip>
                  <span className={styles.repoName}>{repo.full_name.split('/').pop()}</span>
                  {user && repo.user_id === user.id && (
                    <Tooltip text={`Click to make ${repo.visibility === 'private' ? 'shared' : 'private'}`} position="top">
                      <button
                        className={`${styles.visibilityBadge} ${repo.visibility === 'shared' ? styles.visibilityShared : styles.visibilityPrivate}`}
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          visibilityMutation.mutate({
                            id: repo.id,
                            visibility: repo.visibility === 'private' ? 'shared' : 'private',
                          });
                        }}
                      >
                        {repo.visibility}
                      </button>
                    </Tooltip>
                  )}
                  <button
                    className={styles.untrackBtn}
                    title="Untrack repo"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      if (window.confirm(`Untrack ${repo.full_name}?`)) {
                        removeMutation.mutate(repo.id);
                      }
                    }}
                  >
                    x
                  </button>
                </div>
                <div className={styles.stats}>
                  <Tooltip text="Total open pull requests" position="bottom">
                    <div className={styles.stat}>
                      <span className={styles.statValue}>
                        {repo.last_synced_at ? repo.open_pr_count : <span className={styles.statPlaceholder} />}
                      </span>
                      <span className={styles.statLabel}>Open PRs</span>
                    </div>
                  </Tooltip>
                  <Tooltip text="PRs with at least one failing CI check" position="bottom">
                    <div className={styles.stat}>
                      <span className={styles.statValue} style={{ color: repo.last_synced_at && repo.failing_ci_count > 0 ? 'var(--ci-fail)' : undefined }}>
                        {repo.last_synced_at ? repo.failing_ci_count : <span className={styles.statPlaceholder} />}
                      </span>
                      <span className={styles.statLabel}>Failing CI</span>
                    </div>
                  </Tooltip>
                  <Tooltip text="Groups of dependent/stacked PRs" position="bottom">
                    <div className={styles.stat}>
                      <span className={styles.statValue}>
                        {repo.last_synced_at ? repo.stack_count : <span className={styles.statPlaceholder} />}
                      </span>
                      <span className={styles.statLabel}>Stacks</span>
                    </div>
                  </Tooltip>
                  <Tooltip text="PRs with no updates in the last 7 days" position="bottom">
                    <div className={styles.stat}>
                      <span className={styles.statValue} style={{ color: repo.last_synced_at && repo.stale_pr_count > 0 ? 'var(--ci-pending)' : undefined }}>
                        {repo.last_synced_at ? repo.stale_pr_count : <span className={styles.statPlaceholder} />}
                      </span>
                      <span className={styles.statLabel}>Stale</span>
                    </div>
                  </Tooltip>
                </div>
                {repo.last_synced_at ? (
                  <Tooltip text="Last sync with GitHub API" position="top">
                    <div className={styles.synced}>
                      Synced {new Date(repo.last_synced_at).toLocaleTimeString()}
                    </div>
                  </Tooltip>
                ) : (
                  <div className={styles.syncingLabel}>
                    <span className={styles.syncSpinner} />
                    Syncing...
                  </div>
                )}
              </Link>
            ))}

            {groupRepos.length === 0 && space && (
              <button
                className={styles.addCard}
                onClick={() => setBrowserSpace(space)}
              >
                <span className={styles.addIcon}>+</span>
                <span className={styles.addTitle}>Add your first repo</span>
              </button>
            )}
          </div>
        </div>
      ))}

      {browserSpace && <RepoBrowser space={browserSpace} onClose={() => setBrowserSpace(null)} />}
    </div>
  );
}
