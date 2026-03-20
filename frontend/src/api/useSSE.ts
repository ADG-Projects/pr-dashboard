/** Hook that listens to backend SSE events and invalidates queries on sync_complete. */

import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

const SSE_URL = '/api/events';
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;

export function useSSE() {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(true);

  useEffect(() => {
    let source: EventSource | null = null;
    let reconnectDelay = RECONNECT_BASE_MS;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let destroyed = false;

    function connect() {
      if (destroyed) return;

      source = new EventSource(SSE_URL, { withCredentials: true });

      source.onopen = () => {
        setConnected(true);
        reconnectDelay = RECONNECT_BASE_MS;
      };

      source.onerror = () => {
        setConnected(false);
        source?.close();
        source = null;

        // Exponential backoff reconnection
        if (!destroyed) {
          reconnectTimer = setTimeout(connect, reconnectDelay);
          reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
        }
      };

      source.addEventListener('sync_complete', (event: MessageEvent) => {
        // refetchType: 'active' forces immediate refetch of mounted queries
        // instead of waiting for staleTime to expire
        qc.invalidateQueries({ queryKey: ['repos'], refetchType: 'active' });
        try {
          const data = JSON.parse(event.data);
          if (data.repo_id) {
            qc.invalidateQueries({ queryKey: ['pulls', data.repo_id], refetchType: 'active' });
            qc.invalidateQueries({ queryKey: ['stacks', data.repo_id], refetchType: 'active' });
            qc.invalidateQueries({ queryKey: ['pr-detail'], refetchType: 'active' });
            qc.invalidateQueries({ queryKey: ['prioritized'], refetchType: 'active' });
            qc.invalidateQueries({ queryKey: ['team-participated', data.repo_id], refetchType: 'active' });
          }
        } catch {
          qc.invalidateQueries({ queryKey: ['pulls'], refetchType: 'active' });
          qc.invalidateQueries({ queryKey: ['stacks'], refetchType: 'active' });
          qc.invalidateQueries({ queryKey: ['pr-detail'], refetchType: 'active' });
          qc.invalidateQueries({ queryKey: ['prioritized'], refetchType: 'active' });
          qc.invalidateQueries({ queryKey: ['team-participated'], refetchType: 'active' });
        }
      });

      source.addEventListener('spaces_discovered', () => {
        qc.invalidateQueries({ queryKey: ['spaces'], refetchType: 'active' });
        qc.invalidateQueries({ queryKey: ['accounts'], refetchType: 'active' });
      });

      source.addEventListener('sync_error', (event: MessageEvent) => {
        try {
          const data = JSON.parse(event.data);
          if (data.error) {
            console.warn('Sync error:', data.error);
          }
        } catch {
          // ignore parse errors
        }
        qc.invalidateQueries({ queryKey: ['auth-health'], refetchType: 'active' });
      });

      source.addEventListener('auth_issue', () => {
        qc.invalidateQueries({ queryKey: ['auth-health'], refetchType: 'active' });
      });

      source.addEventListener('auth_resolved', () => {
        qc.invalidateQueries({ queryKey: ['auth-health'], refetchType: 'active' });
      });
    }

    connect();

    // After OAuth redirect the background discovery task may still be running.
    // Poll spaces/accounts a few times to catch the result regardless of SSE timing.
    let pollCount = 0;
    const pollInterval = setInterval(() => {
      pollCount++;
      qc.invalidateQueries({ queryKey: ['spaces'] });
      qc.invalidateQueries({ queryKey: ['accounts'] });
      if (pollCount >= 5) clearInterval(pollInterval);
    }, 2000);

    return () => {
      destroyed = true;
      source?.close();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      clearInterval(pollInterval);
    };
  }, [qc]);

  return { connected };
}
