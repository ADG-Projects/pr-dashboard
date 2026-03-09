/** Modal panel for viewing users (created via GitHub OAuth). */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { Tooltip } from './Tooltip';
import styles from './TeamPanel.module.css';

interface Props {
  onClose: () => void;
}

export function TeamPanel({ onClose }: Props) {
  const qc = useQueryClient();

  const { data: users } = useQuery({
    queryKey: ['team'],
    queryFn: api.listTeam,
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, is_active }: { id: number; is_active: boolean }) =>
      api.updateUser(id, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['team'] }),
  });

  const activeUsers = users?.filter((u) => u.is_active) || [];
  const inactiveUsers = users?.filter((u) => !u.is_active) || [];

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2>Team Members</h2>
          <button onClick={onClose} className={styles.closeBtn}>x</button>
        </div>
        <div className={styles.body}>
          <p className={styles.hint}>
            Users appear here after connecting via GitHub OAuth.
          </p>
          {activeUsers.length === 0 ? (
            <div className={styles.empty}>No users yet</div>
          ) : (
            <div className={styles.memberList}>
              {activeUsers.map((u) => (
                <div key={u.id} className={styles.memberRow}>
                  {u.avatar_url && (
                    <img src={u.avatar_url} alt="" className={styles.memberAvatar} />
                  )}
                  <span className={styles.memberName}>{u.name || u.login}</span>
                  <span className={styles.memberLogin}>@{u.login}</span>
                  {u.linked_accounts.length > 0 &&
                    u.linked_accounts.some((a) => a.login !== u.login) && (
                      <span className={styles.linkedAccounts}>
                        {u.linked_accounts
                          .filter((a) => a.login !== u.login)
                          .map((a) => a.login)
                          .join(' · ')}
                      </span>
                    )}
                  <Tooltip text="Deactivate user" position="left">
                    <button
                      className={styles.deleteBtn}
                      onClick={() => toggleMutation.mutate({ id: u.id, is_active: false })}
                    >
                      x
                    </button>
                  </Tooltip>
                </div>
              ))}
            </div>
          )}

          {inactiveUsers.length > 0 && (
            <>
              <h3 className={styles.sectionTitle}>Inactive</h3>
              <div className={styles.memberList}>
                {inactiveUsers.map((u) => (
                  <div key={u.id} className={`${styles.memberRow} ${styles.inactive}`}>
                    {u.avatar_url && (
                      <img src={u.avatar_url} alt="" className={styles.memberAvatar} />
                    )}
                    <span className={styles.memberName}>{u.name || u.login}</span>
                    <span className={styles.memberLogin}>@{u.login}</span>
                    <button
                      className={styles.reactivateBtn}
                      onClick={() => toggleMutation.mutate({ id: u.id, is_active: true })}
                    >
                      Reactivate
                    </button>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
