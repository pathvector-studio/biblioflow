"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquarePlus, Send, Square, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, describeError } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useWorkspace } from "@/lib/session";
import type { ChatStatus, Citation, Conversation, ConversationDetail } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { MessageList } from "@/components/ai/message-list";
import { AiDisabledNotice } from "@/components/ai/status-notice";
import { fromConversation, useAiErrorText, useChatStream } from "@/components/ai/use-chat-stream";

const ALL = "all";
const LIST_KEY = ["chat-conversations", "cross"];

/** 会話の最初の質問に残った範囲（null = すべて）。残っていない古い会話は「すべて」として扱う */
function storedScope(c: ConversationDetail): string {
  const first = c.messages.find((m) => m.role === "user");
  const id = first?.meta?.scope_workspace_id;
  return typeof id === "string" ? id : ALL;
}

/** 「AI」画面。ライブラリ全体（またはワークスペース 1 つ）への質問と、横断の会話の一覧 */
export function CrossChat() {
  const { t } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const errorText = useAiErrorText();
  const { workspaces } = useWorkspace();
  const [draft, setDraft] = useState("");
  const [scope, setScope] = useState(ALL);
  const [deleting, setDeleting] = useState<Conversation | null>(null);

  const status = useQuery({
    queryKey: ["chat-status"],
    queryFn: () => api.get<ChatStatus>("/chat/status"),
    retry: false,
    staleTime: 0,
  });
  const enabled = status.data?.enabled === true;
  const conversations = useQuery({
    queryKey: LIST_KEY,
    queryFn: async () => (await api.get<Conversation[]>("/chat/conversations")).filter((c) => c.paper_id === null),
    enabled,
  });

  const chat = useChatStream({
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: LIST_KEY });
      void queryClient.invalidateQueries({ queryKey: ["chat-status"] });
      // 応答の開始前に停止すると、サーバーが会話を保存するのはこの後になる
      window.setTimeout(() => void queryClient.invalidateQueries({ queryKey: LIST_KEY }), 5000);
    },
  });

  const open = useMutation({
    mutationFn: (id: string) => api.get<ConversationDetail>(`/chat/conversations/${id}`),
    onSuccess: (c) => {
      setScope(storedScope(c));
      chat.reset(c.id, fromConversation(c));
    },
    onError: (e) => toast.error(errorText(e)),
  });
  const remove = useMutation({
    mutationFn: (c: Conversation) => api.del(`/chat/conversations/${c.id}`),
    onSuccess: (_, c) => {
      if (chat.conversationId === c.id) chat.reset(null, []);
      void queryClient.invalidateQueries({ queryKey: LIST_KEY });
    },
    onError: (e) => toast.error(describeError(t, e)),
  });

  // 続きの会話は、保存された範囲で答える（workspace_id を送らない）。範囲は変えられない
  const locked = chat.conversationId !== null;
  const canSend = enabled && !chat.streaming && draft.trim() !== "";

  async function submit() {
    if (!canSend) return;
    const text = draft.trim();
    setDraft("");
    const body: Record<string, unknown> = {};
    if (!locked && scope !== ALL) body.workspace_id = scope;
    const accepted = await chat.send(text, { body });
    if (!accepted) setDraft((d) => d || text);
  }

  function onCite(c: Citation) {
    const q = new URLSearchParams({ page: String(c.page_number), hl: c.chunk_id });
    router.push(`/papers/${c.paper_id}?${q.toString()}`);
  }

  const list = conversations.data ?? [];
  const known = scope === ALL || workspaces.some((w) => w.id === scope);

  return (
    <div className="mx-auto flex h-full min-h-0 max-w-6xl flex-col gap-3 p-4 md:flex-row">
      <aside className="flex max-h-48 shrink-0 flex-col gap-2 md:max-h-none md:w-64">
        <Button
          variant="outline"
          size="sm"
          disabled={chat.streaming || (chat.messages.length === 0 && chat.conversationId === null)}
          onClick={() => chat.reset(null, [])}
        >
          <MessageSquarePlus className="h-4 w-4" />
          {t.ai.newConversation}
        </Button>
        {enabled && (
          <ul className="min-h-0 flex-1 overflow-auto rounded-md border" aria-label={t.ai.conversations}>
            {list.length === 0 && <li className="p-2 text-xs text-muted-foreground">{t.ai.noConversations}</li>}
            {list.map((c) => (
              <li
                key={c.id}
                className={cn(
                  "flex items-center gap-1 border-b px-2 py-1 last:border-b-0",
                  c.id === chat.conversationId && "bg-muted",
                )}
              >
                <button
                  type="button"
                  disabled={chat.streaming || open.isPending}
                  onClick={() => open.mutate(c.id)}
                  className="min-w-0 flex-1 truncate text-left text-sm hover:underline disabled:opacity-50"
                  title={c.title}
                >
                  {c.title}
                </button>
                <span className="shrink-0 text-[11px] text-muted-foreground">
                  {new Date(c.updated_at).toLocaleDateString()}
                </span>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-6 w-6"
                  aria-label={t.ai.deleteConversation}
                  disabled={chat.streaming}
                  onClick={() => setDeleting(c)}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </aside>

      <section className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-base font-semibold">{t.ai.crossTitle}</h1>
          <div className="flex items-center gap-2">
            {status.data?.daily_requests_remaining != null && (
              <span className="text-[11px] text-muted-foreground">
                {t.ai.remaining.replace("{n}", String(status.data.daily_requests_remaining))}
              </span>
            )}
            <label className="flex items-center gap-1.5 text-sm">
              <span className="text-muted-foreground">{t.ai.scope}</span>
              <Select
                value={scope}
                disabled={locked || chat.streaming}
                title={locked ? t.ai.scopeLocked : undefined}
                onChange={(e) => setScope(e.target.value)}
                className="max-w-56"
              >
                <option value={ALL}>{t.ai.scopeAll}</option>
                {workspaces.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.kind === "personal" ? t.nav.personal : w.name}
                  </option>
                ))}
                {!known && <option value={scope}>{t.ai.scopeUnknown}</option>}
              </Select>
            </label>
          </div>
        </div>

        {status.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {errorText(status.error)}
          </p>
        ) : status.data && !status.data.enabled ? (
          <AiDisabledNotice />
        ) : null}

        <div className="min-h-0 flex-1 overflow-auto">
          <MessageList
            messages={chat.messages}
            onCite={onCite}
            empty={enabled ? t.ai.crossEmpty : undefined}
            showTitle
          />
        </div>

        {chat.error && (
          <p role="alert" className="text-sm text-destructive">
            {chat.error}
          </p>
        )}

        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <Textarea
            className="min-h-[44px] flex-1 resize-none text-sm"
            rows={2}
            maxLength={4000}
            disabled={!enabled}
            aria-label={t.ai.crossPlaceholder}
            placeholder={t.ai.crossPlaceholder}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // 日本語入力の変換確定の Enter では送らない
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void submit();
              }
            }}
          />
          {chat.streaming ? (
            <Button type="button" variant="outline" onClick={chat.stop}>
              <Square className="h-4 w-4" />
              {t.ai.stop}
            </Button>
          ) : (
            <Button type="submit" disabled={!canSend}>
              <Send className="h-4 w-4" />
              {t.ai.send}
            </Button>
          )}
        </form>
      </section>

      <AlertDialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogTitle>{t.ai.deleteTitle}</AlertDialogTitle>
          <AlertDialogDescription>{t.ai.deleteBody}</AlertDialogDescription>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction onClick={() => deleting && remove.mutate(deleting)}>{t.common.delete}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
