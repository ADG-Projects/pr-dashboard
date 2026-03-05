/** Hook that listens to backend SSE events and invalidates queries on sync_complete. */

import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';

const SSE_URL = import.meta.env.DEV ? 'http://localhost:8000/api/events' : '/api/events';

export function useSSE() {
  const qc = useQueryClient();

  useEffect(() => {
    const source = new EventSource(SSE_URL, { withCredentials: true });

    source.addEventListener('sync_complete', () => {
      qc.invalidateQueries({ queryKey: ['repos'] });
    });

    return () => source.close();
  }, [qc]);
}
