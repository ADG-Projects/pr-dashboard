/** Slide-out right panel showing PR detail, checks, reviews, assignee, and team progress. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type PRDetail } from '../api/client';
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

  const assigneeMutation = useMutation({
    mutationFn: (assigneeId: number | null) =>
      api.assignPr(repoId, prSummary!.number, assigneeId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pulls', repoId] });
      qc.invalidateQueries({ queryKey: ['pr-detail', repoId, prSummary?.number] });
    },
  });

  const { data: progress } = useQuery({
    queryKey: ['progress', prId],
    queryFn: () => api.getProgress(prId),
    enabled: !!prId,
  });

  const progressMutation = useMutation({
    mutationFn: (data: { user_id: number; reviewed?: boolean; approved?: boolean }) =>
      api.updateProgress(prId, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['progress', prId] });
    },
  });


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
          {/* Assignee */}
          <section className={styles.section}>
            <Tooltip text="Requested reviewers synced from GitHub" position="right">
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
                  </div>
                ))}
              </div>
            ) : (
              <div className={styles.ghUnassigned}>No reviewers requested</div>
            )}
            <div className={styles.overrideLabel}>Dashboard override</div>
            <select
              className={styles.assigneeSelect}
              value={pr.assignee_id ?? ''}
              onChange={(e) => {
                const val = e.target.value;
                assigneeMutation.mutate(val ? Number(val) : null);
              }}
              disabled={assigneeMutation.isPending}
            >
              <option value="">None</option>
              {activeTeam.map((m) => (
                <option key={m.id} value={m.id}>{m.name || m.login}</option>
              ))}
            </select>
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

          {/* Team Progress */}
          {activeTeam.length > 0 && (
            <section className={styles.section}>
              <Tooltip text="Track which team members have reviewed and approved" position="right">
                <h3>Team Progress</h3>
              </Tooltip>
              <div className={styles.progressList}>
                {activeTeam.map((member) => {
                  const p = progress?.find((x) => x.user_id === member.id);
                  return (
                    <div key={member.id} className={styles.progressRow}>
                      <span className={styles.progressName}>{member.name || member.login}</span>
                      <Tooltip text="Reviewed" position="top">
                        <label className={styles.progressCheck}>
                          <input
                            type="checkbox"
                            checked={p?.reviewed ?? false}
                            onChange={(e) =>
                              progressMutation.mutate({
                                user_id: member.id,
                                reviewed: e.target.checked,
                              })
                            }
                          />
                          R
                        </label>
                      </Tooltip>
                      <Tooltip text="Approved" position="top">
                        <label className={styles.progressCheck}>
                          <input
                            type="checkbox"
                            checked={p?.approved ?? false}
                            onChange={(e) =>
                              progressMutation.mutate({
                                user_id: member.id,
                                approved: e.target.checked,
                              })
                            }
                          />
                          A
                        </label>
                      </Tooltip>
                    </div>
                  );
                })}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
