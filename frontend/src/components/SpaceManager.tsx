/** Modal for managing linked GitHub accounts and discovered spaces. */

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type GitHubAccountInfo, type Space } from '../api/client';
import { useCurrentUser } from '../App';
import { GitHubIcon } from './GitHubIcon';
import styles from './SpaceManager.module.css';

interface Props {
  onClose: () => void;
}

export function SpaceManager({ onClose }: Props) {
  const qc = useQueryClient();
  const { user, oauthConfigured } = useCurrentUser();
  const [showTokenForm, setShowTokenForm] = useState(false);

  const { data: accounts } = useQuery({
    queryKey: ['accounts'],
    queryFn: api.listAccounts,
    enabled: !!user,
  });

  const { data: spaces } = useQuery({
    queryKey: ['spaces'],
    queryFn: api.listSpaces,
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, isActive }: { id: number; isActive: boolean }) =>
      api.toggleSpace(id, isActive),
    onSuccess: (_data, { id, isActive }) => {
      qc.invalidateQueries({ queryKey: ['spaces'] });
      qc.invalidateQueries({ queryKey: ['repos'] });
      if (isActive) {
        qc.prefetchQuery({
          queryKey: ['available-repos', id],
          queryFn: () => api.listSpaceAvailableRepos(id),
          staleTime: 5 * 60 * 1000,
        });
      }
    },
  });

  const discoverMutation = useMutation({
    mutationFn: (accountId: number) => api.discoverSpaces(accountId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['spaces'] }),
  });

  const removeAccountMutation = useMutation({
    mutationFn: (accountId: number) => api.removeAccount(accountId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['accounts'] });
      qc.invalidateQueries({ queryKey: ['spaces'] });
    },
  });

  // Group spaces by account
  const spacesByAccount = new Map<number, Space[]>();
  const orphanSpaces: Space[] = [];
  for (const space of spaces ?? []) {
    if (space.github_account_id) {
      const list = spacesByAccount.get(space.github_account_id) ?? [];
      list.push(space);
      spacesByAccount.set(space.github_account_id, list);
    } else {
      orphanSpaces.push(space);
    }
  }
  // Sort spaces alphabetically within each account for stable ordering
  const sortSpaces = (a: Space, b: Space) => a.name.localeCompare(b.name);
  for (const list of spacesByAccount.values()) list.sort(sortSpaces);
  orphanSpaces.sort(sortSpaces);


  function handleSignIn() {
    window.location.href = '/api/auth/github';
  }

  function handleLinkOAuth() {
    // link=true tells the backend to attach this GitHub account
    // to the current user instead of signing in as a new user
    window.location.href = '/api/auth/github?link=true';
  }

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2>GitHub Accounts</h2>
          <button onClick={onClose} className={styles.closeBtn}>x</button>
        </div>
        <div className={styles.body}>
          <p className={styles.hint}>
            Each GitHub account you link is scanned for organizations and personal repos.
            Mark a space as <strong>Shown</strong> to track its repositories on the dashboard.
          </p>

          {accounts?.map((account) => (
            <AccountSection
              key={account.id}
              account={account}
              spaces={spacesByAccount.get(account.id) ?? []}
              onToggleSpace={(id, active) => toggleMutation.mutate({ id, isActive: active })}
              onDiscover={() => discoverMutation.mutate(account.id)}
              onRemove={() => {
                if (window.confirm(`Unlink ${account.login}? All its spaces will be permanently removed. You can rediscover them by linking the account again.`)) {
                  removeAccountMutation.mutate(account.id);
                }
              }}
              isDiscovering={discoverMutation.isPending}
            />
          ))}

          {orphanSpaces.length > 0 && (
            <div className={styles.accountSection}>
              <div className={styles.accountHeader}>
                <span className={styles.accountLogin}>Legacy spaces</span>
              </div>
              {orphanSpaces.map((space) => (
                <SpaceRow
                  key={space.id}
                  space={space}
                  onToggle={(active) => toggleMutation.mutate({ id: space.id, isActive: active })}
                />
              ))}
            </div>
          )}

          {(!accounts || accounts.length === 0) && !user && (
            <div className={styles.empty}>
              Sign in with GitHub to get started.
            </div>
          )}

          {showTokenForm ? (
            <TokenLinkForm
              onLinked={() => {
                setShowTokenForm(false);
                qc.invalidateQueries({ queryKey: ['accounts'] });
                qc.invalidateQueries({ queryKey: ['spaces'] });
              }}
              onCancel={() => setShowTokenForm(false)}
            />
          ) : (
            <div className={styles.linkButtons}>
              {oauthConfigured && !user && (
                <button className={styles.linkBtn} onClick={handleSignIn}>
                  <GitHubIcon size={16} />
                  Sign in with GitHub
                </button>
              )}
              {oauthConfigured && user && (
                <button className={styles.linkBtn} onClick={handleLinkOAuth}>
                  <GitHubIcon size={16} />
                  Link another GitHub account
                </button>
              )}
              {user && (
                <button className={`${styles.linkBtn} ${styles.linkBtnSecondary}`} onClick={() => setShowTokenForm(true)}>
                  + Link with Personal Access Token
                  <span className={styles.linkBtnHint}>for GitHub Enterprise or fine-grained access</span>
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function TokenLinkForm({
  onLinked,
  onCancel,
}: {
  onLinked: () => void;
  onCancel: () => void;
}) {
  const [token, setToken] = useState('');
  const [baseUrl, setBaseUrl] = useState('https://api.github.com');
  const [error, setError] = useState('');

  const linkMutation = useMutation({
    mutationFn: () => api.linkAccountWithToken(token, baseUrl),
    onSuccess: onLinked,
    onError: (e: Error) => setError(e.message),
  });

  return (
    <div className={styles.tokenForm}>
      <h3 className={styles.tokenFormTitle}>Link account with token</h3>
      <p className={styles.tokenFormHint}>
        Paste a Personal Access Token (PAT) to link a different GitHub account
        or a GitHub Enterprise instance.
      </p>
      <details className={styles.tokenGuide}>
        <summary>How to create a token</summary>
        <div className={styles.tokenGuideBody}>
          <p><strong>Option A: Fine-grained token</strong> (recommended)</p>
          <ol>
            <li>
              Open{' '}
              <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener noreferrer">
                github.com &rarr; Settings &rarr; Fine-grained tokens
              </a>
            </li>
            <li><strong>Token name</strong> &mdash; anything you like (e.g. "PR Dashboard")</li>
            <li><strong>Expiration</strong> &mdash; 90 days is a good default</li>
            <li><strong>Resource owner</strong> &mdash; pick your org or personal account</li>
            <li><strong>Repository access</strong> &mdash; "All repositories", or select the ones you want to track</li>
            <li>
              Under <strong>Repository permissions</strong>, set:
              <ul>
                <li><strong>Pull requests</strong> &rarr; Read-only</li>
                <li><strong>Actions</strong> &rarr; Read-only</li>
                <li><em>Metadata</em> &rarr; Read-only (selected automatically)</li>
              </ul>
            </li>
            <li>
              Under <strong>Organization permissions</strong>, set:
              <ul>
                <li><strong>Members</strong> &rarr; Read-only (needed to discover your orgs)</li>
              </ul>
            </li>
            <li>Click <strong>Generate token</strong> and copy it</li>
          </ol>
          <p><strong>Option B: Classic token</strong></p>
          <ol>
            <li>
              Open{' '}
              <a href="https://github.com/settings/tokens/new" target="_blank" rel="noopener noreferrer">
                github.com &rarr; Settings &rarr; Classic tokens
              </a>
            </li>
            <li>Select scopes: <code>repo</code> and <code>read:org</code></li>
            <li>Click <strong>Generate token</strong> and copy it</li>
          </ol>
          <p className={styles.tokenGuideSso}>
            If your org uses SAML SSO, click <strong>Configure SSO</strong> next to the token
            after creating it and authorize it for your org.
          </p>
        </div>
      </details>
      {error && <div className={styles.tokenFormError}>{error}</div>}
      <div className={styles.tokenFormRow}>
        <label>Personal Access Token</label>
        <input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="ghp_... or github_pat_..."
          autoFocus
        />
      </div>
      <div className={styles.tokenFormRow}>
        <label>API base URL</label>
        <input
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
        />
        <span className={styles.tokenFormFieldHint}>
          Leave as-is for github.com (including SSO/enterprise orgs).
          Only change for self-hosted GitHub Enterprise Server (e.g. https://github.mycompany.com/api/v3).
        </span>
      </div>
      <div className={styles.tokenFormActions}>
        <button className={styles.actionBtn} onClick={onCancel}>Cancel</button>
        <button
          className={styles.linkBtn}
          disabled={!token.trim() || linkMutation.isPending}
          onClick={() => linkMutation.mutate()}
        >
          {linkMutation.isPending ? 'Linking...' : 'Link account'}
        </button>
      </div>
    </div>
  );
}

function AccountSection({
  account,
  spaces,
  onToggleSpace,
  onDiscover,
  onRemove,
  isDiscovering,
}: {
  account: GitHubAccountInfo;
  spaces: Space[];
  onToggleSpace: (id: number, active: boolean) => void;
  onDiscover: () => void;
  onRemove: () => void;
  isDiscovering: boolean;
}) {
  return (
    <div className={styles.accountSection}>
      <div className={styles.accountHeader}>
        {account.avatar_url && (
          <img src={account.avatar_url} alt="" className={styles.accountAvatar} />
        )}
        <span className={styles.accountLogin}>{account.login}</span>
        {account.base_url !== 'https://api.github.com' && (
          <span className={styles.accountBaseUrl}>{account.base_url}</span>
        )}
        <div className={styles.accountActions}>
          <button
            className={styles.actionBtn}
            onClick={onDiscover}
            disabled={isDiscovering}
            title="Re-discover orgs"
          >
            {isDiscovering ? 'Discovering...' : 'Refresh'}
          </button>
          <button
            className={`${styles.actionBtn} ${styles.deleteAction}`}
            onClick={onRemove}
            title="Unlink this account"
          >
            Unlink
          </button>
        </div>
      </div>
      {spaces.map((space) => (
        <SpaceRow
          key={space.id}
          space={space}
          onToggle={(active) => onToggleSpace(space.id, active)}
        />
      ))}
    </div>
  );
}

function SpaceRow({
  space,
  onToggle,
}: {
  space: Space;
  onToggle: (active: boolean) => void;
}) {
  return (
    <div className={`${styles.spaceRow} ${!space.is_active ? styles.spaceInactive : ''}`}>
      <span className={styles.spaceName}>{space.name}</span>
      <span className={styles.spaceType}>{space.space_type}</span>
      <button
        className={`${styles.visibilityBtn} ${space.is_active ? styles.visibilityShown : styles.visibilityHidden}`}
        onClick={() => onToggle(!space.is_active)}
      >
        {space.is_active ? 'Shown' : 'Hidden'}
      </button>
    </div>
  );
}
