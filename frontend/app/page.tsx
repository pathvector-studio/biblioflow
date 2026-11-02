"use client";

import { useEffect, useState } from "react";

type Health = "checking" | "ok" | "error";

export default function Home() {
  const [health, setHealth] = useState<Health>("checking");

  useEffect(() => {
    fetch("/api/v1/healthz")
      .then((r) => setHealth(r.ok ? "ok" : "error"))
      .catch(() => setHealth("error"));
  }, []);

  const label = { checking: "確認中...", ok: "正常 (200)", error: "接続できません" }[health];

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-4 px-6 py-24">
      <h1 className="text-3xl font-bold">BiblioFlow</h1>
      <p className="text-slate-600">Phase 1 (インフラ・認証基盤) が稼働しています。</p>
      <p className="rounded border border-slate-200 bg-white px-4 py-3 text-sm">
        Backend health: <span className="font-mono font-semibold">{label}</span>
      </p>
    </main>
  );
}
