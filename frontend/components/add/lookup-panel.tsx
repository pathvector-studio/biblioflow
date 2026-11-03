"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, describeError } from "@/lib/api";
import { formatAuthors, textToAuthors } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { useWorkspace } from "@/lib/session";
import type { LookupItem, LookupResult, PaperDetail } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export function LookupPanel() {
  const { t } = useI18n();
  const { current } = useWorkspace();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<LookupResult | null>(null);
  const [registered, setRegistered] = useState<Record<string, string>>({});
  const [manual, setManual] = useState(false);
  const [title, setTitle] = useState("");
  const [authors, setAuthors] = useState("");
  const [year, setYear] = useState("");

  const lookup = useMutation({
    mutationFn: (q: string) => api.get<LookupResult>("/papers/lookup", { query: q }),
    onSuccess: (r) => {
      setResult(r);
      if (r.results.length === 0) setManual(true);
    },
    onError: (e) => toast.error(describeError(t, e, { 502: t.add.upstreamError })),
  });

  const register = useMutation({
    mutationFn: async ({ item, key }: { item?: LookupItem; key: string }) => {
      const base = { workspace_id: current.id };
      const body =
        item && item.identifier_type !== "internal"
          ? { ...base, identifier: item.identifier }
          : item
            ? { ...base, title: item.title, authors: item.authors, year: item.year, venue: item.venue }
            : {
                ...base,
                title: title.trim(),
                authors: textToAuthors(authors),
                year: year ? Number(year) : null,
              };
      const paper = await api.post<PaperDetail>("/papers/metadata-only", body);
      return { key, paper };
    },
    onSuccess: ({ key, paper }) => {
      setRegistered((r) => ({ ...r, [key]: paper.id }));
      toast.success(t.add.registered);
      void queryClient.invalidateQueries({ queryKey: ["papers"] });
    },
    onError: (e) => toast.error(describeError(t, e, { 409: t.add.alreadyRegistered, 502: t.add.upstreamError })),
  });

  return (
    <section className="space-y-3">
      <h2 className="text-base font-semibold">{t.add.lookupTitle}</h2>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (query.trim()) lookup.mutate(query.trim());
        }}
      >
        <Input
          aria-label={t.add.lookupTitle}
          placeholder={t.add.lookupPlaceholder}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Button type="submit" disabled={lookup.isPending || !query.trim()}>
          {t.add.search}
        </Button>
      </form>

      {result && result.results.length === 0 && <p className="text-sm text-muted-foreground">{t.add.noCandidates}</p>}
      <ul className="space-y-2">
        {result?.results.map((item) => {
          const paperId = registered[item.identifier];
          return (
            <li key={item.identifier} className="rounded-md border p-3 text-sm">
              <p className="font-medium">{item.title}</p>
              <p className="text-xs text-muted-foreground">{formatAuthors(item.authors, 5)}</p>
              <p className="text-xs text-muted-foreground">
                {[item.year, item.venue, item.identifier_type !== "internal" ? item.identifier : null]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              <div className="mt-2">
                {paperId ? (
                  <Link href={`/papers/${paperId}`} className="text-primary hover:underline">
                    {t.add.openPaper}
                  </Link>
                ) : (
                  <Button size="sm" disabled={register.isPending} onClick={() => register.mutate({ item, key: item.identifier })}>
                    {t.add.registerWishlist}
                  </Button>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      <button type="button" className="text-sm text-primary hover:underline" onClick={() => setManual((m) => !m)}>
        {t.add.manualToggle}
      </button>
      {manual && (
        <form
          className="space-y-3 rounded-md border p-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (title.trim()) register.mutate({ key: "manual" });
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="m-title">{t.paper.title}</Label>
            <Input id="m-title" required maxLength={1000} value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="m-authors">{t.paper.authors}</Label>
            <Textarea
              id="m-authors"
              placeholder={t.paper.authorsHint}
              value={authors}
              onChange={(e) => setAuthors(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="m-year">{t.paper.year}</Label>
            <Input
              id="m-year"
              type="number"
              min={1}
              max={9999}
              className="w-28"
              value={year}
              onChange={(e) => setYear(e.target.value)}
            />
          </div>
          {registered.manual ? (
            <Link href={`/papers/${registered.manual}`} className="text-sm text-primary hover:underline">
              {t.add.openPaper}
            </Link>
          ) : (
            <Button type="submit" disabled={register.isPending || !title.trim()}>
              {t.add.registerWishlist}
            </Button>
          )}
        </form>
      )}
    </section>
  );
}
