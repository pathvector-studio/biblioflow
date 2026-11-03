"use client";

import { useRef, useState } from "react";
import { Upload } from "lucide-react";
import { describeError, uploadPdf } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { PaperDetail } from "@/lib/types";
import { cn } from "@/lib/utils";

/** PDF のない論文（Wishlist など）にファイルを紐付ける */
export function UploadBox({
  paper,
  canEdit,
  onUploaded,
}: {
  paper: PaperDetail;
  canEdit: boolean;
  onUploaded: (p: PaperDetail) => void;
}) {
  const { t } = useI18n();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function upload(file: File | undefined) {
    if (!file) return;
    setError(null);
    setProgress(0);
    try {
      onUploaded(await uploadPdf(file, { workspaceId: paper.workspace_id, paperId: paper.id }, setProgress));
    } catch (e) {
      setError(describeError(t, e, { 409: t.add.duplicate }));
    } finally {
      setProgress(null);
    }
  }

  if (!canEdit) return <p className="p-6 text-sm text-muted-foreground">{t.reader.noPdf}</p>;

  return (
    <div className="p-4">
      <p className="mb-3 text-sm text-muted-foreground">{t.reader.noPdf}</p>
      <div
        role="button"
        tabIndex={0}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void upload(e.dataTransfer.files[0]);
        }}
        className={cn(
          "flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed p-8 text-center text-sm text-muted-foreground hover:bg-accent/50",
          dragging && "border-primary bg-accent",
        )}
      >
        <Upload className="h-6 w-6" />
        <span>{t.reader.uploadPdf}</span>
        <input
          ref={input}
          type="file"
          accept="application/pdf,.pdf"
          hidden
          onChange={(e) => {
            void upload(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
      </div>
      {progress !== null && (
        <div className="mt-3 h-1.5 overflow-hidden rounded bg-muted">
          <div className="h-full bg-primary transition-all" style={{ width: `${Math.round(progress * 100)}%` }} />
        </div>
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
