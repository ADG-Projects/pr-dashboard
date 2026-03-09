/** Prioritize view — cross-repo ranked list of open PRs by priority score. */

import { useQuery } from '@tanstack/react-query';
import { Fragment, useState } from 'react';
import { api, type PrioritizedPR, type PriorityBreakdown, type RepoSummary } from '../api/client';
import { PRDetailPanel } from '../components/PRDetailPanel';
import { useStore } from '../store/useStore';
import styles from './PrioritizeView.module.css';

function scoreColor(score: number): string {
  if (score >= 70) return 'var(--accent-green)';
  if (score >= 40) return 'var(--accent-amber)';
  return 'var(--accent-red)';
}

function reviewBadge(state: string) {
  const map: Record<string, { label: string; cls: string }> = {
    approved: { label: 'Approved', cls: styles.badgeGreen },
    changes_requested: { label: 'Changes', cls: styles.badgeRed },
    reviewed: { label: 'Reviewed', cls: styles.badgePurple },
    none: { label: 'No review', cls: styles.badgeDim },
  };
  const info = map[state] || map.none;
  return <span className={`${styles.badge} ${info.cls}`}>{info.label}</span>;
}

function ciBadge(status: string) {
  const map: Record<string, { label: string; cls: string }> = {
    success: { label: 'CI pass', cls: styles.badgeGreen },
    failure: { label: 'CI fail', cls: styles.badgeRed },
    pending: { label: 'CI pending', cls: styles.badgeAmber },
    unknown: { label: 'CI unknown', cls: styles.badgeDim },
  };
  const info = map[status] || map.unknown;
  return <span className={`${styles.badge} ${info.cls}`}>{info.label}</span>;
}

function BreakdownTooltip({ breakdown, score }: { breakdown: PriorityBreakdown; score: number }) {
  return (
    <div className={styles.breakdownTooltip}>
      <div className={styles.breakdownTitle}>Score breakdown ({score}/100)</div>
      <div className={styles.breakdownRow}><span>Review readiness</span><span>{breakdown.review}/35</span></div>
      <div className={styles.breakdownRow}><span>CI status</span><span>{breakdown.ci}/25</span></div>
      <div className={styles.breakdownRow}><span>Size (inverse)</span><span>{breakdown.size}/15</span></div>
      <div className={styles.breakdownRow}><span>Mergeable</span><span>{breakdown.mergeable}/10</span></div>
      <div className={styles.breakdownRow}><span>Age</span><span>{breakdown.age}/10</span></div>
      <div className={styles.breakdownRow}><span>Rebase check</span><span>{breakdown.rebase}/5</span></div>
      {breakdown.draft_penalty < 0 && (
        <div className={`${styles.breakdownRow} ${styles.breakdownPenalty}`}>
          <span>Draft penalty</span><span>{breakdown.draft_penalty}</span>
        </div>
      )}
    </div>
  );
}

function ScoringGuide({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <div className={styles.guide}>
      <button className={styles.guideToggle} onClick={onToggle}>
        How scoring works {open ? '\u25B4' : '\u25BE'}
      </button>
      {open && (
        <div className={styles.guideBody}>
          <p>Each PR is scored 0–100 based on how ready it is to review and merge:</p>
          <table className={styles.guideTable}>
            <thead><tr><th>Signal</th><th>Max</th><th>Logic</th></tr></thead>
            <tbody>
              <tr><td>Review readiness</td><td>35</td><td>Approved (35), reviewed (20), no review (10), changes requested (0)</td></tr>
              <tr><td>CI status</td><td>25</td><td>Passing (25), pending (10), unknown (5), failing (0)</td></tr>
              <tr><td>Diff size</td><td>15</td><td>Smaller PRs score higher — easier to review, less rebase risk</td></tr>
              <tr><td>Mergeable</td><td>10</td><td>Clean (10), unstable (5), conflicts/blocked (0)</td></tr>
              <tr><td>Age</td><td>10</td><td>Older PRs rise in priority (linear over 7 days)</td></tr>
              <tr><td>Rebase check</td><td>5</td><td>+5 if rebased since last approval (needs re-review)</td></tr>
              <tr><td>Draft</td><td>-30</td><td>Penalty for draft PRs (not ready for review)</td></tr>
            </tbody>
          </table>
          <p><strong>Manual priority</strong> overrides score-based ordering. High-priority PRs always appear at the top, low-priority at the bottom. Within each tier, PRs are sorted by automated score.</p>
          <p><strong>Stacked PRs</strong> are ordered parent-before-child — you review the base of the stack first. The stack&apos;s position in the queue is determined by the root PR&apos;s score.</p>
          <p>Hover over any score bar to see the full breakdown. Set priority from the PR detail panel.</p>
        </div>
      )}
    </div>
  );
}

