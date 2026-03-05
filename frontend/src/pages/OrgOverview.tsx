/** Level 1 — Org overview showing all tracked repos as cards. */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useState, useMemo } from 'react';
import { api, type RepoSummary } from '../api/client';
import styles from './OrgOverview.module.css';

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
                    <span className={styles.privateBadge}>private</span>
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
  const { data: repos, isLoading } = useQuery({
    queryKey: ['repos'],
    queryFn: api.listRepos,
    refetchInterval: 30_000,
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
              <span
                className={styles.healthDot}
                style={{ background: healthColor(repo) }}
              />
              <span className={styles.repoName}>{repo.full_name}</span>
            </div>
            <div className={styles.stats}>
              <div className={styles.stat}>
                <span className={styles.statValue}>{repo.open_pr_count}</span>
                <span className={styles.statLabel}>Open PRs</span>
              </div>
              <div className={styles.stat}>
                <span className={styles.statValue} style={{ color: repo.failing_ci_count > 0 ? 'var(--ci-fail)' : undefined }}>
                  {repo.failing_ci_count}
                </span>
                <span className={styles.statLabel}>Failing CI</span>
              </div>
              <div className={styles.stat}>
                <span className={styles.statValue}>{repo.stack_count}</span>
                <span className={styles.statLabel}>Stacks</span>
              </div>
              <div className={styles.stat}>
                <span className={styles.statValue} style={{ color: repo.stale_pr_count > 0 ? 'var(--ci-pending)' : undefined }}>
                  {repo.stale_pr_count}
                </span>
                <span className={styles.statLabel}>Stale</span>
              </div>
            </div>
            {repo.last_synced_at && (
              <div className={styles.synced}>
                Synced {new Date(repo.last_synced_at).toLocaleTimeString()}
              </div>
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
