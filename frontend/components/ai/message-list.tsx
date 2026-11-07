"use client";

import { useCallback, useEffect, useRef } from "react";
import { useI18n } from "@/lib/i18n";
import type { Citation } from "@/lib/types";
import { cn } from "@/lib/utils";
import { MarkdownAnswer } from "@/components/ai/markdown-answer";
import type { UiMessage } from "@/components/ai/use-chat-stream";

export function MessageList({
  messages,
  onCite,
  empty,
}: {
  messages: UiMessage[];
  onCite?: (c: Citation) => void;
  empty?: string;
}) {
  const { t } = useI18n();
  const end = useRef<HTMLDivElement>(null);
  const citeTitle = useCallback((c: Citation) => t.ai.citationTitle.replace("{label}", c.label), [t]);

  // 新しい内容が来たら末尾へ（流れている間も追う）
  const last = messages[messages.length - 1];
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [messages.length, last?.content, last?.status]);

  if (messages.length === 0) {
    return empty ? <p className="p-2 text-sm text-muted-foreground">{empty}</p> : null;
  }
  return (
    <div className="space-y-3" aria-live="polite">
      {messages.map((m) => (
        <div key={m.key} className={cn("flex flex-col gap-1", m.role === "user" && "items-end")}>
          <span className="text-[11px] text-muted-foreground">{m.role === "user" ? t.ai.you : t.ai.assistant}</span>
          {m.role === "user" ? (
            <p className="max-w-[90%] whitespace-pre-wrap rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
              {m.content}
            </p>
          ) : (
            <div className="w-full max-w-full rounded-lg border bg-card px-3 py-2">
              {m.status === "streaming" && !m.content ? (
                <p className="text-sm text-muted-foreground">{t.common.loading}</p>
              ) : m.status === "streaming" ? (
                // 流れている間は検証前のテキストなので、バッジにせずそのまま見せる
                <p className="whitespace-pre-wrap text-sm leading-relaxed">{m.content}</p>
              ) : (
                <MarkdownAnswer content={m.content} citations={m.citations} onCite={onCite} citeTitle={citeTitle} />
              )}
              {m.status === "cancelled" && (
                <p className="mt-1 text-xs font-medium text-muted-foreground">{t.ai.cancelled}</p>
              )}
              {m.status === "error" && (
                <p role="alert" className="mt-1 text-xs text-destructive">
                  {m.notice ?? t.ai.answerFailed}
                </p>
              )}
              {m.degraded && <p className="mt-1 text-[11px] text-muted-foreground">{t.ai.degraded}</p>}
            </div>
          )}
        </div>
      ))}
      <div ref={end} />
    </div>
  );
}
