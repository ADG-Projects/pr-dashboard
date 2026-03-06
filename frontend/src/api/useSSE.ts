/** Hook that listens to backend SSE events and invalidates queries on sync_complete. */

import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';

const SSE_URL = import.meta.env.DEV ? 'http://localhost:8000/api/events' : '/api/events';

export function useSSE() {
  const qc = useQueryClient();

  useEffect(() => {
    const source = new EventSource(SSE_URL, { withCredentials: true });

    source.addEventListener('sync_complete', (event: MessageEvent) => {
      qc.invalidateQueries({ queryKey: ['repos'] });
      try {
        const data = JSON.parse(event.data);
        if (data.repo_id) {
          qc.invalidateQueries({ queryKey: ['pulls', data.repo_id] });
          qc.invalidateQueries({ queryKey: ['stacks', data.repo_id] });
        }
      } catch {
        // If no parseable data, invalidate all pulls/stacks
        qc.invalidateQueries({ queryKey: ['pulls'] });
        qc.invalidateQueries({ queryKey: ['stacks'] });
      }
    });

    source.addEventListener('spaces_discovered', () => {
      qc.invalidateQueries({ queryKey: ['spaces'] });
      qc.invalidateQueries({ queryKey: ['accounts'] });
    });

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
      source.close();
      clearInterval(pollInterval);
    };
  }, [qc]);
}
