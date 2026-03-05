/** Slide-out right panel showing PR detail, checks, reviews, assignee, and team progress. */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type PRDetail } from '../api/client';
import { StatusDot } from './StatusDot';
import styles from './PRDetailPanel.module.css';

interface Props {
  repoId: number;
  prId: number;
  onClose: () => void;
}

export function PRDetailPanel({ repoId, prId, onClose }: Props) {
  const qc = useQueryClient();

  // We need the PR number — look it up from the pulls cache or fetch it
  // For simplicity, we'll use a separate query that matches by ID
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

  const trackingMutation = useMutation({
    mutationFn: (data: { reviewed?: boolean; approved?: boolean }) =>
      api.updateTracking(repoId, prSummary!.number, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pulls', repoId] });
      qc.invalidateQueries({ queryKey: ['pr-detail', repoId, prSummary?.number] });
      qc.invalidateQueries({ queryKey: ['stacks', repoId] });
    },
  });

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
    mutationFn: (data: { team_member_id: number; reviewed?: boolean; approved?: boolean }) =>
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
              <a href={pr.html_url} target="_blank" rel="noopener noreferrer">#{pr.number}</a>
              {' '}{pr.title}
            </h2>
            <div className={styles.branch}>
              <span className={styles.branchName}>{pr.head_ref}</span>
              <span className={styles.arrow}>→</span>
              <span className={styles.branchName}>{pr.base_ref}</span>
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
            <h3>Assignee</h3>
            <select
              className={styles.assigneeSelect}
              value={pr.assignee_id ?? ''}
              onChange={(e) => {
                const val = e.target.value;
                assigneeMutation.mutate(val ? Number(val) : null);
              }}
              disabled={assigneeMutation.isPending}
            >
              <option value="">Unassigned</option>
              {activeTeam.map((m) => (
                <option key={m.id} value={m.id}>{m.display_name}</option>
              ))}
            </select>
          </section>

          {/* Diff stats */}
          <section className={styles.section}>
            <h3>Changes</h3>
            <div className={styles.diffStats}>
              <span className={styles.files}>{pr.changed_files} files</span>
              <span className={styles.add}>+{pr.additions}</span>
              <span className={styles.del}>-{pr.deletions}</span>
            </div>
          </section>

          {/* Check Runs */}
          <section className={styles.section}>
            <h3>CI Checks ({pr.check_runs.length})</h3>
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
            <h3>Reviews ({pr.reviews.length})</h3>
            {pr.reviews.length === 0 ? (
              <div className={styles.empty}>No reviews yet</div>
            ) : (
              <div className={styles.reviewList}>
                {pr.reviews.map((r) => (
                  <div key={r.id} className={styles.reviewItem}>
                    <StatusDot status={r.state.toLowerCase()} size={7} />
                    <span className={styles.reviewer}>{r.reviewer}</span>
                    <span className={styles.reviewState}>{r.state}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Dashboard Tracking */}
          <section className={styles.section}>
            <h3>Tracking</h3>
            <div className={styles.trackingRow}>
              <button
                className={`${styles.trackingBtn} ${pr.dashboard_reviewed ? styles.trackingActive : ''}`}
                onClick={() => trackingMutation.mutate({ reviewed: !pr.dashboard_reviewed })}
                disabled={trackingMutation.isPending}
                title="Mark as reviewed"
              >
                R
              </button>
              <span className={styles.trackingLabel}>Reviewed</span>
            </div>
            <div className={styles.trackingRow}>
              <button
                className={`${styles.trackingBtn} ${pr.dashboard_approved ? styles.trackingActive : ''} ${pr.rebased_since_approval ? styles.trackingWarn : ''}`}
                onClick={() => trackingMutation.mutate({ approved: !pr.dashboard_approved })}
                disabled={trackingMutation.isPending}
                title={pr.rebased_since_approval ? 'Rebased since approval — click to re-confirm' : 'Mark as approved'}
              >
                A
              </button>
              <span className={styles.trackingLabel}>Approved</span>
              {pr.rebased_since_approval && (
                <span className={styles.rebaseWarning}>rebased</span>
              )}
            </div>
          </section>

          {/* Team Progress */}
          {activeTeam.length > 0 && (
            <section className={styles.section}>
              <h3>Team Progress</h3>
              <div className={styles.progressList}>
                {activeTeam.map((member) => {
                  const p = progress?.find((x) => x.team_member_id === member.id);
                  return (
                    <div key={member.id} className={styles.progressRow}>
                      <span className={styles.progressName}>{member.display_name}</span>
                      <label className={styles.progressCheck}>
                        <input
                          type="checkbox"
                          checked={p?.reviewed ?? false}
                          onChange={(e) =>
                            progressMutation.mutate({
                              team_member_id: member.id,
                              reviewed: e.target.checked,
                            })
                          }
                        />
                        R
                      </label>
                      <label className={styles.progressCheck}>
                        <input
                          type="checkbox"
                          checked={p?.approved ?? false}
                          onChange={(e) =>
                            progressMutation.mutate({
                              team_member_id: member.id,
                              approved: e.target.checked,
                            })
                          }
                        />
                        A
                      </label>
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
