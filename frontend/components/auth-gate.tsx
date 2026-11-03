"use client";

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useMe, WorkspaceProvider } from "@/lib/session";
import type { Workspace } from "@/lib/types";
import { Button } from "@/components/ui/button";

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-screen flex-col items-center justify-center gap-3 text-sm">{children}</div>;
}

/** /me が確定するまで保護ページの中身を描画しない */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const me = useMe();
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => api.get<Workspace[]>("/workspaces"),
    enabled: me.isSuccess,
  });

  // ログアウト後に戻るボタンで bfcache から復元された場合は再検証する
  useEffect(() => {
    const onShow = (e: PageTransitionEvent) => {
      if (e.persisted) window.location.reload();
    };
    window.addEventListener("pageshow", onShow);
    return () => window.removeEventListener("pageshow", onShow);
  }, []);

  const failed = me.error ?? workspaces.error;
  if (failed) {
    // 401 は api.ts がログイン画面へ遷移させる
    if (failed instanceof ApiError && failed.status === 401) {
      return (
        <Centered>
          <Loader2 className="h-5 w-5 animate-spin" />
        </Centered>
      );
    }
    return (
      <Centered>
        <p>{t.errors.network}</p>
        <Button variant="outline" onClick={() => window.location.reload()}>
          {t.common.retry}
        </Button>
      </Centered>
    );
  }
  if (!me.data || !workspaces.data) {
    return (
      <Centered>
        <Loader2 className="h-5 w-5 animate-spin" />
        <span className="text-muted-foreground">{t.common.loading}</span>
      </Centered>
    );
  }
  return <WorkspaceProvider workspaces={workspaces.data}>{children}</WorkspaceProvider>;
}
