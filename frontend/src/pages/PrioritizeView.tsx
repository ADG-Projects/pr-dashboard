/** Prioritize view — cross-repo ranked list of open PRs by priority score. */

import { useQuery } from '@tanstack/react-query';
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { api, type PrioritizedPR, type PriorityBreakdown, type PriorityMode, type RepoSummary, type User } from '../api/client';
import { useCurrentUser } from '../App';
import { PRDetailPanel } from '../components/PRDetailPanel';
import { Tooltip } from '../components/Tooltip';
import { useStore } from '../store/useStore';
import { repoColor } from '../utils/repoColors';
import styles from './PrioritizeView.module.css';

function scoreColor(score: number): string {
  if (score >= 70) return 'var(--accent-green)';
  if (score >= 40) return 'var(--accent-amber)';
  return 'var(--accent-red)';
}


function BreakdownTooltip({ breakdown, score, mode }: { breakdown: PriorityBreakdown; score: number; mode: PriorityMode | null }) {
  if (mode === 'review') {
    return (
      <div className={styles.breakdownTooltip}>
        <div className={styles.breakdownTitle}>Score breakdown ({score}/100)</div>
        <div className={styles.breakdownRow}><span>Ball in my court</span><span>{breakdown.review}/35</span></div>
        <div className={styles.breakdownRow}><span>CI passing</span><span>{breakdown.ci}/20</span></div>
        <div className={styles.breakdownRow}><span>Small diff</span><span>{breakdown.size}/15</span></div>
        <div className={styles.breakdownRow}><span>Age</span><span>{breakdown.age}/15</span></div>
        <div className={styles.breakdownRow}><span>Mergeable</span><span>{breakdown.mergeable}/10</span></div>
        {breakdown.draft_penalty < 0 && (
          <div className={`${styles.breakdownRow} ${styles.breakdownPenalty}`}>
            <span>Draft penalty</span><span>{breakdown.draft_penalty}</span>
          </div>
        )}
      </div>
    );
  }
  if (mode === 'owner') {
    return (
      <div className={styles.breakdownTooltip}>
        <div className={styles.breakdownTitle}>Score breakdown ({score}/100)</div>
        <div className={styles.breakdownRow}><span>Changes requested</span><span>{breakdown.review}/30</span></div>
        <div className={styles.breakdownRow}><span>CI needs fix</span><span>{breakdown.ci}/25</span></div>
        <div className={styles.breakdownRow}><span>Has conflicts</span><span>{breakdown.mergeable}/15</span></div>
        <div className={styles.breakdownRow}><span>Ready to merge</span><span>{breakdown.size}/15</span></div>
        <div className={styles.breakdownRow}><span>Age</span><span>{breakdown.age}/10</span></div>
        <div className={styles.breakdownRow}><span>New feedback</span><span>{breakdown.rebase}/5</span></div>
        {breakdown.draft_penalty < 0 && (
          <div className={`${styles.breakdownRow} ${styles.breakdownPenalty}`}>
            <span>Draft penalty</span><span>{breakdown.draft_penalty}</span>
          </div>
        )}
      </div>
    );
  }
  // Default/legacy mode
  return (
    <div className={styles.breakdownTooltip}>
      <div className={styles.breakdownTitle}>Score breakdown ({score}/100)</div>
      <div className={styles.breakdownRow}><span>Review readiness</span><span>{breakdown.review}/35</span></div>
      <div className={styles.breakdownRow}><span>CI status</span><span>{breakdown.ci}/25</span></div>
      <div className={styles.breakdownRow}><span>Mergeable</span><span>{breakdown.mergeable}/15</span></div>
      <div className={styles.breakdownRow}><span>Size (inverse)</span><span>{breakdown.size}/10</span></div>
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

type ScoreImpact = 'positive' | 'negative' | 'neutral';
type ScoredPhrase = { text: string; tip: string; impact: ScoreImpact; priority: number };

function scoreSummaryReview(b: PriorityBreakdown): ScoredPhrase[] {
  const phrases: ScoredPhrase[] = [];

  // Ball in my court (max 35)
  if (b.review === 35) phrases.push({ text: 'Never reviewed', tip: "You haven't reviewed this PR yet, you're blocking", impact: 'negative', priority: 9 });
  else if (b.review === 30) phrases.push({ text: 'New changes', tip: 'Author pushed new commits since your last review', impact: 'negative', priority: 8 });
  else if (b.review === 0) phrases.push({ text: 'Waiting on author', tip: "You've reviewed and nothing changed since, ball is in author's court", impact: 'positive', priority: 1 });

  // CI (max 20)
  if (b.ci === 20) phrases.push({ text: 'CI passing', tip: 'All checks passing', impact: 'positive', priority: 3 });
  else if (b.ci === 8) phrases.push({ text: 'CI pending', tip: 'Checks still running', impact: 'neutral', priority: 5 });
  else if (b.ci === 0) phrases.push({ text: 'CI failing', tip: 'Checks are failing, may not be worth reviewing yet', impact: 'negative', priority: 7 });

  // Size (max 15)
  if (b.size >= 12) phrases.push({ text: 'Quick review', tip: 'Small diff, easy to review', impact: 'positive', priority: 2 });
  else if (b.size === 0) phrases.push({ text: 'Very large diff', tip: 'Over 1000 lines, hard to review', impact: 'negative', priority: 5 });

  // Mergeable (max 10)
  if (b.mergeable === 10) phrases.push({ text: 'Clean merge', tip: 'No conflicts', impact: 'positive', priority: 1 });
  else if (b.mergeable === 0) phrases.push({ text: 'Has conflicts', tip: 'Merge conflicts, needs rebase', impact: 'negative', priority: 6 });

  // Age (max 15)
  if (b.age >= 10) phrases.push({ text: 'Getting stale', tip: 'Open for a while, aging PRs risk merge conflicts', impact: 'neutral', priority: 3 });

  // Draft
  if (b.draft_penalty < 0) phrases.push({ text: 'Draft', tip: 'Draft PR, not ready for review', impact: 'negative', priority: 10 });

  const order: Record<ScoreImpact, number> = { positive: 0, neutral: 1, negative: 2 };
  phrases.sort((a, b) => order[a.impact] - order[b.impact] || b.priority - a.priority);
  return phrases.slice(0, 4);
}

function scoreSummaryOwner(b: PriorityBreakdown): ScoredPhrase[] {
  const phrases: ScoredPhrase[] = [];

  // Changes requested (review field, max 30)
  if (b.review === 30) phrases.push({ text: 'Changes requested', tip: 'A reviewer requested changes, address their feedback', impact: 'negative', priority: 9 });

  // CI needs fix (ci field, max 25) - inverted: high score = failure
  if (b.ci >= 20) phrases.push({ text: 'CI broken', tip: 'CI is failing, fix it before it can be merged', impact: 'negative', priority: 8 });
  else if (b.ci === 5) phrases.push({ text: 'CI pending', tip: 'CI checks still running', impact: 'neutral', priority: 4 });
  else if (b.ci === 0) phrases.push({ text: 'CI passing', tip: 'All checks passing', impact: 'positive', priority: 2 });

  // Conflicts (mergeable field, max 15) - inverted: high score = conflicts
  if (b.mergeable === 15) phrases.push({ text: 'Has conflicts', tip: 'Merge conflicts, needs rebase', impact: 'negative', priority: 7 });

  // Ready to merge (size field, max 15)
  if (b.size === 15) phrases.push({ text: 'Ready to merge!', tip: 'Approved, CI passing, clean merge - ship it!', impact: 'positive', priority: 10 });
  else if (b.size >= 5) phrases.push({ text: 'Nearly ready', tip: 'Approved but missing CI or clean merge', impact: 'neutral', priority: 5 });

  // New feedback (rebase field, max 5)
  if (b.rebase === 5) phrases.push({ text: 'New feedback', tip: 'Someone left review comments', impact: 'neutral', priority: 6 });

  // Draft
  if (b.draft_penalty < 0) phrases.push({ text: 'Draft', tip: 'Draft PR, not yet published', impact: 'negative', priority: 10 });

  const order: Record<ScoreImpact, number> = { positive: 0, neutral: 1, negative: 2 };
  phrases.sort((a, b) => order[a.impact] - order[b.impact] || b.priority - a.priority);
  return phrases.slice(0, 4);
}

function scoreSummaryDefault(b: PriorityBreakdown, reviewState: string): ScoredPhrase[] {
  const phrases: ScoredPhrase[] = [];

  if (b.review === 35) phrases.push({ text: 'Approved', tip: 'Review approved, ready to merge', impact: 'positive', priority: 6 });
  else if (reviewState === 'reviewed') phrases.push({ text: 'Reviewed', tip: 'Has review comments but not yet approved', impact: 'neutral', priority: 4 });
  else if (reviewState === 'none') phrases.push({ text: 'Awaiting review', tip: 'No reviews yet, needs someone to look at it', impact: 'negative', priority: 6 });
  else if (b.review === 0) phrases.push({ text: 'Changes requested', tip: 'Reviewer requested changes, must be addressed before merge', impact: 'negative', priority: 9 });

  if (b.ci === 25) phrases.push({ text: 'CI passing', tip: 'All checks passing, safe to merge', impact: 'positive', priority: 3 });
  else if (b.ci === 10) phrases.push({ text: 'CI pending', tip: 'Checks still running, wait for results', impact: 'neutral', priority: 5 });
  else if (b.ci === 0) phrases.push({ text: 'CI failing', tip: 'Checks are failing, must be fixed before merge', impact: 'negative', priority: 8 });

  if (b.size === 10) phrases.push({ text: 'Small diff', tip: 'Small change, easy to review', impact: 'positive', priority: 1 });
  else if (b.size === 0) phrases.push({ text: 'Very large diff', tip: 'Over 1000 lines changed, hard to review, consider splitting', impact: 'negative', priority: 5 });
  else if (b.size === 2) phrases.push({ text: 'Large diff', tip: '500-1000 lines changed, may be hard to review', impact: 'negative', priority: 4 });

  if (b.mergeable === 15) phrases.push({ text: 'Clean merge', tip: 'No conflicts, can merge cleanly', impact: 'positive', priority: 2 });
  else if (b.mergeable === 0) phrases.push({ text: 'Has conflicts', tip: 'Merge conflicts detected, needs rebase', impact: 'negative', priority: 7 });

  if (b.age >= 8) phrases.push({ text: 'Getting stale', tip: 'Open for over a week, aging PRs risk merge conflicts', impact: 'neutral', priority: 3 });
  if (b.rebase === 5) phrases.push({ text: 'Freshly rebased', tip: 'Rebased since last approval, up to date with base branch', impact: 'positive', priority: 1 });
  if (b.draft_penalty < 0) phrases.push({ text: 'Draft', tip: 'Draft PR, not ready for review, large score penalty', impact: 'negative', priority: 10 });

  const order: Record<ScoreImpact, number> = { positive: 0, neutral: 1, negative: 2 };
  phrases.sort((a, b) => order[a.impact] - order[b.impact] || b.priority - a.priority);
  return phrases.slice(0, 4);
}

function getScoreSummary(b: PriorityBreakdown, reviewState: string, mode: PriorityMode | null): ScoredPhrase[] {
  if (mode === 'review') return scoreSummaryReview(b);
  if (mode === 'owner') return scoreSummaryOwner(b);
  return scoreSummaryDefault(b, reviewState);
}

function ScoringGuide({ open, onToggle, mode }: { open: boolean; onToggle: () => void; mode: PriorityMode | null }) {
  return (
    <div className={styles.guide}>
      <button className={styles.guideToggle} onClick={onToggle}>
        How scoring works {open ? '\u25B4' : '\u25BE'}
      </button>
      {open && (
        <div className={styles.guideBody}>
          {mode === 'review' ? (
            <>
              <p>Each PR is scored 0-100 based on how urgently it needs your review:</p>
              <table className={styles.guideTable}>
                <thead><tr><th>Signal</th><th>Max</th><th>Logic</th></tr></thead>
                <tbody>
                  <tr><td>Ball in my court</td><td>35</td><td>Never reviewed (35), author pushed since my review (30), already reviewed and waiting (0)</td></tr>
                  <tr><td>CI passing</td><td>20</td><td>Passing (20), pending (8), unknown (4), failing (0)</td></tr>
                  <tr><td>Small diff</td><td>15</td><td>Smaller PRs score higher, quick wins first</td></tr>
                  <tr><td>Age</td><td>15</td><td>Older PRs rise in priority (linear over 7 days)</td></tr>
                  <tr><td>Mergeable</td><td>10</td><td>Clean (10), unstable (5), conflicts (0)</td></tr>
                  <tr><td>Draft</td><td>-30</td><td>Penalty for draft PRs</td></tr>
                </tbody>
              </table>
              <p>PRs where you have never reviewed rank highest. Once you review and the author hasn&apos;t pushed new changes, the PR drops to 0 on that signal (ball is in their court).</p>
            </>
          ) : mode === 'owner' ? (
            <>
              <p>Each PR is scored 0-100 based on how urgently it needs your attention:</p>
              <table className={styles.guideTable}>
                <thead><tr><th>Signal</th><th>Max</th><th>Logic</th></tr></thead>
                <tbody>
                  <tr><td>Changes requested</td><td>30</td><td>Reviewer sent it back (30), no changes requested (0)</td></tr>
                  <tr><td>CI needs fix</td><td>25</td><td>Failing (25), action required (20), pending (5), passing (0)</td></tr>
                  <tr><td>Has conflicts</td><td>15</td><td>Conflicts (15), unstable (8), clean (0)</td></tr>
                  <tr><td>Ready to merge</td><td>15</td><td>Approved + CI + clean (15), approved + CI (10), approved (5)</td></tr>
                  <tr><td>Age</td><td>10</td><td>Older PRs rise in priority (linear over 7 days)</td></tr>
                  <tr><td>New feedback</td><td>5</td><td>Has review comments (5), none (0)</td></tr>
                  <tr><td>Draft</td><td>-20</td><td>Lighter penalty for draft PRs</td></tr>
                </tbody>
              </table>
              <p>PRs with failing CI or requested changes rank highest since those need your action. PRs that are ready to merge also score well so you don&apos;t forget to ship them.</p>
            </>
          ) : (
            <>
              <p>Each PR is scored 0-100 based on how ready it is to review and merge:</p>
              <table className={styles.guideTable}>
                <thead><tr><th>Signal</th><th>Max</th><th>Logic</th></tr></thead>
                <tbody>
                  <tr><td>Review readiness</td><td>35</td><td>Approved (35), reviewed (15), no review (15), changes requested (0)</td></tr>
                  <tr><td>CI status</td><td>25</td><td>Passing (25), pending (10), unknown (5), failing (0)</td></tr>
                  <tr><td>Mergeable</td><td>15</td><td>Clean (15), unstable (8), conflicts/blocked (0)</td></tr>
                  <tr><td>Diff size</td><td>10</td><td>Smaller PRs score higher</td></tr>
                  <tr><td>Age</td><td>10</td><td>Older PRs rise in priority (linear over 7 days)</td></tr>
                  <tr><td>Rebase check</td><td>5</td><td>+5 if rebased since last approval</td></tr>
                  <tr><td>Draft</td><td>-30</td><td>Penalty for draft PRs</td></tr>
                </tbody>
              </table>
            </>
          )}
          <p><strong>Manual priority</strong> overrides score-based ordering. High-priority PRs always appear at the top, low-priority at the bottom. Within each tier, PRs are sorted by automated score.</p>
          <p><strong>Stacked PRs</strong> are ordered parent-before-child. The stack&apos;s position in the queue is determined by the root PR&apos;s score.</p>
          <p>Hover over any score bar to see the full breakdown. Set priority from the PR detail panel.</p>
        </div>
      )}
    </div>
  );
}

export function PrioritizeView() {
  const { selectedPrNumber, selectPr, selectedRepoId, setSelectedRepoId } = useStore();
  const { user: currentUser } = useCurrentUser();
  const [hoveredId, setHoveredId] = useState<number | null>(null);
  const [filterRepoId, setFilterRepoId] = useState<number | undefined>(undefined);
  const [guideOpen, setGuideOpen] = useState(false);
  const [mode, setMode] = useState<PriorityMode>('review');

  // Dropdown open/close state
  const [repoDropdownOpen, setRepoDropdownOpen] = useState(false);

  // Dropdown refs
  const repoDropdownRef = useRef<HTMLDivElement>(null);

  // Click-outside handler for dropdowns
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (repoDropdownRef.current && !repoDropdownRef.current.contains(e.target as Node)) setRepoDropdownOpen(false);
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const { data: repos } = useQuery({
    queryKey: ['repos'],
    queryFn: () => api.listRepos(),
  });

  const { data: team } = useQuery({
    queryKey: ['team'],
    queryFn: api.listTeam,
  });

  // Build login → { displayName, avatar } from team data
  const authorInfoMap = useMemo(() => {
    const map = new Map<string, { displayName: string; avatar: string | null }>();
    for (const m of (team || []) as User[]) {
      const displayName = m.name || m.login;
      for (const acct of m.linked_accounts || []) {
        map.set(acct.login, { displayName, avatar: acct.avatar_url });
      }
      if (!map.has(m.login)) {
        map.set(m.login, { displayName, avatar: m.avatar_url });
      }
    }
    return map;
  }, [team]);

  // Build repo color map keyed by repo id
  const repoColorMap = useMemo(() => {
    const map = new Map<number, string>();
    for (const r of repos || []) {
      map.set(r.id, repoColor(r.full_name));
    }
    return map;
  }, [repos]);

  const { data: items, isLoading } = useQuery({
    queryKey: ['prioritized', filterRepoId, currentUser ? mode : 'default'],
    queryFn: () => api.listPrioritized(filterRepoId, currentUser ? mode : undefined),
    refetchInterval: 30_000,
  });

  // Determine active mode from response (handles unauth fallback)
  const activeMode: PriorityMode | null = items?.[0]?.mode === 'review' ? 'review'
    : items?.[0]?.mode === 'owner' ? 'owner'
    : null;

  const prs = items || [];

  const prById = useMemo(() => {
    const map = new Map<number, PrioritizedPR>();
    for (const p of prs) map.set(p.pr.id, p);
    return map;
  }, [prs]);

  if (isLoading) return <div className={styles.loading}>Loading prioritized PRs...</div>;

  // Mode-specific summary stats
  const summaryBar = (() => {
    if (activeMode === 'review') {
      const quickWins = prs.filter((p) => p.priority_breakdown.size >= 12 && p.priority_score >= 40).length;
      return (
        <div className={styles.summaryBar}>
          <Tooltip text="PRs where you're a requested reviewer or have unfinished reviews" position="bottom">
            <span className={styles.summaryItem}>
              <span className={styles.summaryCount}>{prs.length}</span> to review
            </span>
          </Tooltip>
          <Tooltip text="Small PRs (≤200 lines) with a score above 40, easy to knock out" position="bottom">
            <span className={`${styles.summaryItem} ${styles.summaryGreen}`}>
              <span className={styles.summaryCount}>{quickWins}</span> quick wins
            </span>
          </Tooltip>
        </div>
      );
    }
    if (activeMode === 'owner') {
      const readyToMerge = prs.filter((p) => p.priority_breakdown.size === 15).length;
      const needAction = prs.filter((p) => p.priority_breakdown.review === 30 || p.priority_breakdown.ci >= 20 || p.priority_breakdown.mergeable === 15).length;
      const unsubmittedComments = prs.filter((p) => p.pr.commenters_without_review?.length > 0).length;
      return (
        <div className={styles.summaryBar}>
          <Tooltip text="Your open PRs across tracked repos" position="bottom">
            <span className={styles.summaryItem}>
              <span className={styles.summaryCount}>{prs.length}</span> open
            </span>
          </Tooltip>
          <Tooltip text="Approved, CI passing, no conflicts" position="bottom">
            <span className={`${styles.summaryItem} ${styles.summaryGreen}`}>
              <span className={styles.summaryCount}>{readyToMerge}</span> ready to merge
            </span>
          </Tooltip>
          {needAction > 0 && (
            <Tooltip text="Changes requested, CI broken, or has merge conflicts" position="bottom">
              <span className={`${styles.summaryItem} ${styles.summaryRed}`}>
                <span className={styles.summaryCount}>{needAction}</span> need action
              </span>
            </Tooltip>
          )}
          {unsubmittedComments > 0 && (
            <Tooltip text="PRs where someone commented but didn't submit a formal review" position="bottom">
              <span className={`${styles.summaryItem} ${styles.summaryAmber}`}>
                <span className={styles.summaryCount}>{'\u26A0'} {unsubmittedComments}</span> unsubmitted reviews
              </span>
            </Tooltip>
          )}
        </div>
      );
    }
    // Default mode
    const readyCount = prs.filter((p) => p.priority_score >= 70 && !p.blocked_by_pr_id).length;
    const needsAttentionCount = prs.filter((p) => p.priority_score < 40).length;
    return (
      <div className={styles.summaryBar}>
        <Tooltip text="All open PRs across tracked repos" position="bottom">
          <span className={styles.summaryItem}>
            <span className={styles.summaryCount}>{prs.length}</span> open
          </span>
        </Tooltip>
        <Tooltip text="Score ≥70 and not blocked by a parent PR" position="bottom">
          <span className={`${styles.summaryItem} ${styles.summaryGreen}`}>
            <span className={styles.summaryCount}>{readyCount}</span> ready to merge
          </span>
        </Tooltip>
        <Tooltip text="Score below 40, likely blocked or failing CI" position="bottom">
          <span className={`${styles.summaryItem} ${styles.summaryRed}`}>
            <span className={styles.summaryCount}>{needsAttentionCount}</span> needs attention
          </span>
        </Tooltip>
      </div>
    );
  })();

  function handleSelectPr(item: PrioritizedPR) {
    setSelectedRepoId(item.repo_id);
    selectPr(item.pr.number);
  }

  const emptyMessage = activeMode === 'review'
    ? 'No PRs waiting for your review.'
    : activeMode === 'owner'
    ? "You don't have any open PRs."
    : 'No open PRs found across your tracked repos.';

  return (
    <div className={styles.container}>
      <div className={styles.content} style={selectedPrNumber ? { marginRight: 396 } : undefined}>
        <div className={styles.header}>
          <h2 className={styles.title}>Priority Queue</h2>
          {summaryBar}
        </div>

        <div className={styles.filters}>
          {/* Mode toggle - only when logged in */}
          {currentUser && (
            <div className={styles.modeToggle}>
              <button
                className={`${styles.modeButton} ${mode === 'review' ? styles.modeButtonActive : ''}`}
                onClick={() => setMode('review')}
              >
                Review Queue
              </button>
              <button
                className={`${styles.modeButton} ${mode === 'owner' ? styles.modeButtonActive : ''}`}
                onClick={() => setMode('owner')}
              >
                My PRs
              </button>
            </div>
          )}

          {/* Repo filter */}
          <Tooltip text="Filter by repository" position="bottom" disabled={repoDropdownOpen}>
            <div className={styles.filterDropdown} ref={repoDropdownRef}>
              <button
                className={`${styles.filterTrigger} ${styles.repoTrigger}`}
                onClick={() => setRepoDropdownOpen(!repoDropdownOpen)}
              >
                {filterRepoId ? (() => {
                  const r = (repos || []).find((r: RepoSummary) => r.id === filterRepoId);
                  const color = filterRepoId ? repoColorMap.get(filterRepoId) : undefined;
                  return <>
                    {color && <span className={styles.repoDot} style={{ backgroundColor: color }} />}
                    <span>{r?.name ?? 'All repos'}</span>
                  </>;
                })() : <span>All repos</span>}
                <span className={styles.filterChevron}>{repoDropdownOpen ? '\u25B4' : '\u25BE'}</span>
              </button>
              {repoDropdownOpen && (
                <div className={styles.filterMenu}>
                  <div
                    className={`${styles.filterMenuItem} ${filterRepoId === undefined ? styles.filterMenuItemActive : ''}`}
                    onClick={() => { setFilterRepoId(undefined); setRepoDropdownOpen(false); }}
                  >
                    <span>All repos</span>
                  </div>
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
                          className={`${styles.filterMenuItem} ${styles.filterMenuItemIndented} ${filterRepoId === r.id ? styles.filterMenuItemActive : ''}`}
                          onClick={() => { setFilterRepoId(r.id); setRepoDropdownOpen(false); }}
                        >
                          <span className={styles.repoDot} style={{ backgroundColor: repoColorMap.get(r.id) }} />
                          <span>{r.name}</span>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Tooltip>
        </div>

        <ScoringGuide open={guideOpen} onToggle={() => setGuideOpen(!guideOpen)} mode={activeMode} />

        {prs.length === 0 ? (
          <div className={styles.empty}>{emptyMessage}</div>
        ) : (
          <div
            className={`${styles.list} ${filterRepoId ? styles.listTinted : ''}`}
            style={filterRepoId ? { background: `${repoColorMap.get(filterRepoId)}0d` } : undefined}
          >
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
                className={`${styles.row} ${selectedPrNumber === item.pr.number ? styles.rowSelected : ''}`}
                onClick={() => handleSelectPr(item)}
              >
                <div className={styles.position}>#{item.merge_position}</div>

                <div className={styles.main}>
                  <div className={styles.prTitleRow}>
                    {!filterRepoId && (() => {
                      const color = repoColorMap.get(item.repo_id);
                      const name = item.repo_full_name.split('/').pop() || item.repo_full_name;
                      return (
                        <span
                          className={styles.repoBadge}
                          style={{ color, backgroundColor: color ? `${color}1a` : undefined }}
                        >
                          {name}
                        </span>
                      );
                    })()}
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
                    {(() => {
                      const info = authorInfoMap.get(item.pr.author);
                      return (
                        <span className={styles.authorWrap}>
                          {info?.avatar && <img src={info.avatar} alt={item.pr.author} className={styles.authorAvatar} />}
                          <span className={styles.author}>{info?.displayName || item.pr.author}</span>
                        </span>
                      );
                    })()}
                  </div>
                  <div className={styles.prMeta}>
                    <span className={styles.lines}>
                      <span className={styles.additions}>+{item.pr.additions}</span>
                      <span className={styles.deletions}>-{item.pr.deletions}</span>
                    </span>
                    {item.pr.manual_priority === 'high' && <span className={`${styles.badge} ${styles.badgeRed}`}>{'\u2191'} High</span>}
                    {item.pr.manual_priority === 'low' && <span className={`${styles.badge} ${styles.badgeDim}`}>{'\u2193'} Low</span>}
                    {item.stack_name && (
                      <span className={`${styles.badge} ${styles.badgeStack}`}>{item.stack_name}</span>
                    )}
                    {activeMode === 'review' && currentUser && item.pr.commenters_without_review?.includes(currentUser.login) && (
                      <Tooltip text="You commented on this PR but didn't submit a formal review" position="bottom">
                        <span className={`${styles.badge} ${styles.badgeAmber}`}>{'\u26A0'} Unsubmitted review</span>
                      </Tooltip>
                    )}
                    {activeMode === 'owner' && item.pr.commenters_without_review?.length > 0 && (
                      <Tooltip text={`${item.pr.commenters_without_review.join(', ')} commented but didn't submit a formal review`} position="bottom">
                        <span className={`${styles.badge} ${styles.badgeAmber}`}>{'\u26A0'} Unsubmitted review</span>
                      </Tooltip>
                    )}
                    {item.blocked_by_pr_id && (() => {
                      const blocker = prById.get(item.blocked_by_pr_id);
                      const tip = blocker
                        ? `Blocked by #${blocker.pr.number}: ${blocker.pr.title}`
                        : 'Blocked by another PR in this stack';
                      return (
                        <Tooltip text={tip} position="bottom">
                          <span className={`${styles.badge} ${styles.badgeAmber}`}>Blocked</span>
                        </Tooltip>
                      );
                    })()}
                    {getScoreSummary(item.priority_breakdown, item.pr.review_state, activeMode).map((p, i) => {
                      const cls = p.impact === 'positive' ? styles.badgeGreen
                        : p.impact === 'negative' ? styles.badgeRed
                        : styles.badgeDim;
                      return (
                        <Tooltip key={i} text={p.tip} position="bottom">
                          <span className={`${styles.badge} ${cls}`}>{p.text}</span>
                        </Tooltip>
                      );
                    })}
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
                    <BreakdownTooltip breakdown={item.priority_breakdown} score={item.priority_score} mode={activeMode} />
                  )}
                </div>
              </div>
              </Fragment>);
            })}
          </div>
        )}
      </div>

      {selectedPrNumber && selectedRepoId && (
        <PRDetailPanel
          repoId={selectedRepoId}
          prNumber={selectedPrNumber}
          onClose={() => { selectPr(null); setSelectedRepoId(null); }}
        />
      )}
    </div>
  );
}
