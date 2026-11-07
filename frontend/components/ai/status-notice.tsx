"use client";

import { useI18n } from "@/lib/i18n";
import { useMe } from "@/lib/session";

export const AI_SETUP_URL = "https://github.com/pathvector-studio/biblioflow#ai-chat-and-figure-explanations";

/** AI が無効のときの案内。admin には有効にする手順（README）、それ以外には管理者待ちと出す */
export function AiDisabledNotice() {
  const { t } = useI18n();
  const me = useMe();
  const isAdmin = me.data?.user.is_admin === true;
  return (
    <div role="status" className="space-y-1 rounded-md border bg-muted/50 p-3 text-sm">
      <p className="font-medium">{t.ai.disabledTitle}</p>
      {isAdmin ? (
        <p className="text-muted-foreground">
          {t.ai.disabledAdmin}{" "}
          <a href={AI_SETUP_URL} target="_blank" rel="noopener noreferrer" className="text-primary underline">
            {t.ai.disabledAdminLink}
          </a>
        </p>
      ) : (
        <p className="text-muted-foreground">{t.ai.disabledMember}</p>
      )}
    </div>
  );
}
