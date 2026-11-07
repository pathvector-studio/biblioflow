"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, describeError } from "@/lib/api";
import { postEventStream } from "@/lib/ai/stream";
import { useI18n } from "@/lib/i18n";
import type { ChatMessage, Citation, ConversationDetail } from "@/lib/types";

export type UiMessageStatus = "streaming" | "complete" | "error" | "cancelled";

export interface UiMessage {
  key: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  status: UiMessageStatus;
  /** 検索がキーワードだけに劣化していた回答 */
  degraded?: boolean;
  /** error のとき、回答の下に出す理由 */
  notice?: string;
}

export function fromStored(m: ChatMessage): UiMessage {
  return { key: m.id, role: m.role, content: m.content, citations: m.citations ?? [], status: m.status };
}

export function fromConversation(c: ConversationDetail): UiMessage[] {
  return c.messages.map(fromStored);
}

/** ストリームの前後で起きた失敗を、画面に出す文言にする（API のエラーコードごと） */
export function useAiErrorText() {
  const { t } = useI18n();
  return useCallback(
    (e: unknown): string => {
      if (e instanceof ApiError) {
        if (e.status === 503 && e.detail === "llm_disabled") return t.ai.disabledTitle;
        if (e.status === 429) return e.detail === "daily_limit" ? t.ai.dailyLimit : t.ai.rateLimited;
        if (e.status === 502) return t.ai.unavailable;
        if (e.status === 501) return t.ai.visionUnsupported;
        if (e.status === 503) return t.ai.serverError;
      }
      return describeError(t, e);
    },
    [t],
  );
}

function streamErrorText(t: ReturnType<typeof useI18n>["t"], code: string): string {
  if (code === "limit") return t.ai.dailyLimit;
  return t.ai.unavailable;
}

export interface SendRequest {
  /** 既定は `/chat` */
  path?: string;
  /** 既定の `/chat` では `message` が付く。どちらでも `conversation_id` は続きなら付く */
  body: Record<string, unknown>;
}

/**
 * AI の質問 1 回分（SSE）を管理する。単一論文のタブでも、横断の画面でも使う。
 * 流れている間は delta を足して見せ、done が来たらその content と citations を正とする。
 */
export function useChatStream(opts: { onSettled?: (conversationId: string | null) => void } = {}) {
  const { t } = useI18n();
  const errorText = useAiErrorText();
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const convRef = useRef<string | null>(null);
  const onSettled = useRef(opts.onSettled);
  useEffect(() => {
    onSettled.current = opts.onSettled;
  });

  const setConversation = useCallback((id: string | null) => {
    convRef.current = id;
    setConversationId(id);
  }, []);

  const stop = useCallback(() => abort.current?.abort(), []);

  // 画面を離れたら切断する（サーバーは cancelled で保存する）
  useEffect(() => () => abort.current?.abort(), []);

  const reset = useCallback(
    (id: string | null, loaded: UiMessage[]) => {
      abort.current?.abort();
      setConversation(id);
      setMessages(loaded);
      setError(null);
    },
    [setConversation],
  );

  const send = useCallback(
    async (text: string, request: SendRequest): Promise<boolean> => {
      if (abort.current) return false;
      const ctrl = new AbortController();
      abort.current = ctrl;
      const stamp = Date.now();
      const answerKey = `a-${stamp}`;
      const patchAnswer = (patch: Partial<UiMessage>) =>
        setMessages((old) => old.map((m) => (m.key === answerKey ? { ...m, ...patch } : m)));
      setError(null);
      setStreaming(true);
      setMessages((old) => [
        ...old,
        { key: `u-${stamp}`, role: "user", content: text, citations: [], status: "complete" },
        { key: answerKey, role: "assistant", content: "", citations: [], status: "streaming" },
      ]);
      let partial = "";
      let finished = false;
      let accepted = true;
      const body: Record<string, unknown> = { ...request.body, ...(request.path ? {} : { message: text }) };
      if (convRef.current && body.conversation_id === undefined) body.conversation_id = convRef.current;
      try {
        await postEventStream(
          request.path ?? "/chat",
          body,
          {
            onMeta: (m) => {
              setConversation(m.conversation_id);
              patchAnswer({ degraded: m.degraded });
            },
            onDelta: (d) => {
              partial += d;
              patchAnswer({ content: partial });
            },
            onDone: (d) => {
              finished = true;
              partial = d.content;
              patchAnswer({ content: d.content, citations: d.citations, status: "complete" });
            },
            onError: (e) => {
              finished = true;
              patchAnswer({ content: partial, status: "error", notice: streamErrorText(t, e.code) });
            },
          },
          ctrl.signal,
        );
        if (!finished) patchAnswer({ content: partial, status: "error", notice: t.ai.answerFailed });
      } catch (e) {
        if (ctrl.signal.aborted) {
          patchAnswer({ content: partial, status: "cancelled" });
        } else if (e instanceof ApiError && e.status >= 400 && !partial) {
          // 開始前の失敗は通常の HTTP エラー。質問も回答の枠も取り下げ、入力欄の上に理由を出す
          setMessages((old) => old.filter((m) => m.key !== answerKey && m.key !== `u-${stamp}`));
          setError(errorText(e));
          accepted = false;
        } else {
          patchAnswer({ content: partial, status: "error", notice: errorText(e) });
        }
      } finally {
        abort.current = null;
        setStreaming(false);
        onSettled.current?.(convRef.current);
      }
      return accepted;
    },
    [errorText, setConversation, t],
  );

  return { messages, conversationId, streaming, error, send, stop, reset };
}
