import { errorText, localize, send, t } from "./src/ui.js";

const $ = (id) => document.getElementById(id);
const ext = globalThis.browser ?? globalThis.chrome;

function setStatus(text) {
  $("status").textContent = text;
  $("status").hidden = !text;
}

function showResult(r) {
  const box = $("result");
  box.hidden = false;
  box.replaceChildren();
  if (!r.ok) {
    box.className = "error";
    box.textContent = errorText(r.error);
    return;
  }
  box.className = "ok";
  const lines = [t(`result_${r.kind}`)];
  if (!r.hasPdf) lines.push(t("result_no_pdf", [t(`pdf_reason_${r.pdfReason || "no_pdf"}`)]));
  const p = document.createElement("span");
  p.textContent = lines.join(" ");
  const a = document.createElement("a");
  a.href = r.url;
  a.target = "_blank";
  a.rel = "noopener";
  a.textContent = t("link_open_paper");
  box.append(p, document.createElement("br"), a);
}

async function main() {
  localize();
  $("open-options").addEventListener("click", (e) => {
    e.preventDefault();
    ext.runtime.openOptionsPage();
  });
  const r = await send({ type: "inspect" });
  if (!r || !r.ok) {
    setStatus(errorText(r && r.error));
    return;
  }
  if (!r.configured) {
    setStatus(t("popup_not_configured"));
    return;
  }
  if (!r.meta.found) {
    setStatus(t("popup_nothing_found"));
    return;
  }
  setStatus("");
  $("detected").hidden = false;
  $("d-title").textContent = r.meta.title || "-";
  const shown = r.meta.authors.slice(0, 4).join(", ") + (r.meta.authors.length > 4 ? " …" : "");
  $("d-authors").textContent = shown || "-";
  $("d-year").textContent = r.meta.year ? String(r.meta.year) : "-";
  $("d-id").textContent = r.meta.identifier || "-";
  $("d-pdf").textContent = r.pdfCandidate ? t("pdf_maybe") : t("pdf_none");

  const sel = $("workspace");
  for (const w of r.workspaces) {
    const o = document.createElement("option");
    o.value = w.id;
    o.textContent = w.name;
    o.disabled = w.role === "viewer";
    sel.append(o);
  }
  if (r.workspaceError) {
    $("ws-row").hidden = true;
    showResult({ ok: false, error: r.workspaceError });
    $("save").disabled = true;
    return;
  }
  const usable = r.workspaces.filter((w) => w.role !== "viewer");
  const pick = usable.find((w) => w.id === r.lastWorkspaceId) || usable[0];
  if (pick) sel.value = pick.id;

  $("save").addEventListener("click", async () => {
    $("save").disabled = true;
    setStatus(t("popup_saving"));
    const res = await send({ type: "save", workspaceId: sel.value });
    setStatus("");
    showResult(res || { ok: false, error: "network" });
    $("save").disabled = false;
  });
}

main();
