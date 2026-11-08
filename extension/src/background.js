// Background (service worker in Chrome, event page in Firefox). The only place that talks to BiblioFlow
// and the only place that reads the PAT. Reads the page only in response to a click in the popup.

import { ApiError, createClient } from "./api.js";
import { b64ToBytes, hasPdfMagic, looksLikePdfUrl } from "./bytes.js";
import { extract } from "./extract.js";
import { collectPage, fetchPdfInPage } from "./page.js";
import { save } from "./save.js";
import { ext_ as ext, loadSettings, saveSettings } from "./settings.js";

async function activeTab() {
  const [tab] = await ext.tabs.query({ active: true, currentWindow: true });
  if (!tab || tab.id === undefined) throw new ApiError("invalid", "no active tab");
  return tab;
}

async function runInTab(tabId, func, args = []) {
  const [res] = await ext.scripting.executeScript({ target: { tabId }, func, args });
  return res ? res.result : undefined;
}

async function inspect() {
  const tab = await activeTab();
  let page = null;
  try {
    page = await runInTab(tab.id, collectPage);
  } catch {
    page = null; // restricted page or a built-in PDF viewer
  }
  const url = (page && page.url) || tab.url || "";
  const meta = extract({ url, metas: page ? page.metas : [], title: page ? page.title : tab.title });
  const tabIsPdf = (page && /pdf/i.test(page.contentType)) || (!page && looksLikePdfUrl(url));
  const pdfUrl = tabIsPdf ? url : meta.pdfUrl;
  if (tabIsPdf && !meta.title) meta.title = (tab.title || "").replace(/\.pdf$/i, "") || null;
  return { tabId: tab.id, meta: { ...meta, found: Boolean(meta.title || meta.identifier) }, pdfUrl, tabIsPdf };
}

async function obtainPdf(info, maxBytes) {
  if (!info.pdfUrl) return { reason: "no_pdf" };
  let r;
  try {
    r = await runInTab(info.tabId, fetchPdfInPage, [info.pdfUrl, maxBytes]);
  } catch {
    r = null;
  }
  if (!r && info.tabIsPdf) {
    // The built-in viewer does not accept scripts; fetch the tab's own URL with the browser's cookies.
    // This works only when the URL is on a host the user granted (e.g. the BiblioFlow host); otherwise it fails.
    try {
      const res = await fetch(info.pdfUrl, { credentials: "include" });
      if (res.ok) {
        const buf = new Uint8Array(await res.arrayBuffer());
        r = buf.length <= maxBytes ? { ok: true, bytes: buf } : { ok: false, reason: "too_large" };
      } else r = { ok: false, reason: "forbidden" };
    } catch {
      r = { ok: false, reason: "network" };
    }
  }
  if (!r || !r.ok) return { reason: (r && r.reason) || "network" };
  const bytes = r.bytes || b64ToBytes(r.b64);
  if (!hasPdfMagic(bytes)) return { reason: "not_pdf" };
  if (bytes.length > maxBytes) return { reason: "too_large" };
  return { bytes };
}

async function clientFromSettings() {
  const s = await loadSettings();
  if (!s.baseUrl || !s.pat) throw new ApiError("not_configured", "not configured");
  return { s, client: createClient({ baseUrl: s.baseUrl, pat: s.pat, cfClientId: s.cfClientId, cfClientSecret: s.cfClientSecret }) };
}

function fail(e) {
  if (e instanceof ApiError) return { ok: false, error: e.code, status: e.status };
  return { ok: false, error: "network" };
}

const handlers = {
  async inspect() {
    const { s, client } = await clientFromSettings().catch((e) => {
      if (e instanceof ApiError && e.code === "not_configured") return { s: null, client: null };
      throw e;
    });
    const info = await inspect();
    const out = { ok: true, configured: Boolean(client), meta: info.meta, pdfCandidate: Boolean(info.pdfUrl), workspaces: [], lastWorkspaceId: "" };
    if (client) {
      try {
        out.workspaces = (await client.listWorkspaces()).map((w) => ({ id: w.id, name: w.name, kind: w.kind, role: w.role }));
        out.lastWorkspaceId = s.lastWorkspaceId;
      } catch (e) {
        out.workspaceError = fail(e).error;
      }
    }
    return out;
  },

  async save({ workspaceId }) {
    const { s, client } = await clientFromSettings();
    const info = await inspect();
    if (!info.meta.found) return { ok: false, error: "nothing_found" };
    const maxBytes = Math.max(1, Number(s.maxPdfMb) || 50) * 1024 * 1024;
    const pdf = await obtainPdf(info, maxBytes);
    const result = await save({ meta: info.meta, pdf, client, workspaceId: workspaceId || undefined });
    if (workspaceId) await saveSettings({ lastWorkspaceId: workspaceId });
    return {
      ok: true,
      kind: result.kind,
      hasPdf: result.hasPdf,
      pdfReason: result.pdfReason || null,
      title: (result.paper && result.paper.title) || info.meta.title,
      url: client.paperUrl(result.paperId),
    };
  },

  async testConnection({ baseUrl, pat, cfClientId, cfClientSecret }) {
    // Called from the options page with the values in the form; nothing is stored here.
    const client = createClient({ baseUrl, pat, cfClientId, cfClientSecret });
    const r = await client.testConnection();
    return { ok: true, workspaces: r.workspaces };
  },
};

ext.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Only our own extension pages may call the background.
  if (sender.id !== ext.runtime.id || !msg || !handlers[msg.type]) return false;
  handlers[msg.type](msg).then(sendResponse, (e) => sendResponse(fail(e)));
  return true;
});
