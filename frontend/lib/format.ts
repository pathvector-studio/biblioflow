import type { Author } from "@/lib/types";

export function authorName(a: Author): string {
  return a.given ? `${a.given} ${a.family}` : a.family;
}

export function formatAuthors(authors: Author[], max = 3): string {
  const names = authors.slice(0, max).map(authorName).join(", ");
  return authors.length > max ? `${names}, et al.` : names;
}

/** 編集用: 1 行 1 著者、"Family, Given"（姓に空白があっても往復で壊れない） */
export function authorsToText(authors: Author[]): string {
  return authors.map((a) => (a.given ? `${a.family}, ${a.given}` : a.family)).join("\n");
}

/** "Family, Given" 形式。カンマがなければ最後の語を姓として扱う */
export function textToAuthors(text: string): Author[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((line) => {
      if (line.includes(",")) {
        const [family, ...rest] = line.split(",");
        const given = rest.join(",").trim();
        return { family: family.trim(), given: given || null };
      }
      const parts = line.split(/\s+/);
      const family = parts.pop() ?? line;
      return { family, given: parts.join(" ") || null };
    });
}
