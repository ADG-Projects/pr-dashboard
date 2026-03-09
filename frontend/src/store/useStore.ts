/** Global UI state (Zustand). Server data is handled by react-query. */

import { create } from 'zustand';

interface AppState {
  /** Currently selected PR id for the detail panel */
  selectedPrId: number | null;
  selectPr: (id: number | null) => void;

  /** Repo ID for cross-repo PR detail panel (e.g. prioritize view) */
  selectedRepoId: number | null;
  setSelectedRepoId: (id: number | null) => void;

  /** Detail panel open state */
  detailOpen: boolean;
  setDetailOpen: (open: boolean) => void;

  /** Sidebar collapsed */
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
}

export const useStore = create<AppState>((set) => ({
  selectedPrId: null,
  selectPr: (id) => set({ selectedPrId: id, detailOpen: id !== null }),

  selectedRepoId: null,
  setSelectedRepoId: (id) => set({ selectedRepoId: id }),

  detailOpen: false,
  setDetailOpen: (open) => set({ detailOpen: open, selectedPrId: open ? undefined : null }),

  sidebarCollapsed: false,
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
}));
