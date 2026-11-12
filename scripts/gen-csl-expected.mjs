#!/usr/bin/env node
// DEV ONLY. Generates backend/tests/fixtures/csl/expected.json, the reference output the backend test compares
// citeproc-py against (see specs/csl-bibliography.md, R6).
//
// The reference engine is citeproc-js, the processor Zotero uses. It is licensed CPAL-1.0 OR AGPL-1.0, so it must
// never become a dependency of this repository: install it in a scratch directory OUTSIDE the checkout and point
// this script at it. Only the generated JSON is committed.
//
//   mkdir /path/to/scratch && cd /path/to/scratch && npm init -y && npm install citeproc@2.4.63
//   node scripts/gen-csl-expected.mjs /path/to/scratch/node_modules/citeproc/citeproc_commonjs.js
//
// The inputs are backend/tests/fixtures/csl/items.json and the style / locale files that ship in the backend
// (backend/app/services/csl). The same escaping the backend applies before formatting HTML is applied here, so
// a field value is always data and never markup in both engines.
import { createRequire } from "node:module";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const cslDir = join(root, "backend/app/services/csl");
const fixtures = join(root, "backend/tests/fixtures/csl");

const enginePath = process.argv[2];
if (!enginePath) {
  console.error("usage: gen-csl-expected.mjs <path to citeproc_commonjs.js from a scratch install>");
  process.exit(2);
}
if (resolve(enginePath).startsWith(root + "/")) {
  console.error("citeproc-js must be installed outside the repository (it is CPAL/AGPL licensed)");
  process.exit(2);
}
const require = createRequire(import.meta.url);
const CSL = require(resolve(enginePath));
const engineVersion = JSON.parse(readFileSync(join(dirname(resolve(enginePath)), "package.json"), "utf8")).version;

const styles = JSON.parse(readFileSync(join(cslDir, "styles.json"), "utf8"));
const locales = ["en-US", "ja-JP"];
const items = JSON.parse(readFileSync(join(fixtures, "items.json"), "utf8"));
const byId = Object.fromEntries(items.map((i) => [i.id, i]));

function run(styleFile, lang, format) {
  const sys = {
    retrieveLocale: (l) => readFileSync(join(cslDir, "locales", `locales-${l}.xml`), "utf8"),
    retrieveItem: (id) => byId[id],
  };
  const xml = readFileSync(join(cslDir, "styles", styleFile), "utf8");
  const engine = new CSL.Engine(sys, xml, lang, true);
  engine.setOutputFormat(format);
  engine.updateItems(items.map((i) => i.id));
  const [params, entries] = engine.makeBibliography();
  return params.entry_ids.map((ids, n) => ({ id: ids[0], out: entries[n] }));
}

const out = { engine: `citeproc-js ${engineVersion}`, cases: {} };
for (const s of styles) {
  out.cases[s.id] = {};
  for (const lang of locales) {
    out.cases[s.id][lang] = {
      html: run(s.file, lang, "html"),
      text: run(s.file, lang, "text"),
    };
  }
}
writeFileSync(join(fixtures, "expected.json"), JSON.stringify(out, null, 1) + "\n");
console.log(`wrote expected.json (${Object.keys(out.cases).length} styles, ${locales.length} locales, ${items.length} items)`);
