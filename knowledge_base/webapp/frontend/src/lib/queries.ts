// React Query hooks — the single source of truth for server state.
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "./api";
import type {
  ControlDetail,
  ControlListItem,
  ControlUpdate,
  DashboardStats,
  Framework,
  HistoryEntry,
  SearchHit,
  TreeNode,
  ValidationReport,
} from "./types";

export const keys = {
  frameworks: ["frameworks"] as const,
  tree: (fw: number) => ["tree", fw] as const,
  controls: (fw: number, node?: number | null) => ["controls", fw, node ?? "all"] as const,
  control: (pk: number) => ["control", pk] as const,
  history: (pk: number) => ["history", pk] as const,
  validation: (fw: number) => ["validation", fw] as const,
  stats: ["stats"] as const,
  search: (q: string) => ["search", q] as const,
};

export const useFrameworks = () =>
  useQuery({ queryKey: keys.frameworks, queryFn: () => api.get<Framework[]>("/frameworks") });

export const useTree = (fw: number | null) =>
  useQuery({
    queryKey: keys.tree(fw ?? 0),
    queryFn: () => api.get<TreeNode[]>(`/frameworks/${fw}/tree`),
    enabled: fw != null,
  });

export const useControls = (fw: number | null, nodeId?: number | null) =>
  useQuery({
    queryKey: keys.controls(fw ?? 0, nodeId),
    queryFn: () => {
      const qs = nodeId != null ? `?node_id=${nodeId}` : "";
      return api.get<ControlListItem[]>(`/frameworks/${fw}/controls${qs}`);
    },
    enabled: fw != null,
  });

export const useControl = (pk: number | null) =>
  useQuery({
    queryKey: keys.control(pk ?? 0),
    queryFn: () => api.get<ControlDetail>(`/controls/${pk}`),
    enabled: pk != null,
  });

export const useControlHistory = (pk: number | null) =>
  useQuery({
    queryKey: keys.history(pk ?? 0),
    queryFn: () => api.get<HistoryEntry[]>(`/controls/${pk}/history`),
    enabled: pk != null,
  });

export const useValidation = (fw: number | null) =>
  useQuery({
    queryKey: keys.validation(fw ?? 0),
    queryFn: () => api.get<ValidationReport>(`/frameworks/${fw}/validate`),
    enabled: fw != null,
  });

export const useStats = () =>
  useQuery({ queryKey: keys.stats, queryFn: () => api.get<DashboardStats>("/stats") });

export const useSearch = (q: string) =>
  useQuery({
    queryKey: keys.search(q),
    queryFn: () => api.get<SearchHit[]>(`/search?q=${encodeURIComponent(q)}&limit=30`),
    enabled: q.trim().length > 0,
  });

export function useUpdateControl(fw: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pk, patch }: { pk: number; patch: ControlUpdate }) =>
      api.patch<ControlDetail>(`/controls/${pk}`, patch),
    onSuccess: (data) => {
      qc.setQueryData(keys.control(data.id), data);
      qc.invalidateQueries({ queryKey: keys.history(data.id) });
      if (fw != null) qc.invalidateQueries({ queryKey: keys.controls(fw) });
      qc.invalidateQueries({ queryKey: keys.validation(data.framework_id) });
    },
  });
}

export function useDuplicateControl(fw: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pk: number) => api.post<ControlDetail>(`/controls/${pk}/duplicate`),
    onSuccess: () => {
      if (fw != null) {
        qc.invalidateQueries({ queryKey: keys.controls(fw) });
        qc.invalidateQueries({ queryKey: keys.tree(fw) });
      }
    },
  });
}

export function useDeleteControl(fw: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pk: number) => api.del(`/controls/${pk}`),
    onSuccess: () => {
      if (fw != null) {
        qc.invalidateQueries({ queryKey: keys.controls(fw) });
        qc.invalidateQueries({ queryKey: keys.tree(fw) });
      }
    },
  });
}
