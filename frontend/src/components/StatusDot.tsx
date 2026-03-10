/** Small colored dot for CI/review status. */

import styles from './StatusDot.module.css';

interface Props {
  status: string;
  size?: number;
  title?: string;
}

const STATUS_COLORS: Record<string, string> = {
  // CI
  success: 'var(--ci-pass)',
  failure: 'var(--ci-fail)',
  pending: 'var(--ci-pending)',
  action_required: 'var(--ci-fail)',
  // Review — uses unified review tokens
  approved: 'var(--review-approved)',
  changes_requested: 'var(--review-changes)',
  mixed: 'var(--review-mixed)',
  reviewed: 'var(--review-commented)',
  unknown: 'var(--ci-neutral)',
  none: 'var(--ci-neutral)',
};

export function StatusDot({ status, size = 8, title }: Props) {
  const color = STATUS_COLORS[status] || STATUS_COLORS.unknown;
  return (
    <span
      className={styles.dot}
      style={{ width: size, height: size, background: color }}
      title={title || status.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
    />
  );
}
