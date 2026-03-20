/** Level 2 — Repo view showing open PRs as a dependency graph with stack filtering. */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { useMemo, useRef, useState, useEffect } from 'react';
import { api, ALLOWED_LABELS, type PRSummary, type RepoSummary, type User } from '../api/client';
import { useCurrentUser } from '../App';
import { DependencyGraph } from '../components/DependencyGraph';
import { PRDetailPanel } from '../components/PRDetailPanel';
import { Tooltip } from '../components/Tooltip';
import { useStore, DEFAULT_REPO_FILTERS } from '../store/useStore';
import { buildRepoColorMap } from '../utils/repoColors';
import styles from './RepoView.module.css';

export function RepoView() {
  const { owner, name } = useParams<{ owner: string; name: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { selectedPrNumber, selectPr, setLastReposSectionPath, setRepoFilters, clearRepoFilters, toggleStackCollapsed } = useStore();
  const { user: currentUser } = useCurrentUser();

  const repoKey = `${owner}/${name}`;
  const filters = useStore((s) => s.repoFilters[repoKey] ?? DEFAULT_REPO_FILTERS);
  const { stateFilter, authorFilter, reviewerFilter, ciFilter, branchFilter, priorityFilter, labelFilter, stackFilter, collapsedStacks, flatView } = filters;

  const setFilter = <K extends keyof typeof filters>(key: K, value: (typeof filters)[K]) =>
    setRepoFilters(repoKey, { [key]: value });

  const [renamingStack, setRenamingStack] = useState(false);
  const [renameValue, setRenameValue] = useState('');
  const [showMoreFilters, setShowMoreFilters] = useState(false);
  const [authorDropdownOpen, setAuthorDropdownOpen] = useState(false);
  const [stateDropdownOpen, setStateDropdownOpen] = useState(false);
  const [reviewerDropdownOpen, setReviewerDropdownOpen] = useState(false);
  const [ciDropdownOpen, setCiDropdownOpen] = useState(false);
  const [priorityDropdownOpen, setPriorityDropdownOpen] = useState(false);
  const [branchDropdownOpen, setBranchDropdownOpen] = useState(false);
  const [stackDropdownOpen, setStackDropdownOpen] = useState(false);
  const [labelDropdownOpen, setLabelDropdownOpen] = useState(false);
  const [repoDropdownOpen, setRepoDropdownOpen] = useState(false);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const authorDropdownRef = useRef<HTMLDivElement>(null);
  const stateDropdownRef = useRef<HTMLDivElement>(null);
  const reviewerDropdownRef = useRef<HTMLDivElement>(null);
  const ciDropdownRef = useRef<HTMLDivElement>(null);
  const priorityDropdownRef = useRef<HTMLDivElement>(null);
  const branchDropdownRef = useRef<HTMLDivElement>(null);
  const labelDropdownRef = useRef<HTMLDivElement>(null);
  const stackDropdownRef = useRef<HTMLDivElement>(null);
  const repoDropdownRef = useRef<HTMLDivElement>(null);

  // Remember this repo path so the "Repos" nav link can return here
  useEffect(() => {
    if (owner && name) setLastReposSectionPath(`/repos/${owner}/${name}`);
  }, [owner, name, setLastReposSectionPath]);

  const hasActiveFilters = authorFilter !== '' || ciFilter !== '' || stackFilter !== null || reviewerFilter !== '' || priorityFilter !== '' || branchFilter !== '' || labelFilter !== '' || stateFilter !== 'open';

  const clearAllFilters = () => clearRepoFilters(repoKey);

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
      if (ciDropdownRef.current && !ciDropdownRef.current.contains(e.target as Node)) {
        setCiDropdownOpen(false);
      }
      if (priorityDropdownRef.current && !priorityDropdownRef.current.contains(e.target as Node)) {
        setPriorityDropdownOpen(false);
      }
      if (branchDropdownRef.current && !branchDropdownRef.current.contains(e.target as Node)) {
        setBranchDropdownOpen(false);
      }
      if (labelDropdownRef.current && !labelDropdownRef.current.contains(e.target as Node)) {
        setLabelDropdownOpen(false);
      }
      if (stackDropdownRef.current && !stackDropdownRef.current.contains(e.target as Node)) {
        setStackDropdownOpen(false);
      }
      if (repoDropdownRef.current && !repoDropdownRef.current.contains(e.target as Node)) {
        setRepoDropdownOpen(false);
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

  // When first sync completes, invalidate pulls/stacks so they refetch immediately
  const prevSyncedAt = useRef(repo?.last_synced_at);
  useEffect(() => {
    if (!prevSyncedAt.current && repo?.last_synced_at && repo?.id) {
      qc.invalidateQueries({ queryKey: ['pulls', repo.id], refetchType: 'active' });
      qc.invalidateQueries({ queryKey: ['stacks', repo.id], refetchType: 'active' });
    }
    prevSyncedAt.current = repo?.last_synced_at;
  }, [repo?.last_synced_at, repo?.id, qc]);

  // Build color map across all repos for unique color assignment
  const colorMap = useMemo(() => buildRepoColorMap((repos || []).map((r) => r.full_name)), [repos]);

  // Apply repo color tint to the Shell's .main scroll container
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const main = containerRef.current?.parentElement;
    if (!main || !repo) return;
    const color = `${colorMap.get(repo.full_name) ?? '#888'}18`;
    main.style.background = color;
    return () => { main.style.background = ''; };
  }, [repo, colorMap]);

  const pullParams: Record<string, string> | undefined =
    stateFilter === 'merged' ? { include_merged_days: '7' }
    : stateFilter === 'closed' ? { include_closed_days: '7' }
    : undefined;
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

  // Fetch backend priority scores for flat view sorting
  const { data: prioritized } = useQuery({
    queryKey: ['prioritized', repo?.id, 'all'],
    queryFn: () => api.listPrioritized(repo!.id, 'all'),
    enabled: !!repo && flatView,
  });
  // Map PR id → position in the backend's sorted order (already sorted by tier then score)
  const priorityOrderMap = useMemo(() => {
    if (!prioritized) return undefined;
    const map = new Map<number, number>();
    for (let i = 0; i < prioritized.length; i++) {
      map.set(prioritized[i].pr.id, i);
    }
    return map;
  }, [prioritized]);

  const { data: team } = useQuery({
    queryKey: ['team'],
    queryFn: api.listTeam,
  });
  const activeTeam = team?.filter((m: User) => m.is_active) || [];

  // Collect all GitHub logins belonging to the current user (primary + linked accounts)
  const myLogins = useMemo(() => {
    if (!currentUser) return new Set<string>();
    const me = activeTeam.find((m: User) => m.id === currentUser.id);
    const logins = new Set<string>();
    if (me) {
      logins.add(me.login);
      for (const acct of me.linked_accounts || []) {
        logins.add(acct.login);
      }
    } else {
      logins.add(currentUser.login);
    }
    return logins;
  }, [currentUser, activeTeam]);

  const renameMutation = useMutation({
    mutationFn: ({ stackId, name }: { stackId: number; name: string }) =>
      api.renameStack(repo!.id, stackId, name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['stacks', repo?.id], refetchType: 'active' });
      setRenamingStack(false);
    },
  });

  const syncMutation = useMutation({
    mutationFn: () => api.syncRepo(repo!.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['repos'], refetchType: 'active' });
      qc.invalidateQueries({ queryKey: ['pulls', repo?.id], refetchType: 'active' });
      qc.invalidateQueries({ queryKey: ['stacks', repo?.id], refetchType: 'active' });
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
  else if (stateFilter === 'mixed') filtered = filtered.filter((p: PRSummary) => p.state === 'open' && p.review_state === 'mixed');
  else if (stateFilter === 'draft') filtered = filtered.filter((p: PRSummary) => p.state === 'open' && p.draft);
  else if (stateFilter === 'merged') filtered = filtered.filter((p: PRSummary) => p.merged_at != null);
  else if (stateFilter === 'closed') filtered = filtered.filter((p: PRSummary) => p.state === 'closed' && p.merged_at == null);
  if (stateFilter === 'merged') {
    filtered = [...filtered].sort((a, b) =>
      new Date(b.merged_at!).getTime() - new Date(a.merged_at!).getTime()
    );
  }
  if (stateFilter === 'closed') {
    filtered = [...filtered].sort((a, b) =>
      new Date(b.closed_at ?? 0).getTime() - new Date(a.closed_at ?? 0).getTime()
    );
  }

  // Unique authors for filter dropdown
  const authors = [...new Set(pulls?.map((p: PRSummary) => p.author) || [])].sort();

  // All people actively involved in this repo (authors + requested reviewers)
  const repoPeopleMap = new Map<string, { login: string; avatar: string | null }>();
  for (const p of pulls || []) {
    if (!repoPeopleMap.has(p.author)) {
      repoPeopleMap.set(p.author, { login: p.author, avatar: null });
    }
    for (const r of p.github_requested_reviewers || []) {
      if (!repoPeopleMap.has(r.login)) {
        repoPeopleMap.set(r.login, { login: r.login, avatar: r.avatar_url });
      }
    }
  }
  const reviewers = [...repoPeopleMap.values()].sort((a, b) => a.login.localeCompare(b.login));

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
    { value: 'mixed', label: 'Mixed reviews' },
    { value: 'draft', label: 'Draft' },
    { value: 'merged', label: 'Recently merged' },
    { value: 'closed', label: 'Recently closed' },
  ];

  const ciOptions = [
    { value: '', label: 'All CI' },
    { value: 'success', label: 'Passing' },
    { value: 'failure', label: 'Failing' },
    { value: 'pending', label: 'Pending' },
  ];

  const priorityOptions = [
    { value: '', label: 'All priorities' },
    { value: 'high', label: 'High' },
    { value: 'normal', label: 'Normal' },
    { value: 'low', label: 'Low' },
  ];

  // Filter icons (14px inline SVGs)
  const icons = {
    state: <svg className={styles.filterIcon} viewBox="0 0 16 16" fill="currentColor"><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="1.5"/><circle cx="8" cy="8" r="2.5"/></svg>,
    author: <svg className={styles.filterIcon} viewBox="0 0 16 16" fill="currentColor"><path d="M8 8a3 3 0 100-6 3 3 0 000 6zm-5 7a5 5 0 0110 0H3z"/></svg>,
    reviewer: <svg className={styles.filterIcon} viewBox="0 0 16 16" fill="currentColor"><path d="M8 3C4.5 3 1.7 5.1.5 8c1.2 2.9 4 5 7.5 5s6.3-2.1 7.5-5c-1.2-2.9-4-5-7.5-5zm0 8a3 3 0 110-6 3 3 0 010 6zm0-5a2 2 0 100 4 2 2 0 000-4z"/></svg>,
    ci: <svg className={styles.filterIcon} viewBox="0 0 16 16" fill="currentColor"><path d="M8 1a7 7 0 100 14A7 7 0 008 1zm3.5 5.3l-4 4a.75.75 0 01-1.06 0l-2-2a.75.75 0 111.06-1.06L7 8.74l3.47-3.47a.75.75 0 011.06 1.06z"/></svg>,
    branch: <svg className={styles.filterIcon} viewBox="0 0 16 16" fill="currentColor"><path d="M11.75 2.5a.75.75 0 100 1.5.75.75 0 000-1.5zm-2.25.75a2.25 2.25 0 113 2.122V6.5a2.5 2.5 0 01-2.5 2.5H8.5v2.128a2.251 2.251 0 11-1.5 0V4.872a2.251 2.251 0 111.5 0V5.5H10a1 1 0 001-1v-1.128A2.251 2.251 0 019.5 3.25zM4.25 3.5a.75.75 0 100 1.5.75.75 0 000-1.5zM4.25 12a.75.75 0 100 1.5.75.75 0 000-1.5z" fill="none" stroke="currentColor" strokeWidth="1" /></svg>,
    priority: <svg className={styles.filterIcon} viewBox="0 0 16 16" fill="currentColor"><path d="M3 14V2l5 4 5-4v12l-5-4-5 4z"/></svg>,
    label: <svg className={styles.filterIcon} viewBox="0 0 16 16" fill="currentColor"><path d="M2 2.5A1.5 1.5 0 013.5 1h3.586a1 1 0 01.707.293l6.414 6.414a1 1 0 010 1.414l-3.586 3.586a1 1 0 01-1.414 0L2.793 6.293A1 1 0 012.5 5.586V2.5z" fill="none" stroke="currentColor" strokeWidth="1.3"/><circle cx="5.25" cy="4.25" r="1"/></svg>,
    stack: <svg className={styles.filterIcon} viewBox="0 0 16 16" fill="currentColor"><path d="M8 1L1 5l7 4 7-4-7-4zM1 8l7 4 7-4M1 11l7 4 7-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/></svg>,
  };

  if (!repo) return <div className={styles.loading}>Loading...</div>;

  return (
    <div className={styles.container} ref={containerRef}>
      <div className={styles.content} style={selectedPrNumber ? { marginRight: 396 } : undefined} onClick={(e) => {
        if (!selectedPrNumber) return;
        const target = e.target as HTMLElement;
        if (target.closest('a, button, [role="button"]')) return;
        if (target.closest('[data-pr-card]')) return;
        selectPr(null);
      }}>
        <div className={styles.titleRow}>
          <div className={styles.repoNav}>
            <div className={styles.filterDropdown} ref={repoDropdownRef}>
              <button
                className={`${styles.filterTrigger} ${styles.repoTrigger}`}
                onClick={() => setRepoDropdownOpen(!repoDropdownOpen)}
              >
                {repo && <span className={styles.repoDot} style={{ backgroundColor: colorMap.get(repo.full_name) ?? '#888' }} />}
                <span>{name}</span>
                <span className={styles.filterChevron}>{repoDropdownOpen ? '\u25B4' : '\u25BE'}</span>
              </button>
              {repoDropdownOpen && (
                <div className={styles.filterMenu}>
                  {Object.entries(
                    (repos || []).reduce<Record<string, RepoSummary[]>>((groups, r) => {
                      (groups[r.owner] ??= []).push(r);
                      return groups;
                    }, {})
                  ).map(([ownerGroup, groupRepos]) => (
                    <div key={ownerGroup}>
                      <div className={styles.filterMenuGroupHeader}>{ownerGroup}</div>
                      {groupRepos.map((r) => (
                        <div
                          key={r.id}
                          className={`${styles.filterMenuItem} ${styles.filterMenuItemIndented} ${r.full_name === `${owner}/${name}` ? styles.filterMenuItemActive : ''}`}
                          onClick={() => {
                            navigate(`/repos/${r.owner}/${r.name}`);
                            setRepoDropdownOpen(false);
                            selectPr(null);
                          }}
                        >
                          <span className={styles.repoDot} style={{ backgroundColor: colorMap.get(r.full_name) ?? '#888' }} />
                          <span>{r.name}</span>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          <Tooltip text="Sync this repo from GitHub" position="bottom">
            <button
              className={styles.syncIcon}
              onClick={() => syncMutation.mutate()}
              disabled={syncMutation.isPending}
            >
              {syncMutation.isPending ? (
                <span className={styles.syncIconSpinner} />
              ) : (
                <svg
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
              )}
            </button>
          </Tooltip>
          </div>
          {filtered.length > 0 && (
            <div className={styles.titleStats}>
              <span>{filtered.filter((p: PRSummary) => p.state === 'open').length} open</span>
              {(() => {
                const approvedPrs = filtered.filter((p: PRSummary) => p.review_state === 'approved');
                const approvedActive = stateFilter === 'approved';
                return approvedPrs.length > 0 ? (
                  <Tooltip text={
                    <div className={styles.tooltipPrList}>
                      {approvedPrs.map(p => (
                        <div key={p.number} className={styles.tooltipPrItem}>
                          <span className={styles.tooltipPrNumber}>#{p.number}</span>
                          <span className={styles.tooltipPrTitle}>{p.title}</span>
                        </div>
                      ))}
                    </div>
                  } position="bottom">
                    <span
                      className={`${styles.clickableStat} ${approvedActive ? styles.clickableStatActive : ''}`}
                      style={{ color: 'var(--accent-green)' }}
                      onClick={() => setFilter('stateFilter', approvedActive ? 'open' : 'approved')}
                    >
                      {approvedPrs.length} approved
                    </span>
                  </Tooltip>
                ) : null;
              })()}
              {(() => {
                const failingPrs = filtered.filter((p: PRSummary) => p.ci_status === 'failure');
                const failingActive = ciFilter === 'failure';
                return failingPrs.length > 0 ? (
                  <Tooltip text={
                    <div className={styles.tooltipPrList}>
                      {failingPrs.map(p => (
                        <div key={p.number} className={styles.tooltipPrItem}>
                          <span className={styles.tooltipPrNumber}>#{p.number}</span>
                          <span className={styles.tooltipPrTitle}>{p.title}</span>
                        </div>
                      ))}
                    </div>
                  } position="bottom">
                    <span
                      className={`${styles.clickableStat} ${failingActive ? styles.clickableStatActive : ''}`}
                      style={{ color: 'var(--accent-red)' }}
                      onClick={() => setFilter('ciFilter', failingActive ? '' : 'failure')}
                    >
                      {failingPrs.length} failing
                    </span>
                  </Tooltip>
                ) : null;
              })()}
            </div>
          )}
        </div>

        <div className={styles.filters}>
          {/* Primary filters: State, Author, CI */}
          <Tooltip text="Filters PRs by review state or merged status" position="bottom" disabled={stateDropdownOpen}>
            <div className={styles.filterDropdown} ref={stateDropdownRef}>
              <button
                className={styles.filterTrigger}
                onClick={() => setStateDropdownOpen(!stateDropdownOpen)}
              >
                {icons.state}
                <span>{stateOptions.find((o) => o.value === stateFilter)?.label ?? 'All open'}</span>
                <span className={styles.filterChevron}>{stateDropdownOpen ? '\u25B4' : '\u25BE'}</span>
              </button>
              {stateDropdownOpen && (
                <div className={styles.filterMenu}>
                  {stateOptions.map((o) => (
                    <div
                      key={o.value}
                      className={`${styles.filterMenuItem} ${stateFilter === o.value ? styles.filterMenuItemActive : ''}`}
                      onClick={() => { setFilter('stateFilter',o.value); setStateDropdownOpen(false); }}
                    >
                      <span>{o.label}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Tooltip>

          <Tooltip text="Dims non-matching PR cards" position="bottom" disabled={authorDropdownOpen}>
            <div className={styles.filterDropdown} ref={authorDropdownRef}>
              <button
                className={styles.filterTrigger}
                onClick={() => setAuthorDropdownOpen(!authorDropdownOpen)}
              >
                {icons.author}
                {(() => {
                  if (authorFilter === '__me__') {
                    return (
                      <span className={styles.filterOption}>
                        {currentUser?.avatar_url && <img src={currentUser.avatar_url} alt="Me" className={styles.filterAvatar} />}
                        <span>Me</span>
                      </span>
                    );
                  }
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
                    onClick={() => { setFilter('authorFilter',''); setAuthorDropdownOpen(false); }}
                  >
                    <span>All authors</span>
                  </div>
                  {currentUser && (
                    <div
                      className={`${styles.filterMenuItem} ${authorFilter === '__me__' ? styles.filterMenuItemActive : ''}`}
                      onClick={() => { setRepoFilters(repoKey, { authorFilter: '__me__', reviewerFilter: '' }); setAuthorDropdownOpen(false); }}
                    >
                      {currentUser.avatar_url && <img src={currentUser.avatar_url} alt="Me" className={styles.filterAvatar} />}
                      <span>Me</span>
                    </div>
                  )}
                  {authors.map((a) => {
                    const info = authorInfoMap.get(a);
                    return (
                      <div
                        key={a}
                        className={`${styles.filterMenuItem} ${authorFilter === a ? styles.filterMenuItemActive : ''}`}
                        onClick={() => { setFilter('authorFilter',a); setAuthorDropdownOpen(false); }}
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

          <Tooltip text="Dims PRs not requesting this reviewer" position="bottom" disabled={reviewerDropdownOpen}>
            <div className={styles.filterDropdown} ref={reviewerDropdownRef}>
              <button
                className={styles.filterTrigger}
                onClick={() => setReviewerDropdownOpen(!reviewerDropdownOpen)}
              >
                {icons.reviewer}
                {(() => {
                  if (reviewerFilter === '__me__') {
                    return (
                      <span className={styles.filterOption}>
                        {currentUser?.avatar_url && <img src={currentUser.avatar_url} alt="Me" className={styles.filterAvatar} />}
                        <span>Me</span>
                      </span>
                    );
                  }
                  if (reviewerFilter) {
                    const info = authorInfoMap.get(reviewerFilter);
                    const prData = repoPeopleMap.get(reviewerFilter);
                    const avatar = info?.avatar ?? prData?.avatar ?? null;
                    const displayName = info?.displayName ?? reviewerFilter;
                    return (
                      <span className={styles.filterOption}>
                        {avatar && <img src={avatar} alt={reviewerFilter} className={styles.filterAvatar} />}
                        <span>{displayName}</span>
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
                    onClick={() => { setFilter('reviewerFilter',''); setReviewerDropdownOpen(false); }}
                  >
                    <span>All reviewers</span>
                  </div>
                  {currentUser && (
                    <div
                      className={`${styles.filterMenuItem} ${reviewerFilter === '__me__' ? styles.filterMenuItemActive : ''}`}
                      onClick={() => { setRepoFilters(repoKey, { reviewerFilter: '__me__', authorFilter: '' }); setReviewerDropdownOpen(false); }}
                    >
                      {currentUser.avatar_url && <img src={currentUser.avatar_url} alt="Me" className={styles.filterAvatar} />}
                      <span>Me</span>
                    </div>
                  )}
                  {reviewers.map((r) => {
                    const info = authorInfoMap.get(r.login);
                    const avatar = info?.avatar ?? r.avatar ?? null;
                    const displayName = info?.displayName ?? r.login;
                    return (
                      <div
                        key={r.login}
                        className={`${styles.filterMenuItem} ${reviewerFilter === r.login ? styles.filterMenuItemActive : ''}`}
                        onClick={() => { setFilter('reviewerFilter',r.login); setReviewerDropdownOpen(false); }}
                      >
                        {avatar && <img src={avatar} alt={r.login} className={styles.filterAvatar} />}
                        <span>{displayName}</span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </Tooltip>

          {/* Promoted secondary filters (show when active) */}
          {ciFilter && (
            <Tooltip text="Hides non-matching PRs" position="bottom" disabled={ciDropdownOpen}>
              <div className={styles.filterDropdown} ref={ciDropdownRef}>
                <button
                  className={styles.filterTrigger}
                  onClick={() => setCiDropdownOpen(!ciDropdownOpen)}
                >
                  {icons.ci}
                  <span>{ciOptions.find((o) => o.value === ciFilter)?.label ?? 'All CI'}</span>
                  <span className={styles.filterChevron}>{ciDropdownOpen ? '\u25B4' : '\u25BE'}</span>
                </button>
                {ciDropdownOpen && (
                  <div className={styles.filterMenu}>
                    {ciOptions.map((o) => (
                      <div
                        key={o.value}
                        className={`${styles.filterMenuItem} ${ciFilter === o.value ? styles.filterMenuItemActive : ''}`}
                        onClick={() => { setFilter('ciFilter',o.value); setCiDropdownOpen(false); }}
                      >
                        <span>{o.label}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </Tooltip>
          )}

          {branchFilter && (
            <Tooltip text="Filter PRs by target branch" position="bottom" disabled={branchDropdownOpen}>
              <div className={styles.filterDropdown} ref={branchDropdownRef}>
                <button className={styles.filterTrigger} onClick={() => setBranchDropdownOpen(!branchDropdownOpen)}>
                  {icons.branch}
                  <span>Targeting main</span>
                  <span className={styles.filterChevron}>{branchDropdownOpen ? '\u25B4' : '\u25BE'}</span>
                </button>
                {branchDropdownOpen && (
                  <div className={styles.filterMenu}>
                    <div className={`${styles.filterMenuItem} ${branchFilter === '' ? styles.filterMenuItemActive : ''}`} onClick={() => { setFilter('branchFilter',''); setBranchDropdownOpen(false); }}><span>All targets</span></div>
                    <div className={`${styles.filterMenuItem} ${branchFilter === 'main' ? styles.filterMenuItemActive : ''}`} onClick={() => { setFilter('branchFilter','main'); setBranchDropdownOpen(false); }}><span>Targeting main</span></div>
                  </div>
                )}
              </div>
            </Tooltip>
          )}

          {priorityFilter && (
            <Tooltip text="Filter PRs by manual priority" position="bottom" disabled={priorityDropdownOpen}>
              <div className={styles.filterDropdown} ref={priorityDropdownRef}>
                <button className={styles.filterTrigger} onClick={() => setPriorityDropdownOpen(!priorityDropdownOpen)}>
                  {icons.priority}
                  <span>{priorityOptions.find((o) => o.value === priorityFilter)?.label ?? 'All priorities'}</span>
                  <span className={styles.filterChevron}>{priorityDropdownOpen ? '\u25B4' : '\u25BE'}</span>
                </button>
                {priorityDropdownOpen && (
                  <div className={styles.filterMenu}>
                    {priorityOptions.map((o) => (
                      <div key={o.value} className={`${styles.filterMenuItem} ${priorityFilter === o.value ? styles.filterMenuItemActive : ''}`} onClick={() => { setFilter('priorityFilter',o.value); setPriorityDropdownOpen(false); }}><span>{o.label}</span></div>
                    ))}
                  </div>
                )}
              </div>
            </Tooltip>
          )}

          {labelFilter && (
            <Tooltip text="Dims PRs without this label" position="bottom" disabled={labelDropdownOpen}>
              <div className={styles.filterDropdown} ref={labelDropdownRef}>
                <button className={styles.filterTrigger} onClick={() => setLabelDropdownOpen(!labelDropdownOpen)}>
                  {icons.label}
                  <span>{labelFilter}</span>
                  <span className={styles.filterChevron}>{labelDropdownOpen ? '\u25B4' : '\u25BE'}</span>
                </button>
                {labelDropdownOpen && (
                  <div className={styles.filterMenu}>
                    <div className={`${styles.filterMenuItem} ${!labelFilter ? styles.filterMenuItemActive : ''}`} onClick={() => { setFilter('labelFilter',''); setLabelDropdownOpen(false); }}><span>All labels</span></div>
                    {ALLOWED_LABELS.map((lbl) => (
                      <div key={lbl.name} className={`${styles.filterMenuItem} ${labelFilter === lbl.name ? styles.filterMenuItemActive : ''}`} onClick={() => { setFilter('labelFilter', lbl.name); setLabelDropdownOpen(false); }}>
                        <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', backgroundColor: `#${lbl.color}`, marginRight: 6 }} />
                        <span>{lbl.name}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </Tooltip>
          )}

          {stackFilter !== null && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              {renamingStack ? (
                <input
                  ref={renameInputRef}
                  className={styles.renameInput}
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && renameValue.trim()) { renameMutation.mutate({ stackId: stackFilter, name: renameValue.trim() }); }
                    else if (e.key === 'Escape') { setRenamingStack(false); }
                  }}
                  onBlur={() => { if (renameValue.trim() && stackFilter) { renameMutation.mutate({ stackId: stackFilter, name: renameValue.trim() }); } else { setRenamingStack(false); } }}
                  autoFocus
                />
              ) : (
                <Tooltip text="Highlight a stack of dependent PRs" position="bottom" disabled={stackDropdownOpen}>
                  <div className={styles.filterDropdown} ref={stackDropdownRef}>
                    <button className={styles.filterTrigger} onClick={() => setStackDropdownOpen(!stackDropdownOpen)}>
                      {icons.stack}
                      <span>{(stacks || []).find((s) => s.id === stackFilter)?.name || `#${stackFilter}`}</span>
                      <span className={styles.filterChevron}>{stackDropdownOpen ? '\u25B4' : '\u25BE'}</span>
                    </button>
                    {stackDropdownOpen && (
                      <div className={styles.filterMenu}>
                        <div className={`${styles.filterMenuItem} ${stackFilter === null ? styles.filterMenuItemActive : ''}`} onClick={() => { setFilter('stackFilter',null); setStackDropdownOpen(false); }}><span>All PRs</span></div>
                        {(stacks || []).map((s) => (
                          <div key={s.id} className={`${styles.filterMenuItem} ${stackFilter === s.id ? styles.filterMenuItemActive : ''}`} onClick={() => { setFilter('stackFilter',s.id); setStackDropdownOpen(false); }}><span>{s.name || `#${s.id}`} ({s.members.length} PRs)</span></div>
                        ))}
                      </div>
                    )}
                  </div>
                </Tooltip>
              )}
              {!renamingStack && (
                <button className={styles.syncBtn} style={{ padding: '2px 6px', fontSize: '0.85rem' }} title="Rename stack" onClick={() => { const selected = (stacks || []).find((s) => s.id === stackFilter); setRenameValue(selected?.name || ''); setRenamingStack(true); }}>
                  ✏
                </button>
              )}
            </div>
          )}

          {/* View toggle: graph vs flat */}
          <Tooltip text={flatView ? 'Switch to graph view' : 'Switch to flat view'} position="bottom">
            <button
              className={`${styles.viewToggle} ${flatView ? styles.viewToggleActive : ''}`}
              onClick={() => setFilter('flatView', !flatView)}
            >
              {flatView ? (
                <svg className={styles.filterIcon} viewBox="0 0 16 16"><rect x="1.5" y="2" width="5" height="4" rx="0.8" fill="none" stroke="currentColor" strokeWidth="1.3"/><rect x="9.5" y="2" width="5" height="4" rx="0.8" fill="none" stroke="currentColor" strokeWidth="1.3"/><rect x="1.5" y="10" width="5" height="4" rx="0.8" fill="none" stroke="currentColor" strokeWidth="1.3"/><rect x="9.5" y="10" width="5" height="4" rx="0.8" fill="none" stroke="currentColor" strokeWidth="1.3"/></svg>
              ) : (
                <svg className={styles.filterIcon} viewBox="0 0 16 16"><circle cx="8" cy="3" r="1.8" fill="currentColor"/><circle cx="4" cy="13" r="1.8" fill="currentColor"/><circle cx="12" cy="13" r="1.8" fill="currentColor"/><path d="M8 4.8V8L4 11.2M8 8l4 3.2" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>
              )}
            </button>
          </Tooltip>

          {/* + Filter button for secondary filters */}
          <button
            className={`${styles.moreFiltersBtn} ${showMoreFilters ? styles.moreFiltersBtnActive : ''}`}
            onClick={() => setShowMoreFilters(!showMoreFilters)}
          >
            + Filter
          </button>

          {/* Clear all filters */}
          {hasActiveFilters && (
            <button className={styles.clearFilters} onClick={clearAllFilters} title="Reset all filters">
              <svg className={styles.filterIcon} viewBox="0 0 16 16" fill="currentColor"><path d="M4.646 4.646a.5.5 0 01.708 0L8 7.293l2.646-2.647a.5.5 0 01.708.708L8.707 8l2.647 2.646a.5.5 0 01-.708.708L8 8.707l-2.646 2.647a.5.5 0 01-.708-.708L7.293 8 4.646 5.354a.5.5 0 010-.708z"/></svg>
              Reset
            </button>
          )}
        </div>

        {/* Expanded secondary filters */}
        {showMoreFilters && (
          <div className={styles.filters}>
            {!ciFilter && (
              <Tooltip text="Hides non-matching PRs" position="bottom" disabled={ciDropdownOpen}>
                <div className={styles.filterDropdown} ref={ciDropdownRef}>
                  <button className={styles.filterTrigger} onClick={() => setCiDropdownOpen(!ciDropdownOpen)}>
                    {icons.ci}
                    <span>All CI</span>
                    <span className={styles.filterChevron}>{ciDropdownOpen ? '\u25B4' : '\u25BE'}</span>
                  </button>
                  {ciDropdownOpen && (
                    <div className={styles.filterMenu}>
                      {ciOptions.map((o) => (
                        <div key={o.value} className={`${styles.filterMenuItem} ${ciFilter === o.value ? styles.filterMenuItemActive : ''}`} onClick={() => { setFilter('ciFilter',o.value); setCiDropdownOpen(false); }}><span>{o.label}</span></div>
                      ))}
                    </div>
                  )}
                </div>
              </Tooltip>
            )}

            {!branchFilter && (
              <Tooltip text="Filter PRs by target branch" position="bottom" disabled={branchDropdownOpen}>
                <div className={styles.filterDropdown} ref={branchDropdownRef}>
                  <button className={styles.filterTrigger} onClick={() => setBranchDropdownOpen(!branchDropdownOpen)}>
                    {icons.branch}
                    <span>All targets</span>
                    <span className={styles.filterChevron}>{branchDropdownOpen ? '\u25B4' : '\u25BE'}</span>
                  </button>
                  {branchDropdownOpen && (
                    <div className={styles.filterMenu}>
                      <div className={`${styles.filterMenuItem} ${branchFilter === '' ? styles.filterMenuItemActive : ''}`} onClick={() => { setFilter('branchFilter',''); setBranchDropdownOpen(false); }}><span>All targets</span></div>
                      <div className={`${styles.filterMenuItem} ${branchFilter === 'main' ? styles.filterMenuItemActive : ''}`} onClick={() => { setFilter('branchFilter','main'); setBranchDropdownOpen(false); }}><span>Targeting main</span></div>
                    </div>
                  )}
                </div>
              </Tooltip>
            )}

            {!priorityFilter && (
              <Tooltip text="Filter PRs by manual priority" position="bottom" disabled={priorityDropdownOpen}>
                <div className={styles.filterDropdown} ref={priorityDropdownRef}>
                  <button className={styles.filterTrigger} onClick={() => setPriorityDropdownOpen(!priorityDropdownOpen)}>
                    {icons.priority}
                    <span>All priorities</span>
                    <span className={styles.filterChevron}>{priorityDropdownOpen ? '\u25B4' : '\u25BE'}</span>
                  </button>
                  {priorityDropdownOpen && (
                    <div className={styles.filterMenu}>
                      {priorityOptions.map((o) => (
                        <div key={o.value} className={`${styles.filterMenuItem} ${priorityFilter === o.value ? styles.filterMenuItemActive : ''}`} onClick={() => { setFilter('priorityFilter',o.value); setPriorityDropdownOpen(false); }}><span>{o.label}</span></div>
                      ))}
                    </div>
                  )}
                </div>
              </Tooltip>
            )}

            {!labelFilter && (
              <Tooltip text="Dims PRs without this label" position="bottom" disabled={labelDropdownOpen}>
                <div className={styles.filterDropdown} ref={labelDropdownRef}>
                  <button className={styles.filterTrigger} onClick={() => setLabelDropdownOpen(!labelDropdownOpen)}>
                    {icons.label}
                    <span>All labels</span>
                    <span className={styles.filterChevron}>{labelDropdownOpen ? '\u25B4' : '\u25BE'}</span>
                  </button>
                  {labelDropdownOpen && (
                    <div className={styles.filterMenu}>
                      <div className={`${styles.filterMenuItem} ${!labelFilter ? styles.filterMenuItemActive : ''}`} onClick={() => { setFilter('labelFilter',''); setLabelDropdownOpen(false); }}><span>All labels</span></div>
                      {ALLOWED_LABELS.map((lbl) => (
                        <div key={lbl.name} className={`${styles.filterMenuItem} ${labelFilter === lbl.name ? styles.filterMenuItemActive : ''}`} onClick={() => { setFilter('labelFilter', lbl.name); setLabelDropdownOpen(false); }}>
                          <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', backgroundColor: `#${lbl.color}`, marginRight: 6 }} />
                          <span>{lbl.name}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </Tooltip>
            )}

            {stackFilter === null && (
              <Tooltip text="Highlight a stack of dependent PRs" position="bottom" disabled={stackDropdownOpen}>
                <div className={styles.filterDropdown} ref={stackDropdownRef}>
                  <button className={styles.filterTrigger} onClick={() => setStackDropdownOpen(!stackDropdownOpen)}>
                    {icons.stack}
                    <span>All stacks</span>
                    <span className={styles.filterChevron}>{stackDropdownOpen ? '\u25B4' : '\u25BE'}</span>
                  </button>
                  {stackDropdownOpen && (
                    <div className={styles.filterMenu}>
                      <div className={`${styles.filterMenuItem} ${stackFilter === null ? styles.filterMenuItemActive : ''}`} onClick={() => { setFilter('stackFilter',null); setStackDropdownOpen(false); }}><span>All PRs</span></div>
                      {(stacks || []).map((s) => (
                        <div key={s.id} className={`${styles.filterMenuItem} ${stackFilter === s.id ? styles.filterMenuItemActive : ''}`} onClick={() => { setFilter('stackFilter',s.id); setStackDropdownOpen(false); }}><span>{s.name || `#${s.id}`} ({s.members.length} PRs)</span></div>
                      ))}
                    </div>
                  )}
                </div>
              </Tooltip>
            )}
          </div>
        )}

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
            dimReviewerLogin={reviewerFilter === '__me__' ? myLogins : reviewerFilter || null}
            dimAuthor={authorFilter === '__me__' ? myLogins : authorFilter || null}
            dimBranchTarget={branchFilter || null}
            dimLabel={labelFilter || null}
            flatView={flatView}
            priorityOrderMap={priorityOrderMap}
            selectedPrNumber={selectedPrNumber}
            onSelectPr={selectPr}
            onRenameStack={(stackId, name) => renameMutation.mutate({ stackId, name })}
            nameMap={authorInfoMap}
            collapsedStacks={collapsedStacks}
            onToggleStackCollapsed={(stackId) => toggleStackCollapsed(repoKey, stackId)}
          />
        )}
      </div>

      {selectedPrNumber && repo && (
        <PRDetailPanel repoId={repo.id} prNumber={selectedPrNumber} onClose={() => selectPr(null)} showRepoLink={false} />
      )}
    </div>
  );
}
