/** App shell — sidebar nav + header + content area. */

import { useState, useEffect, useRef, useCallback } from 'react';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSSE } from '../api/useSSE';
import { useCurrentUser } from '../App';
import { api } from '../api/client';
import { useStore } from '../store/useStore';
import { TeamPanel } from './TeamPanel';
import { SpaceManager } from './SpaceManager';
import { Tooltip } from './Tooltip';
import { GitHubIcon } from './GitHubIcon';
import { DevUserSwitcher } from './DevUserSwitcher';
import { VersionBadge } from './VersionBadge';
import { AuthHealthBanner } from './AuthHealthBanner';
import { AuthHealthPanel } from './AuthHealthPanel';
import styles from './Shell.module.css';

export function Shell() {
  const location = useLocation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { connected } = useSSE();
  const isReposSection = location.pathname === '/' || location.pathname.startsWith('/repos');
  const isPrioritize = location.pathname === '/prioritise' || location.pathname === '/prioritize';
  const lastReposSectionPath = useStore((s) => s.lastReposSectionPath);

  const handleReposClick = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    if (isReposSection) {
      // Already in repos section, go to overview
      navigate('/');
    } else {
      // Coming from another section, return to last repos-section page
      navigate(lastReposSectionPath ?? '/');
    }
  }, [isReposSection, lastReposSectionPath, navigate]);
  const [showTeam, setShowTeam] = useState(false);
  const [showSpaces, setShowSpaces] = useState(false);
  const [showHealth, setShowHealth] = useState(false);
  const [showUserMenu, setShowUserMenu] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);
  const { user, setUser, oauthConfigured, banner, setBanner } = useCurrentUser();

  // Close user menu on outside click
  useEffect(() => {
    if (!showUserMenu) return;
    function handleClick(e: MouseEvent) {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
        setShowUserMenu(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [showUserMenu]);

  useEffect(() => {
    const handler = () => setShowSpaces(true);
    window.addEventListener('open-spaces', handler);
    return () => window.removeEventListener('open-spaces', handler);
  }, []);

  function handleConnectGitHub() {
    window.location.href = '/api/auth/github';
  }

  const { data: accounts } = useQuery({
    queryKey: ['accounts'],
    queryFn: api.listAccounts,
    enabled: !!user && showUserMenu,
  });

  async function handleSignOut() {
    await api.disconnectGitHub();
    setUser(null);
    setShowUserMenu(false);
    qc.removeQueries({ queryKey: ['accounts'] });
    qc.removeQueries({ queryKey: ['spaces'] });
    qc.removeQueries({ queryKey: ['repos'] });
  }

  function handleLinkOAuth() {
    setShowUserMenu(false);
    window.location.href = '/api/auth/github?link=true';
  }

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <Link to="/" className={styles.logo}>PR Dashboard<VersionBadge /></Link>
        {!connected && (
          <span
            style={{
              fontSize: '0.75rem', color: 'var(--ci-fail, #d73a4a)',
              background: 'rgba(215, 58, 74, 0.12)', padding: '2px 8px',
              borderRadius: 4, marginLeft: 8,
            }}
            title="Live updates disconnected — data may be stale"
          >
            Disconnected
          </span>
        )}
        <nav className={styles.nav}>
          <Tooltip text="View all tracked repositories" position="bottom">
            <a href="/" className={`${styles.navLink} ${isReposSection ? styles.navLinkActive : ''}`} onClick={handleReposClick}>Repos</a>
          </Tooltip>
          <Tooltip text="Cross-repo priority queue for review and merge order" position="bottom">
            <Link to="/prioritise" className={`${styles.navLink} ${isPrioritize ? styles.navLinkActive : ''}`}>Prioritise</Link>
          </Tooltip>
        </nav>
        <div className={styles.spacer} />
        <DevUserSwitcher />
        <div className={styles.userArea}>
          {user ? (
            <div className={styles.userMenuWrapper} ref={userMenuRef}>
              <button className={styles.userBtn} onClick={() => setShowUserMenu(v => !v)}>
                {user.avatar_url && (
                  <img src={user.avatar_url} alt="" className={styles.avatar} />
                )}
                <span className={styles.userName}>{user.name || user.login}</span>
                <span className={styles.chevron}>&#9662;</span>
              </button>
              {showUserMenu && (
                <div className={styles.userMenu}>
                  {accounts && accounts.length > 0 && (
                    <div className={styles.userMenuSection}>
                      <div className={styles.userMenuSectionLabel}>Linked accounts</div>
                      {accounts.map((acct) => (
                        <div key={acct.id} className={styles.userMenuAccount}>
                          {acct.avatar_url && (
                            <img src={acct.avatar_url} alt="" className={styles.userMenuAccountAvatar} />
                          )}
                          <span className={styles.userMenuAccountLogin}>{acct.login}</span>
                          {acct.base_url !== 'https://api.github.com' && (
                            <span className={styles.userMenuAccountGhe}>GHE</span>
                          )}
                        </div>
                      ))}
                    </div>
                  )}

                  <div className={styles.userMenuDivider} />

                  <button
                    className={styles.userMenuItem}
                    onClick={() => { setShowUserMenu(false); setShowSpaces(true); }}
                  >
                    Spaces
                  </button>
                  <button
                    className={styles.userMenuItem}
                    onClick={() => { setShowUserMenu(false); setShowTeam(true); }}
                  >
                    Team
                  </button>
                  <button
                    className={styles.userMenuItem}
                    onClick={() => { setShowUserMenu(false); setShowHealth(true); }}
                  >
                    Account health
                  </button>

                  <div className={styles.userMenuDivider} />

                  {oauthConfigured && (
                    <button className={styles.userMenuItem} onClick={handleLinkOAuth}>
                      <GitHubIcon size={14} />
                      Link another account
                    </button>
                  )}

                  <div className={styles.userMenuDivider} />

                  <button
                    className={`${styles.userMenuItem} ${styles.userMenuItemDanger}`}
                    onClick={handleSignOut}
                  >
                    Sign out
                  </button>
                  <button
                    className={`${styles.userMenuItem} ${styles.userMenuItemDanger}`}
                    onClick={async () => {
                      if (!window.confirm('Delete your account? This removes your spaces, tracked repos, and linked GitHub accounts. This cannot be undone.')) return;
                      await api.deleteMyAccount();
                      setUser(null);
                      setShowUserMenu(false);
                      qc.invalidateQueries();
                    }}
                  >
                    Delete my account
                  </button>
                </div>
              )}
            </div>
          ) : oauthConfigured ? (
            <Tooltip text="Sign in to link your identity for assignments, avatars, and optional token sharing with spaces" position="bottom">
              <button className={styles.githubBtn} onClick={handleConnectGitHub}>
                <GitHubIcon size={18} />
                Sign in with GitHub
              </button>
            </Tooltip>
          ) : null}
        </div>
      </header>
      {user && <AuthHealthBanner onViewDetails={() => setShowHealth(true)} />}
      {banner && (
        <div className={`${styles.banner} ${banner.type === 'error' ? styles.bannerError : ''}`}>
          <span>{banner.message}</span>
          <button className={styles.bannerDismiss} onClick={() => setBanner(null)}>&times;</button>
        </div>
      )}
      <main className={styles.main}>
        <Outlet />
      </main>
      {showTeam && <TeamPanel onClose={() => setShowTeam(false)} />}
      {showSpaces && <SpaceManager onClose={() => setShowSpaces(false)} />}
      {showHealth && <AuthHealthPanel onClose={() => setShowHealth(false)} />}
    </div>
  );
}
