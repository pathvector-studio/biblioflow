"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  AreaHighlight,
  MonitoredHighlightContainer,
  PdfHighlighter,
  PdfLoader,
  TextHighlight,
  usePdfHighlighterContext,
  useHighlightContainerContext,
  type Highlight,
  type PdfHighlighterUtils,
  type PdfScaleValue,
  type ScaledPosition,
} from "react-pdf-highlighter-extended";
import { BoxSelect, ChevronLeft, ChevronRight, Minus, Plus } from "lucide-react";
import { version as pdfjsVersion } from "pdfjs-dist";
import { useI18n } from "@/lib/i18n";
import { pdfBoxToScaled, unionScaled } from "@/lib/ai/geometry";
import type { Annotation, AnnotationDraft, PdfBox } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ColorSwatches, DEFAULT_COLOR } from "@/components/paper/colors";

// public/ にコピーした worker（scripts/copy-pdf-worker.mjs）。バージョンを URL に含め、長期キャッシュさせる（next.config.ts）
const WORKER_SRC = `/pdf.worker.min.mjs?v=${pdfjsVersion}`;

interface AnnotationHighlight extends Highlight {
  /** AI の引用の一時ハイライトには注釈がない */
  annotation?: Annotation;
}

/** AI の回答の引用。ページと、その範囲（PDF のポイント、原点左下）。nonce が変わるたびに移動する */
export interface CitationRequest {
  pageNumber: number;
  bboxes: PdfBox[];
  nonce: number;
}

const CITATION_ID = "bf-citation";
const CITATION_MS = 6000;

function citationHighlight(req: CitationRequest): AnnotationHighlight | null {
  if (req.bboxes.length === 0) return null;
  const rects = req.bboxes.map((b) => pdfBoxToScaled(b, req.pageNumber));
  return { id: CITATION_ID, type: "text", position: { boundingRect: unionScaled(rects), rects } };
}

function isScaledPosition(p: unknown): p is ScaledPosition {
  if (!p || typeof p !== "object") return false;
  const { boundingRect, rects } = p as Partial<ScaledPosition>;
  return !!boundingRect && typeof boundingRect.pageNumber === "number" && Array.isArray(rects);
}

function toHighlights(annotations: Annotation[]): AnnotationHighlight[] {
  const out: AnnotationHighlight[] = [];
  for (const a of annotations) {
    if (a.kind === "note" || !isScaledPosition(a.position)) continue;
    out.push({ id: a.id, type: a.kind === "area" ? "area" : "text", position: a.position, annotation: a });
  }
  return out;
}

interface TipLabels {
  noComment: string;
  edit: string;
  delete: string;
}

/** ハイライト自体は別の React ルートに描画されるため、i18n などの context に頼らず props だけで作る */
function HighlightContainer({
  labels,
  canEdit,
  myUserId,
  isOwner,
  onEdit,
  onDelete,
  onMove,
}: {
  labels: TipLabels;
  canEdit: boolean;
  myUserId: string | undefined;
  isOwner: boolean;
  onEdit: (a: Annotation) => void;
  onDelete: (a: Annotation) => void;
  onMove: (a: Annotation, p: ScaledPosition) => void;
}) {
  const { highlight, isScrolledTo, viewportToScaled } = useHighlightContainerContext<AnnotationHighlight>();
  const a = highlight.annotation;
  if (!a) {
    // 引用の一時ハイライト。注釈ではないので、ツールチップも編集もない
    return <TextHighlight highlight={highlight} isScrolledTo={false} style={{ background: "rgba(250,204,21,0.5)" }} />;
  }
  const color = a.color ?? DEFAULT_COLOR;
  // バックエンドと同じ規則: editor 以上で、作成者本人かワークスペース owner
  const canModify = canEdit && (a.user_id === myUserId || isOwner);

  const tip = {
    position: highlight.position,
    content: (
      <div className="max-w-64 rounded-md border bg-popover p-2 text-xs text-popover-foreground shadow-md">
        <p className="whitespace-pre-wrap">{a.comment || labels.noComment}</p>
        {canModify && (
          <div className="mt-2 flex gap-1">
            <Button size="sm" variant="outline" className="h-6 px-2" onClick={() => onEdit(a)}>
              {labels.edit}
            </Button>
            <Button size="sm" variant="outline" className="h-6 px-2" onClick={() => onDelete(a)}>
              {labels.delete}
            </Button>
          </div>
        )}
      </div>
    ),
  };

  return (
    <MonitoredHighlightContainer highlightTip={tip}>
      {highlight.type === "area" ? (
        <AreaHighlight
          highlight={highlight}
          isScrolledTo={isScrolledTo}
          style={isScrolledTo ? undefined : { background: `${color}55`, border: `2px solid ${color}` }}
          onChange={(rect) => {
            if (!canModify) return;
            const scaled = viewportToScaled(rect);
            onMove(a, { boundingRect: scaled, rects: [scaled] });
          }}
        />
      ) : (
        <TextHighlight
          highlight={highlight}
          isScrolledTo={isScrolledTo}
          style={isScrolledTo ? undefined : { background: `${color}99` }}
        />
      )}
    </MonitoredHighlightContainer>
  );
}

