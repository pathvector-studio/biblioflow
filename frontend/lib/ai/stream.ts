import { ApiError } from "@/lib/api";
import { SseParser, type SseEvent } from "@/lib/ai/sse";
import type { ChatSource, Citation } from "@/lib/types";

export interface ChatMeta {
  conversation_id: string;
  message_id: string;
  degraded: boolean;
  sources: ChatSource[];
}

export interface ChatDone {
  message_id: string;
  content: string;
  citations: Citation[];
}

export interface ChatStreamError {
  code: string;
  message: string;
}

export interface StreamHandlers {
  onMeta?: (m: ChatMeta) => void;
  onDelta?: (text: string) => void;
  onDone?: (d: ChatDone) => void;
  onError?: (e: ChatStreamError) => void;
}

/**
 * POST して SSE を読む（EventSource は POST できない）。開始前の失敗は JSON の通常エラーなので ApiError で投げる。
 * 中断（signal.abort）は AbortError のまま投げるので、呼び出し側で判定する。
 */
export async function postEventStream(
  path: string,
  body: unknown,
  handlers: StreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if (signal.aborted) throw e;
    throw new ApiError(0, null);
  }
  if (!res.ok) {
    const j: unknown = await res.json().catch(() => null);
    const d = j && typeof j === "object" && "detail" in j ? (j as { detail: unknown }).detail : null;
    throw new ApiError(res.status, typeof d === "string" ? d : null);
  }
  if (!res.body) throw new ApiError(0, null);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  const dispatch = (events: SseEvent[]) => {
    for (const ev of events) {
      let data: unknown;
      try {
        data = JSON.parse(ev.data);
      } catch {
        continue;
      }
      if (ev.event === "meta") handlers.onMeta?.(data as ChatMeta);
      else if (ev.event === "delta") handlers.onDelta?.((data as { text: string }).text);
      else if (ev.event === "done") handlers.onDone?.(data as ChatDone);
      else if (ev.event === "error") handlers.onError?.(data as ChatStreamError);
    }
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    dispatch(parser.push(decoder.decode(value, { stream: true })));
  }
  dispatch(parser.push(decoder.decode() + "\n\n"));
}
