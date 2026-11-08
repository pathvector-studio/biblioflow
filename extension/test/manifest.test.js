import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { build, readVersion } from "../build.mjs";
import { buildManifest } from "../manifest.mjs";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const ALLOWED = ["activeTab", "scripting", "storage"];

for (const browser of ["chrome", "firefox"]) {
  test(`manifest (${browser})`, () => {
    const m = buildManifest(browser, readVersion());
    assert.equal(m.manifest_version, 3);
    assert.deepEqual([...m.permissions].sort(), ALLOWED);
    assert.equal(m.host_permissions, undefined, "no install-time host permissions");
    assert.ok(m.optional_host_permissions.length > 0);
    assert.equal(m.content_scripts, undefined, "no persistent content scripts");
    assert.match(m.version, /^\d+\.\d+\.\d+$/);
    for (const f of [m.action.default_popup, m.options_ui.page]) assert.ok(existsSync(join(root, f)), f);
    const bg = browser === "chrome" ? m.background.service_worker : m.background.scripts[0];
    assert.ok(existsSync(join(root, bg)), bg);
    if (browser === "chrome") assert.equal(m.background.scripts, undefined);
    else {
      assert.equal(m.background.service_worker, undefined);
      assert.ok(m.browser_specific_settings.gecko.id);
    }
  });
}

test("locales have the same keys and cover every message used", () => {
  const load = (l) => JSON.parse(readFileSync(join(root, "_locales", l, "messages.json"), "utf8"));
  const en = load("en");
  const ja = load("ja");
  assert.deepEqual(Object.keys(en).sort(), Object.keys(ja).sort());
  const used = new Set();
  const sources = ["popup.html", "options.html", "popup.js", "options.js", "src/ui.js", "src/background.js"];
  for (const f of sources) {
    const text = readFileSync(join(root, f), "utf8");
    for (const m of text.matchAll(/(?:data-i18n="|\bt\(")([a-z_]+)"/g)) used.add(m[1]);
  }
  for (const k of ["ext_name", "ext_description", "action_title"]) used.add(k);
  for (const k of used) assert.ok(k in en, `missing message: ${k}`);
  for (const code of ["bad_token", "forbidden", "access_login", "network", "too_large", "conflict", "rate_limited", "server", "invalid", "not_configured", "nothing_found", "unknown"]) {
    assert.ok(`err_${code}` in en, `err_${code}`);
  }
  for (const r of ["no_pdf", "forbidden", "not_pdf", "too_large", "network", "http"]) assert.ok(`pdf_reason_${r}` in en);
  for (const r of ["new", "matched", "wishlist", "exists"]) assert.ok(`result_${r}` in en);
});

test("background never exposes secrets to pages or the popup", () => {
  const bg = readFileSync(join(root, "src/background.js"), "utf8");
  const page = readFileSync(join(root, "src/page.js"), "utf8");
  assert.ok(!/pat|cfClientSecret|Authorization/.test(page), "page.js must not touch secrets");
  assert.ok(!/runInTab\([^)]*(pat|Secret|settings)/.test(bg), "no secret is passed to a page script");
  for (const f of ["popup.js"]) assert.ok(!/loadSettings|\.pat\b/.test(readFileSync(join(root, f), "utf8")));
});

test("build produces both zips", () => {
  const files = build();
  assert.equal(files.length, 2);
  for (const z of files) {
    const buf = readFileSync(z);
    assert.equal(buf.readUInt32LE(0), 0x04034b50, "zip header");
    assert.equal(buf.readUInt32LE(buf.length - 22), 0x06054b50, "zip end record");
  }
  for (const b of ["chrome", "firefox"]) {
    const dir = join(root, "dist", b);
    assert.ok(readdirSync(dir).includes("manifest.json"));
    assert.equal(JSON.parse(readFileSync(join(dir, "manifest.json"), "utf8")).manifest_version, 3);
  }
});
