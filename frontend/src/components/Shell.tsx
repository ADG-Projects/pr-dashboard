/** App shell — sidebar nav + header + content area. */

import { useState, useEffect, useRef } from 'react';
import { Link, Outlet, useLocation } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSSE } from '../api/useSSE';
import { useCurrentUser } from '../App';
import { api } from '../api/client';
import { TeamPanel } from './TeamPanel';
import { SpaceManager } from './SpaceManager';
import { Tooltip } from './Tooltip';
import { GitHubIcon } from './GitHubIcon';
import { DevUserSwitcher } from './DevUserSwitcher';
import styles from './Shell.module.css';

export function Shell() {
  const location = useLocation();
  const qc = useQueryClient();
  useSSE();
  const isHome = location.pathname === '/';
  const [showTeam, setShowTeam] = useState(false);
  const [showSpaces, setShowSpaces] = useState(false);
  const [showUserMenu, setShowUserMenu] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);
  const { user, setUser, oauthConfigured } = useCurrentUser();

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
        <Link to="/" className={styles.logo}>PR Dashboard</Link>
        <nav className={styles.nav}>
          <Tooltip text="View all tracked repositories" position="bottom">
            <Link to="/" className={isHome ? styles.active : ''}>Repos</Link>
          </Tooltip>
          <Tooltip text="Manage GitHub connections" position="bottom">
            <button className={styles.teamBtn} onClick={() => setShowSpaces(true)}>Spaces</button>
          </Tooltip>
          <Tooltip text="Manage team members and assignments" position="bottom">
            <button className={styles.teamBtn} onClick={() => setShowTeam(true)}>Team</button>
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
                  <div className={styles.userMenuInfo}>
                    Signed in as <strong>{user.login}</strong>
                  </div>

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

                  {oauthConfigured && (
                    <button className={styles.userMenuItem} onClick={handleLinkOAuth}>
                      <GitHubIcon size={14} />
                      Link another account
                    </button>
                  )}
                  <button
                    className={styles.userMenuItem}
                    onClick={() => { setShowUserMenu(false); setShowSpaces(true); }}
                  >
                    Manage accounts & spaces
                  </button>

                  <div className={styles.userMenuDivider} />

                  <button
                    className={`${styles.userMenuItem} ${styles.userMenuItemDanger}`}
                    onClick={handleSignOut}
                  >
                    Sign out
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
      <main className={styles.main}>
        <Outlet />
      </main>
      {showTeam && <TeamPanel onClose={() => setShowTeam(false)} />}
      {showSpaces && <SpaceManager onClose={() => setShowSpaces(false)} />}
    </div>
  );
}
