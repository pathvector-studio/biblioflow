"use client";

import { fmt, useI18n } from "@/lib/i18n";
import type { BibQuery } from "@/lib/bibliography";
import { BibliographyPanel } from "@/components/bibliography/bibliography-panel";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

/** ライブラリの書き出しメニューから開く。選んだ論文があればそれ、なければ今の絞り込み。 */
export function BibliographyDialog({
  open,
  onOpenChange,
  query,
  selectedCount,
  total,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  query: BibQuery;
  selectedCount: number;
  total: number;
}) {
  const { t } = useI18n();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent closeLabel={t.bibliography.close} className="max-h-[90vh] max-w-3xl overflow-auto">
        <DialogHeader>
          <DialogTitle>{t.bibliography.title}</DialogTitle>
          <DialogDescription>
            {t.bibliography.description}{" "}
            {selectedCount > 0
              ? fmt(t.bibliography.scopeSelected, { n: selectedCount })
              : fmt(t.bibliography.scopeFilters, { n: total })}
          </DialogDescription>
        </DialogHeader>
        {open && <BibliographyPanel query={query} idPrefix="lib-bib" />}
      </DialogContent>
    </Dialog>
  );
}