function SelectionTip({ onCreate }: { onCreate: (d: AnnotationDraft) => void }) {
  const { t } = useI18n();
  const utils = usePdfHighlighterContext();
  const [color, setColor] = useState<string>(DEFAULT_COLOR);
  const [comment, setComment] = useState("");

  function save() {
    const selection = utils.getCurrentSelection();
    if (!selection) return;
    const ghost = selection.makeGhostHighlight();
    utils.removeGhostHighlight();
    utils.setTip(null);
    onCreate({
      kind: ghost.type === "area" ? "area" : "highlight",
      page_number: ghost.position.boundingRect.pageNumber,
      position: ghost.position,
      content_text: ghost.content.text ?? null,
      comment: comment.trim(),
      color,
    });
  }

  return (
    <div className="w-60 space-y-2 rounded-md border bg-popover p-2 text-popover-foreground shadow-md">
      <ColorSwatches value={color} onChange={setColor} />
      <Textarea
        className="min-h-[56px] text-xs"
        placeholder={t.reader.commentPlaceholder}
        value={comment}
        onChange={(e) => setComment(e.target.value)}
      />
      <div className="flex justify-end gap-1">
        <Button size="sm" variant="outline" onClick={() => utils.setTip(null)}>
          {t.common.cancel}
        </Button>
        <Button size="sm" onClick={save}>
          {t.reader.saveHighlight}
        </Button>
      </div>
    </div>
  );
}

export interface PdfPaneProps {
  paperId: string;
  annotations: Annotation[];
  canEdit: boolean;
  myUserId: string | undefined;
  isOwner: boolean;
  focusRequest: { id: string; nonce: number } | null;
  citationRequest?: CitationRequest | null;
  onCreate: (d: AnnotationDraft) => void;
  onEdit: (a: Annotation) => void;
  onDelete: (a: Annotation) => void;
  onMove: (a: Annotation, p: ScaledPosition) => void;
}

