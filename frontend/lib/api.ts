import type { Dict } from "@/lib/i18n/ja";
import type { PaperDetail } from "@/lib/types";

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string | null,
  ) {
    super(detail ?? `HTTP ${status}`);
  }
}

function redirectToLogin() {
  if (typeof window === "undefined" || window.location.pathname.startsWith("/login")) return;
  const next = window.location.pathname + window.location.search;
  window.location.assign(`/login?next=${encodeURIComponent(next)}`);
}

function detailOf(body: unknown): string | null {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    return typeof d === "string" ? d : null;
  }
  return null;
}

interface RequestOptions {
  method?: string;
  query?: Record<string, string | number | undefined | null>;
  body?: unknown;
  /** ログイン失敗の 401 をログイン画面への遷移にしない */
  noAuthRedirect?: boolean;
}

async function send(path: string, opts: RequestOptions = {}): Promise<Response> {
  const url = new URL(BASE + path, window.location.origin);
  for (const [k, v] of Object.entries(opts.query ?? {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  let res: Response;
  try {
    res = await fetch(url, {
      method: opts.method ?? "GET",
      credentials: "same-origin",
      headers: opts.body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new ApiError(0, null);
  }
  if (!res.ok) {
    if (res.status === 401 && !opts.noAuthRedirect) redirectToLogin();
    const body: unknown = await res.json().catch(() => null);
    throw new ApiError(res.status, detailOf(body));
  }
  return res;
}

async function json<T>(path: string, opts?: RequestOptions): Promise<T> {
  const res = await send(path, opts);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  get: <T>(path: string, query?: RequestOptions["query"]) => json<T>(path, { query }),
  post: <T>(path: string, body?: unknown, opts?: Pick<RequestOptions, "noAuthRedirect">) =>
    json<T>(path, { method: "POST", body: body ?? {}, ...opts }),
  patch: <T>(path: string, body: unknown) => json<T>(path, { method: "PATCH", body }),
  del: (path: string) => json<void>(path, { method: "DELETE" }),
  /** ファイルとして取得してブラウザのダウンロードを起こす（エラーは ApiError） */
  async download(path: string, query: RequestOptions["query"], fallbackName: string): Promise<void> {
    const res = await send(path, { query });
    const m = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "");
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = m?.[1] ?? fallbackName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
  /** 件数ヘッダ付きの一覧取得 */
  async getWithTotal<T>(path: string, query: RequestOptions["query"]): Promise<{ items: T[]; total: number }> {
    const res = await send(path, { query });
    const items = (await res.json()) as T[];
    return { items, total: Number(res.headers.get("X-Total-Count") ?? items.length) };
  },
};

export function uploadPdf(
  file: File,
  target: { workspaceId: string; paperId?: string },
  onProgress: (fraction: number) => void,
): Promise<PaperDetail> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    form.append("workspace_id", target.workspaceId);
    if (target.paperId) form.append("paper_id", target.paperId);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}/papers/upload`);
    xhr.responseType = "json";
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onerror = () => reject(new ApiError(0, null));
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response as PaperDetail);
        return;
      }
      if (xhr.status === 401) redirectToLogin();
      reject(new ApiError(xhr.status, detailOf(xhr.response)));
    };
    xhr.send(form);
  });
}

/** API エラーを画面に出す文言にする。`overrides` はステータス別に呼び出し側で差し替える */
export function describeError(t: Dict, e: unknown, overrides: Partial<Record<number, string>> = {}): string {
  if (!(e instanceof ApiError)) return t.errors.unknown;
  const override = overrides[e.status];
  if (override) return override;
  switch (e.status) {
    case 0:
      return t.errors.network;
    case 401:
      return t.errors.e401;
    case 403:
      return t.errors.e403;
    case 404:
      return t.errors.e404;
    case 409:
      return t.errors.e409;
    case 413:
      return t.errors.e413;
    case 415:
      return t.errors.e415;
    case 422:
      return e.detail ? `${t.errors.e422}: ${e.detail}` : t.errors.e422;
    case 429:
      return t.errors.e429;
    default:
      return e.status >= 500 ? t.errors.e5xx : t.errors.unknown;
  }
}
