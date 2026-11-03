import type { Collection } from "@/lib/types";

export interface FlatCollection {
  collection: Collection;
  depth: number;
}

/** parent_id の入れ子を、深さ付きの平らな表示順リストにする */
export function flattenCollections(all: Collection[]): FlatCollection[] {
  const children = new Map<string | null, Collection[]>();
  const ids = new Set(all.map((c) => c.id));
  for (const c of all) {
    // 親が見えない（不整合な）ものはルート扱いにして取りこぼさない
    const key = c.parent_id && ids.has(c.parent_id) ? c.parent_id : null;
    children.set(key, [...(children.get(key) ?? []), c]);
  }
  const out: FlatCollection[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const c of (children.get(parent) ?? []).sort((a, b) => a.name.localeCompare(b.name))) {
      out.push({ collection: c, depth });
      walk(c.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}
