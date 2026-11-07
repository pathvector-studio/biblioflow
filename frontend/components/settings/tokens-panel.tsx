"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardCopy } from "lucide-react";
import { toast } from "sonner";
import { api, describeError } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { useI18n } from "@/lib/i18n";
import type { ApiToken, CreatedApiToken } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const SCOPES = ["read:papers", "write:papers", "use:ai"] as const;
const SCOPE_LABELS = { "read:papers": "scopeRead", "write:papers": "scopeWrite", "use:ai": "scopeAi" } as const;
const EXPIRY_DAYS = ["30", "90", "365", ""] as const;

export function TokensPanel() {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["read:papers"]);
  const [days, setDays] = useState<string>("90");
  const [created, setCreated] = useState<CreatedApiToken | null>(null);
  const [revoking, setRevoking] = useState<ApiToken | null>(null);

  const tokens = useQuery({ queryKey: ["tokens"], queryFn: () => api.get<ApiToken[]>("/tokens") });

  const create = useMutation({
    mutationFn: () =>
      api.post<CreatedApiToken>("/tokens", { name: name.trim(), scopes, expires_in_days: days ? Number(days) : null }),
    onSuccess: (tok) => {
      setCreated(tok);
      setName("");
      void queryClient.invalidateQueries({ queryKey: ["tokens"] });
    },
    onError: (e) => toast.error(describeError(t, e)),
  });

  const revoke = useMutation({
    mutationFn: (tok: ApiToken) => api.del(`/tokens/${tok.id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tokens"] }),
    onError: (e) => toast.error(describeError(t, e)),
  });

  const fmtDate = (s: string | null) => (s ? new Date(s).toLocaleString(locale) : "-");

  return (
    <section className="space-y-4">
      <h2 className="text-base font-semibold">{t.settings.tokensTitle}</h2>
      <p className="text-sm text-muted-foreground">{t.settings.tokensHelp}</p>

      <form
        className="grid max-w-xl gap-3 rounded-md border p-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim() && scopes.length > 0) create.mutate();
        }}
      >
        <div className="space-y-1.5">
          <Label htmlFor="tok-name">{t.settings.tokenName}</Label>
          <Input id="tok-name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <fieldset className="flex flex-wrap items-center gap-4 text-sm">
          <legend className="mb-1 text-xs font-medium text-muted-foreground">{t.settings.tokenScopes}</legend>
          {SCOPES.map((s) => (
            <label key={s} className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={scopes.includes(s)}
                onChange={(e) => setScopes((prev) => (e.target.checked ? [...prev, s] : prev.filter((x) => x !== s)))}
              />
              {t.settings[SCOPE_LABELS[s]]}
            </label>
          ))}
        </fieldset>
        <div className="space-y-1.5">
          <Label htmlFor="tok-exp">{t.settings.tokenExpiry}</Label>
          <Select id="tok-exp" value={days} onChange={(e) => setDays(e.target.value)}>
            {EXPIRY_DAYS.map((d) => (
              <option key={d} value={d}>
                {d ? `${d} ${t.settings.days}` : t.settings.never}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Button type="submit" disabled={create.isPending || !name.trim() || scopes.length === 0}>
            {t.settings.issueToken}
          </Button>
        </div>
      </form>

      {created && (
        <div className="space-y-2 rounded-md border border-primary p-3 text-sm">
          <p className="font-medium">{t.settings.tokenShownOnce}</p>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 break-all rounded bg-muted px-2 py-1 text-xs">{created.token}</code>
            <Button
              size="sm"
              variant="outline"
              onClick={async () => {
                if (await copyText(created.token)) toast.success(t.common.copied);
                else toast.error(t.common.copyFailed);
              }}
            >
              <ClipboardCopy className="h-3.5 w-3.5" />
              {t.common.copy}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setCreated(null)}>
              {t.common.close}
            </Button>
          </div>
        </div>
      )}

      {tokens.isError && <p className="text-sm text-destructive">{describeError(t, tokens.error)}</p>}
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-sm">
          <thead className="bg-muted text-left text-xs text-muted-foreground">
            <tr>
              <th className="px-2 py-2">{t.settings.tokenName}</th>
              <th className="px-2 py-2">{t.settings.tokenPrefix}</th>
              <th className="px-2 py-2">{t.settings.tokenScopes}</th>
              <th className="px-2 py-2">{t.settings.tokenExpiresAt}</th>
              <th className="px-2 py-2">{t.settings.tokenLastUsed}</th>
              <th className="px-2 py-2" />
            </tr>
          </thead>
          <tbody>
            {(tokens.data ?? []).map((tok) => (
              <tr key={tok.id} className={tok.revoked_at ? "border-t text-muted-foreground line-through" : "border-t"}>
                <td className="px-2 py-2">{tok.name}</td>
                <td className="px-2 py-2 font-mono text-xs">{tok.token_prefix}…</td>
                <td className="px-2 py-2">
                  {tok.scopes.map((s) => (
                    <Badge key={s} className="mr-1">
                      {s}
                    </Badge>
                  ))}
                </td>
                <td className="px-2 py-2 text-xs">{tok.expires_at ? fmtDate(tok.expires_at) : t.settings.never}</td>
                <td className="px-2 py-2 text-xs">{tok.last_used_at ? fmtDate(tok.last_used_at) : t.settings.neverUsed}</td>
                <td className="px-2 py-2 text-right">
                  {tok.revoked_at ? (
                    <span className="text-xs no-underline">{t.settings.revoked}</span>
                  ) : (
                    <Button size="sm" variant="outline" onClick={() => setRevoking(tok)}>
                      {t.settings.revoke}
                    </Button>
                  )}
                </td>
              </tr>
            ))}
            {tokens.data?.length === 0 && (
              <tr>
                <td colSpan={6} className="px-2 py-6 text-center text-muted-foreground">
                  {t.settings.noTokens}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <AlertDialog open={revoking !== null} onOpenChange={(o) => !o && setRevoking(null)}>
        <AlertDialogContent>
          <AlertDialogTitle>{t.settings.revokeTitle}</AlertDialogTitle>
          <AlertDialogDescription>
            {revoking?.name}
            <br />
            {t.settings.revokeBody}
          </AlertDialogDescription>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction onClick={() => revoking && revoke.mutate(revoking)}>{t.settings.revoke}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
