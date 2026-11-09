"use client";

import { Pencil, Trash2 } from "lucide-react";
import { fmt, useI18n } from "@/lib/i18n";
import { useMe } from "@/lib/session";
import type { Annotation } from "@/lib/types";
import { Button } from "@/components/ui/button";

export function AnnotationsTab({
  annotations,
  canEdit,
  isOwner,
  onFocus,
  onEdit,
  onDelete,
}: {
  annotations: Annotation[];
  /** 論文のワークスペースで editor 以上 */
  canEdit: boolean;
  /** 論文のワークスペースの owner（ほかの人の注釈も直せる） */
  isOwner: boolean;
  onFocus: (a: Annotation) => void;
  onEdit: (a: Annotation) => void;
  onDelete: (a: Annotation) => void;
}) {
  const { t } = useI18n();
  const me = useMe();

  if (annotations.length === 0) return <p className="text-sm text-muted-foreground">{t.reader.noAnnotations}</p>;

  const sorted = [...annotations].sort((a, b) => a.page_number - b.page_number);
  return (
    <ul className="space-y-2">
      {sorted.map((a) => {
        const mine = canEdit && (a.user_id === me.data?.user.id || isOwner);
        return (
          <li key={a.id} className="flex gap-2 rounded-md border p-2 text-sm">
            <span className="mt-1 h-3 w-3 shrink-0 rounded-full border border-black/20" style={{ background: a.color ?? "#fde047" }} />
            <button type="button" className="min-w-0 flex-1 text-left" onClick={() => onFocus(a)}>
              <span className="text-xs text-muted-foreground">
                {fmt(t.reader.pageLabel, { n: a.page_number })} · {a.kind === "area" ? t.reader.areaKind : t.reader.textKind} ·{" "}
                {a.user_display_name}
              </span>
              {a.content_text && <span className="mt-0.5 line-clamp-3 block italic">{a.content_text}</span>}
              {a.comment && <span className="mt-0.5 block whitespace-pre-wrap">{a.comment}</span>}
            </button>
            {mine && (
              <span className="flex shrink-0 flex-col">
                <Button size="icon" variant="ghost" className="h-6 w-6" aria-label={t.common.edit} onClick={() => onEdit(a)}>
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button size="icon" variant="ghost" className="h-6 w-6" aria-label={t.common.delete} onClick={() => onDelete(a)}>
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