export function PdfPane({ paperId, annotations, canEdit, myUserId, isOwner, focusRequest, citationRequest, onCreate, onEdit, onDelete, onMove }: PdfPaneProps) {
  const { t } = useI18n();
  const utilsRef = useRef<PdfHighlighterUtils | null>(null);
  const [scale, setScale] = useState<PdfScaleValue>("page-width");
  const [page, setPage] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [pageInput, setPageInput] = useState("1");
  const [areaMode, setAreaMode] = useState(false);
  // ライブラリはマウスイベントの購読時に enableAreaSelection を閉じ込めて使い回すため、最新値は ref で読む
  const areaModeRef = useRef(areaMode);
  useLayoutEffect(() => {
    areaModeRef.current = areaMode;
  }, [areaMode]);
  const [error, setError] = useState<string | null>(null);

  const source = useMemo(() => ({ url: `/api/v1/papers/${paperId}/file`, withCredentials: true }), [paperId]);
  // 引用の一時ハイライトは、要求から導く。数秒たったら（nonce ごとに）消す
  const [expiredNonce, setExpiredNonce] = useState<number | null>(null);
  const citation = useMemo(
    () => (citationRequest && citationRequest.nonce !== expiredNonce ? citationHighlight(citationRequest) : null),
    [citationRequest, expiredNonce],
  );
  const highlights = useMemo(() => {
    const list = toHighlights(annotations);
    return citation ? [...list, citation] : list;
  }, [annotations, citation]);

  // ビューアの準備ができたらページ変更イベントを購読する
  useEffect(() => {
    let unsubscribe: (() => void) | null = null;
    const poll = setInterval(() => {
      const viewer = utilsRef.current?.getViewer();
      if (!viewer || viewer.pagesCount === 0) return;
      clearInterval(poll);
      setPageCount(viewer.pagesCount);
      const onChange = (e: { pageNumber: number }) => {
        setPage(e.pageNumber);
        setPageInput(String(e.pageNumber));
      };
      viewer.eventBus.on("pagechanging", onChange);
      unsubscribe = () => viewer.eventBus.off("pagechanging", onChange);
    }, 200);
    return () => {
      clearInterval(poll);
      unsubscribe?.();
    };
  }, [paperId]);

  // ズームはライブラリの再描画経路に頼らず直接ビューアへ反映する
  useEffect(() => {
    const viewer = utilsRef.current?.getViewer();
    if (viewer && viewer.pagesCount > 0) viewer.currentScaleValue = String(scale);
  }, [scale]);

  useEffect(() => {
    if (!focusRequest) return;
    const h = highlights.find((x) => x.id === focusRequest.id);
    if (h) utilsRef.current?.scrollToHighlight(h);
  }, [focusRequest, highlights]);

  // 引用のクリック: そのページへ移り、範囲を数秒だけハイライトする（保存しない）
  useEffect(() => {
    if (!citationRequest) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const apply = (): boolean => {
      // 別画面から開いた直後は、ビューアの準備ができるまで待つ
      const viewer = utilsRef.current?.getViewer();
      if (!viewer || viewer.pagesCount === 0) return false;
      const h = citationHighlight(citationRequest);
      if (h) {
        utilsRef.current?.scrollToHighlight(h);
      } else {
        viewer.currentPageNumber = Math.min(Math.max(1, citationRequest.pageNumber), viewer.pagesCount);
      }
      timer = setTimeout(() => setExpiredNonce(citationRequest.nonce), CITATION_MS);
      return true;
    };
    let poll: ReturnType<typeof setInterval> | undefined;
    if (!apply()) {
      let tries = 0;
      poll = setInterval(() => {
        if (apply() || ++tries > 100) clearInterval(poll);
      }, 200);
    }
    return () => {
      clearInterval(poll);
      clearTimeout(timer);
    };
  }, [citationRequest]);

  function goToPage(n: number) {
    const viewer = utilsRef.current?.getViewer();
    if (!viewer || !Number.isFinite(n)) return;
    viewer.currentPageNumber = Math.min(Math.max(1, Math.round(n)), viewer.pagesCount);
  }

  function zoom(factor: number) {
    const viewer = utilsRef.current?.getViewer();
    const base = typeof scale === "number" ? scale : (viewer?.currentScale ?? 1);
    setScale(Math.min(4, Math.max(0.3, Math.round(base * factor * 100) / 100)));
  }

  const labels: TipLabels = { noComment: t.reader.noComment, edit: t.common.edit, delete: t.common.delete };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-1 border-b px-2 py-1">
        <Button size="icon" variant="ghost" aria-label={t.reader.prevPage} onClick={() => goToPage(page - 1)} disabled={page <= 1}>
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <form
          className="flex items-center gap-1 text-xs"
          onSubmit={(e) => {
            e.preventDefault();
            goToPage(Number(pageInput));
          }}
        >
          <Input
            aria-label={t.reader.jumpToPage}
            className="h-7 w-14 px-1 text-center text-xs"
            inputMode="numeric"
            value={pageInput}
            onChange={(e) => setPageInput(e.target.value)}
          />
          <span className="text-muted-foreground">/ {pageCount || "-"}</span>
        </form>
        <Button size="icon" variant="ghost" aria-label={t.reader.nextPage} onClick={() => goToPage(page + 1)} disabled={page >= pageCount}>
          <ChevronRight className="h-4 w-4" />
        </Button>
        <span className="mx-1 h-4 w-px bg-border" />
        <Button size="icon" variant="ghost" aria-label={t.reader.zoomOut} onClick={() => zoom(1 / 1.2)}>
          <Minus className="h-4 w-4" />
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setScale("page-width")}>
          {typeof scale === "number" ? `${Math.round(scale * 100)}%` : t.reader.fitWidth}
        </Button>
        <Button size="icon" variant="ghost" aria-label={t.reader.zoomIn} onClick={() => zoom(1.2)}>
          <Plus className="h-4 w-4" />
        </Button>
        {canEdit && (
          <>
            <span className="mx-1 h-4 w-px bg-border" />
            <Button
              size="sm"
              variant={areaMode ? "default" : "ghost"}
              aria-pressed={areaMode}
              title={t.reader.areaModeHint}
              onClick={() => setAreaMode((v) => !v)}
            >
              <BoxSelect className="h-4 w-4" />
              {t.reader.areaMode}
            </Button>
          </>
        )}
      </div>
      <div className={cn("relative min-h-0 flex-1", areaMode && "cursor-crosshair")}>
        {error ? (
          <p role="alert" className="p-4 text-sm text-destructive">
            {error}
          </p>
        ) : (
          <PdfLoader
            document={source}
            workerSrc={WORKER_SRC}
            beforeLoad={() => <p className="p-4 text-sm text-muted-foreground">{t.reader.loadingPdf}</p>}
            errorMessage={() => <p className="p-4 text-sm text-destructive">{t.reader.pdfFailed}</p>}
            onError={() => setError(t.reader.pdfFailed)}
          >
            {(pdfDocument) => (
              <PdfHighlighter
                pdfDocument={pdfDocument}
                highlights={highlights}
                pdfScaleValue={scale}
                utilsRef={(u) => {
                  utilsRef.current = u;
                }}
                enableAreaSelection={canEdit ? (e) => areaModeRef.current || e.altKey : undefined}
                mouseSelectionStyle={{ border: "2px dashed hsl(221 83% 53%)", background: "rgba(59,130,246,0.15)" }}
                // 新しい要素を渡し続けることで、ライブラリ側のイベント購読が毎回張り直される（ズーム値の追従に必要）
                selectionTip={canEdit ? <SelectionTip onCreate={onCreate} /> : <></>}
              >
                <HighlightContainer
                  labels={labels}
                  canEdit={canEdit}
                  myUserId={myUserId}
                  isOwner={isOwner}
                  onEdit={onEdit}
                  onDelete={onDelete}
                  onMove={onMove}
                />
              </PdfHighlighter>
            )}
          </PdfLoader>
        )}
      </div>
    </div>
  );
}
