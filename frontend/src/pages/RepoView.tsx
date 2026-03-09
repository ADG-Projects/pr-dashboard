/** Level 2 — Repo view showing open PRs as a dependency graph with stack filtering. */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { useRef, useState, useEffect } from 'react';
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
  const [reviewerFilter, setReviewerFilter] = useState('');
  const [renamingStack, setRenamingStack] = useState(false);
  const [renameValue, setRenameValue] = useState('');
  const [reviewerDropdownOpen, setReviewerDropdownOpen] = useState(false);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const reviewerDropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (reviewerDropdownRef.current && !reviewerDropdownRef.current.contains(e.target as Node)) {
        setReviewerDropdownOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  // Get repo ID from the repos list
  // Poll while repo hasn't been synced yet so we pick up last_synced_at
  const { data: repos } = useQuery({
    queryKey: ['repos'],
    queryFn: () => api.listRepos(),
    refetchInterval: (query) => {
      const repoData = query.state.data?.find(
        (r: RepoSummary) => r.owner === owner && r.name === name,
      );
      return repoData && !repoData.last_synced_at ? 3_000 : false;
    },
  });
  const repo = repos?.find((r: RepoSummary) => r.owner === owner && r.name === name);

  // Redirect to home if repo no longer exists (e.g. after unlinking an account)
  useEffect(() => {
    if (repos && !repo) {
      navigate('/', { replace: true });
    }
  }, [repos, repo, navigate]);

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

  const renameMutation = useMutation({
    mutationFn: ({ stackId, name }: { stackId: number; name: string }) =>
      api.renameStack(repo!.id, stackId, name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['stacks', repo?.id] });
      setRenamingStack(false);
    },
  });

  const syncMutation = useMutation({
    mutationFn: () => api.syncRepo(repo!.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['repos'] });
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
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              {renamingStack && stackFilter ? (
                <input
                  ref={renameInputRef}
                  className={styles.select}
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && renameValue.trim()) {
                      renameMutation.mutate({ stackId: stackFilter, name: renameValue.trim() });
                    } else if (e.key === 'Escape') {
                      setRenamingStack(false);
                    }
                  }}
                  onBlur={() => {
                    if (renameValue.trim() && stackFilter) {
                      renameMutation.mutate({ stackId: stackFilter, name: renameValue.trim() });
                    } else {
                      setRenamingStack(false);
                    }
                  }}
                  autoFocus
                />
              ) : (
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
              )}
              {stackFilter && !renamingStack && (
                <button
                  className={styles.syncBtn}
                  style={{ padding: '2px 6px', fontSize: '0.85rem' }}
                  title="Rename stack"
                  onClick={() => {
                    const selected = (stacks || []).find((s) => s.id === stackFilter);
                    setRenameValue(selected?.name || '');
                    setRenamingStack(true);
                  }}
                >
                  ✏
                </button>
              )}
            </div>
          </Tooltip>
          <Tooltip text="Dims PRs not requesting this reviewer" position="bottom">
            <div className={styles.reviewerDropdown} ref={reviewerDropdownRef}>
              <button
                className={styles.reviewerTrigger}
                onClick={() => setReviewerDropdownOpen(!reviewerDropdownOpen)}
              >
                {(() => {
                  const selected = activeTeam.find((m: User) => m.login === reviewerFilter);
                  if (selected) {
                    return (
                      <span className={styles.reviewerOption}>
                        {selected.avatar_url && <img src={selected.avatar_url} alt={selected.login} className={styles.reviewerAvatar} />}
                        <span>{selected.name || selected.login}</span>
                      </span>
                    );
                  }
                  return <span>All reviewers</span>;
                })()}
                <span className={styles.reviewerChevron}>{reviewerDropdownOpen ? '\u25B4' : '\u25BE'}</span>
              </button>
              {reviewerDropdownOpen && (
                <div className={styles.reviewerMenu}>
                  <div
                    className={`${styles.reviewerMenuItem} ${!reviewerFilter ? styles.reviewerMenuItemActive : ''}`}
                    onClick={() => { setReviewerFilter(''); setReviewerDropdownOpen(false); }}
                  >
                    <span>All reviewers</span>
                  </div>
                  {activeTeam.map((m: User) => (
                    <div
                      key={m.id}
                      className={`${styles.reviewerMenuItem} ${reviewerFilter === m.login ? styles.reviewerMenuItemActive : ''}`}
                      onClick={() => { setReviewerFilter(m.login); setReviewerDropdownOpen(false); }}
                    >
                      {m.avatar_url && <img src={m.avatar_url} alt={m.login} className={styles.reviewerAvatar} />}
                      <span>{m.name || m.login}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Tooltip>
        </div>

        {!repo.last_synced_at ? (
          <div className={styles.syncing}>
            <span className={styles.syncSpinner} />
            Syncing repository — pull requests will appear shortly...
          </div>
        ) : isLoading ? (
          <div className={styles.loading}>Loading PRs...</div>
        ) : (
          <DependencyGraph
            prs={filtered}
            stacks={stacks || []}
            highlightStackId={stackFilter}
            dimReviewerLogin={reviewerFilter || null}
            dimAuthor={authorFilter || null}
            selectedPrId={selectedPrId}
            onSelectPr={selectPr}
          />
        )}
      </div>

      {selectedPrId && repo && (
        <PRDetailPanel repoId={repo.id} prId={selectedPrId} onClose={() => selectPr(null)} />
      )}
    </div>
  );
}
