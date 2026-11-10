import { normalizeBaseUrl } from "./src/api.js";
import { loadSettings, saveSettings } from "./src/settings.js";
import { errorText, localize, send, t } from "./src/ui.js";

const ext = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);
const msg = (text, cls = "") => {
  $("msg").textContent = text;
  $("msg").className = cls;
};

// Secrets are never written back into the form: an empty field keeps the stored value, but only for the server
// they were saved for. After the URL changes they have to be typed again, so a stored token is never sent to a
// different host.
async function current(origin) {
  const s = await loadSettings();
  const same = s.baseUrl === origin;
  return {
    baseUrl: $("baseUrl").value.trim(),
    pat: $("pat").value.trim() || (same ? s.pat : ""),
    cfClientId: $("cfClientId").value.trim(),
    cfClientSecret: $("cfClientSecret").value.trim() || (same ? s.cfClientSecret : ""),
    maxPdfMb: Math.max(1, Number($("maxPdfMb").value) || 50),
  };
}

async function init() {
  localize();
  const s = await loadSettings();
  $("baseUrl").value = s.baseUrl;
  $("maxPdfMb").value = s.maxPdfMb;
  $("cfClientId").value = s.cfClientId;
  if (s.pat) $("pat").placeholder = t("opt_secret_set");
  if (s.cfClientSecret) $("cfClientSecret").placeholder = t("opt_secret_set");
}

// permissions.request must be called synchronously from the click, so it is the first await in each handler.
async function origin() {
  let o;
  try {
    o = normalizeBaseUrl($("baseUrl").value);
  } catch {
    msg(errorText("invalid"), "error");
    return null;
  }
  const granted = await ext.permissions.request({ origins: [`${o}/*`] });
  if (!granted) {
    msg(t("opt_permission_denied"), "error");
    return null;
  }
  return o;
}

async function onSave(ev) {
  ev.preventDefault();
  const o = await origin();
  if (!o) return;
  const v = await current(o);
  if (!v.pat) {
    msg(t("opt_token_needed"), "error");
    return;
  }
  await saveSettings({ ...v, baseUrl: o });
  $("baseUrl").value = o;
  $("pat").value = "";
  $("cfClientSecret").value = "";
  msg(t("opt_saved"), "ok");
}

async function onTest() {
  const o = await origin();
  if (!o) return;
  msg(t("opt_testing"));
  const v = await current(o);
  if (!v.pat) {
    msg(t("opt_token_needed"), "error");
    return;
  }
  const r = await send({ type: "testConnection", ...v, baseUrl: o });
  if (r && r.ok) msg(t("opt_connected", [String(r.workspaces)]), "ok");
  else msg(errorText(r && r.error), "error");
}

$("form").addEventListener("submit", onSave);
$("test").addEventListener("click", onTest);
init();
