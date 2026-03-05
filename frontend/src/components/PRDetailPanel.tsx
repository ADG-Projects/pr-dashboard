/** Slide-out right panel showing PR detail, checks, reviews. */

import { useQuery } from '@tanstack/react-query';
import { api, type PRDetail } from '../api/client';
import { StatusDot } from './StatusDot';
import styles from './PRDetailPanel.module.css';

interface Props {
  repoId: number;
  prId: number;
  onClose: () => void;
}

export function PRDetailPanel({ repoId, prId, onClose }: Props) {
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
            {pr.rebased_since_approval && (
              <div className={styles.rebaseWarning}>Rebased since last approval</div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
