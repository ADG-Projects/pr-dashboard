/** Level 1 — Org overview showing all tracked repos as cards, grouped by space. */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useState, useMemo } from 'react';
import { api, type RepoSummary, type Space, type AvailableRepo } from '../api/client';
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

  const { data: available, isLoading } = useQuery({
    queryKey: ['repos', 'available', space.id],
    queryFn: () => api.listSpaceAvailableRepos(space.id),
  });

  const addMutation = useMutation({
    mutationFn: (repo: AvailableRepo) => {
      const [owner, name] = repo.full_name.split('/');
      return api.addRepo(name, space.id, owner);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['repos'] });
      qc.invalidateQueries({ queryKey: ['repos', 'available', space.id] });
    },
  });

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
        <input
          className={styles.searchInput}
          placeholder="Search repos..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          autoFocus
        />
        <div className={styles.repoList}>
          {isLoading && (
            <div className={styles.listEmpty}>Loading repos...</div>
          )}
          {!isLoading && filtered.length === 0 && (
            <div className={styles.listEmpty}>
              {search ? 'No matching repos' : 'All repos are already tracked'}
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

const BASE = import.meta.env.DEV ? 'http://localhost:8000' : '';

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
      qc.invalidateQueries({ queryKey: ['repos'] });
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

    // Active spaces first (in order), then unassigned
    for (const space of spaces || []) {
      if (!space.is_active) {
        bySpace.delete(space.id);
        continue;
      }
      const spaceRepos = bySpace.get(space.id);
      groups.push({ space, repos: spaceRepos || [] });
      bySpace.delete(space.id);
    }
    const unassigned = bySpace.get(null);
    if (unassigned?.length) {
      groups.push({ space: null, repos: unassigned });
    }

    return groups;
  }, [repos, spaces]);

  if (reposLoading) return <div className={styles.loading}>Loading repos...</div>;

  const hasSpaces = spaces && spaces.some((s) => s.is_active);

  return (
    <div>
      <div className={styles.titleRow}>
        <h1 className={styles.title}>Tracked Repositories</h1>
      </div>

      {!hasSpaces && (
        <div className={styles.onboarding}>
          <h2 className={styles.onboardingTitle}>Welcome to PR Dashboard</h2>
          <p className={styles.onboardingDesc}>
            Track pull requests across your GitHub orgs and personal repos, all in one place.
          </p>
          <div className={styles.steps}>
            {oauthConfigured && (
              <div className={`${styles.step} ${user ? styles.stepDone : ''}`}>
                <span className={styles.stepNum}>{user ? '\u2713' : '1'}</span>
                <div className={styles.stepContent}>
                  <strong>Sign in with GitHub</strong>
                  <span className={styles.stepDesc}>
                    Your orgs and personal account are auto-discovered. Link multiple accounts for work + personal.
                  </span>
                  {!user && (
                    <button
                      className={styles.githubBtn}
                      onClick={() => { window.location.href = `${BASE}/api/auth/github`; }}
                    >
                      <GitHubIcon size={16} />
                      Sign in with GitHub
                    </button>
                  )}
                </div>
              </div>
            )}
            <div className={styles.step}>
              <span className={styles.stepNum}>{oauthConfigured ? '2' : '1'}</span>
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
              {space?.name ?? 'Unassigned'}
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
                  <span className={styles.repoName}>{repo.full_name}</span>
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
                {repo.last_synced_at && (
                  <Tooltip text="Last sync with GitHub API" position="top">
                    <div className={styles.synced}>
                      Synced {new Date(repo.last_synced_at).toLocaleTimeString()}
                    </div>
                  </Tooltip>
                )}
              </Link>
            ))}

            {groupRepos.length === 0 && space && (
              <button
                className={styles.addCard}
                onClick={() => setBrowserSpace(space)}
              >
                <span className={styles.addIcon}>+</span>
                <span className={styles.addTitle}>Add repos</span>
              </button>
            )}
          </div>
        </div>
      ))}

      {browserSpace && <RepoBrowser space={browserSpace} onClose={() => setBrowserSpace(null)} />}
    </div>
  );
}
