// Pure metadata extraction. Input: { url, metas: [{name, content}], title? } or a document-like object
// (anything with querySelectorAll and location.href). No DOM or browser APIs are used, so Node can test it.

const DOI_RE = /10\.\d{4,9}\/[^\s"'<>?#]+/i;
const ARXIV_NEW = /\b(\d{4}\.\d{4,5})(?:v\d+)?\b/;
const ARXIV_OLD = /\b([a-z-]+(?:\.[A-Z]{2})?\/\d{7})(?:v\d+)?\b/;
const PII_RE = /\/pii\/(S[0-9X]{16,17})/i;

export function normalizeDoi(raw) {
  if (!raw) return null;
  let s = String(raw).trim();
  try {
    s = decodeURIComponent(s);
  } catch {
    /* keep as is */
  }
  s = s.replace(/^doi:\s*/i, "").replace(/^https?:\/\/(dx\.)?doi\.org\//i, "");
  const m = DOI_RE.exec(s);
  if (!m) return null;
  return m[0]
    .replace(/[.,;:)\]]+$/, "")
    .replace(/\.pdf$/i, "")
    .toLowerCase();
}

export function normalizeArxiv(raw) {
  if (!raw) return null;
  const s = String(raw)
    .trim()
    .replace(/^arxiv:\s*/i, "");
  const m = ARXIV_NEW.exec(s) || ARXIV_OLD.exec(s);
  return m ? m[1] : null;
}

function safeUrl(u, base) {
  try {
    return new URL(u, base);
  } catch {
    return null;
  }
}

export function arxivFromUrl(url) {
  const u = safeUrl(url);
  if (!u || !/(^|\.)arxiv\.org$/i.test(u.hostname)) return null;
  const m = /^\/(?:abs|pdf|html)\/(.+?)(?:\.pdf)?\/?$/.exec(u.pathname);
  return m ? normalizeArxiv(m[1]) : null;
}

export function piiFromUrl(url) {
  const u = safeUrl(url);
  if (!u || !/(^|\.)sciencedirect\.com$/i.test(u.hostname)) return null;
  const m = PII_RE.exec(u.pathname);
  return m ? m[1].toUpperCase() : null;
}

export function doiFromUrl(url) {
  const u = safeUrl(url);
  if (!u) return null;
  const host = u.hostname.toLowerCase();
  if (host === "doi.org" || host === "dx.doi.org") return normalizeDoi(u.pathname.slice(1));
  if (host === "journals.plos.org") return normalizeDoi(u.searchParams.get("id"));
  if (host === "www.nature.com" || host === "nature.com") {
    const m = /^\/articles\/([a-z0-9][a-z0-9-]*)/i.exec(u.pathname);
    return m && /^(s?\d|d\d)/i.test(m[1]) ? `10.1038/${m[1].toLowerCase()}` : null;
  }
  if (/\/(?:doi|article|chapter)\//.test(u.pathname)) return normalizeDoi(u.pathname);
  return null;
}

function metasOf(input) {
  if (Array.isArray(input.metas)) return input.metas;
  if (typeof input.querySelectorAll === "function") {
    const out = [];
    for (const el of input.querySelectorAll("meta")) {
      const name = el.getAttribute("name") || el.getAttribute("property");
      if (name) out.push({ name, content: el.getAttribute("content") || "" });
    }
    return out;
  }
  return [];
}

function indexMetas(list) {
  const idx = new Map();
  for (const { name, content } of list) {
    if (!name || typeof content !== "string") continue;
    const key = name.trim().toLowerCase();
    const val = content.trim();
    if (!val) continue;
    if (!idx.has(key)) idx.set(key, []);
    idx.get(key).push(val);
  }
  return idx;
}

function pick(idx, ...keys) {
  for (const k of keys) {
    const v = idx.get(k);
    if (v && v.length) return v[0];
  }
  return null;
}

function pickAll(idx, ...keys) {
  for (const k of keys) {
    const v = idx.get(k);
    if (v && v.length) return v;
  }
  return [];
}

export function parseYear(raw) {
  if (!raw) return null;
  const m = /\b(1[5-9]\d{2}|20\d{2})\b/.exec(String(raw));
  return m ? Number(m[1]) : null;
}

function cleanAuthors(list) {
  const out = [];
  for (const a of list) {
    for (const part of a.split(";")) {
      const n = part.trim();
      if (n && !out.includes(n)) out.push(n);
    }
  }
  return out.slice(0, 100);
}

export function extract(input) {
  const url = input.url || (input.location && input.location.href) || "";
  const idx = indexMetas(metasOf(input));

  const title =
    pick(idx, "citation_title", "dc.title", "prism.title", "og:title", "twitter:title") ||
    (input.title ? String(input.title).trim() : null) ||
    null;
  const authors = cleanAuthors(
    pickAll(idx, "citation_author", "dc.creator", "dc.contributor", "citation_authors", "article:author"),
  );
  const year = parseYear(
    pick(
      idx,
      "citation_publication_date",
      "citation_date",
      "citation_online_date",
      "prism.publicationdate",
      "dc.date",
      "dc.date.issued",
      "article:published_time",
    ),
  );
  const venue = pick(
    idx,
    "citation_journal_title",
    "citation_conference_title",
    "prism.publicationname",
    "citation_book_title",
    "og:site_name",
  );

  const doi =
    normalizeDoi(pick(idx, "citation_doi", "prism.doi", "dc.identifier.doi", "bepress_citation_doi")) ||
    pickAll(idx, "dc.identifier", "dc.identifier.uri", "dc.source").map(normalizeDoi).find(Boolean) ||
    doiFromUrl(url);
  const arxivId =
    normalizeArxiv(pick(idx, "citation_arxiv_id")) ||
    arxivFromUrl(url) ||
    arxivFromUrl(pick(idx, "citation_pdf_url") || "") ||
    arxivFromUrl(pick(idx, "citation_abstract_html_url") || "");
  const pii = piiFromUrl(url) || (pick(idx, "citation_pii") || "").toUpperCase() || null;

  let pdfUrl = null;
  const pdfRaw = pick(idx, "citation_pdf_url");
  if (pdfRaw) {
    const abs = safeUrl(pdfRaw, safeUrl(url) || undefined);
    if (abs && (abs.protocol === "https:" || abs.protocol === "http:")) pdfUrl = abs.href;
  } else if (arxivId && arxivFromUrl(url)) {
    pdfUrl = `https://arxiv.org/pdf/${arxivId}`;
  }

  const identifier = arxivId || doi || pii || null;
  return { found: Boolean(title || identifier), title, authors, year, venue, doi, arxivId, pii, identifier, pdfUrl, url };
}
