"use client";

import { useState } from "react";
import { toast } from "sonner";
import {
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  ShieldCheck,
  Trash2,
  UserCheck,
  UserRound,
  UserX,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
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
import { PermissionList } from "@/components/shared/permission-list";
import { FieldError } from "@/components/shared/field-error";
import {
  useCreateUser,
  useDeleteUser,
  usePlatformUser,
  usePlatformUsers,
  useSetUserStatus,
  useUpdateUser,
} from "@/features/platform/hooks";
import {
  useGrantPlatformRole,
  usePlatformPermissionCatalog,
  usePlatformRoles,
  useRevokePlatformRole,
} from "@/features/rbac/hooks";
import { isApiError } from "@/lib/api-client";
import type { PlatformUser } from "@/lib/types";

const PAGE_SIZE = 25;

export default function PlatformUsersPage() {
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const users = usePlatformUsers({ search: search || undefined, limit: PAGE_SIZE, offset });

  const total = users.data?.total ?? 0;
  const showingFrom = total === 0 ? 0 : offset + 1;
  const showingTo = Math.min(offset + PAGE_SIZE, total);

  return (
    <div>
      <PageHeader
        eyebrow="Platform"
        title="Users"
        description="Every account on the platform, across all tenants. Suspending here stops the person signing in anywhere — it is not the same as removing them from one tenant."
        actions={
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus />
            New user
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative max-w-sm flex-1">
          <Label htmlFor="user-search" className="sr-only">
            Search by email
          </Label>
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            id="user-search"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setOffset(0);
            }}
            placeholder="Search by email…"
            className="pl-8"
          />
        </div>
        {total > 0 && (
          <p className="text-sm text-muted-foreground tabular-nums">
            {showingFrom}–{showingTo} of {total}
          </p>
        )}
      </div>

      {users.isLoading && <TableSkeleton rows={6} columns={5} />}
      {users.error && <ErrorState error={users.error} resource="the user directory" />}

      {users.data &&
        (users.data.users.length === 0 ? (
          <EmptyState
            icon={UserRound}
            title={search ? "No users match that search" : "No users yet"}
            description={
              search
                ? "Try a different fragment of the email address."
                : "Create the first account, or let people register themselves."
            }
            action={
              !search ? (
                <Button size="sm" onClick={() => setCreateOpen(true)}>
                  <Plus />
                  New user
                </Button>
              ) : undefined
            }
          />
        ) : (
          <>
            <Card className="overflow-hidden">
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Email</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Verified</TableHead>
                      <TableHead>Last sign-in</TableHead>
                      <TableHead>User ID</TableHead>
                      <TableHead className="w-10" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {users.data.users.map((user) => (
                      <UserRow
                        key={user.id}
                        user={user}
                        onOpen={() => setSelectedUserId(user.id)}
                      />
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>

            <div className="mt-4 flex items-center justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                Previous
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </>
        ))}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <CreateUserDialog onDone={() => setCreateOpen(false)} />
      </Dialog>

      <UserDetailSheet userId={selectedUserId} onClose={() => setSelectedUserId(null)} />
    </div>
  );
}

function UserRow({ user, onOpen }: { user: PlatformUser; onOpen: () => void }) {
  const setStatus = useSetUserStatus();
  const deleteUser = useDeleteUser();
  const [editOpen, setEditOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const isSuspended = user.status === "suspended";

  async function toggleStatus() {
    try {
      await setStatus.mutateAsync({
        userId: user.id,
        suspend: !isSuspended,
        reason: isSuspended ? null : "Suspended from the admin console",
      });
      toast.success(isSuspended ? "Account reactivated" : "Account suspended");
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't change the account status.");
    }
  }

  async function remove() {
    try {
      await deleteUser.mutateAsync(user.id);
      toast.success(`Deleted ${user.email}`);
      setConfirmDelete(false);
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't delete the account.");
    }
  }

  return (
    <>
      <TableRow className="cursor-pointer" onClick={onOpen}>
        <TableCell className="font-medium">{user.email}</TableCell>
        <TableCell>
          <StatusBadge status={user.status} />
        </TableCell>
        <TableCell>
          {user.email_verified ? (
            <Badge variant="outline">yes</Badge>
          ) : (
            <span className="text-xs text-muted-foreground">no</span>
          )}
        </TableCell>
        <TableCell className="text-sm tabular-nums text-muted-foreground">
          {user.last_login_at ? new Date(user.last_login_at).toLocaleDateString() : "never"}
        </TableCell>
        <TableCell onClick={(e) => e.stopPropagation()}>
          <IdentityChip value={user.id} label="user" />
        </TableCell>
        <TableCell onClick={(e) => e.stopPropagation()}>
          <DropdownMenu>
            <DropdownMenuTrigger
              render={<Button size="icon" variant="ghost" className="size-7" />}
            >
              <MoreHorizontal className="size-4" />
              <span className="sr-only">Actions for {user.email}</span>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              <DropdownMenuItem onClick={onOpen}>
                <ShieldCheck className="size-3.5" />
                View &amp; manage roles
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setEditOpen(true)}>
                <Pencil className="size-3.5" />
                Change email
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={toggleStatus} disabled={setStatus.isPending}>
                {isSuspended ? (
                  <>
                    <UserCheck className="size-3.5" />
                    Reactivate
                  </>
                ) : (
                  <>
                    <UserX className="size-3.5" />
                    Suspend
                  </>
                )}
              </DropdownMenuItem>
              <DropdownMenuItem variant="destructive" onClick={() => setConfirmDelete(true)}>
                <Trash2 className="size-3.5" />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </TableCell>
      </TableRow>

      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <EditEmailDialog user={user} onDone={() => setEditOpen(false)} />
      </Dialog>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {user.email}?</DialogTitle>
            <DialogDescription>
              The account is deactivated and signed out everywhere, and disappears from this
              directory. The row itself is kept — audit history references it, and an IAM system
              that can erase who did what defeats its own record-keeping.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={remove}
              disabled={deleteUser.isPending}
            >
              {deleteUser.isPending ? "Deleting…" : "Delete account"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function CreateUserDialog({ onDone }: { onDone: () => void }) {
  const createUser = useCreateUser();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [violations, setViolations] = useState<string[]>([]);

  const canSubmit = email.trim().length > 0 && password.length > 0;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setViolations([]);
    try {
      await createUser.mutateAsync({ email: email.trim(), password });
      toast.success(`Created ${email.trim()}`);
      setEmail("");
      setPassword("");
      onDone();
    } catch (err) {
      if (isApiError(err) && err.violations?.length) {
        setViolations(err.violations);
        return;
      }
      toast.error(
        isApiError(err) && err.status === 409
          ? "An account with that email already exists."
          : isApiError(err)
            ? err.message
            : "Couldn't create the account.",
      );
    }
  }

  return (
    <DialogContent className="sm:max-w-lg">
      <form onSubmit={submit}>
        <DialogHeader>
          <DialogTitle>New user</DialogTitle>
          <DialogDescription>
            The account is created active — an administrator creating it is the vouching step
            email verification would otherwise provide. Share the initial password out of band;
            it is hashed immediately and never shown again.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div>
            <Label htmlFor="new-user-email">Email</Label>
            <Input
              id="new-user-email"
              type="email"
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="person@example.com"
              className="mt-1.5"
            />
          </div>
          <div>
            <Label htmlFor="new-user-password">Initial password</Label>
            <Input
              id="new-user-password"
              type="text"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 12 characters"
              className="mt-1.5 font-mono text-sm"
            />
            {violations.length > 0 ? (
              <ul className="mt-1.5 space-y-0.5">
                {violations.map((v) => (
                  <li key={v}>
                    <FieldError message={v} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-xs text-muted-foreground">
                Shown as plain text on purpose — you need to be able to copy it.
              </p>
            )}
          </div>
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" size="sm" onClick={onDone}>
            Cancel
          </Button>
          <Button type="submit" size="sm" disabled={!canSubmit || createUser.isPending}>
            {createUser.isPending ? "Creating…" : "Create user"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}

function EditEmailDialog({ user, onDone }: { user: PlatformUser; onDone: () => void }) {
  const updateUser = useUpdateUser();
  const [email, setEmail] = useState(user.email);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await updateUser.mutateAsync({ userId: user.id, email: email.trim() });
      toast.success("Email updated");
      onDone();
    } catch (err) {
      toast.error(
        isApiError(err) && err.status === 409
          ? "Another account already uses that email."
          : isApiError(err)
            ? err.message
            : "Couldn't update the email.",
      );
    }
  }

  return (
    <DialogContent>
      <form onSubmit={submit}>
        <DialogHeader>
          <DialogTitle>Change email</DialogTitle>
          <DialogDescription>
            This is the address they sign in with. Changing it signs them out everywhere and
            resets email verification.
          </DialogDescription>
        </DialogHeader>
        <div className="py-4">
          <Label htmlFor={`email-${user.id}`}>Email</Label>
          <Input
            id={`email-${user.id}`}
            type="email"
            autoFocus
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1.5"
          />
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" size="sm" onClick={onDone}>
            Cancel
          </Button>
          <Button
            type="submit"
            size="sm"
            disabled={!email.trim() || email.trim() === user.email || updateUser.isPending}
          >
            {updateUser.isPending ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}

function UserDetailSheet({ userId, onClose }: { userId: string | null; onClose: () => void }) {
  const detail = usePlatformUser(userId);
  const catalog = usePlatformPermissionCatalog();
  const roles = usePlatformRoles();
  const grant = useGrantPlatformRole();
  const revoke = useRevokePlatformRole();
  const [roleToGrant, setRoleToGrant] = useState("");

  const held = new Set(detail.data?.platform_roles ?? []);
  const grantable = roles.data?.filter((r) => !held.has(r.code)) ?? [];

  async function runRole(action: "grant" | "revoke", roleCode: string) {
    if (!userId) return;
    const mutation = action === "grant" ? grant : revoke;
    try {
      await mutation.mutateAsync({ targetUserId: userId, roleCode });
      toast.success(action === "grant" ? "Role granted" : "Role revoked");
      setRoleToGrant("");
      await detail.refetch();
    } catch (err) {
      // The self-escalation guard answers 403 with a "; "-joined list naming
      // exactly which permissions the actor is missing -- pass it through.
      toast.error(isApiError(err) ? err.message : `Couldn't ${action} the role.`);
    }
  }

  const user = detail.data?.user;

  return (
    <Sheet open={userId != null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{user?.email ?? "User"}</SheetTitle>
          <SheetDescription>
            Platform roles, resolved permissions, and tenant memberships.
          </SheetDescription>
        </SheetHeader>

        <div className="space-y-6 px-4 pb-8">
          {detail.isLoading && <TableSkeleton rows={4} columns={2} />}
          {detail.error && <ErrorState error={detail.error} resource="this user" />}

          {detail.data && (
            <>
              <section>
                <SectionLabel>Account</SectionLabel>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
                  <dt className="text-muted-foreground">Status</dt>
                  <dd>
                    <StatusBadge status={detail.data.user.status} />
                  </dd>
                  <dt className="text-muted-foreground">User ID</dt>
                  <dd>
                    <IdentityChip value={detail.data.user.id} label="user" />
                  </dd>
                  <dt className="text-muted-foreground">Created</dt>
                  <dd className="tabular-nums">
                    {new Date(detail.data.user.created_at).toLocaleDateString()}
                  </dd>
                  <dt className="text-muted-foreground">Last sign-in</dt>
                  <dd className="tabular-nums">
                    {detail.data.user.last_login_at
                      ? new Date(detail.data.user.last_login_at).toLocaleString()
                      : "never"}
                  </dd>
                </dl>
              </section>

              <section>
                <SectionLabel>Platform roles</SectionLabel>
                {detail.data.platform_roles.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    None — this is a tenant-only user.
                  </p>
                ) : (
                  <ul className="space-y-1.5">
                    {detail.data.platform_roles.map((code) => (
                      <li
                        key={code}
                        className="flex items-center justify-between rounded-md border border-border px-2.5 py-1.5"
                      >
                        <code className="font-mono text-xs">{code}</code>
                        <Button
                          size="xs"
                          variant="ghost"
                          disabled={revoke.isPending}
                          onClick={() => runRole("revoke", code)}
                        >
                          Revoke
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}

                <div className="mt-3 flex items-end gap-2">
                  <div className="min-w-0 flex-1">
                    <Label htmlFor="grant-role" className="text-xs">
                      Grant a role
                    </Label>
                    <Select value={roleToGrant} onValueChange={(v) => setRoleToGrant(v ?? "")}>
                      <SelectTrigger id="grant-role" className="mt-1 w-full">
                        <SelectValue placeholder="Select a role" />
                      </SelectTrigger>
                      <SelectContent>
                        {grantable.map((role) => (
                          <SelectItem key={role.id} value={role.code}>
                            {role.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <Button
                    size="sm"
                    disabled={!roleToGrant || grant.isPending}
                    onClick={() => runRole("grant", roleToGrant)}
                  >
                    {grant.isPending ? "Granting…" : "Grant"}
                  </Button>
                </div>
                <p className="mt-1.5 text-xs text-muted-foreground">
                  You can only grant a role whose permissions you already hold, and never one
                  that outranks you.
                </p>
              </section>

              {detail.data.platform_permissions.length > 0 && catalog.data && (
                <section>
                  <SectionLabel>Effective platform permissions</SectionLabel>
                  <PermissionList
                    permissions={catalog.data.filter((p) =>
                      detail.data.platform_permissions.includes(p.code),
                    )}
                  />
                </section>
              )}

              <section>
                <SectionLabel>Tenant memberships</SectionLabel>
                {detail.data.memberships.length === 0 ? (
                  <p className="text-sm text-muted-foreground">Not a member of any tenant.</p>
                ) : (
                  <ul className="divide-y divide-border">
                    {detail.data.memberships.map((m) => (
                      <li
                        key={m.membership_id}
                        className="flex items-center justify-between py-2.5"
                      >
                        <div>
                          <p className="text-sm font-medium">{m.tenant_display_name}</p>
                          <p className="font-mono text-xs text-muted-foreground">
                            {m.tenant_slug}
                            {m.job_title ? ` · ${m.job_title}` : ""}
                          </p>
                        </div>
                        <StatusBadge status={m.status} />
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
      {children}
    </h3>
  );
}
