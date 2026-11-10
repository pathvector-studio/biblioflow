// Pure metadata extraction. Input: { url, metas: [{name, content}], title?, embedded? } or a document-like object
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

function hostIs(url, domain) {
  const u = safeUrl(url);
  return Boolean(u) && (u.hostname.toLowerCase() === domain || u.hostname.toLowerCase().endsWith(`.${domain}`));
}

// IEEE Xplore has no citation_* tags. The record is a JSON object assigned in an inline script
// (`xplGlobal.document.metadata={...};`); `script` is that script's text. Returns the same shape as <meta> tags.
export function ieeeMetas(script) {
  if (typeof script !== "string") return [];
  const at = script.indexOf("xplGlobal.document.metadata");
  const start = at < 0 ? -1 : script.indexOf("{", at);
  if (start < 0) return [];
  let md = null;
  let end = script.lastIndexOf("};");
  for (let tries = 0; tries < 20 && end > start && md === null; tries++) {
    try {
      md = JSON.parse(script.slice(start, end + 1));
    } catch {
      end = script.lastIndexOf("};", end - 1); // "};" inside a string, or more code after the object
    }
  }
  if (!md || typeof md !== "object") return [];
  const str = (v) => (typeof v === "string" || typeof v === "number" ? String(v).trim() : "");
  const out = [];
  const add = (name, v) => {
    if (str(v)) out.push({ name, content: str(v) });
  };
  add("citation_title", md.title || md.displayDocTitle);
  add("citation_doi", md.doi);
  add("citation_publication_date", md.publicationYear);
  add("citation_journal_title", md.publicationTitle || md.displayPublicationTitle);
  for (const a of Array.isArray(md.authors) ? md.authors.slice(0, 100) : []) add("citation_author", a && a.name);
  const number = str(md.articleNumber);
  if (/^\d{1,12}$/.test(number)) {
    add("citation_pdf_url", `https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=${number}&ref=`);
  }
  return out;
}

// The ACM Digital Library writes dc.Creator as family name + given name with nothing between ("HintonGeoffrey E.").
// Split at the last lowercase-uppercase boundary, but only when the family name is a single word: "John McDonald"
// (a normal name) has a space before its boundary and is left alone.
export function splitJoinedName(name) {
  if (name.includes(",")) return name;
  let cut = -1;
  for (const m of name.matchAll(/\p{Ll}(?=\p{Lu})/gu)) cut = m.index + 1;
  if (cut < 0 || /\s/.test(name.slice(0, cut))) return name;
  return `${name.slice(0, cut)}, ${name.slice(cut).trim()}`;
}

export function extract(input) {
  const url = input.url || (input.location && input.location.href) || "";
  // Real <meta> tags come first, so they win over values read from the embedded record.
  const idx = indexMetas([...metasOf(input), ...(hostIs(url, "ieeexplore.ieee.org") ? ieeeMetas(input.embedded) : [])]);
  const acm = hostIs(url, "dl.acm.org");

  const title =
    pick(idx, "citation_title", "dc.title", "prism.title", "og:title", "twitter:title") ||
    (input.title ? String(input.title).trim() : null) ||
    null;
  const authors = cleanAuthors(
    pickAll(idx, "citation_author", "dc.creator", "dc.contributor", "citation_authors", "article:author"),
  ).map((a) => (acm ? splitJoinedName(a) : a));
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
  } else if (acm && doi) {
    pdfUrl = `https://dl.acm.org/doi/pdf/${doi}`; // the page has no citation_pdf_url; this is its own PDF link
  }

  const identifier = arxivId || doi || pii || null;
  return { found: Boolean(title || identifier), title, authors, year, venue, doi, arxivId, pii, identifier, pdfUrl, url };
}
