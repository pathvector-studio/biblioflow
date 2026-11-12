"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy, NotebookPen, Square, X } from "lucide-react";
import { postEventStream } from "@/lib/ai/stream";
import { useAiErrorText } from "@/components/ai/use-chat-stream";
import { copyText } from "@/lib/clipboard";
import { useI18n } from "@/lib/i18n";
import { Button } from "@/components/ui/button";

/** サーバーの上限（TranslateRequest.text）と同じ。超えたら送らない */
export const TRANSLATE_MAX_CHARS = 4000;

export const TRANSLATE_LANGS = ["ja", "en", "zh", "ko", "fr", "de", "es"] as const;

/** 選んだ文章の翻訳の要求。呼び出し側は nonce を key にして、選ぶたびにパネルを作り直す（言語も画面の言語に戻る）。anchor はパネルの左上（PDF 領域内の px） */
export interface TranslateRequest {
  text: string;
  page: number;
  anchor: { left: number; top: number };
  nonce: number;
}

/** 文字数（サーバーはコードポイントで数える） */
export function countChars(text: string): number {
  return Array.from(text).length;
}

/** 原文の引用と訳を、ノートに足す Markdown にする */
export function noteMarkdown(original: string, translation: string): string {
  const quote = original
    .trim()
    .split(/\r?\n/)
    .map((l) => `> ${l}`)
    .join("\n");
  return `${quote}\n\n${translation.trim()}\n`;
}

type Phase = "streaming" | "done" | "stopped" | "error";

export function TranslatePanel({
  paperId,
  request,
  canEdit,
  onAddToNote,
  onClose,
}: {
  paperId: string;
  request: TranslateRequest;
  canEdit: boolean;
  onAddToNote: (markdown: string) => void;
  onClose: () => void;
}) {
  const { t, locale } = useI18n();
  const errorText = useAiErrorText();
  const [lang, setLang] = useState<string>(locale);
  const [output, setOutput] = useState("");
  const [phase, setPhase] = useState<Phase>("streaming");
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<"ok" | "failed" | null>(null);
  const [added, setAdded] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const chars = countChars(request.text.trim());
  const tooLong = chars > TRANSLATE_MAX_CHARS;
  const text = request.text.trim();

  useEffect(() => {
    if (tooLong) return;
    const ctrl = new AbortController();
    abort.current = ctrl;
    let settled = false;
    postEventStream(
      `/papers/${paperId}/translate`,
      { text, target_lang: lang, page: request.page },
      {
        onDelta: (d) => setOutput((o) => o + d),
        onDone: (d) => {
          settled = true;
          setOutput(d.content);
          setPhase("done");
        },
        onError: () => {
          settled = true;
          setError(t.translate.failed);
          setPhase("error");
        },
      },
      ctrl.signal,
    )
      .then(() => {
        if (!settled && !ctrl.signal.aborted) {
          setError(t.translate.failed);
          setPhase("error");
        }
      })
      .catch((e: unknown) => {
        if (ctrl.signal.aborted) return;
        setError(errorText(e));
        setPhase("error");
      });
    return () => ctrl.abort();
    // 言語を変えるか、新しい選択のときだけ始め直す
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paperId, text, request.page, request.nonce, lang, tooLong]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const stop = useCallback(() => {
    abort.current?.abort();
    setPhase("stopped");
  }, []);

  async function copy() {
    setCopied((await copyText(output)) ? "ok" : "failed");
  }

  const streaming = phase === "streaming" && !tooLong;
  const hasOutput = output.trim().length > 0;

  return (
    <div
      role="dialog"
      aria-label={t.translate.title}
      data-testid="translate-panel"
      className="absolute z-20 flex max-h-[70%] w-[22rem] max-w-[calc(100%-1rem)] flex-col gap-2 rounded-md border bg-popover p-3 text-sm text-popover-foreground shadow-lg"
      style={{ left: request.anchor.left, top: request.anchor.top }}
    >
      <div className="flex items-center justify-between gap-2">
        <label className="flex items-center gap-1 text-xs text-muted-foreground">
          {t.translate.target}
          <select
            aria-label={t.translate.target}
            className="h-7 rounded-md border bg-background px-1 text-xs text-foreground"
            value={lang}
            onChange={(e) => {
              // 言語を変えたら、前の訳を消して訳し直す（effect が新しい言語で始める）
              setOutput("");
              setError(null);
              setCopied(null);
              setPhase("streaming");
              setLang(e.target.value);
            }}
          >
            {TRANSLATE_LANGS.map((l) => (
              <option key={l} value={l}>
                {t.translate.langs[l]}
              </option>
            ))}
          </select>
        </label>
        <Button size="icon" variant="ghost" className="h-6 w-6" aria-label={t.translate.close} onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>
      {tooLong ? (
        <p role="alert" className="text-xs text-destructive">
          {t.translate.tooLong.replace("{count}", String(chars)).replace("{max}", String(TRANSLATE_MAX_CHARS))}
        </p>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          {hasOutput ? (
            <p data-testid="translate-output" lang={lang} className="whitespace-pre-wrap leading-relaxed">
              {output}
            </p>
          ) : (
            streaming && <p className="text-xs text-muted-foreground">{t.translate.working}</p>
          )}
          {phase === "stopped" && <p className="mt-1 text-xs text-muted-foreground">{t.translate.stopped}</p>}
          {error && (
            <p role="alert" className="mt-1 text-xs text-destructive">
              {error}
            </p>
          )}
        </div>
      )}
      <div className="flex flex-wrap justify-end gap-1">
        {streaming && (
          <Button size="sm" variant="outline" onClick={stop}>
            <Square className="h-3.5 w-3.5" />
            {t.translate.stop}
          </Button>
        )}
        <Button size="sm" variant="outline" disabled={!hasOutput} onClick={() => void copy()}>
          {copied === "ok" ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          {copied === "ok" ? t.translate.copied : copied === "failed" ? t.translate.copyFailed : t.translate.copy}
        </Button>
        {canEdit && (
          <Button
            size="sm"
            disabled={!hasOutput || streaming || added}
            onClick={() => {
              onAddToNote(noteMarkdown(text, output));
              setAdded(true);
            }}
          >
            <NotebookPen className="h-3.5 w-3.5" />
            {added ? t.translate.addedToNote : t.translate.addToNote}
          </Button>
        )}
      </div>
    </div>
  );
}
