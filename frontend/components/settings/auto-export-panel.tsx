"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, describeError } from "@/lib/api";
import { flattenCollections } from "@/lib/collections";
import { useI18n } from "@/lib/i18n";
import { useCollections } from "@/lib/queries";
import { useWorkspace } from "@/lib/session";
import { PAPER_STATUSES, type AutoExport, type ExportFormat, type PaperStatus, type Workspace } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const FORMATS: { value: ExportFormat; ext: string }[] = [
  { value: "bibtex", ext: "bib" },
  { value: "ris", ext: "ris" },
  { value: "csljson", ext: "json" },
];

export function AutoExportPanel() {
  const { t } = useI18n();
  const { workspaces } = useWorkspace();
  return (
    <section className="space-y-4">
      <h2 className="text-base font-semibold">{t.settings.autoExportTitle}</h2>
      <p className="text-sm text-muted-foreground">{t.settings.autoExportHelp}</p>
      {workspaces.map((w) => (
        <WorkspaceAutoExports key={w.id} workspace={w} />
      ))}
    </section>
  );
}

function WorkspaceAutoExports({ workspace }: { workspace: Workspace }) {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const canEdit = workspace.role === "editor" || workspace.role === "owner";
  const key = ["auto-exports", workspace.id];
  const list = useQuery({
    queryKey: key,
    queryFn: () => api.get<AutoExport[]>(`/workspaces/${workspace.id}/auto-exports`),
    refetchInterval: 15_000,
  });
  const collections = useCollections(workspace.id);
  const flat = flattenCollections(collections.data ?? []);

  const [format, setFormat] = useState<ExportFormat>("bibtex");
  const [filename, setFilename] = useState("");
  const [tag, setTag] = useState("");
  const [q, setQ] = useState("");
  const [year, setYear] = useState("");
  const [status, setStatus] = useState<PaperStatus | "">("");
  const [collectionId, setCollectionId] = useState("");
  const [deleting, setDeleting] = useState<AutoExport | null>(null);

  const ext = FORMATS.find((f) => f.value === format)?.ext ?? "bib";
  const collectionName = (id: string) => collections.data?.find((c) => c.id === id)?.name ?? id.slice(0, 8);

  const add = useMutation({
    mutationFn: () =>
      api.post<AutoExport>(`/workspaces/${workspace.id}/auto-exports`, {
        format,
        filename: filename.trim(),
        tag: tag.trim() || null,
        q: q.trim() || null,
        year: year.trim() ? Number(year) : null,
        status: status || null,
        collection_id: collectionId || null,
      }),
    onSuccess: () => {
      setFilename("");
      void queryClient.invalidateQueries({ queryKey: key });
    },
    onError: (e) => toast.error(describeError(t, e)),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/workspaces/${workspace.id}/auto-exports/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
    onError: (e) => toast.error(describeError(t, e)),
  });

  const filtersOf = (a: AutoExport): string => {
    const parts: string[] = [];
    if (a.collection_id) parts.push(`${t.settings.autoExportCollection}: ${collectionName(a.collection_id)}`);
    if (a.tag) parts.push(`${t.settings.autoExportTag}: ${a.tag}`);
    if (a.status) parts.push(`${t.settings.autoExportStatus}: ${t.status[a.status]}`);
    if (a.year) parts.push(`${t.settings.autoExportYear}: ${a.year}`);
    if (a.q) parts.push(`${t.settings.autoExportQuery}: ${a.q}`);
    return parts.length ? parts.join(" / ") : t.settings.autoExportAllPapers;
  };

  const yearValid = !year.trim() || (/^\d{1,4}$/.test(year.trim()) && Number(year) >= 1);
  const nameValid = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(filename.trim()) && filename.trim().endsWith(`.${ext}`);

  return (
    <div className="space-y-3 rounded-md border p-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-medium">{workspace.name}</h3>
        <span className="text-xs text-muted-foreground">{t.roles[workspace.role]}</span>
      </div>

      {list.isError && <p className="text-sm text-destructive">{describeError(t, list.error)}</p>}
      {list.data?.length === 0 && <p className="text-sm text-muted-foreground">{t.settings.autoExportNone}</p>}
      {(list.data ?? []).map((a) => (
        <div key={a.id} className="space-y-0.5 border-t pt-2 text-sm first:border-t-0 first:pt-0">
          <div className="flex items-center justify-between gap-2">
            <code className="break-all text-xs">
              data/exports/{a.path}
            </code>
            {canEdit && (
              <Button size="sm" variant="outline" onClick={() => setDeleting(a)}>
                {t.settings.autoExportDelete}
              </Button>
            )}
          </div>
          <p className="text-xs text-muted-foreground">{filtersOf(a)}</p>
          <p className="text-xs text-muted-foreground">
            {a.last_written_at
              ? `${t.settings.autoExportWritten}: ${new Date(a.last_written_at).toLocaleString(locale)}${
                  a.paper_count !== null ? ` (${a.paper_count} ${t.settings.autoExportPapers})` : ""
                }`
              : t.settings.autoExportPending}
          </p>
          {a.last_error && (
            <p className="text-xs text-destructive">
              {t.settings.autoExportError}: {a.last_error}
            </p>
          )}
        </div>
      ))}

      {canEdit ? (
        <form
          className="flex flex-wrap items-end gap-2 border-t pt-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (nameValid && yearValid) add.mutate();
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor={`ae-format-${workspace.id}`}>{t.settings.autoExportFormat}</Label>
            <Select
              id={`ae-format-${workspace.id}`}
              value={format}
              onChange={(e) => setFormat(e.target.value as ExportFormat)}
            >
              <option value="bibtex">BibTeX (.bib)</option>
              <option value="ris">RIS (.ris)</option>
              <option value="csljson">CSL JSON (.json)</option>
            </Select>
          </div>
          <div className="min-w-40 flex-1 space-y-1.5">
            <Label htmlFor={`ae-name-${workspace.id}`}>{t.settings.autoExportFilename}</Label>
            <Input
              id={`ae-name-${workspace.id}`}
              required
              maxLength={100}
              placeholder={`refs.${ext}`}
              title={t.settings.autoExportFilenameHint}
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`ae-col-${workspace.id}`}>{t.settings.autoExportCollection}</Label>
            <Select id={`ae-col-${workspace.id}`} value={collectionId} onChange={(e) => setCollectionId(e.target.value)}>
              <option value="">{t.settings.autoExportAny}</option>
              {flat.map(({ collection, depth }) => (
                <option key={collection.id} value={collection.id}>
                  {"  ".repeat(depth)}
                  {collection.name}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`ae-status-${workspace.id}`}>{t.settings.autoExportStatus}</Label>
            <Select
              id={`ae-status-${workspace.id}`}
              value={status}
              onChange={(e) => setStatus(e.target.value as PaperStatus | "")}
            >
              <option value="">{t.settings.autoExportAny}</option>
              {PAPER_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {t.status[s]}
                </option>
              ))}
            </Select>
          </div>
          <div className="w-32 space-y-1.5">
            <Label htmlFor={`ae-tag-${workspace.id}`}>{t.settings.autoExportTag}</Label>
            <Input id={`ae-tag-${workspace.id}`} maxLength={100} value={tag} onChange={(e) => setTag(e.target.value)} />
          </div>
          <div className="w-20 space-y-1.5">
            <Label htmlFor={`ae-year-${workspace.id}`}>{t.settings.autoExportYear}</Label>
            <Input
              id={`ae-year-${workspace.id}`}
              inputMode="numeric"
              maxLength={4}
              value={year}
              onChange={(e) => setYear(e.target.value)}
            />
          </div>
          <div className="w-40 space-y-1.5">
            <Label htmlFor={`ae-q-${workspace.id}`}>{t.settings.autoExportQuery}</Label>
            <Input id={`ae-q-${workspace.id}`} maxLength={200} value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <Button type="submit" disabled={add.isPending || !nameValid || !yearValid}>
            {t.settings.autoExportAdd}
          </Button>
          <p className="w-full text-xs text-muted-foreground">{t.settings.autoExportFilenameHint}</p>
        </form>
      ) : (
        <p className="text-xs text-muted-foreground">{t.settings.autoExportReadOnly}</p>
      )}

      <AlertDialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogTitle>{t.settings.autoExportDeleteTitle}</AlertDialogTitle>
          <AlertDialogDescription>
            {deleting?.path}
            <br />
            {t.settings.autoExportDeleteBody}
          </AlertDialogDescription>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction onClick={() => deleting && remove.mutate(deleting.id)}>
              {t.settings.autoExportDelete}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
