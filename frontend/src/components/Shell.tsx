/** App shell — sidebar nav + header + content area. */

import { useState } from 'react';
import { Link, Outlet, useLocation } from 'react-router-dom';
import { useSSE } from '../api/useSSE';
import { TeamPanel } from './TeamPanel';
import { Tooltip } from './Tooltip';
import styles from './Shell.module.css';

export function Shell() {
  const location = useLocation();
  useSSE();
  const isHome = location.pathname === '/';
  const [showTeam, setShowTeam] = useState(false);

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <Link to="/" className={styles.logo}>PR Dashboard</Link>
        <nav className={styles.nav}>
          <Tooltip text="View all tracked repositories" position="bottom">
            <Link to="/" className={isHome ? styles.active : ''}>Repos</Link>
          </Tooltip>
          <Tooltip text="Manage team members and assignments" position="bottom">
            <button className={styles.teamBtn} onClick={() => setShowTeam(true)}>Team</button>
          </Tooltip>
        </nav>
      </header>
      <main className={styles.main}>
        <Outlet />
      </main>
      {showTeam && <TeamPanel onClose={() => setShowTeam(false)} />}
    </div>
  );
}
