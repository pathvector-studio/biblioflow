"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, describeError } from "@/lib/api";
import { fmt, useI18n } from "@/lib/i18n";
import { useWorkspace } from "@/lib/session";
import type { ImportFolder, ImportJob } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";

const isActive = (j: ImportJob) => j.status === "queued" || j.status === "running";

export function ImportFolderPanel() {
  const { t, locale } = useI18n();
  const { current } = useWorkspace();
  const queryClient = useQueryClient();
  const [source, setSource] = useState("");
  const base = `/workspaces/${current.id}`;
  const folderPath = `data/import/${current.slug}/`;

  const folder = useQuery({
    queryKey: ["import-folder", current.id],
    queryFn: () => api.get<ImportFolder>(`${base}/import-folder`),
  });
  const jobs = useQuery({
    queryKey: ["import-jobs", current.id],
    queryFn: () => api.get<ImportJob[]>(`${base}/import-jobs`),
    refetchInterval: (query) => (query.state.data?.some(isActive) ? 2000 : false),
  });

  const running = (jobs.data ?? []).some(isActive);
  const files = folder.data?.files ?? [];
  const chosen = files.some((f) => f.name === source) ? source : (files[0]?.name ?? "");

  // 実行中だった取り込みが終わったら、一覧・コレクション・タグを読み直す
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) {
      void queryClient.invalidateQueries({ queryKey: ["papers"] });
      void queryClient.invalidateQueries({ queryKey: ["collections"] });
      void queryClient.invalidateQueries({ queryKey: ["tags"] });
    }
    wasRunning.current = running;
  }, [running, queryClient]);

  const start = useMutation({
    mutationFn: () => api.post<ImportJob>(`${base}/import-jobs`, { source: chosen }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["import-jobs", current.id] }),
    onError: (e) => {
      toast.error(describeError(t, e, { 409: t.importFolder.running, 429: t.importFolder.rateLimited, 503: t.importFolder.queueError }));
      void queryClient.invalidateQueries({ queryKey: ["import-jobs", current.id] });
    },
  });

  return (
    <section className="space-y-3 lg:col-span-2">
      <h2 className="text-base font-semibold">{t.importFolder.title}</h2>
      <p className="text-sm text-muted-foreground">
        {fmt(t.importFolder.hint, { folder: folderPath, workspace: current.name })}
      </p>

      {folder.isError && <p className="text-sm text-destructive">{describeError(t, folder.error)}</p>}
      {folder.data && !folder.data.exists && (
        <p className="text-sm text-muted-foreground">{fmt(t.importFolder.noFolder, { folder: folderPath })}</p>
      )}
      {folder.data?.exists && files.length === 0 && (
        <p className="text-sm text-muted-foreground">{t.importFolder.noFiles}</p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {files.length > 0 && (
          <Select
            aria-label={t.importFolder.choose}
            value={chosen}
            onChange={(e) => setSource(e.target.value)}
            className="max-w-full"
          >
            {files.map((f) => (
              <option key={f.name} value={f.name}>
                {f.name} ({t.import.formats[f.format]}, {Math.max(1, Math.round(f.size / 1024))} KiB)
              </option>
            ))}
          </Select>
        )}
        <Button type="button" variant="outline" disabled={folder.isFetching} onClick={() => void folder.refetch()}>
          {t.importFolder.refresh}
        </Button>
        {files.length > 0 && (
          <Button type="button" disabled={!chosen || running || start.isPending} onClick={() => start.mutate()}>
            {start.isPending ? t.importFolder.starting : t.importFolder.start}
          </Button>
        )}
      </div>
      {running && <p className="text-xs text-muted-foreground">{t.importFolder.running}</p>}
      <p className="text-xs text-muted-foreground">{t.importFolder.note}</p>

      <div className="space-y-2">
        <h3 className="text-sm font-medium">{t.importFolder.jobs}</h3>
        {jobs.isError && <p className="text-sm text-destructive">{describeError(t, jobs.error)}</p>}
        {jobs.data?.length === 0 && <p className="text-sm text-muted-foreground">{t.importFolder.noJobs}</p>}
        {(jobs.data ?? []).map((j) => (
          <JobCard key={j.id} job={j} locale={locale} />
        ))}
      </div>
    </section>
  );
}

function JobCard({ job, locale }: { job: ImportJob; locale: string }) {
  const { t } = useI18n();
  const pct = job.with_attachments > 0 ? Math.round((job.pdfs_processed / job.with_attachments) * 100) : 100;
  return (
    <div className="space-y-1.5 rounded-md border p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <code className="break-all text-xs">{job.source}</code>
        <span className={job.status === "failed" ? "text-destructive" : "text-muted-foreground"}>
          {t.importFolder.status[job.status]}
          <span className="ml-2 text-xs">{new Date(job.created_at).toLocaleString(locale)}</span>
        </span>
      </div>
      {job.error && <p className="text-xs text-destructive">{job.error}</p>}
      {job.status !== "queued" && !job.error && (
        <>
          <p className="text-xs">
            {fmt(t.importFolder.counts, { new: job.new, matched: job.matched, unreadable: job.unreadable, total: job.total })}
          </p>
          {job.with_attachments > 0 && (
            <>
              <p className="text-xs">
                {fmt(t.importFolder.pdfs, {
                  processed: job.pdfs_processed,
                  withFiles: job.with_attachments,
                  attached: job.pdfs_attached,
                  failed: job.pdfs_failed,
                })}
              </p>
              {job.status === "running" && (
                <div
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={pct}
                  className="h-1.5 w-full overflow-hidden rounded bg-muted"
                >
                  <div className="h-full bg-primary transition-all" style={{ width: `${pct}%` }} />
                </div>
              )}
            </>
          )}
        </>
      )}
      {job.problems.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer font-medium">
            {t.importFolder.problems} ({job.problems.length})
          </summary>
          <ul className="mt-1 space-y-0.5">
            {job.problems.map((p, i) => (
              <li key={`${p.ref}-${i}`}>
                <span className="text-muted-foreground">
                  [{p.stage === "pdf" ? t.importFolder.problemPdf : t.importFolder.problemRecord}]
                </span>{" "}
                <span className="font-mono">{p.ref}</span>
                <span className="text-muted-foreground"> — {p.reason}</span>
              </li>
            ))}
          </ul>
          {job.problems.length >= 100 && <p className="text-muted-foreground">{t.importFolder.problemsCapped}</p>}
        </details>
      )}
      {job.status === "done" && (
        <Button asChild size="sm" variant="outline">
          <Link href="/library">{t.importFolder.openLibrary}</Link>
        </Button>
      )}
    </div>
  );
}
