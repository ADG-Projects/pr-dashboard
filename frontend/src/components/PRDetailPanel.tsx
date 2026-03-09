/** Slide-out right panel showing PR detail, checks, reviews, and requested reviewers. */

import { useState, useRef, useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type PRDetail, type User, type Space } from '../api/client';
import { StatusDot } from './StatusDot';
import { Tooltip } from './Tooltip';
import styles from './PRDetailPanel.module.css';

interface Props {
  repoId: number;
  prId: number;
  onClose: () => void;
}

export function PRDetailPanel({ repoId, prId, onClose }: Props) {
  const qc = useQueryClient();
  const [addReviewerOpen, setAddReviewerOpen] = useState(false);
  const addReviewerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (addReviewerRef.current && !addReviewerRef.current.contains(e.target as Node)) {
        setAddReviewerOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const { data: pulls } = useQuery({
    queryKey: ['pulls', repoId],
    queryFn: () => api.listPulls(repoId),
    enabled: !!repoId,
  });
  const prSummary = pulls?.find((p) => p.id === prId);

  const { data: detail } = useQuery({
    queryKey: ['pr-detail', repoId, prSummary?.number],
    queryFn: () => api.getPull(repoId, prSummary!.number),
    enabled: !!prSummary,
  });

  const pr: PRDetail | undefined = detail;

  const { data: team } = useQuery({
    queryKey: ['team'],
    queryFn: api.listTeam,
  });
  const activeTeam = team?.filter((m) => m.is_active) || [];

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
    qc.invalidateQueries({ queryKey: ['pulls', repoId] });
    qc.invalidateQueries({ queryKey: ['pr-detail', repoId, prSummary?.number] });
  };

  const addReviewerMutation = useMutation({
    mutationFn: (userId: number) =>
      api.updateReviewers(repoId, prSummary!.number, [userId], []),
    onSuccess: invalidatePr,
  });

  const removeReviewerMutation = useMutation({
    mutationFn: (login: string) =>
      api.updateReviewers(repoId, prSummary!.number, [], [login]),
    onSuccess: invalidatePr,
  });

  // Team members not already requested as reviewers
  const currentReviewerLogins = new Set(
    pr?.github_requested_reviewers.map((r) => r.login) || [],
  );
  const availableReviewers = activeTeam.filter(
    (m: User) => !currentReviewerLogins.has(m.login),
  );


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
          {/* Reviewers */}
          <section className={styles.section}>
            <Tooltip text="Requested reviewers — synced with GitHub" position="right">
              <h3>Reviewers</h3>
            </Tooltip>
            {pr.github_requested_reviewers.length > 0 ? (
              <div className={styles.ghReviewerList}>
                {pr.github_requested_reviewers.map((r) => (
                  <div key={r.login} className={styles.ghReviewer}>
                    {r.avatar_url && (
                      <img
                        src={r.avatar_url}
                        alt={r.login}
                        className={styles.ghReviewerAvatar}
                      />
                    )}
                    <span className={styles.ghReviewerLogin}>{r.login}</span>
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
              <div className={styles.ghUnassigned}>No reviewers requested</div>
            )}
            {availableReviewers.length > 0 && (
              <div className={styles.addReviewerDropdown} ref={addReviewerRef}>
                <button
                  className={styles.addReviewerTrigger}
                  onClick={() => setAddReviewerOpen(!addReviewerOpen)}
                  disabled={addReviewerMutation.isPending}
                >
                  <span className={styles.addReviewerPlaceholder}>Add reviewer...</span>
                  <span className={styles.addReviewerChevron}>{addReviewerOpen ? '\u25B4' : '\u25BE'}</span>
                </button>
                {addReviewerOpen && (
                  <div className={styles.addReviewerMenu}>
                    {availableReviewers.map((m: User) => {
                      const resolved = resolveLogin(m);
                      return (
                        <div
                          key={m.id}
                          className={styles.addReviewerMenuItem}
                          onClick={() => {
                            addReviewerMutation.mutate(m.id);
                            setAddReviewerOpen(false);
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
                  </div>
                )}
              </div>
            )}
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

          {/* Reviews */}
          <section className={styles.section}>
            <Tooltip text="GitHub review approvals and feedback" position="right">
              <h3>Reviews ({pr.reviews.length})</h3>
            </Tooltip>
            {pr.reviews.length === 0 ? (
              <div className={styles.empty}>No reviews yet</div>
            ) : (
              <div className={styles.reviewList}>
                {pr.reviews.map((r) => (
                  <div key={r.id} className={styles.reviewItem}>
                    <StatusDot status={r.state.toLowerCase()} size={7} />
                    <span className={styles.reviewer}>{r.reviewer}</span>
                    <span className={`${styles.reviewState} ${r.state === 'APPROVED' ? styles.reviewApproved : r.state === 'CHANGES_REQUESTED' ? styles.reviewChanges : styles.reviewCommented}`}>{r.state}</span>
                  </div>
                ))}
              </div>
            )}
            {pr.rebased_since_approval && (
              <Tooltip text="New commits were force-pushed after the last approval — re-review may be needed" position="top">
                <div className={styles.rebaseWarning}>Rebased since last approval</div>
              </Tooltip>
            )}
          </section>

        </div>
      )}
    </div>
  );
}
