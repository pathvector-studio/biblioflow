"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { api, describeError } from "@/lib/api";
import { useI18n, type Locale } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";

/** 同一オリジンの相対パスだけを許可する（オープンリダイレクト対策） */
function safeNext(raw: string | null): string {
  if (raw && raw.startsWith("/") && !raw.startsWith("//") && !raw.includes("\\")) return raw;
  return "/library";
}

export default function LoginPage() {
  const { t, locale, setLocale } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/login", { email, password }, { noAuthRedirect: true });
      queryClient.clear();
      router.replace(safeNext(new URLSearchParams(window.location.search).get("next")));
    } catch (err) {
      setError(
        describeError(t, err, {
          401: t.login.invalid,
          422: t.login.invalid,
          429: t.login.rateLimited,
        }),
      );
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={onSubmit} className="w-full max-w-sm space-y-4 rounded-lg border bg-card p-6 shadow-sm">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold">BiblioFlow</h1>
          <Select aria-label={t.nav.language} value={locale} onChange={(e) => setLocale(e.target.value as Locale)}>
            <option value="ja">日本語</option>
            <option value="en">English</option>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="email">{t.login.email}</Label>
          <Input
            id="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password">{t.login.password}</Label>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <Button type="submit" className="w-full" disabled={busy}>
          {t.login.submit}
        </Button>
      </form>
    </div>
  );
}
