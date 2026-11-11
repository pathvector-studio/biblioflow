"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { api, describeError } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { PaperDetail } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Textarea } from "@/components/ui/textarea";

type SaveState = "saved" | "dirty" | "saving" | "error";

export function NotesTab({ paper, canEdit }: { paper: PaperDetail; canEdit: boolean }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [text, setText] = useState(paper.notes_md);
  const [state, setState] = useState<SaveState>("saved");
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);
  const latest = useRef(paper.notes_md);
  const saved = useRef(paper.notes_md);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const persist = useCallback(
    async (body: string) => {
      await api.patch(`/papers/${paper.id}`, { notes_md: body });
      saved.current = body;
      queryClient.setQueryData<PaperDetail>(["paper", paper.id], (old) => (old ? { ...old, notes_md: body } : old));
    },
    [paper.id, queryClient],
  );

  const save = useCallback(async () => {
    const body = latest.current;
    if (body === saved.current) return;
    setState("saving");
    try {
      await persist(body);
      setError(null);
      setState(latest.current === body ? "saved" : "dirty");
    } catch (e) {
      setError(describeError(t, e));
      setState("error");
    }
  }, [persist, t]);

  function onChange(value: string) {
    setText(value);
    latest.current = value;
    setState("dirty");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void save(), 1000);
  }

  // 画面を離れるときに未保存の変更があればその場で送る
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
      if (latest.current !== saved.current) void persist(latest.current).catch(() => undefined);
    },
    [persist],
  );

  const label: Record<SaveState, string> = {
    saved: t.notes.saved,
    dirty: t.notes.unsaved,
    saving: t.notes.saving,
    error: error ?? t.notes.saveFailed,
  };

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-center justify-between">
        <div className="flex rounded-md bg-muted p-0.5 text-xs">
          {[false, true].map((p) => (
            <button
              key={String(p)}
              type="button"
              onClick={() => setPreview(p)}
              className={cn("rounded px-2 py-1", preview === p && "bg-background shadow")}
            >
              {p ? t.notes.preview : t.notes.edit}
            </button>
          ))}
        </div>
        <span
          role="status"
          className={cn("text-xs text-muted-foreground", state === "error" && "text-destructive")}
        >
          {label[state]}
        </span>
      </div>
      {preview ? (
        <div className="markdown min-h-0 flex-1 overflow-auto rounded-md border p-3">
          {text.trim() ? (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[rehypeSanitize]}
              components={{
                // 外部の画像は自動で読み込まない（共有ワークスペースのほかの人や MCP が書いた画像の URL で、
                // 開いた人の IP やアクセスした時刻が外に伝わるため）。押せば開けるリンクにする。
                img: ({ src, alt }) =>
                  typeof src === "string" && src ? (
                    <a href={src} target="_blank" rel="noopener noreferrer">
                      [{t.notes.image}: {alt || src}]
                    </a>
                  ) : null,
              }}
            >
              {text}
            </ReactMarkdown>
          ) : (
            <p className="text-muted-foreground">{t.notes.empty}</p>
          )}
        </div>
      ) : (
        <Textarea
          className="min-h-0 flex-1 resize-none font-mono text-xs"
          aria-label={t.reader.tabNotes}
          placeholder={t.notes.placeholder}
          readOnly={!canEdit}
          value={text}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </div>
  );
}
