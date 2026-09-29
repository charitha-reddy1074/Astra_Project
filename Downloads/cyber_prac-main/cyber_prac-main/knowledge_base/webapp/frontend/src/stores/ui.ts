// Ephemeral UI state (selection, view mode, tree expansion, command palette).
// Server data lives in React Query; only view concerns live here.
import { create } from "zustand";
import { persist } from "zustand/middleware";

type ViewMode = "tree" | "grid";

interface UiState {
  frameworkId: number | null;
  nodeId: number | null;
  controlPk: number | null;
  viewMode: ViewMode;
  expanded: Record<number, boolean>;
  commandOpen: boolean;

  selectFramework: (id: number) => void;
  selectNode: (id: number | null) => void;
  selectControl: (pk: number | null) => void;
  setViewMode: (m: ViewMode) => void;
  toggleNode: (id: number) => void;
  expandNodes: (ids: number[]) => void;
  setCommandOpen: (open: boolean) => void;
}

export const useUi = create<UiState>()(
  persist(
    (set) => ({
      frameworkId: null,
      nodeId: null,
      controlPk: null,
      viewMode: "tree",
      expanded: {},
      commandOpen: false,

      selectFramework: (id) =>
        set({ frameworkId: id, nodeId: null, controlPk: null, expanded: {} }),
      selectNode: (id) => set({ nodeId: id }),
      selectControl: (pk) => set({ controlPk: pk }),
      setViewMode: (m) => set({ viewMode: m }),
      toggleNode: (id) =>
        set((s) => ({ expanded: { ...s.expanded, [id]: !s.expanded[id] } })),
      expandNodes: (ids) =>
        set((s) => {
          const next = { ...s.expanded };
          ids.forEach((i) => (next[i] = true));
          return { expanded: next };
        }),
      setCommandOpen: (open) => set({ commandOpen: open }),
    }),
    {
      name: "cyberai-fms-ui",
      partialize: (s) => ({ frameworkId: s.frameworkId, viewMode: s.viewMode }),
    },
  ),
);
