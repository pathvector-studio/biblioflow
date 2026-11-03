"use client";

import { AuthGate } from "@/components/auth-gate";
import { AppShell } from "@/components/app-shell";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGate>
      <AppShell>{children}</AppShell>
    </AuthGate>
  );
}
