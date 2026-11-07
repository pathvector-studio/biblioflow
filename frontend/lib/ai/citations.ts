import type { Citation } from "@/lib/types";

export const CITE_HREF_PREFIX = "#bf-cite-";

const CODE_SPAN = /(```[\s\S]*?```|`[^`\n]*`)/;

/**
 * 本文の `[n]` を、citations にあるものだけ Markdown のリンク `[label](#bf-cite-n)` に置き換える。
 * citations にない番号と、コードの中は触らない。表示側が `#bf-cite-` のリンクをバッジにする。
 */
export function linkCitations(content: string, citations: Citation[]): string {
  if (citations.length === 0) return content;
  const byRef = new Map(citations.map((c) => [c.ref, c]));
  return content
    .split(CODE_SPAN)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part.replace(/\[(\d{1,2})\]/g, (whole, n: string) => {
            const c = byRef.get(Number(n));
            if (!c) return whole;
            const label = c.label.replace(/[\\[\]]/g, "\\$&");
            return `[${label}](${CITE_HREF_PREFIX}${c.ref})`;
          }),
    )
    .join("");
}

export function citeRefOf(href: string | undefined): number | null {
  if (!href?.startsWith(CITE_HREF_PREFIX)) return null;
  const n = Number(href.slice(CITE_HREF_PREFIX.length));
  return Number.isInteger(n) ? n : null;
}
