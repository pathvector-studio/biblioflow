"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, MessageSquarePlus, Send, Square, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, describeError } from "@/lib/api";
import type { RegionCapture } from "@/lib/ai/crop";
import { useI18n } from "@/lib/i18n";
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
import { Textarea } from "@/components/ui/textarea";
import { MessageList } from "@/components/ai/message-list";
import { AiDisabledNotice } from "@/components/ai/status-notice";
import { fromConversation, useAiErrorText, useChatStream } from "@/components/ai/use-chat-stream";

/** 右ペインの「AI」タブ。この論文についての質問と、自分の会話の一覧 */
/** PDF の矩形選択から来た「AI で解説」の依頼。nonce が変わるたびに 1 回だけ送る */
export interface ExplainRequest {
  capture: RegionCapture;
  question: string;
  nonce: number;
}

export function AiTab({
  paperId,
  active,
  onCite,
  explainRequest,
}: {
  paperId: string;
  /** タブが開いているとき。開いたときに状態を確かめる */
  active: boolean;
  onCite: (c: Citation) => void;
  explainRequest?: ExplainRequest | null;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const errorText = useAiErrorText();
  const [draft, setDraft] = useState("");
  const [listOpen, setListOpen] = useState(false);
  const [deleting, setDeleting] = useState<Conversation | null>(null);

  const status = useQuery({
    queryKey: ["chat-status"],
    queryFn: () => api.get<ChatStatus>("/chat/status"),
    enabled: active,
    retry: false,
    staleTime: 0,
  });
  const listKey = ["chat-conversations", paperId];
  const conversations = useQuery({
    queryKey: listKey,
    queryFn: () => api.get<Conversation[]>("/chat/conversations", { paper_id: paperId }),
    enabled: active && status.data?.enabled === true,
  });

  const chat = useChatStream({
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: listKey });
      void queryClient.invalidateQueries({ queryKey: ["chat-status"] });
      // 応答の開始前に停止すると、サーバーが会話を保存するのはこの後（最初のトークンが来たとき）になる
      window.setTimeout(() => void queryClient.invalidateQueries({ queryKey: listKey }), 5000);
    },
  });

  const open = useMutation({
    mutationFn: (id: string) => api.get<ConversationDetail>(`/chat/conversations/${id}`),
    onSuccess: (c) => {
      chat.reset(c.id, fromConversation(c));
      setListOpen(false);
    },
    onError: (e) => toast.error(errorText(e)),
  });
  const remove = useMutation({
    mutationFn: (c: Conversation) => api.del(`/chat/conversations/${c.id}`),
    onSuccess: (_, c) => {
      if (chat.conversationId === c.id) chat.reset(null, []);
      void queryClient.invalidateQueries({ queryKey: listKey });
    },
    onError: (e) => toast.error(describeError(t, e)),
  });

  const enabled = status.data?.enabled === true;
  const canSend = enabled && !chat.streaming && draft.trim() !== "";

  async function submit() {
    if (!canSend) return;
    const text = draft.trim();
    setDraft("");
    const accepted = await chat.send(text, { body: { paper_id: paperId } });
    if (!accepted) setDraft((d) => d || text);
  }

  // 矩形選択からの解説。開いている会話があればその続きに、なければ新しい会話にする
  const handled = useRef<number | null>(null);
  const { send } = chat;
  const streamingRef = useRef(false);
  useEffect(() => {
    streamingRef.current = chat.streaming;
  });
  useEffect(() => {
    if (!explainRequest || handled.current === explainRequest.nonce) return;
    handled.current = explainRequest.nonce;
    // 回答の生成中は送れない（send は何もせず false を返す）。理由を伝える
    if (streamingRef.current) {
      toast.error(t.ai.explainBusy);
      return;
    }
    const { capture, question } = explainRequest;
    const text = [t.ai.explainMessage.replace("{n}", String(capture.page_number)), question].filter(Boolean).join("\n");
    void send(text, {
      path: `/papers/${paperId}/explain-region`,
      body: {
        page_number: capture.page_number,
        rect: capture.rect,
        image: capture.image,
        ...(question ? { question } : {}),
      },
    });
  }, [explainRequest, send, paperId, t]);

  const list = conversations.data ?? [];

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {status.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {errorText(status.error)}
        </p>
      ) : status.data && !status.data.enabled ? (
        <AiDisabledNotice />
      ) : null}

      {enabled && (
        <div className="space-y-1">
          <div className="flex items-center justify-between gap-2">
            <Button
              size="sm"
              variant="ghost"
              aria-expanded={listOpen}
              onClick={() => {
                if (!listOpen) void conversations.refetch();
                setListOpen((v) => !v);
              }}
            >
              {t.ai.conversations}
              {list.length > 0 && ` (${list.length})`}
              <ChevronDown className={cn("h-4 w-4 transition-transform", listOpen && "rotate-180")} />
            </Button>
            <div className="flex items-center gap-2">
              {status.data?.daily_requests_remaining != null && (
                <span className="text-[11px] text-muted-foreground">
                  {t.ai.remaining.replace("{n}", String(status.data.daily_requests_remaining))}
                </span>
              )}
              <Button
                size="sm"
                variant="outline"
                disabled={chat.streaming || (chat.messages.length === 0 && chat.conversationId === null)}
                onClick={() => chat.reset(null, [])}
              >
                <MessageSquarePlus className="h-4 w-4" />
                {t.ai.newConversation}
              </Button>
            </div>
          </div>
          {listOpen && (
            <ul className="max-h-40 overflow-auto rounded-md border">
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
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-auto">
        <MessageList messages={chat.messages} onCite={onCite} empty={enabled ? t.ai.empty : undefined} />
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
          aria-label={t.ai.placeholder}
          placeholder={t.ai.placeholder}
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
