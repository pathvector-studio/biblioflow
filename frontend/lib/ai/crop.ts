import type { Scaled } from "react-pdf-highlighter-extended";

/** `POST /papers/{id}/explain-region` の本体（質問と会話 ID は呼び出し側で足す） */
export interface RegionCapture {
  page_number: number;
  /** PDF のポイント、原点左下（y1 < y2）。width / height はページの大きさ */
  rect: { x1: number; y1: number; x2: number; y2: number; width: number; height: number };
  /** PNG の data URL */
  image: string;
}

export const MAX_IMAGE_SIDE = 2048;
export const MAX_IMAGE_BYTES = 1.5 * 1024 * 1024;

/** pdf.js のページ表示の、使う部分だけ */
export interface PageViewLike {
  div: HTMLElement;
  canvas?: HTMLCanvasElement | null;
  pdfPage?: { getViewport(p: { scale: number }): { width: number; height: number } };
}

/**
 * ビューアの矩形（原点左上。Scaled の x / y は width / height に対する割合で決まる）を、
 * ページの描画済み canvas から PNG に切り抜き、PDF のポイント（原点左下）の矩形にする。
 * 長辺が 2048 px を超える、または 1.5 MiB を超えるときは縮小する。
 */
export async function captureRegion(pageView: PageViewLike, s: Scaled): Promise<RegionCapture | null> {
  const canvas = pageView.canvas ?? pageView.div.querySelector("canvas");
  const page = pageView.pdfPage;
  if (!canvas || !page || canvas.width === 0 || s.width <= 0 || s.height <= 0) return null;

  const { width: pageW, height: pageH } = page.getViewport({ scale: 1 });
  const clamp = (v: number) => Math.min(1, Math.max(0, v));
  const fx1 = clamp(Math.min(s.x1, s.x2) / s.width);
  const fx2 = clamp(Math.max(s.x1, s.x2) / s.width);
  const fy1 = clamp(Math.min(s.y1, s.y2) / s.height);
  const fy2 = clamp(Math.max(s.y1, s.y2) / s.height);
  const sx = Math.round(fx1 * canvas.width);
  const sy = Math.round(fy1 * canvas.height);
  const sw = Math.round((fx2 - fx1) * canvas.width);
  const sh = Math.round((fy2 - fy1) * canvas.height);
  if (sw < 2 || sh < 2) return null;

  let scale = Math.min(1, MAX_IMAGE_SIDE / Math.max(sw, sh));
  for (let attempt = 0; attempt < 6; attempt++) {
    const out = document.createElement("canvas");
    out.width = Math.max(1, Math.round(sw * scale));
    out.height = Math.max(1, Math.round(sh * scale));
    const ctx = out.getContext("2d");
    if (!ctx) return null;
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, out.width, out.height);
    ctx.drawImage(canvas, sx, sy, sw, sh, 0, 0, out.width, out.height);
    const blob = await new Promise<Blob | null>((resolve) => out.toBlob(resolve, "image/png"));
    if (!blob) return null;
    if (blob.size <= MAX_IMAGE_BYTES) {
      const image = await new Promise<string>((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result));
        r.onerror = () => reject(r.error);
        r.readAsDataURL(blob);
      });
      return {
        page_number: s.pageNumber,
        rect: {
          x1: fx1 * pageW,
          x2: fx2 * pageW,
          // 原点を左下にする: 上端の割合が小さいほど y は大きい
          y1: pageH - fy2 * pageH,
          y2: pageH - fy1 * pageH,
          width: pageW,
          height: pageH,
        },
        image,
      };
    }
    scale *= 0.75;
  }
  return null;
}
