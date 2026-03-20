/** Slide-out panel showing auth health details and remediation actions. */

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type AuthHealthAccount, type AuthHealthRepo } from '../api/client';
import styles from './AuthHealthPanel.module.css';

const CRITICAL_STATUSES = new Set(['expired', 'revoked', 'decrypt_failed']);

const STATUS_LABELS: Record<string, string> = {
  expired: 'Token expired',
  revoked: 'Token revoked',
  decrypt_failed: 'Decryption failed',
  insufficient_scope: 'Insufficient permissions',
  sso_required: 'SSO required',
  repo_not_accessible: 'Repo not accessible',
};

function timeAgo(iso: string | null): string {
  if (!iso) return 'never';
  const diff = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

interface Props {
  onClose: () => void;
}

export function AuthHealthPanel({ onClose }: Props) {
  const qc = useQueryClient();
  const [checkError, setCheckError] = useState<string | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['auth-health'],
    queryFn: api.authHealth,
    staleTime: 30_000,
  });

  const checkMutation = useMutation({
    mutationFn: api.authHealthCheck,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['auth-health'] });
    },
    onError: (error: Error) => {
      setCheckError(error.message);
    },
  });

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.panel} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2>Account Health</h2>
          <button onClick={onClose} className={styles.closeBtn}>x</button>
        </div>

        <div className={styles.body}>
          {isLoading && <div className={styles.empty}>Loading...</div>}

          {data && !data.has_issues && (
            <div className={styles.allGood}>
              All accounts are healthy. No issues detected.
            </div>
          )}

          {data?.accounts.map((account) => (
            <AccountCard key={account.id} account={account} />
          ))}

          {data && data.stale_repos.length > 0 && (
            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>Repos with sync errors</h3>
              {data.stale_repos.map((repo) => (
                <RepoCard key={repo.id} repo={repo} />
              ))}
            </div>
          )}
        </div>

        <div className={styles.footer}>
          {checkError && (
            <span className={styles.checkError}>{checkError}</span>
          )}
          <button
            className={styles.checkBtn}
            onClick={() => { setCheckError(null); checkMutation.mutate(); }}
            disabled={checkMutation.isPending}
          >
            {checkMutation.isPending ? 'Checking...' : 'Check all now'}
          </button>
        </div>
      </div>
    </div>
  );
}

function AccountCard({ account }: { account: AuthHealthAccount }) {
  const isCritical = CRITICAL_STATUSES.has(account.token_status);

  return (
    <div className={`${styles.card} ${isCritical ? styles.cardCritical : styles.cardWarning}`}>
      <div className={styles.cardHeader}>
        <span className={styles.login}>@{account.login}</span>
        <span className={`${styles.badge} ${isCritical ? styles.badgeCritical : styles.badgeWarning}`}>
          {STATUS_LABELS[account.token_status] || account.token_status}
        </span>
      </div>

      {account.token_error && (
        <div className={styles.error}>{account.token_error}</div>
      )}

      {account.token_checked_at && (
        <div className={styles.meta}>Last checked: {timeAgo(account.token_checked_at)}</div>
      )}

      {account.affected_repos.length > 0 && (
        <div className={styles.repoList}>
          <span className={styles.repoListLabel}>Affected repos:</span>
          {account.affected_repos.map((r) => (
            <span key={r} className={styles.repoChip}>{r}</span>
          ))}
        </div>
      )}

      <div className={styles.remediation}>
        {account.remediation.url ? (
          <a href={account.remediation.url} className={styles.remediationBtn}>
            {account.remediation.label}
          </a>
        ) : (
          <span className={styles.remediationText}>{account.remediation.description}</span>
        )}
      </div>
    </div>
  );
}

function RepoCard({ repo }: { repo: AuthHealthRepo }) {
  return (
    <div className={styles.repoCard}>
      <div className={styles.repoName}>{repo.full_name}</div>
      {repo.last_sync_error && (
        <div className={styles.error}>{repo.last_sync_error}</div>
      )}
      <div className={styles.meta}>
        {repo.last_successful_sync_at
          ? `Last synced: ${timeAgo(repo.last_successful_sync_at)}`
          : 'Never synced successfully'}
        {repo.last_sync_error_at && ` | Error: ${timeAgo(repo.last_sync_error_at)}`}
      </div>
    </div>
  );
}
