import { useCallback, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export interface BibStyle {
  id: string;
  title: string;
  kind: "author-date" | "numeric";
}

export const BIB_LOCALES = ["en-US", "ja-JP"] as const;
export type BibLocale = (typeof BIB_LOCALES)[number];

export interface BibPref {
  style: string;
  locale: BibLocale;
}

const PREF_KEY = "bf_bibliography_pref";
const DEFAULT_PREF: BibPref = { style: "apa", locale: "en-US" };

function readPref(): BibPref {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(PREF_KEY) ?? "null");
    if (raw && typeof raw === "object") {
      const { style, locale } = raw as Record<string, unknown>;
      return {
        style: typeof style === "string" && style ? style : DEFAULT_PREF.style,
        locale: (BIB_LOCALES as readonly unknown[]).includes(locale) ? (locale as BibLocale) : DEFAULT_PREF.locale,
      };
    }
  } catch {
    // 読めなくても既定の書式で動く
  }
  return DEFAULT_PREF;
}

/** 最後に選んだ書式と言語（この端末のブラウザにだけ覚える） */
export function useBibPref(): [BibPref, (patch: Partial<BibPref>) => void] {
  const [pref, setPref] = useState<BibPref>(DEFAULT_PREF);
  useEffect(() => setPref(readPref()), []);
  const update = useCallback((patch: Partial<BibPref>) => {
    setPref((prev) => {
      const next = { ...prev, ...patch };
      try {
        localStorage.setItem(PREF_KEY, JSON.stringify(next));
      } catch {
        // 保存できなくても選択は有効
      }
      return next;
    });
  }, []);
  return [pref, update];
}

export function useBibStyles() {
  return useQuery({
    queryKey: ["bibliography-styles"],
    queryFn: () => api.get<BibStyle[]>("/bibliography/styles"),
    staleTime: Infinity,
  });
}

export type BibQuery = Record<string, string | number | undefined | null>;

/**
 * 整形済みの参考文献（HTML の断片）。
 * サーバーは `format=html` の本文のすべての値を HTML エスケープして返す（backend/app/services/csl）。
 * 画面が挿入してよいのは、この API の応答だけ。
 */
export function useBibliographyHtml(query: BibQuery, pref: BibPref, enabled: boolean, version?: string) {
  return useQuery({
    queryKey: ["bibliography", query, pref, version],
    queryFn: () => api.getText("/bibliography", { ...query, style: pref.style, locale: pref.locale, format: "html" }),
    enabled,
    staleTime: 60_000,
    retry: false,
  });
}

/** HTML の断片から、1 件 1 行のプレーンテキストを作る（サーバーの format=text と同じ内容） */
export function htmlToText(html: string): string {
  const doc = new DOMParser().parseFromString(html, "text/html");
  return [...doc.querySelectorAll(".csl-entry")].map((e) => (e.textContent ?? "").trim()).join("\n");
}

export function downloadFile(name: string, type: string, content: string): void {
  const url = URL.createObjectURL(new Blob([content], { type: `${type};charset=utf-8` }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function htmlDocument(fragment: string, title: string): string {
  const safeTitle = title.replace(/[&<>"]/g, "");
  return `<!doctype html>\n<html><head><meta charset="utf-8"><title>${safeTitle}</title></head><body>\n${fragment}</body></html>\n`;
}
