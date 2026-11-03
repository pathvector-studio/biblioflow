"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FolderPlus, Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, describeError } from "@/lib/api";
import { flattenCollections } from "@/lib/collections";
import { useI18n, fmt } from "@/lib/i18n";
import { useCollections } from "@/lib/queries";
import { useWorkspace } from "@/lib/session";
import type { Collection } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

type NameDialog = { mode: "create"; parentId: string | null } | { mode: "rename"; collection: Collection };

export function CollectionsSidebar({
  selectedId,
  onSelect,
}: {
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const { t } = useI18n();
  const { current, canEdit } = useWorkspace();
  const queryClient = useQueryClient();
  const collections = useCollections(current.id);
  const flat = useMemo(() => flattenCollections(collections.data ?? []), [collections.data]);
  const [nameDialog, setNameDialog] = useState<NameDialog | null>(null);
  const [name, setName] = useState("");
  const [deleting, setDeleting] = useState<Collection | null>(null);

  // 選択中のコレクションが（削除などで）なくなったら絞り込みを解除する
  useEffect(() => {
    if (selectedId && collections.data && !collections.data.some((c) => c.id === selectedId)) onSelect(null);
  }, [selectedId, collections.data, onSelect]);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["collections", current.id] });

  const save = useMutation({
    mutationFn: (d: NameDialog) =>
      d.mode === "create"
        ? api.post("/collections", { workspace_id: current.id, name: name.trim(), parent_id: d.parentId })
        : api.patch(`/collections/${d.collection.id}`, { name: name.trim() }),
    onSuccess: () => {
      setNameDialog(null);
      void invalidate();
    },
    onError: (e) => toast.error(describeError(t, e)),
  });

  const remove = useMutation({
    mutationFn: (c: Collection) => api.del(`/collections/${c.id}`),
    onSuccess: () => {
      setDeleting(null);
      void invalidate();
      void queryClient.invalidateQueries({ queryKey: ["papers"] });
    },
    onError: (e) => toast.error(describeError(t, e)),
  });

  function openDialog(d: NameDialog) {
    setName(d.mode === "rename" ? d.collection.name : "");
    setNameDialog(d);
  }

  return (
    <aside className="w-full shrink-0 space-y-1 md:w-56">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t.collections.title}
        </h2>
        {canEdit && (
          <Button
            variant="ghost"
            size="icon"
            aria-label={t.collections.create}
            title={t.collections.create}
            onClick={() => openDialog({ mode: "create", parentId: null })}
          >
            <FolderPlus className="h-4 w-4" />
          </Button>
        )}
      </div>
      <button
        type="button"
        onClick={() => onSelect(null)}
        className={cn(
          "w-full rounded-md px-2 py-1 text-left text-sm hover:bg-accent",
          selectedId === null && "bg-accent font-medium",
        )}
      >
        {t.collections.all}
      </button>
      {collections.isError && <p className="text-xs text-destructive">{describeError(t, collections.error)}</p>}
      {flat.map(({ collection: c, depth }) => (
        <div
          key={c.id}
          className={cn("group flex items-center rounded-md hover:bg-accent", selectedId === c.id && "bg-accent")}
          style={{ paddingLeft: depth * 12 }}
        >
          <button
            type="button"
            onClick={() => onSelect(c.id)}
            className={cn("min-w-0 flex-1 truncate px-2 py-1 text-left text-sm", selectedId === c.id && "font-medium")}
            title={c.name}
          >
            {c.name} <span className="text-xs text-muted-foreground">{c.paper_count}</span>
          </button>
          {canEdit && (
            <span className="flex opacity-0 focus-within:opacity-100 group-hover:opacity-100">
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                aria-label={t.collections.createChild}
                title={t.collections.createChild}
                onClick={() => openDialog({ mode: "create", parentId: c.id })}
              >
                <FolderPlus className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                aria-label={t.collections.rename}
                title={t.collections.rename}
                onClick={() => openDialog({ mode: "rename", collection: c })}
              >
                <Pencil className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                aria-label={t.common.delete}
                title={t.common.delete}
                onClick={() => setDeleting(c)}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </span>
          )}
        </div>
      ))}

      <Dialog open={nameDialog !== null} onOpenChange={(o) => !o && setNameDialog(null)}>
        <DialogContent closeLabel={t.common.close}>
          <DialogHeader>
            <DialogTitle>{nameDialog?.mode === "rename" ? t.collections.rename : t.collections.create}</DialogTitle>
          </DialogHeader>
          <form
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (nameDialog && name.trim()) save.mutate(nameDialog);
            }}
          >
            <Input
              autoFocus
              aria-label={t.collections.name}
              placeholder={t.collections.name}
              maxLength={200}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setNameDialog(null)}>
                {t.common.cancel}
              </Button>
              <Button type="submit" disabled={!name.trim() || save.isPending}>
                {t.common.save}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogTitle>{fmt(t.collections.deleteTitle, { name: deleting?.name ?? "" })}</AlertDialogTitle>
          <AlertDialogDescription>{t.collections.deleteBody}</AlertDialogDescription>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction onClick={() => deleting && remove.mutate(deleting)}>
              {t.common.delete}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </aside>
  );
}
