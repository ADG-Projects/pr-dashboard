/** Dev-only user switcher for testing multi-user scenarios. */

import { useState, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api, type GitHubUser } from '../api/client';
import { useCurrentUser } from '../App';

export function DevUserSwitcher() {
  const { user, setUser } = useCurrentUser();
  const qc = useQueryClient();
  const [users, setUsers] = useState<GitHubUser[]>([]);
  const [available, setAvailable] = useState(false);

  useEffect(() => {
    api.devListUsers()
      .then((data) => { setUsers(data); setAvailable(true); })
      .catch(() => setAvailable(false));
  }, []);

  if (!available || users.length < 2) return null;

  async function switchTo(u: GitHubUser) {
    await api.devLogin(u.id);
    setUser(u);
    qc.invalidateQueries();
  }

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '6px',
      padding: '2px 8px',
      borderRadius: '6px',
      background: 'var(--bg-card, #1a1a2e)',
      border: '1px dashed var(--ci-pending, #f0ad4e)',
      fontSize: '12px',
    }}>
      <span style={{ color: 'var(--ci-pending, #f0ad4e)', fontWeight: 600 }}>DEV</span>
      <select
        value={user?.id ?? ''}
        onChange={(e) => {
          const selected = users.find(u => u.id === Number(e.target.value));
          if (selected) switchTo(selected);
        }}
        style={{
          background: 'transparent',
          color: 'inherit',
          border: 'none',
          fontSize: '12px',
          cursor: 'pointer',
          outline: 'none',
        }}
      >
        {users.map((u) => (
          <option key={u.id} value={u.id}>
            {u.name || u.login}
          </option>
        ))}
      </select>
    </div>
  );
}
