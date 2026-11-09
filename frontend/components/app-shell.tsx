"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BookOpen, LogOut, Plus, Settings, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { api, describeError } from "@/lib/api";
import { useI18n, type Locale } from "@/lib/i18n";
import { useMe, useWorkspace } from "@/lib/session";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";

export function AppShell({ children }: { children: React.ReactNode }) {
  const { t, locale, setLocale } = useI18n();
  const { workspaces, current, setCurrentId } = useWorkspace();
  const me = useMe();
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();

  const logout = useMutation({
    mutationFn: () => api.post<void>("/auth/logout"),
    onSuccess: () => {
      queryClient.clear();
      router.replace("/login");
    },
    onError: (e) => toast.error(describeError(t, e)),
  });

  const nav = [
    { href: "/library", label: t.nav.library, icon: BookOpen },
    { href: "/add", label: t.nav.add, icon: Plus },
    { href: "/ai", label: t.nav.ai, icon: Sparkles },
    { href: "/settings", label: t.nav.settings, icon: Settings },
  ];

  return (
    <div className="flex h-screen flex-col">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-2">
        <Link href="/library" className="text-base font-semibold tracking-tight">
          BiblioFlow
        </Link>
        <nav className="flex items-center gap-1">
          {nav.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm hover:bg-accent",
                pathname.startsWith(href) && "bg-accent font-medium",
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Select
            aria-label={t.nav.workspace}
            value={current.id}
            onChange={(e) => setCurrentId(e.target.value)}
            className="max-w-48"
          >
            {workspaces.map((w) => (
              <option key={w.id} value={w.id}>
                {w.kind === "personal" ? t.nav.personal : w.name}
                {w.role === "viewer" ? ` (${t.roles.viewer})` : ""}
              </option>
            ))}
          </Select>
          <Select
            aria-label={t.nav.language}
            value={locale}
            onChange={(e) => setLocale(e.target.value as Locale)}
          >
            <option value="ja">日本語</option>
            <option value="en">English</option>
          </Select>
          <span className="hidden text-xs text-muted-foreground lg:inline">{me.data?.user.email}</span>
          <Button variant="ghost" size="sm" onClick={() => logout.mutate()} disabled={logout.isPending}>
            <LogOut className="h-4 w-4" />
            {t.nav.logout}
          </Button>
        </div>
      </header>
      <main className="min-h-0 flex-1 overflow-auto">{children}</main>
    </div>
  );
}
