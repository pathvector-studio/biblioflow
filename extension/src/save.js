// The save flow: PDF in hand -> upload; otherwise -> metadata-only (wishlist). Pure apart from `client`.

import { ApiError } from "./api.js";

export function splitAuthor(name) {
  const s = String(name).trim();
  if (s.includes(",")) {
    const [family, ...rest] = s.split(",");
    return { family: family.trim().slice(0, 200), given: rest.join(",").trim().slice(0, 200) || null };
  }
  const parts = s.split(/\s+/);
  if (parts.length === 1) return { family: parts[0].slice(0, 200), given: null };
  return { family: parts.pop().slice(0, 200), given: parts.join(" ").slice(0, 200) };
}

export function titlePayload(meta, workspaceId) {
  return {
    title: meta.title.slice(0, 1000),
    authors: meta.authors.slice(0, 500).filter(Boolean).map(splitAuthor).filter((a) => a.family),
    year: meta.year || undefined,
    venue: meta.venue ? meta.venue.slice(0, 500) : undefined,
    workspace_id: workspaceId || undefined,
  };
}

/**
 * @param {object} p
 * @param {object} p.meta result of extract()
 * @param {{bytes: ArrayBuffer|Uint8Array}|{reason: string}} p.pdf
 * @returns {Promise<{kind: 'new'|'matched'|'wishlist'|'exists', paper: object|null, paperId: string, hasPdf: boolean, pdfReason?: string}>}
 */
export async function save({ meta, pdf, client, workspaceId, now = Date.now() }) {
  if (pdf && pdf.bytes) {
    try {
      const paper = await client.uploadPdf({ bytes: pdf.bytes, workspaceId, identifier: meta.identifier });
      // The server answers 201 whether it created the paper or linked the PDF to an older (wishlist) one.
      const created = Date.parse(paper.created_at);
      const matched = Number.isFinite(created) && created < now - 10_000;
      return { kind: matched ? "matched" : "new", paper, paperId: paper.id, hasPdf: paper.has_pdf };
    } catch (e) {
      if (e instanceof ApiError && e.code === "conflict" && e.paperId) {
        return { kind: "exists", paper: null, paperId: e.paperId, hasPdf: true };
      }
      throw e;
    }
  }

  const reason = (pdf && pdf.reason) || "no_pdf";
  const attempts = [];
  if (meta.identifier) attempts.push({ identifier: meta.identifier, workspace_id: workspaceId || undefined });
  if (meta.title) attempts.push(titlePayload(meta, workspaceId));
  if (!attempts.length) throw new ApiError("invalid", "nothing to save");
  let last;
  for (const body of attempts) {
    try {
      const paper = await client.metadataOnly(body);
      return { kind: "wishlist", paper, paperId: paper.id, hasPdf: paper.has_pdf, pdfReason: reason };
    } catch (e) {
      if (e instanceof ApiError && e.code === "conflict" && e.paperId) {
        return { kind: "exists", paper: null, paperId: e.paperId, hasPdf: false, pdfReason: reason };
      }
      // An identifier the server cannot resolve (422/404/502): fall back to the title.
      if (e instanceof ApiError && (e.status === 422 || e.status === 404 || e.status === 502 || e.code === "server")) {
        last = e;
        continue;
      }
      throw e;
    }
  }
  throw last;
}
