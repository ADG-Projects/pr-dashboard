/** Dependency graph showing PRs as cards with SVG arrows.
 *
 * Layout: builds parent-child edges from head_ref/base_ref relationships,
 * then recursively assigns tree positions (depth = column, siblings stacked vertically).
 * Standalone PRs shown in a flexbox grid below.
 */

import { useMemo, useRef, useCallback, useState } from 'react';
import type { PRSummary, Stack } from '../api/client';
import { StatusDot } from './StatusDot';
import { Tooltip } from './Tooltip';
import { useStore } from '../store/useStore';
import styles from './DependencyGraph.module.css';

interface Props {
  prs: PRSummary[];
  stacks: Stack[];
  highlightStackId: number | null;
  dimReviewerLogin: string | Set<string> | null;
  dimAuthor: string | Set<string> | null;
  selectedPrNumber: number | null;
  onSelectPr: (prNumber: number | null) => void;
  onRenameStack?: (stackId: number, name: string) => void;
  nameMap?: Map<string, { avatar: string | null; displayName: string }>;
}

const CARD_W = 210;
const CARD_H = 140;
const GAP_X = 50;
const GAP_Y = 30;
const PAD = 20;

interface CardPos {
  x: number;
  y: number;
  pr: PRSummary;
}

interface Arrow {
  key: string;
  d: string;
  dimmed: boolean;
}

interface StackLabel {
  stackId: number;
  name: string;
  x: number;
  y: number;
}

const LABEL_H = 24;

