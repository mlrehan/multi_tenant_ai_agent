"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader } from "@/components/shared/page-header";
import { ErrorState, TableSkeleton } from "@/components/shared/states";
import { PermissionList } from "@/components/shared/permission-list";
import { FieldError } from "@/components/shared/field-error";
import { UserPicker } from "@/components/shared/user-picker";
import {
  useCreatePlatformRole,
  useEditPlatformRolePermissions,
  useGrantPlatformRole,
  usePlatformEffectivePermissions,
  usePlatformPermissionCatalog,
  usePlatformRoles,
  useRevokePlatformRole,
} from "@/features/rbac/hooks";
import { usePlatformRolePermissions } from "@/features/platform/hooks";
import { isApiError } from "@/lib/api-client";
import type { RoleSummary } from "@/lib/types";

export default function PlatformRolesPage() {
  const roles = usePlatformRoles();
  const catalog = usePlatformPermissionCatalog();
  const mine = usePlatformEffectivePermissions();
  const [createOpen, setCreateOpen] = useState(false);

  return (
    <div>
      <PageHeader
        eyebrow="Platform"
        title="Roles"
        description="Platform-scope roles and permissions. These are entirely separate from tenant roles — a tenant role can never hold a platform permission."
        actions={
          <Dialog open={createOpen} onOpenChange={setCreateOpen}>
            <DialogTrigger render={<Button size="sm" />}>
              <Plus />
              New role
            </DialogTrigger>
            <CreateRoleDialog onDone={() => setCreateOpen(false)} />
          </Dialog>
        }
      />

      <Tabs defaultValue="grant">
        <TabsList>
          <TabsTrigger value="grant">Grant &amp; revoke</TabsTrigger>
          <TabsTrigger value="roles">Roles</TabsTrigger>
          <TabsTrigger value="mine">My permissions</TabsTrigger>
          <TabsTrigger value="catalog">Permission catalog</TabsTrigger>
        </TabsList>

        <TabsContent value="grant" className="mt-4">
          <GrantRevokeCard />
        </TabsContent>

        <TabsContent value="roles" className="mt-4">
          {roles.isLoading && <TableSkeleton rows={4} columns={4} />}
          {roles.error && <ErrorState error={roles.error} resource="platform roles" scope="platform" />}
          {roles.data && (
            <Card>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Code</TableHead>
                      <TableHead>Name</TableHead>
                      <TableHead className="text-right">Rank</TableHead>
                      <TableHead>Kind</TableHead>
                      <TableHead>Description</TableHead>
                      <TableHead className="w-20" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {roles.data.map((role) => (
                      <RoleRow key={role.id} role={role} />
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="mine" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>Your effective platform permissions</CardTitle>
              <CardDescription>
                The fully resolved set, after role inheritance and any overrides.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {mine.isLoading && <TableSkeleton rows={4} columns={1} />}
              {mine.error && <ErrorState error={mine.error} resource="your permissions" scope="platform" />}
              {mine.data && catalog.data && (
                <PermissionList
                  permissions={catalog.data.filter((p) => mine.data.permissions.includes(p.code))}
                  emptyMessage="You hold no platform permissions. This console will only show tenant-scope features."
                />
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="catalog" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>Platform permission catalog</CardTitle>
              <CardDescription>Every platform-scope permission defined on this deployment.</CardDescription>
            </CardHeader>
            <CardContent>
              {catalog.isLoading && <TableSkeleton rows={5} columns={1} />}
              {catalog.error && <ErrorState error={catalog.error} resource="the permission catalog" scope="platform" />}
              {catalog.data && <PermissionList permissions={catalog.data} />}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

function GrantRevokeCard() {
  const roles = usePlatformRoles();
  const grant = useGrantPlatformRole();
  const revoke = useRevokePlatformRole();
  const [userId, setUserId] = useState("");
  const [roleCode, setRoleCode] = useState("");

  const isValid = userId.trim().length > 0 && roleCode.length > 0;

  async function run(action: "grant" | "revoke") {
    const mutation = action === "grant" ? grant : revoke;
    try {
      await mutation.mutateAsync({ targetUserId: userId.trim(), roleCode });
      toast.success(action === "grant" ? "Role granted" : "Role revoked");
      setUserId("");
    } catch (err) {
      // The self-escalation guard returns 403 with a "; "-joined list of
      // violations -- surface it verbatim, it names exactly which
      // permissions the actor is missing.
      toast.error(isApiError(err) ? err.message : `Couldn't ${action} the role.`);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Grant or revoke a platform role</CardTitle>
        <CardDescription>
          You can only grant a role whose permissions you already hold, and never one that outranks
          you.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="target-user">User</Label>
            <div className="mt-1.5">
              <UserPicker id="target-user" value={userId} onChange={setUserId} />
            </div>
          </div>
          <div>
            <Label htmlFor="role-code">Role</Label>
            <Select value={roleCode} onValueChange={(v) => setRoleCode(v ?? "")}>
              <SelectTrigger id="role-code" className="mt-1.5 w-full">
                <SelectValue placeholder="Select a role" />
              </SelectTrigger>
              <SelectContent>
                {roles.data?.map((role) => (
                  <SelectItem key={role.id} value={role.code}>
                    {role.name} ({role.code})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" disabled={!isValid || grant.isPending} onClick={() => run("grant")}>
            {grant.isPending ? "Granting…" : "Grant role"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!isValid || revoke.isPending}
            onClick={() => run("revoke")}
          >
            {revoke.isPending ? "Revoking…" : "Revoke role"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function RoleRow({ role }: { role: RoleSummary }) {
  const [editOpen, setEditOpen] = useState(false);

  return (
    <>
      <TableRow>
        <TableCell className="font-mono text-xs">{role.code}</TableCell>
        <TableCell className="font-medium">{role.name}</TableCell>
        <TableCell className="text-right tabular-nums">{role.rank}</TableCell>
        <TableCell>
          <Badge variant={role.is_system ? "outline" : "secondary"}>
            {role.is_system ? "System" : "Custom"}
          </Badge>
        </TableCell>
        <TableCell className="text-sm text-muted-foreground">{role.description ?? "—"}</TableCell>
        <TableCell>
          <Dialog open={editOpen} onOpenChange={setEditOpen}>
            <DialogTrigger
              render={<Button size="xs" variant="ghost" disabled={role.is_system} />}
            >
              {role.is_system ? "Fixed" : "Edit"}
            </DialogTrigger>
            <EditRolePermissionsDialog role={role} />
          </Dialog>
        </TableCell>
      </TableRow>
    </>
  );
}

function EditRolePermissionsDialog({ role }: { role: RoleSummary }) {
  const catalog = usePlatformPermissionCatalog();
  const rolePermissions = usePlatformRolePermissions();
  const { add, remove } = useEditPlatformRolePermissions();

  const assigned = new Set(rolePermissions.data?.by_role_code[role.code] ?? []);

  async function toggle(code: string, currentlyAssigned: boolean) {
    try {
      if (currentlyAssigned) {
        await remove.mutateAsync({ roleCode: role.code, permissionCode: code });
      } else {
        await add.mutateAsync({ roleCode: role.code, permissionCode: code });
      }
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't change the role's permissions.");
    }
  }

  return (
    <DialogContent className="max-w-lg">
      <DialogHeader>
        <DialogTitle>{role.name}</DialogTitle>
        <DialogDescription>
          You can only add a permission you hold yourself; removing one never needs that check.
        </DialogDescription>
      </DialogHeader>
      <div className="max-h-96 overflow-y-auto py-2">
        {catalog.isLoading || rolePermissions.isLoading ? (
          <TableSkeleton rows={4} columns={1} />
        ) : (
          <PermissionList
            permissions={catalog.data ?? []}
            selected={assigned}
            onToggle={(code) => toggle(code, assigned.has(code))}
          />
        )}
      </div>
    </DialogContent>
  );
}

function CreateRoleDialog({ onDone }: { onDone: () => void }) {
  const createRole = useCreatePlatformRole();
  const catalog = usePlatformPermissionCatalog();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [rank, setRank] = useState("10");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [codeError, setCodeError] = useState<string | undefined>();

  const codeValid = /^[a-z0-9_]{2,}$/.test(code);
  const canSubmit = codeValid && name.trim().length > 0 && rank.trim().length > 0;

  function toggle(permCode: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(permCode)) next.delete(permCode);
      else next.add(permCode);
      return next;
    });
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setCodeError(undefined);
    if (!codeValid) {
      setCodeError("Lowercase letters, numbers, and underscores only, at least 2 characters.");
      return;
    }
    try {
      await createRole.mutateAsync({
        code,
        name: name.trim(),
        description: description.trim() || null,
        rank: Number(rank),
        permissionCodes: [...selected],
      });
      toast.success(`Created role ${code}`);
      setCode("");
      setName("");
      setDescription("");
      setRank("10");
      setSelected(new Set());
      onDone();
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Couldn't create the role.");
    }
  }

  return (
    <DialogContent className="max-w-2xl">
      <form onSubmit={submit}>
        <DialogHeader>
          <DialogTitle>New platform role</DialogTitle>
          <DialogDescription>
            You can only include permissions you hold yourself, and the role can never outrank you
            when you later try to hold it.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <Label htmlFor="platform-role-code">Code</Label>
              <Input
                id="platform-role-code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="billing_contact"
                className="mt-1.5 font-mono text-xs"
              />
              <FieldError message={codeError} />
            </div>
            <div>
              <Label htmlFor="platform-role-name">Name</Label>
              <Input
                id="platform-role-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Billing Contact"
                className="mt-1.5"
              />
            </div>
            <div>
              <Label htmlFor="platform-role-rank">Rank</Label>
              <Input
                id="platform-role-rank"
                type="number"
                value={rank}
                onChange={(e) => setRank(e.target.value)}
                className="mt-1.5 tabular-nums"
              />
            </div>
          </div>
          <div>
            <Label htmlFor="platform-role-description">Description</Label>
            <Input
              id="platform-role-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="mt-1.5"
            />
          </div>
          <div>
            <Label>Permissions ({selected.size} selected)</Label>
            <div className="mt-1.5 max-h-64 overflow-y-auto rounded-md border border-border p-3">
              <PermissionList
                permissions={catalog.data ?? []}
                selected={selected}
                onToggle={toggle}
                emptyMessage="No platform permissions are defined on this deployment yet."
              />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" size="sm" onClick={onDone}>
            Cancel
          </Button>
          <Button type="submit" size="sm" disabled={!canSubmit || createRole.isPending}>
            {createRole.isPending ? "Creating…" : "Create role"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
