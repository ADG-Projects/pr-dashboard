/** Global UI state (Zustand). Server data is handled by react-query. */

import { create } from 'zustand';

interface AppState {
  /** Currently selected PR number for the detail panel */
  selectedPrNumber: number | null;
  selectPr: (prNumber: number | null) => void;

  /** Repo ID for cross-repo PR detail panel (e.g. prioritize view) */
  selectedRepoId: number | null;
  setSelectedRepoId: (id: number | null) => void;

  /** Detail panel open state */
  detailOpen: boolean;
  setDetailOpen: (open: boolean) => void;

  /** Last visited repo path (e.g. "/repos/org/name") for nav memory */
  lastRepoPath: string | null;
  setLastRepoPath: (path: string | null) => void;

  /** Sidebar collapsed */
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
}

export const useStore = create<AppState>((set) => ({
  selectedPrNumber: null,
  selectPr: (prNumber) => set({ selectedPrNumber: prNumber, detailOpen: prNumber !== null }),

  selectedRepoId: null,
  setSelectedRepoId: (id) => set({ selectedRepoId: id }),

  detailOpen: false,
  setDetailOpen: (open) => set({ detailOpen: open, selectedPrNumber: open ? undefined : null }),

  lastRepoPath: null,
  setLastRepoPath: (path) => set({ lastRepoPath: path }),

  sidebarCollapsed: false,
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
}));
