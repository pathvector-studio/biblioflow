"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/lib/i18n";
import type { Annotation } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { ColorSwatches, DEFAULT_COLOR } from "@/components/paper/colors";

export function AnnotationEditDialog({
  annotation,
  onClose,
  onSave,
}: {
  annotation: Annotation | null;
  onClose: () => void;
  onSave: (a: Annotation, patch: { comment: string; color: string }) => void;
}) {
  const { t } = useI18n();
  const [comment, setComment] = useState("");
  const [color, setColor] = useState<string>(DEFAULT_COLOR);

  useEffect(() => {
    if (annotation) {
      setComment(annotation.comment ?? "");
      setColor(annotation.color ?? DEFAULT_COLOR);
    }
  }, [annotation]);

  return (
    <Dialog open={annotation !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={t.common.close}>
        <DialogHeader>
          <DialogTitle>{t.reader.editAnnotation}</DialogTitle>
        </DialogHeader>
        <ColorSwatches value={color} onChange={setColor} />
        <Textarea
          autoFocus
          aria-label={t.reader.comment}
          placeholder={t.reader.commentPlaceholder}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button onClick={() => annotation && onSave(annotation, { comment: comment.trim(), color })}>
            {t.common.save}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
