"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { ja, type Dict } from "./ja";
import { en } from "./en";

export type Locale = "ja" | "en";

const STORAGE_KEY = "bf_locale";
const dicts: Record<Locale, Dict> = { ja, en };

interface I18n {
  locale: Locale;
  t: Dict;
  setLocale: (l: Locale) => void;
}

const I18nContext = createContext<I18n | null>(null);

function detectLocale(): Locale {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "ja" || saved === "en") return saved;
  } catch {
    // localStorage が使えない環境ではブラウザの言語に従う
  }
  return navigator.language.toLowerCase().startsWith("ja") ? "ja" : "en";
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale | null>(null);

  useEffect(() => {
    const l = detectLocale();
    document.documentElement.lang = l;
    setLocaleState(l);
  }, []);

  const setLocale = useCallback((l: Locale) => {
    try {
      localStorage.setItem(STORAGE_KEY, l);
    } catch {
      // 保存できなくても切り替え自体は行う
    }
    document.documentElement.lang = l;
    setLocaleState(l);
  }, []);

  const value = useMemo(() => (locale ? { locale, t: dicts[locale], setLocale } : null), [locale, setLocale]);
  // 言語が決まるまでは描画しない（誤った言語が一瞬見えるのを避ける）
  if (!value) return null;
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used inside I18nProvider");
  return ctx;
}

/** `{name}` 形式のプレースホルダを置換する */
export function fmt(template: string, vars: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (_, k: string) => String(vars[k] ?? ""));
}
