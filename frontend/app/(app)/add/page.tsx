"use client";

import { useI18n } from "@/lib/i18n";
import { useWorkspace } from "@/lib/session";
import { UploadZone } from "@/components/add/upload-zone";
import { LookupPanel } from "@/components/add/lookup-panel";
import { ImportPanel } from "@/components/add/import-panel";
import { ImportFolderPanel } from "@/components/add/import-folder-panel";

export default function AddPage() {
  const { t } = useI18n();
  const { canEdit } = useWorkspace();

  if (!canEdit) {
    return <p className="p-6 text-sm text-muted-foreground">{t.common.readOnly}</p>;
  }
  return (
    <div className="mx-auto grid max-w-5xl gap-8 p-4 lg:grid-cols-2">
      <UploadZone />
      <LookupPanel />
      <ImportPanel />
      <ImportFolderPanel />
    </div>
  );
}
