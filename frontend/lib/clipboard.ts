/** http の LAN アクセスでは navigator.clipboard が使えないため、execCommand にフォールバックする */
export async function copyText(text: string): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // フォールバックへ
    }
  }
  const el = document.createElement("textarea");
  el.value = text;
  el.setAttribute("readonly", "");
  el.style.position = "fixed";
  el.style.opacity = "0";
  document.body.appendChild(el);
  el.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    document.body.removeChild(el);
  }
}

/**
 * 書式つき（text/html）とプレーンテキスト（text/plain）の両方をクリップボードに入れる。
 * Word や Google Docs には text/html が、メモ帳などには text/plain が貼られる。
 * http の LAN アクセスでは ClipboardItem が使えないので、HTML を選択して execCommand("copy") する。
 * `html` はサーバーがエスケープ済みの参考文献（lib/bibliography.ts）だけを渡すこと。
 */
export async function copyHtml(html: string, text: string): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard && typeof ClipboardItem !== "undefined") {
    try {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/html": new Blob([html], { type: "text/html" }),
          "text/plain": new Blob([text], { type: "text/plain" }),
        }),
      ]);
      return true;
    } catch {
      // フォールバックへ
    }
  }
  const el = document.createElement("div");
  el.innerHTML = html;
  el.style.position = "fixed";
  el.style.opacity = "0";
  document.body.appendChild(el);
  const selection = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(el);
  selection?.removeAllRanges();
  selection?.addRange(range);
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    selection?.removeAllRanges();
    document.body.removeChild(el);
  }
}
