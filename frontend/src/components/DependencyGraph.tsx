/** Dependency graph showing PRs as cards with SVG arrows.
 *
 * Layout: builds parent-child edges from head_ref/base_ref relationships,
 * then recursively assigns tree positions (depth = column, siblings stacked vertically).
 * Standalone PRs shown in a flexbox grid below.
 */

import { useMemo, useRef, useCallback } from 'react';
import type { PRSummary, Stack } from '../api/client';
import { StatusDot } from './StatusDot';
import { Tooltip } from './Tooltip';
import styles from './DependencyGraph.module.css';

interface Props {
  prs: PRSummary[];
  stacks: Stack[];
  highlightStackId: number | null;
  dimReviewerLogin: string | null;
  dimAuthor: string | null;
  selectedPrId: number | null;
  onSelectPr: (id: number | null) => void;
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

export function DependencyGraph({ prs, stacks, highlightStackId, dimReviewerLogin, dimAuthor, selectedPrId, onSelectPr, nameMap }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  // Build highlighted PR set
  const highlightedPrIds = useMemo(() => {
    if (highlightStackId == null) return null;
    const stack = stacks.find((s) => s.id === highlightStackId);
    if (!stack) return null;
    return new Set(stack.members.map((m) => m.pr.id));
  }, [stacks, highlightStackId]);

  // Build graph edges from head_ref/base_ref
  const { layout, standalones, arrows, svgW, svgH } = useMemo(() => {
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

    // Tree layout: column = depth, row = vertical position.
    // Siblings stack vertically; parent centered among children.
    const positions: CardPos[] = [];
    let globalRow = 0; // next available row across all trees

    for (const root of roots) {
      // Recursive function: returns the row span [startRow, endRow] used
      function layoutNode(pr: PRSummary, depth: number, startRow: number): number {
        const kids = children.get(pr.id) || [];
        if (kids.length === 0) {
          // Leaf node
          positions.push({
            x: PAD + depth * (CARD_W + GAP_X),
            y: PAD + startRow * (CARD_H + GAP_Y),
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
          y: PAD + parentRow * (CARD_H + GAP_Y),
          pr,
        });

        return nextRow - 1; // last row used
      }

      const lastRow = layoutNode(root, 0, globalRow);
      globalRow = lastRow + 1;
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

    // SVG dimensions
    let maxX = 0;
    let maxY = 0;
    for (const pos of positions) {
      maxX = Math.max(maxX, pos.x + CARD_W);
      maxY = Math.max(maxY, pos.y + CARD_H);
    }

    return {
      layout: positions,
      standalones: standalone,
      arrows: arrowList,
      svgW: maxX + PAD,
      svgH: maxY + PAD,
    };
  }, [prs, highlightedPrIds]);

  const isDimmed = useCallback((pr: PRSummary) => {
    if (highlightedPrIds != null && !highlightedPrIds.has(pr.id)) return true;
    if (dimReviewerLogin != null) {
      const hasReviewer = pr.github_requested_reviewers?.some((r) => r.login === dimReviewerLogin);
      if (!hasReviewer) return true;
    }
    if (dimAuthor != null && pr.author !== dimAuthor) return true;
    return false;
  }, [highlightedPrIds, dimReviewerLogin, dimAuthor]);

  function reviewBorderClass(pr: PRSummary): string {
    if (pr.merged_at) return styles.borderMerged;
    if (pr.review_state === 'approved' && !pr.rebased_since_approval) return styles.borderApproved;
    if (pr.review_state === 'approved' && pr.rebased_since_approval) return styles.borderRebased;
    if (pr.review_state === 'changes_requested') return styles.borderChanges;
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
          {pr.author && <span className={styles.cardAuthor}>{nameMap?.get(pr.author)?.displayName || pr.author}</span>}
          {pr.manual_priority === 'high' && <Tooltip text="High priority" position="top"><span className={styles.priorityHighBadge}>{'\u2191'}</span></Tooltip>}
          {pr.manual_priority === 'low' && <Tooltip text="Low priority" position="top"><span className={styles.priorityLowBadge}>{'\u2193'}</span></Tooltip>}
          {pr.draft && <Tooltip text="Draft PR — not ready for merge" position="top"><span className={styles.draftBadge}>Draft</span></Tooltip>}
          {pr.merged_at && <Tooltip text={`Merged ${new Date(pr.merged_at).toLocaleDateString()}`} position="top"><span className={styles.mergedBadge}>Merged</span></Tooltip>}
        </div>
        <div className={styles.cardTitle}>{pr.title}</div>
        <div className={styles.cardReviewers}>
          {pr.github_requested_reviewers && pr.github_requested_reviewers.length > 0 ? (
            <Tooltip
              text={pr.github_requested_reviewers.map((r) => nameMap?.get(r.login)?.displayName || r.login).join(', ')}
              position="top"
            >
              <div className={styles.reviewerAvatarStack}>
                {pr.github_requested_reviewers.slice(0, 3).map((r) =>
                  r.avatar_url ? (
                    <img key={r.login} src={r.avatar_url} alt={r.login} className={styles.reviewerAvatar} />
                  ) : (
                    <span key={r.login} className={styles.reviewerAvatarInitial}>
                      {(nameMap?.get(r.login)?.displayName || r.login).charAt(0).toUpperCase()}
                    </span>
                  )
                )}
                {pr.github_requested_reviewers.length > 3 && (
                  <span className={styles.reviewerOverflow}>+{pr.github_requested_reviewers.length - 3}</span>
                )}
              </div>
            </Tooltip>
          ) : (
            <span className={styles.noReviewers}>No reviewers</span>
          )}
        </div>
        <div className={styles.cardFooter}>
          <Tooltip text={`CI: ${pr.ci_status}`} position="top">
            <StatusDot status={pr.ci_status} size={7} />
          </Tooltip>
          <Tooltip text={`Review: ${pr.review_state}`} position="top">
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
      {layout.length > 0 && (
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

          {layout.map((pos) => {
            const isSelected = selectedPrId === pos.pr.id;
            const dimmed = isDimmed(pos.pr);
            return (
              <div
                key={pos.pr.id}
                className={`${styles.card} ${isSelected ? styles.cardSelected : ''} ${dimmed ? styles.cardDimmed : ''} ${reviewBorderClass(pos.pr)}`}
                style={{ left: pos.x, top: pos.y, width: CARD_W, height: CARD_H }}
                onClick={() => onSelectPr(isSelected ? null : pos.pr.id)}
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
              const isSelected = selectedPrId === pr.id;
              const dimmed = isDimmed(pr);
              return (
                <div
                  key={pr.id}
                  className={`${styles.standaloneCard} ${isSelected ? styles.cardSelected : ''} ${dimmed ? styles.cardDimmed : ''} ${reviewBorderClass(pr)}`}
                  onClick={() => onSelectPr(isSelected ? null : pr.id)}
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
