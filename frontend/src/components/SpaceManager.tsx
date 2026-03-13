/** Modal for managing linked GitHub accounts and discovered spaces. */

import { useState, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type AdoAccountInfo, type GitHubAccountInfo, type Space } from '../api/client';
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
  const [showAdoForm, setShowAdoForm] = useState(false);
  const [patLinked, setPatLinked] = useState(false);
  const initialHadActiveRef = useRef<boolean | null>(null);

  const { data: accounts } = useQuery({
    queryKey: ['accounts'],
    queryFn: api.listAccounts,
    enabled: !!user,
  });

  const { data: spaces } = useQuery({
    queryKey: ['spaces'],
    queryFn: api.listSpaces,
  });

  const { data: adoAccounts } = useQuery({
    queryKey: ['ado-accounts'],
    queryFn: api.listAdoAccounts,
    enabled: !!user,
  });

  const removeAdoMutation = useMutation({
    mutationFn: (accountId: number) => api.removeAdoAccount(accountId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ado-accounts'], refetchType: 'active' });
      qc.invalidateQueries({ queryKey: ['ado-status'], refetchType: 'active' });
    },
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, isActive }: { id: number; isActive: boolean }) =>
      api.toggleSpace(id, isActive),
    onSuccess: (_data, { id, isActive }) => {
      qc.invalidateQueries({ queryKey: ['spaces'], refetchType: 'active' });
      qc.invalidateQueries({ queryKey: ['repos'], refetchType: 'active' });
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
    onSuccess: () => qc.invalidateQueries({ queryKey: ['spaces'], refetchType: 'active' }),
  });

  const removeAccountMutation = useMutation({
    mutationFn: (accountId: number) => api.removeAccount(accountId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['accounts'], refetchType: 'active' });
      qc.invalidateQueries({ queryKey: ['spaces'], refetchType: 'active' });
      qc.invalidateQueries({ queryKey: ['repos'], refetchType: 'active' });
    },
  });

  const hasActiveSpace = spaces?.some((s) => s.is_active) ?? false;

  // Capture initial state on first load so guidance persists across
  // space activations and only disappears after actually linking a new account.
  if (initialHadActiveRef.current === null && spaces !== undefined) {
    initialHadActiveRef.current = hasActiveSpace;
  }
  const showPatGuidance = !patLinked && initialHadActiveRef.current === false;

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
            Activate a space to track its repositories on the dashboard.
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
              showPulse={!hasActiveSpace}
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
                  showPulse={!hasActiveSpace}
                />
              ))}
            </div>
          )}

          {(!accounts || accounts.length === 0) && !user && (
            <div className={styles.empty}>
              Sign in with GitHub to get started.
            </div>
          )}

          {!hasActiveSpace && (spaces?.length ?? 0) > 0 && (
            <div className={styles.firstTimeHint}>
              Activate a space to start tracking its repos
            </div>
          )}

          {showTokenForm ? (
            <TokenLinkForm
              onLinked={() => {
                setShowTokenForm(false);
                setPatLinked(true);
                qc.invalidateQueries({ queryKey: ['accounts'], refetchType: 'active' });
                qc.invalidateQueries({ queryKey: ['spaces'], refetchType: 'active' });
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
                <>
                  {showPatGuidance && (
                    <div className={styles.patHint}>
                      Need access to a GitHub Enterprise instance or want fine-grained token control? Link a Personal Access Token below.
                    </div>
                  )}
                  <button className={`${styles.linkBtn} ${styles.linkBtnSecondary} ${showPatGuidance ? styles.linkBtnPulse : ''}`} onClick={() => setShowTokenForm(true)}>
                    + Link with Personal Access Token
                    <span className={styles.linkBtnHint}>for GitHub Enterprise or fine-grained access</span>
                  </button>
                </>
              )}
            </div>
          )}

          {user && (
            <>
              <div className={styles.accountSection} style={{ marginTop: '1.5rem' }}>
                <div className={styles.accountHeader}>
                  <span className={styles.accountLogin}>Azure DevOps <span className="betaBadge">Beta</span></span>
                </div>
                {adoAccounts && adoAccounts.length > 0 ? (
                  adoAccounts.map((ado) => (
                    <AdoAccountRow
                      key={ado.id}
                      account={ado}
                      onRemove={() => {
                        if (window.confirm(`Remove ADO account "${ado.display_name || ado.org_url}"?`)) {
                          removeAdoMutation.mutate(ado.id);
                        }
                      }}
                    />
                  ))
                ) : (
                  <p className={styles.hint} style={{ margin: '0.25rem 0 0 0' }}>
                    Link an ADO account to enable work item integration.
                  </p>
                )}
              </div>

              {showAdoForm ? (
                <AdoLinkForm
                  onLinked={() => {
                    setShowAdoForm(false);
                    qc.invalidateQueries({ queryKey: ['ado-accounts'], refetchType: 'active' });
                    qc.invalidateQueries({ queryKey: ['ado-status'], refetchType: 'active' });
                  }}
                  onCancel={() => setShowAdoForm(false)}
                />
              ) : (
                <div className={styles.linkButtons}>
                  <button
                    className={`${styles.linkBtn} ${styles.linkBtnSecondary}`}
                    onClick={() => setShowAdoForm(true)}
                  >
                    + Link ADO Account <span className="betaBadge">Beta</span>
                    <span className={styles.linkBtnHint}>for Azure DevOps work item integration</span>
                  </button>
                </div>
              )}
            </>
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
          <p><strong>Option A: Classic token</strong> (recommended for multi-org)</p>
          <ol>
            <li>
              Open{' '}
              <a href="https://github.com/settings/tokens/new" target="_blank" rel="noopener noreferrer">
                github.com &rarr; Settings &rarr; Classic tokens
              </a>
            </li>
            <li><strong>Note</strong> &mdash; anything you like (e.g. "PR Dashboard")</li>
            <li><strong>Expiration</strong> &mdash; 90 days is a good default</li>
            <li>Select scopes: <code>repo</code> and <code>read:org</code></li>
            <li>Click <strong>Generate token</strong> and copy it</li>
            <li>
              <strong>SSO orgs only:</strong> Back on the tokens list, click{' '}
              <strong>Configure SSO</strong> next to the token and <strong>Authorize</strong> it
              for each org that uses SAML single sign-on. The token won't work for SSO-protected
              orgs until this step is completed.
            </li>
          </ol>
          <p><strong>Option B: Fine-grained token</strong></p>
          <p className={styles.tokenGuideNote}>
            Fine-grained tokens are scoped to a single owner (one org or your personal account).
            You'll need a separate token for each org you want to track.
          </p>
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
                <li><strong>Pull requests</strong> &rarr; Read and write</li>
                <li><strong>Actions</strong> &rarr; Read-only</li>
                <li><em>Metadata</em> &rarr; Read-only (selected automatically)</li>
              </ul>
            </li>
            <li>Click <strong>Generate token</strong> and copy it</li>
          </ol>
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
  showPulse,
}: {
  account: GitHubAccountInfo;
  spaces: Space[];
  onToggleSpace: (id: number, active: boolean) => void;
  onDiscover: () => void;
  onRemove: () => void;
  isDiscovering: boolean;
  showPulse: boolean;
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
          showPulse={showPulse}
        />
      ))}
    </div>
  );
}

