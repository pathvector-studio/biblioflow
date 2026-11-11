"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useMe } from "@/lib/session";
import type { UpdateStatus } from "@/lib/types";

const README_URL = "https://github.com/pathvector-studio/biblioflow#updating";

function ExternalLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline">
      {children}
    </a>
  );
}

/** 管理者だけに出す更新の通知。管理者以外には何も描画せず、API も呼ばない */
export function UpdatesPanel() {
  const me = useMe();
  if (me.data?.user.is_admin !== true) return null;
  return <UpdatesSection />;
}

function UpdatesSection() {
  const { t, locale } = useI18n();
  const status = useQuery({
    queryKey: ["update-status"],
    queryFn: () => api.get<UpdateStatus>("/system/update-status"),
    retry: false,
    staleTime: 5 * 60_000,
  });
  const s = status.data;
  if (!s) return null;
  return (
    <section className="space-y-2">
      <h2 className="text-base font-semibold">{t.settings.updatesTitle}</h2>
      {!s.enabled ? (
        <p className="text-sm text-muted-foreground">
          {t.settings.updatesDisabled} <ExternalLink href={README_URL}>{t.settings.updatesDisabledLink}</ExternalLink>
        </p>
      ) : s.update_available && s.latest ? (
        <div className="space-y-1 text-sm">
          <p>{t.settings.updatesAvailable.replace("{version}", s.latest).replace("{current}", s.current)}</p>
          {s.security && <p className="font-medium text-destructive">{t.settings.updatesSecurity}</p>}
          {s.url && <ExternalLink href={s.url}>{t.settings.updatesRelease}</ExternalLink>}
        </div>
      ) : s.checked_at ? (
        <p className="text-sm text-muted-foreground">
          {t.settings.updatesUpToDate.replace("{checked}", new Date(s.checked_at).toLocaleString(locale))}
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">{t.settings.updatesNotChecked}</p>
      )}
    </section>
  );
}
