"use client";

import { memo, useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { citeRefOf, linkCitations } from "@/lib/ai/citations";
import type { Citation } from "@/lib/types";

/**
 * AI の回答の Markdown 表示。画像と生 HTML は出さない（画像は URL を読み込むだけでデータが外に出せるため）。
 * `citations` にある `[n]` はラベルのバッジになり、クリックで `onCite` が呼ばれる。
 */
function MarkdownAnswerBase({
  content,
  citations,
  onCite,
  citeTitle,
}: {
  content: string;
  citations: Citation[];
  onCite?: (c: Citation) => void;
  citeTitle: (c: Citation) => string;
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
              onClick={() => onCite?.(c)}
              className="mx-0.5 inline-flex items-center rounded-full border border-primary/40 bg-primary/10 px-1.5 py-0 text-[11px] font-medium leading-5 text-primary no-underline hover:bg-primary/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {children}
            </button>
          );
        }
        if (ref !== null) return <>{children}</>;
        return (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        );
      },
    };
  }, [citations, onCite, citeTitle]);

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
