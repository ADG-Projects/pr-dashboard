/** Level 1 — Org overview showing all tracked repos as cards. */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useState, useMemo } from 'react';
import { api, type RepoSummary } from '../api/client';
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

function RepoBrowser({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [search, setSearch] = useState('');

  const { data: available, isLoading } = useQuery({
    queryKey: ['repos', 'available'],
    queryFn: api.listAvailableRepos,
  });

  const addMutation = useMutation({
    mutationFn: (name: string) => api.addRepo(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['repos'] });
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
          <h2 className={styles.modalTitle}>Add repositories</h2>
          <button className={styles.modalClose} onClick={onClose}>
            ×
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
            <div className={styles.listEmpty}>Loading org repos...</div>
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
                onClick={() => addMutation.mutate(repo.name)}
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
  const { data: repos, isLoading } = useQuery({
    queryKey: ['repos'],
    queryFn: api.listRepos,
    refetchInterval: 30_000,
  });

  const removeMutation = useMutation({
    mutationFn: (id: number) => api.removeRepo(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['repos'] });
    },
  });

  const [browserOpen, setBrowserOpen] = useState(false);

  if (isLoading) return <div className={styles.loading}>Loading repos...</div>;

  return (
    <div>
      <div className={styles.titleRow}>
        <h1 className={styles.title}>Tracked Repositories</h1>
      </div>

      <div className={styles.grid}>
        {repos?.map((repo) => (
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
                ×
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

        {/* Add repo card */}
        <button
          className={styles.addCard}
          onClick={() => setBrowserOpen(true)}
        >
          <span className={styles.addIcon}>+</span>
          <span className={styles.addTitle}>Add repos</span>
        </button>
      </div>

      {browserOpen && <RepoBrowser onClose={() => setBrowserOpen(false)} />}
    </div>
  );
}
