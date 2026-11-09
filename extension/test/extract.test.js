import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { extract, normalizeArxiv, normalizeDoi } from "../src/extract.js";

const dir = join(dirname(fileURLToPath(import.meta.url)), "fixtures");

// Hand-rolled meta parser for the fixtures (only <meta> tags and the leading url comment).
function load(name) {
  const html = readFileSync(join(dir, `${name}.html`), "utf8");
  const url = /<!-- url: (\S+) -->/.exec(html)[1];
  const metas = [];
  for (const m of html.matchAll(/<meta\s+(name|property)="([^"]+)"\s+content="([^"]*)"\s*>/g)) {
    metas.push({ name: m[2], content: m[3].replaceAll("&amp;", "&") });
  }
  return { url, metas };
}

const cases = {
  sciencedirect: {
    title: "An Example Study of Fixture Handling",
    authors: ["Doe, Jane", "Roe, Richard"],
    year: 2020,
    doi: "10.1016/s0140-6736(20)30183-5",
    pii: "S0140673620301835",
    identifier: "10.1016/s0140-6736(20)30183-5",
    pdfUrl: "https://www.sciencedirect.com/science/article/pii/S0140673620301835/pdfft?isDTMRedir=true",
  },
  ieee: {
    year: 2021,
    doi: "10.1109/example.2021.9000000",
    identifier: "10.1109/example.2021.9000000",
    pdfUrl: "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=9000000&ref=",
  },
  acm: {
    title: "A Fixture Paper on Clos Fabrics",
    authors: ["Carol Tester", "Dan Checker"],
    year: 2024,
    doi: "10.1145/3651890",
    pdfUrl: "https://dl.acm.org/doi/pdf/10.1145/3651890",
  },
  springer: { year: 2020, doi: "10.1007/s00000-020-0001-2", pdfUrl: "https://link.springer.com/content/pdf/10.1007/s00000-020-0001-2.pdf" },
  wiley: { year: 2019, doi: "10.1002/abc.12345", pdfUrl: "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/abc.12345" },
  arxiv: {
    title: "Scalable BGP Routing in Clos Fabrics",
    year: 2024,
    arxivId: "2405.12345",
    identifier: "2405.12345",
    doi: null,
    pdfUrl: "https://arxiv.org/pdf/2405.12345v2",
  },
  nature: { year: 2021, doi: "10.1038/s41586-021-03819-2", venue: "Nature" },
  plos: {
    year: 2018,
    doi: "10.1371/journal.pone.0000001",
    pdfUrl: "https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0000001&type=printable",
  },
};

for (const [name, expected] of Object.entries(cases)) {
  test(`extract: ${name}`, () => {
    const r = extract(load(name));
    assert.equal(r.found, true);
    for (const [k, v] of Object.entries(expected)) assert.deepEqual(r[k], v, `${name}.${k}`);
    assert.ok(r.identifier, "has an identifier");
    assert.ok(r.title, "has a title");
  });
}

test("extract: URL-only fallbacks (no meta tags)", () => {
  assert.equal(extract({ url: "https://www.sciencedirect.com/science/article/abs/pii/S0925231220309978", metas: [] }).pii, "S0925231220309978");
  assert.equal(extract({ url: "https://arxiv.org/pdf/2405.12345v3.pdf", metas: [] }).arxivId, "2405.12345");
  assert.equal(extract({ url: "https://arxiv.org/abs/cs/0601001v2", metas: [] }).arxivId, "cs/0601001");
  assert.equal(extract({ url: "https://doi.org/10.1145/3651890", metas: [] }).doi, "10.1145/3651890");
  assert.equal(extract({ url: "https://dx.doi.org/10.1145%2F3651890", metas: [] }).doi, "10.1145/3651890");
  assert.equal(extract({ url: "https://www.nature.com/articles/s41586-021-03819-2", metas: [] }).doi, "10.1038/s41586-021-03819-2");
  assert.equal(extract({ url: "https://example.com/blog/post", metas: [] }).found, false);
});

test("extract: og:title and document title are fallbacks, arXiv abs page derives the PDF", () => {
  const r = extract({ url: "https://arxiv.org/abs/2405.12345", metas: [{ name: "og:title", content: "Only OG" }] });
  assert.equal(r.title, "Only OG");
  assert.equal(r.pdfUrl, "https://arxiv.org/pdf/2405.12345");
  assert.equal(extract({ url: "https://x.example/a", metas: [], title: "Tab title" }).title, "Tab title");
});

test("extract: accepts a document-like object", () => {
  const els = [
    { getAttribute: (k) => ({ name: "citation_doi", content: "10.1/abc.def" })[k] ?? null },
    { getAttribute: (k) => ({ property: "og:title", content: "T" })[k] ?? null },
  ];
  const doc = { location: { href: "https://x.example/p" }, querySelectorAll: () => els };
  const r = extract(doc);
  assert.equal(r.title, "T");
  assert.equal(r.doi, null); // "10.1" is not a valid registrant prefix
  els[0] = { getAttribute: (k) => ({ name: "citation_doi", content: "https://doi.org/10.1234/ABC.def." })[k] ?? null };
  assert.equal(extract(doc).doi, "10.1234/abc.def");
});

test("extract: ignores non-http citation_pdf_url", () => {
  const r = extract({ url: "https://x.example/p", metas: [{ name: "citation_pdf_url", content: "javascript:alert(1)" }, { name: "citation_title", content: "T" }] });
  assert.equal(r.pdfUrl, null);
});

test("normalizeDoi / normalizeArxiv", () => {
  assert.equal(normalizeDoi("doi:10.1038/S41586-021-03819-2"), "10.1038/s41586-021-03819-2");
  assert.equal(normalizeDoi("not a doi"), null);
  assert.equal(normalizeDoi("10.1016/j.x.2020.01.001.pdf"), "10.1016/j.x.2020.01.001");
  assert.equal(normalizeArxiv("arXiv:2405.12345v4"), "2405.12345");
  assert.equal(normalizeArxiv(""), null);
});
