export interface SseEvent {
  event: string;
  data: string;
}

/**
 * Server-sent events の増分パーサ。チャンクが行の途中で切れても、複数のイベントが 1 チャンクに入っても正しく扱う。
 * `:` で始まるコメント行（ping）は捨てる。`event:` 名のないイベントは "message"。
 */
export class SseParser {
  private buffer = "";
  private event = "";
  private data: string[] = [];

  push(chunk: string): SseEvent[] {
    this.buffer += chunk;
    const out: SseEvent[] = [];
    for (;;) {
      const nl = this.buffer.search(/\r\n|\n|\r/);
      if (nl < 0) break;
      // 末尾が "\r" だけなら、次のチャンクの "\n" と合わせて 1 つの改行かもしれないので待つ
      if (this.buffer[nl] === "\r" && nl + 1 === this.buffer.length) break;
      const sep = this.buffer.startsWith("\r\n", nl) ? 2 : 1;
      const line = this.buffer.slice(0, nl);
      this.buffer = this.buffer.slice(nl + sep);
      const ev = this.line(line);
      if (ev) out.push(ev);
    }
    return out;
  }

  private line(line: string): SseEvent | null {
    if (line === "") {
      if (this.data.length === 0) {
        this.event = "";
        return null;
      }
      const ev = { event: this.event || "message", data: this.data.join("\n") };
      this.event = "";
      this.data = [];
      return ev;
    }
    if (line.startsWith(":")) return null;
    const i = line.indexOf(":");
    const field = i < 0 ? line : line.slice(0, i);
    let value = i < 0 ? "" : line.slice(i + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") this.event = value;
    else if (field === "data") this.data.push(value);
    return null;
  }
}
