"use client";

import { PasswordForm } from "@/components/settings/password-form";
import { WorkspacesPanel } from "@/components/settings/workspaces-panel";
import { AutoExportPanel } from "@/components/settings/auto-export-panel";
import { TokensPanel } from "@/components/settings/tokens-panel";

export default function SettingsPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-10 p-4">
      <PasswordForm />
      <WorkspacesPanel />
      <AutoExportPanel />
      <TokensPanel />
    </div>
  );
}
