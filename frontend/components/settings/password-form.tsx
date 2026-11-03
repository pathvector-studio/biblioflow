"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, describeError } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const MIN_LEN = 12;

export function PasswordForm() {
  const { t } = useI18n();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);

  const change = useMutation({
    mutationFn: () => api.post("/auth/password", { current_password: current, new_password: next }),
    onSuccess: () => {
      setCurrent("");
      setNext("");
      setConfirm("");
      toast.success(t.settings.passwordChanged);
    },
    onError: (e) =>
      setError(describeError(t, e, { 400: t.settings.wrongCurrent, 422: t.settings.passwordPolicy })),
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (next.length < MIN_LEN) return setError(t.settings.passwordPolicy);
    if (next !== confirm) return setError(t.settings.passwordMismatch);
    change.mutate();
  }

  return (
    <section className="space-y-3">
      <h2 className="text-base font-semibold">{t.settings.passwordTitle}</h2>
      <form onSubmit={onSubmit} className="max-w-sm space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="pw-current">{t.settings.currentPassword}</Label>
          <Input id="pw-current" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="pw-new">{t.settings.newPassword}</Label>
          <Input id="pw-new" type="password" autoComplete="new-password" required minLength={MIN_LEN} maxLength={128} value={next} onChange={(e) => setNext(e.target.value)} />
          <p className="text-xs text-muted-foreground">{t.settings.passwordPolicy}</p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="pw-confirm">{t.settings.confirmPassword}</Label>
          <Input id="pw-confirm" type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <Button type="submit" disabled={change.isPending}>
          {t.settings.changePassword}
        </Button>
      </form>
    </section>
  );
}
