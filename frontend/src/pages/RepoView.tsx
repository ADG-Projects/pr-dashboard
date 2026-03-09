/** Level 2 — Repo view showing open PRs as a dependency graph with stack filtering. */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { useRef, useState, useEffect } from 'react';
import { api, type PRSummary, type RepoSummary, type Space, type User } from '../api/client';
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
  const [priorityFilter, setPriorityFilter] = useState('');
  const [stateFilter, setStateFilter] = useState('open');
  const [renamingStack, setRenamingStack] = useState(false);
  const [renameValue, setRenameValue] = useState('');
  const [authorDropdownOpen, setAuthorDropdownOpen] = useState(false);
  const [stateDropdownOpen, setStateDropdownOpen] = useState(false);
  const [reviewerDropdownOpen, setReviewerDropdownOpen] = useState(false);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const authorDropdownRef = useRef<HTMLDivElement>(null);
  const stateDropdownRef = useRef<HTMLDivElement>(null);
  const reviewerDropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (authorDropdownRef.current && !authorDropdownRef.current.contains(e.target as Node)) {
        setAuthorDropdownOpen(false);
      }
      if (stateDropdownRef.current && !stateDropdownRef.current.contains(e.target as Node)) {
        setStateDropdownOpen(false);
      }
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

  const pullParams = stateFilter === 'merged' ? { include_merged_days: '7' } : undefined;
  const { data: pulls, isLoading } = useQuery({
    queryKey: ['pulls', repo?.id, stateFilter],
    queryFn: () => api.listPulls(repo!.id, pullParams),
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

  const { data: spaces } = useQuery({
    queryKey: ['spaces'],
    queryFn: api.listSpaces,
  });

  // Resolve the repo's space slug for linked account lookups
  const repoSpaceSlug = (() => {
    if (!repo?.space_id || !spaces) return null;
    return spaces.find((s: Space) => s.id === repo.space_id)?.slug ?? null;
  })();

  // Resolve a team member's display info for the current repo's space.
  // If they have a linked account for this space, prefer that identity.
  const resolveUser = (user: User): { login: string; avatar: string | null } => {
    if (repoSpaceSlug) {
      const match = user.linked_accounts.find((a) =>
        a.space_slugs.includes(repoSpaceSlug),
      );
      if (match) return { login: match.login, avatar: match.avatar_url };
    }
    return { login: user.login, avatar: user.avatar_url };
  };

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

  // Hard filters: CI and state; author/reviewer dim cards
  let filtered = pulls || [];
  if (ciFilter) filtered = filtered.filter((p: PRSummary) => p.ci_status === ciFilter);
  if (priorityFilter === 'high') filtered = filtered.filter((p: PRSummary) => p.manual_priority === 'high');
  else if (priorityFilter === 'normal') filtered = filtered.filter((p: PRSummary) => p.manual_priority == null || (p.manual_priority !== 'high' && p.manual_priority !== 'low'));
  else if (priorityFilter === 'low') filtered = filtered.filter((p: PRSummary) => p.manual_priority === 'low');
  if (stateFilter === 'open') filtered = filtered.filter((p: PRSummary) => p.state === 'open');
  else if (stateFilter === 'needs_review') filtered = filtered.filter((p: PRSummary) => p.state === 'open' && p.review_state === 'none' && !p.draft);
  else if (stateFilter === 'reviewed') filtered = filtered.filter((p: PRSummary) => p.state === 'open' && p.review_state === 'reviewed');
  else if (stateFilter === 'approved') filtered = filtered.filter((p: PRSummary) => p.state === 'open' && p.review_state === 'approved');
  else if (stateFilter === 'changes_requested') filtered = filtered.filter((p: PRSummary) => p.state === 'open' && p.review_state === 'changes_requested');
  else if (stateFilter === 'draft') filtered = filtered.filter((p: PRSummary) => p.state === 'open' && p.draft);
  else if (stateFilter === 'merged') filtered = filtered.filter((p: PRSummary) => p.merged_at != null);

  // Unique authors for filter dropdown
  const authors = [...new Set(pulls?.map((p: PRSummary) => p.author) || [])].sort();

  // Build GitHub login → { avatar, displayName } from team members + linked accounts.
  // This maps PR author logins (GitHub identities) to display info.
  const authorInfoMap = new Map<string, { avatar: string | null; displayName: string }>();
  for (const m of activeTeam) {
    const displayName = m.name || m.login;
    // Add all linked account logins pointing to this user's display name
    for (const acct of m.linked_accounts || []) {
      authorInfoMap.set(acct.login, { avatar: acct.avatar_url, displayName });
    }
    // Also add the app-level login
    if (!authorInfoMap.has(m.login)) {
      authorInfoMap.set(m.login, { avatar: m.avatar_url, displayName });
    }
  }

  const stateOptions = [
    { value: 'open', label: 'All open' },
    { value: 'needs_review', label: 'Needs review' },
    { value: 'reviewed', label: 'Reviewed' },
    { value: 'approved', label: 'Approved' },
    { value: 'changes_requested', label: 'Changes requested' },
    { value: 'draft', label: 'Draft' },
    { value: 'merged', label: 'Recently merged' },
  ];

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
            <div className={styles.filterDropdown} ref={authorDropdownRef}>
              <button
                className={styles.filterTrigger}
                onClick={() => setAuthorDropdownOpen(!authorDropdownOpen)}
              >
                {(() => {
                  const info = authorFilter ? authorInfoMap.get(authorFilter) : null;
                  if (authorFilter) {
                    return (
                      <span className={styles.filterOption}>
                        {info?.avatar && <img src={info.avatar} alt={authorFilter} className={styles.filterAvatar} />}
                        <span>{info?.displayName || authorFilter}</span>
                      </span>
                    );
                  }
                  return <span>All authors</span>;
                })()}
                <span className={styles.filterChevron}>{authorDropdownOpen ? '\u25B4' : '\u25BE'}</span>
              </button>
              {authorDropdownOpen && (
                <div className={styles.filterMenu}>
                  <div
                    className={`${styles.filterMenuItem} ${!authorFilter ? styles.filterMenuItemActive : ''}`}
                    onClick={() => { setAuthorFilter(''); setAuthorDropdownOpen(false); }}
                  >
                    <span>All authors</span>
                  </div>
                  {authors.map((a) => {
                    const info = authorInfoMap.get(a);
                    return (
                      <div
                        key={a}
                        className={`${styles.filterMenuItem} ${authorFilter === a ? styles.filterMenuItemActive : ''}`}
                        onClick={() => { setAuthorFilter(a); setAuthorDropdownOpen(false); }}
                      >
                        {info?.avatar && <img src={info.avatar} alt={a} className={styles.filterAvatar} />}
                        <span>{info?.displayName || a}</span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </Tooltip>
          <Tooltip text="Hides non-matching PRs" position="bottom">
            <select value={ciFilter} onChange={(e) => setCiFilter(e.target.value)} className={styles.select}>
              <option value="">All CI</option>
              <option value="success">Passing</option>
              <option value="failure">Failing</option>
              <option value="pending">Pending</option>
            </select>
          </Tooltip>
          <Tooltip text="Filter PRs by manual priority" position="bottom">
            <select value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)} className={styles.select}>
              <option value="">All priorities</option>
              <option value="high">High</option>
              <option value="normal">Normal</option>
              <option value="low">Low</option>
            </select>
          </Tooltip>
          <Tooltip text="Filters PRs by review state or merged status" position="bottom">
            <div className={styles.filterDropdown} ref={stateDropdownRef}>
              <button
                className={styles.filterTrigger}
                onClick={() => setStateDropdownOpen(!stateDropdownOpen)}
              >
                <span>{stateOptions.find((o) => o.value === stateFilter)?.label ?? 'All open'}</span>
                <span className={styles.filterChevron}>{stateDropdownOpen ? '\u25B4' : '\u25BE'}</span>
              </button>
              {stateDropdownOpen && (
                <div className={styles.filterMenu}>
                  {stateOptions.map((o) => (
                    <div
                      key={o.value}
                      className={`${styles.filterMenuItem} ${stateFilter === o.value ? styles.filterMenuItemActive : ''}`}
                      onClick={() => { setStateFilter(o.value); setStateDropdownOpen(false); }}
                    >
                      <span>{o.label}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
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
            <div className={styles.filterDropdown} ref={reviewerDropdownRef}>
              <button
                className={styles.filterTrigger}
                onClick={() => setReviewerDropdownOpen(!reviewerDropdownOpen)}
              >
                {(() => {
                  const selected = activeTeam.find((m: User) => resolveUser(m).login === reviewerFilter);
                  if (selected) {
                    const r = resolveUser(selected);
                    return (
                      <span className={styles.filterOption}>
                        {r.avatar && <img src={r.avatar} alt={r.login} className={styles.filterAvatar} />}
                        <span>{selected.name || r.login}</span>
                      </span>
                    );
                  }
                  return <span>All reviewers</span>;
                })()}
                <span className={styles.filterChevron}>{reviewerDropdownOpen ? '\u25B4' : '\u25BE'}</span>
              </button>
              {reviewerDropdownOpen && (
                <div className={styles.filterMenu}>
                  <div
                    className={`${styles.filterMenuItem} ${!reviewerFilter ? styles.filterMenuItemActive : ''}`}
                    onClick={() => { setReviewerFilter(''); setReviewerDropdownOpen(false); }}
                  >
                    <span>All reviewers</span>
                  </div>
                  {activeTeam.map((m: User) => {
                    const r = resolveUser(m);
                    return (
                      <div
                        key={m.id}
                        className={`${styles.filterMenuItem} ${reviewerFilter === r.login ? styles.filterMenuItemActive : ''}`}
                        onClick={() => { setReviewerFilter(r.login); setReviewerDropdownOpen(false); }}
                      >
                        {r.avatar && <img src={r.avatar} alt={r.login} className={styles.filterAvatar} />}
                        <span>{m.name || r.login}</span>
                      </div>
                    );
                  })}
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