export function PrioritizeView() {
  const { selectedPrId, selectPr, selectedRepoId, setSelectedRepoId } = useStore();
  const [hoveredId, setHoveredId] = useState<number | null>(null);
  const [filterRepoId, setFilterRepoId] = useState<number | undefined>(undefined);
  const [guideOpen, setGuideOpen] = useState(false);

  const { data: repos } = useQuery({
    queryKey: ['repos'],
    queryFn: () => api.listRepos(),
  });

  const { data: items, isLoading } = useQuery({
    queryKey: ['prioritized', filterRepoId],
    queryFn: () => api.listPrioritized(filterRepoId),
    refetchInterval: 30_000,
  });

  if (isLoading) return <div className={styles.loading}>Loading prioritized PRs...</div>;

  const prs = items || [];
  const readyCount = prs.filter((p) => p.priority_score >= 70 && !p.blocked_by_pr_id).length;
  const needsAttentionCount = prs.filter((p) => p.priority_score < 40).length;

  function handleSelectPr(item: PrioritizedPR) {
    setSelectedRepoId(item.repo_id);
    selectPr(item.pr.id);
  }

  return (
    <div className={styles.container}>
      <div className={styles.content}>
        <div className={styles.header}>
          <h2 className={styles.title}>Priority Queue</h2>
          <select
            className={styles.repoFilter}
            value={filterRepoId ?? ''}
            onChange={(e) => setFilterRepoId(e.target.value ? Number(e.target.value) : undefined)}
          >
            <option value="">All repos</option>
            {(repos || []).map((r: RepoSummary) => (
              <option key={r.id} value={r.id}>{r.full_name}</option>
            ))}
          </select>
          <div className={styles.summaryBar}>
            <span className={styles.summaryItem}>
              <span className={styles.summaryCount}>{prs.length}</span> open
            </span>
            <span className={`${styles.summaryItem} ${styles.summaryGreen}`}>
              <span className={styles.summaryCount}>{readyCount}</span> ready to merge
            </span>
            <span className={`${styles.summaryItem} ${styles.summaryRed}`}>
              <span className={styles.summaryCount}>{needsAttentionCount}</span> needs attention
            </span>
          </div>
        </div>

        <ScoringGuide open={guideOpen} onToggle={() => setGuideOpen(!guideOpen)} />

        {prs.length === 0 ? (
          <div className={styles.empty}>No open PRs found across your tracked repos.</div>
        ) : (
          <div className={styles.list}>
            {prs.map((item, idx) => {
              // Show tier separator when the tier changes
              const prevTier = idx > 0 ? prs[idx - 1].priority_tier : null;
              const showSeparator = item.priority_tier !== prevTier;
              const tierLabel = item.priority_tier === 'high' ? 'High Priority' : item.priority_tier === 'low' ? 'Low Priority' : 'Normal Priority';
              return (<Fragment key={item.pr.id}>
              {showSeparator && (
                <div className={`${styles.tierSeparator} ${styles[`tierSeparator${item.priority_tier.charAt(0).toUpperCase() + item.priority_tier.slice(1)}`]}`}>
                  {tierLabel}
                </div>
              )}

              <div
                className={`${styles.row} ${selectedPrId === item.pr.id ? styles.rowSelected : ''}`}
                onClick={() => handleSelectPr(item)}
              >
                <div className={styles.position}>#{item.merge_position}</div>

                <div className={styles.main}>
                  <div className={styles.prTitleRow}>
                    <span className={styles.repoName}>{item.repo_full_name}</span>
                    <a
                      href={item.pr.html_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={styles.prLink}
                      onClick={(e) => e.stopPropagation()}
                    >
                      #{item.pr.number}
                    </a>
                    <span className={styles.prTitle}>{item.pr.title}</span>
                  </div>
                  <div className={styles.prMeta}>
                    <span className={styles.author}>{item.pr.author}</span>
                    <span className={styles.lines}>
                      <span className={styles.additions}>+{item.pr.additions}</span>
                      <span className={styles.deletions}>-{item.pr.deletions}</span>
                    </span>
                    {item.pr.manual_priority === 'high' && <span className={`${styles.badge} ${styles.badgeRed}`}>{'\u2191'} High</span>}
                    {item.pr.manual_priority === 'low' && <span className={`${styles.badge} ${styles.badgeDim}`}>{'\u2193'} Low</span>}
                    {item.pr.draft && <span className={`${styles.badge} ${styles.badgeDim}`}>Draft</span>}
                    {reviewBadge(item.pr.review_state)}
                    {ciBadge(item.pr.ci_status)}
                    {item.stack_name && (
                      <span className={`${styles.badge} ${styles.badgeStack}`}>{item.stack_name}</span>
                    )}
                    {item.blocked_by_pr_id && (
                      <span className={`${styles.badge} ${styles.badgeAmber}`}>Blocked</span>
                    )}
                  </div>
                </div>

                <div
                  className={styles.scoreArea}
                  onMouseEnter={() => setHoveredId(item.pr.id)}
                  onMouseLeave={() => setHoveredId(null)}
                >
                  <div className={styles.scoreBar}>
                    <div
                      className={styles.scoreFill}
                      style={{
                        width: `${item.priority_score}%`,
                        backgroundColor: scoreColor(item.priority_score),
                      }}
                    />
                  </div>
                  <span className={styles.scoreLabel} style={{ color: scoreColor(item.priority_score) }}>
                    {item.priority_score}
                  </span>
                  {hoveredId === item.pr.id && (
                    <BreakdownTooltip breakdown={item.priority_breakdown} score={item.priority_score} />
                  )}
                </div>
              </div>
              </Fragment>);
            })}
          </div>
        )}
      </div>

      {selectedPrId && selectedRepoId && (
        <PRDetailPanel
          repoId={selectedRepoId}
          prId={selectedPrId}
          onClose={() => { selectPr(null); setSelectedRepoId(null); }}
        />
      )}
    </div>
  );
}
