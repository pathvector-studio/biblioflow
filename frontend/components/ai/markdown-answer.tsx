"use client";

import { memo, useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { citeRefOf, linkCitations } from "@/lib/ai/citations";
import type { Citation } from "@/lib/types";

/**
 * AI の回答の Markdown 表示。画像と生 HTML は出さない（画像は URL を読み込むだけでデータが外に出せるため）。
 * 外部リンクも押せる形にせず、URL を文字で出す。
 * `citations` にある `[n]` はラベルのバッジになり、クリックで `onCite` が呼ばれる。
 */
function MarkdownAnswerBase({
  content,
  citations,
  onCite,
  citeTitle,
  showTitle = false,
}: {
  content: string;
  citations: Citation[];
  onCite?: (c: Citation) => void;
  citeTitle: (c: Citation) => string;
  /** 横断の回答: バッジに論文名も出す */
  showTitle?: boolean;
}) {
  const text = useMemo(() => linkCitations(content, citations), [content, citations]);
  const components = useMemo<Components>(() => {
    const byRef = new Map(citations.map((c) => [c.ref, c]));
    return {
      img: () => null,
      a: ({ href, children }) => {
        const ref = citeRefOf(href);
        const c = ref === null ? undefined : byRef.get(ref);
        if (c) {
          return (
            <button
              type="button"
              title={citeTitle(c)}
              aria-label={citeTitle(c)}
              onClick={() => onCite?.(c)}
              className="mx-0.5 inline-flex max-w-full items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-1.5 py-0 text-[11px] font-medium leading-5 text-primary no-underline hover:bg-primary/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className="shrink-0">{children}</span>
              {showTitle && c.title && <span className="max-w-[14rem] truncate font-normal">{c.title}</span>}
            </button>
          );
        }
        if (ref !== null) return <>{children}</>;
        // 引用以外のリンクは押せる形にしない。論文に仕込まれた指示で、質問や回答の内容を URL に入れたリンクを
        // 作られうるため。宛先は文字で見せる（必要ならコピーして開ける）。
        const url = typeof href === "string" && href && !href.startsWith("#") ? href : null;
        const sameAsText = typeof children === "string" && children === url;
        return (
          <span>
            {children}
            {url && !sameAsText && <span className="break-all text-muted-foreground"> ({url})</span>}
          </span>
        );
      },
    };
  }, [citations, onCite, citeTitle, showTitle]);

  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        disallowedElements={["img"]}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export const MarkdownAnswer = memo(MarkdownAnswerBase);
