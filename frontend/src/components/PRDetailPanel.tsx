/** Slide-out right panel showing PR detail, checks, reviews, and requested reviewers. */

import { useState, useRef, useEffect, useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type PRDetail, type User, type Space } from '../api/client';
import { StatusDot } from './StatusDot';
import { Tooltip } from './Tooltip';
import styles from './PRDetailPanel.module.css';

interface Props {
  repoId: number;
  prNumber: number;
  onClose: () => void;
}

export function PRDetailPanel({ repoId, prNumber, onClose }: Props) {
  const qc = useQueryClient();
  const [addReviewerOpen, setAddReviewerOpen] = useState(false);
  const [reviewerSearch, setReviewerSearch] = useState('');
  const addReviewerRef = useRef<HTMLDivElement>(null);
  const reviewerSearchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (addReviewerRef.current && !addReviewerRef.current.contains(e.target as Node)) {
        setAddReviewerOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const { data: detail } = useQuery({
    queryKey: ['pr-detail', repoId, prNumber],
    queryFn: () => api.getPull(repoId, prNumber),
    enabled: !!prNumber,
  });

  const pr: PRDetail | undefined = detail;

  const { data: team } = useQuery({
    queryKey: ['team'],
    queryFn: api.listTeam,
  });
  const activeTeam = team?.filter((m) => m.is_active) || [];

  // Build login → display name and login → avatar maps from team data
  const nameMap = new Map<string, string>();
  const avatarMap = new Map<string, string>();
  for (const m of (team || [])) {
    const displayName = m.name || m.login;
    for (const acct of m.linked_accounts || []) {
      nameMap.set(acct.login, displayName);
      if (acct.avatar_url) avatarMap.set(acct.login, acct.avatar_url);
    }
    if (!nameMap.has(m.login)) {
      nameMap.set(m.login, displayName);
    }
    if (m.avatar_url && !avatarMap.has(m.login)) {
      avatarMap.set(m.login, m.avatar_url);
    }
  }

  const { data: repos } = useQuery({
    queryKey: ['repos'],
    queryFn: () => api.listRepos(),
  });
  const { data: spaces } = useQuery({
    queryKey: ['spaces'],
    queryFn: api.listSpaces,
  });

  const repoSpaceSlug = (() => {
    const repo = repos?.find((r) => r.id === repoId);
    if (!repo?.space_id || !spaces) return null;
    return spaces.find((s: Space) => s.id === repo.space_id)?.slug ?? null;
  })();

  const resolveLogin = (user: User): string | null => {
    if (!repoSpaceSlug || user.linked_accounts.length <= 1) return null;
    const match = user.linked_accounts.find((a) =>
      a.space_slugs.includes(repoSpaceSlug),
    );
    if (!match || match.login === (user.name || user.login)) return null;
    return match.login;
  };

  const invalidatePr = () => {
    qc.invalidateQueries({ queryKey: ['pulls', repoId], refetchType: 'active' });
    qc.invalidateQueries({ queryKey: ['pr-detail', repoId, prNumber], refetchType: 'active' });
  };

  const addReviewerMutation = useMutation({
    mutationFn: (userId: number) =>
      api.updateReviewers(repoId, prNumber, [userId], []),
    onSuccess: invalidatePr,
  });

  const removeReviewerMutation = useMutation({
    mutationFn: (login: string) =>
      api.updateReviewers(repoId, prNumber, [], [login]),
    onSuccess: invalidatePr,
  });

  const priorityMutation = useMutation({
    mutationFn: (priority: string | null) =>
      api.setPriority(repoId, prNumber, priority),
    onSuccess: () => {
      invalidatePr();
      qc.invalidateQueries({ queryKey: ['prioritized'], refetchType: 'active' });
    },
  });

  // Build unified reviewer list: merge requested reviewers + reviews
  const unifiedReviewers = useMemo(() => {
    if (!pr) return [];
    const map = new Map<string, {
      login: string;
      state: string;
      stateLabel: string;
    }>();

    // First pass: reviews sorted by submitted_at ascending (last write wins)
    const sorted = [...pr.reviews].sort(
      (a, b) => new Date(a.submitted_at).getTime() - new Date(b.submitted_at).getTime(),
    );
    for (const r of sorted) {
      const state =
        r.state === 'APPROVED' ? 'approved'
        : r.state === 'CHANGES_REQUESTED' ? 'changes_requested'
        : 'reviewed';
      const stateLabel = r.state.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase());
      map.set(r.reviewer, { login: r.reviewer, state, stateLabel });
    }

    // Second pass: requested reviewers not yet in map are "pending"
    for (const r of pr.github_requested_reviewers) {
      if (!map.has(r.login)) {
        map.set(r.login, { login: r.login, state: 'pending', stateLabel: 'Pending' });
      }
    }

    // Third pass: commenters without a formal review
    for (const login of pr.commenters_without_review || []) {
      if (!map.has(login)) {
        map.set(login, { login, state: 'commented_only', stateLabel: '\u26A0 Commented (no review)' });
      }
    }

    const stateOrder: Record<string, number> = {
      changes_requested: 0,
      pending: 1,
      commented_only: 2,
      reviewed: 2,
      approved: 3,
    };
    return Array.from(map.values()).sort(
      (a, b) => (stateOrder[a.state] ?? 2) - (stateOrder[b.state] ?? 2)
        || a.login.localeCompare(b.login),
    );
  }, [pr]);

  // Exclude anyone already in the unified list from the add-reviewer dropdown
  const allReviewerLogins = new Set(unifiedReviewers.map((r) => r.login));

  const notAlreadyRequested = activeTeam.filter(
    (m: User) => !allReviewerLogins.has(m.login),
  );

  const matchesSearch = (m: User) => {
    if (!reviewerSearch) return true;
    const q = reviewerSearch.toLowerCase();
    return (m.name || '').toLowerCase().includes(q) || m.login.toLowerCase().includes(q);
  };


  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <button onClick={onClose} className={styles.closeBtn}>x</button>
        {pr ? (
          <>
            <h2 className={styles.title}>
              <Tooltip text="Open on GitHub" position="bottom">
                <a href={pr.html_url} target="_blank" rel="noopener noreferrer">#{pr.number}</a>
              </Tooltip>
              {' '}{pr.title}
            </h2>
            <div className={styles.branch}>
              <Tooltip text="Feature branch" position="bottom">
                <span className={styles.branchName}>{pr.head_ref}</span>
              </Tooltip>
              <span className={styles.arrow}>→</span>
              <Tooltip text="Target branch" position="bottom">
                <span className={styles.branchName}>{pr.base_ref}</span>
              </Tooltip>
            </div>
          </>
        ) : (
          <div className={styles.loading}>Loading...</div>
        )}
      </div>

      {pr && (
        <div className={styles.body}>
          {/* Priority */}
          <section className={styles.section}>
            <h3>Priority</h3>
            <div className={styles.priorityToggle}>
              {(['high', null, 'low'] as const).map((val) => {
                const label = val === 'high' ? 'High' : val === 'low' ? 'Low' : 'Normal';
                const current = pr?.manual_priority ?? null;
                const isActive = current === val;
                return (
                  <button
                    key={label}
                    className={`${styles.priorityBtn} ${isActive ? styles[`priorityBtn${label}`] : ''}`}
                    onClick={() => {
                      if (!isActive) priorityMutation.mutate(val);
                    }}
                    disabled={priorityMutation.isPending}
                  >
                    {val === 'high' && '\u2191 '}{val === 'low' && '\u2193 '}{label}
                  </button>
                );
              })}
            </div>
          </section>

          {/* Reviewers */}
          <section className={styles.section}>
            <Tooltip text="Reviewers and review status — synced with GitHub" position="right">
              <h3>Reviewers ({unifiedReviewers.length})</h3>
            </Tooltip>
            {unifiedReviewers.length > 0 ? (
              <div className={styles.reviewList}>
                {unifiedReviewers.map((r) => (
                  <div key={r.login} className={styles.reviewItem}>
                    {avatarMap.get(r.login) ? (
                      <img
                        src={avatarMap.get(r.login)}
                        alt={r.login}
                        className={styles.ghReviewerAvatar}
                      />
                    ) : (
                      <span className={styles.ghReviewerAvatarPlaceholder} />
                    )}
                    <StatusDot status={r.state} size={7} />
                    <span className={styles.reviewer}>{nameMap.get(r.login) || r.login}</span>
                    <span className={`${styles.reviewState} ${
                      r.state === 'approved' ? styles.reviewApproved
                      : r.state === 'changes_requested' ? styles.reviewChanges
                      : r.state === 'reviewed' ? styles.reviewCommented
                      : r.state === 'commented_only' ? styles.reviewCommentedOnly
                      : styles.reviewerPending
                    }`}>{r.stateLabel}</span>
                    <button
                      className={styles.removeReviewerBtn}
                      onClick={() => removeReviewerMutation.mutate(r.login)}
                      disabled={removeReviewerMutation.isPending}
                      title={`Remove ${r.login}`}
                    >
                      x
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <div className={styles.ghUnassigned}>No reviewers</div>
            )}
            {pr.rebased_since_approval && (
              <Tooltip text="New commits were force-pushed after the last approval — re-review may be needed" position="top">
                <div className={styles.rebaseWarning}>Rebased since last approval</div>
              </Tooltip>
            )}
            <div className={styles.addReviewerDropdown} ref={addReviewerRef}>
              <button
                className={styles.addReviewerTrigger}
                onClick={() => {
                  const opening = !addReviewerOpen;
                  setAddReviewerOpen(opening);
                  if (opening) {
                    setReviewerSearch('');
                    setTimeout(() => reviewerSearchRef.current?.focus(), 0);
                  }
                }}
                disabled={addReviewerMutation.isPending}
              >
                <span className={styles.addReviewerPlaceholder}>Add reviewer...</span>
                <span className={styles.addReviewerChevron}>{addReviewerOpen ? '\u25B4' : '\u25BE'}</span>
              </button>
              {addReviewerOpen && (
                <div className={styles.addReviewerMenu}>
                  <div className={styles.addReviewerSearchWrap}>
                    <input
                      ref={reviewerSearchRef}
                      className={styles.addReviewerSearchInput}
                      placeholder="Search team members..."
                      value={reviewerSearch}
                      onChange={(e) => setReviewerSearch(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Escape') setAddReviewerOpen(false);
                      }}
                    />
                  </div>
                  {notAlreadyRequested.filter(matchesSearch).map((m: User) => {
                    const resolved = resolveLogin(m);
                    return (
                      <div
                        key={m.id}
                        className={styles.addReviewerMenuItem}
                        onClick={() => {
                          addReviewerMutation.mutate(m.id);
                          setAddReviewerOpen(false);
                          setReviewerSearch('');
                        }}
                      >
                        {m.avatar_url && <img src={m.avatar_url} alt={m.login} className={styles.addReviewerAvatar} />}
                        <div className={styles.addReviewerInfo}>
                          <span>{m.name || m.login}</span>
                          {resolved && (
                            <span className={styles.addReviewerHint}>will use @{resolved}</span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                  {notAlreadyRequested.filter(matchesSearch).length === 0 && (
                    <div className={styles.addReviewerEmpty}>No matching team members</div>
                  )}
                </div>
              )}
            </div>
          </section>

          {/* Diff stats */}
          <section className={styles.section}>
            <h3>Changes</h3>
            <div className={styles.diffStats}>
              <Tooltip text="Files modified" position="bottom">
                <span className={styles.files}>{pr.changed_files} files</span>
              </Tooltip>
              <Tooltip text="Lines added" position="bottom">
                <span className={styles.add}>+{pr.additions}</span>
              </Tooltip>
              <Tooltip text="Lines removed" position="bottom">
                <span className={styles.del}>-{pr.deletions}</span>
              </Tooltip>
            </div>
          </section>

          {/* Check Runs */}
          <section className={styles.section}>
            <Tooltip text="Status checks required for merge" position="right">
              <h3>CI Checks ({pr.check_runs.length})</h3>
            </Tooltip>
            {pr.check_runs.length === 0 ? (
              <div className={styles.empty}>No checks</div>
            ) : (
              <table className={styles.checksTable}>
                <tbody>
                  {pr.check_runs.map((c) => (
                    <tr key={c.id}>
                      <td><StatusDot status={c.conclusion || c.status} size={7} /></td>
                      <td>
                        {c.details_url ? (
                          <a href={c.details_url} target="_blank" rel="noopener noreferrer">{c.name}</a>
                        ) : c.name}
                      </td>
                      <td className={styles.conclusion}>{c.conclusion || c.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>


        </div>
      )}
    </div>
  );
}
