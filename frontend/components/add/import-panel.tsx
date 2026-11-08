"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { describeError, importBibliography } from "@/lib/api";
import { fmt, useI18n } from "@/lib/i18n";
import { useWorkspace } from "@/lib/session";
import type { ImportSummary } from "@/lib/types";
import { Button } from "@/components/ui/button";

export function ImportPanel() {
  const { t } = useI18n();
  const { current } = useWorkspace();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportSummary | null>(null);
  const [result, setResult] = useState<ImportSummary | null>(null);

  const overrides = { 413: t.import.tooLarge, 422: t.import.unsupported, 429: t.import.rateLimited };

  const run = useMutation({
    mutationFn: ({ f, dryRun }: { f: File; dryRun: boolean }) => importBibliography(f, current.id, dryRun),
    onSuccess: (summary) => {
      if (summary.dry_run) {
        setPreview(summary);
      } else {
        setResult(summary);
        setPreview(null);
        void queryClient.invalidateQueries({ queryKey: ["papers"] });
        void queryClient.invalidateQueries({ queryKey: ["collections"] });
        void queryClient.invalidateQueries({ queryKey: ["tags"] });
      }
    },
    onError: (e) => {
      // 422 のうち「項目が多すぎる」はサーバーの文言を見て切り替える
      const tooMany = e instanceof Error && /too many records/i.test(e.message);
      toast.error(tooMany ? t.import.tooMany : describeError(t, e, overrides));
    },
  });

  function reset() {
    setFile(null);
    setPreview(null);
    setResult(null);
    if (input.current) input.current.value = "";
  }

  function choose(f: File | undefined) {
    setPreview(null);
    setResult(null);
    setFile(f ?? null);
    if (f) run.mutate({ f, dryRun: true });
  }

  const counts = (s: ImportSummary) => (
    <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm font-medium">
      <span>{fmt(t.import.countNew, { n: s.new })}</span>
      <span>{fmt(t.import.countMatched, { n: s.matched })}</span>
      <span className={s.unreadable > 0 ? "text-destructive" : undefined}>
        {fmt(t.import.countUnreadable, { n: s.unreadable })}
      </span>
    </p>
  );

  return (
    <section className="space-y-3 lg:col-span-2">
      <h2 className="text-base font-semibold">{t.import.title}</h2>
      <p className="text-sm text-muted-foreground">{fmt(t.import.hint, { workspace: current.name })}</p>

      {!result && (
        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={input}
            type="file"
            accept=".bib,.bibtex,.ris,.json,.rdf,.xml"
            className="hidden"
            aria-label={t.import.choose}
            onChange={(e) => choose(e.target.files?.[0])}
          />
          <Button type="button" variant="outline" disabled={run.isPending} onClick={() => input.current?.click()}>
            {t.import.choose}
          </Button>
          {file && <span className="text-sm text-muted-foreground">{file.name}</span>}
          {file && !run.isPending && (
            <Button type="button" variant="ghost" size="sm" onClick={() => run.mutate({ f: file, dryRun: true })}>
              {t.import.preview}
            </Button>
          )}
        </div>
      )}

      {preview && file && (
        <div className="space-y-3 rounded-md border p-3">
          <p className="text-xs text-muted-foreground">
            {t.import.format}: {t.import.formats[preview.format]}
          </p>
          {counts(preview)}
          {preview.matched > 0 && <p className="text-xs text-muted-foreground">{t.import.matchedNote}</p>}
          {preview.with_attachments > 0 && (
            <p className="text-xs text-muted-foreground">{fmt(t.import.attachmentsNote, { n: preview.with_attachments })}</p>
          )}
          {preview.problems.length > 0 && (
            <div className="space-y-1">
              <h3 className="text-sm font-medium">{t.import.problems}</h3>
              <ul className="space-y-0.5 text-xs">
                {preview.problems.map((p, i) => (
                  <li key={`${p.ref}-${i}`}>
                    <span className="font-mono">{p.ref}</span>
                    {p.reason && <span className="text-muted-foreground"> — {p.reason}</span>}
                  </li>
                ))}
              </ul>
              {preview.problems_truncated && <p className="text-xs text-muted-foreground">{t.import.problemsTruncated}</p>}
            </div>
          )}
          {preview.items.length > 0 && (
            <div className="space-y-1">
              <h3 className="text-sm font-medium">{t.import.sample}</h3>
              <ul className="space-y-0.5 text-xs">
                {preview.items.map((p, i) => (
                  <li key={`${p.ref}-${i}`} className="flex gap-2">
                    <span className="w-10 shrink-0 text-muted-foreground">
                      {p.status === "matched" ? t.import.matchedBadge : t.import.newBadge}
                    </span>
                    <span className="truncate">{p.title}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="flex gap-2">
            <Button
              type="button"
              disabled={run.isPending || preview.new + preview.matched === 0}
              onClick={() => run.mutate({ f: file, dryRun: false })}
            >
              {t.import.confirm}
            </Button>
            <Button type="button" variant="ghost" disabled={run.isPending} onClick={reset}>
              {t.import.cancel}
            </Button>
          </div>
        </div>
      )}

      {result && (
        <div className="space-y-2 rounded-md border p-3 text-sm">
          <p className="font-medium">
            {fmt(t.import.done, { new: result.new, matched: result.matched, unreadable: result.unreadable })}
          </p>
          <p className="text-xs text-muted-foreground">
            {fmt(t.import.doneDetail, {
              tags: result.tags_added,
              collections: result.collections_created,
              notes: result.notes_added,
            })}
          </p>
          {result.problems.length > 0 && (
            <ul className="space-y-0.5 text-xs">
              {result.problems.map((p, i) => (
                <li key={`${p.ref}-${i}`}>
                  <span className="font-mono">{p.ref}</span>
                  {p.reason && <span className="text-muted-foreground"> — {p.reason}</span>}
                </li>
              ))}
            </ul>
          )}
          <div className="flex gap-2">
            <Button asChild>
              <Link href="/library">{t.import.openLibrary}</Link>
            </Button>
            <Button type="button" variant="ghost" onClick={reset}>
              {t.import.cancel}
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
