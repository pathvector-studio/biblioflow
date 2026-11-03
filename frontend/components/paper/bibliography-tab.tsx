"use client";

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ClipboardCopy, Download } from "lucide-react";
import { toast } from "sonner";
import { api, describeError } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { flattenCollections } from "@/lib/collections";
import { authorsToText, textToAuthors } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { useCollections, useTags } from "@/lib/queries";
import { useWorkspace } from "@/lib/session";
import { PAPER_STATUSES, type PaperDetail, type PaperPatch, type PaperStatus } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  );
}

export function BibliographyTab({ paper }: { paper: PaperDetail }) {
  const { t } = useI18n();
  const { current, canEdit } = useWorkspace();
  const queryClient = useQueryClient();
  const tags = useTags(current.id);
  const collections = useCollections(current.id);
  const flat = useMemo(() => flattenCollections(collections.data ?? []), [collections.data]);

  const [title, setTitle] = useState(paper.title);
  const [authors, setAuthors] = useState(authorsToText(paper.authors));
  const [year, setYear] = useState(paper.year?.toString() ?? "");
  const [venue, setVenue] = useState(paper.venue ?? "");
  const [abstract, setAbstract] = useState(paper.abstract ?? "");
  const [status, setStatus] = useState<PaperStatus>(paper.status);
  const [citekey, setCitekey] = useState(paper.citekey ?? "");
  const [tagText, setTagText] = useState(paper.tags.join(", "));
  const [collectionIds, setCollectionIds] = useState<string[]>(paper.collection_ids);

  const save = useMutation({
    mutationFn: (patch: PaperPatch) => api.patch<PaperDetail>(`/papers/${paper.id}`, patch),
    onSuccess: (updated) => {
      // notes_md はノートタブが管理しているので、書誌の保存で上書きしないよう現在のキャッシュ値を残す
      queryClient.setQueryData<PaperDetail>(["paper", paper.id], (old) => ({
        ...updated,
        notes_md: old?.notes_md ?? updated.notes_md,
      }));
      void queryClient.invalidateQueries({ queryKey: ["papers"] });
      void queryClient.invalidateQueries({ queryKey: ["tags"] });
      void queryClient.invalidateQueries({ queryKey: ["collections"] });
      toast.success(t.paper.saved);
    },
    onError: (e) => toast.error(describeError(t, e, { 409: t.paper.citekeyTaken })),
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const patch: PaperPatch = {
      title: title.trim(),
      authors: textToAuthors(authors),
      year: year ? Number(year) : null,
      venue: venue.trim() || null,
      abstract: abstract.trim() || null,
      status,
      tags: [...new Set(tagText.split(",").map((s) => s.trim()).filter(Boolean))],
      collection_ids: collectionIds,
    };
    if (citekey.trim()) patch.citekey = citekey.trim();
    save.mutate(patch);
  }

  async function copy(text: string) {
    if (await copyText(text)) toast.success(t.common.copied);
    else toast.error(t.common.copyFailed);
  }

  return (
    <div className="space-y-4">
      <form onSubmit={onSubmit} className="space-y-3">
        <fieldset disabled={!canEdit || save.isPending} className="space-y-3">
          <Field label={t.paper.title} htmlFor="b-title">
            <Input id="b-title" required maxLength={1000} value={title} onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <Field label={t.paper.authors} htmlFor="b-authors">
            <Textarea id="b-authors" placeholder={t.paper.authorsHint} value={authors} onChange={(e) => setAuthors(e.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label={t.paper.year} htmlFor="b-year">
              <Input id="b-year" type="number" min={1} max={9999} value={year} onChange={(e) => setYear(e.target.value)} />
            </Field>
            <Field label={t.paper.status} htmlFor="b-status">
              <Select id="b-status" className="w-full" value={status} onChange={(e) => setStatus(e.target.value as PaperStatus)}>
                {PAPER_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {t.status[s]}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <Field label={t.paper.venue} htmlFor="b-venue">
            <Input id="b-venue" maxLength={500} value={venue} onChange={(e) => setVenue(e.target.value)} />
          </Field>
          <Field label={t.paper.citekey} htmlFor="b-citekey">
            <Input id="b-citekey" pattern="[A-Za-z0-9_:.\-]+" maxLength={100} value={citekey} onChange={(e) => setCitekey(e.target.value)} />
          </Field>
          <Field label={t.paper.tags} htmlFor="b-tags">
            <Input
              id="b-tags"
              list="b-tag-suggestions"
              placeholder={t.paper.tagsHint}
              value={tagText}
              onChange={(e) => setTagText(e.target.value)}
            />
            <datalist id="b-tag-suggestions">
              {(tags.data ?? []).map((tg) => (
                <option key={tg.id} value={tg.name} />
              ))}
            </datalist>
          </Field>
          <div className="space-y-1">
            <Label htmlFor="b-collections">{t.paper.collections}</Label>
            <div id="b-collections" className="max-h-32 space-y-0.5 overflow-auto rounded-md border p-2 text-sm">
              {flat.length === 0 && <p className="text-xs text-muted-foreground">{t.paper.noCollections}</p>}
              {flat.map(({ collection: c, depth }) => (
                <label key={c.id} className="flex items-center gap-1.5" style={{ paddingLeft: depth * 12 }}>
                  <input
                    type="checkbox"
                    checked={collectionIds.includes(c.id)}
                    onChange={(e) =>
                      setCollectionIds((prev) => (e.target.checked ? [...prev, c.id] : prev.filter((x) => x !== c.id)))
                    }
                  />
                  {c.name}
                </label>
              ))}
            </div>
          </div>
          <Field label={t.paper.abstract} htmlFor="b-abstract">
            <Textarea id="b-abstract" className="min-h-[120px]" value={abstract} onChange={(e) => setAbstract(e.target.value)} />
          </Field>
          {canEdit && <Button type="submit">{t.common.save}</Button>}
        </fieldset>
      </form>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label>BibTeX</Label>
          {paper.bibtex && (
            <Button size="sm" variant="outline" onClick={() => copy(paper.bibtex ?? "")}>
              <ClipboardCopy className="h-3.5 w-3.5" />
              {t.common.copy}
            </Button>
          )}
        </div>
        <pre className="max-h-64 overflow-auto rounded-md bg-muted p-2 text-xs">{paper.bibtex ?? t.paper.noBibtex}</pre>
      </div>

      {paper.has_pdf && (
        <Button asChild variant="outline" size="sm">
          <a href={`/api/v1/papers/${paper.id}/file`} download>
            <Download className="h-3.5 w-3.5" />
            {t.paper.downloadPdf}
          </a>
        </Button>
      )}
    </div>
  );
}
