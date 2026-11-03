"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { MeResponse, Workspace } from "@/lib/types";

const WORKSPACE_KEY = "bf_workspace";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api.get<MeResponse>("/auth/me"), retry: false, staleTime: 60_000 });
}

interface WorkspaceState {
  workspaces: Workspace[];
  current: Workspace;
  setCurrentId: (id: string) => void;
  /** editor 以上。viewer では編集系の操作を出さない */
  canEdit: boolean;
}

const WorkspaceContext = createContext<WorkspaceState | null>(null);

function readSavedId(): string | null {
  try {
    return localStorage.getItem(WORKSPACE_KEY);
  } catch {
    return null;
  }
}

export function WorkspaceProvider({ workspaces, children }: { workspaces: Workspace[]; children: React.ReactNode }) {
  const [savedId, setSavedId] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setSavedId(readSavedId());
    setReady(true);
  }, []);

  const setCurrentId = useCallback((id: string) => {
    try {
      localStorage.setItem(WORKSPACE_KEY, id);
    } catch {
      // 保存できなくても選択は有効
    }
    setSavedId(id);
  }, []);

  const value = useMemo(() => {
    const current =
      workspaces.find((w) => w.id === savedId) ?? workspaces.find((w) => w.kind === "personal") ?? workspaces[0];
    return current ? { workspaces, current, setCurrentId, canEdit: current.role !== "viewer" } : null;
  }, [workspaces, savedId, setCurrentId]);

  if (!ready || !value) return null;
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceState {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return ctx;
}
