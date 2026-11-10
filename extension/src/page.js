// Functions injected into the page with scripting.executeScript({ func }). Each must be self-contained
// (it is serialized), and none receives a secret: only a URL and a size cap.

export function collectPage() {
  const metas = [];
  for (const el of document.querySelectorAll("meta")) {
    const name = el.getAttribute("name") || el.getAttribute("property");
    const content = el.getAttribute("content");
    if (name && content) metas.push({ name, content });
  }
  // IEEE Xplore keeps the record in an inline script instead of <meta> tags (see ieeeMetas in extract.js).
  let embedded = null;
  if (/(^|\.)ieeexplore\.ieee\.org$/i.test(location.hostname)) {
    for (const s of document.scripts) {
      const text = s.src ? "" : s.textContent || "";
      if (text.includes("xplGlobal.document.metadata")) {
        embedded = text.slice(0, 500000);
        break;
      }
    }
  }
  return {
    url: location.href,
    title: document.title || "",
    contentType: document.contentType || "",
    metas,
    embedded,
  };
}

// Fetch a PDF with the page's own cookies. Returns base64 (executeScript results must be JSON).
export async function fetchPdfInPage(url, maxBytes) {
  try {
    const res = await fetch(url, { credentials: "include", redirect: "follow" });
    if (!res.ok) return { ok: false, reason: res.status === 401 || res.status === 403 ? "forbidden" : "http" };
    const declared = Number(res.headers.get("content-length") || 0);
    if (declared > maxBytes) return { ok: false, reason: "too_large" };
    const blob = await res.blob();
    if (blob.size > maxBytes) return { ok: false, reason: "too_large" };
    const head = new Uint8Array(await blob.slice(0, 1024).arrayBuffer());
    const magic = [0x25, 0x50, 0x44, 0x46, 0x2d]; // %PDF-
    let at = -1;
    for (let i = 0; i + 5 <= head.length; i++) {
      if (magic.every((b, k) => head[i + k] === b)) {
        at = i;
        break;
      }
    }
    if (at !== 0) return { ok: false, reason: "not_pdf" };
    const dataUrl = await new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result));
      r.onerror = () => reject(r.error);
      r.readAsDataURL(blob);
    });
    return { ok: true, b64: dataUrl.slice(dataUrl.indexOf(",") + 1), size: blob.size };
  } catch {
    return { ok: false, reason: "network" };
  }
}