function SpaceRow({
  space,
  onToggle,
  showPulse,
}: {
  space: Space;
  onToggle: (active: boolean) => void;
  showPulse: boolean;
}) {
  const pulseClass = !space.is_active && showPulse ? styles.visibilityPulse : '';
  return (
    <div className={`${styles.spaceRow} ${!space.is_active ? styles.spaceInactive : ''}`}>
      <span className={styles.spaceName}>{space.name}</span>
      <span className={styles.spaceType}>{space.space_type}</span>
      <button
        className={`${styles.visibilityBtn} ${space.is_active ? styles.visibilityShown : styles.visibilityHidden} ${pulseClass}`}
        onClick={() => onToggle(!space.is_active)}
      >
        {space.is_active ? 'Active' : 'Activate'}
      </button>
    </div>
  );
}

function AdoAccountRow({
  account,
  onRemove,
}: {
  account: AdoAccountInfo;
  onRemove: () => void;
}) {
  return (
    <div className={styles.spaceRow}>
      <span className={styles.spaceName}>
        {account.display_name || `${account.org_url} / ${account.project}`}
      </span>
      <span className={styles.spaceType}>ADO</span>
      <button
        className={`${styles.actionBtn} ${styles.deleteAction}`}
        onClick={onRemove}
        title="Remove ADO account"
      >
        Remove
      </button>
    </div>
  );
}

function AdoLinkForm({
  onLinked,
  onCancel,
}: {
  onLinked: () => void;
  onCancel: () => void;
}) {
  const [token, setToken] = useState('');
  const [orgUrl, setOrgUrl] = useState('');
  const [project, setProject] = useState('');
  const [error, setError] = useState('');

  const linkMutation = useMutation({
    mutationFn: () => api.linkAdoAccount(token, orgUrl, project),
    onSuccess: onLinked,
    onError: (e: Error) => setError(e.message),
  });

  return (
    <div className={styles.tokenForm}>
      <h3 className={styles.tokenFormTitle}>Link ADO account</h3>
      <p className={styles.tokenFormHint}>
        Paste a Personal Access Token to enable Azure DevOps work item linking.
      </p>
      <details className={styles.tokenGuide}>
        <summary>How to create an ADO token</summary>
        <div className={styles.tokenGuideBody}>
          <ol>
            <li>Go to <strong>Azure DevOps</strong> &rarr; User Settings (top-right) &rarr; <strong>Personal Access Tokens</strong></li>
            <li>Click <strong>New Token</strong></li>
            <li><strong>Name</strong> &mdash; anything you like (e.g. "PR Dashboard")</li>
            <li><strong>Organization</strong> &mdash; select your organization</li>
            <li><strong>Expiration</strong> &mdash; 90 days is a good default</li>
            <li>Under <strong>Scopes</strong>, select: <strong>Work Items</strong> &rarr; Read &amp; Write</li>
            <li>Click <strong>Create</strong> and copy the token</li>
          </ol>
          <p className={styles.tokenGuideNote}>
            The organization URL should look like <code>https://dev.azure.com/YourOrg</code>
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
          placeholder="your-ado-pat"
          autoFocus
        />
      </div>
      <div className={styles.tokenFormRow}>
        <label>Organization URL</label>
        <input
          value={orgUrl}
          onChange={(e) => setOrgUrl(e.target.value)}
          placeholder="https://dev.azure.com/YourOrg"
        />
      </div>
      <div className={styles.tokenFormRow}>
        <label>Project name</label>
        <input
          value={project}
          onChange={(e) => setProject(e.target.value)}
          placeholder="My Project"
        />
      </div>
      <div className={styles.tokenFormActions}>
        <button className={styles.actionBtn} onClick={onCancel}>Cancel</button>
        <button
          className={styles.linkBtn}
          disabled={!token.trim() || !orgUrl.trim() || !project.trim() || linkMutation.isPending}
          onClick={() => linkMutation.mutate()}
        >
          {linkMutation.isPending ? 'Linking...' : 'Link account'}
        </button>
      </div>
    </div>
  );
}
