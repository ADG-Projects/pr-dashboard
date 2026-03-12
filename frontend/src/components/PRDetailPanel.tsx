/** Slide-out right panel showing PR detail, checks, reviews, and requested reviewers. */

import { useState, useRef, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ALLOWED_LABELS, type PRDetail, type User, type Space } from '../api/client';
import { StatusDot } from './StatusDot';
import { Tooltip } from './Tooltip';
import styles from './PRDetailPanel.module.css';

interface Props {
  repoId: number;
  prNumber: number;
  onClose: () => void;
  showRepoLink?: boolean;
}

export function PRDetailPanel({ repoId, prNumber, onClose, showRepoLink = true }: Props) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [addReviewerOpen, setAddReviewerOpen] = useState(false);
  const [reviewerSearch, setReviewerSearch] = useState('');
  const [labelDropdownOpen, setLabelDropdownOpen] = useState(false);
  const addReviewerRef = useRef<HTMLDivElement>(null);
  const reviewerSearchRef = useRef<HTMLInputElement>(null);
  const labelDropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (addReviewerRef.current && !addReviewerRef.current.contains(e.target as Node)) {
        setAddReviewerOpen(false);
      }
      if (labelDropdownRef.current && !labelDropdownRef.current.contains(e.target as Node)) {
        setLabelDropdownOpen(false);
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

  const { data: participantLogins } = useQuery({
    queryKey: ['team-participated', repoId],
    queryFn: () => api.listParticipants(repoId),
    staleTime: 5 * 60 * 1000,
  });
  const participantSet = useMemo(
    () => new Set(participantLogins || []),
    [participantLogins],
  );

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

  const repo = repos?.find((r) => r.id === repoId);

  const repoSpaceSlug = (() => {
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

  const labelMutation = useMutation({
    mutationFn: ({ add, remove }: { add: string[]; remove: string[] }) =>
      api.updateLabels(repoId, prNumber, add, remove),
    onSuccess: invalidatePr,
  });

  // Friendly display names for known bots
  const botDisplayNames: Record<string, string> = {
    'copilot-pull-request-reviewer[bot]': 'Copilot',
  };

  // Build unified reviewer list: merge requested reviewers + reviews
  const unifiedReviewers = useMemo(() => {
    if (!pr) return [];
    const map = new Map<string, {
      login: string;
      avatar_url: string | null;
      state: string;
      stateLabel: string;
    }>();

    // Build a login → avatar_url lookup from all_reviewers and requested reviewers
    const prAvatars = new Map<string, string | null>();
    for (const r of pr.all_reviewers || []) {
      if (r.avatar_url) prAvatars.set(r.login, r.avatar_url);
    }
    for (const r of pr.github_requested_reviewers) {
      if (r.avatar_url && !prAvatars.has(r.login)) prAvatars.set(r.login, r.avatar_url);
    }

    // First pass: reviews sorted by submitted_at ascending (last write wins)
    const sorted = [...pr.reviews].sort(
      (a, b) => new Date(a.submitted_at).getTime() - new Date(b.submitted_at).getTime(),
    );
    for (const r of sorted) {
      const state =
        r.state === 'APPROVED' ? 'approved'
        : r.state === 'CHANGES_REQUESTED' ? 'changes_requested'
        : 'reviewed';
      const stateLabel =
        state === 'approved' ? 'Approved'
        : state === 'changes_requested' ? 'Changes Requested'
        : 'Reviewed';
      map.set(r.reviewer, { login: r.reviewer, avatar_url: prAvatars.get(r.reviewer) ?? null, state, stateLabel });
    }

    // Second pass: requested reviewers not yet in map are "pending"
    for (const r of pr.github_requested_reviewers) {
      if (!map.has(r.login)) {
        map.set(r.login, { login: r.login, avatar_url: r.avatar_url, state: 'pending', stateLabel: 'Pending' });
      }
    }

    // Third pass: commenters without a formal review
    for (const login of pr.commenters_without_review || []) {
      if (!map.has(login)) {
        map.set(login, { login, avatar_url: prAvatars.get(login) ?? null, state: 'commented_only', stateLabel: '\u26A0 Commented (no review)' });
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
              <Tooltip text="Feature branch — click to view on GitHub" position="bottom">
                <a
                  href={`${pr.html_url.replace(/\/pull\/\d+$/, '')}/tree/${pr.head_ref}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={styles.branchName}
                >{pr.head_ref}</a>
              </Tooltip>
              <span className={styles.arrow}>→</span>
              <Tooltip text="Target branch — click to view on GitHub" position="bottom">
                <a
                  href={`${pr.html_url.replace(/\/pull\/\d+$/, '')}/tree/${pr.base_ref}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={styles.branchName}
                >{pr.base_ref}</a>
              </Tooltip>
            </div>
            {(pr.commit_count > 0 || pr.head_sha) && (
              <div className={styles.commitInfo}>
                {pr.commit_count > 0 && (
                  <Tooltip text="View all commits in this PR" position="bottom">
                    <a href={`${pr.html_url}/commits`} target="_blank" rel="noopener noreferrer">
                      {pr.commit_count} {pr.commit_count === 1 ? 'commit' : 'commits'}
                    </a>
                  </Tooltip>
                )}
                {pr.commit_count > 0 && pr.head_sha && <span className={styles.separator}>·</span>}
                {pr.head_sha && (
                  <Tooltip text={`Latest commit: ${pr.head_sha}`} position="bottom">
                    <a
                      href={`${pr.html_url.replace(/\/pull\/\d+$/, '')}/commit/${pr.head_sha}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={styles.sha}
                    >{pr.head_sha.slice(0, 7)}</a>
                  </Tooltip>
                )}
              </div>
            )}
            <div className={styles.author}>
              {avatarMap.get(pr.author) ? (
                <img
                  src={avatarMap.get(pr.author)}
                  alt={pr.author}
                  className={styles.authorAvatar}
                />
              ) : (
                <span className={styles.authorAvatarPlaceholder} />
              )}
              <span className={styles.authorName}>{nameMap.get(pr.author) || pr.author}</span>
            </div>
            {showRepoLink && repo && (
              <button
                className={styles.repoViewLink}
                onClick={() => navigate(`/repos/${repo.owner}/${repo.name}`)}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="2" y="2" width="12" height="12" rx="2" />
                  <path d="M2 6h12" />
                  <path d="M6 6v8" />
                </svg>
                See in repo view
              </button>
            )}
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

          {/* Label */}
          <section className={styles.section}>
            <h3>Label</h3>
            {(() => {
              const currentLabel = pr.labels?.find((l) => ALLOWED_LABELS.some((a) => a.name === l.name));
              const labelIcons: Record<string, React.ReactNode> = {
                bug: <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M4.72 3.22a.75.75 0 011.06 1.06L4.56 5.5h6.88l-1.22-1.22a.75.75 0 011.06-1.06l2.5 2.5a.75.75 0 010 1.06l-2.5 2.5a.75.75 0 11-1.06-1.06L11.44 7H4.56l1.22 1.22a.75.75 0 11-1.06 1.06l-2.5-2.5a.75.75 0 010-1.06l2.5-2.5zM8 13.5A5.5 5.5 0 018 2.5a5.5 5.5 0 010 11z" fill="none" stroke="currentColor" strokeWidth="1.2"/></svg>,
                enhancement: <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M8 2a.75.75 0 01.75.75v4.5h4.5a.75.75 0 010 1.5h-4.5v4.5a.75.75 0 01-1.5 0v-4.5h-4.5a.75.75 0 010-1.5h4.5v-4.5A.75.75 0 018 2z"/></svg>,
                documentation: <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M2 3.5A1.5 1.5 0 013.5 2h9A1.5 1.5 0 0114 3.5v9a1.5 1.5 0 01-1.5 1.5h-9A1.5 1.5 0 012 12.5v-9zM5 5h6M5 8h6M5 11h3" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>,
                refactor: <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M2 8a6 6 0 1012 0A6 6 0 002 8zm8.5-1.5L8 4 5.5 6.5M5.5 9.5L8 12l2.5-2.5" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/></svg>,
                testing: <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M6 2v4L3.5 13.5a1.5 1.5 0 001.5 1.5h6a1.5 1.5 0 001.5-1.5L10 6V2M4.5 2h7" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/></svg>,
              };
              return (
                <div className={styles.labelDropdown} ref={labelDropdownRef}>
                  <button
                    className={styles.labelTrigger}
                    onClick={() => setLabelDropdownOpen(!labelDropdownOpen)}
                    disabled={labelMutation.isPending}
                  >
                    {currentLabel ? (
                      <span className={styles.labelTriggerValue}>
                        <span className={styles.labelDot} style={{ backgroundColor: `#${currentLabel.color}` }} />
                        {labelIcons[currentLabel.name]}
                        {currentLabel.name}
                      </span>
                    ) : (
                      <span className={styles.labelTriggerPlaceholder}>None</span>
                    )}
                    <span className={styles.labelChevron}>{labelDropdownOpen ? '\u25B4' : '\u25BE'}</span>
                  </button>
                  {labelDropdownOpen && (
                    <div className={styles.labelMenu}>
                      <div
                        className={`${styles.labelMenuItem} ${!currentLabel ? styles.labelMenuItemActive : ''}`}
                        onClick={() => {
                          if (currentLabel) labelMutation.mutate({ add: [], remove: [currentLabel.name] });
                          setLabelDropdownOpen(false);
                        }}
                      >
                        None
                      </div>
                      {ALLOWED_LABELS.map((lbl) => (
                        <div
                          key={lbl.name}
                          className={`${styles.labelMenuItem} ${currentLabel?.name === lbl.name ? styles.labelMenuItemActive : ''}`}
                          onClick={() => {
                            const remove = currentLabel ? [currentLabel.name] : [];
                            if (currentLabel?.name !== lbl.name) {
                              labelMutation.mutate({ add: [lbl.name], remove });
                            }
                            setLabelDropdownOpen(false);
                          }}
                        >
                          <span className={styles.labelDot} style={{ backgroundColor: `#${lbl.color}` }} />
                          {labelIcons[lbl.name]}
                          <span>{lbl.name}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })()}
          </section>

          {/* Reviewers */}
          <section className={styles.section}>
            <Tooltip text="Reviewers and review status — synced with GitHub" position="right">
              <h3>Reviewers ({unifiedReviewers.length})</h3>
            </Tooltip>
            {unifiedReviewers.length > 0 ? (
              <div className={styles.reviewList}>
                {unifiedReviewers.map((r) => {
                  const avatarSrc = avatarMap.get(r.login) || r.avatar_url;
                  return (
                  <div key={r.login} className={styles.reviewItem}>
                    {avatarSrc ? (
                      <img
                        src={avatarSrc}
                        alt={r.login}
                        className={styles.ghReviewerAvatar}
                      />
                    ) : (
                      <span className={styles.ghReviewerAvatarPlaceholder} />
                    )}
                    <StatusDot status={r.state} size={7} />
                    <span className={styles.reviewer}>{botDisplayNames[r.login] || nameMap.get(r.login) || r.login}</span>
                    <Tooltip text={
                      r.state === 'approved' ? 'Approved this pull request'
                      : r.state === 'changes_requested' ? 'Requested changes to this pull request'
                      : r.state === 'reviewed' ? 'Submitted a review with comments (no approval or change request)'
                      : r.state === 'commented_only' ? 'Left comments on the PR without submitting a formal GitHub review'
                      : 'Requested as reviewer but hasn\'t submitted a review yet'
                    } position="left">
                      <span className={`${styles.reviewState} ${
                        r.state === 'approved' ? styles.reviewApproved
                        : r.state === 'changes_requested' ? styles.reviewChanges
                        : r.state === 'reviewed' ? styles.reviewCommented
                        : r.state === 'commented_only' ? styles.reviewCommentedOnly
                        : styles.reviewerPending
                      }`}>{r.stateLabel}</span>
                    </Tooltip>
                    <button
                      className={styles.removeReviewerBtn}
                      onClick={() => removeReviewerMutation.mutate(r.login)}
                      disabled={removeReviewerMutation.isPending}
                      title={`Remove ${r.login}`}
                    >
                      x
                    </button>
                  </div>
                  );
                })}
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
                  {(() => {
                    const filtered = notAlreadyRequested.filter(matchesSearch);
                    const hasParticipated = (m: User) =>
                      participantSet.has(m.login) ||
                      m.linked_accounts.some((a) => participantSet.has(a.login));
                    const participated = filtered.filter(hasParticipated);
                    const neverParticipated = filtered.filter((m) => !hasParticipated(m));

                    const renderItem = (m: User) => {
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
                    };

                    if (filtered.length === 0) {
                      return <div className={styles.addReviewerEmpty}>No matching team members</div>;
                    }

                    return (
                      <>
                        {participated.length > 0 && (
                          <>
                            <div className={styles.addReviewerSectionLabel}>Participated</div>
                            {participated.map(renderItem)}
                          </>
                        )}
                        {neverParticipated.length > 0 && (
                          <>
                            <div className={styles.addReviewerSectionLabel}>Never participated</div>
                            {neverParticipated.map(renderItem)}
                          </>
                        )}
                      </>
                    );
                  })()}
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
