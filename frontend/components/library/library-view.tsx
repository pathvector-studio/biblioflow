"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, ClipboardCopy, FileText, Info, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, describeError } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { formatAuthors } from "@/lib/format";
import { fmt, useI18n } from "@/lib/i18n";
import { useSearchStatus, useTags } from "@/lib/queries";
import { useMe, useWorkspace } from "@/lib/session";
import { PAPER_STATUSES, type PaperDetail, type PaperLight, type PaperStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import { CollectionsSidebar } from "@/components/library/collections-sidebar";
import { BibliographyDialog } from "@/components/bibliography/bibliography-dialog";

const PAGE_SIZE = 50;
const SEMANTIC_NOTICE_KEY = "bf_semantic_notice_dismissed";

export function LibraryView() {
  const { t } = useI18n();
  const { current, canEdit } = useWorkspace();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const queryClient = useQueryClient();

  const wishlistTab = params.get("tab") === "wishlist";
  const status = wishlistTab ? "wishlist" : (params.get("status") ?? "");
  const year = params.get("year") ?? "";
  const tag = params.get("tag") ?? "";
  const collection = params.get("collection");
  const q = params.get("q") ?? "";
  const page = Math.max(0, Number(params.get("page") ?? 0) || 0);

  const setParams = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(window.location.search);
      for (const [k, v] of Object.entries(patch)) {
        if (v) next.set(k, v);
        else next.delete(k);
      }
      if (!("page" in patch)) next.delete("page");
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname);
    },
    [router, pathname],
  );

  // 検索語は入力中はローカル state、250ms 後に URL へ反映する
  const [qInput, setQInput] = useState(q);
  useEffect(() => {
    if (qInput === q) return;
    const id = setTimeout(() => setParams({ q: qInput.trim() || null }), 250);
    return () => clearTimeout(id);
  }, [qInput, q, setParams]);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState<PaperLight | null>(null);
  const [bibliographyOpen, setBibliographyOpen] = useState(false);
  const tags = useTags(current.id);
  const me = useMe();
  const isAdmin = me.data?.user.is_admin === true;
  const searchStatus = useSearchStatus(isAdmin);
  const [noticeDismissed, setNoticeDismissed] = useState(true);
  useEffect(() => {
    try {
      setNoticeDismissed(localStorage.getItem(SEMANTIC_NOTICE_KEY) === "1");
    } catch {
      setNoticeDismissed(false);
    }
  }, []);
  function dismissNotice() {
    setNoticeDismissed(true);
    try {
      localStorage.setItem(SEMANTIC_NOTICE_KEY, "1");
    } catch {
      // 保存できなくてもこの表示は閉じる
    }
  }
  // 管理者にだけ、意味検索が無効（EMBEDDING_PROVIDER=none）のときの案内を出す
  const showSemanticNotice = isAdmin && searchStatus.data?.model_state === "disabled" && !noticeDismissed;
  const onSelectCollection = useCallback((id: string | null) => setParams({ collection: id }), [setParams]);

  const papers = useQuery({
    queryKey: ["papers", current.id, { q, status, year, tag, collection, page }],
    queryFn: () =>
      api.getWithTotal<PaperLight>("/papers/light", {
        workspace_id: current.id,
        q,
        status,
        year,
        tag,
        collection_id: collection,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    placeholderData: keepPreviousData,
  });

  // ワークスペースや絞り込みが変わったら選択を捨てる
  useEffect(() => setSelected(new Set()), [current.id, q, status, year, tag, collection, page]);

  const changeStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: PaperStatus }) => api.patch(`/papers/${id}`, { status }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["papers"] }),
    onError: (e) => toast.error(describeError(t, e)),
  });

  const remove = useMutation({
    mutationFn: (p: PaperLight) => api.del(`/papers/${p.id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["papers"] });
      void queryClient.invalidateQueries({ queryKey: ["tags"] });
      void queryClient.invalidateQueries({ queryKey: ["collections"] });
      toast.success(t.library.deleted);
    },
    onError: (e) => toast.error(describeError(t, e)),
  });

  async function copy(kind: "bibtex" | "citekey") {
    const ids = [...selected];
    try {
      let text: string;
      if (kind === "citekey") {
        text = (papers.data?.items ?? [])
          .filter((p) => selected.has(p.id))
          .map((p) => p.citekey)
          .filter(Boolean)
          .join(", ");
      } else {
        const details = await Promise.all(ids.map((id) => api.get<PaperDetail>(`/papers/${id}`)));
        text = details
          .map((d) => d.bibtex)
          .filter(Boolean)
          .join("\n\n");
      }
      if (!text) {
        toast.error(t.library.nothingToCopy);
        return;
      }
      if (await copyText(text)) toast.success(t.common.copied);
      else toast.error(t.common.copyFailed);
    } catch (e) {
      toast.error(describeError(t, e));
    }
  }

  async function exportAs(format: "bibtex" | "ris" | "csljson") {
    try {
      await api.download(
        "/export",
        { format, workspace_id: current.id, q, status, year, tag, collection_id: collection },
        `biblioflow-export.${format === "bibtex" ? "bib" : format === "ris" ? "ris" : "json"}`,
      );
    } catch (e) {
      toast.error(describeError(t, e));
    }
  }

  const items = papers.data?.items ?? [];
  const total = papers.data?.total ?? 0;
  const allSelected = items.length > 0 && items.every((p) => selected.has(p.id));
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="flex flex-col gap-4 p-4 md:flex-row">
      <CollectionsSidebar selectedId={collection} onSelect={onSelectCollection} />
      <section className="min-w-0 flex-1 space-y-3">
        <div className="flex gap-1 border-b">
          {(["all", "wishlist"] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setParams({ tab: tab === "wishlist" ? "wishlist" : null, status: null })}
              className={cn(
                "-mb-px border-b-2 px-3 py-1.5 text-sm",
                (tab === "wishlist") === wishlistTab ? "border-primary font-medium" : "border-transparent text-muted-foreground",
              )}
            >
              {tab === "all" ? t.library.tabAll : t.library.tabWishlist}
            </button>
          ))}
        </div>

        {showSemanticNotice && (
          <div role="note" className="flex items-start gap-2 rounded-md border bg-muted/40 p-3 text-sm">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            <div className="min-w-0 flex-1 space-y-1">
              <p className="font-medium">{t.library.semanticOffTitle}</p>
              <p className="text-muted-foreground">{t.library.semanticOffBody}</p>
            </div>
            <Button type="button" variant="ghost" size="sm" onClick={dismissNotice}>
              {t.library.semanticOffDismiss}
            </Button>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Input
            className="w-64"
            type="search"
            aria-label={t.library.search}
            placeholder={t.library.search}
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
          />
          {!wishlistTab && (
            <Select aria-label={t.library.status} value={status} onChange={(e) => setParams({ status: e.target.value || null })}>
              <option value="">{t.library.anyStatus}</option>
              {PAPER_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {t.status[s]}
                </option>
              ))}
            </Select>
          )}
          <Input
            className="w-24"
            type="number"
            min={1}
            max={9999}
            aria-label={t.library.year}
            placeholder={t.library.year}
            value={year}
            onChange={(e) => setParams({ year: e.target.value || null })}
          />
          <Select aria-label={t.library.tag} value={tag} onChange={(e) => setParams({ tag: e.target.value || null })}>
            <option value="">{t.library.anyTag}</option>
            {(tags.data ?? []).map((tg) => (
              <option key={tg.id} value={tg.name}>
                {tg.name} ({tg.paper_count})
              </option>
            ))}
          </Select>
          <div className="ml-auto flex items-center gap-2">
            <Select
              aria-label={t.library.export}
              value=""
              onChange={(e) => {
                const v = e.target.value as "bibtex" | "ris" | "csljson" | "bibliography" | "";
                if (v === "bibliography") setBibliographyOpen(true);
                else if (v) void exportAs(v);
              }}
            >
              <option value="">{t.library.export}…</option>
              <option value="bibtex">{t.library.exportBibtex}</option>
              <option value="ris">{t.library.exportRis}</option>
              <option value="csljson">{t.library.exportCsl}</option>
              <option value="bibliography">{t.bibliography.menu}</option>
            </Select>
            {selected.size > 0 && (
              <>
                <span className="text-xs text-muted-foreground">{fmt(t.library.selected, { n: selected.size })}</span>
                <Button size="sm" variant="outline" onClick={() => copy("bibtex")}>
                  <ClipboardCopy className="h-3.5 w-3.5" />
                  {t.library.copyBibtex}
                </Button>
                <Button size="sm" variant="outline" onClick={() => copy("citekey")}>
                  <ClipboardCopy className="h-3.5 w-3.5" />
                  {t.library.copyCitekey}
                </Button>
              </>
            )}
          </div>
        </div>

        {papers.isError && <p className="text-sm text-destructive">{describeError(t, papers.error)}</p>}

        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-sm">
            <thead className="bg-muted text-left text-xs text-muted-foreground">
              <tr>
                <th className="w-8 px-2 py-2">
                  <input
                    type="checkbox"
                    aria-label={t.library.selectAll}
                    checked={allSelected}
                    onChange={() => setSelected(allSelected ? new Set() : new Set(items.map((p) => p.id)))}
                  />
                </th>
                <th className="px-2 py-2">{t.library.colTitle}</th>
                <th className="hidden px-2 py-2 lg:table-cell">{t.library.colVenue}</th>
                <th className="w-14 px-2 py-2">{t.library.year}</th>
                <th className="w-32 px-2 py-2">{t.library.status}</th>
                <th className="w-8 px-2 py-2">
                  <span className="sr-only">{t.library.hasPdf}</span>
                </th>
                {canEdit && <th className="w-8 px-2 py-2" />}
              </tr>
            </thead>
            <tbody>
              {items.map((p) => (
                <tr key={p.id} className="border-t align-top hover:bg-muted/50">
                  <td className="px-2 py-2">
                    <input
                      type="checkbox"
                      aria-label={p.title}
                      checked={selected.has(p.id)}
                      onChange={() => toggle(p.id)}
                    />
                  </td>
                  <td className="px-2 py-2">
                    <Link href={`/papers/${p.id}`} className="font-medium hover:underline">
                      {p.title}
                    </Link>
                    <div className="text-xs text-muted-foreground">{formatAuthors(p.authors)}</div>
                    {p.tags.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {p.tags.map((tg) => (
                          <Badge key={tg}>{tg}</Badge>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="hidden px-2 py-2 text-xs text-muted-foreground lg:table-cell">{p.venue}</td>
                  <td className="px-2 py-2">{p.year}</td>
                  <td className="px-2 py-2">
                    <Select
                      className="h-7 w-full text-xs"
                      aria-label={t.library.status}
                      value={p.status}
                      disabled={!canEdit || changeStatus.isPending}
                      onChange={(e) => changeStatus.mutate({ id: p.id, status: e.target.value as PaperStatus })}
                    >
                      {PAPER_STATUSES.map((s) => (
                        <option key={s} value={s}>
                          {t.status[s]}
                        </option>
                      ))}
                    </Select>
                  </td>
                  <td className="px-2 py-2">
                    {p.has_pdf && <FileText className="h-4 w-4 text-muted-foreground" aria-label={t.library.hasPdf} />}
                  </td>
                  {canEdit && (
                    <td className="px-2 py-2">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        aria-label={t.common.delete}
                        title={t.common.delete}
                        onClick={() => setDeleting(p)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </td>
                  )}
                </tr>
              ))}
              {!papers.isPending && items.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-2 py-8 text-center text-muted-foreground">
                    {t.library.empty}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {fmt(t.library.count, {
              from: total === 0 ? 0 : page * PAGE_SIZE + 1,
              to: Math.min(total, (page + 1) * PAGE_SIZE),
              total,
            })}
          </span>
          <div className="flex gap-1">
            <Button
              size="icon"
              variant="outline"
              aria-label={t.library.prev}
              disabled={page === 0}
              onClick={() => setParams({ page: page > 1 ? String(page - 1) : null })}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button
              size="icon"
              variant="outline"
              aria-label={t.library.next}
              disabled={page >= lastPage}
              onClick={() => setParams({ page: String(page + 1) })}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </section>

      <BibliographyDialog
        open={bibliographyOpen}
        onOpenChange={setBibliographyOpen}
        selectedCount={selected.size}
        total={total}
        query={
          selected.size > 0
            ? { workspace_id: current.id, ids: [...selected].join(",") }
            : { workspace_id: current.id, q, status, year, tag, collection_id: collection }
        }
      />

      <AlertDialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogTitle>{t.library.deleteTitle}</AlertDialogTitle>
          <AlertDialogDescription>
            {deleting?.title}
            <br />
            {t.library.deleteBody}
          </AlertDialogDescription>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction onClick={() => deleting && remove.mutate(deleting)}>{t.common.delete}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
