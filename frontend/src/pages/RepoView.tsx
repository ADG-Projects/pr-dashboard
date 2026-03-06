/** Level 2 — Repo view showing open PRs as a dependency graph with stack filtering. */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { useState } from 'react';
import { api, type PRSummary, type RepoSummary, type User } from '../api/client';
import { DependencyGraph } from '../components/DependencyGraph';
import { PRDetailPanel } from '../components/PRDetailPanel';
import { Tooltip } from '../components/Tooltip';
import { useStore } from '../store/useStore';
import styles from './RepoView.module.css';

export function RepoView() {
  const { owner, name } = useParams<{ owner: string; name: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { selectedPrId, selectPr } = useStore();

  const [authorFilter, setAuthorFilter] = useState('');
  const [ciFilter, setCiFilter] = useState('');
  const [stackFilter, setStackFilter] = useState<number | null>(null);
  const [assigneeFilter, setAssigneeFilter] = useState<number | null>(null);

  // Get repo ID from the repos list
  const { data: repos } = useQuery({
    queryKey: ['repos'],
    queryFn: api.listRepos,
  });
  const repo = repos?.find((r: RepoSummary) => r.owner === owner && r.name === name);

  const { data: pulls, isLoading } = useQuery({
    queryKey: ['pulls', repo?.id],
    queryFn: () => api.listPulls(repo!.id),
    enabled: !!repo,
    refetchInterval: 30_000,
  });

  const { data: stacks } = useQuery({
    queryKey: ['stacks', repo?.id],
    queryFn: () => api.listStacks(repo!.id),
    enabled: !!repo,
  });

  const { data: team } = useQuery({
    queryKey: ['team'],
    queryFn: api.listTeam,
  });
  const activeTeam = team?.filter((m: User) => m.is_active) || [];

  const syncMutation = useMutation({
    mutationFn: () => api.syncRepo(repo!.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pulls', repo?.id] });
      qc.invalidateQueries({ queryKey: ['stacks', repo?.id] });
    },
  });

  // Filter PRs (CI is a hard filter; author dims cards like the original dashboard)
  let filtered = pulls || [];
  if (ciFilter) filtered = filtered.filter((p: PRSummary) => p.ci_status === ciFilter);

  // Unique authors for filter dropdown
  const authors = [...new Set(pulls?.map((p: PRSummary) => p.author) || [])].sort();

  if (!repo) return <div className={styles.loading}>Loading...</div>;

  return (
    <div className={styles.container}>
      <div className={styles.content}>
        <div className={styles.titleRow}>
          <div className={styles.repoNav}>
            <select
              value={`${owner}/${name}`}
              onChange={(e) => {
                const [o, n] = e.target.value.split('/');
                navigate(`/repos/${o}/${n}`);
              }}
              className={styles.repoSelect}
            >
              {(repos || []).map((r: RepoSummary) => (
                <option key={r.id} value={r.full_name}>{r.full_name}</option>
              ))}
            </select>
          </div>
          <Tooltip text="Fetch latest data from GitHub (auto-syncs every 3 min)" position="bottom">
            <button
              onClick={() => syncMutation.mutate()}
              disabled={syncMutation.isPending}
              className={styles.syncBtn}
            >
              {syncMutation.isPending ? 'Syncing...' : 'Sync now'}
            </button>
          </Tooltip>
        </div>

        <div className={styles.filters}>
          <Tooltip text="Dims non-matching PR cards" position="bottom">
            <select value={authorFilter} onChange={(e) => setAuthorFilter(e.target.value)} className={styles.select}>
              <option value="">All authors</option>
              {authors.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </Tooltip>
          <Tooltip text="Hides non-matching PRs" position="bottom">
            <select value={ciFilter} onChange={(e) => setCiFilter(e.target.value)} className={styles.select}>
              <option value="">All CI</option>
              <option value="success">Passing</option>
              <option value="failure">Failing</option>
              <option value="pending">Pending</option>
            </select>
          </Tooltip>
          <Tooltip text="Highlight a stack of dependent PRs" position="bottom">
            <select
              value={stackFilter ?? ''}
              onChange={(e) => setStackFilter(e.target.value ? Number(e.target.value) : null)}
              className={styles.select}
            >
              <option value="">All PRs</option>
              {(stacks || []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name || `Stack #${s.id}`} ({s.members.length} PRs)
                </option>
              ))}
            </select>
          </Tooltip>
          <Tooltip text="Dims PRs not assigned to this person" position="bottom">
            <select
              value={assigneeFilter ?? ''}
              onChange={(e) => setAssigneeFilter(e.target.value ? Number(e.target.value) : null)}
              className={styles.select}
            >
              <option value="">All assignees</option>
              {activeTeam.map((m: User) => (
                <option key={m.id} value={m.id}>{m.name || m.login}</option>
              ))}
            </select>
          </Tooltip>
        </div>

        {isLoading ? (
          <div className={styles.loading}>Loading PRs...</div>
        ) : (
          <DependencyGraph
            prs={filtered}
            stacks={stacks || []}
            highlightStackId={stackFilter}
            dimAssigneeId={assigneeFilter}
            dimAuthor={authorFilter || null}
            selectedPrId={selectedPrId}
            onSelectPr={selectPr}
            team={activeTeam}
            repoId={repo.id}
            onAssign={(repoId, prNumber, assigneeId) => {
              api.assignPr(repoId, prNumber, assigneeId).then(() => {
                qc.invalidateQueries({ queryKey: ['pulls', repo?.id] });
              });
            }}
          />
        )}
      </div>

      {selectedPrId && repo && (
        <PRDetailPanel repoId={repo.id} prId={selectedPrId} onClose={() => selectPr(null)} />
      )}
    </div>
  );
}
