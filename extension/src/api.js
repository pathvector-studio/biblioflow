// BiblioFlow API client. Talks to one configured origin only. `fetchImpl` is injectable for tests.

export class ApiError extends Error {
  constructor(code, message, { status = 0, paperId = null } = {}) {
    super(message || code);
    this.code = code; // bad_token | forbidden | access_login | network | too_large | conflict | rate_limited | server | invalid
    this.status = status;
    this.paperId = paperId;
  }
}

export function normalizeBaseUrl(raw) {
  let u;
  try {
    u = new URL(String(raw).trim());
  } catch {
    throw new ApiError("invalid", "invalid URL");
  }
  if (u.protocol !== "https:" && u.protocol !== "http:") throw new ApiError("invalid", "invalid URL");
  return u.origin;
}

export function createClient({ baseUrl, pat, cfClientId, cfClientSecret, fetchImpl = globalThis.fetch }) {
  const origin = normalizeBaseUrl(baseUrl);

  function headers() {
    const h = { Authorization: `Bearer ${pat}`, Accept: "application/json" };
    if (cfClientId && cfClientSecret) {
      h["CF-Access-Client-Id"] = cfClientId;
      h["CF-Access-Client-Secret"] = cfClientSecret;
    }
    return h;
  }

  async function request(path, init = {}) {
    let res;
    try {
      res = await fetchImpl(origin + path, {
        ...init,
        headers: { ...headers(), ...(init.headers || {}) },
        credentials: "include",
        redirect: "manual",
      });
    } catch (e) {
      throw new ApiError("network", e && e.message);
    }
    // Cloudflare Access (and similar) answers an unauthenticated browser with a redirect to a login page.
    if (res.type === "opaqueredirect" || (res.status >= 300 && res.status < 400)) {
      throw new ApiError("access_login", "redirected to a login page", { status: res.status });
    }
    const ctype = (res.headers.get("content-type") || "").toLowerCase();
    let body = null;
    if (ctype.includes("application/json")) {
      try {
        body = await res.json();
      } catch {
        body = null;
      }
    }
    if (res.ok) {
      if (body === null) throw new ApiError("access_login", "not a BiblioFlow API response", { status: res.status });
      return body;
    }
    const detail = body && typeof body.detail === "string" ? body.detail : "";
    const paperId = body && typeof body.paper_id === "string" ? body.paper_id : null;
    if (res.status === 401) throw new ApiError("bad_token", detail, { status: 401 });
    if (res.status === 403) {
      throw new ApiError(body === null ? "access_login" : "forbidden", detail, { status: 403 });
    }
    if (res.status === 409) throw new ApiError("conflict", detail, { status: 409, paperId });
    if (res.status === 413) throw new ApiError("too_large", detail, { status: 413 });
    if (res.status === 429) throw new ApiError("rate_limited", detail, { status: 429 });
    if (res.status >= 500) throw new ApiError("server", detail, { status: res.status });
    throw new ApiError("invalid", detail, { status: res.status });
  }

  return {
    origin,
    listWorkspaces: () => request("/api/v1/workspaces"),
    // An authenticated GET: proves the URL, the token and the read:papers scope.
    testConnection: async () => {
      const ws = await request("/api/v1/workspaces");
      return { workspaces: Array.isArray(ws) ? ws.length : 0 };
    },
    uploadPdf({ bytes, workspaceId, identifier, paperId, status, filename = "paper.pdf" }) {
      const form = new FormData();
      form.append("file", new Blob([bytes], { type: "application/pdf" }), filename);
      if (workspaceId) form.append("workspace_id", workspaceId);
      if (identifier) form.append("identifier", identifier);
      if (paperId) form.append("paper_id", paperId);
      if (status) form.append("status", status);
      return request("/api/v1/papers/upload", { method: "POST", body: form });
    },
    metadataOnly(payload) {
      return request("/api/v1/papers/metadata-only", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    },
    paperUrl: (id) => `${origin}/papers/${encodeURIComponent(id)}`,
  };
}
