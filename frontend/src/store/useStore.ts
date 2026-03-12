/** Global UI state (Zustand). Server data is handled by react-query. */

import { create } from 'zustand';

export interface RepoFilters {
  stateFilter: string;
  authorFilter: string;
  reviewerFilter: string;
  ciFilter: string;
  branchFilter: string;
  priorityFilter: string;
  labelFilter: string;
  stackFilter: number | null;
  collapsedStacks: Set<number>;
}

export const DEFAULT_REPO_FILTERS: RepoFilters = {
  stateFilter: 'open',
  authorFilter: '',
  reviewerFilter: '',
  ciFilter: '',
  branchFilter: '',
  priorityFilter: '',
  labelFilter: '',
  stackFilter: null,
  collapsedStacks: new Set(),
};

function loadPerRepoCollapsed(): Record<string, number[]> {
  try {
    const stored = localStorage.getItem('perRepoCollapsedStacks');
    return stored ? JSON.parse(stored) : {};
  } catch { return {}; }
}

function savePerRepoCollapsed(repoFilters: Record<string, RepoFilters>) {
  try {
    const serializable: Record<string, number[]> = {};
    for (const [key, filters] of Object.entries(repoFilters)) {
      if (filters.collapsedStacks.size > 0) {
        serializable[key] = [...filters.collapsedStacks];
      }
    }
    localStorage.setItem('perRepoCollapsedStacks', JSON.stringify(serializable));
  } catch {}
}

interface AppState {
  /** Per-repo filter state keyed by "owner/name" */
  repoFilters: Record<string, RepoFilters>;
  setRepoFilters: (repoKey: string, filters: Partial<RepoFilters>) => void;
  clearRepoFilters: (repoKey: string) => void;
  toggleStackCollapsed: (repoKey: string, stackId: number) => void;

  /** Currently selected PR number for the detail panel */
  selectedPrNumber: number | null;
  selectPr: (prNumber: number | null) => void;

  /** Repo ID for cross-repo PR detail panel (e.g. prioritize view) */
  selectedRepoId: number | null;
  setSelectedRepoId: (id: number | null) => void;

  /** Detail panel open state */
  detailOpen: boolean;
  setDetailOpen: (open: boolean) => void;

  /** Last visited repos-section path ("/" or "/repos/org/name") for nav memory */
  lastReposSectionPath: string | null;
  setLastReposSectionPath: (path: string | null) => void;

  /** Sidebar collapsed */
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
}

function getRepoFilters(state: AppState, repoKey: string): RepoFilters {
  return state.repoFilters[repoKey] ?? DEFAULT_REPO_FILTERS;
}

export const useStore = create<AppState>((set) => ({
  repoFilters: (() => {
    const collapsed = loadPerRepoCollapsed();
    const initial: Record<string, RepoFilters> = {};
    for (const [key, ids] of Object.entries(collapsed)) {
      initial[key] = { ...DEFAULT_REPO_FILTERS, collapsedStacks: new Set(ids) };
    }
    return initial;
  })(),

  setRepoFilters: (repoKey, filters) => set((s) => {
    const current = getRepoFilters(s, repoKey);
    const updated = { ...current, ...filters };
    const next = { ...s.repoFilters, [repoKey]: updated };
    if ('collapsedStacks' in filters) savePerRepoCollapsed(next);
    return { repoFilters: next };
  }),

  clearRepoFilters: (repoKey) => set((s) => {
    const current = getRepoFilters(s, repoKey);
    const next = {
      ...s.repoFilters,
      [repoKey]: { ...DEFAULT_REPO_FILTERS, collapsedStacks: current.collapsedStacks },
    };
    return { repoFilters: next };
  }),

  toggleStackCollapsed: (repoKey, stackId) => set((s) => {
    const current = getRepoFilters(s, repoKey);
    const nextCollapsed = new Set(current.collapsedStacks);
    if (nextCollapsed.has(stackId)) nextCollapsed.delete(stackId);
    else nextCollapsed.add(stackId);
    const next = { ...s.repoFilters, [repoKey]: { ...current, collapsedStacks: nextCollapsed } };
    savePerRepoCollapsed(next);
    return { repoFilters: next };
  }),

  selectedPrNumber: null,
  selectPr: (prNumber) => set({ selectedPrNumber: prNumber, detailOpen: prNumber !== null }),

  selectedRepoId: null,
  setSelectedRepoId: (id) => set({ selectedRepoId: id }),

  detailOpen: false,
  setDetailOpen: (open) => set({ detailOpen: open, selectedPrNumber: open ? undefined : null }),

  lastReposSectionPath: null,
  setLastReposSectionPath: (path) => set({ lastReposSectionPath: path }),

  sidebarCollapsed: false,
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
}));
