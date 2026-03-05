/** Modal panel for managing team members. */

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { Tooltip } from './Tooltip';
import styles from './TeamPanel.module.css';

interface Props {
  onClose: () => void;
}

export function TeamPanel({ onClose }: Props) {
  const qc = useQueryClient();
  const [displayName, setDisplayName] = useState('');
  const [githubLogin, setGithubLogin] = useState('');

  const { data: members } = useQuery({
    queryKey: ['team'],
    queryFn: api.listTeam,
  });

  const addMutation = useMutation({
    mutationFn: (data: { display_name: string; github_login?: string }) =>
      api.addTeamMember(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['team'] });
      setDisplayName('');
      setGithubLogin('');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteTeamMember(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['team'] }),
  });

  const activeMembers = members?.filter((m) => m.is_active) || [];

  function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!displayName.trim()) return;
    addMutation.mutate({
      display_name: displayName.trim(),
      ...(githubLogin.trim() ? { github_login: githubLogin.trim() } : {}),
    });
  }

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2>Team Members</h2>
          <button onClick={onClose} className={styles.closeBtn}>x</button>
        </div>
        <div className={styles.body}>
          {activeMembers.length === 0 ? (
            <div className={styles.empty}>No team members yet</div>
          ) : (
            <div className={styles.memberList}>
              {activeMembers.map((m) => (
                <div key={m.id} className={styles.memberRow}>
                  <span className={styles.memberName}>{m.display_name}</span>
                  {m.github_login && (
                    <span className={styles.memberLogin}>@{m.github_login}</span>
                  )}
                  <Tooltip text="Remove team member" position="left">
                    <button
                      className={styles.deleteBtn}
                      onClick={() => deleteMutation.mutate(m.id)}
                    >
                      x
                    </button>
                  </Tooltip>
                </div>
              ))}
            </div>
          )}

          <form className={styles.addForm} onSubmit={handleAdd}>
            <Tooltip text="Name shown in the dashboard (required)" position="top">
              <input
                className={styles.input}
                placeholder="Display name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
              />
            </Tooltip>
            <Tooltip text="GitHub username for matching reviews (optional)" position="top">
              <input
                className={styles.input}
                placeholder="GitHub login"
                value={githubLogin}
                onChange={(e) => setGithubLogin(e.target.value)}
                style={{ flex: 0.7 }}
              />
            </Tooltip>
            <button
              type="submit"
              className={styles.addBtn}
              disabled={!displayName.trim() || addMutation.isPending}
            >
              Add
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
