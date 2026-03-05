/** Dependency graph showing PRs as cards with SVG arrows.
 *
 * Layout: builds parent-child edges from head_ref/base_ref relationships,
 * then BFS from roots to assign positions. Chains wrap to new rows when
 * they'd exceed the container width (snake layout).
 * Standalone PRs shown in a flexbox grid below.
 */

import { useMemo, useRef, useState, useEffect, useCallback } from 'react';
import type { PRSummary, Stack } from '../api/client';
import { StatusDot } from './StatusDot';
import styles from './DependencyGraph.module.css';

interface Props {
  prs: PRSummary[];
  stacks: Stack[];
  highlightStackId: number | null;
  selectedPrId: number | null;
  onSelectPr: (id: number | null) => void;
}

const CARD_W = 210;
const CARD_H = 85;
const GAP_X = 50;
const GAP_Y = 30;
const PAD = 20;
const DEFAULT_MAX_COLS = 4;

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

export function DependencyGraph({ prs, stacks, highlightStackId, selectedPrId, onSelectPr }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(0);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setContainerWidth(entry.contentRect.width);
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const maxCols = useMemo(() => {
    if (containerWidth <= 0) return DEFAULT_MAX_COLS;
    return Math.max(2, Math.floor((containerWidth - PAD * 2) / (CARD_W + GAP_X)));
  }, [containerWidth]);

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

    // BFS layout with wrapping: each chain gets its own row band.
    // Within a chain, columns wrap at maxCols.
    const positions: CardPos[] = [];
    let globalRow = 0; // tracks the next available row across all chains

    for (const root of roots) {
      // Flatten this chain via BFS to get ordered nodes
      const chainNodes: PRSummary[] = [];
      const queue: PRSummary[] = [root];
      const visited = new Set<number>();

      while (queue.length > 0) {
        const pr = queue.shift()!;
        if (visited.has(pr.id)) continue;
        visited.add(pr.id);
        chainNodes.push(pr);
        const kids = children.get(pr.id) || [];
        for (const child of kids) {
          if (!visited.has(child.id)) queue.push(child);
        }
      }

      // Place chain nodes with wrapping
      const chainStartRow = globalRow;
      let col = 0;
      let row = chainStartRow;

      for (const pr of chainNodes) {
        if (col >= maxCols) {
          col = 0;
          row += 1;
        }
        positions.push({
          x: PAD + col * (CARD_W + GAP_X),
          y: PAD + row * (CARD_H + GAP_Y),
          pr,
        });
        col += 1;
      }

      globalRow = row + 1; // next chain starts on a new row
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
  }, [prs, highlightedPrIds, maxCols]);

  const isDimmed = useCallback((prId: number) =>
    highlightedPrIds != null && !highlightedPrIds.has(prId), [highlightedPrIds]);

  function reviewBorderClass(pr: PRSummary): string {
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
          {pr.draft && <span className={styles.draftBadge}>Draft</span>}
        </div>
        <div className={styles.cardTitle}>{pr.title}</div>
        <div className={styles.cardFooter}>
          <StatusDot status={pr.ci_status} title={`CI: ${pr.ci_status}`} size={7} />
          <StatusDot status={pr.review_state} title={`Review: ${pr.review_state}`} size={7} />
          {pr.rebased_since_approval && (
            <span className={styles.badgeWarn} title="Rebased since approval">!</span>
          )}
          <span className={styles.cardDiff}>
            <span className={styles.add}>+{pr.additions}</span>
            <span className={styles.del}>-{pr.deletions}</span>
          </span>
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
            const dimmed = isDimmed(pos.pr.id);
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
          <div className={styles.standaloneLabel}>Standalone PRs</div>
          <div className={styles.standaloneGrid}>
            {standalones.map((pr) => {
              const isSelected = selectedPrId === pr.id;
              const dimmed = isDimmed(pr.id);
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