/** Format status strings: "changes_requested" -> "Changes Requested" */
function formatStatus(s: string): string {
  return s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

const REVIEW_TOOLTIPS: Record<string, string> = {
  approved: 'All reviewers approved',
  changes_requested: 'Changes requested by reviewer(s)',
  mixed: 'Has both approvals and unresolved change requests',
  reviewed: 'Reviewed, but no approval or change request yet',
  none: 'No reviews yet',
};

/** Compute priority score (0-100) matching backend compute_priority_score logic. */
function standalonePriorityScore(pr: PRSummary): number {
  // Review readiness (max 35)
  const reviewScores: Record<string, number> = { approved: 35, reviewed: 15, none: 15, changes_requested: 0 };
  const reviewPts = reviewScores[pr.review_state] ?? 15;

  // CI status (max 25)
  const ciScores: Record<string, number> = { success: 25, pending: 10, unknown: 5, failure: 0 };
  const ciPts = ciScores[pr.ci_status] ?? 5;

  // Size inverse (max 10)
  const totalLines = pr.additions + pr.deletions;
  let sizePts: number;
  if (totalLines <= 50) sizePts = 10;
  else if (totalLines <= 200) sizePts = 8;
  else if (totalLines <= 500) sizePts = 5;
  else if (totalLines <= 1000) sizePts = 2;
  else sizePts = 0;

  // Mergeable state (max 15)
  const mergeScores: Record<string, number> = { clean: 15, unstable: 8 };
  const mergeablePts = mergeScores[pr.mergeable_state ?? ''] ?? 0;

  // Age linear 0→10 over 7 days (max 10)
  const ageDays = (Date.now() - new Date(pr.created_at).getTime()) / 86_400_000;
  const agePts = Math.min(10, Math.floor(ageDays * 10 / 7));

  // Rebase check (max 5)
  const rebasePts = pr.rebased_since_approval ? 5 : 0;

  // Draft penalty
  const draftPenalty = pr.draft ? -30 : 0;

  return Math.max(0, reviewPts + ciPts + sizePts + mergeablePts + agePts + rebasePts + draftPenalty);
}

export function DependencyGraph({ prs, stacks, highlightStackId, dimReviewerLogin, dimAuthor, selectedPrNumber, onSelectPr, onRenameStack, nameMap }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [editingStackId, setEditingStackId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState('');
  const collapsedStacks = useStore((s) => s.collapsedStacks);
  const toggleStackCollapsed = useStore((s) => s.toggleStackCollapsed);
  // Build highlighted PR set
  const highlightedPrIds = useMemo(() => {
    if (highlightStackId == null) return null;
    const stack = stacks.find((s) => s.id === highlightStackId);
    if (!stack) return null;
    return new Set(stack.members.map((m) => m.pr.id));
  }, [stacks, highlightStackId]);

  // Build graph edges from head_ref/base_ref
  const { layout, standalones, arrows, stackLabels, svgW, svgH } = useMemo(() => {
    // Map head_ref -> PR (a PR's head_ref is its branch name)
    const headRefToPr = new Map<string, PRSummary>();
    for (const pr of prs) {
      headRefToPr.set(pr.head_ref, pr);
    }

    // Build parent -> children map
    const children = new Map<number, PRSummary[]>();
    const parentOf = new Map<number, PRSummary>();

    for (const pr of prs) {
      const parent = headRefToPr.get(pr.base_ref);
      if (parent && parent.id !== pr.id) {
        parentOf.set(pr.id, parent);
        const siblings = children.get(parent.id) || [];
        siblings.push(pr);
        children.set(parent.id, siblings);
      }
    }

    // Find roots and standalones
    const roots: PRSummary[] = [];
    const standalone: PRSummary[] = [];

    for (const pr of prs) {
      const hasChildren = children.has(pr.id);
      const hasParent = parentOf.has(pr.id);
      if (!hasParent && hasChildren) {
        roots.push(pr);
      } else if (!hasParent && !hasChildren) {
        standalone.push(pr);
      }
    }

    standalone.sort((a, b) => {
      // Manual priority tiers: high=0, normal=1, low=2
      const tierOrder = (p: PRSummary) =>
        p.manual_priority === 'high' ? 0 : p.manual_priority === 'low' ? 2 : 1;
      const tierDiff = tierOrder(a) - tierOrder(b);
      if (tierDiff !== 0) return tierDiff;
      const scoreDiff = standalonePriorityScore(b) - standalonePriorityScore(a);
      if (scoreDiff !== 0) return scoreDiff;
      return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
    });

    // Build root PR id -> Stack lookup
    const rootToStack = new Map<number, Stack>();
    for (const stack of stacks) {
      if (stack.root_pr_id != null) {
        rootToStack.set(stack.root_pr_id, stack);
      }
    }

    // Tree layout: column = depth, row = vertical position.
    // Siblings stack vertically; parent centered among children.
    const positions: CardPos[] = [];
    const labels: StackLabel[] = [];
    const prToStackId = new Map<number, number>();

    // Track cumulative Y offset for labels (accounts for collapsed stacks taking less space)
    let labelY = PAD;

    for (const root of roots) {
      // Match root to its stack for the label
      const stack = rootToStack.get(root.id);
      if (stack) {
        // Map all PRs in this stack to the stack id
        for (const member of stack.members) {
          prToStackId.set(member.pr.id, stack.id);
        }
      }

      const treeTopY = labelY;

      if (stack) {
        labels.push({
          stackId: stack.id,
          name: stack.name || `#${stack.id}`,
          x: PAD,
          y: treeTopY,
        });
      }

      // If this stack is collapsed, only reserve space for the label row
      const isCollapsed = stack && collapsedStacks.has(stack.id);
      if (isCollapsed) {
        labelY += LABEL_H + 8; // just the label + small gap
        continue;
      }

      // Label offset pushes cards down within this tree
      const labelOffset = stack ? LABEL_H : 0;
      const baseY = treeTopY + labelOffset;

      // Recursive function: returns the row span [startRow, endRow] used
      // Rows are relative (starting from 0 for this tree)
      function layoutNode(pr: PRSummary, depth: number, startRow: number): number {
        const kids = children.get(pr.id) || [];
        if (kids.length === 0) {
          // Leaf node
          positions.push({
            x: PAD + depth * (CARD_W + GAP_X),
            y: baseY + startRow * (CARD_H + GAP_Y),
            pr,
          });
          return startRow; // occupied one row
        }

        // Layout children first to determine vertical span
        let nextRow = startRow;
        const childRows: number[] = [];
        for (const child of kids) {
          const endRow = layoutNode(child, depth + 1, nextRow);
          childRows.push(nextRow + (endRow - nextRow) / 2); // center of each child's span
          nextRow = endRow + 1;
        }

        // Center parent among its children
        const parentRow = (childRows[0] + childRows[childRows.length - 1]) / 2;
        positions.push({
          x: PAD + depth * (CARD_W + GAP_X),
          y: baseY + parentRow * (CARD_H + GAP_Y),
          pr,
        });

        return nextRow - 1; // last row used
      }

      const lastRow = layoutNode(root, 0, 0);
      const treeRows = lastRow + 1;
      labelY = baseY + treeRows * (CARD_H + GAP_Y);
    }

    // Position map for arrow computation
    const posMap = new Map<number, CardPos>();
    positions.forEach((pos) => posMap.set(pos.pr.id, pos));

    // Compute arrows
    const arrowList: Arrow[] = [];
    for (const pos of positions) {
      const parent = parentOf.get(pos.pr.id);
      if (!parent) continue;
      const parentPos = posMap.get(parent.id);
      if (!parentPos) continue;

      const fromX = parentPos.x + CARD_W;
      const fromY = parentPos.y + CARD_H / 2;
      const toX = pos.x;
      const toY = pos.y + CARD_H / 2;

      let d: string;

      // Check if this is a wrap (child is on a new row, at col 0)
      const isWrap = pos.y > parentPos.y && pos.x <= parentPos.x;

      if (isWrap) {
        // Wrap arrow: go right from parent, down, then left to child
        const exitX = parentPos.x + CARD_W + GAP_X / 3;
        const entryX = pos.x - GAP_X / 3;
        const midY = parentPos.y + CARD_H + GAP_Y / 2;
        d = `M ${fromX} ${fromY} L ${exitX} ${fromY} L ${exitX} ${midY} L ${entryX} ${midY} L ${entryX} ${toY} L ${toX} ${toY}`;
      } else if (Math.abs(fromY - toY) < 5) {
        // Same row: simple bezier
        const cx = (fromX + toX) / 2;
        d = `M ${fromX} ${fromY} C ${cx} ${fromY}, ${cx} ${toY}, ${toX} ${toY}`;
      } else {
        // Cross row (branching): L-shaped path
        const midX = fromX + GAP_X / 2;
        d = `M ${fromX} ${fromY} L ${midX} ${fromY} L ${midX} ${toY} L ${toX} ${toY}`;
      }

      const dimmed = highlightedPrIds != null &&
        (!highlightedPrIds.has(pos.pr.id) || !highlightedPrIds.has(parent.id));

      arrowList.push({ key: `${parent.id}-${pos.pr.id}`, d, dimmed });
    }

    // Container dimensions (include labels for collapsed stacks)
    let maxX = 0;
    let maxY = 0;
    for (const pos of positions) {
      maxX = Math.max(maxX, pos.x + CARD_W);
      maxY = Math.max(maxY, pos.y + CARD_H);
    }
    for (const label of labels) {
      maxX = Math.max(maxX, label.x + 300);
      maxY = Math.max(maxY, label.y);
    }

    return {
      layout: positions,
      standalones: standalone,
      arrows: arrowList,
      stackLabels: labels,
      svgW: maxX + PAD,
      svgH: maxY + PAD,
    };
  }, [prs, stacks, highlightedPrIds, collapsedStacks]);

  const isDimmed = useCallback((pr: PRSummary) => {
    if (highlightedPrIds != null && !highlightedPrIds.has(pr.id)) return true;
    if (dimReviewerLogin != null) {
      const reviewers = pr.all_reviewers ?? pr.github_requested_reviewers;
      const hasReviewer = dimReviewerLogin instanceof Set
        ? reviewers?.some((r) => dimReviewerLogin.has(r.login))
        : reviewers?.some((r) => r.login === dimReviewerLogin);
      if (!hasReviewer) return true;
    }
    if (dimAuthor != null) {
      const matches = dimAuthor instanceof Set ? dimAuthor.has(pr.author) : pr.author === dimAuthor;
      if (!matches) return true;
    }
    return false;
  }, [highlightedPrIds, dimReviewerLogin, dimAuthor]);

  function reviewBorderClass(pr: PRSummary): string {
    if (pr.merged_at) return styles.borderMerged;
    if (pr.review_state === 'approved' && !pr.rebased_since_approval) return styles.borderApproved;
    if (pr.review_state === 'approved' && pr.rebased_since_approval) return styles.borderRebased;
    if (pr.review_state === 'changes_requested') return styles.borderChanges;
    if (pr.review_state === 'mixed') return styles.borderMixed;
    if (pr.review_state === 'reviewed') return styles.borderReviewed;
    return '';
  }

  function renderCard(pr: PRSummary) {
    return (
      <>
        <div className={styles.cardHeader}>
          <a
            href={pr.html_url}
            target="_blank"
            rel="noopener noreferrer"
            className={styles.prNumber}
            onClick={(e) => e.stopPropagation()}
          >
            #{pr.number}
          </a>
          {pr.author && (
            <>
              {(() => {
                const avatar = nameMap?.get(pr.author)?.avatar;
                return avatar ? (
                  <img src={avatar} alt={pr.author} className={styles.cardAuthorAvatar} />
                ) : null;
              })()}
              <span className={styles.cardAuthor}>{nameMap?.get(pr.author)?.displayName || pr.author}</span>
            </>
          )}
          {pr.manual_priority === 'high' && <Tooltip text="High priority" position="top"><span className={styles.priorityHighBadge}>{'\u2191'}</span></Tooltip>}
          {pr.manual_priority === 'low' && <Tooltip text="Low priority" position="top"><span className={styles.priorityLowBadge}>{'\u2193'}</span></Tooltip>}
          {pr.draft && <Tooltip text="Draft PR — not ready for merge" position="top"><span className={styles.draftBadge}>Draft</span></Tooltip>}
          {pr.merged_at && <Tooltip text={`Merged ${new Date(pr.merged_at).toLocaleDateString()}`} position="top"><span className={styles.mergedBadge}>Merged</span></Tooltip>}
        </div>
        <div className={styles.cardTitle}>{pr.title}</div>
        <div className={styles.cardReviewers}>
          {pr.all_reviewers && pr.all_reviewers.length > 0 ? (
            <Tooltip
              text={pr.all_reviewers.map((r) => nameMap?.get(r.login)?.displayName || r.login).join(', ')}
              position="top"
            >
              <div className={styles.reviewerAvatarStack}>
                {pr.all_reviewers.slice(0, 3).map((r) => {
                  const avatar = r.avatar_url || nameMap?.get(r.login)?.avatar || null;
                  return avatar ? (
                    <img key={r.login} src={avatar} alt={r.login} className={styles.reviewerAvatar} />
                  ) : (
                    <span key={r.login} className={styles.reviewerAvatarInitial}>
                      {(nameMap?.get(r.login)?.displayName || r.login).charAt(0).toUpperCase()}
                    </span>
                  );
                })}
                {pr.all_reviewers.length > 3 && (
                  <span className={styles.reviewerOverflow}>+{pr.all_reviewers.length - 3}</span>
                )}
              </div>
            </Tooltip>
          ) : (
            <span className={styles.noReviewers}>No reviewers</span>
          )}
        </div>
        <div className={styles.cardFooter}>
          <Tooltip text={`CI: ${formatStatus(pr.ci_status)}`} position="top">
            <StatusDot status={pr.ci_status} size={7} />
          </Tooltip>
          <Tooltip text={REVIEW_TOOLTIPS[pr.review_state] || `Review: ${formatStatus(pr.review_state)}`} position="top">
            <StatusDot status={pr.review_state} size={7} />
          </Tooltip>
          {pr.rebased_since_approval && (
            <Tooltip text="Rebased since approval — re-review may be needed" position="top">
              <span className={styles.badgeWarn}>!</span>
            </Tooltip>
          )}
          <Tooltip text={`+${pr.additions} added, -${pr.deletions} removed`} position="top">
            <span className={styles.cardDiff}>
              <span className={styles.add}>+{pr.additions}</span>
              <span className={styles.del}>-{pr.deletions}</span>
            </span>
          </Tooltip>
        </div>
      </>
    );
  }

  return (
    <div className={styles.graphArea} ref={containerRef}>
      {(layout.length > 0 || stackLabels.length > 0) && (
        <div className={styles.graphContainer} style={{ width: svgW, height: svgH }}>
          <svg className={styles.svg} width={svgW} height={svgH}>
            <defs>
              <marker id="dep-arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                <polygon points="0 0, 8 3, 0 6" fill="var(--border-hover)" />
              </marker>
            </defs>
            {arrows.map((a) => (
              <path
                key={a.key}
                d={a.d}
                className={`${styles.arrowPath} ${a.dimmed ? styles.arrowDimmed : ''}`}
                markerEnd="url(#dep-arrowhead)"
              />
            ))}
          </svg>

          {stackLabels.map((label) => (
            <div
              key={`stack-label-${label.stackId}`}
              className={styles.stackLabel}
              style={{ left: label.x, top: label.y - LABEL_H }}
            >
              <span
                className={`${styles.collapseToggle} ${collapsedStacks.has(label.stackId) ? styles.collapseToggleCollapsed : ''}`}
                onClick={(e) => { e.stopPropagation(); toggleStackCollapsed(label.stackId); }}
              >
                {'\u25BC'}
              </span>
              {editingStackId === label.stackId ? (
                <input
                  className={styles.stackLabelInput}
                  value={editValue}
                  size={Math.max(editValue.length + 1, 4)}
                  onChange={(e) => setEditValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && editValue.trim()) {
                      onRenameStack?.(label.stackId, editValue.trim());
                      setEditingStackId(null);
                    } else if (e.key === 'Escape') {
                      setEditingStackId(null);
                    }
                  }}
                  onBlur={() => {
                    if (editValue.trim()) {
                      onRenameStack?.(label.stackId, editValue.trim());
                    }
                    setEditingStackId(null);
                  }}
                  autoFocus
                />
              ) : (
                <Tooltip text="Click to rename this stack" position="top">
                  <span
                    className={styles.stackLabelText}
                    onClick={() => {
                      if (onRenameStack) {
                        setEditValue(label.name);
                        setEditingStackId(label.stackId);
                      }
                    }}
                  >
                    {label.name}
                    <svg className={styles.stackLabelEditIcon} viewBox="0 0 16 16" fill="currentColor">
                      <path d="M11.013 1.427a1.75 1.75 0 012.474 0l1.086 1.086a1.75 1.75 0 010 2.474l-8.61 8.61c-.21.21-.47.364-.756.445l-3.251.93a.75.75 0 01-.927-.928l.929-3.25a1.75 1.75 0 01.445-.758l8.61-8.61zm1.414 1.06a.25.25 0 00-.354 0L3.463 11.098a.25.25 0 00-.064.108l-.631 2.208 2.208-.63a.25.25 0 00.108-.064l8.61-8.61a.25.25 0 000-.355l-1.086-1.086z"/>
                    </svg>
                  </span>
                </Tooltip>
              )}
            </div>
          ))}

          {layout.map((pos) => {
            const isSelected = selectedPrNumber === pos.pr.number;
            const dimmed = isDimmed(pos.pr);
            return (
              <div
                key={pos.pr.id}
                className={`${styles.card} ${isSelected ? styles.cardSelected : ''} ${dimmed ? styles.cardDimmed : ''} ${reviewBorderClass(pos.pr)}`}
                style={{ left: pos.x, top: pos.y, width: CARD_W, height: CARD_H }}
                onClick={() => onSelectPr(isSelected ? null : pos.pr.number)}
              >
                {renderCard(pos.pr)}
              </div>
            );
          })}
        </div>
      )}

      {standalones.length > 0 && (
        <div className={styles.standaloneSection}>
          <Tooltip text="PRs without parent/child dependencies" position="right">
            <div className={styles.standaloneLabel}>Standalone PRs</div>
          </Tooltip>
          <div className={styles.standaloneGrid}>
            {standalones.map((pr) => {
              const isSelected = selectedPrNumber === pr.number;
              const dimmed = isDimmed(pr);
              return (
                <div
                  key={pr.id}
                  className={`${styles.standaloneCard} ${isSelected ? styles.cardSelected : ''} ${dimmed ? styles.cardDimmed : ''} ${reviewBorderClass(pr)}`}
                  onClick={() => onSelectPr(isSelected ? null : pr.number)}
                >
                  {renderCard(pr)}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
