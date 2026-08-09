"use client";

import { use as usePromise, useState } from "react";
import { MoreHorizontal, Pencil, UserPlus, UserRoundPlus, Users } from "lucide-react";
import { toast } from "sonner";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader } from "@/components/shared/page-header";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { StatusBadge } from "@/components/shared/status-badge";
import { IdentityChip } from "@/components/shared/identity-chip";
import { FieldError } from "@/components/shared/field-error";
import { UserPicker } from "@/components/shared/user-picker";
import {
  useAddMember,
  useInviteMember,
  useMembershipLifecycle,
  useMembershipRoles,
  useTenantMembers,
  useUpdateMembership,
} from "@/features/tenancy/hooks";
import { useMembershipRoleAssignment, useTenantRoles } from "@/features/rbac/hooks";
import { isApiError } from "@/lib/api-client";
import type { TenantMember } from "@/lib/types";

const inviteSchema = z.object({ email: z.string().email("Enter a valid email address.") });
type InviteForm = z.infer<typeof inviteSchema>;

export default function MembersPage({ params }: { params: Promise<{ tenantId: string }> }) {
  const { tenantId } = usePromise(params);
  const { data: members, isLoading, error } = useTenantMembers(tenantId);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);

  return (
    <div>
      <PageHeader
        title="Members"
        description="Everyone with a membership in this tenant, and the roles they hold."
        actions={
          <div className="flex items-center gap-2">
            <Dialog open={addOpen} onOpenChange={setAddOpen}>
              <DialogTrigger render={<Button size="sm" variant="outline" />}>
                <UserRoundPlus />
                Add member
              </DialogTrigger>
              <AddMemberDialog tenantId={tenantId} onDone={() => setAddOpen(false)} />
            </Dialog>
            <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
              <DialogTrigger render={<Button size="sm" />}>
                <UserPlus />
                Invite member
              </DialogTrigger>
              <InviteDialog tenantId={tenantId} onDone={() => setInviteOpen(false)} />
            </Dialog>
          </div>
        }
      />

      {isLoading && <TableSkeleton rows={5} columns={4} />}
      {error && <ErrorState error={error} resource="members" />}

      {members && members.length === 0 && (
        <EmptyState
          icon={Users}
          title="No members yet"
          description="Invite someone to give them access to this tenant."
          action={
            <Button size="sm" onClick={() => setInviteOpen(true)}>
              <UserPlus />
              Invite member
            </Button>
          }
        />
      )}

      {members && members.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>User</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Roles</TableHead>
                  <TableHead>Joined</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {members.map((member) => (
                  <MemberRow key={member.membership_id} tenantId={tenantId} member={member} />
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function MemberRow({ tenantId, member }: { tenantId: string; member: TenantMember }) {
  const lifecycle = useMembershipLifecycle(tenantId);
  const { data: roleAssignments } = useMembershipRoles(tenantId, member.membership_id);
  const { data: roles } = useTenantRoles(tenantId);
  const [rolesOpen, setRolesOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);

  const roleNameById = new Map((roles ?? []).map((r) => [r.id, r]));

  async function run(action: "suspend" | "reactivate" | "revoke" | "restore") {
    try {
      if (action === "reactivate" || action === "restore") {
        await lifecycle[action].mutateAsync(member.membership_id);
      } else {
        await lifecycle[action].mutateAsync({
          membershipId: member.membership_id,
          reason: `Changed from the members console`,
        });
      }
      toast.success(
        action === "restore" ? "Membership restored" : `Membership ${action}d`,
      );
    } catch (err) {
      toast.error(isApiError(err) ? err.message : `Couldn't ${action} the membership.`);
    }
  }

  return (
    <TableRow>
      <TableCell>
        <IdentityChip value={member.user_id} label="user" />
        <p className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
          {member.job_title ?? <span className="italic">No title</span>}
          <Dialog open={editOpen} onOpenChange={setEditOpen}>
            <DialogTrigger render={<Button size="icon-xs" variant="ghost" className="size-4" />}>
              <Pencil className="size-2.5" />
            </DialogTrigger>
            <EditJobTitleDialog
              tenantId={tenantId}
              member={member}
              onDone={() => setEditOpen(false)}
            />
          </Dialog>
        </p>
      </TableCell>
      <TableCell>
        <StatusBadge status={member.status} />
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap items-center gap-1">
          {roleAssignments?.length ? (
            roleAssignments.map((assignment) => {
              const role = roleNameById.get(assignment.role_id);
              return (
                <Badge key={assignment.role_id} variant="secondary" className="font-mono text-xs">
                  {role?.code ?? assignment.role_id.slice(0, 8)}
                </Badge>
              );
            })
          ) : (
            <span className="text-xs text-muted-foreground">No roles</span>
          )}
          <Dialog open={rolesOpen} onOpenChange={setRolesOpen}>
            <DialogTrigger render={<Button size="xs" variant="ghost" className="h-5 px-1.5" />}>
              Edit
            </DialogTrigger>
            <ManageRolesDialog
              tenantId={tenantId}
              membershipId={member.membership_id}
              assignedRoleIds={new Set((roleAssignments ?? []).map((a) => a.role_id))}
            />
          </Dialog>
        </div>
      </TableCell>
      <TableCell className="text-sm text-muted-foreground tabular-nums">
        {new Date(member.created_at).toLocaleDateString()}
      </TableCell>
      <TableCell>
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button size="icon-xs" variant="ghost" />}>
            <MoreHorizontal />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {/* The label and the items it names must sit inside one
                DropdownMenuGroup -- Base UI throws "MenuGroupContext is
                missing" otherwise, and it also associates the label with the
                items for screen readers. */}
            <DropdownMenuGroup>
              <DropdownMenuLabel>Membership</DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                disabled={member.status !== "active"}
                onClick={() => run("suspend")}
              >
                Suspend
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={member.status !== "suspended"}
                onClick={() => run("reactivate")}
              >
                Reactivate
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={member.status !== "revoked"}
                onClick={() => run("restore")}
              >
                Restore access
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant="destructive"
                disabled={member.status === "revoked"}
                onClick={() => run("revoke")}
              >
                Revoke access
              </DropdownMenuItem>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </TableCell>
    </TableRow>
  );
}

function ManageRolesDialog({
  tenantId,
  membershipId,
  assignedRoleIds,
}: {
  tenantId: string;
  membershipId: string;
  assignedRoleIds: Set<string>;
}) {
  const { data: roles } = useTenantRoles(tenantId);
  const { assign, revoke } = useMembershipRoleAssignment(tenantId);

  async function toggle(roleCode: string, currentlyAssigned: boolean) {
    try {
      if (currentlyAssigned) {
        await revoke.mutateAsync({ membershipId, roleCode });
        toast.success(`Removed ${roleCode}`);
      } else {
        await assign.mutateAsync({ membershipId, roleCode });
        toast.success(`Assigned ${roleCode}`);
      }
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't change the role.");
    }
  }

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Roles for this member</DialogTitle>
        <DialogDescription>
          You can only assign roles whose permissions you hold yourself, and never one that
          outranks you.
        </DialogDescription>
      </DialogHeader>
      <div className="max-h-80 space-y-1 overflow-y-auto py-2">
        {roles?.map((role) => {
          const isAssigned = assignedRoleIds.has(role.id);
          return (
            <label
              key={role.id}
              className="flex cursor-pointer items-start gap-2.5 rounded-md px-2 py-1.5 hover:bg-accent/60"
            >
              <Checkbox
                checked={isAssigned}
                onCheckedChange={() => toggle(role.code, isAssigned)}
                className="mt-0.5"
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <code className="font-mono text-xs">{role.code}</code>
                  <span className="text-xs text-muted-foreground tabular-nums">rank {role.rank}</span>
                  {role.is_system && (
                    <Badge variant="outline" className="text-xs">
                      System
                    </Badge>
                  )}
                </div>
                {role.description && (
                  <p className="mt-0.5 text-xs text-muted-foreground">{role.description}</p>
                )}
              </div>
            </label>
          );
        })}
      </div>
    </DialogContent>
  );
}

function InviteDialog({ tenantId, onDone }: { tenantId: string; onDone: () => void }) {
  const invite = useInviteMember(tenantId);
  const { data: roles } = useTenantRoles(tenantId);
  const [selectedRoles, setSelectedRoles] = useState<Set<string>>(new Set());
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<InviteForm>({ resolver: zodResolver(inviteSchema) });

  function toggleRole(code: string) {
    setSelectedRoles((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }

  async function onSubmit(values: InviteForm) {
    try {
      await invite.mutateAsync({ email: values.email, roleCodes: [...selectedRoles] });
      toast.success(`Invitation sent to ${values.email}`);
      reset();
      setSelectedRoles(new Set());
      onDone();
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't send the invitation.");
    }
  }

  return (
    <DialogContent>
      <form onSubmit={handleSubmit(onSubmit)}>
        <DialogHeader>
          <DialogTitle>Invite a member</DialogTitle>
          <DialogDescription>
            They&apos;ll get an email with a link to join this tenant.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div>
            <Label htmlFor="invite-email">Email</Label>
            <Input id="invite-email" type="email" className="mt-1.5" {...register("email")} />
            <FieldError message={errors.email?.message} />
          </div>
          <div>
            <Label>Roles to assign on acceptance</Label>
            <div className="mt-1.5 max-h-48 space-y-1 overflow-y-auto rounded-md border border-border p-2">
              {roles?.length ? (
                roles.map((role) => (
                  <label
                    key={role.id}
                    className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-sm hover:bg-accent/60"
                  >
                    <Checkbox
                      checked={selectedRoles.has(role.code)}
                      onCheckedChange={() => toggleRole(role.code)}
                    />
                    <code className="font-mono text-xs">{role.code}</code>
                    <span className="text-xs text-muted-foreground">{role.name}</span>
                  </label>
                ))
              ) : (
                <p className="px-1.5 py-1 text-xs text-muted-foreground">No roles available.</p>
              )}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              Optional. You can only pre-assign roles whose permissions you hold.
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button type="submit" size="sm" disabled={invite.isPending}>
            {invite.isPending ? "Sending…" : "Send invitation"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}

/**
 * Skips the email-invitation flow entirely and creates an active membership
 * directly. Useful because this deployment has no email provider wired up
 * (see docs/23) -- "invite" mails a link nobody receives, so this is the only
 * way to actually get a known user into a tenant today.
 */
function AddMemberDialog({ tenantId, onDone }: { tenantId: string; onDone: () => void }) {
  const addMember = useAddMember(tenantId);
  const { data: roles } = useTenantRoles(tenantId);
  const [userId, setUserId] = useState("");
  const [jobTitle, setJobTitle] = useState("");
  const [selectedRoles, setSelectedRoles] = useState<Set<string>>(new Set());

  function toggleRole(code: string) {
    setSelectedRoles((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await addMember.mutateAsync({
        userId: userId.trim(),
        roleCodes: [...selectedRoles],
        jobTitle: jobTitle.trim() || null,
      });
      toast.success("Member added");
      setUserId("");
      setJobTitle("");
      setSelectedRoles(new Set());
      onDone();
    } catch (err) {
      toast.error(
        isApiError(err) && err.status === 409
          ? "That user already has a membership in this tenant."
          : isApiError(err)
            ? err.message
            : "Couldn't add the member.",
      );
    }
  }

  return (
    <DialogContent>
      <form onSubmit={onSubmit}>
        <DialogHeader>
          <DialogTitle>Add a member directly</DialogTitle>
          <DialogDescription>
            Creates an active membership immediately — no invitation email is sent.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div>
            <Label htmlFor="add-member-user">User</Label>
            <div className="mt-1.5">
              <UserPicker id="add-member-user" value={userId} onChange={setUserId} />
            </div>
          </div>
          <div>
            <Label htmlFor="add-member-title">Job title</Label>
            <Input
              id="add-member-title"
              value={jobTitle}
              onChange={(e) => setJobTitle(e.target.value)}
              placeholder="Optional"
              className="mt-1.5"
            />
          </div>
          <div>
            <Label>Roles to assign</Label>
            <div className="mt-1.5 max-h-48 space-y-1 overflow-y-auto rounded-md border border-border p-2">
              {roles?.length ? (
                roles.map((role) => (
                  <label
                    key={role.id}
                    className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-sm hover:bg-accent/60"
                  >
                    <Checkbox
                      checked={selectedRoles.has(role.code)}
                      onCheckedChange={() => toggleRole(role.code)}
                    />
                    <code className="font-mono text-xs">{role.code}</code>
                    <span className="text-xs text-muted-foreground">{role.name}</span>
                  </label>
                ))
              ) : (
                <p className="px-1.5 py-1 text-xs text-muted-foreground">No roles available.</p>
              )}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              Optional. You can only assign roles whose permissions you hold.
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button type="submit" size="sm" disabled={!userId.trim() || addMember.isPending}>
            {addMember.isPending ? "Adding…" : "Add member"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}

function EditJobTitleDialog({
  tenantId,
  member,
  onDone,
}: {
  tenantId: string;
  member: TenantMember;
  onDone: () => void;
}) {
  const update = useUpdateMembership(tenantId);
  const [jobTitle, setJobTitle] = useState(member.job_title ?? "");

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await update.mutateAsync({ membershipId: member.membership_id, jobTitle: jobTitle.trim() || null });
      toast.success("Job title updated");
      onDone();
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't update the job title.");
    }
  }

  return (
    <DialogContent>
      <form onSubmit={onSubmit}>
        <DialogHeader>
          <DialogTitle>Edit job title</DialogTitle>
        </DialogHeader>
        <div className="py-4">
          <Label htmlFor={`job-title-${member.membership_id}`}>Job title</Label>
          <Input
            id={`job-title-${member.membership_id}`}
            autoFocus
            value={jobTitle}
            onChange={(e) => setJobTitle(e.target.value)}
            placeholder="Optional"
            className="mt-1.5"
          />
        </div>
        <DialogFooter>
          <Button type="submit" size="sm" disabled={update.isPending}>
            {update.isPending ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
