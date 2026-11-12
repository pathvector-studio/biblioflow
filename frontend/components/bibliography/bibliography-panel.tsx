"use client";

import { useMemo } from "react";
import { ClipboardCopy, Download } from "lucide-react";
import { toast } from "sonner";
import { describeError } from "@/lib/api";
import { BIB_LOCALES, downloadFile, htmlDocument, htmlToText, useBibPref, useBibStyles, useBibliographyHtml, type BibLocale, type BibQuery } from "@/lib/bibliography";
import { copyHtml, copyText } from "@/lib/clipboard";
import { useI18n } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";

/**
 * 書式と言語を選び、`query`（絞り込みか論文 ID）に合う参考文献を整形して見せる。
 * ライブラリの書き出しダイアログと、論文画面の書誌タブの両方で使う。`version` は、書誌が変わったら取り直すための目印。
 */
export function BibliographyPanel({ query, idPrefix, version }: { query: BibQuery; idPrefix: string; version?: string }) {
  const { t } = useI18n();
  const [pref, setPref] = useBibPref();
  const styles = useBibStyles();
  const result = useBibliographyHtml(query, pref, styles.isSuccess, version);
  const html = result.data?.text ?? "";
  const text = useMemo(() => (html ? htmlToText(html) : ""), [html]);
  const count = result.data?.total ?? 0;

  // 保存された書式が今の一覧にない（将来の変更など）ときは先頭に寄せる
  const styleList = styles.data ?? [];
  const styleValue = styleList.some((s) => s.id === pref.style) ? pref.style : (styleList[0]?.id ?? pref.style);

  async function copyAsHtml() {
    if (await copyHtml(html, text)) toast.success(t.common.copied);
    else toast.error(t.common.copyFailed);
  }
  async function copyAsText() {
    if (await copyText(text)) toast.success(t.common.copied);
    else toast.error(t.common.copyFailed);
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor={`${idPrefix}-style`}>{t.bibliography.style}</Label>
          <Select id={`${idPrefix}-style`} value={styleValue} onChange={(e) => setPref({ style: e.target.value })}>
            {styleList.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title} ({t.bibliography.kinds[s.kind]})
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor={`${idPrefix}-locale`}>{t.bibliography.language}</Label>
          <Select id={`${idPrefix}-locale`} value={pref.locale} onChange={(e) => setPref({ locale: e.target.value as BibLocale })}>
            {BIB_LOCALES.map((l) => (
              <option key={l} value={l}>
                {t.bibliography.languages[l]}
              </option>
            ))}
          </Select>
        </div>
      </div>

      <div
        className="max-h-80 min-h-16 overflow-auto rounded-md border bg-background p-3 text-sm leading-relaxed"
        aria-live="polite"
        aria-label={t.bibliography.preview}
      >
        {styles.isError && <p className="text-destructive">{describeError(t, styles.error)}</p>}
        {result.isError && <p className="text-destructive">{describeError(t, result.error)}</p>}
        {(styles.isPending || result.isFetching) && !result.isError && <p className="text-muted-foreground">{t.bibliography.loading}</p>}
        {result.isSuccess && !result.isFetching && count === 0 && <p className="text-muted-foreground">{t.bibliography.empty}</p>}
        {result.isSuccess && !result.isFetching && count > 0 && (
          // 安全: HTML は API（format=html）の応答だけで、サーバーが書誌の値をすべてエスケープしている。
          // タグはスタイルが付ける i / b / sup / sub / div / span だけ（backend/tests/test_csl_service.py が検査する）。
          <div className="bf-bibliography space-y-1.5" dangerouslySetInnerHTML={{ __html: html }} />
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={!html || count === 0} onClick={copyAsHtml}>
          <ClipboardCopy className="h-3.5 w-3.5" />
          {t.bibliography.copyHtml}
        </Button>
        <Button size="sm" variant="outline" disabled={!html || count === 0} onClick={copyAsText}>
          <ClipboardCopy className="h-3.5 w-3.5" />
          {t.bibliography.copyText}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!html || count === 0}
          onClick={() => downloadFile("biblioflow-bibliography.html", "text/html", htmlDocument(html, t.bibliography.title))}
        >
          <Download className="h-3.5 w-3.5" />
          {t.bibliography.downloadHtml}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!html || count === 0}
          onClick={() => downloadFile("biblioflow-bibliography.txt", "text/plain", `${text}\n`)}
        >
          <Download className="h-3.5 w-3.5" />
          {t.bibliography.downloadText}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{t.bibliography.htmlHint}</p>
    </div>
  );
}
