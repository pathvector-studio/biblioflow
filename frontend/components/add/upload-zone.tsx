"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { describeError, uploadPdf } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useWorkspace } from "@/lib/session";
import { cn } from "@/lib/utils";

interface Item {
  key: number;
  name: string;
  progress: number;
  state: "queued" | "uploading" | "done" | "error";
  paperId?: string;
  paperTitle?: string;
  error?: string;
}

export function UploadZone() {
  const { t } = useI18n();
  const { current } = useWorkspace();
  const queryClient = useQueryClient();
  const [items, setItems] = useState<Item[]>([]);
  const [dragging, setDragging] = useState(false);
  const nextKey = useRef(0);
  const chain = useRef<Promise<void>>(Promise.resolve());
  const input = useRef<HTMLInputElement>(null);

  const patch = (key: number, p: Partial<Item>) =>
    setItems((prev) => prev.map((i) => (i.key === key ? { ...i, ...p } : i)));

  async function run(key: number, file: File) {
    patch(key, { state: "uploading" });
    try {
      const paper = await uploadPdf(file, { workspaceId: current.id }, (f) => patch(key, { progress: f }));
      patch(key, { state: "done", progress: 1, paperId: paper.id, paperTitle: paper.title });
      void queryClient.invalidateQueries({ queryKey: ["papers"] });
    } catch (e) {
      patch(key, { state: "error", error: describeError(t, e, { 409: t.add.duplicate }) });
    }
  }

  function addFiles(files: FileList | null) {
    if (!files) return;
    for (const file of Array.from(files)) {
      const key = nextKey.current++;
      setItems((prev) => [...prev, { key, name: file.name, progress: 0, state: "queued" }]);
      // 同じ PDF を続けて投げたときに重複判定が確実に働くよう、1 件ずつ順番にアップロードする
      chain.current = chain.current.then(() => run(key, file));
    }
  }

  return (
    <section className="space-y-3">
      <h2 className="text-base font-semibold">{t.add.uploadTitle}</h2>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          addFiles(e.dataTransfer.files);
        }}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        role="button"
        tabIndex={0}
        className={cn(
          "flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed p-8 text-center text-sm text-muted-foreground hover:bg-accent/50",
          dragging && "border-primary bg-accent",
        )}
      >
        <Upload className="h-6 w-6" />
        <span>{t.add.dropHere}</span>
        <input
          ref={input}
          type="file"
          accept="application/pdf,.pdf"
          multiple
          hidden
          onChange={(e) => {
            addFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>
      <ul className="space-y-2">
        {items.map((i) => (
          <li key={i.key} className="rounded-md border p-2 text-sm">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate">{i.name}</span>
              {i.state === "done" && i.paperId && (
                <Link href={`/papers/${i.paperId}`} className="shrink-0 text-primary hover:underline">
                  {t.add.openPaper}
                </Link>
              )}
            </div>
            {(i.state === "uploading" || i.state === "queued") && (
              <div className="mt-1.5 h-1.5 overflow-hidden rounded bg-muted">
                <div className="h-full bg-primary transition-all" style={{ width: `${Math.round(i.progress * 100)}%` }} />
              </div>
            )}
            {i.state === "done" && <p className="text-xs text-muted-foreground">{i.paperTitle}</p>}
            {i.state === "error" && (
              <p role="alert" className="text-xs text-destructive">
                {i.error}
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
