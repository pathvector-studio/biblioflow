import type { Scaled } from "react-pdf-highlighter-extended";
import type { PdfBox } from "@/lib/types";

/**
 * PDF のポイント（原点左下）の矩形を、ビューア（原点左上）の Scaled にする。
 * width / height はページの大きさのまま渡す。ライブラリが現在の表示倍率に直すので、ズームしても合う。
 */
export function pdfBoxToScaled(box: PdfBox, pageNumber: number): Scaled {
  return {
    x1: box.x1,
    x2: box.x2,
    y1: box.height - box.y2,
    y2: box.height - box.y1,
    width: box.width,
    height: box.height,
    pageNumber,
  };
}

/** 複数の矩形を囲む矩形（同じページの矩形だけを渡す）。 */
export function unionScaled(rects: Scaled[]): Scaled {
  const [first, ...rest] = rects;
  return rest.reduce(
    (u, r) => ({
      ...u,
      x1: Math.min(u.x1, r.x1),
      y1: Math.min(u.y1, r.y1),
      x2: Math.max(u.x2, r.x2),
      y2: Math.max(u.y2, r.y2),
    }),
    first,
  );
}
