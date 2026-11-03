"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, describeError } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useMe, useWorkspace } from "@/lib/session";
import type { MyInvitation, Role, Workspace, WorkspaceDetail, WorkspaceInvitation } from "@/lib/types";
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

const ROLES: Role[] = ["viewer", "editor", "owner"];

export function WorkspacesPanel() {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const { workspaces } = useWorkspace();
  const [name, setName] = useState("");

  const refreshWorkspaces = () => queryClient.invalidateQueries({ queryKey: ["workspaces"] });

  const invitations = useQuery({
    queryKey: ["my-invitations"],
    queryFn: () => api.get<MyInvitation[]>("/invitations"),
  });

  const create = useMutation({
    mutationFn: () => api.post<Workspace>("/workspaces", { name: name.trim() }),
    onSuccess: () => {
      setName("");
      void refreshWorkspaces();
    },
    onError: (e) => toast.error(describeError(t, e)),
  });

  const respond = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "accept" | "decline" }) =>
      api.post<unknown>(`/invitations/${id}/${action}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["my-invitations"] });
      void refreshWorkspaces();
    },
    onError: (e) => {
      toast.error(describeError(t, e));
      void queryClient.invalidateQueries({ queryKey: ["my-invitations"] });
    },
  });

  const shared = workspaces.filter((w) => w.kind === "shared");

  return (
    <section className="space-y-4">
      <h2 className="text-base font-semibold">{t.settings.workspacesTitle}</h2>
      <p className="text-sm text-muted-foreground">{t.settings.workspacesHelp}</p>

      <form
        className="flex max-w-xl items-end gap-2 rounded-md border p-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) create.mutate();
        }}
      >
        <div className="min-w-0 flex-1 space-y-1.5">
          <Label htmlFor="ws-name">{t.settings.newWorkspace}</Label>
          <Input
            id="ws-name"
            required
            maxLength={200}
            placeholder={t.settings.workspaceName}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <Button type="submit" disabled={create.isPending || !name.trim()}>
          {t.settings.createWorkspace}
        </Button>
      </form>

      <div className="space-y-2">
        <h3 className="text-sm font-medium">{t.settings.myInvitations}</h3>
        {invitations.isError && <p className="text-sm text-destructive">{describeError(t, invitations.error)}</p>}
        {invitations.data?.length === 0 && <p className="text-sm text-muted-foreground">{t.settings.noInvitations}</p>}
        {(invitations.data ?? []).map((inv) => (
          <div key={inv.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-3 text-sm">
            <div>
              <p className="font-medium">
                {inv.workspace_name} <span className="text-muted-foreground">({t.roles[inv.role]})</span>
              </p>
              <p className="text-xs text-muted-foreground">
                {t.settings.invitedBy}: {inv.invited_by_name} / {t.settings.expiresOn}:{" "}
                {new Date(inv.expires_at).toLocaleDateString(locale)}
              </p>
            </div>
            <div className="flex gap-2">
              <Button size="sm" disabled={respond.isPending} onClick={() => respond.mutate({ id: inv.id, action: "accept" })}>
                {t.settings.accept}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={respond.isPending}
                onClick={() => respond.mutate({ id: inv.id, action: "decline" })}
              >
                {t.settings.decline}
              </Button>
            </div>
          </div>
        ))}
      </div>

      <div className="space-y-3">
        <h3 className="text-sm font-medium">{t.settings.mySharedWorkspaces}</h3>
        {shared.length === 0 && <p className="text-sm text-muted-foreground">{t.settings.noSharedWorkspaces}</p>}
        {shared.map((w) => (
          <WorkspaceCard key={w.id} workspace={w} />
        ))}
      </div>
    </section>
  );
}

function WorkspaceCard({ workspace }: { workspace: Workspace }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const me = useMe();
  const isOwner = workspace.role === "owner";
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [removing, setRemoving] = useState<{ user_id: string; label: string; self: boolean } | null>(null);

  const detailKey = ["workspace", workspace.id];
  const invKey = ["ws-invitations", workspace.id];
  const detail = useQuery({ queryKey: detailKey, queryFn: () => api.get<WorkspaceDetail>(`/workspaces/${workspace.id}`) });
  const pending = useQuery({
    queryKey: invKey,
    queryFn: () => api.get<WorkspaceInvitation[]>(`/workspaces/${workspace.id}/invitations`),
    enabled: isOwner,
  });

  const refreshMembers = () => queryClient.invalidateQueries({ queryKey: detailKey });
  const onError = (e: unknown) => {
    toast.error(describeError(t, e, { 409: t.settings.lastOwner }));
    void refreshMembers();
  };

  const invite = useMutation({
    mutationFn: () => api.post<unknown>(`/workspaces/${workspace.id}/members`, { email: email.trim(), role }),
    onSuccess: () => {
      setEmail("");
      toast.success(t.settings.inviteSent);
      void queryClient.invalidateQueries({ queryKey: invKey });
    },
    onError: (e) => toast.error(describeError(t, e, { 409: t.errors.e409 })),
  });

  const changeRole = useMutation({
    mutationFn: ({ userId, newRole }: { userId: string; newRole: Role }) =>
      api.patch(`/workspaces/${workspace.id}/members/${userId}`, { role: newRole }),
    onSuccess: () => {
      void refreshMembers();
      void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    },
    onError,
  });

  const remove = useMutation({
    mutationFn: (userId: string) => api.del(`/workspaces/${workspace.id}/members/${userId}`),
    onSuccess: () => {
      void refreshMembers();
      void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    },
    onError,
  });

  const revoke = useMutation({
    mutationFn: (id: string) => api.del(`/workspaces/${workspace.id}/invitations/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: invKey }),
    onError: (e) => toast.error(describeError(t, e)),
  });

  return (
    <div className="space-y-3 rounded-md border p-3">
      <div className="flex items-center justify-between gap-2">
        <h4 className="font-medium">{workspace.name}</h4>
        <span className="text-xs text-muted-foreground">{t.roles[workspace.role]}</span>
      </div>

      {detail.isError && <p className="text-sm text-destructive">{describeError(t, detail.error)}</p>}
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted-foreground">
          <tr>
            <th className="py-1 pr-2">{t.settings.members}</th>
            <th className="py-1 pr-2">{t.settings.memberRole}</th>
            <th className="py-1" />
          </tr>
        </thead>
        <tbody>
          {(detail.data?.members ?? []).map((m) => {
            const self = m.user_id === me.data?.user.id;
            return (
              <tr key={m.user_id} className="border-t">
                <td className="py-1.5 pr-2">
                  {m.display_name} <span className="text-xs text-muted-foreground">{m.email}</span>
                  {self && <span className="ml-1 text-xs text-muted-foreground">({t.settings.you})</span>}
                </td>
                <td className="py-1.5 pr-2">
                  {isOwner ? (
                    <Select
                      aria-label={`${t.settings.memberRole}: ${m.email}`}
                      value={m.role}
                      disabled={changeRole.isPending}
                      onChange={(e) => changeRole.mutate({ userId: m.user_id, newRole: e.target.value as Role })}
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {t.roles[r]}
                        </option>
                      ))}
                    </Select>
                  ) : (
                    t.roles[m.role]
                  )}
                </td>
                <td className="py-1.5 text-right">
                  {(isOwner || self) && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setRemoving({ user_id: m.user_id, label: m.email, self })}
                    >
                      {self ? t.settings.leave : t.settings.removeMember}
                    </Button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {isOwner && (
        <>
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (email.trim()) invite.mutate();
            }}
          >
            <div className="min-w-48 flex-1 space-y-1.5">
              <Label htmlFor={`inv-email-${workspace.id}`}>{t.settings.memberEmail}</Label>
              <Input
                id={`inv-email-${workspace.id}`}
                type="email"
                required
                maxLength={255}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`inv-role-${workspace.id}`}>{t.settings.memberRole}</Label>
              <Select id={`inv-role-${workspace.id}`} value={role} onChange={(e) => setRole(e.target.value as Role)}>
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {t.roles[r]}
                  </option>
                ))}
              </Select>
            </div>
            <Button type="submit" disabled={invite.isPending || !email.trim()}>
              {t.settings.invite}
            </Button>
          </form>

          {(pending.data?.length ?? 0) > 0 && (
            <div className="space-y-1">
              <h5 className="text-xs font-medium text-muted-foreground">{t.settings.pendingInvitations}</h5>
              {(pending.data ?? []).map((inv) => (
                <div key={inv.id} className="flex items-center justify-between gap-2 border-t py-1.5 text-sm">
                  <span>
                    {inv.email} <span className="text-xs text-muted-foreground">({t.roles[inv.role]})</span>
                  </span>
                  <Button size="sm" variant="outline" onClick={() => revoke.mutate(inv.id)}>
                    {t.settings.cancelInvitation}
                  </Button>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      <AlertDialog open={removing !== null} onOpenChange={(o) => !o && setRemoving(null)}>
        <AlertDialogContent>
          <AlertDialogTitle>{removing?.self ? t.settings.leaveTitle : t.settings.removeMemberTitle}</AlertDialogTitle>
          <AlertDialogDescription>
            {removing?.label}
            <br />
            {removing?.self ? t.settings.leaveBody : t.settings.removeMemberBody}
          </AlertDialogDescription>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction onClick={() => removing && remove.mutate(removing.user_id)}>
              {removing?.self ? t.settings.leave : t.settings.removeMember}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
