/** Modal for managing spaces (GitHub connections). */

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type Space } from '../api/client';
import { useCurrentUser } from '../App';
import { Tooltip } from './Tooltip';
import styles from './SpaceManager.module.css';

interface Props {
  onClose: () => void;
}

export function SpaceManager({ onClose }: Props) {
  const qc = useQueryClient();
  const { user } = useCurrentUser();
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);

  const { data: spaces } = useQuery({
    queryKey: ['spaces'],
    queryFn: api.listSpaces,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteSpace(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['spaces'] }),
  });

  const editingSpace = editingId ? spaces?.find((s) => s.id === editingId) : undefined;

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2>Spaces</h2>
          <button onClick={onClose} className={styles.closeBtn}>x</button>
        </div>
        <div className={styles.body}>
          <p className={styles.hint}>
            A space is a GitHub connection (org or user account) with its own token.
          </p>

          {spaces?.length === 0 && !showForm && (
            <div className={styles.empty}>No spaces yet. Create one to get started.</div>
          )}

          {spaces?.map((space) => (
            <SpaceCard
              key={space.id}
              space={space}
              onEdit={() => { setEditingId(space.id); setShowForm(true); }}
              onDelete={() => {
                if (window.confirm(`Delete space "${space.name}"?`)) {
                  deleteMutation.mutate(space.id);
                }
              }}
            />
          ))}

          {showForm ? (
            <SpaceForm
              existing={editingSpace}
              hasOAuth={!!user}
              onSaved={() => {
                setShowForm(false);
                setEditingId(null);
                qc.invalidateQueries({ queryKey: ['spaces'] });
              }}
              onCancel={() => { setShowForm(false); setEditingId(null); }}
            />
          ) : (
            <button className={styles.addBtn} onClick={() => setShowForm(true)}>
              + Add space
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function SpaceCard({
  space,
  onEdit,
  onDelete,
}: {
  space: Space;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api.checkSpaceConnectivity(space.id);
      setTestResult(res.ok ? `Connected (${res.rate_remaining ?? '?'} requests remaining)` : `Failed: ${res.error}`);
    } catch (e: unknown) {
      setTestResult(`Error: ${e instanceof Error ? e.message : 'unknown'}`);
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <span className={styles.cardName}>{space.name}</span>
        <span className={styles.cardSlug}>{space.slug}</span>
        <span className={styles.cardType}>{space.space_type}</span>
      </div>
      <div className={styles.cardMeta}>
        <span>{space.base_url}</span>
        <span>{space.has_token ? 'Token set' : 'No token'}</span>
      </div>
      <div className={styles.cardActions}>
        <Tooltip text="Test GitHub connectivity" position="top">
          <button className={styles.actionBtn} onClick={handleTest} disabled={testing}>
            {testing ? 'Testing...' : 'Test'}
          </button>
        </Tooltip>
        <button className={styles.actionBtn} onClick={onEdit}>Edit</button>
        <button className={`${styles.actionBtn} ${styles.deleteAction}`} onClick={onDelete}>Delete</button>
      </div>
      {testResult && <div className={styles.testResult}>{testResult}</div>}
    </div>
  );
}

function SpaceForm({
  existing,
  hasOAuth,
  onSaved,
  onCancel,
}: {
  existing?: Space;
  hasOAuth: boolean;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(existing?.name || '');
  const [slug, setSlug] = useState(existing?.slug || '');
  const [spaceType, setSpaceType] = useState(existing?.space_type || 'org');
  const [baseUrl, setBaseUrl] = useState(existing?.base_url || 'https://api.github.com');
  const [token, setToken] = useState('');
  const [useOauth, setUseOauth] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !slug.trim()) return;
    setSaving(true);
    setError('');
    try {
      const tokenValue = useOauth ? 'use_oauth' : token;
      if (existing) {
        await api.updateSpace(existing.id, {
          name: name.trim(),
          slug: slug.trim(),
          space_type: spaceType,
          base_url: baseUrl.trim(),
          ...(tokenValue ? { token: tokenValue } : {}),
        });
      } else {
        await api.createSpace({
          name: name.trim(),
          slug: slug.trim(),
          space_type: spaceType,
          base_url: baseUrl.trim(),
          token: tokenValue,
        });
      }
      onSaved();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to save');
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      <h3 className={styles.formTitle}>{existing ? 'Edit space' : 'New space'}</h3>
      {error && <div className={styles.formError}>{error}</div>}
      <div className={styles.formRow}>
        <label>Name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Kyndryl" />
      </div>
      <div className={styles.formRow}>
        <label>Slug (org/user)</label>
        <input value={slug} onChange={(e) => setSlug(e.target.value)} placeholder="e.g. kyndryl-agentic-ai" />
      </div>
      <div className={styles.formRow}>
        <label>Type</label>
        <select value={spaceType} onChange={(e) => setSpaceType(e.target.value)}>
          <option value="org">Organization</option>
          <option value="user">User</option>
        </select>
      </div>
      <div className={styles.formRow}>
        <label>Base URL</label>
        <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
      </div>
      <div className={styles.formRow}>
        <label>Token</label>
        {hasOAuth && baseUrl === 'https://api.github.com' && (
          <label className={styles.oauthToggle}>
            <input type="checkbox" checked={useOauth} onChange={(e) => setUseOauth(e.target.checked)} />
            Use my GitHub token
          </label>
        )}
        {!useOauth && (
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder={existing ? '(unchanged)' : 'ghp_...'}
          />
        )}
      </div>
      <div className={styles.formActions}>
        <button type="button" className={styles.cancelBtn} onClick={onCancel}>Cancel</button>
        <button type="submit" className={styles.saveBtn} disabled={saving || !name.trim() || !slug.trim()}>
          {saving ? 'Saving...' : existing ? 'Save' : 'Create'}
        </button>
      </div>
    </form>
  );
}
