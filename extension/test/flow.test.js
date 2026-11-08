import assert from "node:assert/strict";
import test from "node:test";
import { ApiError, createClient, normalizeBaseUrl } from "../src/api.js";
import { hasPdfMagic, looksLikePdfUrl } from "../src/bytes.js";
import { save, splitAuthor } from "../src/save.js";

function resp(status, body, { type = "basic", json = true } = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    type,
    headers: { get: (k) => (k.toLowerCase() === "content-type" ? (json ? "application/json" : "text/html") : null) },
    json: async () => body,
  };
}

function client(handler) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return handler(url, init, calls.length);
  };
  return { calls, c: createClient({ baseUrl: "https://papers.example.com/", pat: "bf_pat_x", fetchImpl }) };
}

test("client sends bearer token, credentials and optional Access headers to the configured origin only", async () => {
  const calls = [];
  const c = createClient({
    baseUrl: "https://papers.example.com/some/path",
    pat: "bf_pat_x",
    cfClientId: "id",
    cfClientSecret: "secret",
    fetchImpl: async (url, init) => (calls.push({ url, init }), resp(200, [{ id: "w" }])),
  });
  assert.deepEqual(await c.testConnection(), { workspaces: 1 });
  assert.equal(calls[0].url, "https://papers.example.com/api/v1/workspaces");
  assert.equal(calls[0].init.credentials, "include");
  assert.equal(calls[0].init.headers.Authorization, "Bearer bf_pat_x");
  assert.equal(calls[0].init.headers["CF-Access-Client-Id"], "id");
  assert.equal(c.paperUrl("abc"), "https://papers.example.com/papers/abc");
});

test("client error classification", async () => {
  const code = async (handler) => {
    const { c } = client(handler);
    try {
      await c.listWorkspaces();
    } catch (e) {
      assert.ok(e instanceof ApiError);
      return e.code;
    }
    return "ok";
  };
  assert.equal(await code(() => resp(401, { detail: "x" })), "bad_token");
  assert.equal(await code(() => resp(403, { detail: "scope" })), "forbidden");
  assert.equal(await code(() => resp(403, null, { json: false })), "access_login");
  assert.equal(await code(() => resp(0, null, { type: "opaqueredirect" })), "access_login");
  assert.equal(await code(() => resp(200, null, { json: false })), "access_login");
  assert.equal(await code(() => { throw new TypeError("fail"); }), "network");
  assert.equal(await code(() => resp(429, {})), "rate_limited");
  assert.equal(await code(() => resp(502, {})), "server");
});

test("normalizeBaseUrl keeps the origin and rejects other schemes", () => {
  assert.equal(normalizeBaseUrl("https://papers.example.com/x?y"), "https://papers.example.com");
  assert.throws(() => normalizeBaseUrl("file:///etc/passwd"));
  assert.throws(() => normalizeBaseUrl("javascript:alert(1)"));
});

test("upload sends multipart with file, identifier, workspace", async () => {
  const { c, calls } = client(() => resp(201, { id: "p1", has_pdf: true, created_at: new Date().toISOString(), title: "T" }));
  const r = await save({
    meta: { identifier: "2405.12345", title: "T", authors: [] },
    pdf: { bytes: new TextEncoder().encode("%PDF-1.7 x") },
    client: c,
    workspaceId: "11111111-1111-1111-1111-111111111111",
  });
  assert.equal(r.kind, "new");
  assert.equal(r.hasPdf, true);
  const form = calls[0].init.body;
  assert.ok(form instanceof FormData);
  assert.equal(form.get("identifier"), "2405.12345");
  assert.equal(form.get("workspace_id"), "11111111-1111-1111-1111-111111111111");
  assert.equal(form.get("file").name, "paper.pdf");
  assert.equal(calls[0].init.headers["Content-Type"], undefined, "browser sets the multipart boundary");
});

test("upload onto an older paper is reported as matched; 409 as exists", async () => {
  const old = new Date(Date.now() - 3600_000).toISOString();
  const a = client(() => resp(201, { id: "p1", has_pdf: true, created_at: old }));
  const r = await save({ meta: { identifier: "x" }, pdf: { bytes: new Uint8Array([1]) }, client: a.c });
  assert.equal(r.kind, "matched");
  const b = client(() => resp(409, { detail: "Paper already has a PDF", paper_id: "p9" }));
  const r2 = await save({ meta: { identifier: "x" }, pdf: { bytes: new Uint8Array([1]) }, client: b.c });
  assert.deepEqual([r2.kind, r2.paperId], ["exists", "p9"]);
});

test("no PDF goes to metadata-only with the reason; unresolvable identifier falls back to the title", async () => {
  const { c, calls } = client((url, init, n) =>
    n === 1 ? resp(404, { detail: "No metadata found" }) : resp(201, { id: "w1", has_pdf: false, title: "T" }),
  );
  const r = await save({
    meta: { identifier: "S0140673620301835", title: "T", authors: ["Doe, Jane", "Richard Roe"], year: 2020 },
    pdf: { reason: "forbidden" },
    client: c,
  });
  assert.deepEqual([r.kind, r.hasPdf, r.pdfReason], ["wishlist", false, "forbidden"]);
  assert.equal(calls.length, 2);
  assert.equal(JSON.parse(calls[0].init.body).identifier, "S0140673620301835");
  const second = JSON.parse(calls[1].init.body);
  assert.equal(second.identifier, undefined);
  assert.deepEqual(second.authors, [{ family: "Doe", given: "Jane" }, { family: "Roe", given: "Richard" }]);
});

test("a bad token is not retried", async () => {
  const { c, calls } = client(() => resp(401, {}));
  await assert.rejects(save({ meta: { identifier: "x", title: "T", authors: [] }, pdf: { reason: "no_pdf" }, client: c }), { code: "bad_token" });
  assert.equal(calls.length, 1);
});

test("helpers", () => {
  assert.deepEqual(splitAuthor("Doe, Jane Q."), { family: "Doe", given: "Jane Q." });
  assert.deepEqual(splitAuthor("Madonna"), { family: "Madonna", given: null });
  assert.equal(hasPdfMagic(new TextEncoder().encode("%PDF-1.4")), true);
  assert.equal(hasPdfMagic(new TextEncoder().encode("<html>")), false);
  assert.equal(looksLikePdfUrl("https://x.example/a/b.pdf?x=1"), true);
  assert.equal(looksLikePdfUrl("https://x.example/article"), false);
});
