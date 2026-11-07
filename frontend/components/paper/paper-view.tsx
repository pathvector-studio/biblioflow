"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import type { ScaledPosition } from "react-pdf-highlighter-extended";
import { api, describeError } from "@/lib/api";
import { formatAuthors } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { useMe, useWorkspace } from "@/lib/session";
import type { Annotation, AnnotationDraft, Citation, PaperChunks, PaperDetail } from "@/lib/types";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AnnotationEditDialog } from "@/components/paper/annotation-edit-dialog";
import { AiTab } from "@/components/paper/ai-tab";
import { AnnotationsTab } from "@/components/paper/annotations-tab";
import { BibliographyTab } from "@/components/paper/bibliography-tab";
import { NotesTab } from "@/components/paper/notes-tab";
import { UploadBox } from "@/components/paper/upload-box";
import type { CitationRequest } from "@/components/paper/pdf-pane";

// pdf.js はブラウザ専用のため SSR では読み込まない
const PdfPane = dynamic(() => import("@/components/paper/pdf-pane").then((m) => m.PdfPane), {
  ssr: false,
  loading: () => null,
});

export function PaperView({ paperId }: { paperId: string }) {
  const { t } = useI18n();
  const { canEdit, current } = useWorkspace();
  const me = useMe();
  const queryClient = useQueryClient();

  const paper = useQuery({ queryKey: ["paper", paperId], queryFn: () => api.get<PaperDetail>(`/papers/${paperId}`) });
  const annotations = useQuery({
    queryKey: ["annotations", paperId],
    // 論文の取得を待たずに並行して取りに行く（PDF がない論文でも空配列が返るだけ）
    queryFn: () => api.get<Annotation[]>(`/papers/${paperId}/annotations`),
  });

  const [split, setSplit] = useState(58);
  const [focus, setFocus] = useState<{ id: string; nonce: number } | null>(null);
  const [citation, setCitation] = useState<CitationRequest | null>(null);
  const [tab, setTab] = useState("notes");
  const [editing, setEditing] = useState<Annotation | null>(null);
  const [deleting, setDeleting] = useState<Annotation | null>(null);
  const container = useRef<HTMLDivElement>(null);

  // 別の画面（AI 画面の引用）から `?page=4&hl=<chunk_id>` で開かれたら、そのページへ移ってハイライトする
  const searchParams = useSearchParams();
  const wantPage = Number(searchParams.get("page"));
  const wantChunk = searchParams.get("hl");
  const hasPdf = paper.data?.has_pdf === true;
  const jumped = useRef<string | null>(null);
  useEffect(() => {
    if (!hasPdf || !Number.isInteger(wantPage) || wantPage < 1) return;
    const marker = `${paperId}:${wantPage}:${wantChunk ?? ""}`;
    if (jumped.current === marker) return;
    jumped.current = marker;
    let cancelled = false;
    void (async () => {
      let bboxes: Citation["bboxes"] = [];
      if (wantChunk) {
        try {
          const r = await api.get<PaperChunks>(`/papers/${paperId}/chunks`, { page_from: wantPage, page_to: wantPage });
          bboxes = r.chunks.find((c) => c.id === wantChunk)?.bboxes ?? [];
        } catch {
          // チャンクが取れなくてもページには移る
        }
      }
      if (!cancelled) setCitation({ pageNumber: wantPage, bboxes, nonce: Date.now() });
    })();
    return () => {
      cancelled = true;
      // 開発時の StrictMode の再実行で、取り消した分をやり直せるようにする
      if (jumped.current === marker) jumped.current = null;
    };
  }, [hasPdf, paperId, wantPage, wantChunk]);

  const key = ["annotations", paperId];
  const replace = (a: Annotation) =>
    queryClient.setQueryData<Annotation[]>(key, (old) => (old ?? []).map((x) => (x.id === a.id ? a : x)));

  const create = useMutation({
    mutationFn: (d: AnnotationDraft) => api.post<Annotation>(`/papers/${paperId}/annotations`, d),
    onSuccess: (a) => queryClient.setQueryData<Annotation[]>(key, (old) => [...(old ?? []), a]),
    onError: (e) => toast.error(describeError(t, e)),
  });
  const update = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Record<string, unknown> }) =>
      api.patch<Annotation>(`/annotations/${id}`, patch),
    onSuccess: (a) => {
      replace(a);
      setEditing(null);
    },
    onError: (e) => toast.error(describeError(t, e)),
  });
  const remove = useMutation({
    mutationFn: (a: Annotation) => api.del(`/annotations/${a.id}`),
    onSuccess: (_, a) => queryClient.setQueryData<Annotation[]>(key, (old) => (old ?? []).filter((x) => x.id !== a.id)),
    onError: (e) => toast.error(describeError(t, e)),
  });

  // 以下は PdfPane のハイライト用 React ルートに渡るので、参照が変わらないようにしておく
  const { mutate: createAnnotation } = create;
  const { mutate: updateAnnotation } = update;
  const onMove = useCallback(
    (a: Annotation, position: ScaledPosition) => updateAnnotation({ id: a.id, patch: { position } }),
    [updateAnnotation],
  );
  const onEdit = useCallback((a: Annotation) => setEditing(a), []);
  const onDelete = useCallback((a: Annotation) => setDeleting(a), []);
  const onCite = useCallback(
    (c: Citation) => setCitation({ pageNumber: c.page_number, bboxes: c.bboxes, nonce: Date.now() }),
    [],
  );
  const onCreate = useCallback((d: AnnotationDraft) => createAnnotation(d), [createAnnotation]);

  function onDividerPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    e.currentTarget.setPointerCapture(e.pointerId);
  }
  function onDividerPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    if (!e.currentTarget.hasPointerCapture(e.pointerId) || !container.current) return;
    const rect = container.current.getBoundingClientRect();
    setSplit(Math.min(75, Math.max(25, ((e.clientX - rect.left) / rect.width) * 100)));
  }

  if (paper.isError) return <p className="p-6 text-sm text-destructive">{describeError(t, paper.error)}</p>;
  if (!paper.data) return <p className="p-6 text-sm text-muted-foreground">{t.common.loading}</p>;
  const p = paper.data;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b px-3 py-1.5">
        <Link href="/library" className="text-muted-foreground hover:text-foreground" aria-label={t.nav.library}>
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <div className="min-w-0">
          <h1 className="truncate text-sm font-semibold">{p.title}</h1>
          <p className="truncate text-xs text-muted-foreground">
            {[formatAuthors(p.authors), p.year, p.venue].filter(Boolean).join(" · ")}
          </p>
        </div>
      </div>
      <div ref={container} className="flex min-h-0 flex-1 flex-col md:flex-row" style={{ "--split": `${split}%` } as React.CSSProperties}>
        <div className="h-[60vh] shrink-0 overflow-auto md:h-auto md:basis-[var(--split)]">
          {p.has_pdf ? (
            <PdfPane
              key={p.id}
              paperId={p.id}
              annotations={annotations.data ?? []}
              canEdit={canEdit}
              myUserId={me.data?.user.id}
              isOwner={current.role === "owner"}
              focusRequest={focus}
              citationRequest={citation}
              onCreate={onCreate}
              onEdit={onEdit}
              onDelete={onDelete}
              onMove={onMove}
            />
          ) : (
            <UploadBox
              paper={p}
              canEdit={canEdit}
              onUploaded={(updated) => {
                queryClient.setQueryData(["paper", paperId], updated);
                void queryClient.invalidateQueries({ queryKey: ["papers"] });
              }}
            />
          )}
        </div>
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label={t.reader.resize}
          tabIndex={0}
          onPointerDown={onDividerPointerDown}
          onPointerMove={onDividerPointerMove}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft") setSplit((s) => Math.max(25, s - 3));
            if (e.key === "ArrowRight") setSplit((s) => Math.min(75, s + 3));
          }}
          className="hidden w-1.5 shrink-0 cursor-col-resize bg-border hover:bg-primary/40 focus-visible:bg-primary/40 md:block"
        />
        <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 min-w-0 flex-1 flex-col p-3">
          <TabsList className="self-start">
            <TabsTrigger value="notes">{t.reader.tabNotes}</TabsTrigger>
            <TabsTrigger value="bibliography">{t.reader.tabBibliography}</TabsTrigger>
            <TabsTrigger value="annotations">{t.reader.tabAnnotations}</TabsTrigger>
            <TabsTrigger value="ai">{t.reader.tabAi}</TabsTrigger>
          </TabsList>
          <TabsContent value="notes" forceMount className="mt-3 min-h-0 flex-1 data-[state=inactive]:hidden">
            <NotesTab key={p.id} paper={p} canEdit={canEdit} />
          </TabsContent>
          <TabsContent value="bibliography" forceMount className="mt-3 min-h-0 flex-1 overflow-auto data-[state=inactive]:hidden">
            <BibliographyTab key={p.updated_at} paper={p} />
          </TabsContent>
          <TabsContent value="annotations" forceMount className="mt-3 min-h-0 flex-1 overflow-auto data-[state=inactive]:hidden">
            <AnnotationsTab
              annotations={annotations.data ?? []}
              onFocus={(a) => setFocus({ id: a.id, nonce: Date.now() })}
              onEdit={onEdit}
              onDelete={onDelete}
            />
          </TabsContent>
          <TabsContent value="ai" forceMount className="mt-3 min-h-0 flex-1 data-[state=inactive]:hidden">
            <AiTab key={p.id} paperId={p.id} active={tab === "ai"} onCite={onCite} />
          </TabsContent>
        </Tabs>
      </div>

      <AnnotationEditDialog
        annotation={editing}
        onClose={() => setEditing(null)}
        onSave={(a, patch) => update.mutate({ id: a.id, patch })}
      />
      <AlertDialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogTitle>{t.reader.deleteAnnotationTitle}</AlertDialogTitle>
          <AlertDialogDescription>{t.reader.deleteAnnotationBody}</AlertDialogDescription>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction onClick={() => deleting && remove.mutate(deleting)}>{t.common.delete}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
