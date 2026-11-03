import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Collection, Tag } from "@/lib/types";

export function useTags(workspaceId: string) {
  return useQuery({
    queryKey: ["tags", workspaceId],
    queryFn: () => api.get<Tag[]>("/tags", { workspace_id: workspaceId }),
  });
}

export function useCollections(workspaceId: string) {
  return useQuery({
    queryKey: ["collections", workspaceId],
    queryFn: () => api.get<Collection[]>("/collections", { workspace_id: workspaceId }),
  });
}
